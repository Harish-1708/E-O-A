"""The Data tab's lead browser, run through the real campaign page: the table, filters, pages, the side panel, and how a click
points the existing 'Manage a lead' menu at a lead. It must never write anything by itself."""
import contextlib
import html
import os
import re
from datetime import date, timedelta
from unittest.mock import patch

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import leads_actions
import preview_logic
from github_client import GitHubActionsError
from test_pages_smoke import (
    FakeWorksheet, FakeSpreadsheet, FIXTURE_CAMPAIGN, PAGES_DIR, _dashboard_secrets, _authed_session, _mock_github_writes,
)

CAMPAIGNS_PAGE = os.path.join(PAGES_DIR, "campaigns.py")


@pytest.fixture(autouse=True)
def _every_test_uses_the_fixture_repo(fixture_repo):
    yield


@pytest.fixture(autouse=True)
def _stub_the_live_template_reads_other_tabs_make():
    filenames = [f"{stage}_{v}.txt" for stage in ["intro", "followup1", "followup2", "followup3", "followup4"] for v in "ABCD"]

    def content(self, path, ref="main"):
        return f"Subject: {path.rsplit('/', 1)[-1][:-4]}\n\nBody.".encode("utf-8")

    with patch("github_client.GitHubClient.list_directory_files", lambda self, path, ref="main": list(filenames)), \
         patch("github_client.GitHubClient.get_file_content", content):
        yield


@pytest.fixture(autouse=True)
def _clear_streamlit_caches():
    st.cache_resource.clear()
    st.cache_data.clear()
    yield
    st.cache_resource.clear()
    st.cache_data.clear()


def lead(i, **kw):
    base = {"LeadID": str(i), "FirstName": f"First{i}", "LastName": f"Last{i}", "Email": f"lead{i}@example.com", "Company": f"Company {i}", "Approval": "Yes",
            "SenderAccount": "sales1", "Status": ""}
    base.update(kw)
    return base


def _ws(leads, extra=None):
    ws = {f"{FIXTURE_CAMPAIGN} Master Sheet": FakeWorksheet(leads), f"{FIXTURE_CAMPAIGN} Response Sheet": FakeWorksheet([]),
          f"{FIXTURE_CAMPAIGN} Custom Log Sheet": FakeWorksheet([]), f"{FIXTURE_CAMPAIGN} Error Log": FakeWorksheet([])}
    ws.update(extra or {})
    return ws


@contextlib.contextmanager
def data_tab(leads, dispatch_error=None):
    captured, fake_create_file = _mock_github_writes()

    def fake_dispatch(self, workflow_file, inputs, ref="main"):
        if dispatch_error:
            raise dispatch_error
        captured.setdefault("dispatches", []).append((workflow_file, inputs))
        return {"id": 1, "html_url": "https://github.com/x"}

    ws = _ws(leads)
    captured["master"] = ws[f"{FIXTURE_CAMPAIGN} Master Sheet"]
    with patch("gspread.authorize", return_value=type("C", (), {"open_by_key": lambda self, k: FakeSpreadsheet(ws)})()), \
         patch("google.oauth2.service_account.Credentials.from_service_account_info", return_value=object()), \
         patch("github_client.GitHubClient.create_file", fake_create_file), patch("github_client.GitHubClient.dispatch_workflow", fake_dispatch):
        at = AppTest.from_file(CAMPAIGNS_PAGE)
        at.secrets.update(_dashboard_secrets())
        for key, value in _authed_session().items():
            at.session_state[key] = value
        at.session_state["selected_campaign"] = FIXTURE_CAMPAIGN
        at.run(timeout=30)
        yield at, captured


# ---------------------------------------------------------------- reading the page back

def _markup(at):
    return " ".join(m.value for m in at.markdown)


