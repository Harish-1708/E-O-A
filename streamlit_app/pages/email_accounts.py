import json
import os
import sys

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from page_state import mark_active_page  # noqa: E402
from auth import login_gate, current_user  # noqa: E402
from config import REPO_ROOT, SETTINGS_PATH, WORKFLOW_CHECK_ACCOUNT_HEALTH, EMAIL_ACCOUNT_SLOT_MAPPING_ABS_PATH  # noqa: E402
from preview_logic import list_campaigns_live, get_campaign_cfg  # noqa: E402
from sheets_readonly import ReadOnlySheetsConnector, ReadOnlySheetsError  # noqa: E402
from github_client import GitHubClient, GitHubActionsError  # noqa: E402
from accounts_logic import (  # noqa: E402
    aggregate_sent_today_by_account, build_account_rows, build_health_lookup, merge_account_directories,
)
from email_account_slots_logic import (  # noqa: E402
    SLOT_MAPPING_PATH, serialize_slot_mapping, add_account_to_mapping,
    remove_account_from_mapping, update_account_address_in_mapping, read_slot_mapping_live,
    build_account_secret_payload, parse_bulk_accounts_csv,
)
from data_import_logic import parse_csv_bytes  # noqa: E402

if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
import outreach  # noqa: E402

# Page config is set once, centrally, in app.py via st.navigation/st.Page —
# calling st.set_page_config here too would raise an error.

# Computed exactly once per script run — used both to gate login normally
# and, later, to reset the Add Account dialog only on a genuine return
# visit (not a rerun triggered by a widget inside the dialog itself).
_just_arrived = mark_active_page("email_accounts")

if not login_gate():
    st.stop()


@st.cache_resource(show_spinner=False)
def _get_connector() -> ReadOnlySheetsConnector:
    sa_info = dict(st.secrets["google_sheets_readonly"]["service_account_json"])
    sheet_id = st.secrets.get("shared_sheet_id", "")
    return ReadOnlySheetsConnector(service_account_info=sa_info, sheet_id=sheet_id)


@st.cache_resource(show_spinner=False)
def _get_github_client() -> GitHubClient:
    gh = st.secrets["github"]
    return GitHubClient(token=gh["token"], owner=gh["owner"], repo=gh["repo"], cache_reads=True)


def _safe_github_client():
    """The GitHub client, or None if it can't be built. Returning None
    rather than raising lets the slot-mapping READ degrade to the local
    checkout instead of breaking the whole page."""
    try:
        return _get_github_client()
    except Exception:  # noqa: BLE001 - missing/invalid token, etc.
        return None


# LIVE read. Removing an account commits this file to GitHub, but the
# local checkout on Streamlit Cloud stays frozen until a redeploy — so a
# removed account kept showing as connected here indefinitely, exactly
# like the campaign settings/schedule did before the same fix.
slot_mapping = read_slot_mapping_live(_safe_github_client(), EMAIL_ACCOUNT_SLOT_MAPPING_ABS_PATH)
streamlit_secret_directory = dict(st.secrets.get("email_accounts_directory", {}))
account_directory = merge_account_directories(streamlit_secret_directory, slot_mapping)

settings = outreach.load_settings(SETTINGS_PATH)
default_account = settings.get("email_accounts", {}).get("default_account", "")

try:
    campaigns = list_campaigns_live(_safe_github_client())
except Exception as exc:  # noqa: BLE001
    st.error(f"Couldn't list campaigns: {exc}")
    campaigns = []


@st.cache_data(ttl=30, show_spinner=False)
def _load_send_logs(campaign_names):
    connector = _get_connector()
    logs = {}
    unavailable = []
    for name in campaign_names:
        try:
            campaign_cfg = get_campaign_cfg(name)
            logs[name] = connector.get_all_send_log(campaign_cfg["send_log_tab"])
        except ReadOnlySheetsError:
            unavailable.append(name)
    return logs, unavailable


@st.cache_data(ttl=120, show_spinner=False)
def _load_account_health():
    return _get_connector().get_account_health()


import email_accounts_html as EH  # noqa: E402
import email_accounts_view_logic as EV  # noqa: E402


def _open_dialog(kind, name=None) -> None:
    if kind == "add":
        _reset_add_form()                                   # a fresh, empty form every time (and never the result of the last add)
    if kind == "bulk":
        _reset_bulk_form()
    st.session_state["ea_dialog"] = {"kind": kind, "name": name}


