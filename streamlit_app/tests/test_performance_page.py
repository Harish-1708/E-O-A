"""Page-level tests for the Performance page (pages/performance.py)."""
import html
import os
import re
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


def _markup(at):
    return " ".join(m.value for m in at.markdown)


def _kpis(at):
    """The headline figures, in order, as (label, value) — read from the cards the shared view draws."""
    return [(html.unescape(label), html.unescape(value))
            for label, value in re.findall(r'pf-kpi-label">(.*?)</div><div class="pf-kpi-value">(.*?)</div>', _markup(at))]


def _card_titles(at):
    return [html.unescape(t) for t in re.findall(r'pf-card-title">(.*?)</h3>', _markup(at))] + \
           [html.unescape(t).replace("🏆 ", "") for t in re.findall(r'pf-callout-title">(.*?)</div>', _markup(at))]


def _tables(at):
    """Every table the shared view drew, as a list of rows of cell text (the header row first)."""
    out = []
    for table in re.findall(r'<table class="pf-table">(.*?)</table>', _markup(at)):
        out.append([[html.unescape(re.sub(r"<[^>]+>", "", c)) for c in re.findall(r"<t[hd]>(.*?)</t[hd]>", row)] for row in re.findall(r"<tr>(.*?)</tr>", table)])        # the visible text of each cell
    return out


def _bars(at):
    return {html.unescape(label): (int(width), int(count))
            for label, width, count in re.findall(r'pf-bar-label">(.*?)</div>.*?width:(\d+)%.*?pf-bar-count">(\d+)<', _markup(at))}


def _headline_and_card_metrics(at):
    """Several labels (Contacted, Reply rate) appear TWICE: once in the
    headline row and once in the campaign card. The headline row is drawn by
    the shared view as HTML cards, the campaign cards by the page as
    st.metric — read separately, so a wrong headline can never hide behind a
    right card (and the other way round)."""
    headline = {}
    for label, value in _kpis(at):
        headline.setdefault(label, value)
    card = {m.label: m.value for m in at.metric}
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
    # the campaign's row in the table repeats the numbers, and must agree with the headline
    table = _glance_markup(at)
    assert '3 <span class="pg-of">of 4</span>' in table and '<div class="pg-num">4</div>' in table and "66.7%" in table
    assert '<div class="pg-num">2</div><div class="pg-sub">replied · 1 positive</div>' in table
    titles = _card_titles(at) + [s.value for s in at.subheader]
    for expected in ("Activity", "What the replies say", "What worked best?", "Follow-up stage effectiveness",
                     "Template variants", "Deliverability"):
        assert expected in titles, (expected, titles)
    assert "Campaigns" not in titles and not any(m.label == "Positive" for m in at.metric)       # the old duplicate list of campaign cards is gone: the table is the one place


def test_the_page_shows_a_campaign_card_with_its_status(fixture_repo):
    at = _run_page(_realistic_sheet())
    cards = " ".join(m.value for m in at.markdown)
    assert FIXTURE_CAMPAIGN in cards


def test_choosing_one_campaign_in_the_filter_shows_that_campaigns_numbers(fixture_repo):
    def pick(at):
        at.selectbox(key="performance_campaign").set_value(FIXTURE_CAMPAIGN)

    at = _run_page(_realistic_sheet(), interact=pick)
    assert list(at.exception) == []
    headline, _ = _headline_and_card_metrics(at)
    assert headline["Contacted"] == "3" and headline["Reply rate"] == "66.7%" and "66.7%" in _glance_markup(at)


def _chart_summary(at):
    return " ".join(re.findall(r'aria-label="([^"]+)"', _markup(at)))


@pytest.mark.parametrize("choice, days", [("Last 7 days", 7), ("Last 30 days", 30), ("Last 90 days", 90)])
def test_the_chart_range_changes_how_many_days_the_chart_covers(fixture_repo, choice, days):
    at = _run_page(_realistic_sheet(), interact=lambda a: a.radio(key="performance_range").set_value(choice))
    assert list(at.exception) == [] and list(at.error) == []
    assert f"per day over {days} days" in _chart_summary(at)


def test_all_time_starts_at_the_first_recorded_day_and_shows_the_busiest_day(fixture_repo):
    at = _run_page(_realistic_sheet(), interact=lambda a: a.radio(key="performance_range").set_value("All time"))
    assert list(at.exception) == [] and list(at.error) == []
    assert "most in one day: 3" in _chart_summary(at)                        # 3 intros went out on 1 Sep (the failed send is not counted)
    assert int(re.search(r"over (\d+) days", _chart_summary(at)).group(1)) > 30


