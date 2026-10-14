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
def sched_page(leads=None, send_log=None, status=None, cfg=None, commit_error=None, live=None, templates=None):
    """The campaign page (the Schedule tab is one of its tabs) against fake Sheets and a fake GitHub. `cfg` REPLACES keys of the campaign's own settings.
    `live` is a dict of the files that are on GitHub right now ({path: bytes}); with it, saves are remembered in it and read back from it, like the real thing.
    Without it, reading the settings file from GitHub fails, so the page falls back to its local copy."""
    leads = [lead(1)] if leads is None else leads
    ws = {f"{FIXTURE_CAMPAIGN} Master Sheet": FakeWorksheet(leads), f"{FIXTURE_CAMPAIGN} Response Sheet": FakeWorksheet([]),
          f"{FIXTURE_CAMPAIGN} Custom Log Sheet": FakeWorksheet(send_log or []), f"{FIXTURE_CAMPAIGN} Error Log": FakeWorksheet([])}
    captured, fake_create_file = _mock_github_writes()

    def create_file(self, path, content, message="", **kw):
        if commit_error:
            raise commit_error
        if live is not None:
            live[path] = content
        return fake_create_file(self, path, content, message=message, **kw)

    def get_file(self, path, ref="main"):
        if path.startswith("config/campaigns/"):
            if live is None:
                raise RuntimeError("GitHub is not reachable")                  # -> the page falls back to its local copy
            return live.get(path)                                              # None: the file is not there (a normal state)
        return b"Subject: S\n\nBody."

    patches = [patch("gspread.authorize", return_value=type("C", (), {"open_by_key": lambda self, k: FakeSpreadsheet(ws)})()),
               patch("google.oauth2.service_account.Credentials.from_service_account_info", return_value=object()),
               patch("github_client.GitHubClient.create_file", create_file),
               patch("github_client.GitHubClient.dispatch_workflow", lambda self, workflow, inputs, ref="main": captured.setdefault("dispatches", []).append((workflow, inputs)) or {"id": 1})]
    patches.append(patch("github_client.GitHubClient.get_file_content", get_file))
    if templates is not None:
        patches.append(patch("github_client.GitHubClient.list_directory_files", lambda self, path, ref="main": list(templates)))
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


def waits(at):
    """The follow-up wait number boxes, in order."""
    return [n for n in at.number_input if (n.key or "").startswith(f"wait_{FIXTURE_CAMPAIGN}_")]


def save_waits(at):
    return next(b for b in at.button if b.key == "sc_savewaits_button")


def info_rows(at):
    """[(what it means in words, day)] for each follow-up row."""
    return [(a, int(b)) for a, b in re.findall(r'sc-wait-info">(.*?)<span class="sc-day"> · day (\d+)<', markup(at))]


def override_yaml(**keys):
    import yaml
    return yaml.safe_dump(keys).encode("utf-8")


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
        assert waits(at)[0].value == 3
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



# ================================================================ editing each follow-up's wait

OVERRIDE_PATH = f"config/campaigns/{FIXTURE_CAMPAIGN}.yaml"


def test_each_follow_up_has_a_number_box_with_its_current_wait_and_the_allowed_range():
    with sched_page() as (at, _):
        boxes = waits(at)
        assert list(at.exception) == [] and [b.value for b in boxes] == [3, 4, 5, 5]                  # the fixture's shared waits for follow-ups 1 to 4
        assert all(b.min == 0 and b.max == 90 and b.step == 1 for b in boxes)
        assert [b.label for b in boxes] == [f"Days before Follow-up {i}" for i in range(1, 5)]
        assert all(b.key.startswith(f"wait_{FIXTURE_CAMPAIGN}_followup") for b in boxes)         # per campaign, so switching campaigns can never carry a value over


def test_each_row_says_what_its_wait_means_and_the_day_it_lands_on_counted_from_the_intro():
    with sched_page() as (at, _):
        assert info_rows(at) == [("3 days after the intro", 3), ("4 days after follow-up 1", 7), ("5 days after follow-up 2", 12), ("5 days after follow-up 3", 17)]
        assert re.findall(r'class="sc-stage">(.*?)</div>', markup(at)) == ["Follow-up 1", "Follow-up 2", "Follow-up 3", "Follow-up 4"]


