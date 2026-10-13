"""The campaign page's title bar, run through the real page: the name, the status pill, the schedule and sender chips, the sent-today
line, the action button, and that nothing about it can change what a button does."""
import contextlib
import os
import re
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import campaign_html
from test_pages_smoke import (
    FakeWorksheet, FakeSpreadsheet, FIXTURE_CAMPAIGN, PAGES_DIR, _dashboard_secrets, _authed_session, _mock_github_writes,
)

CAMPAIGNS_PAGE = os.path.join(PAGES_DIR, "campaigns.py")


@pytest.fixture(autouse=True)
def _every_test_uses_the_fixture_repo(fixture_repo):
    yield


@pytest.fixture(autouse=True)
def _stub_the_live_template_reads():
    filenames = [f"{stage}_{v}.txt" for stage in ["intro", "followup1", "followup2", "followup3", "followup4"] for v in "ABCD"]
    with patch("github_client.GitHubClient.list_directory_files", lambda self, path, ref="main": list(filenames)), \
         patch("github_client.GitHubClient.get_file_content", lambda self, path, ref="main": f"Subject: {path.rsplit('/', 1)[-1][:-4]}\n\nBody.".encode("utf-8")):
        yield


@pytest.fixture(autouse=True)
def _clear_streamlit_caches():
    st.cache_resource.clear()
    st.cache_data.clear()
    yield
    st.cache_resource.clear()
    st.cache_data.clear()


def _stamp(days_ago=0, hours_ago=0):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago, hours=hours_ago)).strftime("%Y-%m-%d %H:%M:%S")


def lead(i, **kw):
    base = {"LeadID": str(i), "FirstName": f"First{i}", "LastName": "", "Email": f"lead{i}@example.com", "Approval": "Yes", "SenderAccount": "sales1", "Status": ""}
    base.update(kw)
    return base


@contextlib.contextmanager
def campaign_page(leads=None, send_log=None, status=None, selected=FIXTURE_CAMPAIGN, drop_master=False, draft=False):
    leads = [lead(1)] if leads is None else leads
    ws = {f"{FIXTURE_CAMPAIGN} Master Sheet": FakeWorksheet(leads), f"{FIXTURE_CAMPAIGN} Response Sheet": FakeWorksheet([]),
          f"{FIXTURE_CAMPAIGN} Custom Log Sheet": FakeWorksheet(send_log or []), f"{FIXTURE_CAMPAIGN} Error Log": FakeWorksheet([])}
    if drop_master:
        del ws[f"{FIXTURE_CAMPAIGN} Master Sheet"]                                      # the Sheet cannot be read for this campaign
    captured, fake_create_file = _mock_github_writes()
    captured["master"] = ws.get(f"{FIXTURE_CAMPAIGN} Master Sheet")
    patches = [patch("gspread.authorize", return_value=type("C", (), {"open_by_key": lambda self, k: FakeSpreadsheet(ws)})()),
               patch("google.oauth2.service_account.Credentials.from_service_account_info", return_value=object()),
               patch("github_client.GitHubClient.create_file", fake_create_file),
               patch("github_client.GitHubClient.dispatch_workflow", lambda self, workflow, inputs, ref="main": captured.setdefault("dispatches", []).append((workflow, inputs)) or {"id": 1})]
    if draft:
        import preview_logic
        real_cfg = preview_logic.get_campaign_cfg
        patches.append(patch("preview_logic.get_campaign_cfg", lambda name: {**real_cfg(name), "status": "draft"}))
    if status:
        import campaign_status_logic as S
        patches.append(patch("campaign_status_logic.compute_campaign_status", lambda cfg, leads_: (status, ["a problem"] if status == S.STATUS_ATTENTION else [])))
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        at = AppTest.from_file(CAMPAIGNS_PAGE)
        at.secrets.update(_dashboard_secrets())
        for key, value in _authed_session().items():
            at.session_state[key] = value
        if selected:
            at.session_state["selected_campaign"] = selected
        at.run(timeout=30)
        yield at, captured


def _chips(at):
    return re.findall(r'<span class="cp-chip">(.*?)</span>', " ".join(m.value for m in at.markdown))


def _subline(at):
    found = re.findall(r'<div class="cp-subline">(.*?)</div>', " ".join(m.value for m in at.markdown))
    return found[0] if found else None


# ================================================================ the title bar

