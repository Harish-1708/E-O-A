"""The Settings tab through the real page: sender accounts, sending limits, the status card, readiness and the danger zone."""
import contextlib
import html
import os
import re
import sys
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
import streamlit as st
import yaml
from streamlit.testing.v1 import AppTest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from test_pages_smoke import FakeWorksheet, FakeSpreadsheet, FIXTURE_CAMPAIGN, PAGES_DIR, _authed_session, _dashboard_secrets, _mock_github_writes  # noqa: E402

OVERRIDE_PATH = f"config/campaigns/{FIXTURE_CAMPAIGN}.yaml"
ACCOUNTS = {"sales1": "sales1@kelson.agency", "sales2": "sales2@kelson.agency", "sales3": "sales3@kelson.agency"}
MANY = {**ACCOUNTS, "sales4": "sales4@kelson.agency", "sales5": "sales5@kelson.agency", "sales6": "sales6@kelson.agency"}      # enough accounts for the search box to be shown
SCHEDULE = {"timezone": "America/Chicago", "window_start": "08:30", "window_end": "18:15", "send_days": ["mon", "wed"]}


@pytest.fixture(autouse=True)
def _every_test_uses_the_fixture_repo(fixture_repo):
    yield


@pytest.fixture(autouse=True)
def _stub_the_live_template_listing():
    names = [f"{stage}_{v}.txt" for stage in ["intro", "followup1", "followup2", "followup3", "followup4"] for v in "ABCD"]
    with patch("github_client.GitHubClient.list_directory_files", lambda self, path, ref="main": list(names)):
        yield


@pytest.fixture(autouse=True)
def _clear_streamlit_caches():
    st.cache_resource.clear()
    st.cache_data.clear()
    yield
    st.cache_resource.clear()
    st.cache_data.clear()


def lead(i, **kw):
    base = {"LeadID": str(i), "FirstName": f"First{i}", "LastName": "", "Email": f"lead{i}@example.com", "Approval": "Yes", "SenderAccount": "sales1", "Status": "", "ReplyStatus": ""}
    base.update(kw)
    return base