def test_changing_a_wait_updates_its_description_and_every_day_after_it_at_once():
    with sched_page() as (at, _):
        waits(at)[1].set_value(1)
        at.run(timeout=30)
        assert info_rows(at) == [("3 days after the intro", 3), ("1 day after follow-up 1", 4), ("5 days after follow-up 2", 9), ("5 days after follow-up 3", 14)]


def test_a_wait_of_zero_is_said_plainly_as_the_same_day():
    with sched_page() as (at, _):
        waits(at)[2].set_value(0)
        at.run(timeout=30)
        assert info_rows(at)[2] == ("Same day as follow-up 2", 7)


def test_save_waits_is_off_until_something_changes_and_nothing_is_saved_by_editing_alone():
    with sched_page() as (at, captured):
        assert save_waits(at).disabled is True and not any("Not saved yet" in c.value for c in at.caption)
        waits(at)[0].set_value(6)
        at.run(timeout=30)
        assert save_waits(at).disabled is False and captured.get("commits") is None and captured.get("dispatches") is None


def test_putting_a_wait_back_to_what_it_was_turns_save_off_again():
    with sched_page() as (at, _):
        waits(at)[0].set_value(6)
        at.run(timeout=30)
        waits(at)[0].set_value(3)
        at.run(timeout=30)
        assert save_waits(at).disabled is True


def test_unsaved_changes_are_listed_and_only_a_shorter_wait_brings_the_warning():
    with sched_page() as (at, _):
        waits(at)[0].set_value(7)                                                               # longer
        at.run(timeout=30)
        assert any("Not saved yet: Follow-up 1 was 3 days, now 7 days." in c.value for c in at.caption) and not any("shorter wait" in w.value for w in at.warning)
        waits(at)[1].set_value(1)                                                               # shorter
        at.run(timeout=30)
        assert any("Follow-up 2 was 4 days, now 1 day" in c.value for c in at.caption)
        assert any("A shorter wait can make leads due straight away" in w.value for w in at.warning)


def test_saving_commits_every_follow_ups_wait_to_the_campaigns_settings_file_and_keeps_everything_else():
    import yaml
    live = {OVERRIDE_PATH: override_yaml(status="active", sending={"daily_limit": 50}, schedule={"timezone": "UTC", "window_start": "08:00", "window_end": "12:00", "send_days": ["mon"]})}
    with sched_page(live=live) as (at, captured):
        waits(at)[1].set_value(9)
        at.run(timeout=30)
        save_waits(at).click()
        at.run(timeout=30)
        assert list(at.exception) == [] and list(at.error) == []
        assert len(captured["commits"]) == 1 and captured.get("dispatches") is None                  # exactly one commit, and nothing was started
        commit = captured["commits"][0]
        saved = yaml.safe_load(commit["content"])
        assert commit["path"] == OVERRIDE_PATH and "Update follow-up waits for " in commit["message"] and FIXTURE_CAMPAIGN in commit["message"]
        assert saved["stage_wait_days"] == {"followup1": 3, "followup2": 9, "followup3": 5, "followup4": 5}
        assert saved["status"] == "active" and saved["sending"] == {"daily_limit": 50} and saved["schedule"]["window_end"] == "12:00"
        assert any("Follow-up waits saved." in s.value for s in at.success)


def test_saving_waits_reads_the_live_file_so_a_schedule_saved_a_moment_ago_is_never_undone():
    """The page's own copy of the file can be old. The live file has a schedule that this page has not seen; saving waits must keep it."""
    import yaml
    live = {OVERRIDE_PATH: override_yaml(schedule={"timezone": "Asia/Kolkata", "window_start": "10:00", "window_end": "15:00", "send_days": ["tue", "thu"]})}
    with sched_page(live=live, cfg={"schedule": None}) as (at, captured):
        waits(at)[0].set_value(8)
        at.run(timeout=30)
        save_waits(at).click()
        at.run(timeout=30)
        saved = yaml.safe_load(captured["commits"][0]["content"])
        assert saved["schedule"] == {"timezone": "Asia/Kolkata", "window_start": "10:00", "window_end": "15:00", "send_days": ["tue", "thu"]} and saved["stage_wait_days"]["followup1"] == 8


