"""The Schedule tab through the real page: the form with its day buttons and live window strip, and the three cards that describe what is happening."""
import contextlib
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import schedule_view_logic as L  # noqa: E402
from test_pages_smoke import FakeWorksheet, FakeSpreadsheet, FIXTURE_CAMPAIGN, PAGES_DIR, _authed_session, _dashboard_secrets, _mock_github_writes  # noqa: E402

CHICAGO = {"timezone": "America/Chicago", "window_start": "08:30", "window_end": "18:15", "send_days": ["mon", "wed", "fri"]}
ALL_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@pytest.fixture(autouse=True)
def _every_test_uses_the_fixture_repo(fixture_repo):
    yield


@pytest.fixture(autouse=True)
def _stub_the_live_template_reads():
    names = [f"{stage}_{v}.txt" for stage in ["intro", "followup1", "followup2", "followup3", "followup4"] for v in "ABCD"]
    with patch("github_client.GitHubClient.list_directory_files", lambda self, path, ref="main": list(names)), \
         patch("github_client.GitHubClient.get_file_content", lambda self, path, ref="main": b"Subject: S\n\nBody."):
        yield


@pytest.fixture(autouse=True)
def _clear_streamlit_caches():
    st.cache_resource.clear()
    st.cache_data.clear()
    yield
    st.cache_resource.clear()
    st.cache_data.clear()


def lead(i, **kw):
    base = {"LeadID": str(i), "FirstName": f"First{i}", "LastName": "", "Email": f"lead{i}@example.com", "Approval": "Yes", "SenderAccount": "sales1", "Status": "", "ReplyStatus": ""}
    base.update(kw)
    return base


def stamp(minutes_ago=0):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%d %H:%M:%S")


@contextlib.contextmanager
def sched_page(leads=None, send_log=None, status=None, cfg=None, commit_error=None):
    """The campaign page (the Schedule tab is one of its tabs) against fake Sheets and a fake GitHub. `cfg` REPLACES keys of the campaign's own settings."""
    leads = [lead(1)] if leads is None else leads
    ws = {f"{FIXTURE_CAMPAIGN} Master Sheet": FakeWorksheet(leads), f"{FIXTURE_CAMPAIGN} Response Sheet": FakeWorksheet([]),
          f"{FIXTURE_CAMPAIGN} Custom Log Sheet": FakeWorksheet(send_log or []), f"{FIXTURE_CAMPAIGN} Error Log": FakeWorksheet([])}
    captured, fake_create_file = _mock_github_writes()

    def create_file(self, path, content, message="", **kw):
        if commit_error:
            raise commit_error
        return fake_create_file(self, path, content, message=message, **kw)

    patches = [patch("gspread.authorize", return_value=type("C", (), {"open_by_key": lambda self, k: FakeSpreadsheet(ws)})()),
               patch("google.oauth2.service_account.Credentials.from_service_account_info", return_value=object()),
               patch("github_client.GitHubClient.create_file", create_file),
               patch("github_client.GitHubClient.dispatch_workflow", lambda self, workflow, inputs, ref="main": captured.setdefault("dispatches", []).append((workflow, inputs)) or {"id": 1})]
    if cfg is not None:
        import preview_logic
        real_cfg = preview_logic.get_campaign_cfg
        patches.append(patch("preview_logic.get_campaign_cfg", lambda name: {**real_cfg(name), **cfg}))
    if status:
        patches.append(patch("campaign_status_logic.compute_campaign_status", lambda cfg_, leads_: (status, [])))
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        at = AppTest.from_file(os.path.join(PAGES_DIR, "campaigns.py"))
        at.secrets.update(_dashboard_secrets())
        for key, value in _authed_session().items():
            at.session_state[key] = value
        at.session_state["selected_campaign"] = FIXTURE_CAMPAIGN
        at.run(timeout=30)
        yield at, captured


def markup(at):
    return " ".join(m.value for m in at.markdown)


def block(at, css_class):
    return next(m.value for m in at.markdown if m.value.startswith(f'<div class="{css_class}"') or (f'class="{css_class}' in m.value[:120] and "<style>" not in m.value[:10]))


def active_blocks(at):
    return markup(at).count('class="sc-block sc-on"')


def set_text(at, label, value):
    next(t for t in at.text_input if t.label == label).set_value(value)
    at.run(timeout=30)


# ================================================================ the form

