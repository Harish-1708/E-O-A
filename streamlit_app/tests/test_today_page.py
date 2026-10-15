"""Page-level tests for the Today page (pages/today.py): it renders, it shows
the right numbers for a real-looking Sheet, it survives a Sheet that cannot
be read, and — the guarantee that matters most for a page you will open
casually — it cannot change anything."""
import os
from unittest.mock import patch

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from test_pages_smoke import (
    FakeWorksheet, FakeSpreadsheet, FIXTURE_CAMPAIGN, PAGES_DIR, _dashboard_secrets, _authed_session,
)

TODAY_PAGE = os.path.join(PAGES_DIR, "today.py")
APP_FILE = os.path.join(PAGES_DIR, "..", "app.py")


@pytest.fixture(autouse=True)
def _clear_streamlit_caches():
    st.cache_resource.clear()
    st.cache_data.clear()
    yield
    st.cache_resource.clear()
    st.cache_data.clear()


def _run_page(fake_spreadsheet=None, authorize_error=None):
    authorize = {"side_effect": authorize_error} if authorize_error else {
        "return_value": type("C", (), {"open_by_key": lambda self, k: fake_spreadsheet})()}
    with patch("gspread.authorize", **authorize), \
         patch("google.oauth2.service_account.Credentials.from_service_account_info", return_value=object()):
        at = AppTest.from_file(TODAY_PAGE)
        at.secrets.update(_dashboard_secrets())
        for key, value in _authed_session().items():
            at.session_state[key] = value
        at.run(timeout=20)
    return at


def _leads_sheet(records):
    return FakeSpreadsheet({f"{FIXTURE_CAMPAIGN} Master Sheet": FakeWorksheet(records)})


def test_today_page_renders_and_shows_who_is_due(fixture_repo):
    at = _run_page(_leads_sheet([
        {"Email": "a@abc.com", "FirstName": "Ann", "LastName": "A", "Status": "", "ReplyStatus": ""},
        {"Email": "b@abc.com", "FirstName": "Bo", "LastName": "B", "Status": "", "ReplyStatus": ""},
        {"Email": "c@abc.com", "FirstName": "Cy", "LastName": "C", "Status": "Stopped - Replied", "ReplyStatus": "Replied"},
    ]))
    assert list(at.exception) == [], f"Today page raised: {list(at.exception)}"
    assert list(at.error) == [], f"Today page showed an error: {[e.value for e in at.error]}"
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Due now (all running campaigns)"] == "2"      # the two unsent leads; the replied one is out of play
    assert metrics["Sending open right now"] == "1 of 1 campaigns"  # the fixture campaign has no schedule: always open
    assert FIXTURE_CAMPAIGN in [s.value for s in at.subheader]
    page_text = " ".join(c.value for c in at.caption) + " " + " ".join(m.value for m in at.markdown)
    assert "Replied 1" in page_text                                   # out-of-play breakdown is shown
    assert "2 leads are due and sending is open" in page_text         # the plain-English answer


def test_today_page_says_so_plainly_when_nothing_is_due(fixture_repo):
    at = _run_page(_leads_sheet([
        {"Email": "a@abc.com", "Status": "Stopped - Replied", "ReplyStatus": "Replied"},
    ]))
    assert list(at.exception) == [] and list(at.error) == []
    assert {m.label: m.value for m in at.metric}["Due now (all running campaigns)"] == "0"
    assert "Nothing is due, and nobody is waiting on a follow-up." in " ".join(m.value for m in at.markdown)


def test_today_page_survives_a_sheet_that_cannot_be_read(fixture_repo):
    at = _run_page(authorize_error=RuntimeError("no access to the Sheet"))
    assert list(at.exception) == [], f"Today page raised: {list(at.exception)}"
    assert any("could not be read" in e.label for e in at.expander)
    assert any("no access to the Sheet" in m.value for m in at.markdown)


def test_today_page_survives_a_campaign_whose_tab_does_not_exist_yet(fixture_repo):
    at = _run_page(FakeSpreadsheet({}))        # a brand-new campaign has no Sheet tabs until its first run
    assert list(at.exception) == []
    assert any("could not be read" in e.label for e in at.expander)


def test_today_page_is_strictly_read_only():
    """It is a page you open casually, so it must not be able to change
    anything: no GitHub writes or workflow starts, no Sheet writes. Checked
    on the page AND its logic module."""
    forbidden = ("dispatch_workflow", "create_file", "delete_file", "update_file", "GitHubClient",
                 "append_row", "update_cell", "update_cells", "batch_update", "add_worksheet", "smtp_send", "sendmail")
    for path in (TODAY_PAGE, os.path.join(PAGES_DIR, "..", "work_queue_logic.py")):
        source = open(path, encoding="utf-8").read()
        found = [token for token in forbidden if token in source]
        assert found == [], f"{os.path.basename(path)} mentions write operations: {found}"


def test_today_is_registered_in_the_navigation_and_listed_on_home():
    source = open(APP_FILE, encoding="utf-8").read()
    assert 'PAGES["today"] = st.Page("pages/today.py"' in source
    assert "list(PAGES.values())" in source.split("st.navigation(")[1].split("\n")[0]               # every registered page is in the navigation
    import shell_logic
    assert any(item[0] == "today" and item[1] == "Today" and item[2] == "pages/today.py" for item in shell_logic.NAV_ITEMS)       # and has its link in the sidebar
    home_view = open(os.path.join(os.path.dirname(APP_FILE), "shell_view.py"), encoding="utf-8").read()
    assert '("today", "Today",' in home_view                                                       # and its card on Home