def stamp(days_ago=0, minutes=0):
    return (datetime.now() - timedelta(days=days_ago, minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")


def sent(account, days_ago=0, status="sent", n=0):
    return {"Timestamp": stamp(days_ago, n), "LeadID": str(n + 1), "Stage": "intro", "Variant": "A", "Status": status, "MessageID": f"<m{account}{days_ago}{n}@x>", "Error": "", "SenderAccount": account}


@contextlib.contextmanager
def settings_page(leads=None, send_log=None, status=None, cfg=None, sending=None, commit_error=None, delete_error=None, live=None, accounts=ACCOUNTS, health=None,
                  intro_text="Subject: Hi\n\nHi {{FirstName}}", templates_fail=False, health_fail=False, no_slots=False, selected=FIXTURE_CAMPAIGN, campaigns_dir=None):
    """The campaign page (the Settings tab is one of its tabs) against fake Sheets and a fake GitHub. `cfg` REPLACES keys of the campaign's settings; `sending` is
    merged into its sending settings. `live` is a dict of the files on GitHub right now ({path: bytes}): saves are remembered in it and read back from it."""
    leads = [lead(1)] if leads is None else leads
    ws = {f"{FIXTURE_CAMPAIGN} Master Sheet": FakeWorksheet(leads), f"{FIXTURE_CAMPAIGN} Response Sheet": FakeWorksheet([]),
          f"{FIXTURE_CAMPAIGN} Custom Log Sheet": FakeWorksheet(send_log or []), f"{FIXTURE_CAMPAIGN} Error Log": FakeWorksheet([])}
    if health is not None:
        ws["Email Accounts Health"] = FakeWorksheet(health)
    captured, fake_create_file = _mock_github_writes()

    def create_file(self, path, content, message="", **kw):
        if commit_error:
            raise commit_error
        if live is not None:
            live[path] = content
        return fake_create_file(self, path, content, message=message, **kw)

    def delete_file(self, path, message="", **kw):
        if delete_error:
            raise delete_error
        captured.setdefault("deleted", []).append(path)

    def get_file(self, path, ref="main"):
        if path.startswith("config/campaigns/"):
            if live is None:
                raise RuntimeError("GitHub is not reachable")                  # -> the page falls back to its local copy
            return live.get(path)
        if "/intro_" in path:
            if templates_fail:
                raise RuntimeError("template unreadable")
            return intro_text.encode("utf-8")
        return b"Subject: S\n\nBody."

    patches = [patch("gspread.authorize", return_value=type("C", (), {"open_by_key": lambda self, k: FakeSpreadsheet(ws)})()),
               patch("google.oauth2.service_account.Credentials.from_service_account_info", return_value=object()),
               patch("github_client.GitHubClient.create_file", create_file), patch("github_client.GitHubClient.delete_file", delete_file),
               patch("github_client.GitHubClient.get_file_content", get_file),
               patch("github_client.GitHubClient.dispatch_workflow", lambda self, workflow, inputs, ref="main": captured.setdefault("dispatches", []).append((workflow, inputs)) or {"id": 1})]
    if campaigns_dir is not None:
        patches.append(patch("config.CAMPAIGNS_DIR", campaigns_dir))
    if no_slots:
        patches.append(patch("config.EMAIL_ACCOUNT_SLOT_MAPPING_ABS_PATH", os.path.join(os.path.dirname(__file__), "no_such_slot_mapping.yaml")))
    if health_fail:
        patches.append(patch("sheets_readonly.ReadOnlySheetsConnector.get_account_health", side_effect=RuntimeError("sheet unreachable")))
    if cfg is not None or sending is not None:
        import preview_logic
        real_cfg = preview_logic.get_campaign_cfg

        def patched(name):
            base = real_cfg(name)
            if sending is not None:
                base = {**base, "sending": {**(base.get("sending") or {}), **sending}}
            return {**base, **(cfg or {})}
        patches.append(patch("preview_logic.get_campaign_cfg", patched))
    if status:
        patches.append(patch("campaign_status_logic.compute_campaign_status", lambda cfg_, leads_: (status, [])))
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        at = AppTest.from_file(os.path.join(PAGES_DIR, "campaigns.py"))
        secrets = _dashboard_secrets()
        secrets["email_accounts_directory"] = dict(accounts)
        at.secrets.update(secrets)
        for key, value in _authed_session().items():
            at.session_state[key] = value
        at.session_state["selected_campaign"] = selected
        at.run(timeout=30)
        yield at, captured


def markup(at):
    return " ".join(m.value for m in at.markdown)


def toggles(at):
    prefix = f"se_acct_{FIXTURE_CAMPAIGN}_"
    return {t.key[len(prefix):]: t.value for t in at.toggle if (t.key or "").startswith(prefix)}


def set_toggle(at, name, value):
    next(t for t in at.toggle if t.key == f"se_acct_{FIXTURE_CAMPAIGN}_{name}").set_value(value)
    at.run(timeout=30)


def select_all_button(at):
    return next(b for b in at.button if (b.key or "").startswith("se_selectall_button_"))


def save_button(at):
    return next(b for b in at.button if b.key == "se_save_button")


def rotation_toggle(at):
    return next(t for t in at.toggle if t.key == f"se_rotation_toggle_{FIXTURE_CAMPAIGN}")


def daily_input(at):
    return next(n for n in at.number_input if n.key == f"se_limit_daily_input_{FIXTURE_CAMPAIGN}")


def per_toggle(at):
    return next(t for t in at.toggle if t.key == f"se_limit_peron_{FIXTURE_CAMPAIGN}")


def per_input(at):
    return next(n for n in at.number_input if n.key == f"se_limit_per_{FIXTURE_CAMPAIGN}")


def override_yaml(**keys):
    return yaml.safe_dump(keys).encode("utf-8")


def committed(captured, index=-1):
    return yaml.safe_load(captured["commits"][index]["content"])


def readiness(at):
    """[(icon, text)] for the checklist, with HTML entities decoded (an apostrophe is stored as &#x27;)."""
    return [(icon, html.unescape(text)) for icon, text in re.findall(r'se-check-icon">(.*?)</span><span>(.*?)</span>', markup(at))]


def account_notes(at):
    return re.findall(r'se-acct-name">(.*?)</div><div class="se-acct-note [^"]*">(.*?)</div>', markup(at))


# ================================================================ sender accounts

def test_there_is_one_toggle_per_account_in_name_order_and_the_picked_ones_start_on():
    with settings_page(sending={"rotation_accounts": ["sales2"]}) as (at, _):
        assert list(at.exception) == [] and list(at.error) == []
        assert toggles(at) == {"sales1": False, "sales2": True, "sales3": False} and list(toggles(at)) == ["sales1", "sales2", "sales3"]
        # sales1 and sales2 are also in the fixture repo's slot-mapping file, which wins over the secrets (as it should: it is the one the Email Accounts page keeps current)
        assert [a for a, _ in account_notes(at)] == ["fixture-sales1@example.com", "fixture-sales2@example.com", "sales3@kelson.agency"]


def test_a_picked_account_shows_its_health_and_an_unpicked_one_says_it_is_not_used():
    health = [{"AccountName": "sales1", "Status": "Connected", "Detail": "", "CheckedAt": "x"}, {"AccountName": "sales2", "Status": "Disconnected", "Detail": "Login failed", "CheckedAt": "x"}]
    with settings_page(sending={"rotation_accounts": ["sales1", "sales2"]}, health=health) as (at, _):
        assert account_notes(at) == [("fixture-sales1@example.com", "Healthy"), ("fixture-sales2@example.com", "Disconnected — Login failed"), ("sales3@kelson.agency", "Not used in this campaign")]


def test_with_no_health_check_yet_a_picked_account_says_so_instead_of_pretending():
    with settings_page(sending={"rotation_accounts": ["sales1"]}) as (at, _):
        assert account_notes(at)[0] == ("fixture-sales1@example.com", "Not checked yet")


def test_todays_sends_per_account_come_from_the_send_log_and_only_todays_successful_ones_count():
    log = [sent("sales1", n=i) for i in range(3)] + [sent("sales1", status="failed", n=9), sent("sales1", days_ago=1, n=8)] + [sent("sales2", n=i) for i in range(2)]
    with settings_page(send_log=log, sending={"rotation_accounts": ["sales1", "sales2"]}) as (at, _):
        assert re.findall(r'se-usage-text">(.*?)</div>', markup(at)) == ["3 sent today", "2 sent today"]


def test_with_a_per_account_limit_the_usage_is_a_bar_against_it():
    log = [sent("sales1", n=i) for i in range(3)]
    with settings_page(send_log=log, sending={"rotation_accounts": ["sales1"], "per_account_daily_limit": 10}) as (at, _):
        text = markup(at)
        assert '<div class="se-usage-text">3 sent today · limit 10</div>' in text and 'style="width:30%"' in text


def test_select_all_switches_every_account_on_and_the_count_follows():
    with settings_page(sending={"rotation_accounts": ["sales1"]}) as (at, _):
        assert select_all_button(at).label == "Select all · 1 of 3 on"
        select_all_button(at).click()
        at.run(timeout=30)
        assert toggles(at) == {"sales1": True, "sales2": True, "sales3": True} and select_all_button(at).label == "Select all · 3 of 3 on"


def test_switching_an_account_on_updates_the_count_and_the_sentence_about_what_will_happen():
    with settings_page(sending={"rotation_accounts": ["sales1"]}) as (at, _):
        assert "All emails go out from fixture-sales1@example.com (sales1)." in markup(at)
        set_toggle(at, "sales3", True)
        assert select_all_button(at).label == "Select all · 2 of 3 on"
        assert "Emails go out from 2 accounts, taking turns: fixture-sales1@example.com (sales1), sales3@kelson.agency (sales3)." in markup(at)
        set_toggle(at, "sales1", False)
        assert "All emails go out from sales3@kelson.agency (sales3)." in markup(at)


@pytest.mark.parametrize("sending, sentence", [
    ({"rotation_accounts": None, "sender_rotation": True}, "No accounts picked, so every configured account takes turns."),
    ({"rotation_accounts": None, "sender_rotation": False}, "No accounts picked and rotation is off, so the default sender, fixture-sales1@example.com (sales1), is used."),
])
def test_with_nothing_picked_the_page_says_what_the_engine_will_do(sending, sentence):
    with settings_page(sending=sending, cfg={"default_sender_account": "sales1"}) as (at, _):
        assert sentence in markup(at)


def test_the_rotation_switch_starts_as_saved():
    for saved in (True, False):
        st.cache_data.clear()
        with settings_page(sending={"sender_rotation": saved}) as (at, _):
            assert rotation_toggle(at).value is saved


def test_searching_shows_only_matching_accounts_and_says_when_none_match():
    with settings_page(accounts=MANY) as (at, _):
        next(t for t in at.text_input if t.key == f"se_search_{FIXTURE_CAMPAIGN}").set_value("sales2")
        at.run(timeout=30)
        assert list(toggles(at)) == ["sales2"]
        next(t for t in at.text_input if t.key == f"se_search_{FIXTURE_CAMPAIGN}").set_value("zzz")
        at.run(timeout=30)
        assert toggles(at) == {} and any("No account matches that search." in c.value for c in at.caption)


def test_an_account_hidden_by_a_search_keeps_its_switch_and_is_still_saved():
    """Streamlit forgets a widget that is not drawn, so the choice lives in its own store: hiding a row must never un-pick it."""
    live = {}
    with settings_page(live=live, accounts=MANY) as (at, captured):
        set_toggle(at, "sales1", True)
        next(t for t in at.text_input if t.key == f"se_search_{FIXTURE_CAMPAIGN}").set_value("sales3")
        at.run(timeout=30)
        assert list(toggles(at)) == ["sales3"]                                                     # sales1 is hidden now
        set_toggle(at, "sales3", True)
        save_button(at).click()
        at.run(timeout=30)
        assert committed(captured)["sending"]["rotation_accounts"] == ["sales1", "sales3"]        # the hidden one was kept and saved
        next(t for t in at.text_input if t.key == f"se_search_{FIXTURE_CAMPAIGN}").set_value("")
        at.run(timeout=30)
        assert {k: v for k, v in toggles(at).items() if k in ("sales1", "sales2", "sales3")} == {"sales1": True, "sales2": False, "sales3": True}


def test_the_search_box_is_shown_only_when_there_are_four_or_more_accounts():
    with settings_page() as (at, _):                                                             # sales1, sales2 (fixture) and sales3: three accounts
        assert not any((t.key or "").startswith("se_search_") for t in at.text_input)
    st.cache_data.clear()
    with settings_page(accounts=MANY) as (at, _):
        assert any((t.key or "").startswith("se_search_") for t in at.text_input)


def test_select_all_is_not_offered_when_there_is_only_one_account():
    with settings_page(accounts={"only": "only@kelson.agency"}, no_slots=True) as (at, _):
        assert list(toggles(at)) == ["only"] and not any((b.key or "").startswith("se_selectall_button_") for b in at.button)


def test_with_no_accounts_configured_it_says_how_to_add_one_and_offers_no_toggles_or_search():
    with settings_page(accounts={}, no_slots=True) as (at, _):
        assert list(at.exception) == [] and toggles(at) == {} and any("No accounts configured yet" in i.value for i in at.info)
        assert not any((t.key or "").startswith("se_search_") for t in at.text_input) and rotation_toggle(at) is not None


# ================================================================ sending limits

def test_the_limits_start_as_saved_and_the_per_account_number_is_off_until_its_switch_is_on():
    with settings_page(sending={"daily_limit": 120, "per_account_daily_limit": None}) as (at, _):
        assert daily_input(at).value == 120 and daily_input(at).label == "Daily limit (across all accounts)" and per_toggle(at).value is False and per_input(at).disabled is True
    st.cache_data.clear()
    with settings_page(sending={"daily_limit": 120, "per_account_daily_limit": 40}) as (at, _):
        assert per_toggle(at).value is True and per_input(at).value == 40 and per_input(at).disabled is False


@pytest.mark.parametrize("daily, per, kind, text", [
    (80, 40, "ok", "Per-account limits add up to 80 (40 + 40) — the same as the campaign limit, so no account is starved."),
    (100, 40, "warn", "Per-account limits add up to 80 (40 + 40) — less than the campaign limit of 100, so the campaign will stop at 80 a day."),
    (60, 40, "info", "Per-account limits add up to 80 (40 + 40) — more than the campaign limit of 60, so the campaign limit is reached first and some accounts may send less than their own limit."),
])
def test_the_live_check_compares_the_per_account_limits_with_the_campaign_limit(daily, per, kind, text):
    with settings_page(sending={"rotation_accounts": ["sales1", "sales2"], "daily_limit": daily, "per_account_daily_limit": per}) as (at, _):
        assert f'<div class="se-line se-line-{kind}">{text}</div>' in markup(at)


def test_the_live_check_follows_what_is_being_edited_and_is_absent_without_a_per_account_limit():
    with settings_page(sending={"rotation_accounts": ["sales1", "sales2"], "daily_limit": 80, "per_account_daily_limit": 40}) as (at, _):
        daily_input(at).set_value(100)
        at.run(timeout=30)
        assert "se-line-warn" in markup(at)
        per_toggle(at).set_value(False)
        at.run(timeout=30)
        assert "Per-account limits add up" not in markup(at)


def test_saving_writes_the_limits_the_rotation_switch_and_the_picked_accounts():
    with settings_page(live={}) as (at, captured):
        daily_input(at).set_value(250)
        rotation_toggle(at).set_value(True)
        per_toggle(at).set_value(True)
        at.run(timeout=30)
        per_input(at).set_value(30)
        set_toggle(at, "sales1", True)
        set_toggle(at, "sales3", True)
        save_button(at).click()
        at.run(timeout=30)
        assert list(at.exception) == [] and list(at.error) == [] and len(captured["commits"]) == 1
        commit = captured["commits"][0]
        sending = yaml.safe_load(commit["content"])["sending"]
        assert commit["path"] == OVERRIDE_PATH and "Update settings for " in commit["message"] and FIXTURE_CAMPAIGN in commit["message"]
        assert (sending["daily_limit"], sending["per_account_daily_limit"], sending["sender_rotation"], sending["rotation_accounts"]) == (250, 30, True, ["sales1", "sales3"])
        assert any("Settings saved." in s.value for s in at.success)


def test_saving_keeps_everything_else_in_the_live_file_even_what_this_page_has_not_seen_yet():
    """The page's own copy has no schedule and no waits; the live file has both (saved a moment ago on another tab). Saving settings must keep them."""
    live = {OVERRIDE_PATH: override_yaml(status="paused", schedule=SCHEDULE, stage_wait_days={"followup1": 6}, sending={"daily_limit": 50, "delay_min_minutes": 2})}
    with settings_page(live=live, cfg={"schedule": None}) as (at, captured):
        daily_input(at).set_value(300)
        at.run(timeout=30)
        save_button(at).click()
        at.run(timeout=30)
        saved = committed(captured)
        assert saved["status"] == "paused" and saved["schedule"] == SCHEDULE and saved["stage_wait_days"] == {"followup1": 6}
        assert saved["sending"]["daily_limit"] == 300 and saved["sending"]["delay_min_minutes"] == 2


def test_the_saved_list_keeps_its_order_and_a_newly_picked_account_goes_on_the_end():
    live = {OVERRIDE_PATH: override_yaml(sending={"rotation_accounts": ["sales3", "sales1"]})}
    with settings_page(live=live, sending={"rotation_accounts": ["sales3", "sales1"]}) as (at, captured):
        set_toggle(at, "sales2", True)
        save_button(at).click()
        at.run(timeout=30)
        assert committed(captured)["sending"]["rotation_accounts"] == ["sales3", "sales1", "sales2"]


def test_switching_every_account_off_saves_no_list_at_all_because_the_engine_rejects_an_empty_one():
    live = {OVERRIDE_PATH: override_yaml(sending={"rotation_accounts": ["sales1"]})}
    with settings_page(live=live, sending={"rotation_accounts": ["sales1"]}) as (at, captured):
        set_toggle(at, "sales1", False)
        save_button(at).click()
        at.run(timeout=30)
        assert "rotation_accounts" not in committed(captured)["sending"]


def test_switching_the_per_account_limit_off_removes_it_from_the_file():
    live = {OVERRIDE_PATH: override_yaml(sending={"per_account_daily_limit": 40})}
    with settings_page(live=live, sending={"per_account_daily_limit": 40}) as (at, captured):
        per_toggle(at).set_value(False)
        at.run(timeout=30)
        save_button(at).click()
        at.run(timeout=30)
        assert "per_account_daily_limit" not in committed(captured)["sending"]


def test_select_all_then_save_persists_every_account():
    with settings_page(live={}) as (at, captured):
        select_all_button(at).click()
        at.run(timeout=30)
        save_button(at).click()
        at.run(timeout=30)
        assert set(committed(captured)["sending"]["rotation_accounts"]) == {"sales1", "sales2", "sales3"}


def test_a_failed_save_says_why_and_keeps_the_edits():
    from github_client import GitHubActionsError
    with settings_page(live={}, commit_error=GitHubActionsError("boom")) as (at, captured):
        daily_input(at).set_value(222)
        at.run(timeout=30)
        save_button(at).click()
        at.run(timeout=30)
        assert any("Save failed: boom" in e.value for e in at.error) and not at.success and captured.get("commits") is None and daily_input(at).value == 222


def test_if_the_final_check_refuses_nothing_is_committed_and_the_reason_is_shown():
    with settings_page(live={}) as (at, captured):
        with patch("settings_view.validate_settings", lambda daily, per: ["Daily limit must be a positive number."]):
            save_button(at).click()
            at.run(timeout=30)
        assert any("Daily limit must be a positive number." in e.value for e in at.error) and captured.get("commits") is None


# ================================================================ campaign status

@pytest.mark.parametrize("status, label, pill", [("running", "⏸ Pause", "● Running"), ("attention", "⏸ Pause", "● Attention needed"), ("paused", "▶ Resume", "● Paused"), ("draft", "🚀 Launch", "● Draft")])
def test_the_status_card_shows_the_status_and_the_one_action_that_fits_it(status, label, pill):
    with settings_page(status=status) as (at, _):
        assert pill in markup(at) and [b.label for b in at.button if (b.key or "").startswith("se_") and b.label in ("⏸ Pause", "▶ Resume", "🚀 Launch")] == [label]


def test_a_completed_campaign_has_no_status_action():
    with settings_page(status="completed") as (at, _):
        assert not any((b.key or "").startswith("se_") and b.label in ("⏸ Pause", "▶ Resume", "🚀 Launch") for b in at.button)


def test_pausing_from_here_sets_the_status_and_keeps_the_live_settings_saved_a_moment_ago():
    live = {OVERRIDE_PATH: override_yaml(sending={"daily_limit": 77}, schedule=SCHEDULE)}
    with settings_page(status="running", live=live) as (at, captured):
        next(b for b in at.button if b.key == "se_pause_button").click()
        at.run(timeout=30)
        saved = committed(captured)
        assert saved["status"] == "paused" and saved["sending"] == {"daily_limit": 77} and saved["schedule"] == SCHEDULE
        assert "Set status=paused" in captured["commits"][0]["message"] and any("Paused." in s.value for s in at.success)


def test_resuming_from_here_sets_the_campaign_active_again():
    with settings_page(status="paused", live={OVERRIDE_PATH: override_yaml(status="paused")}) as (at, captured):
        next(b for b in at.button if b.key == "se_resume_button").click()
        at.run(timeout=30)
        assert committed(captured)["status"] == "active" and any("Resumed." in s.value for s in at.success)


def test_launching_from_here_asks_for_the_same_confirmation_the_header_does_and_changes_nothing_yet():
    with settings_page(status="draft") as (at, captured):
        next(b for b in at.button if b.key == "se_launch_button").click()
        at.run(timeout=30)
        assert any("Launch this campaign?" in w.value for w in at.warning) and captured.get("commits") is None


# ================================================================ readiness

def test_a_ready_campaign_has_every_check_ticked():
    with settings_page(cfg={"schedule": SCHEDULE}) as (at, _):
        assert readiness(at) == [("✅", "Leads imported (1)"), ("✅", "Intro template has all placeholders"), ("✅", "At least one sender account"), ("✅", "Schedule has send days and a window")]


def test_a_placeholder_that_matches_no_column_is_named():
    with settings_page(cfg={"schedule": SCHEDULE}, intro_text="Subject: Hi\n\nHi {{FirstName}} {{Typo}}") as (at, _):
        assert ("❌", "Intro template uses {{Typo}}, which matches no column") in readiness(at)


def test_if_the_templates_cannot_be_read_it_says_so_and_the_tab_still_works():
    with settings_page(cfg={"schedule": SCHEDULE}, templates_fail=True) as (at, _):
        assert list(at.exception) == [] and ("ℹ️", "Couldn't check the intro template just now") in readiness(at)


def test_with_no_leads_it_says_so_and_does_not_guess_about_placeholders():
    with settings_page(leads=[], cfg={"schedule": SCHEDULE}) as (at, _):
        assert readiness(at)[:2] == [("❌", "No leads with an email address"), ("ℹ️", "Add leads to check the intro template's placeholders")]


@pytest.mark.parametrize("schedule, line", [(None, ("ℹ️", "No schedule is set, so this campaign sends anytime")), ({**SCHEDULE, "send_days": []}, ("❌", "Schedule needs send days and a valid window"))])
def test_the_schedule_line_follows_the_campaigns_schedule(schedule, line):
    with settings_page(cfg={"schedule": schedule}) as (at, _):
        assert line in readiness(at)


def test_a_disconnected_account_that_is_in_use_is_flagged_but_one_that_is_not_in_use_is_not():
    health = [{"AccountName": "sales1", "Status": "Disconnected", "Detail": "Login failed", "CheckedAt": "x"}, {"AccountName": "sales2", "Status": "Disconnected", "Detail": "Timeout", "CheckedAt": "x"}]
    with settings_page(cfg={"schedule": SCHEDULE}, sending={"rotation_accounts": ["sales1"]}, health=health) as (at, _):
        assert ("⚠️", "sales1: disconnected — Login failed") in readiness(at) and not any("sales2" in text for _, text in readiness(at))


def test_the_checklist_follows_the_accounts_being_picked_right_now():
    health = [{"AccountName": "sales2", "Status": "Disconnected", "Detail": "Timeout", "CheckedAt": "x"}]
    with settings_page(cfg={"schedule": SCHEDULE}, sending={"rotation_accounts": ["sales1"]}, health=health) as (at, _):
        assert not any("sales2" in text for _, text in readiness(at))
        set_toggle(at, "sales2", True)
        assert ("⚠️", "sales2: disconnected — Timeout") in readiness(at)


# ================================================================ danger zone

def test_the_danger_zone_has_its_two_actions_and_its_warning_line():
    with settings_page() as (at, _):
        assert ">Danger zone<" in markup(at) and "These can't be undone from here." in html.unescape(markup(at))
        assert next(b for b in at.button if b.key == "temp_remove_campaign_button").label == "Archive campaign"
        assert any(p.proto.popover.label == "Delete campaign…" for p in at.get("popover"))


def test_archiving_hides_the_campaign_keeps_the_live_settings_and_returns_to_the_list():
    live = {OVERRIDE_PATH: override_yaml(sending={"daily_limit": 77}, status="active")}
    with settings_page(live=live) as (at, captured):
        next(b for b in at.button if b.key == "temp_remove_campaign_button").click()
        at.run(timeout=30)
        saved = committed(captured)
        assert saved["status"] == "deleted" and saved["sending"] == {"daily_limit": 77} and "Temporarily remove" in captured["commits"][0]["message"]
        assert saved["previous_status"] == "active"                                              # remembered, so Restore brings it back exactly as it was
        assert at.session_state["selected_campaign"] is None


def test_deleting_needs_the_exact_campaign_name_typed_and_then_removes_its_files():
    with settings_page() as (at, captured):
        confirm = lambda: next(b for b in at.button if b.key == "delete_campaign_button")        # noqa: E731
        assert confirm().disabled is True
        for wrong in ("", "nope", FIXTURE_CAMPAIGN[:-1]):
            next(t for t in at.text_input if t.key == "delete_campaign_confirm_text").set_value(wrong)
            at.run(timeout=30)
            assert confirm().disabled is True, wrong
        next(t for t in at.text_input if t.key == "delete_campaign_confirm_text").set_value(FIXTURE_CAMPAIGN)
        at.run(timeout=30)
        assert confirm().disabled is False and captured.get("deleted") is None
        confirm().click()
        at.run(timeout=30)
        assert captured["deleted"] and all(p.startswith(("templates/", "config/campaigns/")) for p in captured["deleted"]) and at.session_state["selected_campaign"] is None


def test_a_failed_delete_says_why_and_the_campaign_stays_selected():
    from github_client import GitHubActionsError
    with settings_page(delete_error=GitHubActionsError("boom")) as (at, captured):
        next(t for t in at.text_input if t.key == "delete_campaign_confirm_text").set_value(FIXTURE_CAMPAIGN)
        at.run(timeout=30)
        next(b for b in at.button if b.key == "delete_campaign_button").click()
        at.run(timeout=30)
        assert any("Failed to delete campaign: boom" in e.value for e in at.error) and at.session_state["selected_campaign"] == FIXTURE_CAMPAIGN


# ================================================================ the rest of the tab

def test_the_tools_the_design_does_not_show_are_still_there_as_cards():
    with settings_page() as (at, _):
        labels = [e.label for e in at.expander]
        assert any("Maintenance · rarely needed repair tools" in label for label in labels)
        assert labels.count("How it works") == 2                                                    # one under Asana, one under the Creator Tracker
        assert 'se-title">Asana sync<' in markup(at) and 'se-title">Creator Tracker sync<' in markup(at)


def test_looking_at_the_settings_tab_never_writes_anything():
    with settings_page(status="running", live={}) as (at, captured):
        at.run(timeout=30)
        assert list(at.exception) == [] and captured.get("commits") is None and captured.get("deleted") is None and captured.get("dispatches") is None


def test_a_failing_health_lookup_never_breaks_the_tab():
    with settings_page(sending={"rotation_accounts": ["sales1"]}, health_fail=True) as (at, _):
        assert list(at.exception) == [] and account_notes(at)[0] == ("fixture-sales1@example.com", "Not checked yet")


def test_asana_and_tracker_saves_now_read_the_live_file_too():
    live = {OVERRIDE_PATH: override_yaml(schedule=SCHEDULE, sending={"daily_limit": 55})}
    with settings_page(live=live, cfg={"schedule": None}) as (at, captured):
        next(c for c in at.toggle if c.key == "tracker_sync_enabled").set_value(True)
        at.run(timeout=30)
        next(b for b in at.button if b.key == "tracker_sync_save").click()
        at.run(timeout=30)
        saved = committed(captured)
        assert saved["tracker_sync"] == {"enabled": True} and saved["schedule"] == SCHEDULE and saved["sending"] == {"daily_limit": 55}


def test_restoring_a_campaign_reads_the_live_file_so_nothing_saved_a_moment_ago_is_undone(tmp_path):
    """The page's own copy only knows the campaign was archived. The live file also has a limit and a schedule saved since; restoring must keep them."""
    (tmp_path / f"{FIXTURE_CAMPAIGN}.yaml").write_text("status: deleted\nprevious_status: paused\n")
    live = {OVERRIDE_PATH: override_yaml(status="deleted", previous_status="paused", sending={"daily_limit": 77}, schedule=SCHEDULE)}
    with settings_page(selected=None, campaigns_dir=str(tmp_path), live=live) as (at, captured):
        next(b for b in at.button if b.key == f"restore_{FIXTURE_CAMPAIGN}").click()
        at.run(timeout=30)
        saved = committed(captured)
        assert list(at.exception) == [] and saved["status"] == "paused" and "previous_status" not in saved
        assert saved["sending"] == {"daily_limit": 77} and saved["schedule"] == SCHEDULE


def type_project(at, value):
    """Types into the Asana project-name box the way a person must: if it is locked, press the padlock first, then type."""
    if next(t for t in at.text_input if t.key == "asana_sync_project_name").disabled:
        next(b for b in at.button if b.key == "asana_name_lock").click()
        at.run(timeout=30)
    next(t for t in at.text_input if t.key == "asana_sync_project_name").set_value(value)


def test_the_asana_settings_save_reads_the_live_file_too():
    live = {OVERRIDE_PATH: override_yaml(schedule=SCHEDULE, sending={"daily_limit": 55})}
    with settings_page(live=live, cfg={"schedule": None}) as (at, captured):
        next(c for c in at.toggle if c.key == "asana_sync_enabled").set_value(True)
        at.run(timeout=30)                                                                      # the project-name box appears once the switch is on
        type_project(at, "Creator Outreach")
        at.run(timeout=30)
        next(b for b in at.button if b.key == "asana_sync_save").click()
        at.run(timeout=30)
        saved = committed(captured)
        assert saved["asana"] == {"enabled": True, "project_name": "Creator Outreach"} and saved["schedule"] == SCHEDULE and saved["sending"] == {"daily_limit": 55}


# ================================================================ the integration and maintenance cards

ASANA_ON = {"asana": {"enabled": True, "project_name": "Creator Outreach"}}


def btn(at, key):
    return next(b for b in at.button if b.key == key)


def tog(at, key):
    return next(t for t in at.toggle if t.key == key)


def chips(at):
    return re.findall(r'<span class="se-chip se-chip-(\w+)">(.*?)</span>', markup(at))


def test_the_cards_show_what_each_integration_is_and_whether_it_is_on():
    with settings_page(cfg={**ASANA_ON, "tracker_sync": {"enabled": False}}) as (at, _):
        text = markup(at)
        assert ">Integrations<" in text and ">Maintenance<" in text and 'se-title">Asana sync<' in text and 'se-title">Creator Tracker sync<' in text
        assert ("on", "● On") in chips(at) and ("off", "○ Off") in chips(at)
        assert "One Asana task per lead — never a duplicate." in html.unescape(text)


def test_a_switched_off_asana_asks_for_nothing_and_offers_no_save_or_sync():
    with settings_page(cfg={"asana": {"enabled": False, "project_name": ""}}) as (at, _):
        assert tog(at, "asana_sync_enabled").value is False and not any(t.key == "asana_sync_project_name" for t in at.text_input)
        assert btn(at, "asana_sync_save").disabled is True and btn(at, "asana_sync_now").disabled is True
        assert "Turn this on and save to start syncing." in markup(at)


def test_switching_asana_on_shows_the_project_box_flags_the_unsaved_change_and_keeps_sync_off_until_saved():
    with settings_page(cfg={"asana": {"enabled": False, "project_name": ""}}) as (at, captured):
        tog(at, "asana_sync_enabled").set_value(True)
        at.run(timeout=30)
        assert any(t.key == "asana_sync_project_name" for t in at.text_input) and ("unsaved", "● Unsaved change") in chips(at)
        assert btn(at, "asana_sync_save").disabled is False and btn(at, "asana_sync_now").disabled is True and captured.get("commits") is None


def test_a_saved_asana_has_save_off_sync_on_and_flips_the_moment_the_project_is_edited():
    with settings_page(cfg=ASANA_ON) as (at, _):
        assert btn(at, "asana_sync_save").disabled is True and btn(at, "asana_sync_now").disabled is False and ("unsaved", "● Unsaved change") not in chips(at)
        type_project(at, "Another project")
        at.run(timeout=30)
        assert btn(at, "asana_sync_save").disabled is False and btn(at, "asana_sync_now").disabled is True and ("unsaved", "● Unsaved change") in chips(at)
        type_project(at, "Creator Outreach")
        at.run(timeout=30)
        assert btn(at, "asana_sync_save").disabled is True and btn(at, "asana_sync_now").disabled is False


def test_a_project_name_that_is_only_spaces_counts_as_unchanged_and_blank_is_refused_on_save():
    with settings_page(cfg=ASANA_ON, live={}) as (at, captured):
        type_project(at, "  Creator Outreach  ")
        at.run(timeout=30)
        assert btn(at, "asana_sync_save").disabled is True                                            # trimmed, so the same as what is saved
        type_project(at, "   ")
        at.run(timeout=30)
        btn(at, "asana_sync_save").click()
        at.run(timeout=30)
        assert any("Enter the Asana project name, or switch Enabled off." in e.value for e in at.error) and captured.get("commits") is None


def test_saving_asana_commits_it_keeps_the_other_live_settings_and_then_the_page_treats_it_as_saved():
    live = {OVERRIDE_PATH: override_yaml(schedule=SCHEDULE, sending={"daily_limit": 55})}
    with settings_page(live=live, cfg={"schedule": None, "asana": {"enabled": False, "project_name": ""}}) as (at, captured):
        tog(at, "asana_sync_enabled").set_value(True)
        at.run(timeout=30)
        type_project(at, "Creator Outreach")
        at.run(timeout=30)
        btn(at, "asana_sync_save").click()
        at.run(timeout=30)
        saved = committed(captured)
        assert saved["asana"] == {"enabled": True, "project_name": "Creator Outreach"} and saved["schedule"] == SCHEDULE and saved["sending"] == {"daily_limit": 55}
        assert "Update Asana sync settings for " in captured["commits"][0]["message"] and any("Asana settings saved." in s.value for s in at.success)
        at.run(timeout=30)
        assert btn(at, "asana_sync_save").disabled is True and btn(at, "asana_sync_now").disabled is False and ("on", "● On") in chips(at)       # saved, although the page's own copy is old


def test_switching_asana_off_and_saving_keeps_the_project_name_for_next_time():
    with settings_page(cfg=ASANA_ON, live={}) as (at, captured):
        tog(at, "asana_sync_enabled").set_value(False)
        at.run(timeout=30)
        assert btn(at, "asana_sync_save").disabled is False and ("on", "● On") in chips(at)       # still on until it is saved
        btn(at, "asana_sync_save").click()
        at.run(timeout=30)
        assert committed(captured)["asana"] == {"enabled": False, "project_name": "Creator Outreach"}


def test_sync_now_starts_the_sync_workflow_for_this_campaign_and_says_so():
    with settings_page(cfg=ASANA_ON) as (at, captured):
        btn(at, "asana_sync_now").click()
        at.run(timeout=30)
        assert captured["dispatches"] == [("sync_asana.yml", {"campaign": FIXTURE_CAMPAIGN})] and any("Asana sync triggered" in s.value for s in at.success)


def test_a_failed_asana_save_or_sync_says_why():
    from github_client import GitHubActionsError
    with settings_page(cfg=ASANA_ON, live={}, commit_error=GitHubActionsError("boom")) as (at, captured):
        type_project(at, "Another project")
        at.run(timeout=30)
        btn(at, "asana_sync_save").click()
        at.run(timeout=30)
        assert any("Failed to save: boom" in e.value for e in at.error) and not at.success and captured.get("commits") is None and btn(at, "asana_sync_save").disabled is False


def test_the_tracker_card_follows_the_same_rules_without_a_project_name():
    with settings_page(cfg={"tracker_sync": {"enabled": False}}, live={}) as (at, captured):
        assert btn(at, "tracker_sync_save").disabled is True and btn(at, "tracker_sync_now").disabled is True and "Turn this on and save to start syncing." in markup(at)
        tog(at, "tracker_sync_enabled").set_value(True)
        at.run(timeout=30)
        assert btn(at, "tracker_sync_save").disabled is False and btn(at, "tracker_sync_now").disabled is True and ("unsaved", "● Unsaved change") in chips(at)
        btn(at, "tracker_sync_save").click()
        at.run(timeout=30)
        assert committed(captured)["tracker_sync"] == {"enabled": True} and any("Creator Tracker settings saved." in s.value for s in at.success)
        at.run(timeout=30)
        assert btn(at, "tracker_sync_now").disabled is False
        btn(at, "tracker_sync_now").click()
        at.run(timeout=30)
        assert captured["dispatches"] == [("sync_asana.yml", {"campaign": FIXTURE_CAMPAIGN})]


def test_asana_and_the_tracker_are_saved_separately_so_one_never_disturbs_the_other():
    live = {OVERRIDE_PATH: override_yaml(asana={"enabled": True, "project_name": "Creator Outreach"})}
    with settings_page(cfg={**ASANA_ON, "tracker_sync": {"enabled": False}}, live=live) as (at, captured):
        tog(at, "tracker_sync_enabled").set_value(True)
        at.run(timeout=30)
        btn(at, "tracker_sync_save").click()
        at.run(timeout=30)
        saved = committed(captured)
        assert saved["asana"] == {"enabled": True, "project_name": "Creator Outreach"} and saved["tracker_sync"] == {"enabled": True}


def test_the_maintenance_card_has_the_backfill_with_a_dry_run_switch_that_starts_on():
    with settings_page() as (at, _):
        assert ">Maintenance<" in markup(at) and any("Maintenance · rarely needed repair tools" in e.label for e in at.expander)
        assert tog(at, "campaigns_backfill_dry_run").value is True and btn(at, "campaigns_run_backfill").label == "▶ Run backfill"
        assert "Backfill ThreadSubject" in markup(at) and "safe to run more than once" in html.unescape(markup(at))


@pytest.mark.parametrize("dry_run", [True, False])
def test_running_the_backfill_starts_its_workflow_with_the_dry_run_choice(dry_run):
    from send_logic import build_backfill_thread_subject_inputs
    with settings_page() as (at, captured):
        tog(at, "campaigns_backfill_dry_run").set_value(dry_run)
        at.run(timeout=30)
        btn(at, "campaigns_run_backfill").click()
        at.run(timeout=30)
        assert captured["dispatches"][0][1] == build_backfill_thread_subject_inputs(FIXTURE_CAMPAIGN, dry_run=dry_run)
        assert any("Triggered backfill" in s.value and (" (dry run)" in s.value) is dry_run for s in at.success)
        assert btn(at, "campaigns_backfill_refresh_status") is not None                           # the last run can be followed


def test_every_card_name_in_the_page_and_the_settings_view_is_unique_and_none_contains_another():
    root = os.path.join(os.path.dirname(__file__), "..")
    keys = []
    for name in ("settings_view.py", os.path.join("pages", "campaigns.py")):
        keys += re.findall(r'key=f?"(se_card_[^"]+)"', open(os.path.join(root, name), encoding="utf-8").read())
    assert sorted(keys) == ["se_card_accounts", "se_card_asana", "se_card_danger", "se_card_footer", "se_card_limits", "se_card_maint", "se_card_readiness", "se_card_status", "se_card_tracker", "se_card_tracking"]


def test_just_after_saving_the_page_shows_it_as_saved_even_while_its_own_copy_of_the_settings_is_still_old():
    """The page's copy of the settings can lag behind GitHub for a while. Without a note of what was just saved, 'Unsaved change' would stay on and Sync would stay off."""
    with settings_page(cfg={"asana": {"enabled": False, "project_name": ""}, "tracker_sync": {"enabled": False}}, live=None) as (at, captured):       # GitHub cannot be re-read here
        tog(at, "asana_sync_enabled").set_value(True)
        tog(at, "tracker_sync_enabled").set_value(True)
        at.run(timeout=30)
        type_project(at, "Creator Outreach")
        at.run(timeout=30)
        btn(at, "asana_sync_save").click()
        at.run(timeout=30)
        at.run(timeout=30)
        assert btn(at, "asana_sync_save").disabled is True and btn(at, "asana_sync_now").disabled is False
        assert btn(at, "tracker_sync_save").disabled is False and btn(at, "tracker_sync_now").disabled is True        # the tracker was not saved: it is still a draft
        btn(at, "tracker_sync_save").click()
        at.run(timeout=30)
        at.run(timeout=30)
        assert btn(at, "tracker_sync_save").disabled is True and btn(at, "tracker_sync_now").disabled is False and len(captured["commits"]) == 2


def test_a_saved_tracker_cannot_sync_while_its_switch_has_an_unsaved_change_and_can_again_once_put_back():
    with settings_page(cfg={"tracker_sync": {"enabled": True}}) as (at, _):
        assert btn(at, "tracker_sync_now").disabled is False and btn(at, "tracker_sync_save").disabled is True and ("on", "● On") in chips(at)
        tog(at, "tracker_sync_enabled").set_value(False)
        at.run(timeout=30)
        assert btn(at, "tracker_sync_now").disabled is True and btn(at, "tracker_sync_save").disabled is False and ("unsaved", "● Unsaved change") in chips(at)
        tog(at, "tracker_sync_enabled").set_value(True)
        at.run(timeout=30)
        assert btn(at, "tracker_sync_now").disabled is False and btn(at, "tracker_sync_save").disabled is True


def test_the_cards_come_in_the_designs_order_integrations_first_and_maintenance_last():
    with settings_page() as (at, _):
        text = markup(at)
        assert text.index(">Integrations<") < text.index('se-title">Asana sync<') < text.index('se-title">Creator Tracker sync<') < text.index(">Maintenance<")


# ================================================================ the lock on the Asana project name

LOCK_KEY = f"asana_name_locked_{FIXTURE_CAMPAIGN}"


def project_box(at):
    return next(t for t in at.text_input if t.key == "asana_sync_project_name")


def test_with_a_saved_project_name_the_box_starts_locked_and_says_so():
    with settings_page(cfg=ASANA_ON) as (at, _):
        assert project_box(at).disabled is True and project_box(at).value == "Creator Outreach"
        assert "Locked, so it cannot be changed by accident" in markup(at) and btn(at, "asana_name_lock").help == "Locked. Press to edit the project name."


def test_with_no_saved_project_name_there_is_nothing_to_protect_so_the_box_starts_open():
    with settings_page(cfg={"asana": {"enabled": False, "project_name": ""}}) as (at, _):
        tog(at, "asana_sync_enabled").set_value(True)
        at.run(timeout=30)
        assert project_box(at).disabled is False and "Unlocked. Press the lock again" in markup(at) and btn(at, "asana_name_lock").help == "Unlocked. Press to lock it again."


def test_the_padlock_unlocks_and_locks_again_and_keeps_what_was_typed():
    with settings_page(cfg=ASANA_ON) as (at, _):
        btn(at, "asana_name_lock").click()
        at.run(timeout=30)
        assert project_box(at).disabled is False and "Unlocked. Press the lock again" in markup(at)
        project_box(at).set_value("Another project")
        at.run(timeout=30)
        btn(at, "asana_name_lock").click()
        at.run(timeout=30)
        assert project_box(at).disabled is True and project_box(at).value == "Another project"          # locking does not throw the edit away
        assert ("unsaved", "● Unsaved change") in chips(at) and btn(at, "asana_sync_save").disabled is False and btn(at, "asana_sync_now").disabled is True


def test_a_locked_box_cannot_be_typed_into():
    with settings_page(cfg=ASANA_ON) as (at, _):
        with pytest.raises(Exception, match="disabled"):
            project_box(at).set_value("Typed by accident")


def test_pressing_save_locks_the_name_again():
    with settings_page(cfg=ASANA_ON, live={}) as (at, captured):
        type_project(at, "Another project")
        at.run(timeout=30)
        assert project_box(at).disabled is False
        btn(at, "asana_sync_save").click()
        at.run(timeout=30)
        assert committed(captured)["asana"]["project_name"] == "Another project"
        assert at.session_state[LOCK_KEY] is True and project_box(at).disabled is True and "Locked, so it cannot be changed" in markup(at)


def test_saving_a_blank_name_is_refused_and_leaves_the_box_open_to_fill_in():
    with settings_page(cfg=ASANA_ON, live={}) as (at, captured):
        type_project(at, "   ")
        at.run(timeout=30)
        btn(at, "asana_sync_save").click()
        at.run(timeout=30)
        assert any("Enter the Asana project name" in e.value for e in at.error) and project_box(at).disabled is False and captured.get("commits") is None


def test_the_lock_guards_only_the_project_name_not_the_switch_or_the_sync():
    with settings_page(cfg=ASANA_ON) as (at, _):
        assert project_box(at).disabled is True and tog(at, "asana_sync_enabled").value is True and btn(at, "asana_sync_now").disabled is False
        tog(at, "asana_sync_enabled").set_value(False)
        at.run(timeout=30)
        assert not any(t.key == "asana_sync_project_name" for t in at.text_input)                 # the switch still works while the name is locked


def test_both_integration_cards_have_their_dividing_lines():
    with settings_page(cfg=ASANA_ON) as (at, _):
        assert markup(at).count('<div class="se-rule"></div>') == 4                                  # two in the Asana card, two in the Tracker card