def _close_dialog() -> None:
    st.session_state["ea_dialog"] = None


def _show_more() -> None:
    st.session_state["ea_shown"] = int(st.session_state.get("ea_shown") or EV.PAGE_SIZE) + EV.PAGE_SIZE


def _clear_account_filters() -> None:
    st.session_state["ea_search"] = ""
    st.session_state["ea_filter"] = "all"


# =============================================================================
# The four dialogs. Each one stays open across the reruns its own widgets cause (the page calls it again every run while ea_dialog says so), and
# is reset on a genuine arrival at the page: the same pattern as the New Campaign dialog.
# =============================================================================
PROVIDER_LABELS = {"Gmail": "**Gmail**  \nUse a Google app password. The simplest setup.",
                   "Custom (Hostinger, etc.)": "**Custom provider**  \nHostinger, Outlook or any SMTP and IMAP server."}


def _reset_add_form() -> None:
    """Start the Add account form empty again (forget what was typed and any result from the last add)."""
    st.session_state.pop("ea_add_result", None)
    for key in [k for k in st.session_state if str(k).startswith("add_account_")]:
        del st.session_state[key]


def _hint(markup: str) -> None:
    """Draw a hint only when there is one (an empty hint would still take up a gap)."""
    if markup:
        st.markdown(markup, unsafe_allow_html=True)


def _add_another() -> None:
    _reset_add_form()


def _done_adding() -> None:
    _reset_add_form()
    st.session_state["ea_dialog"] = None


@st.dialog("Add email account", width="large")
def _add_account_dialog(current_mapping):
    result = st.session_state.get("ea_add_result")
    if result:
        st.markdown(EH.result_panel_html(result["name"], result["address"], result["slot"]), unsafe_allow_html=True)
        _spacer, again_column, done_column = st.columns([1, 1.3, 1])
        with again_column:
            st.button("＋ Add another", key="ea_add_another", width="stretch", on_click=_add_another)
        with done_column:
            st.button("Done", type="primary", key="ea_add_done", width="stretch", on_click=_done_adding)
        return

    st.markdown(EH.dialog_header_html("Connect a mailbox your campaigns can send from."), unsafe_allow_html=True)
    provider = st.radio("Provider", ["Gmail", "Custom (Hostinger, etc.)"], key="add_account_provider", horizontal=True, format_func=lambda option: PROVIDER_LABELS[option])
    legacy_names = [n for n in account_directory if n not in current_mapping]
    name = st.text_input("Account name", key="add_account_name", placeholder="e.g. sales1")
    _hint(EH.field_feedback_html(*EV.account_name_feedback(name, current_mapping, legacy_names)))
    address = st.text_input("Email address", key="add_account_address", placeholder="name@yourdomain.com")
    _hint(EH.field_feedback_html(*EV.address_feedback(address)))
    password = st.text_input("App password" if provider == "Gmail" else "Password", type="password", key="add_account_password")
    if provider == "Gmail":
        st.markdown(EH.field_feedback_html("hint", "Gmail needs a 16-character app password, not your normal password. Create one in your Google account under Security, then App passwords."), unsafe_allow_html=True)

    smtp_host = smtp_port = smtp_username = imap_host = imap_port = imap_username = imap_password = ""
    if provider != "Gmail":
        smtp_column, imap_column = st.columns(2)
        with smtp_column:
            with st.container(key="ea_grp_smtp"):
                st.markdown(EH.group_title_html("Sending (SMTP)"), unsafe_allow_html=True)
                smtp_host = st.text_input("SMTP Host", key="add_account_smtp_host", placeholder="smtp.hostinger.com")
                smtp_port = st.text_input("SMTP Port", key="add_account_smtp_port", placeholder="465")
                smtp_username = st.text_input("SMTP Username", key="add_account_smtp_username", placeholder="defaults to the email address")
        with imap_column:
            with st.container(key="ea_grp_imap"):
                st.markdown(EH.group_title_html("Checking replies (IMAP)"), unsafe_allow_html=True)
                imap_host = st.text_input("IMAP Host", key="add_account_imap_host", placeholder="imap.hostinger.com")
                imap_port = st.text_input("IMAP Port", key="add_account_imap_port", placeholder="993")
                imap_username = st.text_input("IMAP Username", key="add_account_imap_username", placeholder="defaults to the email address")
        if not st.toggle("Use the same password for IMAP", value=True, key="add_account_same_imap_password"):
            imap_password = st.text_input("IMAP password", type="password", key="add_account_imap_password")

    st.markdown(EH.secure_note_html(), unsafe_allow_html=True)
    confirm = st.checkbox("These details are right. Add this account now.", key="add_account_confirm")
    ready = EV.add_form_ready(name, address, current_mapping)

    _spacer, cancel_column, go_column = st.columns([2, 1, 1.5])
    with cancel_column:
        cancelled = st.button("Cancel", key="cancel_add_account", width="stretch")
    with go_column:
        submit_clicked = st.button("Add account", type="primary", disabled=not (confirm and ready), key="add_account_go", width="stretch")
    if cancelled:
        _close_dialog()
        st.rerun()

    if submit_clicked:
        errors = []
        if not name or not name.strip():
            errors.append("Account name is required.")
        if not address or "@" not in address:
            errors.append("A valid email address is required.")
        if not password or not password.strip():
            errors.append("Password is required.")

        if errors:
            for e in errors:
                st.error(e)
        else:
            try:
                updated_mapping = add_account_to_mapping(current_mapping, name.strip(), address.strip())
                slot = updated_mapping[name.strip()]["slot"]
                secret_payload = build_account_secret_payload(
                    name.strip(), address.strip(), password.strip(),
                    imap_password=imap_password.strip() or None,
                    smtp_host=smtp_host.strip() or None, smtp_port=smtp_port.strip() or None,
                    smtp_username=smtp_username.strip() or None,
                    imap_host=imap_host.strip() or None, imap_port=imap_port.strip() or None,
                    imap_username=imap_username.strip() or None,
                )
                client = _get_github_client()
                client.set_secret(f"EMAIL_ACCOUNT_SLOT_{slot}", secret_payload)
                client.create_file(
                    SLOT_MAPPING_PATH, serialize_slot_mapping(updated_mapping),
                    message=f"Add email account '{name.strip()}' to slot {slot} (via Streamlit, by {current_user()})",
                )
                try:
                    client.dispatch_workflow(WORKFLOW_CHECK_ACCOUNT_HEALTH, {})
                except GitHubActionsError:
                    pass  # non-critical — the periodic health check will still catch it within 2 hours
                st.session_state["ea_add_result"] = {"name": name.strip(), "address": address.strip(), "slot": slot}
                st.rerun()
            except (ValueError, GitHubActionsError) as exc:
                st.error(f"Failed to add account: {exc}")