def test_the_chart_range_is_a_horizontal_choice_that_starts_on_30_days(fixture_repo):
    at = _run_page(_realistic_sheet())
    assert at.radio(key="performance_range").value == "Last 30 days"
    assert at.radio(key="performance_range").options == ["Last 7 days", "Last 30 days", "Last 90 days", "All time"]


def test_the_page_says_so_plainly_for_a_campaign_with_no_activity(fixture_repo):
    at = _run_page(_sheet([], [], []))
    assert list(at.exception) == [] and list(at.error) == []
    headline, _ = _headline_and_card_metrics(at)
    assert headline["Reply rate"] == "—" and headline["Contacted"] == "0"          # no data is a dash, never "0.0%"
    assert '<div class="pg-num pg-rate">—</div>' in _glance_markup(at) and "0.0%" not in _glance_markup(at)
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
    labels = " ".join(m.label.lower() for m in at.metric) + " " + " ".join(label.lower() for label, _ in _kpis(at))
    assert len(_kpis(at)) >= 13                                              # the headline cards really were read, not an empty list
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
    assert 'PAGES["performance"] = st.Page("pages/performance.py"' in source
    assert "list(PAGES.values())" in source.split("st.navigation(")[1].split("\n")[0]               # every registered page is in the navigation
    import shell_logic
    assert any(item[0] == "performance" and item[1] == "Performance" and item[2] == "pages/performance.py" for item in shell_logic.NAV_ITEMS)       # and has its link in the sidebar
    home_view = open(os.path.join(os.path.dirname(APP_FILE), "shell_view.py"), encoding="utf-8").read()
    assert '("performance", "Performance",' in home_view                                                       # and its card on Home


# ================================================================ replacing Analytics, Overview and Dashboard

def _all_dataframes(at):
    return [df.value for df in at.dataframe]


def _glance_markup(at):
    return next(m.value for m in at.markdown if 'class="pg-table"' in m.value)


def test_every_figure_the_old_overview_page_showed_is_still_in_the_at_a_glance_table(fixture_repo):
    at = _run_page(_realistic_sheet())
    assert list(at.exception) == [] and list(at.error) == []
    assert any("All campaigns at a glance" in m.value for m in at.markdown)
    assert not any("Sequence completion" in list(df.value.columns) for df in at.dataframe)                  # the old plain data table is gone
    table = _glance_markup(at)
    assert FIXTURE_CAMPAIGN in table and "3 <span class=\"pg-of\">of 4</span>" in table and "1 pending" in table                 # contacted of leads, pending
    assert "<div class=\"pg-num\">4</div><div class=\"pg-sub\">4 delivered (est.)</div>" in table and "66.7%" in table and "All 1 campaign<" in table      # emails sent, delivered, reply rate, total row
    assert "0 hard" in table and "0 soft" in table and "reached the last email" in table and "completed the sequence" not in table


def test_the_glance_csv_has_every_column_the_old_table_had_and_the_rows_in_the_order_shown(fixture_repo):
    import csv
    import io
    import performance_logic
    given = []
    real = performance_logic.glance_csv

    def spy(perfs, status_labels, sort):
        data = real(perfs, status_labels, sort)
        given.append((sort, data))
        return data

    with patch("performance_logic.glance_csv", spy):
        at = _run_page(_realistic_sheet(), interact=lambda a: a.selectbox(key="performance_glance_sort").set_value("Replies"))
    assert any(b.proto.label == "⬇ CSV" for b in at.get("download_button"))
    sort, data = given[-1]
    rows = list(csv.reader(io.StringIO(data.decode("utf-8"))))
    assert sort == "Replies" and rows[0] == ["Campaign", "Status", "Total leads", "Contacted", "Pending", "Emails sent", "Delivered (est.)", "Hard bounces", "Soft bounces", "Replies",
                                            "Reply rate", "Sequence completion"]
    assert rows[1][0] == FIXTURE_CAMPAIGN and dict(zip(rows[0], rows[1]))["Replies"] == "2" and dict(zip(rows[0], rows[1]))["Reply rate"] == "66.7%"


