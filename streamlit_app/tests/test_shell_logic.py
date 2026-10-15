"""The sidebar's and Home's pure logic: greeting, markers, unread counts, account overview, running campaigns and the attention cards."""
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import shell_logic as S  # noqa: E402

NOW = datetime(2026, 10, 15, 14, 0, 0, tzinfo=timezone.utc)
OPEN = lambda schedule, when: (True, "")                  # noqa: E731  a sending window that is always open


@pytest.mark.parametrize("hour, name, text", [(0, "Harish", "Good morning, Harish"), (11, "Harish", "Good morning, Harish"), (12, "Harish", "Good afternoon, Harish"), (17, "Harish", "Good afternoon, Harish"),
                                              (18, "Harish", "Good evening, Harish"), (23, "Harish", "Good evening, Harish"), (9, "", "Good morning"), (None, "Harish", "Hello, Harish"),
                                              (24, "Harish", "Hello, Harish"), (-1, "", "Hello"), ("9", "Harish", "Hello, Harish"), (True, "Harish", "Hello, Harish")])
def test_the_greeting_follows_the_hour_and_never_guesses_when_the_hour_is_unknown(hour, name, text):
    assert S.greeting(hour, name) == text


def test_the_navigation_lists_the_six_pages_in_order_with_a_path_for_all_but_home():
    assert [key for key, *_ in S.NAV_ITEMS] == ["home", "today", "performance", "campaigns", "responses", "accounts"]
    assert [path for _, _, path, _ in S.NAV_ITEMS] == [None, "pages/today.py", "pages/performance.py", "pages/campaigns.py", "pages/responses.py", "pages/email_accounts.py"]
    assert all(icon.startswith(":material/") and icon.endswith(":") for *_, icon in S.NAV_ITEMS)


@pytest.mark.parametrize("unread, disconnected, has_badge, has_dot", [(49, 1, True, True), (3, 0, True, False), (0, 2, False, True), (0, 0, False, False), (-5, -1, False, False),
                                                                      (None, None, False, False), ("7", "1", True, True), ("x", "y", False, False)])
def test_the_sidebar_markers_appear_only_for_a_positive_count(unread, disconnected, has_badge, has_dot):
    css = S.marker_css(unread, disconnected)
    assert ('st-key-nav_responses' in css) is has_badge and ('st-key-nav_accounts' in css) is has_dot
    if has_badge:
        assert f'content: "{int(unread)}"' in css
    if not (has_badge or has_dot):
        assert css == ""


def _resp(rid, lead, read=False):
    return {"ResponseID": rid, "LeadID": lead, "IsRead": "TRUE" if read else ""}


DETAILS = {"A": {"cfg": {}, "responses": [_resp("1", "L1"), _resp("2", "L1"), _resp("3", "L2", read=True), _resp("4", "L3")], "send_log": [], "leads": []},
           "B": {"cfg": {}, "responses": [_resp("1", "L1")], "send_log": [], "leads": []}, "C": {"cfg": {}, "responses": [], "send_log": [], "leads": []}}


def test_unread_replies_are_counted_per_conversation_per_campaign_and_campaigns_with_none_are_left_out():
    assert S.unread_by_campaign(DETAILS, set()) == {"A": 2, "B": 1}                         # L1 wrote twice but counts once; L2's reply is read in the Sheet


def test_a_reply_read_in_this_session_is_not_counted_even_if_the_sheet_does_not_know_yet():
    assert S.unread_by_campaign(DETAILS, {"A:1", "A:2", "A:4"}) == {"B": 1}
    assert S.unread_by_campaign(DETAILS, {"A:1", "A:2", "A:4", "B:1"}) == {}


def test_the_same_reply_id_in_two_campaigns_is_two_different_replies():
    assert S.unread_by_campaign(DETAILS, {"A:1", "A:2"}) == {"A": 1, "B": 1}                # B:1 is not A:1


@pytest.mark.parametrize("details", [None, {}, {"A": {}}, {"A": {"responses": None}}])
def test_no_data_means_no_unread_and_never_an_error(details):
    assert S.unread_by_campaign(details, set()) == {}