def _reset_bulk_form() -> None:
    """Start the Bulk add form empty again: forget the file, the confirmation and the result of the last run."""
    st.session_state.pop("ea_bulk_result", None)
    for key in ("bulk_accounts_csv_upload", "confirm_bulk_add_accounts"):
        st.session_state.pop(key, None)


def _bulk_add_more() -> None:
    _reset_bulk_form()


def _bulk_done() -> None:
    _reset_bulk_form()
    st.session_state["ea_dialog"] = None


@st.dialog("Bulk add accounts", width="large")
def _bulk_add_dialog(current_mapping):
    result = st.session_state.get("ea_bulk_result")
    if result:
        st.markdown(EH.bulk_result_html(result["added"], result["skipped"]), unsafe_allow_html=True)
        _spacer, more_column, done_column = st.columns([1, 1.3, 1])
        with more_column:
            st.button("＋ Add more", key="ea_bulk_more", width="stretch", on_click=_bulk_add_more)
        with done_column:
            st.button("Done", type="primary", key="ea_bulk_done", width="stretch", on_click=_bulk_done)
        return

    st.markdown(EH.dialog_header_html("Add many mailboxes at once from a CSV file."), unsafe_allow_html=True)
    st.markdown(EH.bulk_step_html(1, "Your file needs these columns"), unsafe_allow_html=True)
    st.markdown(EH.column_chips_html(EV.BULK_REQUIRED_COLUMNS, EV.BULK_OPTIONAL_COLUMNS), unsafe_allow_html=True)
    st.download_button("⬇ Download template", data=EV.bulk_template_csv(), file_name="email_accounts_template.csv", mime="text/csv", key="bulk_accounts_template")
    with st.expander("See an example"):
        st.code(EV.bulk_template_csv().decode("utf-8"), language="csv")

    st.markdown(EH.bulk_step_html(2, "Upload it"), unsafe_allow_html=True)
    bulk_file = st.file_uploader("Upload CSV", type=["csv"], key="bulk_accounts_csv_upload", label_visibility="collapsed")
    if bulk_file is None:
        if st.button("Cancel", key="cancel_bulk_accounts_empty"):
            _close_dialog()
            st.rerun()
        return

    bulk_columns, bulk_rows = parse_csv_bytes(bulk_file.getvalue())
    parsed_accounts, parse_errors = parse_bulk_accounts_csv(bulk_columns, bulk_rows)
    counts = EV.bulk_counts(parsed_accounts, parse_errors)
    st.markdown(EH.bulk_step_html(3, "Review"), unsafe_allow_html=True)
    st.markdown(EH.bulk_summary_html(counts["ready"], counts["skipped"]), unsafe_allow_html=True)
    if parsed_accounts:
        st.dataframe(EV.bulk_review_rows(parsed_accounts), hide_index=True, width="stretch")
    st.markdown(EH.bulk_skipped_html(parse_errors), unsafe_allow_html=True)

    if not parsed_accounts:
        if st.button("Cancel", key="cancel_bulk_accounts", width="stretch"):
            _close_dialog()
            st.rerun()
        return

    total = len(parsed_accounts)
    st.caption(f"Adding makes {total} separate call(s) to GitHub (one per account's secret), so a long list can take a little while. Skipped rows are not added.")
    confirm_bulk = st.checkbox(f"These accounts are right. Add all {total} now.", key="confirm_bulk_add_accounts")
    _spacer, cancel_column, go_column = st.columns([2, 1, 1.8])
    with cancel_column:
        cancelled = st.button("Cancel", key="cancel_bulk_accounts", width="stretch")
    with go_column:
        go_clicked = st.button(f"Add all {total} accounts", type="primary", disabled=not confirm_bulk, key="bulk_add_accounts_button", width="stretch")
    if cancelled:
        _close_dialog()
        st.rerun()
    if go_clicked:
        working_mapping = dict(current_mapping)
        added = []
        skipped = list(parse_errors)
        client = _get_github_client()
        progress = st.progress(0.0, text="Starting…")
        listing = st.empty()
        names = [row["name"] for row in parsed_accounts]
        for index, row in enumerate(parsed_accounts):
            progress.progress(index / total, text=f"Adding account {index + 1} of {total} · {row['name']}")
            listing.markdown(EH.bulk_progress_html([n for n, _ in added], names[index:]), unsafe_allow_html=True)
            try:
                working_mapping = add_account_to_mapping(working_mapping, row["name"], row["address"])
                slot = working_mapping[row["name"]]["slot"]
                secret_payload = build_account_secret_payload(
                    row["name"], row["address"], row["password"],
                    imap_password=row["imap_password"], smtp_host=row["smtp_host"],
                    smtp_port=row["smtp_port"], smtp_username=row["smtp_username"],
                    imap_host=row["imap_host"], imap_port=row["imap_port"],
                    imap_username=row["imap_username"],
                )
                client.set_secret(f"EMAIL_ACCOUNT_SLOT_{slot}", secret_payload)
                added.append((row["name"], slot))
            except (ValueError, GitHubActionsError) as exc:
                skipped.append(f"{row['name']}: {exc}")
        progress.progress(1.0, text="Finishing…")
        if added:
            # One mapping-file commit at the end, not one per account — the mapping is a
            # single small file; committing it 100 times for a 100-row CSV would be pure waste.
            client.create_file(
                SLOT_MAPPING_PATH, serialize_slot_mapping(working_mapping),
                message=f"Bulk-add {len(added)} email account(s) "
                        f"(via Streamlit, by {current_user()})",
            )
            try:
                client.dispatch_workflow(WORKFLOW_CHECK_ACCOUNT_HEALTH, {})
            except GitHubActionsError:
                pass
        st.session_state["ea_bulk_result"] = {"added": added, "skipped": skipped}
        st.rerun()


