"""Page-level tests for the Performance page (pages/performance.py)."""
import os
from unittest.mock import patch

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from test_pages_smoke import (
    FakeWorksheet, FakeSpreadsheet, FIXTURE_CAMPAIGN, PAGES_DIR, _dashboard_secrets, _authed_session,
)

PERFORMANCE_PAGE = os.path.join(PAGES_DIR, "performance.py")
APP_FILE = os.path.join(PAGES_DIR, "..", "app.py")


@pytest.fixture(autouse=True)
def _clear_streamlit_caches():
    st.cache_resource.clear()
    st.cache_data.clear()
    yield
    st.cache_resource.clear()
    st.cache_data.clear()


def _run_page(fake_spreadsheet=None, authorize_error=None, interact=None):
    authorize = {"side_effect": authorize_error} if authorize_error else {
        "return_value": type("C", (), {"open_by_key": lambda self, k: fake_spreadsheet})()}
    with patch("gspread.authorize", **authorize), \
         patch("google.oauth2.service_account.Credentials.from_service_account_info", return_value=object()):
        at = AppTest.from_file(PERFORMANCE_PAGE)
        at.secrets.update(_dashboard_secrets())
        for key, value in _authed_session().items():
            at.session_state[key] = value
        at.run(timeout=25)
        if interact:
            interact(at)
            at.run(timeout=25)
    return at


def _sheet(leads, responses, send_log, error_log=()):
    """error_log=None leaves the Error Log tab out entirely (an unreadable tab)."""
    tabs = {
        f"{FIXTURE_CAMPAIGN} Master Sheet": FakeWorksheet(leads),
        f"{FIXTURE_CAMPAIGN} Response Sheet": FakeWorksheet(responses),
        f"{FIXTURE_CAMPAIGN} Custom Log Sheet": FakeWorksheet(send_log),
    }
    if error_log is not None:
        tabs[f"{FIXTURE_CAMPAIGN} Error Log"] = FakeWorksheet(list(error_log))
    return FakeSpreadsheet(tabs)


def _realistic_sheet():
    leads = [
        {"LeadID": "1", "Email": "a@x.com", "IntroSentAt": "2026-09-01 10:00:00", "IntroVariant": "A", "ReplyStatus": "Replied", "Status": "Stopped - Replied"},
        {"LeadID": "2", "Email": "b@x.com", "IntroSentAt": "2026-09-01 10:00:00", "IntroVariant": "B", "ReplyStatus": "Replied", "Status": "Stopped - Replied"},
        {"LeadID": "3", "Email": "c@x.com", "IntroSentAt": "2026-09-01 10:00:00", "IntroVariant": "A", "ReplyStatus": "", "Status": "Intro Sent"},
        {"LeadID": "4", "Email": "d@x.com", "IntroSentAt": "", "IntroVariant": "", "ReplyStatus": "", "Status": ""},
    ]
    responses = [{"ResponseID": "R1", "LeadID": "1", "ReceivedAt": "2026-09-02 10:00:00", "Classification": "Genuine Reply",
                  "Intent": "Interested", "InReplyTo": "<m1@x>"},
                 {"ResponseID": "R2", "LeadID": "2", "ReceivedAt": "2026-09-03 10:00:00", "Classification": "Genuine Reply",
                  "Intent": "Not Interested", "InReplyTo": "<m2@x>"}]
    send_log = [
        {"Timestamp": "2026-09-01 10:00:00", "LeadID": "1", "Stage": "intro", "Variant": "A", "Status": "sent", "MessageID": "<m1@x>", "Error": ""},
        {"Timestamp": "2026-09-01 10:05:00", "LeadID": "2", "Stage": "intro", "Variant": "B", "Status": "sent", "MessageID": "<m2@x>", "Error": ""},
        {"Timestamp": "2026-09-01 10:10:00", "LeadID": "3", "Stage": "intro", "Variant": "A", "Status": "sent", "MessageID": "<m3@x>", "Error": ""},
        {"Timestamp": "2026-09-01 10:15:00", "LeadID": "4", "Stage": "intro", "Variant": "A", "Status": "failed", "MessageID": "", "Error": "SMTP 550"},
        {"Timestamp": "2026-09-03 10:00:00", "LeadID": "3", "Stage": "followup1", "Variant": "C", "Status": "sent", "MessageID": "<m4@x>", "Error": ""},
    ]
    error_log = [{"ErrorType": "SMTP", "Timestamp": "2026-09-01 10:15:00", "Detail": "550"}] * 2
    return _sheet(leads, responses, send_log, error_log)


def _headline_and_card_metrics(at):
    """Several labels (Contacted, Reply rate) appear TWICE: once in the
    headline row and once in the campaign card. A dict keeps only the last,
    which would let a wrong headline hide behind a right card — so keep the
    first occurrence (headline) and the last (campaign card) separately."""
    pairs = [(m.label, m.value) for m in at.metric]
    headline = {}
    for label, value in pairs:
        headline.setdefault(label, value)
    card = {label: value for label, value in pairs}
    return headline, card


