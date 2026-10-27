"""The Google page (apps_script/Code.gs) and the Python engine must agree on the link format, the tab and the reasons. Node tests prove the script's behaviour (apps_script/test_unsubscribe.js);
these tests run anywhere Python does, so GitHub's own CI also notices if one side is changed without the other."""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

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
    block = code.split("function campaignLabel_")[1].split("/* ------------------------------------------------------------------ troubleshooting log")[0]
    assert "try {" in block and "} catch (error) {" in block and block.rstrip().endswith("}") and "throw" not in block


def test_the_name_is_written_through_the_same_cleaning_as_everything_else_the_script_writes(code):
    assert "safeCell_(cleanText_(campaignLabel_(payload.c, props.sheetId), 120))" in code and "campaignLabel_(payload.c, sheetId)" in code


def test_the_steps_at_the_top_of_the_script_explain_the_code_list(code):
    head = code.split("var TAB_NAME")[0]
    for words in ("CAMPAIGN CODES:", "instead of your campaign's name", "Campaign codes", "this script only READS that tab", "the Campaign column shows the code"):
        assert words in head, words
