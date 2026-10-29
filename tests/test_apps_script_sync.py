"""The Google page (apps_script/Code.gs) and the Python engine must agree on the link format, the tab and the reasons. Node tests prove the script's behaviour (apps_script/test_unsubscribe.js);
these tests run anywhere Python does, so GitHub's own CI also notices if one side is changed without the other."""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import outreach  # noqa: E402
import unsubscribe_logic as ul  # noqa: E402
from test_unsubscribe_logic import KNOWN_PLAIN, KNOWN_UNICODE_TEST, SECRET  # noqa: E402

CODE_PATH = os.path.join(os.path.dirname(__file__), "..", "apps_script", "Code.gs")


@pytest.fixture(scope="module")
def code():
    with open(CODE_PATH, encoding="utf-8") as handle:
        return handle.read()


def js_string(code, name):
    match = re.search(rf"var {name} = '([^']*)';", code)
    assert match, f"{name} not found in Code.gs"
    return match.group(1)


def js_list(code, name):
    match = re.search(rf"var {name} = \[(.*?)\];", code, re.S)
    assert match, f"{name} not found in Code.gs"
    return re.findall(r"'([^']*)'", match.group(1))


def js_number(code, name):
    match = re.search(rf"var {name} = (\d+);", code)
    assert match, f"{name} not found in Code.gs"
    return int(match.group(1))


def test_the_script_holds_the_very_links_the_python_engine_makes_today(code):
    assert js_string(code, "SELF_TEST_SECRET") == SECRET and js_string(code, "SELF_TEST_PLAIN") == KNOWN_PLAIN and js_string(code, "SELF_TEST_UNICODE_TEST") == KNOWN_UNICODE_TEST


def test_the_links_in_the_script_are_ones_the_python_side_accepts_and_reads_as_the_script_expects(code):
    plain = ul.verify_token(SECRET, js_string(code, "SELF_TEST_PLAIN"))
    assert plain["b"] == "Kelson Agency" and plain["e"] == "ann@example.com" and plain["k"] == "kelsonagency" and "t" not in plain
    unicode_link = ul.verify_token(SECRET, js_string(code, "SELF_TEST_UNICODE_TEST"))
    assert unicode_link["b"] == "Café Ünïcode ☕" and unicode_link["t"] == 1


def test_the_tab_the_columns_and_the_reasons_are_the_same_on_both_sides(code):
    assert js_string(code, "TAB_NAME") == ul.UNSUBSCRIBE_TAB and js_list(code, "COLUMNS") == ul.UNSUBSCRIBE_COLUMNS and js_list(code, "REASONS") == ul.REASONS


def test_the_rules_for_a_link_are_the_same_on_both_sides(code):
    assert js_number(code, "TOKEN_VERSION") == ul.TOKEN_VERSION and js_number(code, "MIN_SECRET_LENGTH") == ul.MIN_SECRET_LENGTH
    assert js_list(code, "REQUIRED_FIELDS") == list(ul._REQUIRED_FIELDS)


def test_the_script_names_the_same_settings_the_engine_reads(code):
    for name in ("UNSUBSCRIBE_SECRET", "UNSUBSCRIBE_URL", "SHEET_ID"):
        assert name in code
    assert 'getProperty(\'UNSUBSCRIBE_SECRET\')' in code and "getProperty('SHEET_ID')" in code


def test_the_script_never_puts_text_into_the_page_as_markup_after_the_buttons_are_pressed(code):
    assert "innerHTML" not in code and "document.write" not in code and "eval(" not in code and "textContent" in code


def test_the_script_asks_for_no_more_than_it_needs_and_serves_no_data_back(code):
    assert "UrlFetchApp" not in code and "MailApp" not in code and "GmailApp" not in code and "DriveApp" not in code and "getUserEmail" not in code and "Session." not in code


def test_the_set_up_steps_name_every_secret_and_the_deploy_settings(code):
    head = code.split("var TAB_NAME")[0]
    for words in ("Execute as: Me", "Who has access: Anyone", "UNSUBSCRIBE_URL", "UNSUBSCRIBE_SECRET", "SHEET_ID", "setupCheck", "selfTest", "Send yourself a test email"):
        assert words in head, words
    for words in ("private (incognito) window", "unable to open the file", "/dev", "This link is not valid", 'NOT "Only myself"', "NEVER from your browser's", "/u/6/"):
        assert words in head, words                       # the two settings that make a page open for you and for nobody else are named in the steps
    assert head.count("UNSUBSCRIBE_SECRET") >= 2          # once for the script's own settings (step 2) and once for GitHub (step 5): the same text in both places


