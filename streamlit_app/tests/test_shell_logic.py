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


def test_the_navigation_lists_the_five_pages_in_order_with_a_path_for_all_but_home():
    assert [key for key, *_ in S.NAV_ITEMS] == ["home", "performance", "campaigns", "responses", "accounts"]                # Today is a part of Home now, not a page
    assert [path for _, _, path, _ in S.NAV_ITEMS] == [None, "pages/performance.py", "pages/campaigns.py", "pages/responses.py", "pages/email_accounts.py"]
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



# ================================================================ Today, as a part of Home (the engine's own rule, with the clock pinned)

import time as _time  # noqa: E402

import outreach  # noqa: E402
import test_work_queue_logic as _wq  # noqa: E402
from test_work_queue_logic import make_cfg, make_lead, sent_field  # noqa: E402


@pytest.fixture
def at(monkeypatch):
    """at("2026-10-15 14:00") pins the engine's clock (it reads the machine's own, which on Streamlit Cloud is UTC) and returns that instant as an aware datetime."""
    old = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    _time.tzset()
    monkeypatch.setattr(outreach, "datetime", _wq._Clock)

    def _set(text):
        _wq._Clock._utc = datetime.strptime(text, "%Y-%m-%d %H:%M")
        return _wq._Clock._utc.replace(tzinfo=timezone.utc)
    yield _set
    if old is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = old
    _time.tzset()

THURSDAY_10AM = "2026-10-15 14:00"          # 10:00 in New York, inside the 9-to-5 window
SATURDAY = "2026-10-17 15:00"               # outside the window: closed until Monday
RUN_ROW = {"name": "Camp", "status": "running", "problems": []}


def _leads():
    """lead 1: intro sent 5 days ago, so follow-up 1 is due; lead 2: intro sent yesterday, so it is waiting; lead 3: never contacted, so the intro is due; lead 4: replied."""
    return [make_lead(1, **{sent_field(0): "2026-10-10 14:00:00"}), make_lead(2, **{sent_field(0): "2026-10-14 14:00:00"}), make_lead(3),
            make_lead(4, **{sent_field(0): "2026-10-10 14:00:00", "ReplyStatus": "Replied", "Status": "Stopped - Replied"})]


def _section(now, cfg=None, leads=None, rows=None, details=None):
    cfg = cfg or make_cfg()
    return S.today_section(rows or [RUN_ROW], details or {"Camp": {"cfg": cfg, "leads": _leads() if leads is None else leads, "responses": [], "send_log": []}}, now)


@pytest.mark.parametrize("name, label", [("intro", "Intro"), ("followup1", "Follow-up 1"), ("followup12", "Follow-up 12"), ("followup", "followup"), ("Welcome", "Welcome"), ("", ""), (None, "")])
def test_stage_names_read_as_words(name, label):
    assert S.stage_label(name) == label


@pytest.mark.parametrize("window, kind, words", [({"open": True}, "open", "● Open now"), ({"open": False, "next_open": "Fri 16 Oct, 9:00 AM EDT"}, "shut", "○ Closed · opens Fri 16 Oct, 9:00 AM EDT"),
                                                 ({"open": False, "next_open": ""}, "shut", "○ Closed"), ({"open": None}, "unknown", "Sending window unknown"), ({}, "unknown", "Sending window unknown"),
                                                 (None, "unknown", "Sending window unknown")])
def test_the_sending_window_pill_says_open_closed_with_when_or_unknown(window, kind, words):
    assert S.window_pill(window) == (kind, words)


def test_a_running_campaign_with_its_window_open_shows_whats_due_waiting_and_the_summary(at):
    card = _section(at(THURSDAY_10AM))["cards"][0]
    assert (card["name"], card["window_kind"], card["window_pill"], card["window_state"], card["window_note"]) == ("Camp", "open", "● Open now", "Open", "")
    assert (card["due"], card["waiting"], card["total_leads"], card["in_sequence"]) == (2, 1, 4, 3)                    # follow-up 1 for lead 1 and the intro for lead 3 are due; lead 2 waits
    assert card["summary"].startswith("2 leads are due and sending is open") and card["next_due"] != ""
    assert dict(card["out_of_play"]) == {"Replied": 1}


def test_a_closed_window_says_when_it_opens_and_why_it_is_closed(at):
    card = _section(at(SATURDAY))["cards"][0]
    assert (card["window_kind"], card["window_state"]) == ("shut", "Closed") and card["window_pill"].startswith("○ Closed · opens ") and "Mon" in card["window_pill"]
    assert card["window_note"].startswith("Today (sat) is not one of this campaign's send_days")
    assert card["due"] == 3 and card["summary"].startswith("3 leads are due. They will go out when sending next opens")           # by Saturday lead 2's wait has passed too


def test_the_stage_breakdown_lists_only_stages_with_someone_due_or_waiting_with_readable_names(at):
    stages = _section(at(THURSDAY_10AM))["cards"][0]["stages"]
    assert [(s["label"], s["due_now"], s["waiting"]) for s in stages] == [("Intro", 1, 0), ("Follow-up 1", 1, 1)]
    assert _section(at(THURSDAY_10AM), leads=[])["cards"][0]["stages"] == []                             # nobody anywhere: no rows