def _rows(at):
    out = []
    for chunk in re.findall(r'(<div class="ld-grid ld-row(?: ld-selected)?">.*?)(?=<div class="ld-grid ld-row|<style|$)', _markup(at), flags=re.S):
        out.append({
            "name": html.unescape(re.search(r'ld-name ld-cell">(.*?)</div>', chunk).group(1)),
            "company": html.unescape((re.search(r'ld-sub ld-cell">(.*?)</div>', chunk) or [None, ""])[1]),
            "email": html.unescape(re.search(r'ld-cell ld-muted">(.*?)</div>', chunk).group(1)),
            "status": html.unescape(re.search(r'class="ld-pill[^"]*">(.*?)</span>', chunk).group(1)),
            "dots": [("replied" if "ld-dot-replied" in d else "sent" if "ld-dot-sent" in d else "pending") for d in re.findall(r'<i class="(ld-dot[^"]*)">', chunk)],
            "selected": chunk.startswith('<div class="ld-grid ld-row ld-selected"'),
        })
    return out


def _open_keys(at):
    return [b.key[len("data_open_"):] for b in at.button if (b.key or "").startswith("data_open_")]


def _panel(at):
    markup = next((m.value for m in at.markdown if 'class="ld-panel"' in m.value), "")
    return html.unescape(re.sub(r"<[^>]+>", "|", markup))


def _click(at, key):
    at.button(key=key).click()
    at.run(timeout=30)


def _stage_count():
    return len(preview_logic.get_campaign_cfg(FIXTURE_CAMPAIGN)["stages"])


def _has(widgets, key):
    return any(w.key == key for w in widgets)


def _caption(at):
    return " ".join(c.value for c in at.caption)


# ================================================================ the table

def test_the_table_lists_each_lead_with_its_name_company_status_and_one_dot_per_stage():
    leads = [lead(1, Status="Stopped - Replied", IntroSentAt="2026-10-09 10:00:00", FollowUp1SentAt="2026-10-11 10:00:00"), lead(2, Status="Intro Sent", IntroSentAt="2026-10-12 10:00:00"), lead(3)]
    with data_tab(leads) as (at, captured):
        assert list(at.exception) == [] and list(at.error) == []
        rows = _rows(at)
        assert [(r["name"], r["company"], r["email"], r["status"]) for r in rows] == [
            ("First1 Last1", "Company 1", "lead1@example.com", "Replied"), ("First2 Last2", "Company 2", "lead2@example.com", "In sequence"),
            ("First3 Last3", "Company 3", "lead3@example.com", "Not contacted")]
        n = _stage_count()
        assert all(len(r["dots"]) == n for r in rows) and n >= 2
        assert rows[0]["dots"][:2] == ["sent", "replied"] and set(rows[0]["dots"][2:]) <= {"pending"}       # replied on the last stage sent
        assert rows[1]["dots"][0] == "sent" and set(rows[1]["dots"][1:]) == {"pending"} and set(rows[2]["dots"]) == {"pending"}


def test_the_caption_still_says_how_many_leads_are_showing():
    with data_tab([lead(i) for i in range(1, 6)]) as (at, _):
        assert "Showing 5 of 5 lead(s)" in _caption(at)


def test_looking_at_the_table_and_clicking_a_lead_never_writes_anything():
    with data_tab([lead(i) for i in range(1, 4)]) as (at, captured):
        _click(at, "data_open_2")
        _click(at, "data_open_3")
        assert "commits" not in captured and "dispatches" not in captured


def test_every_column_of_the_sheet_is_still_available_including_custom_ones():
    leads = [lead(1, Client="DubeRobe", ContentScore="8")]
    with data_tab(leads) as (at, _):
        assert any("All columns" in e.label for e in at.expander)
        table = at.dataframe[0].value
        assert "Client" in table.columns and "ContentScore" in table.columns and list(table["Client"]) == ["DubeRobe"]


def test_no_leads_at_all_still_says_so_and_offers_the_import():
    with data_tab([]) as (at, _):
        assert list(at.exception) == [] and any("No leads yet" in i.value for i in at.info)
        assert _rows(at) == [] and any("Add Leads" in e.label for e in at.expander)


# ================================================================ filter and search

def test_the_filter_offers_the_same_options_as_before_each_with_its_count():
    leads = [lead(1, Status="Stopped - Replied"), lead(2, Status="Stopped - Bounced"), lead(3, Status="Removed"), lead(4, IntroSentAt="2026-10-01 10:00:00", Status="Intro Sent"), lead(5, Approval="")]
    with data_tab(leads) as (at, _):
        options = at.radio(key="data_filter").options
        assert options == ["All · 5", "Pending Approval · 1", "In Progress · 1", "Replied · 1", "Bounced · 1", "Removed · 1"]


