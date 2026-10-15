"""The Email Accounts page through the real page: header, health figures, one card per account, filters and search, 25 at a time, and the Edit / Update password / Remove dialogs."""
import contextlib
import html
import json
import os
import re
import sys
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
import streamlit as st
import yaml
from streamlit.testing.v1 import AppTest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from test_pages_smoke import (FakeSpreadsheet, FakeWorksheet, FIXTURE_CAMPAIGN, PAGES_DIR, _authed_session, _dashboard_secrets, _mock_github_writes, _mock_secret_writes,  # noqa: E402
                              _write_slot_mapping_fixture)
from github_client import GitHubActionsError  # noqa: E402


@pytest.fixture(autouse=True)
def _every_test_uses_the_fixture_repo(fixture_repo):
    yield


@pytest.fixture(autouse=True)
def _clear_streamlit_caches():
    st.cache_resource.clear()
    st.cache_data.clear()
    yield
    st.cache_resource.clear()
    st.cache_data.clear()


def slots(count=3, prefix="sales"):
    return "".join(f"{prefix}{i}:\n  slot: {i}\n  address: {prefix}{i}@kelson.agency\n" for i in range(1, count + 1))


def sent_row(account, n=0):
    return {"Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), "Status": "sent", "SenderAccount": account, "LeadID": str(n)}


HEALTH = [{"AccountName": "sales1", "Status": "Connected", "Detail": "", "CheckedAt": "2026-10-14 23:10:10"}, {"AccountName": "sales2", "Status": "Connected", "Detail": "", "CheckedAt": "2026-10-14 23:10:10"},
          {"AccountName": "sales3", "Status": "Disconnected", "Detail": "Login failed", "CheckedAt": "2026-10-14 23:10:10"}]


def accounts_page(tmp_path, slot_yaml=None, legacy=None, health=HEALTH, sends=None, set_secret_error=None, prime=None, no_send_tab=False):
    @contextlib.contextmanager
    def _ctx():
        _write_slot_mapping_fixture(tmp_path, slot_yaml if slot_yaml is not None else slots())
        ws = {}
        if not no_send_tab:
            ws[f"{FIXTURE_CAMPAIGN} Custom Log Sheet"] = FakeWorksheet(sends if sends is not None else [sent_row("sales1", i) for i in range(5)] + [sent_row("sales2", 9)])
        if health is not None:
            ws["Email Accounts Health"] = FakeWorksheet(health)
        secrets_captured, fake_set_secret, fake_delete_secret = _mock_secret_writes()
        commits_captured, fake_create_file = _mock_github_writes()
        dispatched = []

        def set_secret(self, name, value):
            if set_secret_error:
                raise set_secret_error
            return fake_set_secret(self, name, value)
        with patch("config.EMAIL_ACCOUNT_SLOT_MAPPING_ABS_PATH", str(tmp_path / "config" / "email_account_slots.yaml")), \
             patch("gspread.authorize", return_value=type("C", (), {"open_by_key": lambda self, k: FakeSpreadsheet(ws)})()), \
             patch("google.oauth2.service_account.Credentials.from_service_account_info", return_value=object()), \
             patch("github_client.GitHubClient.set_secret", set_secret), patch("github_client.GitHubClient.delete_secret", fake_delete_secret), \
             patch("github_client.GitHubClient.create_file", fake_create_file), \
             patch("github_client.GitHubClient.dispatch_workflow", lambda self, w, i, ref="main": dispatched.append(w) or {"id": 1}):
            at = AppTest.from_file(os.path.join(PAGES_DIR, "email_accounts.py"))
            secrets = _dashboard_secrets()
            if legacy:
                secrets["email_accounts_directory"] = dict(legacy)
            at.secrets.update(secrets)
            for key, value in _authed_session().items():
                at.session_state[key] = value
            for key, value in (prime or {}).items():
                at.session_state[key] = value
            at.run(timeout=30)
            yield at, {"secrets": secrets_captured, "commits": commits_captured, "dispatched": dispatched}
    return _ctx()


def markup(at):
    return " ".join(str(m.value) for m in at.markdown)


def addresses(at):
    return re.findall(r'class="ea-addr">(.*?)</div>', markup(at))


def pills(at):
    return next(g for g in at.get("button_group") if g.key == "ea_filter")


def btn(at, key):
    return next(b for b in at.button if b.key == key)


def test_the_header_has_the_title_three_equally_wide_buttons_and_the_security_note(tmp_path):
    with accounts_page(tmp_path) as (at, _):
        assert list(at.exception) == [] and list(at.error) == []
        text = markup(at)
        assert 'class="ea-title">Email Accounts<' in text and "Passwords are never stored, logged or shown." in text
        assert [btn(at, k).label for k in ("ea_refresh", "ea_bulk_open", "ea_add_open")] == ["🔄 Refresh", "📥 Bulk add (CSV)", "＋ Add account"]
    source = open(os.path.join(os.path.dirname(__file__), "..", "pages", "email_accounts.py"), encoding="utf-8").read()
    ratios = re.search(r"head_column, refresh_column, bulk_column, add_column = st\.columns\(\[([\d., ]+)\]", source).group(1).replace(" ", "").split(",")
    assert len(ratios) == 4 and ratios[1] == ratios[2] == ratios[3]                 # REGRESSION guard: Add account was bigger than its two neighbours in the mockup


def test_the_four_figures_add_up_from_the_accounts_the_health_checks_and_todays_sends(tmp_path):
    with accounts_page(tmp_path, legacy={"legacy_one": "legacy@kelson.agency"}) as (at, _):
        text = markup(at)
        assert '2 <span class="ea-of">of 4</span>' in text
        values = re.findall(r'ea-stat-v">(.*?)</div>', text)
        assert values[1] == "1" and values[2] == "6" and "ea-stat ea-stat-bad" in text and "automatic checks every 2 hours" in text


def test_every_account_is_a_card_in_name_order_with_its_slot_and_the_default_starred(tmp_path):
    with accounts_page(tmp_path) as (at, _):
        assert addresses(at) == ["sales1@kelson.agency", "sales2@kelson.agency", "sales3@kelson.agency"]
        text = markup(at)
        assert text.count("⭐ Default") == 1 and 'ea-slot">Slot 1<' in text and 'ea-slot">Slot 3<' in text
        assert re.findall(r"<b>(\d+)</b><span>sent today</span>", text) == ["5", "1", "0"]


def test_a_healthy_account_offers_edit_and_remove_and_a_disconnected_one_offers_update_password_instead(tmp_path):
    with accounts_page(tmp_path) as (at, _):
        assert btn(at, "ea_edit_sales1").label == "✎ Edit" and btn(at, "ea_remove_sales1").label == "Remove" and not any(b.key == "ea_fix_sales1" for b in at.button)
        assert btn(at, "ea_fix_sales3").label == "🔑 Update password" and btn(at, "ea_remove_sales3").label == "Remove" and not any(b.key == "ea_edit_sales3" for b in at.button)
        assert '<div class="ea-why">Login failed</div>' in markup(at) and '<span class="ea-chip ea-chip-bad">● Disconnected</span>' in markup(at)


def test_an_account_that_only_exists_in_the_old_secrets_says_so_and_has_no_buttons(tmp_path):
    with accounts_page(tmp_path, legacy={"legacy_one": "legacy@kelson.agency"}) as (at, _):
        assert "legacy@kelson.agency" in addresses(at) and '<span class="ea-chip">Legacy</span>' in markup(at) and "Set up in Streamlit secrets" in markup(at)
        assert not any(b.key in ("ea_edit_legacy_one", "ea_remove_legacy_one", "ea_fix_legacy_one") for b in at.button)


def test_the_status_filters_show_their_counts_and_narrow_the_list(tmp_path):
    with accounts_page(tmp_path, legacy={"legacy_one": "legacy@kelson.agency"}) as (at, _):
        assert [o.content for o in pills(at).proto.options] == ["All 4", "Connected 2", "Disconnected 1", "Unknown 1"]
        pills(at).set_value("Disconnected")
        at.run(timeout=30)
        assert addresses(at) == ["sales3@kelson.agency"]
        pills(at).set_value("Unknown")
        at.run(timeout=30)
        assert addresses(at) == ["legacy@kelson.agency"]
        pills(at).set_value(None)
        at.run(timeout=30)
        assert len(addresses(at)) == 4


def test_the_search_box_appears_only_from_four_accounts_and_narrows_by_name_or_address(tmp_path):
    with accounts_page(tmp_path) as (at, _):
        assert not any(t.key == "ea_search" for t in at.text_input)
    st.cache_data.clear()
    with accounts_page(tmp_path, legacy={"legacy_one": "legacy@kelson.agency"}) as (at, _):
        at.text_input(key="ea_search").set_value("SALES2")                                                      # any case
        at.run(timeout=30)
        assert addresses(at) == ["sales2@kelson.agency"]
        at.text_input(key="ea_search").set_value("legacy@")
        at.run(timeout=30)
        assert addresses(at) == ["legacy@kelson.agency"]


def test_nothing_matching_says_so_and_clearing_brings_everything_back(tmp_path):
    with accounts_page(tmp_path, legacy={"legacy_one": "legacy@kelson.agency"}) as (at, _):
        at.text_input(key="ea_search").set_value("zzz")
        at.run(timeout=30)
        assert "No accounts match" in markup(at) and addresses(at) == []
        btn(at, "ea_clear_filters").click()
        at.run(timeout=30)
        assert len(addresses(at)) == 4 and at.text_input(key="ea_search").value == ""


def test_a_long_list_is_drawn_25_at_a_time_with_a_button_that_says_how_many_are_left(tmp_path):
    with accounts_page(tmp_path, slot_yaml=slots(60), health=[], sends=[]) as (at, _):
        assert len(addresses(at)) == 25 and btn(at, "ea_show_more").label == "Show 25 more (35 left)"
        btn(at, "ea_show_more").click()
        at.run(timeout=30)
        assert len(addresses(at)) == 50 and btn(at, "ea_show_more").label == "Show the last 10"
        btn(at, "ea_show_more").click()
        at.run(timeout=30)
        assert len(addresses(at)) == 60 and not any(b.key == "ea_show_more" for b in at.button)


def test_a_short_list_has_no_show_more_button(tmp_path):
    with accounts_page(tmp_path, slot_yaml=slots(25), health=[], sends=[]) as (at, _):
        assert len(addresses(at)) == 25 and not any(b.key == "ea_show_more" for b in at.button)


def test_edit_opens_that_accounts_dialog_with_its_address_and_cancel_closes_it(tmp_path):
    with accounts_page(tmp_path) as (at, _):
        btn(at, "ea_edit_sales1").click()
        at.run(timeout=30)
        assert at.session_state["ea_dialog"] == {"kind": "edit", "name": "sales1"} and at.text_input(key="manage_address_sales1").value == "sales1@kelson.agency"
        assert "Slot 1 · sales1@kelson.agency" in html.unescape(markup(at)) and btn(at, "manage_save_sales1").label == "💾 Save changes"
        btn(at, "manage_cancel_sales1").click()
        at.run(timeout=30)
        assert at.session_state["ea_dialog"] is None and not any(t.key == "manage_address_sales1" for t in at.text_input)


def test_update_password_on_a_disconnected_account_saves_only_the_new_password_to_its_own_slot(tmp_path):
    with accounts_page(tmp_path) as (at, captured):
        btn(at, "ea_fix_sales3").click()
        at.run(timeout=30)
        at.text_input(key="manage_password_sales3").set_value("brand-new-app-password")
        at.run(timeout=30)
        btn(at, "manage_save_sales3").click()
        at.run(timeout=30)
        calls = captured["secrets"]["set_secret_calls"]
        assert list(at.exception) == [] and list(at.error) == [] and len(calls) == 1 and calls[0]["name"] == "EMAIL_ACCOUNT_SLOT_3"
        payload = json.loads(calls[0]["value"])
        assert payload["app_password"] == "brand-new-app-password" and payload["name"] == "sales3" and captured["commits"].get("commits") in (None, [])        # the address did not change
        assert any("'sales3' updated" in s.value for s in at.success) and at.session_state["ea_dialog"] is None


def test_a_failed_save_says_why_and_keeps_the_dialog_open(tmp_path):
    with accounts_page(tmp_path, set_secret_error=GitHubActionsError("boom")) as (at, _):
        btn(at, "ea_fix_sales3").click()
        at.run(timeout=30)
        at.text_input(key="manage_password_sales3").set_value("x")
        at.run(timeout=30)
        btn(at, "manage_save_sales3").click()
        at.run(timeout=30)
        assert any("Failed to update: boom" in e.value for e in at.error) and at.session_state["ea_dialog"] == {"kind": "edit", "name": "sales3"}


def test_remove_asks_for_confirmation_shows_the_warning_and_cancel_closes_it(tmp_path):
    with accounts_page(tmp_path) as (at, captured):
        btn(at, "ea_remove_sales2").click()
        at.run(timeout=30)
        text = html.unescape(markup(at))
        assert "sales2@kelson.agency" in text and "This deletes the stored password for this account." in text
        assert btn(at, "manage_remove_sales2").disabled is True and btn(at, "manage_remove_sales2").label == "🗑 Remove account"
        at.checkbox(key="manage_confirm_remove_sales2").set_value(True)
        at.run(timeout=30)
        assert btn(at, "manage_remove_sales2").disabled is False and captured["secrets"]["delete_secret_calls"] == []
        btn(at, "manage_cancelremove_sales2").click()
        at.run(timeout=30)
        assert at.session_state["ea_dialog"] is None and captured["secrets"]["delete_secret_calls"] == []


def test_removing_deletes_that_slots_secret_and_takes_the_account_out_of_the_mapping(tmp_path):
    with accounts_page(tmp_path) as (at, captured):
        btn(at, "ea_remove_sales2").click()
        at.run(timeout=30)
        at.checkbox(key="manage_confirm_remove_sales2").set_value(True)
        at.run(timeout=30)
        btn(at, "manage_remove_sales2").click()
        at.run(timeout=30)
        assert list(at.exception) == [] and captured["secrets"]["delete_secret_calls"] == ["EMAIL_ACCOUNT_SLOT_2"]
        written = yaml.safe_load(captured["commits"]["commits"][0]["content"].decode("utf-8"))
        assert set(written) == {"sales1", "sales3"} and any("'sales2' removed" in s.value for s in at.success)


def test_the_header_buttons_open_the_add_and_bulk_dialogs_and_each_can_be_cancelled(tmp_path):
    with accounts_page(tmp_path) as (at, _):
        btn(at, "ea_add_open").click()
        at.run(timeout=30)
        assert at.session_state["ea_dialog"]["kind"] == "add" and any(t.key == "add_account_name" for t in at.text_input) and btn(at, "add_account_go").disabled is True
        btn(at, "cancel_add_account").click()
        at.run(timeout=30)
        assert at.session_state["ea_dialog"] is None
        btn(at, "ea_bulk_open").click()
        at.run(timeout=30)
        assert at.session_state["ea_dialog"]["kind"] == "bulk" and any(f.key == "bulk_accounts_csv_upload" for f in at.file_uploader)


def test_an_open_dialog_closes_when_the_page_is_arrived_at_afresh_but_survives_ordinary_reruns(tmp_path):
    with accounts_page(tmp_path, prime={"ea_dialog": {"kind": "edit", "name": "sales1"}}) as (at, _):
        assert at.session_state["ea_dialog"] is None and not any(t.key == "manage_address_sales1" for t in at.text_input)             # arriving afresh never reopens it
    with accounts_page(tmp_path) as (at, _):
        btn(at, "ea_edit_sales1").click()
        at.run(timeout=30)
        at.run(timeout=30)
        assert any(t.key == "manage_address_sales1" for t in at.text_input)                                                            # an ordinary rerun keeps it open


def test_with_no_health_check_yet_every_account_is_unknown_and_the_page_explains_why(tmp_path):
    with accounts_page(tmp_path, health=None) as (at, _):
        assert markup(at).count("○ Unknown") == 3 and "Not checked yet" in markup(at) and any("runs automatically every 2 hours" in c.value for c in at.caption)
        assert "Not yet" in markup(at)


def test_a_campaign_that_has_sent_nothing_yet_is_noted_under_the_list(tmp_path):
    with accounts_page(tmp_path, no_send_tab=True) as (at, _):
        assert any("isn’t counted in “sent today” yet" in c.value for c in at.caption)


def test_with_no_accounts_at_all_there_is_a_welcome_and_the_add_buttons_still_work(tmp_path):
    with accounts_page(tmp_path, slot_yaml="\n") as (at, _):
        assert "No accounts configured yet" in markup(at) and btn(at, "ea_add_open") is not None
        btn(at, "ea_add_open").click()
        at.run(timeout=30)
        assert any(t.key == "add_account_name" for t in at.text_input)


def test_looking_at_the_page_never_writes_anything(tmp_path):
    with accounts_page(tmp_path) as (at, captured):
        at.run(timeout=30)
        assert list(at.exception) == [] and captured["secrets"]["set_secret_calls"] == [] and captured["secrets"]["delete_secret_calls"] == [] and captured["dispatched"] == []
        assert captured["commits"].get("commits") in (None, [])


def test_refresh_really_re_reads_the_health_check_instead_of_showing_cached_data(tmp_path):
    from sheets_readonly import ReadOnlySheetsConnector
    calls = []
    real = ReadOnlySheetsConnector.get_account_health

    def counting(self, *args, **kwargs):
        calls.append(1)
        return real(self, *args, **kwargs)
    with accounts_page(tmp_path) as (at, _):
        with patch.object(ReadOnlySheetsConnector, "get_account_health", counting):
            at.run(timeout=30)
            assert calls == []                                                          # an ordinary rerun uses what it already has
            btn(at, "ea_refresh").click()
            at.run(timeout=30)
            assert len(calls) == 1                                                      # Refresh asks the Sheet again


def test_cancel_in_the_remove_dialog_can_never_pick_up_the_red_remove_styling(tmp_path):
    """REGRESSION (seen in a real browser): Cancel's key contained the text the red Remove style is keyed on, so Cancel turned red and Remove went pale."""
    import email_accounts_html as EH
    selector = re.search(r'(\[class\*="st-key-(manage_remove_)"\])', EH.EA_CSS).group(2)
    with accounts_page(tmp_path) as (at, _):
        btn(at, "ea_remove_sales2").click()
        at.run(timeout=30)
        keys = [b.key for b in at.button if (b.key or "").startswith("manage_")]
        assert keys.count("manage_remove_sales2") == 1 and [k for k in keys if selector in k] == ["manage_remove_sales2"]       # only the real Remove button matches the red style


def test_arriving_from_home_with_add_account_opens_the_dialog_and_uses_up_the_note(tmp_path):
    with accounts_page(tmp_path, prime={"ea_open_add_on_arrival": True}) as (at, _):
        assert at.session_state["ea_dialog"] == {"kind": "add", "name": None} and any(t.key == "add_account_name" for t in at.text_input)
        assert "ea_open_add_on_arrival" not in at.session_state


# ================================================================ the redesigned Add email account dialog

def open_add(at):
    btn(at, "ea_add_open").click()
    at.run(timeout=30)


def fill(at, name=None, address=None, password=None, confirm=None):
    if name is not None:
        at.text_input(key="add_account_name").set_value(name)
    if address is not None:
        at.text_input(key="add_account_address").set_value(address)
    if password is not None:
        at.text_input(key="add_account_password").set_value(password)
    if confirm is not None:
        at.checkbox(key="add_account_confirm").set_value(confirm)
    at.run(timeout=30)


def go_button(at):
    return btn(at, "add_account_go")


def test_the_provider_is_two_cards_gmail_first_each_with_a_line_saying_what_it_is(tmp_path):
    with accounts_page(tmp_path) as (at, _):
        open_add(at)
        radio = at.radio(key="add_account_provider")
        assert radio.value == "Gmail" and len(radio.options) == 2
        assert "Use a Google app password" in radio.options[0] and "Hostinger, Outlook or any SMTP and IMAP server" in radio.options[1]


@pytest.mark.parametrize("typed, kind, said", [("", "hint", "A short label you will see in campaigns"), ("sales1", "error", "already exists (slot 1). Use Edit on its card to change it."),
                                              ("old_one", "info", "is set up in Streamlit secrets. Adding it here takes over managing it."), ("sales9", "ok", "Free to use")])
def test_the_name_gets_a_live_hint_from_the_real_rules(tmp_path, typed, kind, said):
    with accounts_page(tmp_path, legacy={"old_one": "old@kelson.agency"}) as (at, _):
        open_add(at)
        fill(at, name=typed)
        assert f'class="ea-fl ea-fl-{kind}">' in markup(at) and said in html.unescape(markup(at))


def test_an_address_without_an_at_sign_is_flagged_as_you_type_and_a_good_one_says_nothing(tmp_path):
    with accounts_page(tmp_path) as (at, _):
        open_add(at)
        fill(at, address="sales5.kelson.agency")
        assert "That is not a valid email address." in markup(at)
        fill(at, address="sales5@kelson.agency")
        assert "That is not a valid email address." not in markup(at)


def test_gmail_gets_one_line_of_app_password_help_and_no_server_fields(tmp_path):
    with accounts_page(tmp_path) as (at, _):
        open_add(at)
        assert "16-character app password" in markup(at) and at.text_input(key="add_account_password").label == "App password"
        assert not any(t.key == "add_account_smtp_host" for t in at.text_input) and not any(t.key == "add_account_same_imap_password" for t in at.toggle)


def test_a_custom_provider_shows_the_sending_and_checking_groups_and_hides_the_imap_password_until_asked(tmp_path):
    with accounts_page(tmp_path) as (at, _):
        open_add(at)
        at.radio(key="add_account_provider").set_value("Custom (Hostinger, etc.)")
        at.run(timeout=30)
        for key in ("add_account_smtp_host", "add_account_smtp_port", "add_account_smtp_username", "add_account_imap_host", "add_account_imap_port", "add_account_imap_username"):
            assert any(t.key == key for t in at.text_input), key
        assert "Sending (SMTP)" in markup(at) and "Checking replies (IMAP)" in markup(at) and at.text_input(key="add_account_password").label == "Password"
        assert at.toggle(key="add_account_same_imap_password").value is True and not any(t.key == "add_account_imap_password" for t in at.text_input)
        at.toggle(key="add_account_same_imap_password").set_value(False)
        at.run(timeout=30)
        assert any(t.key == "add_account_imap_password" for t in at.text_input)


@pytest.mark.parametrize("name, address, confirm, enabled", [("sales9", "a@b.com", True, True), ("sales9", "a@b.com", False, False), ("", "a@b.com", True, False), ("sales1", "a@b.com", True, False), ("sales9", "nope", True, False)])
def test_add_needs_a_free_name_a_real_address_and_the_deliberate_confirmation(tmp_path, name, address, confirm, enabled):
    with accounts_page(tmp_path) as (at, _):
        open_add(at)
        fill(at, name=name, address=address, confirm=confirm)                   # no password typed: it is not part of this
        assert go_button(at).disabled is (not enabled)


def test_pressing_add_without_a_password_says_so_and_writes_nothing(tmp_path):
    with accounts_page(tmp_path) as (at, captured):
        open_add(at)
        fill(at, name="sales9", address="sales9@kelson.agency", confirm=True)
        go_button(at).click()
        at.run(timeout=30)
        assert any("Password is required." in e.value for e in at.error) and captured["secrets"]["set_secret_calls"] == [] and "ea_add_result" not in at.session_state


def _add_one(at, name="sales9", address="sales9@kelson.agency"):
    open_add(at)
    fill(at, name=name, address=address, password="a-real-app-password", confirm=True)
    go_button(at).click()
    at.run(timeout=30)


def test_adding_an_account_writes_its_secret_and_the_mapping_and_shows_what_was_done(tmp_path):
    with accounts_page(tmp_path) as (at, captured):
        _add_one(at)
        calls = captured["secrets"]["set_secret_calls"]
        assert list(at.exception) == [] and list(at.error) == [] and len(calls) == 1 and calls[0]["name"] == "EMAIL_ACCOUNT_SLOT_4"
        payload = json.loads(calls[0]["value"])
        assert payload["name"] == "sales9" and payload["address"] == "sales9@kelson.agency" and payload["app_password"] == "a-real-app-password"
        assert set(yaml.safe_load(captured["commits"]["commits"][0]["content"].decode("utf-8"))) == {"sales1", "sales2", "sales3", "sales9"} and "check_account_health.yml" in captured["dispatched"][0]
        assert at.session_state["ea_add_result"] == {"name": "sales9", "address": "sales9@kelson.agency", "slot": 4}
        text = markup(at)
        assert "sales9 was added" in text and "sales9@kelson.agency · slot 4" in text and "Connection check started" in text and not any(t.key == "add_account_name" for t in at.text_input)


def test_a_custom_providers_server_details_and_a_separate_imap_password_are_sent_with_it(tmp_path):
    with accounts_page(tmp_path) as (at, captured):
        open_add(at)
        at.radio(key="add_account_provider").set_value("Custom (Hostinger, etc.)")
        at.run(timeout=30)
        for key, value in (("add_account_smtp_host", "smtp.hostinger.com"), ("add_account_smtp_port", "465"), ("add_account_imap_host", "imap.hostinger.com"), ("add_account_imap_port", "993")):
            at.text_input(key=key).set_value(value)
        at.toggle(key="add_account_same_imap_password").set_value(False)
        at.run(timeout=30)
        at.text_input(key="add_account_imap_password").set_value("different-imap-password")
        fill(at, name="host1", address="hello@brand.com", password="smtp-password", confirm=True)
        go_button(at).click()
        at.run(timeout=30)
        payload = json.loads(captured["secrets"]["set_secret_calls"][0]["value"])
        assert payload["smtp_host"] == "smtp.hostinger.com" and str(payload["smtp_port"]) == "465" and payload["imap_host"] == "imap.hostinger.com" and payload["imap_password"] == "different-imap-password"


def test_with_the_same_password_for_imap_no_separate_imap_password_is_sent(tmp_path):
    with accounts_page(tmp_path) as (at, captured):
        open_add(at)
        at.radio(key="add_account_provider").set_value("Custom (Hostinger, etc.)")
        at.run(timeout=30)
        fill(at, name="host1", address="hello@brand.com", password="smtp-password", confirm=True)
        go_button(at).click()
        at.run(timeout=30)
        assert "imap_password" not in json.loads(captured["secrets"]["set_secret_calls"][0]["value"])


def test_add_another_starts_an_empty_form_and_done_closes_the_dialog(tmp_path):
    with accounts_page(tmp_path) as (at, _):
        _add_one(at)
        btn(at, "ea_add_another").click()
        at.run(timeout=30)
        assert "ea_add_result" not in at.session_state and at.text_input(key="add_account_name").value == "" and at.text_input(key="add_account_password").value == "" and at.checkbox(key="add_account_confirm").value is False
        assert go_button(at).disabled is True and at.session_state["ea_dialog"]["kind"] == "add"
    with accounts_page(tmp_path) as (at, _):
        _add_one(at)
        btn(at, "ea_add_done").click()
        at.run(timeout=30)
        assert at.session_state["ea_dialog"] is None and "ea_add_result" not in at.session_state and not any(t.key == "add_account_name" for t in at.text_input)


def test_opening_the_dialog_again_after_an_add_shows_an_empty_form_never_the_old_result(tmp_path):
    with accounts_page(tmp_path) as (at, _):
        _add_one(at)
        btn(at, "ea_add_done").click()
        at.run(timeout=30)
        open_add(at)
        assert "was added" not in markup(at) and at.text_input(key="add_account_name").value == ""


def test_cancelling_a_half_filled_form_and_reopening_gives_a_clean_one(tmp_path):
    with accounts_page(tmp_path) as (at, _):
        open_add(at)
        fill(at, name="half", address="half@x.com")
        btn(at, "cancel_add_account").click()
        at.run(timeout=30)
        open_add(at)
        assert at.text_input(key="add_account_name").value == "" and at.text_input(key="add_account_address").value == ""


def test_a_failed_add_says_why_and_keeps_the_form_instead_of_pretending(tmp_path):
    with accounts_page(tmp_path, set_secret_error=GitHubActionsError("boom")) as (at, captured):
        _add_one(at)
        assert any("Failed to add account: boom" in e.value for e in at.error) and "ea_add_result" not in at.session_state and at.text_input(key="add_account_name").value == "sales9"
        assert captured["commits"].get("commits") in (None, [])


def test_closing_the_dialog_with_the_cross_while_the_result_shows_and_reopening_gives_an_empty_form(tmp_path):
    """The cross closes a dialog in the browser without telling the app, so the last result is still remembered. Opening Add again must still start from a clean form."""
    with accounts_page(tmp_path) as (at, _):
        _add_one(at)
        assert "ea_add_result" in at.session_state                 # the result is showing; we never press Done or Add another (as when the cross is used)
        btn(at, "ea_add_open").click()
        at.run(timeout=30)
        assert "ea_add_result" not in at.session_state and "was added" not in markup(at) and at.text_input(key="add_account_name").value == ""
        assert at.text_input(key="add_account_address").value == "" and go_button(at).disabled is True


def test_add_another_really_clears_the_result_so_the_form_comes_back(tmp_path):
    with accounts_page(tmp_path) as (at, _):
        _add_one(at)
        btn(at, "ea_add_another").click()
        at.run(timeout=30)
        assert "was added" not in markup(at) and any(t.key == "add_account_name" for t in at.text_input)


def test_closing_a_half_filled_form_with_the_cross_and_clicking_add_again_gives_an_empty_form(tmp_path):
    """The cross gives the app no signal, so the half-typed values are still remembered; opening Add again must start clean (this is what clears them)."""
    with accounts_page(tmp_path) as (at, _):
        open_add(at)
        fill(at, name="half", address="half@x.com", password="secret", confirm=True)
        assert at.text_input(key="add_account_name").value == "half"
        btn(at, "ea_add_open").click()                              # no Cancel pressed: as if the cross had closed it
        at.run(timeout=30)
        assert at.text_input(key="add_account_name").value == "" and at.text_input(key="add_account_address").value == "" and at.text_input(key="add_account_password").value == ""
        assert at.checkbox(key="add_account_confirm").value is False and go_button(at).disabled is True
