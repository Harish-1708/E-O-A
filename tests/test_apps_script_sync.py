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
    assert head.count("UNSUBSCRIBE_SECRET") >= 2          # once for the script's own settings (step 2) and once for GitHub (step 5): the same text in both places