def test_the_filter_counts_ignore_the_search_but_follow_the_real_filter_rules():
    leads = [lead(i, Status="Stopped - Replied") for i in range(1, 4)] + [lead(9)]
    with data_tab(leads) as (at, _):
        at.text_input(key="data_search").set_value("lead9@")
        at.run(timeout=30)
        assert "Replied · 3" in at.radio(key="data_filter").options and "All · 4" in at.radio(key="data_filter").options


def test_choosing_a_filter_shows_only_those_leads_and_goes_back_to_page_one():
    leads = [lead(i) for i in range(1, 60)] + [lead(100, Status="Stopped - Replied"), lead(101, Status="Stopped - Replied")]
    with data_tab(leads) as (at, _):
        _click(at, "data_next")
        assert at.session_state["data_page"] == 1
        at.radio(key="data_filter").set_value("Replied")
        at.run(timeout=30)
        assert _open_keys(at) == ["100", "101"] and at.session_state["data_page"] == 0 and "Showing 1–2 of 2" in _caption(at)


def test_searching_narrows_the_table_and_goes_back_to_page_one():
    leads = [lead(i) for i in range(1, 60)]
    with data_tab(leads) as (at, _):
        _click(at, "data_next")
        at.text_input(key="data_search").set_value("lead42@")
        at.run(timeout=30)
        assert _open_keys(at) == ["42"] and at.session_state["data_page"] == 0


def test_a_search_or_filter_with_no_matches_says_so_and_offers_no_pager():
    with data_tab([lead(1)]) as (at, _):
        at.text_input(key="data_search").set_value("zzz-nobody")
        at.run(timeout=30)
        assert list(at.exception) == [] and "No leads match this search or filter." in _markup(at)
        assert _open_keys(at) == [] and not any(b.key == "data_next" for b in at.button)
        assert not any("Remove leads" in e.label for e in at.expander)                         # as before: nothing to remove or manage


# ================================================================ pages

def test_a_long_list_is_shown_25_at_a_time_with_a_pager():
    leads = [lead(i) for i in range(1, 61)]
    with data_tab(leads) as (at, _):
        assert len(_open_keys(at)) == 25 and _open_keys(at)[0] == "1" and "Showing 1–25 of 60 · page 1 of 3" in _caption(at)
        assert at.button(key="data_prev").disabled and not at.button(key="data_next").disabled
        _click(at, "data_next")
        assert _open_keys(at)[0] == "26" and len(_open_keys(at)) == 25 and "Showing 26–50 of 60 · page 2 of 3" in _caption(at)
        _click(at, "data_next")
        assert _open_keys(at) == [str(i) for i in range(51, 61)] and "Showing 51–60 of 60 · page 3 of 3" in _caption(at)
        assert at.button(key="data_next").disabled and not at.button(key="data_prev").disabled
        _click(at, "data_prev")
        assert _open_keys(at)[0] == "26"


def test_exactly_one_page_has_no_next_button_enabled():
    with data_tab([lead(i) for i in range(1, 26)]) as (at, _):
        assert at.button(key="data_next").disabled and at.button(key="data_prev").disabled and "page 1 of 1" in _caption(at)


# ================================================================ the side panel and the Manage menu

def test_with_nothing_chosen_the_panel_says_to_select_a_lead_and_shows_no_controls():
    with data_tab([lead(1)]) as (at, _):
        assert "Select a lead in the table" in _markup(at) and not any(r["selected"] for r in _rows(at))
        assert not any((w.key or "").startswith(("manage_", "remove_")) for w in list(at.button) + list(at.selectbox))


def test_clicking_a_row_opens_that_lead_in_the_panel_with_its_timeline():
    leads = [lead(1), lead(2, Status="Stopped - Replied", IntroSentAt="2026-10-09 10:00:00", FollowUp1SentAt="2026-10-11 10:00:00", ReplyStatus="Replied", ReplyAt="2026-10-13 08:00:00",
                          SenderAccount="sales2@x.com", ManualAsanaStage="Negotiating")]
    with data_tab(leads) as (at, _):
        _click(at, "data_open_2")
        assert [r["selected"] for r in _rows(at)] == [False, True]
        panel = _panel(at)
        for text in ("First2 Last2", "lead2@example.com", "Replied", "Lead ID", "Company 2", "sales2@x.com", "Negotiating", "Intro sent", "Follow-up 1 sent", "Oct 9", "Oct 11", "Replied", "Follow-ups stopped automatically"):
            assert text in panel, text