def test_the_troubleshooting_log_is_named_the_same_on_both_sides(code):
    assert js_string(code, "DIAG_TAB") == ul.DIAGNOSTICS_TAB and js_list(code, "DIAG_COLUMNS") == ul.DIAGNOSTICS_COLUMNS
    assert js_list(code, "DIAG_STATES") == ["valid", "invalid", "test", "not set up"]
    for event in ul.DIAGNOSTIC_EVENTS:
        assert f"'{event}'" in code, event


def test_the_troubleshooting_log_is_off_unless_asked_for_and_says_so_in_the_steps(code):
    head = code.split("var TAB_NAME")[0]
    assert "String(PropertiesService.getScriptProperties().getProperty('DIAGNOSTICS') || '').trim().toLowerCase() === 'on'" in code
    for words in ("DIAGNOSTICS = on", "Unsubscribe diagnostics", "no new row", "opened", "rendered", "It records no email address, no lead and no link"):
        assert words in head, words


def test_the_troubleshooting_log_is_written_without_any_address_lead_or_link(code):
    block = code.split("function logDiag_")[1].split("function recordRendered")[0]
    assert "payload.e" not in block and "payload.l" not in block and "parameter.t" in block and "token.length" in block and "String(e.queryString).length" in block
    assert "Reason" not in block and "details" not in block.lower()


# ================================================================ campaign codes: the engine writes the list, the script reads it

def test_the_code_list_tab_and_the_shape_of_a_code_are_the_same_on_both_sides(code):
    assert js_string(code, "CODES_TAB") == ul.CODES_TAB and f"var CAMPAIGN_CODE_PATTERN = /{ul.CAMPAIGN_CODE_PATTERN}/;" in code


def test_the_script_reads_the_two_columns_the_engine_writes_in_that_order(code):
    block = code.split("function campaignLabel_")[1].split("function logDiag_")[0] if "function logDiag_" in code.split("function campaignLabel_")[1] else code.split("function campaignLabel_")[1]
    assert ul.CODES_COLUMNS == ["Code", "Campaign"] and "values[row][0]" in block and "values[row][1]" in block and "getSheetByName(CODES_TAB)" in block


def test_the_script_never_lets_a_failed_code_lookup_stop_an_opt_out(code):
    block = code.split("function campaignLabel_")[1].split("/* ------------------------------------------------------------------")[0]
    assert "try {" in block and "} catch (error) {" in block and block.rstrip().endswith("}") and "throw" not in block


def test_the_name_is_written_through_the_same_cleaning_as_everything_else_the_script_writes(code):
    assert "safeCell_(cleanText_(campaignLabel_(payload.c, props.sheetId), 120))" in code and "campaignLabel_(payload.c, sheetId)" in code


def test_the_steps_at_the_top_of_the_script_explain_the_code_list(code):
    head = code.split("var TAB_NAME")[0]
    for words in ("CAMPAIGN CODES:", "instead of your campaign's name", "Campaign codes", "this script only READS that tab", "the Campaign column shows the code"):
        assert words in head, words



# ================================================================ the master sheet: the script and the engine follow one rule

def js_array(code, name):
    return re.findall(r"'([^']*)'", re.search(rf"var {name} = \[(.*?)\];", code, re.S).group(1))


def test_the_status_the_script_writes_is_the_status_the_engine_and_the_app_read(code):
    assert js_string(code, "STATUS_UNSUBSCRIBED") == outreach.STATUS_STOPPED_UNSUBSCRIBED == "Stopped - Unsubscribed"


def test_the_outcomes_the_script_never_overwrites_are_exactly_the_ones_the_engine_never_overwrites(code):
    assert sorted(js_array(code, "UNSUBSCRIBE_KEEPS_STATUS")) == sorted(outreach.UNSUBSCRIBE_KEEPS_STATUS)
    assert outreach.STATUS_COMPLETED not in outreach.UNSUBSCRIBE_KEEPS_STATUS and outreach.STATUS_PAUSED not in outreach.UNSUBSCRIBE_KEEPS_STATUS
    assert {outreach.STATUS_STOPPED_REPLIED, outreach.STATUS_STOPPED_BOUNCED, outreach.STATUS_STOPPED_REJECTED, outreach.STATUS_STOPPED_MANUAL, outreach.STATUS_REMOVED,
            outreach.STATUS_STOPPED_UNSUBSCRIBED} == outreach.UNSUBSCRIBE_KEEPS_STATUS


