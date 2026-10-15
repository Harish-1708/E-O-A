"""The Email Accounts page's pure logic: cards, the health summary, filters, search and showing 25 at a time."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import email_accounts_view_logic as V  # noqa: E402


def _row(name, address, status="Connected", detail="", checked="2026-10-14 23:10:10", sent=0, default=False):
    return {"name": name, "address": address, "status": status, "status_detail": detail, "checked_at": checked, "sent_today": sent, "is_default": default}


ROWS = [_row("sales1", "harish@kelson.agency", sent=41, default=True), _row("sales2", "outreach@kelson.agency", sent=12), _row("sales3", "press@kelson.agency", "Disconnected", "Login failed", sent=0),
        _row("sales4", "legacy@kelson.agency", "Unknown", "", "", 0)]
SLOTS = {"sales1": {"slot": 1, "address": "harish@kelson.agency"}, "sales2": {"slot": 2, "address": "outreach@kelson.agency"}, "sales3": {"slot": 3, "address": "press@kelson.agency"}}


@pytest.mark.parametrize("status, kind", [("Connected", "ok"), ("Disconnected", "bad"), ("Unknown", "unknown"), ("", "unknown"), (None, "unknown"), ("Mystery", "unknown")])
def test_a_status_is_ok_bad_or_unknown(status, kind):
    assert V.status_kind(status) == kind


@pytest.mark.parametrize("address, name, letter", [("harish@kelson.agency", "sales1", "H"), ("", "sales1", "S"), (None, "x", "X"), ("  ", "  ", "?"), ("", "", "?"), ("é@x.com", "a", "É")])
def test_the_avatar_shows_the_first_letter_of_the_address_or_else_the_name(address, name, letter):
    assert V.avatar_initial(address, name) == letter


def test_the_avatar_colour_is_red_when_disconnected_grey_when_unknown_and_otherwise_fixed_by_the_name():
    assert V.avatar_color("x", "bad") == "#dc2626" and V.avatar_color("x", "unknown") == "#9ca3af"
    assert V.avatar_color("sales1", "ok") == V.avatar_color("sales1", "ok") and V.avatar_color("sales1", "ok") in V.PALETTE
    assert len({V.avatar_color(name, "ok") for name in ("a", "b", "c", "d", "e", "f", "g", "h")}) > 3          # different names are not all one colour


def test_cards_carry_what_the_page_shows_and_whether_each_account_can_be_managed():
    cards = {c["name"]: c for c in V.account_cards(ROWS, SLOTS)}
    assert (cards["sales1"]["managed"], cards["sales1"]["slot"], cards["sales1"]["is_default"], cards["sales1"]["sent_today"], cards["sales1"]["kind"]) == (True, 1, True, 41, "ok")
    assert (cards["sales3"]["kind"], cards["sales3"]["detail"], cards["sales3"]["status"]) == ("bad", "Login failed", "Disconnected")
    assert (cards["sales4"]["managed"], cards["sales4"]["slot"], cards["sales4"]["kind"], cards["sales4"]["checked_at"]) == (False, None, "unknown", "")


def test_the_reason_is_only_kept_for_a_disconnected_account():
    cards = {c["name"]: c for c in V.account_cards([_row("a", "a@x", "Connected", "stale reason"), _row("b", "b@x", "Disconnected", "  Login failed  ")], {})}
    assert cards["a"]["detail"] == "" and cards["b"]["detail"] == "Login failed"


def test_with_no_slot_file_every_account_is_legacy_and_nothing_can_be_managed():
    assert not any(c["managed"] for c in V.account_cards(ROWS, {})) and not any(c["managed"] for c in V.account_cards(ROWS, None))


def test_the_summary_adds_up_the_accounts_and_finds_the_latest_check():
    cards = V.account_cards(ROWS + [_row("sales5", "five@x", checked="2026-10-15 01:00:00", sent=5)], SLOTS)
    assert V.summarize_accounts(cards) == {"total": 5, "connected": 3, "bad": 1, "unknown": 1, "sent_today": 58, "last_checked": "2026-10-15 01:00:00"}
    assert V.summarize_accounts([]) == {"total": 0, "connected": 0, "bad": 0, "unknown": 0, "sent_today": 0, "last_checked": ""}


def test_the_filters_show_their_counts_in_a_fixed_order():
    assert V.status_filter_options(V.account_cards(ROWS, SLOTS)) == [("all", "All 4"), ("Connected", "Connected 2"), ("Disconnected", "Disconnected 1"), ("Unknown", "Unknown 1")]
    assert V.status_filter_options([]) == [("all", "All 0"), ("Connected", "Connected 0"), ("Disconnected", "Disconnected 0"), ("Unknown", "Unknown 0")]


@pytest.mark.parametrize("status, query, names", [
    ("all", "", ["sales1", "sales2", "sales3", "sales4"]), (None, "", ["sales1", "sales2", "sales3", "sales4"]), ("Connected", "", ["sales1", "sales2"]), ("Disconnected", "", ["sales3"]),
    ("Unknown", "", ["sales4"]), ("all", "PRESS", ["sales3"]), ("all", "sales2", ["sales2"]), ("all", "kelson", ["sales1", "sales2", "sales3", "sales4"]), ("Connected", "press", []),
    ("all", "  harish ", ["sales1"]), ("all", "zzz", []), ("Nonsense", "", []),
])
def test_filtering_by_status_and_by_text_matches_name_or_address_in_any_case(status, query, names):
    assert [c["name"] for c in V.filter_accounts(V.account_cards(ROWS, SLOTS), status, query)] == names


@pytest.mark.parametrize("count, shown", [(0, False), (3, False), (4, True), (5, True), (500, True)])
def test_the_search_appears_from_four_accounts(count, shown):
    assert V.show_search(count) is shown


@pytest.mark.parametrize("total, shown, drawn, hidden", [(0, 25, 0, 0), (10, 25, 10, 0), (25, 25, 25, 0), (26, 25, 25, 1), (60, 25, 25, 35), (60, 50, 50, 10), (60, 75, 60, 0), (60, 0, 25, 35), (60, None, 25, 35), (60, 3, 25, 35)])
def test_a_long_list_is_drawn_25_at_a_time(total, shown, drawn, hidden):
    assert V.visible_count(total, shown) == (drawn, hidden)


@pytest.mark.parametrize("hidden, label", [(35, "Show 25 more (35 left)"), (26, "Show 25 more (26 left)"), (25, "Show the last 25"), (7, "Show the last 7"), (1, "Show the last 1")])
def test_the_show_more_label_says_how_many_are_left(hidden, label):
    assert V.show_more_label(hidden) == label


@pytest.mark.parametrize("names, text", [([], ""), (["A"], "1 campaign isn’t counted in “sent today” yet (nothing sent for it): A"),
                                         (["A", "B"], "2 campaigns aren’t counted in “sent today” yet (nothing sent for them): A, B")])
def test_the_footnote_about_campaigns_that_have_sent_nothing(names, text):
    assert V.not_counted_note(names) == text