def test_saving_the_schedule_reads_the_live_file_so_waits_saved_a_moment_ago_are_never_undone():
    import yaml
    live = {OVERRIDE_PATH: override_yaml(stage_wait_days={"followup1": 6, "followup2": 7})}
    with sched_page(live=live, cfg={"schedule": dict(CHICAGO)}) as (at, captured):
        set_text(at, "Start", "10:00")
        next(b for b in at.button if b.label == "💾 Save schedule").click()
        at.run(timeout=30)
        saved = yaml.safe_load(captured["commits"][0]["content"])
        assert saved["stage_wait_days"] == {"followup1": 6, "followup2": 7} and saved["schedule"]["window_start"] == "10:00"


def test_when_github_cannot_be_read_the_page_falls_back_to_its_local_copy_instead_of_failing():
    with sched_page(live=None) as (at, captured):
        waits(at)[0].set_value(4)
        at.run(timeout=30)
        save_waits(at).click()
        at.run(timeout=30)
        assert list(at.exception) == [] and list(at.error) == [] and len(captured["commits"]) == 1


def test_after_saving_a_fresh_page_shows_the_new_waits_and_has_nothing_left_to_save():
    live = {}
    with sched_page(live=live) as (at, _):
        waits(at)[1].set_value(11)
        at.run(timeout=30)
        save_waits(at).click()
        at.run(timeout=30)
        assert OVERRIDE_PATH in live
    st.cache_data.clear()
    with sched_page(live=live) as (at, _):                                                      # a new visit, reading what GitHub now holds
        assert [b.value for b in waits(at)] == [3, 11, 5, 5] and save_waits(at).disabled is True
        assert info_rows(at)[1] == ("11 days after follow-up 1", 14)


def test_a_failed_save_says_why_and_keeps_the_edit_so_it_can_be_tried_again():
    from github_client import GitHubActionsError
    with sched_page(live={}, commit_error=GitHubActionsError("boom")) as (at, captured):
        waits(at)[0].set_value(6)
        at.run(timeout=30)
        save_waits(at).click()
        at.run(timeout=30)
        assert any("Save failed: boom" in e.value for e in at.error) and not at.success and captured.get("commits") is None
        assert waits(at)[0].value == 6 and save_waits(at).disabled is False


def test_a_campaigns_own_waits_in_its_live_settings_file_are_what_the_boxes_show():
    with sched_page(live={OVERRIDE_PATH: override_yaml(stage_wait_days={"followup2": 6, "followup4": 1})}) as (at, _):
        assert [b.value for b in waits(at)] == [3, 6, 5, 1] and save_waits(at).disabled is True
        assert info_rows(at) == [("3 days after the intro", 3), ("6 days after follow-up 1", 9), ("5 days after follow-up 2", 14), ("1 day after follow-up 3", 15)]


@pytest.mark.parametrize("bad", [{"followup2": -5}, {"followup2": 2.5}, {"followup2": "x"}, {"followup2": 500}, {"followup2": True}, {"followup1": 2, "followup3": None}])
def test_a_wait_the_engine_would_refuse_in_the_live_file_is_not_shown_and_the_page_still_works(bad):
    with sched_page(live={OVERRIDE_PATH: override_yaml(stage_wait_days=bad)}) as (at, _):
        assert list(at.exception) == [] and [b.value for b in waits(at)] == [3, 4, 5, 5]


def test_a_campaign_with_only_an_intro_has_no_boxes_no_save_button_and_says_why():
    with sched_page(templates=["intro_A.txt", "intro_B.txt", "intro_C.txt", "intro_D.txt"]) as (at, _):
        assert list(at.exception) == [] and waits(at) == [] and not any(b.key == "sc_savewaits_button" for b in at.button)
        assert "only its intro, so there are no follow-up waits to set" in markup(at)


def test_only_the_follow_ups_are_listed_never_the_intro():
    with sched_page() as (at, _):
        assert not any("intro" in b.key for b in waits(at)) and len(waits(at)) == 4


def test_if_the_final_check_refuses_a_wait_nothing_is_committed_and_the_reason_is_shown():
    """The number boxes already stop out-of-range typing, so this last check is defence in depth: it is made to fail here to prove it really is wired in."""
    with sched_page(live={}) as (at, captured):
        with patch("schedule_view.validate_stage_waits", lambda values, labels=None: ["Follow-up 1: the wait must be a whole number of days from 0 to 90."]):
            waits(at)[0].set_value(6)
            at.run(timeout=30)
            save_waits(at).click()
            at.run(timeout=30)
        assert any("Follow-up 1: the wait must be a whole number of days from 0 to 90." in e.value for e in at.error) and captured.get("commits") is None and not at.success