def test_the_glance_table_has_a_sort_choice_that_starts_on_reply_rate(fixture_repo):
    at = _run_page(_realistic_sheet())
    sort = at.selectbox(key="performance_glance_sort")
    assert sort.options == ["Sort: Reply rate", "Sort: Replies", "Sort: Emails sent", "Sort: Name"] and sort.value == "Reply rate"
    assert "Sorted by reply rate." in " ".join(m.value for m in at.markdown)
    at2 = _run_page(_realistic_sheet(), interact=lambda a: a.selectbox(key="performance_glance_sort").set_value("Name"))
    assert "Sorted by name." in " ".join(m.value for m in at2.markdown) and list(at2.exception) == []


def test_the_page_level_export_is_still_there_next_to_the_tables_csv(fixture_repo):
    at = _run_page(_realistic_sheet())
    labels = sorted(b.proto.label for b in at.get("download_button"))
    assert labels == ["⤓ Export", "⬇ CSV"]


def test_the_by_sender_and_errors_tables_the_old_analytics_tab_had_are_here(fixture_repo):
    at = _run_page(_realistic_sheet(), interact=lambda a: a.selectbox(key="performance_campaign").set_value(FIXTURE_CAMPAIGN))
    assert list(at.exception) == [] and list(at.error) == []
    headings = [s.value for s in at.subheader] + _card_titles(at)
    assert "By sender account" in headings
    errors_dropdown = next(e for e in at.expander if e.label == "Errors (all time)")             # a dropdown, not a block that is always on show
    assert errors_dropdown.proto.expanded is False
    errors = next(t for t in _tables(at) if t[0] == ["Error type", "Count"])
    assert errors == [["Error type", "Count"], ["SMTP", "2"]]
    assert "Errors (all time)" not in _card_titles(at)


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
    assert "<b>Campaign:</b>" in _markdown_text(together)                       # all campaigns: a campaign ranking makes sense
    one = _run_page(_realistic_sheet(), interact=lambda a: a.selectbox(key="performance_campaign").set_value(FIXTURE_CAMPAIGN))
    assert "<b>Campaign:</b>" not in _markdown_text(one)                        # one campaign: nothing to rank it against
    assert "<b>Template variant:</b>" in _markdown_text(one)


# ================================================================ the cards, tables and bars show exactly the figures the logic computed

def _one_campaign(sheet):
    return _run_page(sheet, interact=lambda a: a.selectbox(key="performance_campaign").set_value(FIXTURE_CAMPAIGN))


def test_every_headline_figure_is_right_for_the_realistic_campaign(fixture_repo):
    """Worked out by hand from _realistic_sheet: 3 leads got an intro and 1 got a follow-up (4 sent, 1 more failed); 2 of the
    3 contacted replied; one reply was positive; nobody has bounced; the Asana sync has recorded nothing."""
    kpis = dict(_kpis(_one_campaign(_realistic_sheet())))
    assert kpis == {"Contacted": "3", "Emails sent": "4", "Reply rate": "66.7%", "Positive replies": "1", "Negotiating": "—", "Rights secured": "—",
                    "Median time to first reply": "36.0 hours", "Needs follow-up": "0", "Hard bounce rate": "0.0%", "Sequence completion": "0.0%",
                    "Hard bounces": "0", "Soft bounces": "0", "Failed sends": "1"}


def test_the_stage_and_variant_tables_show_the_right_counts(fixture_repo):
    stages, variants = _tables(_one_campaign(_realistic_sheet()))[:2]
    header = ["Stage", "Emails sent", "Replies", "Reply rate", "Positive", "Positive rate"]
    assert stages == [header, ["Intro", "3", "2", "66.7%", "1", "33.3%"], ["Follow-up 1", "1", "0", "0.0%", "0", "0.0%"]]        # readable names, not "intro" / "followup1"
    assert variants == [["Variant"] + header[1:], ["A", "2", "1", "50.0%", "1", "50.0%"], ["B", "1", "1", "100.0%", "0", "0.0%"], ["C", "1", "0", "0.0%", "0", "0.0%"]]


def test_the_reply_breakdown_bars_are_scaled_to_the_largest_and_zero_stays_empty(fixture_repo):
    bars = _bars(_one_campaign(_realistic_sheet()))                       # the page also draws the "Where your leads are now" bars, so read only the reply breakdown's own
    reply_labels = ("Interested", "Lead / Needs Follow-up", "Not Interested", "Unclear", "Not classified")
    assert {label: bars[label] for label in reply_labels} == {
        "Interested": (100, 1), "Lead / Needs Follow-up": (0, 0), "Not Interested": (100, 1), "Unclear": (0, 0), "Not classified": (0, 0)}