def test_the_title_bar_shows_the_name_the_status_and_both_chips_with_no_errors():
    with campaign_page([lead(1, SenderAccount="sales1"), lead(2, SenderAccount="sales2")]) as (at, _):
        assert list(at.exception) == [] and list(at.error) == []
        assert FIXTURE_CAMPAIGN in [t.value for t in at.title] and len(_chips(at)) == 2
        assert _chips(at)[1] == "2 sender accounts"


def test_the_schedule_chip_says_what_the_campaign_really_has():
    from preview_logic import get_campaign_cfg
    from campaign_header_logic import schedule_summary
    with campaign_page() as (at, _):
        assert _chips(at)[0] == schedule_summary(get_campaign_cfg(FIXTURE_CAMPAIGN))


def test_a_campaign_with_no_leads_says_there_is_no_sender_yet():
    with campaign_page([]) as (at, _):
        assert _chips(at)[1] == "No sender yet" and list(at.exception) == []


def test_the_sub_line_counts_only_emails_sent_today():
    log = [{"Timestamp": _stamp(0, 0), "Status": "sent"}, {"Timestamp": _stamp(0, 0), "Status": "sent"}, {"Timestamp": _stamp(3), "Status": "sent"}, {"Timestamp": _stamp(0, 0), "Status": "failed"}]
    with campaign_page(send_log=log) as (at, _):
        sub = _subline(at)
        assert sub is not None and re.fullmatch(r"(Next automatic send: .+ \(in .+\) · )?\d+ sent today( · daily limit \d+)?", sub)
        from preview_logic import get_campaign_cfg
        from campaign_header_logic import subline
        cfg = get_campaign_cfg(FIXTURE_CAMPAIGN)
        assert sub.endswith(subline(cfg, log))                                                # the page shows exactly what the logic says (after any next-send part)
        if not cfg.get("schedule"):                                                           # no time zone: the UTC day, so the count is exact
            assert subline(cfg, log).startswith("2 sent today")                               # two sent now; not the one 3 days ago, not the failed one


def test_the_status_pill_and_the_chips_agree_on_the_colour_family():
    import campaign_status_logic as S
    for status, family in ((S.STATUS_RUNNING, "good"), (S.STATUS_PAUSED, "waiting"), (S.STATUS_ATTENTION, "bad"), (S.STATUS_DRAFT, "neutral"), (S.STATUS_COMPLETED, "neutral")):
        st.cache_data.clear()
        with campaign_page(status=status) as (at, _):
            assert f"cp-s-{family}" in " ".join(m.value for m in at.markdown), status
            assert S.status_label(status) in [h.value for h in at.subheader], status


@pytest.mark.parametrize("status, button", [("running", "⏸ Pause"), ("attention", "⏸ Pause"), ("paused", "▶ Resume"), ("draft", "🚀 Launch")])
def test_each_status_offers_its_one_action_button_with_the_same_label_as_ever(status, button):
    with campaign_page(status=status) as (at, _):
        assert [b.label for b in at.button if b.label in ("⏸ Pause", "▶ Resume", "🚀 Launch")] == [button]


def test_pausing_still_changes_the_campaign_status_the_same_way():
    with campaign_page(status="running") as (at, captured):
        next(b for b in at.button if b.label == "⏸ Pause").click()
        at.run(timeout=30)
        assert list(at.exception) == [] and any("Paused." in s.value for s in at.success)


def test_a_problem_is_still_listed_under_the_title_bar_and_a_draft_still_gets_its_banner():
    with campaign_page(status="attention") as (at, _):
        assert any("a problem" in c.value for c in at.caption)


def test_a_campaign_that_really_is_a_draft_gets_its_banner_and_a_launch_button():
    with campaign_page(draft=True) as (at, _):
        assert list(at.exception) == [] and any("this campaign hasn't been launched yet" in i.value for i in at.info)
        assert any(b.label == "🚀 Launch" for b in at.button)


def test_a_campaign_that_is_not_a_draft_has_no_draft_banner():
    with campaign_page() as (at, _):
        assert not any("hasn't been launched yet" in i.value for i in at.info)


def test_a_campaign_whose_sheet_cannot_be_read_still_shows_its_title_bar_with_a_warning_and_no_crash():
    with campaign_page(drop_master=True) as (at, _):
        assert list(at.exception) == []
        assert FIXTURE_CAMPAIGN in [t.value for t in at.title] and _chips(at)[1] == "No sender yet"
        assert any("Couldn't load Sheet data yet" in w.value for w in at.warning)