def test_the_form_shows_the_saved_schedule_with_seven_day_buttons():
    with sched_page(cfg={"schedule": dict(CHICAGO)}) as (at, _):
        assert list(at.exception) == [] and list(at.error) == []
        assert next(s for s in at.selectbox if s.label == "Time zone").value == "Central Time (US & Canada)"
        assert next(t for t in at.text_input if t.label == "Start").value == "08:30" and next(t for t in at.text_input if t.label == "End").value == "18:15"
        days = {c.key: c.value for c in at.checkbox if (c.key or "").startswith("schedule_day_")}
        assert days == {"schedule_day_mon": True, "schedule_day_tue": False, "schedule_day_wed": True, "schedule_day_thu": False, "schedule_day_fri": True, "schedule_day_sat": False, "schedule_day_sun": False}
        assert [c.label for c in at.checkbox if (c.key or "").startswith("schedule_day_")] == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def test_the_hour_strip_shows_the_saved_window_and_follows_what_is_typed():
    with sched_page(cfg={"schedule": dict(CHICAGO)}) as (at, _):
        assert active_blocks(at) == 11                                           # 08:30–18:15 touches the hours 8 to 18
        set_text(at, "Start", "10:00")
        set_text(at, "End", "12:00")
        assert active_blocks(at) == 2                                            # now only 10 and 11


def test_a_time_that_is_not_a_real_hh_mm_lights_nothing_and_saving_it_is_refused_with_the_same_message_as_ever():
    with sched_page(cfg={"schedule": dict(CHICAGO)}) as (at, captured):
        set_text(at, "End", "abc")
        assert active_blocks(at) == 0
        next(b for b in at.button if b.label == "💾 Save schedule").click()
        at.run(timeout=30)
        assert any("End time must be in HH:MM format." in e.value for e in at.error) and captured.get("commits") is None


def test_an_overnight_window_lights_the_hours_around_midnight_and_can_be_saved():
    """The engine allows an end before the start (22:00 to 06:00 is open overnight), so the strip must show it — and saving it is not an error."""
    with sched_page(cfg={"schedule": dict(CHICAGO)}) as (at, captured):
        set_text(at, "Start", "22:00")
        set_text(at, "End", "06:00")
        assert active_blocks(at) == 8                                              # 22, 23 and 0 to 5
        next(b for b in at.button if b.label == "💾 Save schedule").click()
        at.run(timeout=30)
        assert list(at.error) == [] and len(captured["commits"]) == 1 and b"22:00" in captured["commits"][0]["content"]


def test_a_campaign_with_no_schedule_says_it_sends_anytime_and_one_with_a_schedule_does_not():
    with sched_page(cfg={"schedule": None}) as (at, _):
        assert any("No schedule is set, so this campaign sends anytime" in c.value for c in at.caption)
    st.cache_data.clear()
    with sched_page(cfg={"schedule": dict(CHICAGO)}) as (at, _):
        assert not any("No schedule is set" in c.value for c in at.caption)


def test_saving_commits_the_same_file_with_the_same_message_and_success_text_as_ever():
    with sched_page(cfg={"schedule": dict(CHICAGO)}) as (at, captured):
        set_text(at, "Start", "09:00")
        next(b for b in at.button if b.label == "💾 Save schedule").click()
        at.run(timeout=30)
        assert list(at.exception) == [] and list(at.error) == []
        assert len(captured["commits"]) == 1 and "Update schedule for " in captured["commits"][0]["message"] and FIXTURE_CAMPAIGN in captured["commits"][0]["message"]
        assert b"09:00" in captured["commits"][0]["content"] and any("Schedule saved." in s.value for s in at.success)


def test_a_failed_save_says_why():
    from github_client import GitHubActionsError
    with sched_page(cfg={"schedule": dict(CHICAGO)}, commit_error=GitHubActionsError("boom")) as (at, _):
        next(b for b in at.button if b.label == "💾 Save schedule").click()
        at.run(timeout=30)
        assert any("Save failed: boom" in e.value for e in at.error)


def test_the_save_button_is_called_save_schedule_and_the_note_beside_it_says_when_it_applies():
    with sched_page() as (at, _):
        assert any(b.label == "💾 Save schedule" for b in at.button) and "Changes apply to the next hourly run." in markup(at)


# ================================================================ the cards on the right

def test_a_running_campaign_shows_its_next_automatic_run():
    with sched_page(status="running", cfg={"schedule": None}) as (at, _):
        text = markup(at)
        assert ">Next automatic run<" in text and '<div class="sc-next-time">' in text and re.search(r'sc-next-sub">in \d+ (minutes?|hours?|days?)', text)