def _sheet_with_senders():
    sheet = _realistic_sheet()
    leads = sheet._worksheets[f"{FIXTURE_CAMPAIGN} Master Sheet"]._records
    for lead, account in zip(leads[:3], ["sales1@x.com", "sales1@x.com", 'sales2@x.com <b onmouseover=alert(1)>x</b>']):
        lead["SenderAccount"] = account
    log = sheet._worksheets[f"{FIXTURE_CAMPAIGN} Custom Log Sheet"]._records
    for row, account in zip(log, ["sales1@x.com", "sales1@x.com", 'sales2@x.com <b onmouseover=alert(1)>x</b>', "", 'sales2@x.com <b onmouseover=alert(1)>x</b>']):
        row["SenderAccount"] = account
    return sheet


def test_the_sender_table_has_one_row_per_account_with_its_own_counts(fixture_repo):
    at = _one_campaign(_sheet_with_senders())
    table = next(t for t in _tables(at) if t[0][0] == "Account")
    assert table[0] == ["Account", "Emails sent", "Leads contacted", "Replies", "Reply rate"]
    by_account = {row[0].split(" ")[0]: row[1:] for row in table[1:]}
    assert by_account["sales1@x.com"] == ["2", "2", "2", "100.0%"]           # 2 leads, both replied, 2 emails
    assert by_account["sales2@x.com"] == ["2", "1", "0", "0.0%"]             # 1 lead (an intro and a follow-up), no reply
    assert "By sender account" in _card_titles(at)


def test_a_hostile_sender_account_is_shown_as_text_and_never_becomes_markup(fixture_repo):
    at = _one_campaign(_sheet_with_senders())
    markup = _markup(at)
    assert "onmouseover" in markup and "<b onmouseover" not in markup and "&lt;b onmouseover=alert(1)&gt;" in markup


def test_a_missing_sender_table_says_so_instead_of_showing_an_empty_card(fixture_repo):
    at = _one_campaign(_realistic_sheet())                                       # this fixture has no sender accounts
    assert "By sender account" in [s.value for s in at.subheader] and any("No sends yet." in c.value for c in at.caption)
    assert "By sender account" not in _card_titles(at)


def test_the_no_activity_page_shows_its_plain_messages_instead_of_cards(fixture_repo):
    at = _run_page(_sheet([], [], []))
    titles = _card_titles(at)
    assert "Activity" not in titles and "What the replies say" not in titles
    assert {"Activity", "What the replies say", "Follow-up stage effectiveness", "Template variants"} <= {s.value for s in at.subheader}
    infos = " ".join(i.value for i in at.info)
    for message in ("Nothing has been sent or replied to yet.", "No genuine replies yet.", "No emails sent yet.", "No variants sent yet."):
        assert message in infos


def _sheet_with_bounces():
    sheet = _realistic_sheet()
    sheet._worksheets[f"{FIXTURE_CAMPAIGN} Response Sheet"]._records.extend([
        {"ResponseID": "B1", "LeadID": "3", "ReceivedAt": "2026-09-02 12:00:00", "Classification": "Bounce (Hard)"},
        {"ResponseID": "B2", "LeadID": "1", "ReceivedAt": "2026-09-02 13:00:00", "Classification": "Bounce (Soft)"},
        {"ResponseID": "B3", "LeadID": "2", "ReceivedAt": "2026-09-02 14:00:00", "Classification": "Bounce (Soft)"}])
    return sheet


def test_hard_and_soft_bounces_are_counted_and_rated_separately_against_emails_sent(fixture_repo):
    """1 hard and 2 soft bounces against 4 emails sent: 25.0% and 50.0% — different on purpose, so the two can never be swapped
    unnoticed. (Bounces are not replies, so the reply figures stay as they were.)"""
    at = _one_campaign(_sheet_with_bounces())
    assert list(at.exception) == [] and list(at.error) == []
    kpis = dict(_kpis(at))
    assert kpis["Hard bounce rate"] == "25.0%" and kpis["Hard bounces"] == "1" and kpis["Soft bounces"] == "2"
    assert kpis["Reply rate"] == "66.7%" and kpis["Positive replies"] == "1"
    markup = _markup(at)
    assert "1 bounced" in markup                                                  # the caption under the hard-bounce rate
    mini = re.findall(r'pf-kpi-label">(Hard bounces|Soft bounces)</div><div class="pf-kpi-value">(\d+)</div><div class="pf-kpi-caption">([^<]*)<', markup)
    assert mini == [("Hard bounces", "1", "25.0%"), ("Soft bounces", "2", "50.0%")]


