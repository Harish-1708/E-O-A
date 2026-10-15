"""The Settings tab's surroundings: account rows, what the account choices will do, the live limits check and the Readiness checklist."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import settings_view_logic as S  # noqa: E402


# ================================================================ health

@pytest.mark.parametrize("record, words, kind", [
    ({"status": "Connected"}, "Healthy", "ok"), ({"status": " connected "}, "Healthy", "ok"), ({"status": "CONNECTED", "detail": "ignored"}, "Healthy", "ok"),
    ({"status": "Disconnected", "detail": "Login failed"}, "Disconnected — Login failed", "bad"), ({"status": "disconnected"}, "Disconnected", "bad"),
    ({"status": "Disconnected", "detail": "  "}, "Disconnected", "bad"),
    ({"status": "Unknown"}, "Not checked yet", "unknown"), ({"status": ""}, "Not checked yet", "unknown"), ({}, "Not checked yet", "unknown"), (None, "Not checked yet", "unknown"),
    ({"status": "Something new"}, "Not checked yet", "unknown"),
])
def test_an_accounts_last_health_check_is_put_in_words(record, words, kind):
    assert S.health_text(record) == (words, kind)


# ================================================================ which accounts will send (the engine's own rules)

ALL = ["sales1", "sales2", "sales3", "outreach"]


@pytest.mark.parametrize("picked, rotation, default, expected", [
    (["sales2"], False, "sales1", ["sales2"]), (["sales2", "sales3"], False, "sales1", ["sales2", "sales3"]), (["sales2"], True, "sales1", ["sales2"]),     # a list decides, rotation or not
    ([], True, "sales1", ALL), ([], False, "sales1", ["sales1"]), ([], False, "", []), ([], False, "ghost", []),
])
def test_the_accounts_that_will_send_follow_the_engines_rules(picked, rotation, default, expected):
    assert S.pool_names(picked, rotation, ALL, default) == expected


def test_the_pool_is_a_copy_so_it_can_never_change_the_callers_list():
    picked = ["sales1"]
    S.pool_names(picked, False, ALL).append("x")
    assert picked == ["sales1"]


@pytest.mark.parametrize("picked, rotation, default, sentence", [
    (["sales1"], False, "", "All emails go out from sales1."), (["sales1"], True, "", "All emails go out from sales1."),
    (["sales1", "sales2"], True, "", "Emails go out from 2 accounts, taking turns: sales1, sales2."),
    (["sales1", "sales2", "sales3"], False, "", "Emails go out from 3 accounts, taking turns: sales1, sales2, sales3."),       # the engine rotates a picked list even with rotation off
    ([], True, "", "No accounts picked, so every configured account takes turns."),
    ([], False, "sales1", "No accounts picked and rotation is off, so the default sender, sales1, is used."),
    ([], False, "", "No accounts picked and rotation is off, so the campaign's default sender is used."),
])
def test_one_sentence_says_what_the_account_choices_will_really_do(picked, rotation, default, sentence):
    assert S.rotation_summary(picked, rotation, default) == sentence


@pytest.mark.parametrize("on, total, text", [(3, 4, "Select all · 3 of 4 on"), (0, 4, "Select all · 0 of 4 on"), (4, 4, "Select all · 4 of 4 on"), (0, 0, "Select all · 0 of 0 on")])
def test_the_select_all_line_counts_what_is_on(on, total, text):
    assert S.select_all_text(on, total) == text


@pytest.mark.parametrize("stored, picked, names, expected", [
    (["sales3", "sales1"], {"sales1", "sales3"}, ["sales1", "sales2", "sales3"], ["sales3", "sales1"]),                 # unchanged: the saved order is kept
    (["sales3", "sales1"], {"sales1", "sales3", "sales2"}, ["sales1", "sales2", "sales3"], ["sales3", "sales1", "sales2"]),   # a new one goes on the end
    (["sales3", "sales1"], {"sales1"}, ["sales1", "sales2", "sales3"], ["sales1"]),                                    # one switched off
    ([], {"sales2", "sales1"}, ["sales1", "sales2", "sales3"], ["sales1", "sales2"]),                                  # nothing saved yet: name order
    (["ghost", "sales1"], {"ghost", "sales1"}, ["sales1", "sales2"], ["sales1"]),                                      # an account that no longer exists is dropped
    (["sales1", "sales1"], {"sales1"}, ["sales1"], ["sales1"]), (None, {"sales1"}, ["sales1"], ["sales1"]), (["sales1"], set(), ["sales1"], []),
    (["sales1"], {"sales9"}, ["sales1"], []),                                                                          # picked but not configured: not saved
])
def test_the_saved_list_keeps_its_order_and_only_ever_holds_configured_accounts(stored, picked, names, expected):
    assert S.ordered_picks(stored, picked, names) == expected


# ================================================================ the account rows

DIRECTORY = {"sales2": "sales2@kelson.agency", "sales1": "sales1@kelson.agency", "outreach": "outreach@kelson.agency"}
HEALTH = {"sales1": {"status": "Connected"}, "sales2": {"status": "Disconnected", "detail": "Login failed"}}


def test_there_is_one_row_per_account_sorted_by_name_with_its_address_and_todays_sends():
    rows = S.account_rows(DIRECTORY, HEALTH, {"sales1": 17, "sales2": 13}, ["sales1", "sales2"], True, 40)
    assert [r["name"] for r in rows] == ["outreach", "sales1", "sales2"]
    by = {r["name"]: r for r in rows}
    assert by["sales1"]["address"] == "sales1@kelson.agency" and by["sales1"]["sent"] == 17 and by["outreach"]["sent"] == 0


def test_a_picked_account_shows_its_health_and_an_unpicked_one_says_why_it_is_not_in_use():
    by = {r["name"]: r for r in S.account_rows(DIRECTORY, HEALTH, {}, ["sales1", "sales2"], True, None)}
    assert by["sales1"]["note"] == "Healthy" and by["sales2"]["note"] == "Disconnected — Login failed" and by["outreach"]["note"] == "Not used in this campaign"
    assert by["sales1"]["on"] and by["sales2"]["on"] and not by["outreach"]["on"] and by["sales2"]["health_kind"] == "bad" and by["outreach"]["health_kind"] == "unknown"


@pytest.mark.parametrize("rotation, note", [(True, "Not picked — used anyway while none is picked"), (False, "Not picked")])
def test_with_nothing_picked_the_rows_explain_that_the_engine_decides(rotation, note):
    assert {r["note"] for r in S.account_rows(DIRECTORY, {}, {}, [], rotation, None)} == {note}


@pytest.mark.parametrize("sent, limit, fraction, shown_limit", [(17, 40, 0.425, 40), (0, 40, 0.0, 40), (40, 40, 1.0, 40), (55, 40, 1.0, 40),
                                                                 (5, None, 0.0, None), (5, 0, 0.0, None), (5, -3, 0.0, None), (5, True, 0.0, None)])
def test_the_usage_bar_is_todays_sends_against_the_per_account_limit_and_never_more_than_full(sent, limit, fraction, shown_limit):
    row = S.account_rows({"a": "a@x.com"}, {}, {"a": sent}, ["a"], True, limit)[0]
    assert (row["fraction"], row["limit"]) == (fraction, shown_limit)


def test_no_accounts_means_no_rows():
    assert S.account_rows({}, {}, {}, [], True, 40) == []


@pytest.mark.parametrize("query, names", [("", ["outreach", "sales1", "sales2"]), ("  ", ["outreach", "sales1", "sales2"]), ("sales", ["sales1", "sales2"]), ("SALES1", ["sales1"]),
                                          ("kelson", ["outreach", "sales1", "sales2"]), ("outreach@", ["outreach"]), ("zzz", []), (None, ["outreach", "sales1", "sales2"])])
def test_the_search_matches_the_name_or_the_address_without_caring_about_case(query, names):
    rows = S.account_rows(DIRECTORY, {}, {}, [], False, None)
    assert [r["name"] for r in S.filter_accounts(rows, query)] == names


# ================================================================ the live check on the limits

@pytest.mark.parametrize("daily, on, per, n, kind, text", [
    (120, True, 40, 3, "ok", "Per-account limits add up to 120 (40 + 40 + 40) — the same as the campaign limit, so no account is starved."),
    (100, True, 20, 3, "warn", "Per-account limits add up to 60 (20 + 20 + 20) — less than the campaign limit of 100, so the campaign will stop at 60 a day."),
    (100, True, 40, 3, "info", "Per-account limits add up to 120 (40 + 40 + 40) — more than the campaign limit of 100, so the campaign limit is reached first and some accounts may send less than their own limit."),
    (40, True, 40, 1, "ok", "Per-account limits add up to 40 (40) — the same as the campaign limit, so no account is starved."),
    (280, True, 40, 7, "ok", "Per-account limits add up to 280 (40 × 7) — the same as the campaign limit, so no account is starved."),
])
def test_the_limits_check_says_whether_the_per_account_limits_fit_the_campaign_limit(daily, on, per, n, kind, text):
    assert S.limits_check(daily, on, per, n) == {"kind": kind, "text": text}


@pytest.mark.parametrize("daily, on, per, n", [(100, False, 40, 3), (100, True, None, 3), (100, True, 0, 3), (100, True, 40, 0), (0, True, 40, 3), (None, True, 40, 3)])
def test_there_is_no_check_when_there_is_nothing_to_compare(daily, on, per, n):
    assert S.limits_check(daily, on, per, n) is None


# ================================================================ readiness

SCHEDULE_OK = {"timezone": "America/New_York", "window_start": "09:00", "window_end": "17:00", "send_days": ["mon", "tue"]}


@pytest.mark.parametrize("schedule, kind, text", [
    (None, "info", "No schedule is set, so this campaign sends anytime"), ({}, "info", "No schedule is set, so this campaign sends anytime"),
    (SCHEDULE_OK, "ok", "Schedule has send days and a window"), ({**SCHEDULE_OK, "window_start": "22:00", "window_end": "06:00"}, "ok", "Schedule has send days and a window"),
    ({**SCHEDULE_OK, "send_days": []}, "bad", "Schedule needs send days and a valid window"), ({**SCHEDULE_OK, "window_start": "25:00"}, "bad", "Schedule needs send days and a valid window"),
    ({**SCHEDULE_OK, "window_end": ""}, "bad", "Schedule needs send days and a valid window"), ({"timezone": "UTC"}, "bad", "Schedule needs send days and a valid window"),
])
def test_the_schedule_line_is_fine_with_days_and_a_window_a_note_with_no_schedule_and_a_problem_when_it_cannot_work(schedule, kind, text):
    assert S.schedule_readiness(schedule) == {"kind": kind, "text": text}


def _cfg(**kw):
    base = {"stages": [{"name": "intro", "template_prefix": "intro"}], "sending": {}, "_global_default_account": "sales1", "schedule": SCHEDULE_OK}
    base.update(kw)
    return base


def _lead(**kw):
    base = {"Email": "a@x.com", "FirstName": "Ann", "Client": "Acme"}
    base.update(kw)
    return base


def _texts(items):
    return [(i["kind"], i["text"]) for i in items]


def test_a_ready_campaign_has_every_line_ticked_in_the_designs_order():
    items = S.readiness_items(_cfg(), [_lead(), _lead(Email="b@x.com")], ["Hi {{FirstName}}, about {{Client}}"], {}, [])
    assert _texts(items) == [("ok", "Leads imported (2)"), ("ok", "Intro template has all placeholders"), ("ok", "At least one sender account"), ("ok", "Schedule has send days and a window")]


def test_only_leads_with_an_email_address_count_as_imported():
    items = S.readiness_items(_cfg(), [_lead(), _lead(Email=""), _lead(Email="  ")], ["Hi"], {}, [])
    assert items[0] == {"kind": "ok", "text": "Leads imported (1)"}


@pytest.mark.parametrize("leads", [[], [_lead(Email="")]])
def test_no_leads_with_an_email_is_a_problem_in_the_apps_own_words(leads):
    assert S.readiness_items(_cfg(), leads, ["Hi"], {}, [])[0] == {"kind": "bad", "text": "No leads with an email address"}


def test_an_intro_placeholder_that_matches_no_column_is_named():
    items = S.readiness_items(_cfg(), [_lead()], ["Hi {{FirstName}} {{Typo}}"], {}, [])
    assert items[1] == {"kind": "bad", "text": "Intro template uses {{Typo}}, which matches no column"}


def test_every_intro_variant_is_checked_and_each_missing_name_is_listed_once_in_the_order_found():
    items = S.readiness_items(_cfg(), [_lead()], ["{{A}} {{Client}}", "{{B}} {{A}}", "{{FirstName}}"], {}, [])
    assert items[1]["text"] == "Intro template uses {{A}}, {{B}}, which matches no column"


def test_a_custom_column_any_lead_has_counts_as_a_known_placeholder():
    assert S.readiness_items(_cfg(), [_lead(), _lead(Product="Mug")], ["{{Product}}"], {}, [])[1]["kind"] == "ok"


def test_if_the_templates_could_not_be_read_it_says_so_instead_of_guessing():
    assert S.readiness_items(_cfg(), [_lead()], None, {}, [])[1] == {"kind": "info", "text": "Couldn't check the intro template just now"}


def test_with_no_leads_the_placeholders_cannot_be_checked_and_it_says_so():
    assert S.readiness_items(_cfg(), [], ["{{Whatever}}"], {}, [])[1] == {"kind": "info", "text": "Add leads to check the intro template's placeholders"}


def test_a_campaign_with_no_stages_has_a_template_problem():
    assert S.readiness_items(_cfg(stages=[]), [_lead()], ["Hi"], {}, [])[1] == {"kind": "bad", "text": "No template stages found"}


def test_a_campaign_with_no_way_to_name_a_sender_is_flagged():
    items = S.readiness_items(_cfg(_global_default_account=""), [_lead()], ["Hi"], {}, [])
    assert {"kind": "bad", "text": "No sender account configured"} in items
    assert {"kind": "ok", "text": "At least one sender account"} in S.readiness_items(_cfg(_global_default_account="", sending={"sender_rotation": True}), [_lead()], ["Hi"], {}, [])
    assert {"kind": "ok", "text": "At least one sender account"} in S.readiness_items(_cfg(_global_default_account="", sending={"rotation_accounts": ["s1"]}), [_lead()], ["Hi"], {}, [])


def test_a_disconnected_account_in_use_is_flagged_but_a_healthy_or_unused_one_is_not():
    health = {"sales1": {"status": "Connected"}, "sales2": {"status": "Disconnected", "detail": "Login failed"}, "sales3": {"status": "Disconnected", "detail": "x"}}
    items = S.readiness_items(_cfg(), [_lead()], ["Hi"], health, ["sales1", "sales2"])
    assert _texts(items)[-1] == ("warn", "sales2: disconnected — Login failed") and not any("sales3" in i["text"] for i in items) and not any("sales1" in i["text"] for i in items)


def test_building_the_checklist_never_changes_the_leads_or_the_config():
    import copy
    cfg, leads = _cfg(), [_lead()]
    before = copy.deepcopy((cfg, leads))
    S.readiness_items(cfg, leads, ["Hi {{Typo}}"], {}, [])
    assert (cfg, leads) == before



# ================================================================ accounts named by their address, and when the search and select-all are worth showing

DIR = {"sales1": "harish@kelson.agency", "sales2": "outreach@kelson.agency"}


@pytest.mark.parametrize("name, directory, label", [("sales1", DIR, "harish@kelson.agency (sales1)"), ("sales9", DIR, "sales9"), ("sales1", None, "sales1"), ("sales1", {}, "sales1"), ("sales1", {"sales1": ""}, "sales1")])
def test_an_account_is_named_by_its_address_with_the_internal_name_beside_it(name, directory, label):
    assert S.account_label(name, directory) == label


@pytest.mark.parametrize("picked, rotation, default, sentence", [
    (["sales1"], False, "", "All emails go out from harish@kelson.agency (sales1)."),
    (["sales1", "sales2"], True, "", "Emails go out from 2 accounts, taking turns: harish@kelson.agency (sales1), outreach@kelson.agency (sales2)."),
    ([], False, "sales1", "No accounts picked and rotation is off, so the default sender, harish@kelson.agency (sales1), is used."),
    ([], True, "sales1", "No accounts picked, so every configured account takes turns."),
])
def test_the_sentence_names_accounts_by_the_addresses_the_rows_show(picked, rotation, default, sentence):
    """REGRESSION: the rows showed harish@kelson.agency while the sentence under them said 'sales1'."""
    assert S.rotation_summary(picked, rotation, default, DIR) == sentence


@pytest.mark.parametrize("count, search, select_all", [(0, False, False), (1, False, False), (2, False, True), (3, False, True), (4, True, True), (5, True, True), (40, True, True)])
def test_the_search_needs_four_or_more_accounts_and_select_all_at_least_two(count, search, select_all):
    assert (S.show_search(count), S.show_select_all(count)) == (search, select_all)