def test_performance_page_renders_with_the_right_headline_numbers(fixture_repo):
    at = _run_page(_realistic_sheet())
    assert list(at.exception) == [], f"Performance page raised: {list(at.exception)}"
    assert list(at.error) == [], f"Performance page showed an error: {[e.value for e in at.error]}"
    headline, card = _headline_and_card_metrics(at)
    assert headline["Contacted"] == "3"
    assert headline["Emails sent"] == "4"               # 3 intros + 1 follow-up: deliberately not equal to Contacted
    assert headline["Reply rate"] == "66.7%"            # 2 of 3 contacted replied...
    assert headline["Positive replies"] == "1"          # ...but only one was positive (so the two cards can never be swapped unnoticed)
    assert headline["Needs follow-up"] == "0"
    assert headline["Failed sends"] == "1"
    assert headline["Negotiating"] == "—"               # the Asana sync has not recorded stages: a dash, not a false zero
    # the campaign card repeats the numbers, and must agree with the headline
    assert (card["Contacted"], card["Sent"], card["Replies"], card["Reply rate"], card["Positive"]) == ("3", "4", "2", "66.7%", "1")
    subheaders = [s.value for s in at.subheader]
    for expected in ("Activity", "What the replies say", "What worked best?", "Follow-up stage effectiveness",
                     "Template variants", "Deliverability", "Campaigns"):
        assert expected in subheaders


def test_the_page_shows_a_campaign_card_with_its_status(fixture_repo):
    at = _run_page(_realistic_sheet())
    cards = " ".join(m.value for m in at.markdown)
    assert FIXTURE_CAMPAIGN in cards


def test_choosing_one_campaign_in_the_filter_shows_that_campaigns_numbers(fixture_repo):
    def pick(at):
        at.selectbox(key="performance_campaign").set_value(FIXTURE_CAMPAIGN)

    at = _run_page(_realistic_sheet(), interact=pick)
    assert list(at.exception) == []
    headline, card = _headline_and_card_metrics(at)
    assert headline["Contacted"] == "3" and headline["Reply rate"] == "66.7%" and card["Reply rate"] == "66.7%"


def test_changing_the_chart_range_does_not_break_the_page(fixture_repo):
    def pick(at):
        at.selectbox(key="performance_range").set_value("All time")

    at = _run_page(_realistic_sheet(), interact=pick)
    assert list(at.exception) == [] and list(at.error) == []


def test_the_page_says_so_plainly_for_a_campaign_with_no_activity(fixture_repo):
    at = _run_page(_sheet([], [], []))
    assert list(at.exception) == [] and list(at.error) == []
    headline, card = _headline_and_card_metrics(at)
    assert headline["Reply rate"] == "—" and headline["Contacted"] == "0"          # no data is a dash, never "0.0%"
    assert card["Reply rate"] == "—"
    infos = " ".join(i.value for i in at.info)
    assert "Nothing has been sent or replied to yet." in infos and "No genuine replies yet." in infos


def test_the_page_survives_a_sheet_that_cannot_be_read(fixture_repo):
    at = _run_page(authorize_error=RuntimeError("no access to the Sheet"))
    assert list(at.exception) == [], f"Performance page raised: {list(at.exception)}"
    assert any("not shown yet" in e.label for e in at.expander)
    assert any("No campaign has any activity" in i.value for i in at.info)


def test_the_page_survives_a_campaign_whose_tabs_do_not_exist_yet(fixture_repo):
    at = _run_page(FakeSpreadsheet({}))
    assert list(at.exception) == []
    assert any("not shown yet" in e.label for e in at.expander)


def test_open_and_click_rates_are_deliberately_not_shown(fixture_repo):
    at = _run_page(_realistic_sheet())
    labels = " ".join(m.label.lower() for m in at.metric)
    assert "open" not in labels and "click" not in labels
    assert "open and click rates are deliberately not shown" in " ".join(c.value.lower() for c in at.caption)


def test_performance_page_is_strictly_read_only():
    forbidden = ("dispatch_workflow", "create_file", "delete_file", "update_file", "GitHubClient",
                 "append_row", "update_cell", "update_cells", "batch_update", "add_worksheet", "smtp_send", "sendmail")
    for path in (PERFORMANCE_PAGE, os.path.join(PAGES_DIR, "..", "performance_logic.py")):
        source = open(path, encoding="utf-8").read()
        found = [token for token in forbidden if token in source]
        assert found == [], f"{os.path.basename(path)} mentions write operations: {found}"


def test_performance_is_registered_in_the_navigation_and_listed_on_home():
    source = open(APP_FILE, encoding="utf-8").read()
    assert 'st.Page("pages/performance.py"' in source
    assert "performance_page" in source.split("st.navigation(")[1].split(")")[0]
    assert "Performance" in source.split("def _home")[1]


# ================================================================ replacing Analytics, Overview and Dashboard

def _all_dataframes(at):
    return [df.value for df in at.dataframe]