def test_replies_the_ai_has_not_read_are_counted_and_the_page_says_how_many(fixture_repo):
    sheet = _realistic_sheet()
    sheet._worksheets[f"{FIXTURE_CAMPAIGN} Response Sheet"]._records.append(
        {"ResponseID": "R3", "LeadID": "3", "ReceivedAt": "2026-09-04 10:00:00", "Classification": "Genuine Reply", "Intent": "", "InReplyTo": "<m3@x>"})
    at = _one_campaign(sheet)
    assert _bars(at)["Not classified"] == (100, 1) and _bars(at)["Interested"] == (100, 1)
    assert "1 of 3 replying creators have no AI reading yet" in _markup(at)
    assert "3 replying creators" in _markup(at)                                   # the card's own hint counts everyone who replied


def test_the_no_ai_reading_note_is_absent_when_every_reply_has_one(fixture_repo):
    at = _one_campaign(_realistic_sheet())
    assert "have no AI reading yet" not in _markup(at) and "2 replying creators" in _markup(at)



# ================================================================ matching the approved design: readable names, equal-height pairs, a calm range control

def test_stage_names_are_readable_everywhere_they_appear(fixture_repo):
    at = _one_campaign(_realistic_sheet())
    stage_column = [row[0] for row in _tables(at)[0][1:]]
    assert stage_column == ["Intro", "Follow-up 1"] and "followup1" not in _markup(at) and ">intro<" not in _markup(at)
    expander_table = next(df for df in _all_dataframes(at) if "Variant" in df.columns and "Stage" in df.columns)
    assert set(expander_table["Stage"]) <= {"Intro", "Follow-up 1"}


def test_the_stage_and_variant_cards_are_in_one_grid_so_they_are_always_the_same_height(fixture_repo):
    at = _one_campaign(_sheet_with_senders())
    grids = [m.value for m in at.markdown if 'pf-grid pf-g2e' in m.value]
    assert len(grids) == 2                                                                         # stage + variants, and sender + deliverability
    assert "Follow-up stage effectiveness" in grids[0] and "Template variants" in grids[0]


def test_with_no_sender_accounts_only_the_first_pair_is_a_grid_and_the_rest_says_so(fixture_repo):
    at = _one_campaign(_realistic_sheet())
    one_grid = [m.value for m in at.markdown if "pf-grid pf-g2e" in m.value]
    assert len(one_grid) == 1 and "Follow-up stage effectiveness" in one_grid[0] and any("No sends yet." in c.value for c in at.caption)


def test_the_sender_card_and_the_deliverability_card_share_a_grid_when_there_are_senders(fixture_repo):
    at = _one_campaign(_sheet_with_senders())
    grid = next(m.value for m in at.markdown if "pf-g2e" in m.value and "By sender account" in m.value)
    assert "Deliverability" in grid


def test_the_chart_range_has_no_visible_label_of_its_own_but_keeps_one_for_screen_readers(fixture_repo):
    at = _run_page(_realistic_sheet())
    radio = at.radio(key="performance_range")
    assert radio.label == "Chart range" and radio.proto.label_visibility.value == 2          # collapsed (2) = hidden but still announced


def test_the_most_common_sending_errors_are_listed_with_how_many_times_each_happened(fixture_repo):
    sheet = _realistic_sheet()
    log = sheet._worksheets[f"{FIXTURE_CAMPAIGN} Custom Log Sheet"]._records
    for error in ("550 mailbox full", "550 mailbox full", "421 try again later"):
        log.append({"Timestamp": "2026-09-05 10:00:00", "LeadID": "1", "Stage": "intro", "Variant": "A", "Status": "failed", "MessageID": "", "Error": error, "SenderAccount": ""})
    at = _one_campaign(sheet)
    assert list(at.exception) == [] and any("Most common sending errors" in e.label for e in at.expander)
    text = " ".join(m.value for m in at.markdown)
    assert "2 × 550 mailbox full" in text and "1 × 421 try again later" in text
    assert text.index("2 × 550 mailbox full") < text.index("1 × 421 try again later")                   # the most common first


def test_with_no_failed_sends_there_is_no_most_common_errors_section(fixture_repo):
    at = _run_page(_sheet([], [], []))
    assert list(at.exception) == [] and not any("Most common sending errors" in e.label for e in at.expander)