def _edit_dialog(name):
    entry = slot_mapping.get(name)

    @st.dialog(f"Edit {name}")
    def _body():
        if entry is None:
            st.info("This account is set up in Streamlit secrets, so it cannot be edited here. Add it again with the same name to manage it.")
            return
        st.markdown(EH.dialog_note_html(f"Slot {entry['slot']} · {entry['address']}"), unsafe_allow_html=True)
        new_address = st.text_input("Email address", value=entry["address"], key=f"manage_address_{name}")
        new_password = st.text_input(
            "New app password (leave blank to keep the current one)", type="password",
            key=f"manage_password_{name}",
        )
        _spacer, cancel_column, save_column = st.columns([2, 1, 1.5])
        with cancel_column:
            cancelled = st.button("Cancel", key=f"manage_cancel_{name}", width="stretch")
        with save_column:
            save_clicked = st.button("💾 Save changes", type="primary", key=f"manage_save_{name}", width="stretch")
        if cancelled:
            _close_dialog()
            st.rerun()
        if save_clicked:
            try:
                client = _get_github_client()
                if new_password and new_password.strip():
                    secret_payload = json.dumps({
                        "name": name, "address": new_address.strip(),
                        "app_password": new_password.strip(),
                    })
                    client.set_secret(f"EMAIL_ACCOUNT_SLOT_{entry['slot']}", secret_payload)
                if new_address.strip() != entry["address"]:
                    updated_mapping = update_account_address_in_mapping(slot_mapping, name, new_address.strip())
                    client.create_file(
                        SLOT_MAPPING_PATH, serialize_slot_mapping(updated_mapping),
                        message=f"Update address for '{name}' (via Streamlit, by {current_user()})",
                    )
                _close_dialog()
                st.success(f"'{name}' updated. May take a minute to reflect here while the app "
                           "redeploys.")
            except (ValueError, GitHubActionsError) as exc:
                st.error(f"Failed to update: {exc}")
    _body()