@pytest.mark.parametrize("status, note", [("paused", "Paused: nothing is scheduled"), ("draft", "Draft: not launched yet"), ("completed", "Completed: nothing more to send")])
def test_a_campaign_that_is_not_running_says_why_there_is_no_next_run(status, note):
    with sched_page(status=status) as (at, _):
        assert f'<div class="sc-next-none">{note}</div>' in markup(at) and '<div class="sc-next-time">' not in markup(at)


def test_today_shows_the_emails_sent_so_far_against_the_daily_limit():
    log = [{"Timestamp": stamp(5), "Status": "sent", "LeadID": "1", "Stage": "intro"}, {"Timestamp": stamp(10), "Status": "sent", "LeadID": "2", "Stage": "intro"},
           {"Timestamp": stamp(20), "Status": "failed", "LeadID": "3", "Stage": "intro"}, {"Timestamp": stamp(60 * 24 * 3), "Status": "sent", "LeadID": "4", "Stage": "intro"}]
    with sched_page(send_log=log, cfg={"schedule": None, "sending": {"daily_limit": 10}}) as (at, _):
        assert any('<div class="sc-count">2 / 10</div>' in m.value and 'style="width:20%"' in m.value for m in at.markdown)       # 2 sent today (not the failed one, not the one 3 days ago)


def test_with_no_daily_limit_today_shows_just_the_count():
    log = [{"Timestamp": stamp(5), "Status": "sent", "LeadID": "1", "Stage": "intro"}]
    with sched_page(send_log=log, cfg={"schedule": None, "sending": {}}) as (at, _):
        assert '<div class="sc-count">1 sent</div>' in markup(at)


def test_the_due_counts_come_from_the_engine_and_leave_out_leads_that_have_replied():
    leads = [lead(1), lead(2), lead(3, ReplyStatus="Replied"), lead(4, Email="")]
    with sched_page(leads=leads, status="running") as (at, _):
        assert "Due now: <b>2</b> leads · Due tomorrow: <b>0</b> leads" in markup(at)