def test_today_sent_is_counted_against_the_daily_limit_and_the_bar_stops_at_full(at):
    sent_today_leads = [make_lead(i, **{sent_field(0): "2026-10-15 13:00:00"}) for i in range(1, 4)]
    card = _section(at(THURSDAY_10AM), cfg=make_cfg(daily_limit=4), leads=sent_today_leads)["cards"][0]
    assert (card["sent_today"], card["limit"], card["percent"]) == (3, 4, 75)
    over = _section(at(THURSDAY_10AM), cfg=make_cfg(daily_limit=2), leads=sent_today_leads)["cards"][0]
    assert (over["sent_today"], over["limit"], over["percent"]) == (3, 2, 100)


def test_a_campaign_with_no_daily_limit_has_no_limit_and_an_empty_bar(at):
    card = _section(at(THURSDAY_10AM), cfg=make_cfg(daily_limit=None), leads=[make_lead(1, **{sent_field(0): "2026-10-15 13:00:00"})])["cards"][0]
    assert (card["sent_today"], card["limit"], card["percent"]) == (1, None, 0)


def test_the_totals_add_up_the_running_campaigns_only_and_the_limit_total_only_counts_those_with_a_limit(at):
    rows = [dict(RUN_ROW, name="A"), dict(RUN_ROW, name="B"), dict(RUN_ROW, name="C"), {"name": "Old", "status": "completed", "problems": []}]
    details = {n: {"cfg": dict(make_cfg(daily_limit=limit), _campaign_name=n), "leads": _leads(), "responses": [], "send_log": []} for n, limit in (("A", 100), ("B", 60), ("C", None))}
    details["Old"] = {"cfg": make_cfg(), "leads": _leads(), "responses": [], "send_log": []}                 # a completed campaign's data is never counted
    section = _section(at(THURSDAY_10AM), rows=rows, details=details)
    assert section["tiles"] == {"running": 3, "due": 6, "waiting": 3, "sent_today": 0, "limit_total": 160, "open": 3}
    assert section["not_running"] == [("Old", "completed")] and section["errors"] == []


def test_with_nothing_running_there_are_no_cards_and_the_totals_are_zero(at):
    section = S.today_section([{"name": "Old", "status": "completed"}, {"name": "Soon", "status": "draft"}], {}, at(THURSDAY_10AM))
    assert section["cards"] == [] and section["tiles"] == {"running": 0, "due": 0, "waiting": 0, "sent_today": 0, "limit_total": None, "open": 0}
    assert section["not_running"] == [("Old", "completed"), ("Soon", "draft")]


def test_a_closed_window_is_not_counted_as_open_in_the_totals(at):
    assert _section(at(SATURDAY))["tiles"]["open"] == 0 and _section(at(THURSDAY_10AM))["tiles"]["open"] == 1


def test_a_running_campaign_whose_data_is_missing_is_reported_and_does_not_hide_the_others(at):
    rows = [dict(RUN_ROW, name="NoData_first"), dict(RUN_ROW, name="Fine"), dict(RUN_ROW, name="NoData_last")]          # broken ones before AND after a good one
    details = {"Fine": {"cfg": make_cfg(), "leads": _leads(), "responses": [], "send_log": []}}
    section = _section(at(THURSDAY_10AM), rows=rows, details=details)
    assert [c["name"] for c in section["cards"]] == ["Fine"]
    assert section["errors"] == [("NoData_first", "its data could not be read"), ("NoData_last", "its data could not be read")]


def test_a_campaign_whose_queue_cannot_be_built_is_reported_with_the_reason(at, monkeypatch):
    def boom(cfg, leads, now):
        raise ValueError("bad schedule: " + "x" * 300)
    monkeypatch.setattr(S, "build_campaign_queue", boom)
    section = _section(at(THURSDAY_10AM))
    assert section["cards"] == [] and section["errors"][0][0] == "Camp" and section["errors"][0][1].startswith("bad schedule") and len(section["errors"][0][1]) <= 120


def test_the_summary_ties_attention_and_today_together(at):
    rows = [RUN_ROW, {"name": "Stuck", "status": "attention", "problems": ["No leads with an email address"]}]
    details = {"Camp": {"cfg": make_cfg(), "leads": _leads(), "responses": [{"ResponseID": "1", "LeadID": "4"}], "send_log": []}}
    summary = S.home_summary(rows, details, set(), DIRECTORY, HEALTH, at(THURSDAY_10AM))
    assert summary["campaigns"] == 2 and summary["unread_total"] == 1 and summary["accounts"]["bad"] == 1
    assert [i["kind"] for i in summary["attention"]] == ["unread", "account", "campaign"] and summary["today"]["tiles"]["due"] == 2
    assert set(summary) == {"campaigns", "unread_total", "unread_by_campaign", "accounts", "attention", "today"}


def test_the_card_carries_the_name_from_the_campaign_list_even_if_the_settings_have_no_name(at):
    cfg = make_cfg()
    cfg.pop("_campaign_name", None)
    card = _section(at(THURSDAY_10AM), cfg=cfg, rows=[dict(RUN_ROW, name="From_The_List")], details={"From_The_List": {"cfg": cfg, "leads": _leads(), "responses": [], "send_log": []}})["cards"][0]
    assert card["name"] == "From_The_List"
