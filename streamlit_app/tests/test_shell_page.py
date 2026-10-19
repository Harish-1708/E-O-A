"""The real app (app.py) through the test harness: the sidebar, its markers, and the Home page with its live part."""
import contextlib
import os
import re
import sys
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from test_pages_smoke import _authed_session, _dashboard_secrets  # noqa: E402

APP = os.path.join(os.path.dirname(__file__), "..", "app.py")


@pytest.fixture(autouse=True)
def _every_test_uses_the_fixture_repo(fixture_repo):
    yield


@pytest.fixture(autouse=True)
def _clear_streamlit_caches():
    st.cache_resource.clear()
    st.cache_data.clear()
    yield
    st.cache_resource.clear()
    st.cache_data.clear()


def _stamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _cfg(limit=100):
    return {"sending": {"daily_limit": limit}, "schedule": {"timezone": "UTC", "window_start": "00:00", "window_end": "23:59", "send_days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]}}


ROWS = [{"name": "Live", "status": "running", "problems": []}, {"name": "Stuck", "status": "attention", "problems": ["No leads with an email address"]}, {"name": "Done", "status": "completed", "problems": []}]
DETAILS = {"Live": {"cfg": _cfg(100), "leads": [], "send_log": [{"Timestamp": _stamp(), "Status": "sent"} for _ in range(41)], "responses": [{"ResponseID": "1", "LeadID": "L1"}, {"ResponseID": "2", "LeadID": "L2"}]},
           "Done": {"cfg": _cfg(50), "leads": [], "send_log": [], "responses": [{"ResponseID": "9", "LeadID": "L9", "IsRead": "TRUE"}]}}
DIRECTORY = {"sales1": "a@kelson.agency", "sales2": "b@kelson.agency", "sales3": "c@kelson.agency"}
HEALTH = {"sales1": {"status": "Connected", "detail": "", "checked_at": "2026-10-15 01:00:00"}, "sales2": {"status": "Connected", "detail": "", "checked_at": "2026-10-15 01:00:00"},
          "sales3": {"status": "Disconnected", "detail": "Login failed", "checked_at": "2026-10-15 01:00:00"}}


def app(rows=None, details=None, directory=None, health=None, boom=None, hour=21, prime=None):
    @contextlib.contextmanager
    def _ctx():
        switched = []

        def bundle():
            if boom:
                raise boom
            return (ROWS if rows is None else rows), [], [], (DETAILS if details is None else details)
        patches = [patch("shell_view.load_hub_bundle", bundle), patch("shell_view.load_sender_overview", lambda: {"directory": DIRECTORY if directory is None else directory, "health": HEALTH if health is None else health}),
                   patch("shell_view.local_now", lambda: datetime(2026, 10, 15, hour, 5, tzinfo=timezone.utc)),
                   patch("streamlit.switch_page", lambda page, *a, **k: switched.append(getattr(page, "title", page)))]
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            at = AppTest.from_file(APP)
            at.secrets.update(_dashboard_secrets())
            for key, value in _authed_session().items():
                at.session_state[key] = value
            for key, value in (prime or {}).items():
                at.session_state[key] = value
            at.run(timeout=30)
            yield at, switched
    return _ctx()


def markup(at):
    return " ".join(str(m.value) for m in at.markdown)


def test_the_app_starts_on_home_with_no_errors_and_no_leftover_loading_shimmer():
    with app() as (at, _):
        assert list(at.exception) == [] and list(at.error) == []
        assert 'class="sh-greet">' in markup(at) and 'class="sh-skel"' not in markup(at)


def test_the_sidebar_has_the_brand_the_signed_in_user_and_log_out():
    with app() as (at, _):
        text = markup(at)
        assert "Email Outreach" in text and "Control panel" in text and "Signed in" in text
        assert any(b.key == "nav_logout" and b.label == "Log out" for b in at.sidebar.button)


def test_the_sidebar_has_one_link_per_page_in_order():
    with app() as (at, _):
        links = [(e.proto.label, e.proto.page) for e in at.sidebar.get("page_link")] if at.sidebar.get("page_link") else []
        assert [label for label, _ in links] == ["Home", "Performance", "Campaigns", "Responses", "Email Accounts"]


def test_the_unread_number_and_the_disconnected_dot_are_drawn_when_there_is_something_to_show():
    with app() as (at, _):
        css = markup(at)
        assert '[class*="st-key-nav_responses"] a::after { content: "2";' in css and '[class*="st-key-nav_accounts"] a::after { content: "";' in css


def test_with_everything_read_and_every_account_connected_there_are_no_markers():
    with app(details={"Live": {**DETAILS["Live"], "responses": []}}, health={k: {**v, "status": "Connected"} for k, v in HEALTH.items()}) as (at, _):
        assert "st-key-nav_responses" not in markup(at) and "st-key-nav_accounts" not in markup(at)


def test_a_reply_read_in_this_session_leaves_the_unread_count_at_once():
    with app(prime={"read_response_keys": {"Live:1"}}) as (at, _):
        assert 'st-key-nav_responses"] a::after { content: "1";' in markup(at)


def test_a_problem_reading_the_data_never_breaks_the_page_or_adds_markers():
    with app(boom=RuntimeError("sheet unreachable")) as (at, _):
        assert list(at.exception) == [] and "st-key-nav_responses" not in markup(at) and "st-key-nav_accounts" not in markup(at)


@pytest.mark.parametrize("hour, greeting", [(8, "Good morning"), (13, "Good afternoon"), (21, "Good evening")])
def test_the_greeting_follows_the_visitors_hour_and_names_them(hour, greeting):
    with app(hour=hour) as (at, _):
        assert re.search(rf'sh-greet">{greeting}, \w+<', markup(at)) and "Thursday, October 15" in markup(at)


def test_the_cards_say_what_needs_attention_with_the_real_numbers():
    with app() as (at, _):
        text = markup(at)
        assert "2 unread replies" in text and "1 account disconnected" in text and "c@kelson.agency · Login failed. Campaigns using it cannot send." in text
        assert "1 campaign needs attention" in text and "Stuck · No leads with an email address." in text and "All clear" not in text


def test_when_nothing_needs_attention_one_calm_card_says_so():
    with app(rows=[{"name": "Live", "status": "running", "problems": []}], details={"Live": {**DETAILS["Live"], "responses": []}}, health={k: {**v, "status": "Connected"} for k, v in HEALTH.items()}) as (at, _):
        assert "All clear" in markup(at) and '<div class="sh-att-t">' not in markup(at)


def test_with_nothing_running_it_says_so():
    with app(rows=[{"name": "Done", "status": "completed", "problems": []}]) as (at, _):
        assert any("No campaign is running right now, so nothing is due." in c.value for c in at.caption)


def test_opening_a_running_campaign_selects_it_and_goes_to_the_campaigns_page():
    with app() as (at, switched):
        next(b for b in at.button if b.key == "home_open_Live").click()
        at.run(timeout=30)
        assert at.session_state["selected_campaign"] == "Live" and switched == ["Campaigns"]


def test_the_quick_actions_leave_a_note_so_the_other_page_opens_its_dialog_on_arrival():
    with app() as (at, switched):
        next(b for b in at.button if b.key == "home_new_campaign").click()
        at.run(timeout=30)
        assert at.session_state["hub_open_new_on_arrival"] is True and switched == ["Campaigns"]
    with app() as (at, switched):
        next(b for b in at.button if b.key == "home_add_account").click()
        at.run(timeout=30)
        assert at.session_state["ea_open_add_on_arrival"] is True and switched == ["Email Accounts"]


def test_the_guide_has_a_card_and_a_link_for_each_of_the_five_pages():
    with app() as (at, _):
        text = markup(at)
        for title in ("Performance", "Campaigns", "Responses", "Email Accounts"):
            assert f'sh-gt">{title}<' in text
        assert text.count('class="sh-gi"') == 4


def test_if_the_live_data_cannot_be_read_the_page_says_so_and_the_rest_still_works():
    with app(boom=RuntimeError("sheet unreachable")) as (at, _):
        text = markup(at)
        assert "Couldn’t load the live status just now (sheet unreachable)" in text and "The rest of this page still works." in text
        assert any(b.key == "home_new_campaign" for b in at.button) and text.count('class="sh-gi"') == 4 and 'class="sh-skel"' not in text


def test_log_out_signs_the_user_out():
    with patch("auth.logout") as fake:
        with app() as (at, _):
            next(b for b in at.sidebar.button if b.key == "nav_logout").click()
            at.run(timeout=30)
            assert fake.called


def _app_source():
    return open(APP, encoding="utf-8").read()


def test_the_markers_are_drawn_after_the_page_and_even_when_a_page_stops_itself():
    """A page that calls st.stop() raises out of nav.run(); the markers must still be drawn, so they sit in a finally block, after the page (so the page is never held up)."""
    assert re.search(r"try:\s*\n\s*nav\.run\(\)\s*\nfinally:\s*\n(?:\s*#[^\n]*\n)*\s*render_sidebar_markers\(\)", _app_source())


def test_the_built_in_page_list_is_hidden_because_the_app_draws_its_own():
    assert re.search(r'st\.navigation\(list\(PAGES\.values\(\)\), position="hidden"\)', _app_source())


def test_the_sidebar_is_drawn_before_the_page_runs_and_only_after_signing_in():
    source = _app_source()
    assert source.index("if not login_gate():") < source.index("render_sidebar(PAGES") < source.index("nav.run()")


def test_the_sidebar_and_the_greeting_name_the_actual_signed_in_user():
    with app() as (at, _):
        text = markup(at)
        name = re.search(r'sh-greet">Good \w+, ([^<]+)<', text).group(1)
        assert name and name != "someone" and f"<b>{name}</b><span>Signed in</span>" in text and f'sh-user-av">{name[0].upper()}<' in text


def test_the_sidebar_marks_the_page_being_shown_as_the_active_one():
    source = open(os.path.join(os.path.dirname(__file__), "..", "shell_view.py"), encoding="utf-8").read()
    assert 'with st.container(key=L.nav_key(key, L.is_current_page(pages[key], current))):' in source
    assert "render_sidebar(PAGES, current_user(), logout, current=nav)" in _app_source()


# ================================================================ Today, as a part of Home (page level)

def _today_cfg(name="Live", limit=100):
    cfg = {"_campaign_name": name, "status": "active", "sending": {"daily_limit": limit},
           "schedule": {"timezone": "UTC", "window_start": "00:00", "window_end": "23:59", "send_days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]},
           "stages": [{"name": n, "template_prefix": n, "wait_days_after_previous": 0 if i == 0 else 2} for i, n in enumerate(["intro", "followup1", "followup2"])]}
    return cfg


def _today_leads():
    """lead 1 was contacted long ago (follow-up 1 is due), lead 2 was never contacted (the intro is due), lead 3 replied."""
    sent = "IntroSentAt"
    return [{"LeadID": "1", "Email": "a@x.com", "FirstName": "Ann", "LastName": "A", sent: "2020-01-01 10:00:00", "Status": "", "ReplyStatus": ""},
            {"LeadID": "2", "Email": "b@x.com", "FirstName": "Bob", "LastName": "B", "Status": "", "ReplyStatus": ""},
            {"LeadID": "3", "Email": "c@x.com", "FirstName": "Cy", "LastName": "C", sent: "2020-01-01 10:00:00", "Status": "Stopped - Replied", "ReplyStatus": "Replied"}]


TODAY_DETAILS = {"Live": {"cfg": _today_cfg(), "leads": _today_leads(), "send_log": [], "responses": []}}
TODAY_ROWS = [{"name": "Live", "status": "running", "problems": []}, {"name": "Done", "status": "completed", "problems": []}]


def test_home_has_a_today_section_with_totals_and_a_card_for_each_running_campaign():
    with app(rows=TODAY_ROWS, details=TODAY_DETAILS) as (at, _):
        text = markup(at)
        assert list(at.exception) == [] and 'class="sh-section">Today<' in text
        values = re.findall(r'sh-stat-v[^"]*">(.*?)</div>', text)
        assert values[:3] == ["2", "0", "0"] and "of 100 daily limit" in text and '1 <span class="sh-of">of 1</span>' in text           # due, waiting, sent today, windows open
        assert 'sh-tc-name">Live<' in text and 'sh-win sh-win-open">● Open now<' in text and "2 leads are due and sending is open" in text
        assert 'class="sh-tc-name">Done<' not in text                                                                                  # a completed campaign has no card


def test_the_card_has_a_stage_breakdown_and_a_list_of_who_is_due_now_one_tap_away():
    with app(rows=TODAY_ROWS, details=TODAY_DETAILS) as (at, _):
        labels = [e.label for e in at.expander]
        assert "Stage breakdown" in labels and "Who is due now (2)" in labels
        text = markup(at)
        assert "<td>Intro</td><td>1</td><td>0</td>" in text and "<td>Follow-up 1</td><td>1</td><td>0</td>" in text
        emails = [row for df in at.dataframe for row in df.value["Email"].tolist()]
        assert sorted(emails) == ["a@x.com", "b@x.com"]                                                                              # the leads who are due, and nobody else (lead 3 replied)


def test_when_nobody_is_due_the_card_offers_no_due_list():
    with app(rows=TODAY_ROWS, details={"Live": {**TODAY_DETAILS["Live"], "leads": _today_leads()[2:]}}) as (at, _):
        assert not any(e.label.startswith("Who is due now") for e in at.expander) and any("Nobody is due or waiting at any stage." in c.value for c in at.caption)


def _fixed_queue(open_state, reason="", next_open=""):
    return {"campaign": "Live", "window": {"open": open_state, "reason": reason, "next_open": next_open}, "stages": [], "due_total": 0, "waiting_total": 3, "next_due_text": "Fri 16 Oct",
            "out_of_play": {}, "in_sequence": 3, "total_leads": 3, "daily_limit": 100, "sent_today": 40, "remaining_today": 60}


@pytest.mark.parametrize("state, kind, words, key", [(True, "open", "● Open now", "home_today_open_0"), (False, "shut", "○ Closed · opens Fri 16 Oct, 9:00 AM EDT", "home_today_shut_0"),
                                                     (None, "unknown", "Sending window unknown", "home_today_shut_0")])
def test_the_card_shows_the_sending_window_in_each_of_its_three_states(state, kind, words, key):
    with patch("shell_logic.build_campaign_queue", lambda cfg, leads, now: _fixed_queue(state, "outside the window", "Fri 16 Oct, 9:00 AM EDT")):
        with app(rows=TODAY_ROWS, details=TODAY_DETAILS) as (at, _):
            assert f'sh-win sh-win-{kind}">{words}<' in markup(at) and "40 <span class=\"sh-of\">/ 100</span>" in markup(at) and 'style="width:40%"' in markup(at)
            if state is not True:
                assert "outside the window" in markup(at)


def test_opening_a_card_goes_to_that_campaign_even_when_its_settings_carry_no_name():
    details = {"Live": {**TODAY_DETAILS["Live"], "cfg": {k: v for k, v in _today_cfg().items() if k != "_campaign_name"}}}
    with app(rows=TODAY_ROWS, details=details) as (at, switched):
        next(b for b in at.button if b.key == "home_open_Live").click()
        at.run(timeout=30)
        assert at.session_state["selected_campaign"] == "Live" and switched == ["Campaigns"]


def test_with_nothing_running_today_says_so_and_still_lists_what_is_not_running():
    with app(rows=[{"name": "Done", "status": "completed", "problems": []}, {"name": "Soon", "status": "draft", "problems": []}]) as (at, _):
        text = " ".join(c.value for c in at.caption)
        assert "No campaign is running right now, so nothing is due." in text and "Not running: Done (completed), Soon (draft)." in text
        assert "sh-stat-l" not in markup(at).split('class="sh-section">Today<')[1].split("Quick actions")[0]                           # no totals either


def test_the_notes_under_today_say_which_time_zone_dates_are_in():
    with app(rows=TODAY_ROWS, details=TODAY_DETAILS) as (at, _):
        text = " ".join(c.value for c in at.caption)
        assert "Dates are in each campaign's own time zone; times stored in the Sheet are UTC." in text and "Not running: Done (completed)." in text


def test_a_running_campaign_that_cannot_be_shown_is_listed_in_a_dropdown_and_the_others_still_show():
    rows = [{"name": "Broken", "status": "running", "problems": []}] + TODAY_ROWS
    with app(rows=rows, details=TODAY_DETAILS) as (at, _):
        assert 'sh-tc-name">Live<' in markup(at) and any(e.label == "1 campaign(s) could not be shown here" for e in at.expander)
        assert any("its data could not be read" in w.value for w in at.markdown)


def test_there_is_no_todays_page_any_more_and_no_link_to_one():
    assert not os.path.exists(os.path.join(os.path.dirname(APP), "pages", "today.py"))
    assert "today.py" not in _app_source() and 'PAGES["today"]' not in _app_source()
    with app() as (at, _):
        assert not any(getattr(e.proto, "label", "") == "What is due today" for e in at.sidebar.get("page_link")) and not any(b.key == "home_qa_today" for b in at.button)


def test_a_card_is_styled_by_its_window_so_a_closed_one_gets_the_grey_edge():
    """The container's name carries the window state ('home_today_open_N' or 'home_today_shut_N'); the grey edge hangs on 'shut'. The test harness cannot see container names, so the source is checked."""
    source = open(os.path.join(os.path.dirname(__file__), "..", "shell_view.py"), encoding="utf-8").read()
    assert "with st.container(key=f\"home_today_{'open' if card['window_kind'] == 'open' else 'shut'}_{index}\"):" in source


def test_until_someone_signs_in_the_app_shows_neither_home_nor_the_sidebar_nor_any_campaign_data():
    """Home carries the sidebar and the Today figures, so the sign-in gate in app.py must stop everything below it."""
    with patch("shell_view.load_hub_bundle", lambda: (_ for _ in ()).throw(AssertionError("data must not be read before signing in"))):
        at = AppTest.from_file(APP)
        at.secrets.update(_dashboard_secrets())
        at.run(timeout=30)
    assert list(at.exception) == [] and not at.sidebar.get("page_link") and not any(b.key == "nav_logout" for b in at.sidebar.button)
    text = " ".join(str(m.value) for m in at.markdown)
    assert "sh-greet" not in text and "sh-tc-name" not in text and "Email Outreach" not in text