# ================================================================ the toolbar (Export), the sender bars and the best-variant star

def test_the_performance_page_offers_an_export_of_what_it_shows(fixture_repo):
    at = _run_page(_realistic_sheet())
    export = [b for b in at.get("download_button") if b.proto.label == "⤓ Export"]
    assert len(export) == 1


def test_the_sender_bars_are_scaled_to_the_best_account_and_an_account_with_no_replies_has_an_empty_bar(fixture_repo):
    at = _one_campaign(_sheet_with_senders())
    widths = [int(w) for w in re.findall(r'class="pf-minibar"><i style="width:(\d+)%"', _markup(at))]
    assert widths == [100, 0]                                                    # sales1: 2 of 2 replied (the best); the other account: none
    table = next(t for t in _tables(at) if t[0][0] == "Account")
    assert [row[4] for row in table[1:]] == ["100.0%", "0.0%"]                      # the bar sits beside the percentage, which is still shown


def _best_variant_script(sent_per_variant):
    """A page that draws the Performance view for a campaign whose variant B earns the replies (12 positive on B, none on A)."""
    return f"""
import streamlit as st
import preview_logic
from performance_logic import build_campaign_performance, build_best_performers
from performance_view import render_performance
cfg = preview_logic.get_campaign_cfg("{FIXTURE_CAMPAIGN}")
leads, log, resp = [], [], []
for i in range(1, 2 * {sent_per_variant} + 1):
    variant = "A" if i <= {sent_per_variant} else "B"
    leads.append({{"LeadID": str(i), "Email": f"p{{i}}@x.com", "SenderAccount": "s1", "Status": "Intro Sent", "IntroVariant": variant, "IntroSentAt": "2026-09-01 10:00:00"}})
    log.append({{"Timestamp": "2026-09-01 10:00:00", "LeadID": str(i), "Stage": "intro", "Variant": variant, "Status": "sent", "MessageID": f"<m{{i}}@x>", "Error": "", "SenderAccount": "s1"}})
    if variant == "B" and i <= {sent_per_variant} + 12:
        resp.append({{"ResponseID": f"R{{i}}", "LeadID": str(i), "ReceivedAt": "2026-09-02 10:00:00", "Classification": "Genuine Reply", "Intent": "Interested", "InReplyTo": f"<m{{i}}@x>"}})
perf = build_campaign_performance(cfg, leads, resp, log, [])
render_performance(perf, key_prefix="demo", best=build_best_performers([perf], perf), show_campaign_ranking=False)
"""


def test_the_variant_the_what_worked_best_box_names_gets_a_star_in_the_table(fixture_repo):
    at = AppTest.from_string(_best_variant_script(40))
    at.run(timeout=30)
    assert list(at.exception) == []
    variants = next(t for t in _tables(at) if t[0][0] == "Variant")
    assert [row[0] for row in variants[1:]] == ["A", "B  ★ best"]                   # B earns the replies, and the box names B too
    assert "<b>Template variant:</b> B" in _markup(at)


def test_with_too_few_emails_to_compare_no_variant_is_starred(fixture_repo):
    at = AppTest.from_string(_best_variant_script(14))                                # 14 per variant: under the 30 needed to rank
    at.run(timeout=30)
    variants = next(t for t in _tables(at) if t[0][0] == "Variant")
    assert [row[0] for row in variants[1:]] == ["A", "B"] and "★" not in _markup(at)


def _two_senders_script():
    """sales A: 4 leads, 2 replied (50%); sales B: 4 leads, 1 replied (25%) — so the bars must be 100% and 50% (relative to the best), not 50% and 25%."""
    return f"""
import preview_logic
from performance_logic import build_campaign_performance, build_best_performers
from performance_view import render_performance
cfg = preview_logic.get_campaign_cfg("{FIXTURE_CAMPAIGN}")
leads, log, resp = [], [], []
for i in range(1, 9):
    account = "salesA" if i <= 4 else "salesB"
    leads.append({{"LeadID": str(i), "Email": f"p{{i}}@x.com", "SenderAccount": account, "Status": "Stopped - Replied" if i in (1, 2, 5) else "Intro Sent", "IntroVariant": "A", "IntroSentAt": "2026-09-01 10:00:00"}})
    log.append({{"Timestamp": "2026-09-01 10:00:00", "LeadID": str(i), "Stage": "intro", "Variant": "A", "Status": "sent", "MessageID": f"<m{{i}}@x>", "Error": "", "SenderAccount": account}})
    if i in (1, 2, 5):
        resp.append({{"ResponseID": f"R{{i}}", "LeadID": str(i), "ReceivedAt": "2026-09-02 10:00:00", "Classification": "Genuine Reply", "Intent": "Interested", "InReplyTo": f"<m{{i}}@x>"}})
perf = build_campaign_performance(cfg, leads, resp, log, [])
render_performance(perf, key_prefix="demo", best=build_best_performers([perf], perf), show_campaign_ranking=False)
"""