def test_leads_whose_follow_up_is_due_now_or_falls_due_tomorrow_are_counted_as_such():
    """The page builds its stages from the campaign's template folder and settings, and in the test fixture follow-up 1 waits 3 days (asserted below, so this cannot
    drift): a lead given the intro 3 days ago is due now, 2 days ago is due tomorrow, and one given it today is neither."""
    def intro_sent(days_ago):
        return (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")
    leads = [lead(1, IntroSentAt=intro_sent(3)), lead(2, IntroSentAt=intro_sent(2)), lead(3, IntroSentAt=intro_sent(0))]
    with sched_page(leads=leads, cfg={"schedule": None}) as (at, _):
        assert re.findall(r'<span class="sc-pill">(.*?)</span>', markup(at))[0] == "3 days"
        assert "Due now: <b>1</b> leads · Due tomorrow: <b>1</b> leads" in markup(at)


def test_the_window_line_and_the_weekend_line_follow_the_saved_schedule():
    with sched_page(cfg={"schedule": {**CHICAGO, "send_days": ["mon", "tue", "wed", "thu", "fri"]}}) as (at, _):
        assert "Weekend sends: none (Sat, Sun off)" in markup(at) and re.search(r"Window is (<b class=\"sc-open\">open</b> until 6:15 PM|closed right now)", markup(at))
    st.cache_data.clear()
    with sched_page(cfg={"schedule": None}) as (at, _):
        assert "No schedule: sends any day" in markup(at) and "No schedule: this campaign sends anytime" in markup(at)


def test_this_week_lists_the_send_days_and_todays_count_from_the_campaigns_own_day():
    now = datetime.now(timezone.utc)
    log = [{"Timestamp": stamp(1), "Status": "sent", "LeadID": "1", "Stage": "intro"}, {"Timestamp": stamp(2), "Status": "sent", "LeadID": "2", "Stage": "intro"}]
    with sched_page(send_log=log, cfg={"schedule": {"timezone": "UTC", "window_start": "00:00", "window_end": "23:59", "send_days": ALL_DAYS}, "sending": {"daily_limit": 4}}) as (at, _):
        rows = re.findall(r'sc-week-day">(\w+)</div><div class="sc-track"><i style="width:(\d+)%"></i></div><div class="sc-week-count">([^<]+)<', markup(at))
        assert [r[0] for r in rows] == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        today = rows[now.astimezone(timezone.utc).weekday()]
        assert today[2] == "2" and today[1] == "50"                                      # 2 of a daily limit of 4
        assert all(r[2] == "—" for r in rows[now.weekday() + 1:])                        # days still to come show a dash


def test_the_follow_up_waits_shown_are_the_campaigns_own():
    import preview_logic
    expected = L.wait_rows(preview_logic.get_campaign_cfg(FIXTURE_CAMPAIGN)["stages"])
    with sched_page() as (at, _):
        text = markup(at)
        assert [m for m in re.findall(r'<td class="sc-stage">(.*?)</td>', text)] == [r["stage"] for r in expected]
        assert re.findall(r'<span class="sc-pill">(.*?)</span>', text) == [r["wait"] for r in expected] and expected


def test_looking_at_the_schedule_tab_never_writes_anything():
    with sched_page(status="running", leads=[lead(1), lead(2)]) as (at, captured):
        at.run(timeout=30)
        assert list(at.exception) == [] and captured.get("commits") is None and captured.get("dispatches") is None


def test_a_campaign_with_no_leads_and_no_log_still_shows_every_card_without_an_error():
    with sched_page(leads=[], send_log=[], status="running") as (at, _):
        text = markup(at)
        assert list(at.exception) == [] and "Due now: <b>0</b> leads" in text and ">This week<" in text and ">Today<" in text and ">Follow-up waits<" in text


# ================================================================ no schedule, and the next-run card with a fixed clock

def test_a_campaign_with_no_schedule_lists_all_seven_days_in_this_week_because_it_sends_any_day():
    with sched_page(cfg={"schedule": None}) as (at, _):
        assert re.findall(r'sc-week-day">(\w+)</div>', markup(at)) == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _card_script(running, viewer, next_run="datetime(2026, 10, 13, 19, 43, tzinfo=timezone.utc)"):
    """A page that draws just the Schedule tab for a made-up campaign, with a fixed clock (Tuesday 09:00 Chicago time) so every number is known in advance."""
    return f"""
import sys
sys.path.insert(0, {os.path.join(os.path.dirname(__file__), "..")!r})
sys.path.insert(0, {os.path.join(os.path.dirname(__file__), "..", "..")!r})
from datetime import datetime, timezone
from schedule_view import ScheduleContext, render_schedule_tab
cfg = {{"_campaign_name": "Demo", "stages": [{{"name": "intro", "template_prefix": "intro", "wait_days_after_previous": 0}}, {{"name": "followup1", "template_prefix": "followup1", "wait_days_after_previous": 2}}],
        "schedule": {{"timezone": "America/Chicago", "window_start": "09:00", "window_end": "17:00", "send_days": ["mon", "tue", "wed", "thu", "fri"]}}, "sending": {{"daily_limit": 100}}}}
render_schedule_tab(ScheduleContext(campaign_cfg=cfg, leads=[], send_log=[], running={running!r}, status_note="Paused: nothing is scheduled", next_run={next_run},
                                    get_client=lambda: None, current_user=lambda: "t", campaigns_dir="/nonexistent", now=datetime(2026, 10, 13, 14, 0, tzinfo=timezone.utc), viewer_timezone={viewer!r}))
"""


def test_the_next_run_card_shows_the_campaigns_time_the_wait_and_the_visitors_own_time():
    at = AppTest.from_string(_card_script(True, "Asia/Kolkata"))
    at.run(timeout=30)
    text = markup(at)
    assert list(at.exception) == [] and '<div class="sc-next-time">Today 2:43 PM CDT</div>' in text and "in 5 hours 43 minutes · <b>Wed 1:13 AM IST</b>" in text


def test_the_visitors_time_is_left_out_when_it_is_the_campaigns_own_zone_or_unknown():
    for viewer in ("America/Chicago", None):
        at = AppTest.from_string(_card_script(True, viewer))
        at.run(timeout=30)
        assert "in 5 hours 43 minutes</div>" in markup(at) and "IST" not in markup(at)


def test_a_campaign_that_is_not_running_shows_its_note_even_if_a_next_run_time_is_passed_in():
    at = AppTest.from_string(_card_script(False, "Asia/Kolkata"))
    at.run(timeout=30)
    assert '<div class="sc-next-none">Paused: nothing is scheduled</div>' in markup(at) and "sc-next-time" not in markup(at).replace(".sc-next-time", "")


def test_a_running_campaign_with_nothing_due_in_the_next_days_says_so():
    at = AppTest.from_string(_card_script(True, None, next_run="None"))
    at.run(timeout=30)
    assert '<div class="sc-next-none">Nothing is due in the next 8 days</div>' in markup(at)