def _remove_dialog(name):
    entry = slot_mapping.get(name)

    @st.dialog(f"Remove {name}?")
    def _body():
        if entry is None:
            st.info("This account is set up in Streamlit secrets, so it cannot be removed here.")
            return
        st.markdown(EH.dialog_note_html(entry["address"]), unsafe_allow_html=True)
        st.markdown(EH.warn_box_html("This deletes the stored password for this account. Campaigns that still list it as a sender will no longer be able to use it."), unsafe_allow_html=True)
        confirm_remove = st.checkbox("Confirm removal", key=f"manage_confirm_remove_{name}")
        _spacer, cancel_column, remove_column = st.columns([1.2, 1, 2])                 # wide enough for the longer label
        with cancel_column:
            # NOT named manage_remove_...: the red styling of the Remove button is keyed on that text, and Cancel must never look like the destructive one.
            cancelled = st.button("Cancel", key=f"manage_cancelremove_{name}", width="stretch")
        with remove_column:
            remove_clicked = st.button("🗑 Remove account", key=f"manage_remove_{name}", disabled=not confirm_remove, width="stretch")
        if cancelled:
            _close_dialog()
            st.rerun()
        if remove_clicked:
            try:
                client = _get_github_client()
                client.delete_secret(f"EMAIL_ACCOUNT_SLOT_{entry['slot']}")
                updated_mapping = remove_account_from_mapping(slot_mapping, name)
                client.create_file(
                    SLOT_MAPPING_PATH, serialize_slot_mapping(updated_mapping),
                    message=f"Remove email account '{name}' (via Streamlit, by {current_user()})",
                )
                _close_dialog()
                st.success(f"'{name}' removed. May take a minute to reflect here while the app "
                           "redeploys.")
            except GitHubActionsError as exc:
                st.error(f"Failed to remove: {exc}")
    _body()


# =============================================================================
# The page
# =============================================================================
st.markdown(f"<style>{EH.EA_CSS}</style>", unsafe_allow_html=True)
if "ea_dialog" not in st.session_state:
    st.session_state["ea_dialog"] = None
# Reset on genuine navigation, not on reruns triggered by widgets inside a dialog itself. Uses the SAME _just_arrived computed once at the top of the script;
# calling mark_active_page a second time here would always see "no change" (the first call just set it).
if _just_arrived:
    st.session_state["ea_dialog"] = None
if st.session_state.pop("ea_open_add_on_arrival", False):                          # Home's "Add account" button: arrive with the dialog already open
    _open_dialog("add")