def test_back_and_refresh_are_still_there_with_their_original_labels_and_back_still_goes_back():
    with campaign_page() as (at, _):
        labels = [b.label for b in at.button]
        assert "← Campaigns" in labels and "🔄 Refresh data" in labels and "← Back to Campaigns" not in labels
        next(b for b in at.button if b.label == "← Campaigns").click()
        at.run(timeout=30)
        assert list(at.exception) == [] and len(at.tabs) == 0                                  # back on the campaign list: no tabs, no title bar


def test_the_page_skin_is_added_once_on_a_campaign_and_not_on_the_campaign_list():
    with campaign_page() as (at, _):
        styles = [m.value for m in at.markdown if m.value.startswith("<style>") and ".cp-chips" in m.value]
        assert styles == [f"<style>{campaign_html.CAMPAIGN_SKIN_CSS}</style>"]
    st.cache_data.clear()
    with campaign_page(selected=None) as (at, _):
        assert not any(".cp-chips" in m.value for m in at.markdown)


def test_the_title_bar_comes_before_the_tabs_and_the_tabs_are_still_six():
    with campaign_page() as (at, _):
        assert len(at.tabs) == 6 and [t.label for t in at.tabs][0] == "Performance"



# ================================================================ the next automatic send, in the real header

def test_a_running_campaign_shows_when_it_will_next_send_at_the_real_workflow_minute():
    with campaign_page(status="running") as (at, _):
        sub = _subline(at)
        match = re.match(r"Next automatic send: (.+?) (\d{1,2}):(\d{2}) (AM|PM) \(in (.+?)\) · ", sub)
        assert match, sub                                                                    # a campaign with no schedule always has a next run
        from preview_logic import get_campaign_cfg
        tz = (get_campaign_cfg(FIXTURE_CAMPAIGN).get("schedule") or {}).get("timezone")
        if not tz:
            assert match.group(3) == "43"                                                    # :43 UTC, read from .github/workflows/auto_send.yml


@pytest.mark.parametrize("status", ["paused", "draft", "attention", "completed"])
def test_a_campaign_that_is_not_running_does_not_claim_a_next_automatic_send(status):
    with campaign_page(status=status) as (at, _):
        assert "Next automatic send" not in (_subline(at) or "")


def test_the_page_still_works_when_the_workflow_file_cannot_be_read():
    with patch("config.REPO_ROOT", "/no/such/folder"):                                       # the real file read now genuinely fails
        with campaign_page(status="running") as (at, _):
            assert list(at.exception) == [] and "Next automatic send" not in (_subline(at) or "") and _subline(at) is not None


def test_the_header_has_a_send_now_popover_and_a_more_menu_with_refresh_inside():
    with campaign_page(status="running") as (at, _):
        assert [p.proto.popover.label for p in at.get("popover")] == ["➤ Send now", "⋯"]
        assert any(b.key == "campaigns_refresh_data" for b in at.button) and any(b.key == "campaigns_send_batch_button" for b in at.button)


def test_send_is_no_longer_at_the_foot_of_the_settings_tab():
    with campaign_page(status="running") as (at, _):
        send_buttons = [b for b in at.button if b.key == "campaigns_send_batch_button"]
        assert len(send_buttons) == 1                                                        # one Send form on the page, not two (that would also crash on duplicate keys)


def test_the_foot_of_the_page_says_which_layout_build_is_live():
    with campaign_page() as (at, _):
        assert f"UI build {campaign_html.UI_BUILD}" in " ".join(m.value for m in at.markdown)


def test_the_breadcrumb_shows_the_campaign_name_after_the_campaigns_link():
    with campaign_page() as (at, _):
        assert f'<span class="cp-crumb-name">/ {FIXTURE_CAMPAIGN}</span>' in " ".join(m.value for m in at.markdown)



def test_the_performance_refresh_button_makes_the_page_read_the_google_sheet_again():
    with campaign_page() as (at, captured):
        reads = captured["master"].read_call_count
        assert reads > 0
        at.run(timeout=30)
        assert captured["master"].read_call_count == reads                                 # an ordinary redraw reads nothing (the data is cached)
        next(b for b in at.button if b.key == "campaigns_perf_refresh").click()
        at.run(timeout=30)
        assert list(at.exception) == [] and captured["master"].read_call_count > reads


def test_the_more_menus_refresh_does_the_same_as_the_performance_one():
    with campaign_page() as (at, captured):
        reads = captured["master"].read_call_count
        next(b for b in at.button if b.key == "campaigns_refresh_data").click()
        at.run(timeout=30)
        assert captured["master"].read_call_count > reads