def test_the_sender_bars_are_relative_to_the_best_account_not_to_one_hundred_percent(fixture_repo):
    at = AppTest.from_string(_two_senders_script())
    at.run(timeout=30)
    assert list(at.exception) == []
    table = next(t for t in _tables(at) if t[0][0] == "Account")
    assert [(row[0], row[4]) for row in table[1:]] == [("salesA", "50.0%"), ("salesB", "25.0%")]
    assert [int(w) for w in re.findall(r'class="pf-minibar"><i style="width:(\d+)%"', _markup(at))] == [100, 50]


def test_all_time_errors_are_a_closed_dropdown_next_to_the_most_common_sending_errors_one(fixture_repo):
    sheet = _realistic_sheet()
    sheet._worksheets[f"{FIXTURE_CAMPAIGN} Custom Log Sheet"]._records.append({"Timestamp": "2026-09-05 10:00:00", "LeadID": "1", "Stage": "intro", "Variant": "A", "Status": "failed", "MessageID": "", "Error": "550 mailbox full", "SenderAccount": ""})
    at = _one_campaign(sheet)
    labels = [e.label for e in at.expander]
    assert "Most common sending errors" in labels and "Errors (all time)" in labels
    assert labels.index("Most common sending errors") < labels.index("Errors (all time)")
    assert all(e.proto.expanded is False for e in at.expander if e.label in ("Most common sending errors", "Errors (all time)"))


def test_with_no_errors_in_the_log_there_is_no_errors_dropdown_at_all(fixture_repo):
    at = _run_page(_sheet([], [], []))
    assert list(at.exception) == [] and not any(e.label == "Errors (all time)" for e in at.expander)


# ================================================================ the reply-rate change on the page

def _trend_script(recent_sent, earlier_sent):
    """Recent emails (1 day ago): leads 1-2 answered. Earlier emails (9 days ago): lead 101 answered. Replies are matched by message id."""
    return f"""
from datetime import datetime, timedelta, timezone
import preview_logic
from performance_logic import build_campaign_performance
from performance_view import render_performance
cfg = preview_logic.get_campaign_cfg("{FIXTURE_CAMPAIGN}")
def stamp(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d 10:00:00")
leads, log, resp = [], [], []
def email(lead_id, days_ago, answered):
    leads.append({{"LeadID": str(lead_id), "Email": f"p{{lead_id}}@x.com", "SenderAccount": "s1", "Status": "Stopped - Replied" if answered else "Intro Sent", "IntroVariant": "A", "IntroSentAt": stamp(days_ago)}})
    log.append({{"Timestamp": stamp(days_ago), "LeadID": str(lead_id), "Stage": "intro", "Variant": "A", "Status": "sent", "MessageID": f"<m{{lead_id}}@x>", "Error": "", "SenderAccount": "s1"}})
    if answered:
        resp.append({{"ResponseID": f"R{{lead_id}}", "LeadID": str(lead_id), "ReceivedAt": stamp(max(days_ago - 1, 0)), "Classification": "Genuine Reply", "Intent": "Interested", "InReplyTo": f"<m{{lead_id}}@x>"}})
for i in range({recent_sent}):
    email(1 + i, 1, i < 2)
for i in range({earlier_sent}):
    email(101 + i, 9, i < 1)
perf = build_campaign_performance(cfg, leads, resp, log, [])
render_performance(perf, key_prefix="demo", best=None, show_campaign_ranking=False)
"""


def test_the_reply_rate_card_shows_how_the_last_7_days_compare_with_the_7_before(fixture_repo):
    at = AppTest.from_string(_trend_script(6, 6))              # 2 of 6 answered now (33.3%) against 1 of 6 before (16.7%)
    at.run(timeout=30)
    text = _markup(at)
    assert list(at.exception) == [] and '<div class="pf-kpi-trend pf-trend-up">▲ 16.7 pts vs previous 7 days</div>' in text
    assert "Last 7 days: 33.3% of 6 emails" in text and "The 7 days before: 16.7% of 6 emails" in text and "Replies can take several days to arrive" in text
    assert text.count('<div class="pf-kpi-trend') == 1                    # only the Reply rate card has one (the stylesheet mentions the class too, so look for the element)


