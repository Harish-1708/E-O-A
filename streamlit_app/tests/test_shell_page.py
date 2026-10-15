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


def app(rows=None, details=None, directory=None, health=None, boom=None, hour=21, cron=43, prime=None):
    @contextlib.contextmanager
    def _ctx():
        switched = []

        def bundle():
            if boom:
                raise boom
            return (ROWS if rows is None else rows), [], [], (DETAILS if details is None else details)
        patches = [patch("shell_view.load_hub_bundle", bundle), patch("shell_view.load_sender_overview", lambda: {"directory": DIRECTORY if directory is None else directory, "health": HEALTH if health is None else health}),
                   patch("shell_view.auto_send_cron_minute", lambda: cron), patch("shell_view.local_now", lambda: datetime(2026, 10, 15, hour, 5, tzinfo=timezone.utc)),
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
        assert [label for label, _ in links] == ["Home", "Today", "Performance", "Campaigns", "Responses", "Email Accounts"]


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


def test_the_four_figures_come_from_the_data():
    with app() as (at, _):
        values = re.findall(r'sh-stat-v">(.*?)</div>', markup(at))
        assert values[0] == "1" and values[1] == "41" and values[2] == "2" and values[3].startswith("2 ")
        assert "of 3 campaigns" in markup(at) and "of 100 daily limit" in markup(at)


def test_a_running_campaign_shows_progress_against_its_daily_limit_and_has_an_open_button():
    with app() as (at, _):
        text = markup(at)
        assert 'sh-run-name">Live<' in text and "<b>41</b> of 100 sent today" in text and 'style="width:41%"' in text
        assert any(b.key == "home_open_Live" for b in at.button)


def test_with_nothing_running_it_says_so():
    with app(rows=[{"name": "Done", "status": "completed", "problems": []}]) as (at, _):
        assert any("No campaigns are running right now." in c.value for c in at.caption)


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
        for title in ("Today", "Performance", "Campaigns", "Responses", "Email Accounts"):
            assert f'sh-gt">{title}<' in text
        assert text.count('class="sh-gi"') == 5


def test_if_the_live_data_cannot_be_read_the_page_says_so_and_the_rest_still_works():
    with app(boom=RuntimeError("sheet unreachable")) as (at, _):
        text = markup(at)
        assert "Couldn’t load the live status just now (sheet unreachable)" in text and "The rest of this page still works." in text
        assert any(b.key == "home_new_campaign" for b in at.button) and text.count('class="sh-gi"') == 5 and 'class="sh-skel"' not in text


def test_the_next_automatic_send_is_shown_when_the_schedule_of_runs_is_known_and_said_plainly_when_not():
    with app(cron=43) as (at, _):
        assert "not scheduled in the next 8 days" not in markup(at) and "sh-run-v" in markup(at)
    with app(cron=None) as (at, _):
        assert "not scheduled in the next 8 days" in markup(at)


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