def test_the_chosen_lead_stays_chosen_when_you_turn_to_another_page_and_back():
    with data_tab([lead(i) for i in range(1, 61)]) as (at, _):
        _click(at, "data_open_5")
        _click(at, "data_next")
        assert "Select a lead in the table" not in _markup(at) and "First5 Last5" in _panel(at)
        _click(at, "data_prev")
        assert [r["selected"] for r in _rows(at)][4] is True


def test_two_leads_with_the_same_id_each_get_their_own_row_and_click_target():
    leads = [lead(5, Email="first@example.com", FirstName="First"), lead(5, Email="second@example.com", FirstName="Second")]
    with data_tab(leads) as (at, _):
        assert list(at.exception) == [] and _open_keys(at) == ["5", "5#2"]
        _click(at, "data_open_5#2")
        assert [r["selected"] for r in _rows(at)] == [False, True] and "second@example.com" in _panel(at)


def test_a_lead_with_no_id_is_still_clickable_by_its_address():
    with data_tab([lead("", Email="noid@example.com")]) as (at, _):
        assert _open_keys(at) == ["noid@example.com"]
        _click(at, "data_open_noid@example.com")
        assert "noid@example.com" in _panel(at)


# ================================================================ untrusted data

HOSTILE = '<script>alert(1)</script> "><img src=x onerror=alert(1)> [a](http://evil.example) **b**'


def test_hostile_text_from_a_csv_is_shown_as_text_in_the_table_and_the_panel_and_in_the_buttons_label():
    leads = [lead(1, FirstName=HOSTILE, LastName="", Company=HOSTILE, Email="x@evil.example", SenderAccount=HOSTILE)]
    with data_tab(leads) as (at, _):
        assert list(at.exception) == []
        _click(at, "data_open_1")
        markup = _markup(at)
        assert "<script>alert" not in markup and "<img src=x" not in markup and "&lt;script&gt;alert(1)&lt;/script&gt;" in markup
        assert _rows(at)[0]["name"] == HOSTILE and HOSTILE in html.unescape(_panel(at))
        label = next(b.label for b in at.button if b.key == "data_open_1")
        assert "\\[a\\]\\(http" in label and "\\*\\*b\\*\\*" in label                   # the invisible button's label is escaped too


# ================================================================ the writes are untouched

# ================================================================ gaps found by deliberately breaking the view

def test_a_new_search_returns_to_page_one_even_when_there_are_still_several_pages():
    with data_tab([lead(i) for i in range(1, 61)]) as (at, _):
        _click(at, "data_next")
        assert _open_keys(at)[0] == "26"
        at.text_input(key="data_search").set_value("example.com")                      # still matches all 60, so still 3 pages
        at.run(timeout=30)
        assert at.session_state["data_page"] == 0 and _open_keys(at)[0] == "1" and "page 1 of 3" in _caption(at)


def test_a_page_number_beyond_the_end_is_brought_back_so_prev_still_works():
    """Leads can disappear (a removal, a refresh) while a later page is open; Prev must then go to the page before the LAST one."""
    with data_tab([lead(i) for i in range(1, 61)]) as (at, _):
        at.session_state["data_page"] = 99
        at.run(timeout=30)
        assert at.session_state["data_page"] == 2 and _open_keys(at)[0] == "51"
        _click(at, "data_prev")
        assert at.session_state["data_page"] == 1 and _open_keys(at)[0] == "26"


def test_a_leads_key_is_the_same_whatever_the_filter_even_when_ids_repeat():
    leads = [lead(5, Email="first@example.com"), lead(5, Email="second@example.com", Status="Stopped - Replied")]
    with data_tab(leads) as (at, _):
        at.radio(key="data_filter").set_value("Replied")
        at.run(timeout=30)
        assert _open_keys(at) == ["5#2"]                                               # it is still the SECOND lead 5, not "5"
        _click(at, "data_open_5#2")
        assert "second@example.com" in _panel(at) and _has(at.button, "manage_save_5#2") and not _has(at.button, "manage_save_5")