with st.container(key="ea_head"):
    head_column, refresh_column, bulk_column, add_column = st.columns([4.2, 1.6, 1.6, 1.6], vertical_alignment="center")       # the three buttons get the same width
    head_column.markdown(EH.page_head_html("Email Accounts", "The sender accounts your campaigns send from, and how each one is doing."), unsafe_allow_html=True)
    with refresh_column:
        refresh_clicked = st.button("🔄 Refresh", key="ea_refresh", width="stretch")
    with bulk_column:
        st.button("📥 Bulk add (CSV)", key="ea_bulk_open", width="stretch", on_click=_open_dialog, args=("bulk",))
    with add_column:
        st.button("＋ Add account", type="primary", key="ea_add_open", width="stretch", on_click=_open_dialog, args=("add",))
if refresh_clicked:
    _load_send_logs.clear()
    _load_account_health.clear()
st.markdown(EH.security_banner_html(), unsafe_allow_html=True)

_dialog = st.session_state.get("ea_dialog")
if _dialog:
    if _dialog["kind"] == "add":
        _add_account_dialog(slot_mapping)
    elif _dialog["kind"] == "bulk":
        _bulk_add_dialog(slot_mapping)
    elif _dialog["kind"] == "edit":
        _edit_dialog(_dialog["name"])
    elif _dialog["kind"] == "remove":
        _remove_dialog(_dialog["name"])

if not account_directory:
    st.markdown(EH.empty_state_html("No accounts configured yet", "Campaigns send from these accounts. Add one at a time, or many at once with a CSV."), unsafe_allow_html=True)
    st.caption("Older setup: add [email_accounts_directory] to Streamlit Secrets for accounts still managed directly in EMAIL_ACCOUNTS_JSON.")
    st.stop()

send_logs_by_campaign, unavailable_campaigns = _load_send_logs(tuple(campaigns))
sent_today_by_account = aggregate_sent_today_by_account(send_logs_by_campaign)

health_records = _load_account_health()
health_lookup = build_health_lookup(health_records)
rows = build_account_rows(account_directory, sent_today_by_account, default_account, health_lookup=health_lookup)
cards = EV.account_cards(rows, slot_mapping)

st.markdown(EH.stats_html(EV.summarize_accounts(cards)), unsafe_allow_html=True)

with st.container(key="ea_toolbar"):
    filter_column, search_column = st.columns([4, 1.8])
    options = EV.status_filter_options(cards)
    labels = dict(options)
    if st.session_state.get("ea_filter") not in labels:
        st.session_state["ea_filter"] = "all"
    with filter_column:
        chosen = st.pills("Status", [key for key, _ in options], format_func=lambda key: labels[key], selection_mode="single", key="ea_filter", label_visibility="collapsed")
    search = ""
    if EV.show_search(len(cards)):
        with search_column:
            search = st.text_input("Search accounts", key="ea_search", label_visibility="collapsed", placeholder="🔍 Search accounts…")

visible = EV.filter_accounts(cards, chosen, search)
if not visible:
    st.markdown(EH.empty_state_html("No accounts match", "Check the spelling, or clear the search and the status filter."), unsafe_allow_html=True)
    st.button("Clear search", key="ea_clear_filters", on_click=_clear_account_filters)
else:
    drawn, hidden = EV.visible_count(len(visible), st.session_state.get("ea_shown"))
    for index, card in enumerate(visible[:drawn]):
        with st.container(key=f"ea_card_{card['kind']}_{index}"):
            body_column, action_column = st.columns([5.3, 2.3], vertical_alignment="center")
            body_column.markdown(EH.account_card_html(card), unsafe_allow_html=True)
            with action_column:
                if card["managed"]:
                    first_column, second_column = st.columns(2)
                    with first_column:
                        if card["kind"] == "bad":
                            st.button("🔑 Update password", key=f"ea_fix_{card['name']}", width="stretch", on_click=_open_dialog, args=("edit", card["name"]))
                        else:
                            st.button("✎ Edit", key=f"ea_edit_{card['name']}", width="stretch", on_click=_open_dialog, args=("edit", card["name"]))
                    with second_column:
                        st.button("Remove", key=f"ea_remove_{card['name']}", width="stretch", on_click=_open_dialog, args=("remove", card["name"]))
                else:
                    st.markdown(EH.legacy_note_html(), unsafe_allow_html=True)
    if hidden:
        st.button(EV.show_more_label(hidden), key="ea_show_more", on_click=_show_more)

if not health_records:
    st.caption(
        "No connection status yet — the 'Check Account Health' workflow runs automatically every 2 "
        "hours, or trigger it manually from the GitHub Actions tab to check now."
    )

if unavailable_campaigns:
    st.caption(EV.not_counted_note(unavailable_campaigns))