def test_the_script_finds_a_campaigns_master_tab_by_the_name_the_engine_gives_it(code):
    suffix = js_string(code, "MASTER_TAB_SUFFIX")
    with open(os.path.join(os.path.dirname(__file__), "..", "outreach.py"), encoding="utf-8") as handle:
        engine = handle.read()
    assert suffix == " Master Sheet" and 'cfg["master_tab"] = cfg.get("master_tab") or f"{campaign_name}' + suffix + '"' in engine


def test_the_script_reads_and_writes_the_master_columns_by_the_names_the_engine_uses(code):
    block = code.split("function updateMasterSheet_")[1].split("function alreadyOptedOut_")[0]
    for column in ("Email", "Status", "LastActionAt"):
        assert f"indexOf('{column}')" in block and column in outreach.MASTER_COLUMNS, column


def test_the_time_the_script_writes_is_in_the_format_and_the_clock_the_engine_writes():
    block = open(os.path.join(os.path.dirname(__file__), "..", "apps_script", "Code.gs"), encoding="utf-8").read().split("function updateMasterSheet_")[1].split("function alreadyOptedOut_")[0]
    assert "Utilities.formatDate(new Date(), 'UTC', 'yyyy-MM-dd HH:mm:ss')" in block and outreach.DATETIME_FMT == "%Y-%m-%d %H:%M:%S"


def test_the_master_update_is_best_effort_and_its_result_never_reaches_the_visitor(code):
    block = code.split("function updateMasterSheet_")[1].split("function alreadyOptedOut_")[0]
    assert "try {" in block and "} catch (error) {" in block and "return 'error';" in block and "throw" not in block
    assert "Object.defineProperty(result, 'master', { value: '', writable: true, enumerable: false })" in code and code.count("result.master = updateMasterSheet_(payload, props);") == 1


def test_a_test_link_returns_before_the_master_sheet_is_touched(code):
    block = code.split("function record_")[1].split("function info_")[0]
    assert block.index("if (result.test) { return result; }") < block.index("updateMasterSheet_(payload, props)")


def test_the_already_unsubscribed_wording_is_the_same_in_the_script_and_the_engines_other_pages(code):
    for words in ("You are already unsubscribed", " has already been removed from this mailing list. You will not receive any more ", "Unsubscribed by mistake? Reply to any of our emails and we will add you back."):
        assert words in code, words


def test_the_check_for_an_address_that_has_already_left_is_read_only_and_never_throws(code):
    block = code.split("function alreadyOptedOut_")[1].split("/* ------------------------------------------------------------------")[0]
    assert "setValue" not in block and "appendRow" not in block and "getScriptLock" not in block and "} catch (error) {\n    return false;" in block and "!rowKey || rowKey ===" in block



# ================================================================ speed: what a request costs

def test_the_spreadsheet_is_opened_in_one_place_only_and_everything_else_asks_for_it_through_that_place(code):
    outside = code.split("function selfTest")[0].split("function setupCheck")[0]
    assert outside.count("SpreadsheetApp.openById(") == 1 and "BOOKS_[sheetId] = SpreadsheetApp.openById(sheetId);" in outside
    for function in ("campaignLabel_", "updateMasterSheet_", "alreadyOptedOut_", "logDiag_", "getTab_"):
        block = code.split(f"function {function}")[1].split("\nfunction ")[0]
        assert "book_(" in block and "SpreadsheetApp.openById" not in block, function


def test_every_request_starts_by_forgetting_what_the_last_one_remembered(code):
    for function in ("doGet", "doPost", "submitUnsubscribe", "recordRendered"):
        block = code.split(f"function {function}(")[1]
        assert block.split("\n", 2)[1].strip() == "resetCaches_();", function
    assert "function resetCaches_() { BOOKS_ = {}; LABELS_ = {}; }" in code


def test_the_master_sheet_is_updated_after_the_lock_is_released_and_reads_only_the_columns_it_needs(code):
    record = code.split("function record_")[1].split("function writeOptOut_")[0]
    assert record.index("writeOptOut_(result, payload, source, reason, details, props);") < record.index("result.master = updateMasterSheet_(payload, props);")
    assert "getScriptLock" not in record and "getScriptLock" in code.split("function writeOptOut_")[1].split("function info_")[0]
    master = code.split("function updateMasterSheet_")[1].split("function alreadyOptedOut_")[0]
    assert "getDataRange" not in master and master.count("getRange(") >= 4 and "getLastColumn()" in master and "getLastRow()" in master


def test_the_campaign_name_lookup_is_remembered_within_a_request_and_never_repeated_after_a_failure(code):
    block = code.split("function campaignLabel_")[1].split("/* ------------------------------------------------------------------")[0]
    assert "if (LABELS_.hasOwnProperty(text)) { return LABELS_[text]; }" in block and block.index("LABELS_[text] = text;") < block.index("try {")