HEALTH = {"sales1": {"status": "Connected", "detail": "", "checked_at": "2026-10-14 23:10:10"}, "sales2": {"status": "Connected", "detail": "", "checked_at": "2026-10-15 01:00:00"},
          "sales3": {"status": "Disconnected", "detail": "Login failed", "checked_at": "2026-10-14 23:10:10"}, "gone": {"status": "Disconnected", "detail": "x", "checked_at": "2026-10-15 02:00:00"}}
DIRECTORY = {"sales1": "a@kelson.agency", "sales2": "b@kelson.agency", "sales3": "c@kelson.agency", "sales4": "d@kelson.agency"}


def test_the_account_overview_counts_connected_and_disconnected_among_the_configured_accounts_only():
    overview = S.account_overview(DIRECTORY, HEALTH)
    assert (overview["total"], overview["connected"], overview["bad"]) == (4, 2, 1)                   # 'gone' is in the health sheet but not configured: ignored; sales4 was never checked
    assert overview["bad_accounts"] == [{"name": "sales3", "address": "c@kelson.agency", "detail": "Login failed"}] and overview["last_checked"] == "2026-10-15 01:00:00"


def test_with_no_accounts_or_no_health_the_overview_is_empty_not_an_error():
    assert S.account_overview({}, {}) == {"total": 0, "connected": 0, "bad": 0, "bad_accounts": [], "last_checked": ""}
    overview = S.account_overview(DIRECTORY, {})
    assert (overview["total"], overview["connected"], overview["bad"]) == (4, 0, 0)
    assert S.account_overview(None, None)["total"] == 0