def test_the_stylesheet_is_put_on_the_page_exactly_once():
    import leads_html
    with data_tab([lead(1)]) as (at, _):
        styles = [m.value for m in at.markdown if m.value.startswith("<style>") and ".ld-grid" in m.value]
        assert styles == [f"<style>{leads_html.LEADS_CSS}</style>"]


def test_using_the_lead_browser_never_re_reads_the_google_sheet():
    """Reads are a limited resource: clicking a lead, turning pages, searching and filtering are all just a redraw of what is loaded."""
    with data_tab([lead(i) for i in range(1, 61)]) as (at, captured):
        reads = captured["master"].read_call_count
        assert reads > 0
        _click(at, "data_open_3")
        _click(at, "data_next")
        _click(at, "data_prev")
        at.text_input(key="data_search").set_value("lead4")
        at.run(timeout=30)
        at.radio(key="data_filter").set_value("In Progress")
        at.run(timeout=30)
        assert list(at.exception) == [] and captured["master"].read_call_count == reads



# ================================================================ managing and removing the open lead, in its own panel

def _days_ago(n):
    return (date.today() - timedelta(days=n)).strftime("%Y-%m-%d 09:00:00")


def _commits(captured):
    return captured.get("commits", [])


def test_opening_a_lead_shows_its_manage_and_remove_controls_with_nothing_chosen_yet():
    with data_tab([lead(1), lead(2)]) as (at, captured):
        _click(at, "data_open_2")
        assert at.selectbox(key="manage_status_2").value == "(no change)" and at.selectbox(key="manage_asana_2").value == "(no change)"
        assert at.selectbox(key="manage_status_2").options == ["(no change)", "Stop sending to this lead", "Clear override (resume normal sending)"]
        assert "Rights Secured" in at.selectbox(key="manage_asana_2").options and "Clear override (resume automatic tracking)" in at.selectbox(key="manage_asana_2").options
        assert _has(at.button, "manage_save_2") and _has(at.button, "remove_ask_2")
        assert not _has(at.button, "manage_save_1") and not _has(at.button, "remove_ask_1")                  # only for the lead that is open
        assert "commits" not in captured and "dispatches" not in captured


def test_saving_with_nothing_changed_says_so_and_sends_nothing():
    with data_tab([lead(1)]) as (at, captured):
        _click(at, "data_open_1")
        _click(at, "manage_save_1")
        assert list(at.exception) == [] and any("Nothing selected to change." in w.value for w in at.warning) and "dispatches" not in captured


def test_stopping_sending_to_the_open_lead_sends_exactly_its_address_and_the_choice():
    with data_tab([lead(1), lead(2), lead(3)]) as (at, captured):
        _click(at, "data_open_3")
        at.selectbox(key="manage_status_3").set_value("Stop sending to this lead")
        at.run(timeout=30)
        _click(at, "manage_save_3")
        assert list(at.exception) == [] and list(at.error) == []
        assert captured["dispatches"] == [("set_lead_override.yml", {"campaign": FIXTURE_CAMPAIGN, "email": "lead3@example.com", "status": "Stop sending to this lead", "asana_stage": "(no change)"})]
        assert any("Update triggered for lead3@example.com" in s.value for s in at.success)


def test_setting_only_the_asana_stage_sends_that_and_no_status_change():
    with data_tab([lead(1)]) as (at, captured):
        _click(at, "data_open_1")
        at.selectbox(key="manage_asana_1").set_value("Negotiating")
        at.run(timeout=30)
        _click(at, "manage_save_1")
        assert captured["dispatches"][0][1] == {"campaign": FIXTURE_CAMPAIGN, "email": "lead1@example.com", "status": "(no change)", "asana_stage": "Negotiating"}


def test_a_status_chosen_for_one_lead_is_not_still_chosen_when_you_open_another():
    """The dangerous case this panel must never allow: choose 'Stop sending' for lead 1, click lead 2, press Save."""
    with data_tab([lead(1), lead(2)]) as (at, captured):
        _click(at, "data_open_1")
        at.selectbox(key="manage_status_1").set_value("Stop sending to this lead")
        at.run(timeout=30)
        _click(at, "data_open_2")
        assert at.selectbox(key="manage_status_2").value == "(no change)"
        _click(at, "manage_save_2")
        assert any("Nothing selected to change." in w.value for w in at.warning) and "dispatches" not in captured
        _click(at, "data_open_1")
        assert at.selectbox(key="manage_status_1").value == "(no change)"                                    # and lead 1's own choice did not linger either