def test_every_figure_the_old_overview_page_showed_is_on_this_page_in_the_at_a_glance_table(fixture_repo):
    at = _run_page(_realistic_sheet())
    assert "All campaigns at a glance" in [s.value for s in at.subheader]
    glance = next(df for df in _all_dataframes(at) if "Sequence completion" in df.columns)
    row = glance.iloc[0]
    assert row["Campaign"] == FIXTURE_CAMPAIGN
    assert (row["Total leads"], row["Contacted"], row["Pending"], row["Emails sent"], row["Replies"], row["Reply rate"]) == \
        ("4", "3", "1", "4", "2", "66.7%")
    assert list(glance.columns) == ["Campaign", "Status", "Total leads", "Contacted", "Pending", "Emails sent", "Delivered (est.)",
                                    "Hard bounces", "Soft bounces", "Replies", "Reply rate", "Sequence completion"]


def test_the_by_sender_and_errors_tables_the_old_analytics_tab_had_are_here(fixture_repo):
    at = _run_page(_realistic_sheet(), interact=lambda a: a.selectbox(key="performance_campaign").set_value(FIXTURE_CAMPAIGN))
    assert list(at.exception) == [] and list(at.error) == []
    subheaders = [s.value for s in at.subheader]
    assert "By sender account" in subheaders and "Errors (all time)" in subheaders
    errors = next(df for df in _all_dataframes(at) if "Error type" in df.columns)
    assert list(errors["Error type"]) == ["SMTP"] and list(errors["Count"]) == [2]


def test_the_full_details_the_old_dashboard_page_showed_are_available_for_one_campaign(fixture_repo):
    at = _run_page(_realistic_sheet(), interact=lambda a: a.selectbox(key="performance_campaign").set_value(FIXTURE_CAMPAIGN))
    assert any("Full details" in e.label for e in at.expander)
    text = " ".join(m.value for m in at.markdown)
    for section in ("**Overview**", "**Per-Stage**"):                       # the engine's own sections, as the Dashboard page listed them
        assert section in text
    # all campaigns together has no single set of raw rows, so the section is not offered there
    together = _run_page(_realistic_sheet())
    assert not any("Full details" in e.label for e in together.expander)


def test_a_missing_error_log_tab_does_not_lose_the_campaign(fixture_repo):
    """Only the all-time error table depends on that tab."""
    at = _run_page(_sheet(_realistic_sheet()._worksheets[f"{FIXTURE_CAMPAIGN} Master Sheet"]._records, [], [], error_log=None),
                   interact=lambda a: a.selectbox(key="performance_campaign").set_value(FIXTURE_CAMPAIGN))
    assert list(at.exception) == [] and list(at.error) == []
    assert any("Error Log tab could not be read" in c.value for c in at.caption)
    assert FIXTURE_CAMPAIGN in " ".join(m.value for m in at.markdown)


def test_overview_and_dashboard_are_gone_from_the_app():
    source = open(APP_FILE, encoding="utf-8").read()
    nav = source.split("st.navigation(")[1].split(")")[0]
    assert "overview" not in nav.lower() and "dashboard" not in nav.lower()
    assert 'pages/overview.py' not in source and 'pages/dashboard.py' not in source
    home = source.split("def _home")[1]
    assert "Overview" not in home and "Dashboard" not in home
    leftovers = [gone for gone in ("pages/overview.py", "pages/dashboard.py", "overview_logic.py", "tests/test_overview_logic.py")
                 if os.path.exists(os.path.join(PAGES_DIR, "..", gone))]
    assert not leftovers, (
        "These old files should have been DELETED from the repo when the Overview and Dashboard pages were replaced by "
        "Performance — nothing uses them any more. Delete: " + ", ".join(f"streamlit_app/{path}" for path in leftovers) +
        ". (Delete tests/test_overview_logic.py together with overview_logic.py, or it will fail to import.)")


def test_the_shared_view_is_read_only_too():
    forbidden = ("dispatch_workflow", "create_file", "delete_file", "update_file", "GitHubClient",
                 "append_row", "update_cell", "update_cells", "batch_update", "add_worksheet", "smtp_send", "sendmail")
    source = open(os.path.join(PAGES_DIR, "..", "performance_view.py"), encoding="utf-8").read()
    assert [t for t in forbidden if t in source] == []
    assert "st.tabs" not in source           # no nested tabs: the campaign page keeps exactly its six tabs


def _markdown_text(at):
    return " ".join(m.value for m in at.markdown)


def test_ranking_campaigns_against_each_other_appears_only_when_looking_at_all_campaigns(fixture_repo):
    together = _run_page(_realistic_sheet())
    assert "**Campaign:**" in _markdown_text(together)                          # all campaigns: a campaign ranking makes sense
    one = _run_page(_realistic_sheet(), interact=lambda a: a.selectbox(key="performance_campaign").set_value(FIXTURE_CAMPAIGN))
    assert "**Campaign:**" not in _markdown_text(one)                           # one campaign: nothing to rank it against
    assert "**Template variant:**" in _markdown_text(one)
