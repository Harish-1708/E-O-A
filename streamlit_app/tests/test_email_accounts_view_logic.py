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


# ================================================================ the Add account dialog: live hints and readiness

MANAGED = {"sales1": {"slot": 1, "address": "a@x"}, "sales2": {"slot": 2, "address": "b@x"}}


@pytest.mark.parametrize("name, legacy, kind", [("", (), "hint"), ("   ", (), "hint"), (None, (), "hint"), ("sales1", (), "error"), (" sales2 ", (), "error"), ("old_one", ("old_one",), "info"),
                                                ("sales5", (), "ok"), ("sales5", ("old_one",), "ok"), ("sales1", ("sales1",), "error")])
def test_the_name_hint_follows_the_real_rules(name, legacy, kind):
    assert V.account_name_feedback(name, MANAGED, legacy)[0] == kind


def test_the_name_hints_say_where_to_go_and_what_adding_would_do():
    assert V.account_name_feedback("sales1", MANAGED, ())[1] == "“sales1” already exists (slot 1). Use Edit on its card to change it."
    assert V.account_name_feedback("old_one", MANAGED, ("old_one",))[1] == "“old_one” is set up in Streamlit secrets. Adding it here takes over managing it."
    assert V.account_name_feedback("", MANAGED, ())[1].startswith("A short label") and V.account_name_feedback("x", MANAGED, ())[1].startswith("Free to use")


@pytest.mark.parametrize("address, kind", [("", "hint"), ("  ", "hint"), (None, "hint"), ("sales5.kelson.agency", "error"), ("@kelson.agency", "error"), ("sales5@", "error"), ("a@b", "ok"), (" sales5@kelson.agency ", "ok")])
def test_the_address_hint_flags_only_what_cannot_be_an_address(address, kind):
    assert V.address_feedback(address)[0] == kind
    if kind != "error":
        assert V.address_feedback(address)[1] == ""


@pytest.mark.parametrize("name, address, ready", [("sales5", "a@b.com", True), ("sales5", "nope", False), ("", "a@b.com", False), ("sales1", "a@b.com", False), ("sales5", "", False), (None, None, False)])
def test_the_button_may_be_pressed_only_with_a_free_name_and_a_real_address(name, address, ready):
    assert V.add_form_ready(name, address, MANAGED) is ready


def test_the_password_is_deliberately_not_part_of_whether_add_may_be_pressed():
    """REGRESSION (seen in a real browser): a password typed and left with Tab is not handed to the app until focus leaves its show/hide eye, so waiting for it left Add stuck."""
    import inspect
    assert list(inspect.signature(V.add_form_ready).parameters) == ["name", "address", "managed"]


# ================================================================ the Bulk add dialog's logic

def test_the_required_columns_are_what_the_parser_requires_and_every_other_column_is_optional():
    from email_account_slots_logic import BULK_ACCOUNT_CSV_COLUMNS
    assert V.BULK_REQUIRED_COLUMNS == ["Email", "Password"] and V.BULK_OPTIONAL_COLUMNS == ["Name", "IMAP Password", "SMTP Host", "SMTP Port", "SMTP Username", "IMAP Host", "IMAP Port", "IMAP Username"]
    assert sorted(V.BULK_REQUIRED_COLUMNS + V.BULK_OPTIONAL_COLUMNS) == sorted(BULK_ACCOUNT_CSV_COLUMNS)


def test_a_file_with_only_the_required_columns_is_really_accepted_by_the_parser():
    """So the chips never tell anyone a column is needed that is not (the old text said Name was required; it is not)."""
    from email_account_slots_logic import parse_bulk_accounts_csv
    parsed, errors = parse_bulk_accounts_csv(["Email", "Password"], [{"Email": "a@x.com", "Password": "pw"}])
    assert errors == [] and parsed[0]["name"] == "a"
    assert parse_bulk_accounts_csv(["Name", "Email"], [{"Name": "n", "Email": "a@x.com"}])[1] == ["CSV is missing required column(s): Password"]


def test_the_template_has_the_real_headings_in_order_and_a_gmail_and_a_custom_provider_example():
    import csv
    import io
    from email_account_slots_logic import BULK_ACCOUNT_CSV_COLUMNS
    rows = list(csv.reader(io.StringIO(V.bulk_template_csv().decode("utf-8"))))
    assert rows[0] == BULK_ACCOUNT_CSV_COLUMNS and len(rows) == 3 and all(len(r) == len(BULK_ACCOUNT_CSV_COLUMNS) for r in rows)
    assert rows[1][:3] == ["sales1", "sales1@gmail.com", "app-password-here"] and "smtp.hostinger.com" in rows[2]


def test_the_review_table_marks_a_row_custom_only_when_it_has_its_own_smtp_host():
    parsed = [{"name": "a", "address": "a@x.com", "smtp_host": None}, {"name": "b", "address": "b@x.com", "smtp_host": "smtp.hostinger.com"}, {"name": "c", "address": "c@x.com"}]
    assert V.bulk_review_rows(parsed) == {"Name": ["a", "b", "c"], "Email": ["a@x.com", "b@x.com", "c@x.com"], "Provider": ["Gmail", "Custom", "Gmail"], "Status": ["✓ Ready"] * 3}
    assert V.bulk_review_rows([]) == {"Name": [], "Email": [], "Provider": [], "Status": []}


@pytest.mark.parametrize("parsed, errors, ready, skipped", [([{}] * 3, [], 3, 0), ([{}], ["Row 2: Email is required."], 1, 1), ([], ["CSV is missing required column(s): Password"], 0, 1), ([], [], 0, 0), ([{}, {}], ["Row 1: a", "Row 4: b", "Row 7: c"], 2, 3)])
def test_the_counts_are_ready_rows_and_skipped_messages(parsed, errors, ready, skipped):
    assert V.bulk_counts(parsed, errors) == {"ready": ready, "skipped": skipped}