def test_a_failed_save_shows_the_reason_and_changes_nothing_else():
    with data_tab([lead(1)], dispatch_error=GitHubActionsError("boom")) as (at, _):
        _click(at, "data_open_1")
        at.selectbox(key="manage_status_1").set_value("Stop sending to this lead")
        at.run(timeout=30)
        _click(at, "manage_save_1")
        assert list(at.exception) == [] and any("Failed to trigger update: boom" in e.value for e in at.error)


def test_the_current_status_and_override_are_shown_and_escaped():
    with data_tab([lead(1, Status="Stopped - Manual", ManualAsanaStage="Negotiating"), lead(2, Status="[x](http://evil.example) **bold**")]) as (at, _):
        _click(at, "data_open_1")
        assert "Current status: **Stopped \\- Manual** · Asana stage override: **Negotiating**" in _caption(at)
        _click(at, "data_open_2")
        assert "\\[x\\]\\(http" in _caption(at) and "\\*\\*bold\\*\\*" in _caption(at)


def test_a_lead_with_no_email_cannot_be_changed_from_the_panel_but_the_page_still_works():
    with data_tab([lead(1, Email="")]) as (at, _):
        _click(at, "data_open_1")
        assert list(at.exception) == [] and "no email address" in _caption(at) and not _has(at.button, "manage_save_1")


# ---- removing one lead

def _remove_one(at, key):
    _click(at, f"data_open_{key}")
    _click(at, f"remove_ask_{key}")


def test_removing_asks_for_a_second_click_and_writes_nothing_until_it_is_given():
    with data_tab([lead(1), lead(2)]) as (at, captured):
        _remove_one(at, "2")
        assert any("Remove" in w.value and "lead2\\@example\\.com" in w.value for w in at.warning)          # the address is escaped like all text from the Sheet
        assert _has(at.button, "remove_yes_2") and _has(at.button, "remove_cancel_2") and not _has(at.button, "remove_ask_2")
        assert _commits(captured) == [] and "dispatches" not in captured


def test_cancelling_a_removal_goes_back_and_writes_nothing():
    with data_tab([lead(1)]) as (at, captured):
        _remove_one(at, "1")
        _click(at, "remove_cancel_1")
        assert _has(at.button, "remove_ask_1") and not _has(at.button, "remove_yes_1") and _commits(captured) == [] and "dispatches" not in captured


def test_confirming_removes_exactly_that_lead_with_the_same_payload_and_workflow_as_always():
    import json
    with data_tab([lead(1), lead(2), lead(3)]) as (at, captured), patch.object(leads_actions.time, "sleep"):
        _remove_one(at, "3")
        _click(at, "remove_yes_3")
        assert list(at.exception) == [] and list(at.error) == []
        assert json.loads(_commits(captured)[0]["content"].decode("utf-8")) == {"lead_ids": ["3"]}
        path = _commits(captured)[0]["path"]
        assert path.startswith("removals/") and FIXTURE_CAMPAIGN in path
        assert captured["dispatches"] == [("remove_leads.yml", {"campaign": FIXTURE_CAMPAIGN, "payload_path": path})]
        assert any("Removal triggered" in s.value for s in at.success)
        assert _has(at.button, "remove_ask_3")                                                                 # back to the plain button, not stuck on the question


def test_a_failed_removal_shows_the_reason():
    with data_tab([lead(1)], dispatch_error=GitHubActionsError("boom")) as (at, _), patch.object(leads_actions.time, "sleep"):
        _remove_one(at, "1")
        _click(at, "remove_yes_1")
        assert list(at.exception) == [] and any("Removal failed: boom" in e.value for e in at.error)


def test_a_removal_waiting_for_confirmation_is_cancelled_by_opening_another_lead():
    with data_tab([lead(1), lead(2)]) as (at, captured):
        _remove_one(at, "1")
        _click(at, "data_open_2")
        assert _has(at.button, "remove_ask_2") and not _has(at.button, "remove_yes_2")
        _click(at, "data_open_1")
        assert _has(at.button, "remove_ask_1") and not _has(at.button, "remove_yes_1") and _commits(captured) == []     # not waiting any more