def _cfg(limit=100, tz="UTC", minute=None):
    return {"sending": {"daily_limit": limit} if limit else {}, "schedule": {"timezone": tz, "window_start": "09:00", "window_end": "18:00", "send_days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]}}


def _sent(n, day="2026-10-15"):
    return [{"Timestamp": f"{day} 10:00:00", "Status": "sent"} for _ in range(n)]


ROWS = [{"name": "Live", "status": "running", "problems": []}, {"name": "Stuck", "status": "attention", "problems": ["No leads with an email address", "second problem"]},
        {"name": "Done", "status": "completed", "problems": []}, {"name": "Soon", "status": "draft", "problems": []}]
RUN_DETAILS = {"Live": {"cfg": _cfg(100), "send_log": _sent(41) + _sent(9, "2026-10-14"), "responses": [], "leads": []}, "Done": {"cfg": _cfg(50), "send_log": _sent(5), "responses": [], "leads": []}}


def test_only_running_campaigns_are_listed_with_today_against_their_limit():
    rows = S.running_rows(ROWS, RUN_DETAILS, NOW, 43, OPEN)
    assert len(rows) == 1 and rows[0]["name"] == "Live" and (rows[0]["sent_today"], rows[0]["limit"], rows[0]["percent"]) == (41, 100, 41)


def test_yesterdays_sends_are_not_todays_and_a_campaign_over_its_limit_stops_at_a_full_bar():
    over = {"Live": {"cfg": _cfg(10), "send_log": _sent(41), "responses": [], "leads": []}}
    row = S.running_rows(ROWS, over, NOW, 43, OPEN)[0]
    assert (row["sent_today"], row["limit"], row["percent"]) == (41, 10, 100)


def test_a_campaign_with_no_daily_limit_has_no_limit_and_an_empty_bar():
    row = S.running_rows(ROWS, {"Live": {"cfg": _cfg(None), "send_log": _sent(7), "responses": [], "leads": []}}, NOW, 43, OPEN)[0]
    assert (row["sent_today"], row["limit"], row["percent"]) == (7, None, 0)


def test_the_next_automatic_send_is_said_without_its_label_and_left_empty_when_the_schedule_of_runs_is_unknown():
    with_minute = S.running_rows(ROWS, RUN_DETAILS, NOW, 43, OPEN)[0]["next_send"]
    assert with_minute and not with_minute.startswith("Next automatic send") and "(" in with_minute
    assert S.running_rows(ROWS, RUN_DETAILS, NOW, None, OPEN)[0]["next_send"] == ""


def test_a_running_campaign_whose_data_could_not_be_read_is_still_listed_with_zero_sent():
    row = S.running_rows(ROWS, {}, NOW, 43, OPEN)[0]
    assert row["name"] == "Live" and row["sent_today"] == 0 and row["limit"] is None and row["next_send"] == ""


def test_the_attention_cards_say_only_what_is_true_and_name_it():
    accounts = S.account_overview(DIRECTORY, HEALTH)
    items = S.attention_items({"A": 2, "B": 1}, accounts, ROWS)
    assert [i["kind"] for i in items] == ["unread", "account", "campaign"] and [i["link"] for i in items] == ["responses", "accounts", "campaigns"]
    assert items[0]["title"] == "3 unread replies" and items[0]["detail"] == "Across 2 campaigns."
    assert items[1]["title"] == "1 account disconnected" and items[1]["detail"] == "c@kelson.agency · Login failed. Campaigns using it cannot send."
    assert items[2]["title"] == "1 campaign needs attention" and items[2]["detail"] == "Stuck · No leads with an email address."


def test_one_unread_reply_in_one_campaign_is_singular_and_names_the_campaign():
    item = S.attention_items({"Live": 1}, {"bad": 0, "bad_accounts": []}, [])[0]
    assert item["title"] == "1 unread reply" and item["detail"] == "In Live."


def test_several_disconnected_accounts_and_stuck_campaigns_say_how_many_more():
    accounts = {"bad": 3, "bad_accounts": [{"name": "s1", "address": "", "detail": ""}, {}, {}]}
    rows = [{"name": "X", "status": "attention", "problems": []}, {"name": "Y", "status": "attention", "problems": []}]
    items = S.attention_items({}, accounts, rows)
    assert items[0]["title"] == "3 accounts disconnected" and items[0]["detail"] == "s1 (and 2 more). Campaigns using it cannot send."
    assert items[1]["title"] == "2 campaigns need attention" and items[1]["detail"] == "X (and 1 more)."


def test_when_nothing_needs_attention_there_are_no_cards():
    assert S.attention_items({}, {"bad": 0, "bad_accounts": []}, [{"name": "Live", "status": "running", "problems": []}]) == []


def test_the_summary_ties_it_all_together():
    summary = S.home_summary(ROWS, {**RUN_DETAILS, **DETAILS}, set(), DIRECTORY, HEALTH, NOW, 43, OPEN)
    assert summary["campaigns"] == 4 and summary["running"] == 1 and summary["sent_today"] == 46 and summary["limit_total"] == 100
    assert summary["unread_total"] == 3 and summary["accounts"]["bad"] == 1 and [i["kind"] for i in summary["attention"]] == ["unread", "account", "campaign"]


def test_with_nothing_running_there_is_no_limit_total_and_nothing_sent_counts_zero():
    summary = S.home_summary([], {}, set(), {}, {}, NOW, 43, OPEN)
    assert (summary["running"], summary["sent_today"], summary["limit_total"], summary["unread_total"], summary["attention"]) == (0, 0, None, 0, [])


def test_the_limit_total_adds_up_only_the_running_campaigns_that_have_a_limit():
    rows = [{"name": "A", "status": "running", "problems": []}, {"name": "B", "status": "running", "problems": []}, {"name": "C", "status": "running", "problems": []}]
    details = {"A": {"cfg": _cfg(100), "send_log": [], "responses": [], "leads": []}, "B": {"cfg": _cfg(60), "send_log": [], "responses": [], "leads": []}, "C": {"cfg": _cfg(None), "send_log": [], "responses": [], "leads": []}}
    assert S.home_summary(rows, details, set(), {}, {}, NOW, 43, OPEN)["limit_total"] == 160


class _P:
    def __init__(self, url_path):
        self.url_path = url_path


@pytest.mark.parametrize("page, current, expected", [(_P(""), _P(""), True), (_P("today"), _P("today"), True), (_P("today"), _P(""), False), (_P(""), _P("today"), False), (None, _P(""), False), (_P(""), None, False),
                                                     (object(), _P(""), False)])
def test_the_current_page_is_found_by_its_url_path_and_a_missing_one_is_never_current(page, current, expected):
    assert S.is_current_page(page, current) is expected


def test_the_active_link_gets_its_own_container_name_and_both_forms_still_contain_the_key_the_markers_use():
    assert S.nav_key("responses", False) == "nav_responses" and S.nav_key("responses", True) == "nav_responses__active"
    assert "nav_responses" in S.nav_key("responses", True) and "__active" not in S.nav_key("responses", False)
    assert S.marker_css(3, 0).startswith('[class*="st-key-nav_responses"]')                      # the marker rule matches the container in either form