def test_the_change_is_hidden_when_either_week_has_too_few_emails_to_compare(fixture_repo):
    for recent, earlier in ((4, 6), (6, 4), (0, 6)):
        st.cache_data.clear()
        at = AppTest.from_string(_trend_script(recent, earlier))
        at.run(timeout=30)
        assert list(at.exception) == [] and '<div class="pf-kpi-trend' not in _markup(at) and "Replies can take several days" not in _markup(at), (recent, earlier)


def test_the_all_campaigns_page_total_never_shows_a_change(fixture_repo):
    at = _run_page(_realistic_sheet())
    assert '<div class="pf-kpi-trend' not in _markup(at)


def test_the_chosen_sort_is_what_the_table_is_built_with(fixture_repo):
    import performance_logic
    seen = []
    real = performance_logic.glance_rows

    def spy(perfs, status_labels=lambda s: s, sort="Reply rate"):
        seen.append(sort)
        return real(perfs, status_labels, sort)

    with patch("performance_logic.glance_rows", spy):
        at = _run_page(_realistic_sheet(), interact=lambda a: a.selectbox(key="performance_glance_sort").set_value("Emails sent"))
    assert seen[0] == "Reply rate" and seen[-1] == "Emails sent" and list(at.exception) == []



# ================================================================ where every lead is now

def _stage_sent_columns():
    import preview_logic
    import leads_logic
    return {leads_logic.sent_column(k): f"2026-10-0{min(k + 1, 9)} 09:00:00" for k in range(len(preview_logic.get_campaign_cfg(FIXTURE_CAMPAIGN)["stages"]))}


def _outcome_sheet():
    done = _stage_sent_columns()
    leads = [
        {"LeadID": "1", "Email": "a@x.com", **done, "Status": ""},                                                                         # finished, no reply: Completed
        {"LeadID": "2", "Email": "b@x.com", **done, "ReplyStatus": "Replied", "Status": "Stopped - Replied"},                              # finished, THEN replied: Replied
        {"LeadID": "3", "Email": "c@x.com", "IntroSentAt": "2026-10-01 09:00:00", "Status": ""},                                          # still going
        {"LeadID": "4", "Email": "d@x.com", "Status": ""},                                                                                 # not contacted
    ]
    return _sheet(leads, [], [])


def test_the_page_shows_where_every_lead_is_now_with_completed_and_in_progress_separate(fixture_repo):
    at = _run_page(_outcome_sheet())
    assert list(at.exception) == [] and "Where your leads are now" in _card_titles(at)
    bars = _bars(at)
    assert {label: bars[label][1] for label in ("Not contacted yet", "In progress", "Completed", "Replied")} == {"Not contacted yet": 1, "In progress": 1, "Completed": 1, "Replied": 1}
    assert "add up to your 4 leads" in _markup(at)



def test_the_sequence_completion_headline_is_still_the_engines_figure(fixture_repo):
    at = _run_page(_outcome_sheet())
    headline, _card = _headline_and_card_metrics(at)
    assert headline["Sequence completion"] == "66.7%"                                                       # 2 of the 3 contacted leads reached the final email, replied or not
    assert "2 reached the final stage" in _markup(at)


def test_the_export_lists_where_the_leads_are_now(fixture_repo):
    import performance_figures as F
    import performance_logic as PL
    import preview_logic
    cfg = preview_logic.get_campaign_cfg(FIXTURE_CAMPAIGN)
    done = _stage_sent_columns()
    leads = [{"LeadID": "1", "Email": "a@x.com", **done, "Status": ""}, {"LeadID": "2", "Email": "b@x.com", "Status": ""}]
    perf = PL.build_campaign_performance(cfg, leads, [], [])
    rows = F.build_performance_csv(perf, "Camp").split("\n")
    block = rows[rows.index("Where the leads are now") + 2: rows.index("Where the leads are now") + 4]
    assert block == ["Not contacted yet,1,50.0%", "In progress,0,0.0%"] and any(r == "Completed,1,50.0%" for r in rows)


def test_a_campaign_with_no_leads_shows_no_where_card(fixture_repo):
    at = _run_page(_sheet([], [], []))
    assert list(at.exception) == [] and "Where your leads are now" not in _card_titles(at)