def test_a_lead_with_no_id_cannot_be_removed_from_the_panel():
    with data_tab([lead("", Email="noid@example.com")]) as (at, _):
        _click(at, "data_open_noid@example.com")
        assert "no Lead ID" in _caption(at) and not any((b.key or "").startswith("remove_") for b in at.button)


def test_removing_several_leads_at_once_is_still_available_and_unchanged():
    with data_tab([lead(1), lead(2)]) as (at, _):
        assert any("Remove several leads at once" in e.label for e in at.expander) and _has(at.multiselect, "remove_select")
        assert not any("Manage a lead" in e.label for e in at.expander)                                       # the common Manage section is gone


# ================================================================ the extra filters

def _filter_leads():
    return [lead(1, SenderAccount="sales1", IntroSentAt=_days_ago(0)), lead(2, SenderAccount="sales2", IntroSentAt=_days_ago(10), FollowUp1SentAt=_days_ago(3)),
            lead(3, SenderAccount="sales1", IntroSentAt=_days_ago(60)), lead(4, SenderAccount="", IntroSentAt=_days_ago(5), FollowUp1SentAt=_days_ago(5), FollowUp2SentAt=_days_ago(5)),
            lead(5, SenderAccount="sales2")]


def test_the_three_extra_filters_are_offered_with_the_real_options():
    with data_tab(_filter_leads()) as (at, _):
        assert at.selectbox(key="data_filter_sender").options == ["All senders", "sales1", "sales2", "(no sender yet)"]
        stages = at.selectbox(key="data_filter_stage").options
        assert stages[:3] == ["Any stage", "Not contacted yet", "At Intro"] and len(stages) == 2 + _stage_count() and stages[3] == "At Follow-up 1"
        assert at.selectbox(key="data_filter_recent").options == ["Any time", "Today", "Last 7 days", "Last 30 days", "Older than 30 days"]
        assert [at.selectbox(key=k).value for k in ("data_filter_sender", "data_filter_stage", "data_filter_recent")] == ["All senders", "Any stage", "Any time"]


@pytest.mark.parametrize("key, choice, expected", [
    ("data_filter_sender", "sales1", ["1", "3"]), ("data_filter_sender", "(no sender yet)", ["4"]),
    ("data_filter_stage", "Not contacted yet", ["5"]), ("data_filter_stage", "At Intro", ["1", "3"]), ("data_filter_stage", "At Follow-up 1", ["2"]),
    ("data_filter_recent", "Today", ["1"]), ("data_filter_recent", "Last 7 days", ["1", "2", "4"]), ("data_filter_recent", "Older than 30 days", ["3"]),
])
def test_each_extra_filter_narrows_the_table_and_the_count(key, choice, expected):
    with data_tab(_filter_leads()) as (at, _):
        at.selectbox(key=key).set_value(choice)
        at.run(timeout=30)
        assert list(at.exception) == [] and _open_keys(at) == expected and f"Showing {len(expected)} of 5 lead(s)" in _caption(at)


def test_the_extra_filters_combine_with_each_other_the_status_chips_and_search():
    with data_tab(_filter_leads()) as (at, _):
        at.selectbox(key="data_filter_sender").set_value("sales1")
        at.selectbox(key="data_filter_recent").set_value("Last 30 days")
        at.run(timeout=30)
        assert _open_keys(at) == ["1"]
        at.text_input(key="data_search").set_value("lead3")
        at.run(timeout=30)
        assert _open_keys(at) == [] and "No leads match this search or filter." in _markup(at)


def test_changing_an_extra_filter_goes_back_to_page_one():
    leads = [lead(i, SenderAccount="sales1" if i % 2 else "sales2", IntroSentAt=_days_ago(1)) for i in range(1, 91)]
    with data_tab(leads) as (at, _):
        _click(at, "data_next")
        assert at.session_state["data_page"] == 1
        at.selectbox(key="data_filter_sender").set_value("sales1")
        at.run(timeout=30)
        assert at.session_state["data_page"] == 0 and _open_keys(at)[0] == "1" and "page 1 of 2" in _caption(at)


def test_the_clear_filters_button_appears_only_while_something_filters_and_puts_everything_back():
    with data_tab(_filter_leads()) as (at, _):
        assert not _has(at.button, "data_filters_clear")
        at.selectbox(key="data_filter_sender").set_value("sales1")
        at.selectbox(key="data_filter_stage").set_value("At Intro")
        at.selectbox(key="data_filter_recent").set_value("Today")
        at.text_input(key="data_search").set_value("lead")
        at.radio(key="data_filter").set_value("Replied")
        at.run(timeout=30)
        assert _has(at.button, "data_filters_clear")
        _click(at, "data_filters_clear")
        assert list(at.exception) == [] and not _has(at.button, "data_filters_clear")
        assert at.text_input(key="data_search").value == "" and at.radio(key="data_filter").value.startswith("All")
        assert [at.selectbox(key=k).value for k in ("data_filter_sender", "data_filter_stage", "data_filter_recent")] == ["All senders", "Any stage", "Any time"]
        assert _open_keys(at) == ["1", "2", "3", "4", "5"]


def test_a_remembered_choice_that_no_longer_exists_goes_back_to_the_first_option_without_hiding_anyone():
    """E.g. the only lead from one sender was removed while that sender was chosen. The stale value is planted from inside the
    script (AppTest cannot inject an invalid widget value from outside), the way it would arise in a real session."""
    def script():
        import streamlit as st
        from leads_view import render_extra_filters
        if "planted" not in st.session_state:
            st.session_state.update({"planted": True, "data_filter_sender": "ghost@nowhere", "data_filter_stage": "At Follow-up 9", "data_filter_recent": "Sometime"})
        st.session_state["chosen"] = render_extra_filters([{"SenderAccount": "sales1", "IntroSentAt": "2026-10-01 09:00:00"}], None)

    at = AppTest.from_function(script)
    at.run(timeout=30)
    assert list(at.exception) == [] and at.session_state["chosen"] == ("All senders", "Any stage", "Any time")



def test_empty_company_and_approval_are_not_shown_as_blank_rows_in_the_panel():
    with data_tab([lead(1, Company="", Approval="")]) as (at, _):
        _click(at, "data_open_1")
        panel = _panel(at)
        assert "Company" not in panel and "Approval" not in panel and "Lead ID" in panel and "Last sent" in panel


def test_using_the_extra_filters_and_the_panel_controls_never_re_reads_the_google_sheet():
    with data_tab(_filter_leads()) as (at, captured):
        reads = captured["master"].read_call_count
        at.selectbox(key="data_filter_sender").set_value("sales1")
        at.run(timeout=30)
        at.selectbox(key="data_filter_recent").set_value("Last 7 days")
        at.run(timeout=30)
        _click(at, "data_open_1")
        _click(at, "remove_ask_1")
        _click(at, "remove_cancel_1")
        _click(at, "data_filters_clear")
        assert list(at.exception) == [] and captured["master"].read_call_count == reads


# ================================================================ gaps found by deliberately breaking the actions

def test_the_removal_message_is_shown_once_and_not_on_every_later_redraw():
    with data_tab([lead(1), lead(2)]) as (at, captured), patch.object(leads_actions.time, "sleep"):
        _remove_one(at, "1")
        _click(at, "remove_yes_1")
        assert any("Removal triggered" in s.value for s in at.success)
        at.selectbox(key="manage_status_1").set_value("Stop sending to this lead")                    # any later interaction
        at.run(timeout=30)
        assert not any("Removal triggered" in s.value for s in at.success) and len(_commits(captured)) == 1


def test_there_is_a_pause_between_committing_the_removal_file_and_starting_its_workflow_as_there_always_was():
    with data_tab([lead(1)]) as (at, captured), patch.object(leads_actions.time, "sleep") as slept:
        _remove_one(at, "1")
        _click(at, "remove_yes_1")
        one_second_pauses = [c for c in slept.call_args_list if c.args == (1,)]                  # (the test harness itself also sleeps, a millisecond at a time)
        assert len(one_second_pauses) == 1
        assert len(_commits(captured)) == 1 and len(captured["dispatches"]) == 1


def test_manage_and_remove_are_separated_by_a_divider():
    with data_tab([lead(1)]) as (at, _):
        before = len(at.get("divider"))
        _click(at, "data_open_1")
        assert len(at.get("divider")) == before + 1
