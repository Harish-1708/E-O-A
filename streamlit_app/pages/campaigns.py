import os
import sys
import time
from datetime import datetime, timezone

import streamlit as st
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auth import login_gate, current_user  # noqa: E402
from page_state import mark_active_page  # noqa: E402
from config import (  # noqa: E402
    REPO_ROOT, WORKFLOW_MARK_RESPONSES_READ,
    WORKFLOW_IMPORT_LEADS, WORKFLOW_REMOVE_LEADS, WORKFLOW_DASHBOARD,
    WORKFLOW_SEND, WORKFLOW_CHECK_REPLIES, WORKFLOW_BACKFILL_THREAD_SUBJECT, WORKFLOW_SYNC_ASANA,
    TEMPLATES_ROOT, CAMPAIGNS_DIR, EMAIL_ACCOUNT_SLOT_MAPPING_ABS_PATH, SETTINGS_PATH,
)
from email_account_slots_logic import read_slot_mapping_live  # noqa: E402
from accounts_logic import build_health_lookup, merge_account_directories  # noqa: E402
from github_client import GitHubClient, GitHubActionsError  # noqa: E402
from preview_logic import list_campaigns_live, get_campaign_cfg  # noqa: E402
from sheets_readonly import ReadOnlySheetsConnector  # noqa: E402
from campaigns_hub_logic import build_campaigns_hub, filter_campaigns_by_search  # noqa: E402
from performance_logic import build_campaign_performance, build_best_performers  # noqa: E402
from performance_view import render_performance  # noqa: E402
from data_import_logic import (  # noqa: E402
    parse_csv_bytes, build_default_mapping, apply_mapping, validate_mapping, count_valid_rows,
    build_import_payload, import_payload_path, build_removal_payload, removal_payload_path,
    payload_to_bytes, filter_leads, search_leads, KNOWN_FIELDS, FILTER_OPTIONS,
    NEW_CUSTOM_FIELD_OPTION, validate_custom_field_name, find_duplicate_columns, build_full_lead_table,
)
from campaign_builder import (  # noqa: E402
    build_campaign_files,
    validate_campaign_name, commit_message_for_campaign, confirmation_matches_campaign_name,
    list_campaign_files_to_delete, build_campaign_duplication_files,
)
from settings_logic import (  # noqa: E402
    load_raw_override_live, merge_live_override_into_cfg,
    override_to_yaml_bytes, override_file_path,
    build_asana_settings_override, build_tracker_sync_settings_override,
)
from schedule_view import ScheduleContext, render_schedule_tab  # noqa: E402
from settings_view import SettingsContext, render_settings_tab  # noqa: E402
from launch_logic import build_status_override, build_delete_override, build_restore_override  # noqa: E402
from responses_hub_logic import (  # noqa: E402
    group_responses_by_lead, sort_conversation_groups_newest_first, tag_responses_with_campaign, search_responses,
    count_unread_conversations, split_keys_by_campaign, build_mark_read_payload,
)
from inbox_view import InboxContext, render_inbox  # noqa: E402
from leads_view import render_lead_browser, render_extra_filters, render_clear_filters  # noqa: E402
from leads_logic import filter_counts, apply_extra_filters, any_extra_filter, stage_count, stage_labels  # noqa: E402
from leads_actions import render_lead_actions  # noqa: E402
from sequences_view import SequencesContext, render_sequences_tab  # noqa: E402
from campaign_html import CAMPAIGN_SKIN_CSS, UI_BUILD, build_html, chips_html, crumb_html, subline_html  # noqa: E402
from campaign_header_logic import read_cron_minute, next_automatic_send, next_send_line, schedule_summary, sender_summary, subline, responses_tab_label  # noqa: E402
from parallel_logic import run_in_parallel  # noqa: E402
from conversation_logic import filter_responses_for_lead, filter_sent_mail_for_lead  # noqa: E402
from campaign_status_logic import (  # noqa: E402
    compute_campaign_status, compute_campaign_readiness, status_label,
    STATUS_DRAFT, STATUS_RUNNING, STATUS_PAUSED, STATUS_ATTENTION, STATUS_COMPLETED,
)
from send_logic import (  # noqa: E402
    build_send_inputs, build_check_replies_inputs, build_backfill_thread_subject_inputs, confirmation_is_valid,
)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
import outreach  # noqa: E402

if not login_gate():
    st.stop()


@st.cache_resource(show_spinner=False)
def _get_github_client() -> GitHubClient:
    gh = st.secrets["github"]
    return GitHubClient(token=gh["token"], owner=gh["owner"], repo=gh["repo"], cache_reads=True)


def _safe_github_client():
    """The GitHub client, or None if it can't be built (no token, bad
    config). Returning None rather than raising lets every live read
    degrade to the local checkout instead of breaking the whole page —
    a read must never be harder to do than it was before going live."""
    try:
        return _get_github_client()
    except Exception:  # noqa: BLE001 - missing/invalid token, etc.
        return None


@st.cache_resource(show_spinner=False)
def _get_connector() -> ReadOnlySheetsConnector:
    sa_info = dict(st.secrets["google_sheets_readonly"]["service_account_json"])
    sheet_id = st.secrets.get("shared_sheet_id", "")
    return ReadOnlySheetsConnector(service_account_info=sa_info, sheet_id=sheet_id)


def _fetch_sheet_data(campaign_cfg, connector=None):
    # connector is passed in by callers that run this on a worker thread
    # (see _load_hub_rows): the cached connector is resolved once on the
    # normal script thread and handed over, so no worker thread ever
    # calls into Streamlit's cache itself.
    connector = connector or _get_connector()
    leads = connector.get_all_leads(campaign_cfg["master_tab"])
    responses = connector.get_all_responses(campaign_cfg["responses_tab"])
    send_log = connector.get_all_send_log(campaign_cfg["send_log_tab"])
    return leads, responses, send_log


@st.cache_data(ttl=30, show_spinner=False)
def _fetch_full_campaign_data_cached(campaign_name: str):
    """Cached by campaign_name (a plain string — hashable, a clean cache
    key), NOT by campaign_cfg (a dict — unhashable, and re-fetching per
    rerun regardless of ttl was exactly the Sheets-quota bug this fixes).
    Streamlit reruns the ENTIRE script on almost every widget interaction
    — every mapping dropdown touched while setting up a CSV import, every
    filter change, every keystroke in search — so without this cache, a
    few minutes of ordinary use on this page alone can comfortably exceed
    Google's 60-reads/minute/user quota and return a 429.

    Returns (leads, responses, send_log, error_log, fetched_at) —
    fetched_at is computed HERE, inside the cached function, so it only
    changes when a real fetch actually happens, not on every rerun. This
    is what lets the Data tab show "as of HH:MM:SS" — Streamlit reruns
    the script on interaction, not on a timer, so a page left open
    without being touched will keep showing whatever it last rendered no
    matter how long real time passes; a visible timestamp is what makes
    that fact obvious instead of confusing."""
    campaign_cfg = get_campaign_cfg(campaign_name)
    connector = _get_connector()
    # The four tabs are independent, so read them at the same time rather
    # than one after another (previously ~8 Google requests in a row).
    # If any fail, the FIRST failure in the original order is re-raised,
    # exactly the error the old sequential code would have surfaced.
    results = run_in_parallel([
        lambda: connector.get_all_leads(campaign_cfg["master_tab"]),
        lambda: connector.get_all_responses(campaign_cfg["responses_tab"]),
        lambda: connector.get_all_send_log(campaign_cfg["send_log_tab"]),
        lambda: connector.get_all_error_log(campaign_cfg["error_log_tab"]),
    ], max_workers=4)
    for ok, value in results:
        if not ok:
            raise value
    leads, responses, send_log, error_log = (value for _, value in results)
    fetched_at = datetime.now().strftime("%H:%M:%S")
    return leads, responses, send_log, error_log, fetched_at


@st.cache_data(ttl=30, show_spinner=False)
def _load_sent_mail_cached(campaign_name: str):
    """This campaign's Sent Mail tab — the emails the team sent by hand or from the Inbox. Read only
    when a conversation is open; [] until the tab exists."""
    campaign_cfg = get_campaign_cfg(campaign_name)
    return _get_connector().get_all_sent_mail(campaign_cfg.get("sent_mail_tab") or f"{campaign_name} Sent Mail")


@st.cache_data(ttl=30, show_spinner=False)
def _get_master_header_cached(campaign_name: str):
    """Same fix, same reason — this was being re-fetched on every single
    mapping-dropdown adjustment during CSV import, which is precisely the
    highest-interaction-density moment on this whole page."""
    campaign_cfg = get_campaign_cfg(campaign_name)
    return _get_connector().get_header(campaign_cfg["master_tab"])


def _get_campaign_cfg_live(campaign_name: str):
    """get_campaign_cfg, with the live GitHub override overlaid — the
    same single-point pattern used on the campaign detail page.

    Without this, the campaigns hub list built each row's status
    straight from get_campaign_cfg, which is LOCAL-DISK ONLY — it never
    consults GitHub at all, unlike the detail page. Resuming a campaign
    commits correctly every time; the hub row for it would otherwise
    never look, and would show "Paused" indefinitely regardless of how
    long you waited, because nothing about waiting re-reads the
    repository — only a genuinely fresh live read does."""
    campaign_cfg = get_campaign_cfg(campaign_name)
    return merge_live_override_into_cfg(
        campaign_cfg, load_raw_override_live(campaign_name, _safe_github_client(), CAMPAIGNS_DIR))


@st.cache_data(ttl=30, show_spinner=False)
def _load_hub_rows():
    campaign_names = list_campaigns_live(_safe_github_client())
    # Resolved here, on the script thread, then handed to the worker
    # threads that fetch each campaign's Sheets in parallel. If it can't
    # be built, every active campaign reports that error individually —
    # exactly what happened before, when each fetch built it itself.
    try:
        connector = _get_connector()
        connector_error = None
    except Exception as exc:  # noqa: BLE001
        connector, connector_error = None, exc

    def _fetch_for_hub(campaign_cfg):
        if connector_error is not None:
            raise connector_error
        return _fetch_sheet_data(campaign_cfg, connector=connector)

    return build_campaigns_hub(campaign_names, _get_campaign_cfg_live, _fetch_for_hub, max_workers=6)


def _relative_time(timestamp_str: str) -> str:
    if not timestamp_str:
        return "—"
    try:
        dt = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return timestamp_str
    delta = datetime.now() - dt
    seconds = delta.total_seconds()
    if seconds < 0:
        return timestamp_str
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} hr ago"
    return f"{int(seconds // 86400)} day(s) ago"


# =============================================================================
# New Campaign — inline modal, no separate page. Minimal by design: just a
# name and one Intro variant. Everything else (more variants, follow-ups,
# leads, schedule, settings) happens after, inside the campaign's own
# Sequences/Data/Settings tabs — this dialog's only job is getting a new
# campaign to the point where it can be opened at all.
# =============================================================================
def _initialize_campaign_tabs(campaign_name: str) -> None:
    """Triggers the Dashboard workflow right after a commit — running it
    connects to the Sheet, which creates every needed tab (Master,
    Responses, Send Log, Error Log, Dashboard) with the correct header as
    a side effect. This is what avoids the "tab doesn't exist" error on
    the first visit to a brand new campaign — no manual Preview/Send run
    required first."""
    try:
        client = _get_github_client()
        client.dispatch_workflow(WORKFLOW_DASHBOARD, {"campaign": campaign_name})
    except GitHubActionsError as exc:
        st.warning(
            f"Campaign created, but couldn't auto-initialize its Sheet tabs: {exc}. "
            f"Run 'Update Dashboard' manually for '{campaign_name}' from the GitHub Actions "
            "tab, or just open the campaign once — either will create them."
        )


PLACEHOLDER_INTRO_SUBJECT = "Write your subject here"
PLACEHOLDER_INTRO_BODY = (
    "Write your intro email here.\n\n"
    "This is placeholder text — edit it in the Sequences tab before sending anything."
)


@st.dialog("Duplicate Campaign")
def _duplicate_campaign_dialog(source_campaign_name, existing_campaigns):
    st.caption(
        f"Copies every template (every stage x every variant) from '{source_campaign_name}' under a "
        "brand new name — completely independent files from the moment this runs, so editing or "
        "deleting a template on the duplicate later can never affect the original. Settings (sending "
        "limits, schedule, Asana sync) carry over too, except status, which always starts as Draft. "
        "Leads, sends, and replies are never copied — the duplicate starts with none."
    )
    new_name = st.text_input("New campaign name (letters, numbers, underscores only)",
                              key="duplicate_campaign_new_name")
    confirm = st.checkbox("Duplicate now", key="duplicate_campaign_confirm")

    col1, col2 = st.columns(2)
    with col1:
        duplicate_clicked = st.button("Duplicate Campaign", type="primary", disabled=not confirm)
    with col2:
        if st.button("Cancel", key="cancel_duplicate_campaign"):
            st.session_state["duplicating_campaign"] = None
            st.rerun()

    if duplicate_clicked:
        name_error = validate_campaign_name(new_name, existing_campaigns)
        if name_error:
            st.error(name_error)
        else:
            try:
                client = _get_github_client()
                # Read the SOURCE fresh from GitHub itself, never from
                # Streamlit's own local checkout — that checkout can lag
                # behind a very recent commit until the next redeploy
                # finishes, and reading it here previously produced a
                # duplicate with ZERO template files whenever that lag
                # was in effect, with no error at all.
                filenames = client.list_directory_files(f"templates/{source_campaign_name}")
                source_template_files = {
                    filename: client.get_file_content(f"templates/{source_campaign_name}/{filename}")
                    for filename in filenames if filename.endswith(".txt")
                }

                override_path = f"config/campaigns/{source_campaign_name}.yaml"
                source_raw_override = None
                if client.get_file_sha(override_path) is not None:
                    override_bytes = client.get_file_content(override_path)
                    source_raw_override = yaml.safe_load(override_bytes.decode("utf-8")) or {}

                files = build_campaign_duplication_files(new_name, source_template_files, source_raw_override)
                client.commit_campaign_files_directly(
                    files=files,
                    commit_message=f"Duplicate '{source_campaign_name}' as '{new_name}' "
                                    f"(via Streamlit, by {current_user()})",
                )
                _load_hub_rows.clear()
                st.session_state["duplicating_campaign"] = None
                st.success(
                    f"'{new_name}' created as a Draft copy of '{source_campaign_name}' with "
                    f"{len(source_template_files)} template file(s). It'll appear in the list below "
                    "within a minute or so, once the app finishes redeploying — worth double-checking "
                    "that file count matches what you expected."
                )
            except (GitHubActionsError, ValueError) as exc:
                st.error(f"Failed to duplicate: {exc}")


@st.dialog("New Campaign")
def _new_campaign_dialog(existing_campaigns):
    st.caption(
        "Just the name — you'll write the actual email in the Sequences tab once the campaign exists "
        "(a placeholder Intro is created for you to edit there)."
    )
    campaign_name = st.text_input("Campaign name (letters, numbers, underscores only)")
    confirm = st.checkbox("Create now — it's live immediately, no approval step")

    col1, col2 = st.columns(2)
    with col1:
        create_clicked = st.button("Create Campaign", type="primary", disabled=not confirm)
    with col2:
        if st.button("Cancel"):
            st.session_state["show_new_campaign_dialog"] = False
            st.rerun()

    if create_clicked:
        name_error = validate_campaign_name(campaign_name, existing_campaigns)
        if name_error:
            st.error(name_error)
        else:
            try:
                files = build_campaign_files(
                    campaign_name, "intro",
                    {"A": {"subject": PLACEHOLDER_INTRO_SUBJECT, "body": PLACEHOLDER_INTRO_BODY}},
                )
                client = _get_github_client()
                client.commit_campaign_files_directly(
                    files=files,
                    commit_message=commit_message_for_campaign(campaign_name, "intro", 1, current_user(),
                                                                 is_new_campaign=True),
                )
                _load_hub_rows.clear()
                with st.spinner("Initializing Sheet tabs..."):
                    time.sleep(1)
                    _initialize_campaign_tabs(campaign_name)
                # Deliberately NOT auto-navigating into the new campaign:
                # Streamlit Cloud's local checkout only picks up this commit
                # once it redeploys (triggered by the GitHub webhook, not
                # instant) — jumping straight to the detail view right now
                # would very likely hit "No templates found" against the
                # still-stale local checkout. Staying on the hub and saying
                # so plainly is the honest version of this UX.
                st.session_state["show_new_campaign_dialog"] = False
                st.success(
                    f"'{campaign_name}' created with a placeholder Intro. It'll appear in the list below "
                    "within a minute or so, once the app finishes redeploying — open it and go to "
                    "Sequences to write the real email."
                )
            except GitHubActionsError as exc:
                st.error(f"Failed to create campaign: {exc}")


# =============================================================================
# Hub view — list, search, click into a campaign
# =============================================================================
def _render_hub(just_arrived: bool):
    st.title("Campaigns")

    col1, col2, col3 = st.columns([3, 1, 1])
    with col1:
        search = st.text_input("🔍 Search campaigns...", label_visibility="collapsed",
                                placeholder="🔍 Search campaigns...")
    with col2:
        if st.button("🔄 Refresh", width="stretch", key="refresh_hub_button"):
            _load_hub_rows.clear()
            _fetch_full_campaign_data_cached.clear()
            _get_master_header_cached.clear()
            st.rerun()
    with col3:
        if "show_new_campaign_dialog" not in st.session_state:
            st.session_state["show_new_campaign_dialog"] = False
        if st.button("＋ New Campaign", width="stretch"):
            st.session_state["show_new_campaign_dialog"] = True
        # Re-checked on EVERY rerun, not just the click that opened it —
        # st.dialog only stays visually open across a rerun triggered by a
        # widget INSIDE it if the code path that calls the dialog function
        # is reached again; gating solely on the button's own return value
        # (True only on the exact rerun it was clicked) would silently
        # close the dialog the instant you touched anything inside it.
        #
        # But that sticky flag creates a SECOND problem on its own: it
        # survives navigating to a completely different page and back,
        # since session_state is shared across the whole app — without the
        # check below, the dialog would silently reopen every time you
        # returned to this page, even long after you closed it. Only reset
        # it on a genuine arrival at this page (just_arrived, computed once
        # at module level via mark_active_page), never on a rerun caused by
        # a widget inside the dialog itself.
        if just_arrived:
            st.session_state["show_new_campaign_dialog"] = False
        if st.session_state["show_new_campaign_dialog"]:
            try:
                existing_campaigns = list_campaigns_live(_safe_github_client())
            except Exception:  # noqa: BLE001
                existing_campaigns = []
            _new_campaign_dialog(existing_campaigns)

    if just_arrived:
        st.session_state["duplicating_campaign"] = None
    if st.session_state.get("duplicating_campaign"):
        try:
            existing_campaigns = list_campaigns_live(_safe_github_client())
        except Exception:  # noqa: BLE001
            existing_campaigns = []
        _duplicate_campaign_dialog(st.session_state["duplicating_campaign"], existing_campaigns)

    try:
        rows, deleted_rows, errors = _load_hub_rows()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Couldn't load campaigns: {exc}")
        st.stop()

    rows = filter_campaigns_by_search(rows, search)

    if not rows:
        st.info("No campaigns match yet." if search else "No campaigns yet — create one to get started.")
    else:
        for row in rows:
            with st.container(border=True):
                c1, c2, c3, c4, c5, c6 = st.columns([3, 2, 1, 1, 1, 2])
                c1.markdown(f"**{row['name']}**")
                c2.write(row["status_label"])
                c3.write(f"{row['total_leads']} leads")
                c4.write(f"{row['sent']} sent")
                c5.write(f"{row['replies']} replies")
                c6.write(f"Last activity: {_relative_time(row['last_activity'])}")
                if row["problems"]:
                    c2.caption(", ".join(row["problems"]))
                button_col1, button_col2 = st.columns(2)
                with button_col1:
                    if st.button("Open", key=f"open_{row['name']}", width="stretch"):
                        st.session_state["selected_campaign"] = row["name"]
                        st.rerun()
                with button_col2:
                    if st.button("⧉ Duplicate", key=f"duplicate_{row['name']}", width="stretch"):
                        st.session_state["duplicating_campaign"] = row["name"]
                        st.rerun()

    if errors:
        with st.expander(f"{len(errors)} campaign(s) couldn't be loaded"):
            for name, message in errors:
                st.write(f"**{name}** — {message}")

    if deleted_rows:
        with st.expander(f"🗑️ Deleted Campaigns ({len(deleted_rows)})"):
            st.caption(
                "Temporarily removed — hidden from the list above, but nothing about them changed. "
                "Restore brings a campaign back exactly as it was, including whether it was Running, "
                "Paused, or still a Draft."
            )
            for row in deleted_rows:
                col1, col2 = st.columns([4, 1])
                col1.write(f"**{row['name']}**")
                if col2.button("↩️ Restore", key=f"restore_{row['name']}"):
                    if _restore_campaign(row["name"]):
                        st.success(f"'{row['name']}' restored.")
                        st.rerun()


# =============================================================================
# Detail view — Performance is the first tab; every tab is real.
# =============================================================================
def _render_performance_tab(campaign_cfg, leads, responses, send_log, error_log):
    """This campaign's own performance — the same view as the Performance
    page, drawn by the same renderer, from the data this page has already
    loaded (no extra Sheet reads). It replaces the old Analytics tab and
    keeps everything that tab showed (stage, variant and sender tables, and
    all-time errors) alongside the reply-quality figures."""
    perf = build_campaign_performance(campaign_cfg, leads, responses, send_log, error_log)

    def _refresh_button():
        st.button("⟳ Refresh", key="campaigns_perf_refresh", on_click=_refresh_all_data)

    render_performance(perf, key_prefix=f"campaign_perf_{campaign_cfg['_campaign_name']}",
                       best=build_best_performers([perf], perf), show_campaign_ranking=False,
                       actions=_refresh_button, export_name=campaign_cfg["_campaign_name"])


def _render_diagnose(campaign_cfg, leads):
    """What this campaign's Data is actually reading, with a live (uncached) check — opened from the header's ⋯ menu."""
    with st.expander("🔍 Diagnose: what is this page actually reading?"):
        st.caption(
            "If leads you've confirmed are in the Sheet aren't showing up here, this tells you exactly "
            "which tab this page is reading and whether the problem is a stale cache or something else "
            "entirely — compare the tab name below against the ACTUAL tab name in Google Sheets."
        )
        st.write(f"**Tab this page reads:** `{campaign_cfg['master_tab']}`")
        st.write(f"**Leads currently shown (cached, up to 30s old):** {len(leads)}")
        if st.button("Force a live check (bypasses every cache)", key="data_tab_live_diagnostic"):
            try:
                live_leads = _get_connector().get_all_leads(campaign_cfg["master_tab"])
                st.write(f"**Leads actually on the Sheet right now, read live:** {len(live_leads)}")
                if len(live_leads) != len(leads):
                    st.warning(
                        "These numbers differ — the cache genuinely is stale. Click '🔄 Refresh data' "
                        "above (or reload the page) to clear it."
                    )
                else:
                    st.info(
                        "These numbers match — the cache isn't the problem. If leads you've confirmed "
                        "are in the Sheet still aren't in this count, the issue is which TAB is being "
                        "read (check the tab name above against Google Sheets' actual tab name), not caching."
                    )
            except Exception as exc:  # noqa: BLE001
                st.error(f"Live check failed: {exc}")


def _open_import_panel():
    st.session_state["ld_import_open"] = True


def _close_import_panel():
    st.session_state["ld_import_open"] = False


def _render_data_tab(campaign_cfg, leads):
    campaign_name = campaign_cfg["_campaign_name"]

    def _add_leads_panel():
        with st.expander("➕ Add Leads (upload CSV)", expanded=(not leads) or bool(st.session_state.get("ld_import_open"))):
            st.caption(
                "Your CSV's column headers can be named anything — you'll map them to the right fields "
                "after upload. A minimal example:"
            )
            st.code(
                "FirstName,LastName,Email,Company\n"
                "Rocky,D,rocky@example.com,Example Corp\n"
                "Jane,Smith,jane@example.com,Acme Inc",
                language="csv",
            )
            st.caption(f"{', '.join(KNOWN_FIELDS)} are recognized automatically. Only Email is required — any "
                       "other column becomes a custom field on the lead using its own column name (visible in "
                       "the Master Sheet as extra columns, and picked up automatically by Asana Sync if the "
                       "name matches one of its fields — Client, Product, Content Score, etc.).")
            uploaded = st.file_uploader("Upload CSV", type=["csv"], key="data_csv_upload")
            if uploaded is not None:
                decode_error = None
                try:
                    columns, rows = parse_csv_bytes(uploaded.getvalue())
                except Exception as exc:  # noqa: BLE001 - a bad upload must never crash the whole page
                    # parse_csv_bytes already falls through several encodings
                    # before ever raising, so reaching here means something
                    # genuinely unexpected (e.g. not text data at all) — this
                    # is the backstop, not the primary defense.
                    decode_error = exc
                    columns, rows = [], []
                if decode_error is not None:
                    st.error(f"Couldn't read that file as a CSV: {decode_error}")
                elif not columns:
                    st.error("Couldn't read any columns from that file — is it a valid CSV?")
                else:
                    try:
                        header = _get_master_header_cached(campaign_cfg["_campaign_name"])
                        custom_columns = [c for c in header if c not in outreach.MASTER_COLUMNS]
                    except Exception:  # noqa: BLE001 - tab may not exist yet for a brand new campaign
                        custom_columns = []

                    st.caption(f"{len(rows)} row(s) detected. Every column is mapped automatically below — "
                               "change any of them, or pick Skip to leave one out.")

                    duplicate_columns = find_duplicate_columns(columns)
                    if duplicate_columns:
                        st.warning(
                            f"This CSV has the same column name more than once: {', '.join(duplicate_columns)}. "
                            "Only the LAST one's data survived when the file was read — the earlier column(s) "
                            "with that name are already gone, not just hidden here. Rename one of them in the "
                            "source file and re-upload if you need both."
                        )

                    mapping = {}
                    custom_field_name_errors = []
                    default_mapping = build_default_mapping(columns, custom_columns, outreach.MASTER_COLUMNS)
                    for col_idx, col in enumerate(columns):
                        default = default_mapping.get(col) or "-- Skip --"
                        # The column's own name is always a valid option in ITS
                        # OWN dropdown, even when it isn't yet a known or
                        # existing field — this is what lets a brand-new custom
                        # field default automatically, with no extra clicks or
                        # retyping the same name a second time.
                        col_options = ["-- Skip --"] + KNOWN_FIELDS + custom_columns
                        if default not in col_options and default != "-- Skip --":
                            col_options = col_options + [default]
                        col_options = col_options + [NEW_CUSTOM_FIELD_OPTION]
                        default_idx = col_options.index(default) if default in col_options else 0
                        # Keyed by POSITION as well as name — two columns can
                        # share the same name (see the duplicate-column warning
                        # above), and a key built from the name alone would
                        # crash Streamlit outright rather than just producing
                        # confusing data.
                        choice = st.selectbox(f"'{col}' maps to", col_options, index=default_idx,
                                               key=f"map_{col_idx}_{col}")
                        if choice == NEW_CUSTOM_FIELD_OPTION:
                            new_field_name = st.text_input(f"New field name for '{col}'",
                                                            key=f"map_new_field_{col_idx}_{col}")
                            field_name_error = validate_custom_field_name(new_field_name, outreach.MASTER_COLUMNS)
                            if field_name_error:
                                custom_field_name_errors.append(f"'{col}': {field_name_error}")
                                mapping[col] = ""
                            else:
                                mapping[col] = new_field_name.strip()
                        else:
                            mapping[col] = "" if choice == "-- Skip --" else choice

                    mapped_rows = apply_mapping(rows, mapping)
                    valid_count = count_valid_rows(mapped_rows)
                    mapping_error = validate_mapping(mapping)

                    st.write(f"**{valid_count} of {len(rows)} row(s) have an email and will be imported** "
                             "(others are skipped — Email is required). Duplicates against existing leads "
                             "are also skipped automatically, checked at import time.")
                    st.caption("Imported leads start as **Pending** — approve them below before they're eligible to send.")
                    allow_duplicate_emails = st.checkbox(
                        "Allow re-importing an email that already exists (e.g. contacting the same creator "
                        "again for a different video — each import becomes its own row and, once synced, "
                        "its own separate Asana task; the automated sender still only ever emails the first "
                        "row for a given address, so this never risks a duplicate send)",
                        key="import_allow_duplicate_emails")

                    for field_name_error in custom_field_name_errors:
                        st.error(field_name_error)
                    if mapping_error:
                        st.error(mapping_error)

                    if not custom_field_name_errors and not mapping_error and st.button(
                            "Import Leads", type="primary", key="confirm_import"):
                        try:
                            client = _get_github_client()
                            payload = build_import_payload(mapped_rows, allow_duplicate_emails=allow_duplicate_emails)
                            path = import_payload_path(campaign_name)
                            client.create_file(path, payload_to_bytes(payload),
                                                message=f"Import {valid_count} lead(s) for {campaign_name} "
                                                         f"(via Streamlit, by {current_user()})")
                            time.sleep(1)
                            client.dispatch_workflow(WORKFLOW_IMPORT_LEADS,
                                                      {"campaign": campaign_name, "payload_path": path})
                            st.success(f"Import triggered — {valid_count} lead(s) will appear within a minute or two.")
                        except GitHubActionsError as exc:
                            st.error(f"Import failed: {exc}")

    if not leads:
        st.info("No leads yet — add some below.")
        _add_leads_panel()
        return

    # the search, the status chips and the three extra filters live together in one card
    with st.container(key="ld_toolbar"):
        search_column, add_column = st.columns([7, 1])
        with search_column:
            search_query = st.text_input("Search", key="data_search", placeholder="🔍 Search name, email or company…", label_visibility="collapsed")
        with add_column:
            st.button("＋ Add leads", key="ld_add_leads", on_click=_open_import_panel, use_container_width=True)
        option_counts = filter_counts(leads, FILTER_OPTIONS, filter_leads)
        status_filter = st.radio("Filter", FILTER_OPTIONS, key="data_filter", horizontal=True, label_visibility="collapsed",
                                 format_func=lambda option: f"{option} · {option_counts[option]}")

        stage_names = [s.get("name") for s in (campaign_cfg.get("stages") or [])]
        sender_filter, stage_filter, recency_filter = render_extra_filters(leads, stage_names)

        shown_stage_count = stage_count(stage_names, leads)
        filtered = apply_extra_filters(search_leads(filter_leads(leads, status_filter), search_query), shown_stage_count,
                                       stage_labels(stage_names, shown_stage_count), sender_filter, stage_filter, recency_filter)
        render_clear_filters(any_extra_filter(sender_filter, stage_filter, recency_filter) or bool(search_query.strip()) or status_filter != FILTER_OPTIONS[0])
    if st.session_state.get("ld_import_open"):
        # opened from the toolbar's "＋ Add leads": the same import panel, right under it (it is drawn only once: down at the foot when closed)
        st.button("✕ Close the import panel", key="ld_import_close", on_click=_close_import_panel)
        _add_leads_panel()
    st.caption(f"Showing {len(filtered)} of {len(leads)} lead(s)")

    # The lead table and the side panel for the lead you click (see leads_view). The controls that change one lead (stop
    # sending, Asana stage, remove) are drawn in that panel by leads_actions; importing leads stays in the section above.
    def _lead_actions(lead, key):
        render_lead_actions(lead, key, campaign_name=campaign_name, github_client=_get_github_client, current_user=current_user)

    render_lead_browser(leads=leads, filtered=filtered, search_query=search_query, status_filter=status_filter, stage_names=stage_names,
                        extra_filters=(sender_filter, stage_filter, recency_filter), render_actions=_lead_actions)

    if filtered:
        try:
            header_order = _get_master_header_cached(campaign_name)
        except Exception:  # noqa: BLE001 - fall back to alphabetical if the header can't be read
            header_order = None
        with st.expander("📋 All columns (every field in the Sheet)"):
            st.dataframe(
                build_full_lead_table(filtered, header_order=header_order),
                width="stretch", hide_index=True,
            )

        with st.expander("🗑️ Remove several leads at once"):
            st.caption(
                "To remove just one lead, open it in the table and use the panel on the right. Removed leads are never deleted — "
                "their row and history stay intact, they're just marked Removed and excluded from all future sends."
            )
            options = {f"{l.get('LeadID', '')} — {l.get('FirstName', '')} {l.get('LastName', '')} "
                       f"<{l.get('Email', '')}>": l.get("LeadID", "") for l in filtered}
            selected_labels = st.multiselect("Select leads to remove", list(options.keys()), key="remove_select")
            if selected_labels and st.button("Remove Selected", key="confirm_remove"):
                try:
                    lead_ids = [options[label] for label in selected_labels]
                    client = _get_github_client()
                    payload = build_removal_payload(lead_ids)
                    path = removal_payload_path(campaign_name)
                    client.create_file(path, payload_to_bytes(payload),
                                        message=f"Remove {len(lead_ids)} lead(s) from {campaign_name} "
                                                 f"(via Streamlit, by {current_user()})")
                    time.sleep(1)
                    client.dispatch_workflow(WORKFLOW_REMOVE_LEADS,
                                              {"campaign": campaign_name, "payload_path": path})
                    st.success(f"Removal triggered for {len(lead_ids)} lead(s).")
                except GitHubActionsError as exc:
                    st.error(f"Removal failed: {exc}")



def _fetch_live_stages_and_variants(client, campaign_name, stage_wait_days=None):
    """Re-reads the campaign's template structure fresh from GitHub's
    own API — deliberately NOT from Streamlit's local checkout, which
    can lag behind a recent commit, sometimes for a while rather than
    the few seconds a redeploy is supposed to take. Used to RENDER the
    Sequences tab (so what you see is never stale), immediately before
    Delete Stage / Delete Variant actually execute (so a destructive
    action can never be decided from an outdated view even if it
    somehow slipped past the render), AND to overlay live stage
    discovery onto campaign_cfg itself, since compute_campaign_status
    (and every other stage-aware computation) reads
    campaign_cfg["stages"], which get_campaign_cfg builds from the same
    frozen local template listing. A campaign whose only stage (intro)
    was fully sent correctly showed Completed; adding a follow-up stage
    24+ hours later committed the new template files fine, and the
    Sequences tab even showed them correctly (already live) — but the
    STATUS never left Completed, because nothing about status
    computation had ever been told to look at GitHub instead of the
    stale local file list.

    stage_wait_days defaults to {} for the Sequences-tab callers, which
    only care which stages/variants exist, not their timing."""
    filenames = client.list_directory_files(f"templates/{campaign_name}")
    return outreach.parse_stages_and_variants_from_filenames(filenames, stage_wait_days or {})


def _live_stage_wait_days(campaign_cfg=None):
    """The follow-up waits for a campaign: the shared table in settings.yaml (safe to read from the local checkout, because the app never writes that file,
    so it cannot go stale), with this campaign's OWN waits laid over it. Those are already live: campaign_cfg has had the live override file merged in."""
    settings = outreach.load_settings(SETTINGS_PATH)
    waits = dict(settings.get("default_campaign_settings", {}).get("stage_wait_days", {}))
    own = (campaign_cfg or {}).get("stage_wait_days")
    if isinstance(own, dict):
        try:
            waits.update(outreach.parse_stage_wait_overrides((campaign_cfg or {}).get("_campaign_name", ""), own))
        except outreach.ConfigError:
            pass                                                         # a value the engine would refuse is not shown; the engine reports it itself
    return waits


def _fetch_live_template_content(client, campaign_name, stage_prefix, variant):
    """Reads one template file's current subject+body fresh from
    GitHub's own API — same "never trust the local checkout" reasoning
    as _fetch_live_stages_and_variants, extended to template CONTENT,
    not just which stages/variants exist. Without this, opening a
    template to edit it shortly after saving a previous change (or
    after anyone else's recent save) could show a stale version — and
    saving from there would silently overwrite the real, newer content
    with that stale copy."""
    content_bytes = client.get_file_content(f"templates/{campaign_name}/{stage_prefix}_{variant}.txt")
    return outreach.parse_template_content(content_bytes.decode("utf-8"))


def _render_sequences_tab(campaign_cfg, leads, responses=None, send_log=None, error_log=None, just_arrived=False):
    """The Sequences tab (see sequences_view for the layout and what each action does). The page supplies its own live readers and the
    GitHub writer; the live results shown per variant come from the same Performance figures as the Performance tab."""
    try:
        perf = build_campaign_performance(campaign_cfg, leads, responses or [], send_log or [], error_log or [])
    except Exception:  # noqa: BLE001 - the results beside a variant are a bonus; the editor must work without them
        perf = None
    render_sequences_tab(SequencesContext(
        campaign_cfg=campaign_cfg, leads=leads, perf=perf, just_arrived=just_arrived, get_client=_get_github_client,
        fetch_structure=_fetch_live_stages_and_variants, fetch_content=_fetch_live_template_content, current_user=current_user,
    ))


@st.cache_resource(ttl=300, show_spinner=False)
def _load_account_health():
    """{account name: {status, detail, checked_at}} from the Email Accounts Health tab (written by the health check). Held for five minutes in a resource cache,
    which a refresh of the page's own data (the 30-second data cache) does not clear: a deployment whose health tab does not exist yet cannot cache a failed lookup,
    so a data refresh must not repeat it. An empty answer on any failure: the Settings tab must never break because a health lookup did."""
    try:
        return build_health_lookup(_get_connector().get_account_health())
    except Exception:  # noqa: BLE001 - no health data just means 'Not checked yet' on each account
        return {}


def _read_intro_texts(campaign_cfg):
    """The intro template (subject and body) of every variant, for the Readiness check of its placeholders. None when they cannot be read right now (no GitHub
    access, a missing file): the checklist then says it could not check, rather than guessing."""
    client = _safe_github_client()
    stages = campaign_cfg.get("stages") or []
    if client is None or not stages:
        return None
    prefix = stages[0].get("template_prefix") or stages[0].get("name")
    try:
        texts = []
        for variant in campaign_cfg.get("variants") or []:
            parsed = outreach.parse_template_content(client.get_file_content(f"templates/{campaign_cfg['_campaign_name']}/{prefix}_{variant}.txt").decode("utf-8"))
            texts.append(f"{parsed['subject']}\n{parsed['body']}")
        return texts
    except Exception:  # noqa: BLE001 - unreadable templates are reported as 'couldn't check', never as a failure
        return None


def _render_settings_tab(campaign_cfg, leads, send_log=None):
    """The Settings tab (see settings_view for the layout and what saving does). The page supplies the account directory (the legacy secrets and the live slot file
    merged, so an account added on the Email Accounts page shows up here too), and every action that reaches outside the page."""
    streamlit_secret_directory = dict(st.secrets.get("email_accounts_directory", {}))
    # LIVE read — an account added via Email Accounts commits this file to GitHub, but the local checkout stays frozen until redeploy, so a new account never
    # appeared in this picker. See read_slot_mapping_live.
    slot_mapping = read_slot_mapping_live(_safe_github_client(), EMAIL_ACCOUNT_SLOT_MAPPING_ABS_PATH)
    account_directory = merge_account_directories(streamlit_secret_directory, slot_mapping)
    status, _problems = compute_campaign_status(campaign_cfg, leads)

    def extras():
        # Not in the design, kept exactly as they were: collapsed tools for this campaign.
        _render_maintenance_section_in_settings(campaign_cfg)
        _render_asana_sync_section_in_settings(campaign_cfg)
        _render_tracker_sync_section_in_settings(campaign_cfg)

    render_settings_tab(SettingsContext(
        campaign_cfg=campaign_cfg, leads=leads or [], send_log=list(send_log or []), status=status, status_label=status_label(status), account_directory=account_directory,
        health=_load_account_health(), intro_texts=_read_intro_texts(campaign_cfg), get_client=_get_github_client, current_user=current_user, campaigns_dir=CAMPAIGNS_DIR,
        update_status=_update_campaign_status, request_launch=lambda: st.session_state.__setitem__("show_launch_confirm", True), archive=_temporarily_remove_campaign,
        files_to_delete=lambda name: list_campaign_files_to_delete(name, TEMPLATES_ROOT, CAMPAIGNS_DIR), name_matches=confirmation_matches_campaign_name, extras=extras,
        default_account=campaign_cfg.get("default_sender_account") or campaign_cfg.get("_global_default_account") or "",
    ))


def _render_schedule_tab(campaign_cfg, leads=None, send_log=None):
    """The Schedule tab (see schedule_view for the layout and what saving does). The page supplies its own GitHub writer, and — for the 'Next automatic run'
    card — when the sender will next run, worked out with the engine's own sending-window rule."""
    status, _problems = compute_campaign_status(campaign_cfg, leads or [])
    running = status == STATUS_RUNNING
    next_run = None
    if running:
        try:
            minute = _auto_send_cron_minute()
            if minute is not None:
                next_run = next_automatic_send(campaign_cfg.get("schedule") or {}, datetime.now(timezone.utc), minute, outreach.is_within_sending_window)
        except Exception:  # noqa: BLE001 - a card that cannot be worked out shows its 'nothing due' note; it must never break the tab
            next_run = None
    notes = {STATUS_PAUSED: "Paused: nothing is scheduled", STATUS_DRAFT: "Draft: not launched yet", STATUS_COMPLETED: "Completed: nothing more to send"}
    render_schedule_tab(ScheduleContext(
        campaign_cfg=campaign_cfg, leads=leads or [], send_log=send_log or [], running=running, status_note=notes.get(status, "Nothing is scheduled"),
        next_run=next_run, get_client=_get_github_client, current_user=current_user, campaigns_dir=CAMPAIGNS_DIR,
    ))


def _read_state():
    """The read/unread state the main Responses page uses (the very same session entries), so a reply read in either place is read in both: the replies
    marked read this session, and those still waiting to be written to the Sheet. (Each reply's own IsRead column in the Sheet is the lasting record.)"""
    return st.session_state.setdefault("read_response_keys", set()), st.session_state.setdefault("pending_sync_keys", set())


def _render_sync_read_status(campaign_name):
    """The same 'Sync read status' step as the Responses page, for this campaign's replies only: reading is remembered here at once, and this writes it
    to the Sheet (through the mark-read workflow) so it lasts."""
    _read, pending = _read_state()
    mine = split_keys_by_campaign(pending).get(campaign_name, [])
    if not mine or not st.button(f"🔄 Sync read status ({len(mine)} pending)", key=f"sync_read_status_{campaign_name}"):
        return
    try:
        payload = build_mark_read_payload(mine)
        path = f"mark_read/{campaign_name}/{time.strftime('%Y-%m-%d-%H%M%S')}.json"
        client = _get_github_client()
        client.create_file(path, payload_to_bytes(payload), message=f"Mark {len(mine)} response(s) read in {campaign_name} (via Streamlit, by {current_user()})")
        client.dispatch_workflow(WORKFLOW_MARK_RESPONSES_READ, {"campaign": campaign_name, "payload_path": path})
        pending.difference_update({f"{campaign_name}:{response_id}" for response_id in mine})
        st.success(f"Synced read status for {len(mine)} reply(ies).")
    except GitHubActionsError as exc:
        st.error(f"Couldn't sync read status: {exc}")


def _render_inbox_for_campaign(campaign_cfg, leads, responses):
    """This campaign's replies in the two-pane Inbox layout — the same view as the Responses page, scoped to this campaign, with the same read/unread
    tracking: unread replies are marked, opening a conversation reads it, and the tab's badge counts what is still unread."""
    campaign_name = campaign_cfg["_campaign_name"]
    read_keys, pending_sync_keys = _read_state()
    _render_sync_read_status(campaign_name)
    tagged = tag_responses_with_campaign(responses, campaign_name)
    query = st.text_input("🔍 Search this campaign's replies (sender, subject, snippet)",
                          key=f"responses_search_{campaign_name}")
    groups = sort_conversation_groups_newest_first(group_responses_by_lead(search_responses(tagged, query)))
    if not groups:
        st.info("No responses match this search.")
        return
    render_inbox(groups, InboxContext(
        namespace=f"tab_{campaign_name}",
        get_campaign_cfg=lambda _name: campaign_cfg,
        leads_for=lambda _name: leads,
        responses_for=lambda name, lead_id: filter_responses_for_lead(tagged, lead_id, name),
        github_client=_get_github_client,
        current_user=current_user,
        read_keys=read_keys,
        pending_sync_keys=pending_sync_keys,
        show_campaign=False,
        pause_before_dispatch=1.0,
        sent_for=lambda name, lead_id: filter_sent_mail_for_lead(_load_sent_mail_cached(name), lead_id),
    ))


def _render_responses_tab(campaign_cfg, leads, responses):
    if not responses:
        st.info("No responses yet.")
        return

    _render_inbox_for_campaign(campaign_cfg, leads, responses)


def _update_campaign_status(campaign_name: str, new_status: str) -> bool:
    """Returns True on success. Clears the hub cache too, so the status
    badge on the campaign LIST reflects this once the redeploy delay has
    passed — same "may take a minute" caveat as every other config write
    in this app."""
    try:
        client = _get_github_client()
        raw_override = load_raw_override_live(campaign_name, client, CAMPAIGNS_DIR)           # the LIVE file: a setting saved a moment ago must not be undone
        updated = build_status_override(raw_override, new_status)
        client.create_file(
            override_file_path(campaign_name), override_to_yaml_bytes(updated),
            message=f"Set status={new_status} for {campaign_name} (via Streamlit, by {current_user()})",
        )
        _load_hub_rows.clear()
        return True
    except GitHubActionsError as exc:
        st.error(f"Failed to update status: {exc}")
        return False


def _temporarily_remove_campaign(campaign_name: str, current_status: str) -> bool:
    """Records current_status (draft/active/paused) so Restore can bring
    the campaign back exactly as it was — see
    launch_logic.build_delete_override's docstring."""
    try:
        client = _get_github_client()
        raw_override = load_raw_override_live(campaign_name, client, CAMPAIGNS_DIR)           # the LIVE file: a setting saved a moment ago must not be undone
        updated = build_delete_override(raw_override, current_status)
        client.create_file(
            override_file_path(campaign_name), override_to_yaml_bytes(updated),
            message=f"Temporarily remove {campaign_name} (via Streamlit, by {current_user()})",
        )
        _load_hub_rows.clear()
        return True
    except GitHubActionsError as exc:
        st.error(f"Failed to remove: {exc}")
        return False


def _restore_campaign(campaign_name: str) -> bool:
    """Restores whatever status was recorded at Temporarily Remove time —
    see launch_logic.build_restore_override's docstring."""
    try:
        client = _get_github_client()
        raw_override = load_raw_override_live(campaign_name, client, CAMPAIGNS_DIR)           # the LIVE file: a setting saved a moment ago must not be undone
        updated = build_restore_override(raw_override)
        client.create_file(
            override_file_path(campaign_name), override_to_yaml_bytes(updated),
            message=f"Restore {campaign_name} (via Streamlit, by {current_user()})",
        )
        _load_hub_rows.clear()
        return True
    except GitHubActionsError as exc:
        st.error(f"Failed to restore: {exc}")
        return False


def _render_status_controls(campaign_cfg, leads, just_arrived: bool, send_log=()):
    """The campaign's title bar — its name, its status, chips for its schedule and senders, and the Launch / Pause / Resume button —
    followed by what is wrong with it (if anything) and how many emails went out today. (These are the same elements as ever, the
    title, the status heading and the same buttons, grouped in named containers so the page's stylesheet can lay them out in a row.)"""
    campaign_name = campaign_cfg["_campaign_name"]
    status, problems = compute_campaign_status(campaign_cfg, leads)

    launch_confirm_key = "show_launch_confirm"
    if launch_confirm_key not in st.session_state:
        st.session_state[launch_confirm_key] = False
    if just_arrived:
        # Same lesson as the New Campaign dialog: a confirmation box driven
        # by a sticky session_state flag must be reset on genuine
        # navigation, or it silently reappears on a later, unrelated visit.
        st.session_state[launch_confirm_key] = False

    with st.container(key="cp_titlebar"):
        st.title(campaign_name)
        st.subheader(status_label(status))
        st.markdown(chips_html(status, [schedule_summary(campaign_cfg), sender_summary(leads)]), unsafe_allow_html=True)
        with st.container(key="cp_actions"):
            if status == STATUS_DRAFT:
                if st.button("🚀 Launch", type="primary"):
                    st.session_state[launch_confirm_key] = True
            elif status in (STATUS_RUNNING, STATUS_ATTENTION):
                if st.button("⏸ Pause"):
                    if _update_campaign_status(campaign_name, "paused"):
                        st.success("Paused. May take a minute to reflect here while the app redeploys.")
            elif status == STATUS_PAUSED:
                if st.button("▶ Resume"):
                    if _update_campaign_status(campaign_name, "active"):
                        st.success("Resumed. May take a minute to reflect here while the app redeploys.")
            with st.container(key="cp_send"):
                # The very same Send controls as ever (stage, variant, typed SEND confirmation, the same safety checks and the
                # same widget keys), opened from the header instead of sitting at the foot of Settings. Not running: it says why.
                with st.popover("➤ Send now"):
                    _render_send_section_in_settings(campaign_cfg, leads)
            with st.container(key="cp_more"):
                with st.popover("⋯"):
                    _render_more_menu(campaign_cfg, leads)
    if problems:
        st.caption("⚠️ " + " · ".join(problems))
    next_line = None
    if status == STATUS_RUNNING:
        try:
            next_line = next_send_line(campaign_cfg, datetime.now(timezone.utc), _auto_send_cron_minute(), outreach.is_within_sending_window)
        except Exception:  # noqa: BLE001 - a line that cannot be worked out is left out; it must never break the page
            next_line = None
    st.markdown(subline_html(subline(campaign_cfg, list(send_log), None, next_line)), unsafe_allow_html=True)

    if status == STATUS_DRAFT and st.session_state[launch_confirm_key]:
        ready, launch_problems = compute_campaign_readiness(campaign_cfg, leads)
        st.warning(
            "**Launch this campaign?** This makes it active and allows Send Batch to run for it "
            "(still respecting its schedule and daily limits, if any)."
        )
        if not ready:
            st.caption(
                "Heads up, not a blocker: " + ", ".join(launch_problems) +
                " — Send simply won't do anything until this is resolved."
            )
        confirm_col1, confirm_col2 = st.columns(2)
        with confirm_col1:
            if st.button("Confirm Launch", type="primary", key="confirm_launch_button"):
                if _update_campaign_status(campaign_name, "active"):
                    st.session_state[launch_confirm_key] = False
                    st.success("Launched. May take a minute to reflect here while the app redeploys.")
        with confirm_col2:
            if st.button("Cancel", key="cancel_launch_button"):
                st.session_state[launch_confirm_key] = False
                st.rerun()


def _render_asana_sync_section_in_settings(campaign_cfg):
    """Enable/configure syncing this campaign's leads to an Asana
    project, and trigger an on-demand sync. The actual sync (create/
    update tasks, decide which stage a task belongs in, never create a
    duplicate) all happens in sync_asana.yml / outreach.py — this is
    only ever the settings + trigger, matching every other "does real
    work elsewhere" action in this app."""
    campaign_name = campaign_cfg["_campaign_name"]
    asana_settings = campaign_cfg.get("asana") or {}
    with st.expander("🔗 Asana Sync", expanded=bool(asana_settings.get("enabled"))):
        st.caption(
            "Creates or updates one Asana task per lead — never a duplicate, since each lead's task is "
            "tracked once it's first created. A lead's stage (Sourced / Outreach Sent / Follow-up / "
            "Negotiating) is set automatically from its send/reply history; Rights Secured and "
            "Declined / Dead are only ever set by hand in Asana and are never overwritten by a sync."
        )
        enabled = st.checkbox("Enable Asana sync for this campaign", value=bool(asana_settings.get("enabled")),
                               key="asana_sync_enabled")
        project_name = st.text_input("Asana project name (exact match)",
                                      value=asana_settings.get("project_name", ""), key="asana_sync_project_name")

        if st.button("💾 Save Asana Settings", key="asana_sync_save"):
            if enabled and not project_name.strip():
                st.error("Enter the Asana project name, or uncheck Enable.")
            else:
                try:
                    client = _get_github_client()
                    raw_override = load_raw_override_live(campaign_name, client, CAMPAIGNS_DIR)             # the LIVE file, not a possibly stale local copy
                    updated = build_asana_settings_override(raw_override, enabled, project_name.strip())
                    client.create_file(
                        override_file_path(campaign_name), override_to_yaml_bytes(updated),
                        message=f"Update Asana sync settings for {campaign_name} (via Streamlit, "
                                f"by {current_user()})",
                    )
                    st.success("Asana settings saved. May take a minute to reflect here while the app redeploys.")
                except GitHubActionsError as exc:
                    st.error(f"Failed to save: {exc}")

        if asana_settings.get("enabled"):
            if st.button("🔄 Sync to Asana Now", key="asana_sync_now"):
                try:
                    client = _get_github_client()
                    client.dispatch_workflow(WORKFLOW_SYNC_ASANA, {"campaign": campaign_name})
                    st.success(f"Asana sync triggered for '{campaign_name}'. Check the Actions tab for progress.")
                except GitHubActionsError as exc:
                    st.error(f"Failed to trigger sync: {exc}")
        else:
            st.caption("Enable and save first to sync.")


def _render_tracker_sync_section_in_settings(campaign_cfg):
    """Enable/configure syncing this campaign's leads to the Creator
    Tracker sheet, and trigger an on-demand sync — a fully separate
    section from Asana Sync, with its own expander, its own enable
    toggle, and its own trigger, since a campaign may want either
    without the other. The actual sync happens in the same
    sync_asana.yml / outreach.py entry point (which runs Asana sync and
    Creator Tracker sync independently of each other), matching every
    other "does real work elsewhere" action in this app."""
    campaign_name = campaign_cfg["_campaign_name"]
    tracker_settings = campaign_cfg.get("tracker_sync") or {}
    with st.expander("📊 Creator Tracker Sync", expanded=bool(tracker_settings.get("enabled"))):
        st.caption(
            "A separate, shared spreadsheet (its ID and worksheet name are set once as GitHub "
            "secrets, not configured per campaign) that keeps its own 'Contact Status' and 'Last "
            "Contacted Date' columns in sync — nothing else in that sheet is ever touched. Matches "
            "by Creator (the @handle) first, falling back to full name — a match against more than "
            "one row in that sheet is reported for review rather than guessed at. Rights Secured and "
            "Declined / Dead freeze the whole row here too, same as Asana."
        )
        tracker_enabled = st.checkbox("Enable Creator Tracker sync for this campaign",
                                       value=bool(tracker_settings.get("enabled")),
                                       key="tracker_sync_enabled")
        if st.button("💾 Save Creator Tracker Settings", key="tracker_sync_save"):
            try:
                client = _get_github_client()
                raw_override = load_raw_override_live(campaign_name, client, CAMPAIGNS_DIR)             # the LIVE file, not a possibly stale local copy
                updated = build_tracker_sync_settings_override(raw_override, tracker_enabled)
                client.create_file(
                    override_file_path(campaign_name), override_to_yaml_bytes(updated),
                    message=f"Update Creator Tracker sync settings for {campaign_name} (via Streamlit, "
                            f"by {current_user()})",
                )
                st.success("Creator Tracker settings saved. May take a minute to reflect here while "
                           "the app redeploys.")
            except GitHubActionsError as exc:
                st.error(f"Failed to save: {exc}")

        if tracker_settings.get("enabled"):
            if st.button("🔄 Sync Creator Tracker Sheet Now", key="tracker_sync_now"):
                try:
                    client = _get_github_client()
                    client.dispatch_workflow(WORKFLOW_SYNC_ASANA, {"campaign": campaign_name})
                    st.success(f"Creator Tracker sync triggered for '{campaign_name}'. Check the "
                               "Actions tab for progress.")
                except GitHubActionsError as exc:
                    st.error(f"Failed to trigger sync: {exc}")
        else:
            st.caption("Enable and save first to sync.")


def _render_send_section_in_settings(campaign_cfg, leads):
    """Called from the END of _render_settings_tab — sending config
    (limits, rotation) already lives there, so the actual Send action
    belongs alongside it rather than in its own separate place. Daily
    limit, per-account limit, and sender rotation are NOT repeated here
    as a separate "override" — Send always uses whatever's actually
    configured just above; a second, parallel set of the same fields
    would just invite them to drift out of sync with each other.

    Only usable while the campaign is actually Running — matches
    outreach.send_batch's own status guard (which blocks Draft and
    Paused at the source, regardless of what this UI shows), so a
    Completed or Attention-needed campaign is never even offered a Send
    button that would just fail or no-op."""
    campaign_name = campaign_cfg["_campaign_name"]
    STAGES = ["intro", "followup1", "followup2", "followup3", "followup4"]
    VARIANTS = ["Auto", "A", "B", "C", "D"]

    st.subheader("Send")

    status, _ = compute_campaign_status(campaign_cfg, leads)
    if status != STATUS_RUNNING:
        reason = {
            STATUS_DRAFT: "Launch it above (in the header) to enable sending.",
            STATUS_PAUSED: "Resume it above (in the header) to enable sending.",
            STATUS_COMPLETED: "Every approved lead has already finished — there's nothing left to send.",
            STATUS_ATTENTION: "Fix the issue shown above the tabs, then this switches to Running automatically.",
        }.get(status, "")
        st.info(f"Sending is only available while a campaign is {status_label(STATUS_RUNNING)} — this one "
                f"is currently {status_label(status)}. {reason}")
        return

    st.caption(
        "Triggers the real `send_batch.yml` workflow — same safety checks, same typed confirmation. Uses "
        "the daily limit, per-account limit, and sender rotation set in the Settings tab, not a separate override."
    )
    stage_s = st.selectbox("Stage", STAGES, key="campaigns_send_stage")
    variant_s = st.selectbox("Variant", VARIANTS, key="campaigns_send_variant")
    ignore_wait_days_send = st.checkbox(
        "Ignore the scheduled wait for this stage (send now regardless of schedule)",
        key="campaigns_send_ignore_wait_days",
    )

    st.warning("This will send real emails. Run Preview for this exact batch first if you haven't already.")
    confirm_text = st.text_input('Type "SEND" to confirm', key="campaigns_send_confirm_text")

    if st.button("Send Batch", type="primary", key="campaigns_send_batch_button"):
        if not confirmation_is_valid(confirm_text):
            st.error('You must type "SEND" (exact match) to confirm.')
        else:
            # batch_size = "how many to attempt this run" — with no separate
            # UI field for it anymore, defaulting to the campaign's own
            # configured daily limit means one Send click naturally
            # attempts up to the full day's allowance in one go, which is
            # what "just send it" almost always means in practice.
            batch_size = int(campaign_cfg.get("sending", {}).get("daily_limit", 100))
            inputs = build_send_inputs(
                campaign=campaign_name, stage=stage_s, batch_size=batch_size, variant=variant_s,
                ignore_wait_days=ignore_wait_days_send,
            )
            try:
                client = _get_github_client()
                run_details = client.dispatch_workflow(WORKFLOW_SEND, inputs)
                if run_details is None:
                    time.sleep(2)
                    run_details = client.find_recent_run(WORKFLOW_SEND)
                if run_details:
                    st.session_state["last_send_run_id"] = run_details.get("id") or run_details.get("run_id")
                    st.session_state["last_send_run_url"] = run_details.get("html_url", "")
                st.success(f"Triggered Send for '{campaign_name}' / stage '{stage_s}', by {current_user()}.")
            except GitHubActionsError as exc:
                st.error(f"Failed to trigger Send: {exc}")

    run_id = st.session_state.get("last_send_run_id")
    if run_id:
        st.divider()
        st.write(f"**Last triggered run:** [{run_id}]({st.session_state.get('last_send_run_url', '')})")
        if st.button("🔄 Refresh run status", key="campaigns_send_refresh_status"):
            try:
                run = _get_github_client().get_run(run_id)
                status = run.get("status", "unknown")
                conclusion = run.get("conclusion")
                st.success(f"Completed — conclusion: {conclusion}") if status == "completed" \
                    else st.info(f"Status: {status}")
            except GitHubActionsError as exc:
                st.error(f"Failed to fetch run status: {exc}")


def _render_check_replies_menu_item(campaign_cfg):
    """Check for new replies right now (they are also checked automatically once an hour), and follow that run. This sits in the header's ⋯ menu:
    it is an occasional action, not something the Responses tab needs to carry above the replies."""
    campaign_name = campaign_cfg["_campaign_name"]
    if st.button("📥 Check Replies Now", key="campaigns_check_replies_button", use_container_width=True):
        try:
            client = _get_github_client()
            inputs = build_check_replies_inputs(campaign_name)
            run_details = client.dispatch_workflow(WORKFLOW_CHECK_REPLIES, inputs)
            if run_details is None:
                time.sleep(2)
                run_details = client.find_recent_run(WORKFLOW_CHECK_REPLIES)
            if run_details:
                st.session_state["last_replies_run_id"] = run_details.get("id") or run_details.get("run_id")
                st.session_state["last_replies_run_url"] = run_details.get("html_url", "")
            st.success(f"Triggered Check Replies for '{campaign_name}'.")
        except GitHubActionsError as exc:
            st.error(f"Failed to trigger Check Replies: {exc}")
    st.caption("Replies are also checked automatically every hour. Use this to check right now.")

    replies_run_id = st.session_state.get("last_replies_run_id")
    if replies_run_id:
        st.write(f"**Last triggered run:** [{replies_run_id}]({st.session_state.get('last_replies_run_url', '')})")
        if st.button("🔄 Refresh run status", key="campaigns_replies_refresh_status", use_container_width=True):
            try:
                run = _get_github_client().get_run(replies_run_id)
                status = run.get("status", "unknown")
                conclusion = run.get("conclusion")
                st.success(f"Completed — conclusion: {conclusion}. Results are in the Responses tab.") \
                    if status == "completed" else st.info(f"Status: {status}")
            except GitHubActionsError as exc:
                st.error(f"Failed to fetch run status: {exc}")


def _render_more_menu(campaign_cfg, leads):
    """The header's ⋯ menu, in three labelled groups: refreshing the data, checking for replies, and the diagnostic tool."""
    st.markdown('<div class="cp-menu-label">Data</div>', unsafe_allow_html=True)
    with st.container(key="cp_menu_data"):
        if st.button("🔄 Refresh data", key="campaigns_refresh_data", use_container_width=True):
            _refresh_all_data()
        st.caption("Re-read this campaign's Google Sheets now.")
    st.divider()
    st.markdown('<div class="cp-menu-label">Replies</div>', unsafe_allow_html=True)
    with st.container(key="cp_menu_replies"):
        _render_check_replies_menu_item(campaign_cfg)
    st.divider()
    st.markdown('<div class="cp-menu-label">Troubleshooting</div>', unsafe_allow_html=True)
    _render_diagnose(campaign_cfg, leads)


def _render_maintenance_section_in_settings(campaign_cfg):
    """Called from _render_settings_tab — moved out of Sequences since it
    was a template-editing tab and this isn't a template edit, it's a
    rare, likely one-time repair tool (new sends populate ThreadSubject
    automatically; this only fixes leads that were already mid-sequence
    before that existed). Collapsed by default so it doesn't compete for
    attention with everyday settings."""
    campaign_name = campaign_cfg["_campaign_name"]
    with st.expander("🔧 Maintenance: Backfill ThreadSubject"):
        st.caption(
            "For leads already mid-sequence before ThreadSubject existed. Never overwrites an "
            "already-set ThreadSubject — safe to run more than once, it only fills gaps. New sends "
            "populate this automatically — you likely only ever need this once, if at all."
        )
        dry_run = st.checkbox("Dry run (preview only, don't write anything)", value=True,
                               key="campaigns_backfill_dry_run")

        if st.button("Run Backfill", key="campaigns_run_backfill"):
            try:
                client = _get_github_client()
                inputs = build_backfill_thread_subject_inputs(campaign_name, dry_run=dry_run)
                run_details = client.dispatch_workflow(WORKFLOW_BACKFILL_THREAD_SUBJECT, inputs)
                if run_details is None:
                    time.sleep(2)
                    run_details = client.find_recent_run(WORKFLOW_BACKFILL_THREAD_SUBJECT)
                if run_details:
                    st.session_state["last_backfill_run_id"] = run_details.get("id") or run_details.get("run_id")
                    st.session_state["last_backfill_run_url"] = run_details.get("html_url", "")
                st.success(f"Triggered backfill for '{campaign_name}'" + (" (dry run)" if dry_run else "") + ".")
            except GitHubActionsError as exc:
                st.error(f"Failed to trigger backfill: {exc}")

        backfill_run_id = st.session_state.get("last_backfill_run_id")
        if backfill_run_id:
            st.write(f"**Last triggered run:** "
                     f"[{backfill_run_id}]({st.session_state.get('last_backfill_run_url', '')})")
            if st.button("🔄 Refresh run status", key="campaigns_backfill_refresh_status"):
                try:
                    run = _get_github_client().get_run(backfill_run_id)
                    status = run.get("status", "unknown")
                    conclusion = run.get("conclusion")
                    st.success(f"Completed — conclusion: {conclusion}.") if status == "completed" \
                        else st.info(f"Status: {status}")
                except GitHubActionsError as exc:
                    st.error(f"Failed to fetch run status: {exc}")


def _refresh_all_data():
    """Forget every cached read of this campaign's Sheets so the next draw reads them again."""
    _fetch_full_campaign_data_cached.clear()
    _get_master_header_cached.clear()
    _load_sent_mail_cached.clear()


def _auto_send_cron_minute():
    """The minute past each hour the auto-send workflow runs at, read from its own file (None if it can't be read or isn't plain hourly)."""
    try:
        with open(os.path.join(REPO_ROOT, ".github", "workflows", "auto_send.yml"), encoding="utf-8") as handle:
            return read_cron_minute(handle.read())
    except OSError:
        return None


def _render_campaign_detail(campaign_name: str, just_arrived: bool):
    st.markdown(f"<style>{CAMPAIGN_SKIN_CSS}</style>", unsafe_allow_html=True)
    with st.container(key="cp_crumb"):
        with st.container(key="cp_back"):
            if st.button("← Campaigns"):
                del st.session_state["selected_campaign"]
                st.rerun()
        st.markdown(crumb_html(campaign_name), unsafe_allow_html=True)

    try:
        campaign_cfg = get_campaign_cfg(campaign_name)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Couldn't load '{campaign_name}': {exc}")
        return

    # Live overlay applied ONCE, here, upstream of EVERYTHING on this page
    # (the Draft banner, Status controls, and all six tabs) — deliberately
    # not repeated per-tab. Without this, pausing a campaign committed
    # correctly to GitHub, but this page read campaign_cfg straight from
    # the local checkout with no overlay at all, so Pause/Resume never
    # reflected in the UI no matter how long you waited.
    campaign_cfg = merge_live_override_into_cfg(
        campaign_cfg, load_raw_override_live(campaign_name, _safe_github_client(), CAMPAIGNS_DIR))

    # Live stage/variant discovery, overlaid the same way and at the same
    # single point as the override above. A campaign whose only stage was
    # fully sent correctly showed Completed — adding a new follow-up stage
    # afterward committed the template files fine (the Sequences tab even
    # showed them, since IT already read live), but compute_campaign_status
    # and everything else reads campaign_cfg["stages"], which get_campaign_cfg
    # built from the same frozen local template listing this page's OTHER
    # live reads exist specifically to avoid. Falls back to the existing
    # (locally-discovered) stages/variants on any API failure, or if the
    # live listing is empty/invalid — a page must never break or go blank
    # because a live re-discovery call failed.
    _github_client_for_stages = _safe_github_client()
    if _github_client_for_stages is not None:
        try:
            live_stages, live_variants = _fetch_live_stages_and_variants(
                _github_client_for_stages, campaign_name, _live_stage_wait_days(campaign_cfg))
            if live_stages and live_variants:
                campaign_cfg["stages"] = live_stages
                campaign_cfg["variants"] = live_variants
        except Exception:  # noqa: BLE001 - network/auth/parsing failure, keep the local fallback
            pass

    is_draft = (campaign_cfg.get("status") or "active") == "draft"
    load_warning = None
    try:
        leads, responses, send_log, error_log, fetched_at = _fetch_full_campaign_data_cached(campaign_name)
        st.session_state["_data_fetched_at"] = fetched_at
    except Exception as exc:  # noqa: BLE001 - genuinely no Sheet tab yet for a never-touched campaign
        if not is_draft:
            load_warning = f"Couldn't load Sheet data yet: {exc}"
        leads, responses, send_log, error_log = [], [], [], []
        st.session_state["_data_fetched_at"] = None

    _render_status_controls(campaign_cfg, leads, just_arrived, send_log)
    if is_draft:
        st.info("📝 Draft — this campaign hasn't been launched yet.")
    if load_warning:
        st.warning(load_warning)

    read_keys, _pending = _read_state()
    unread = count_unread_conversations(tag_responses_with_campaign(responses, campaign_name), read_keys)         # leads with something unread, not emails
    tabs = st.tabs(["Performance", "Data", "Sequences", "Schedule", "Settings", responses_tab_label(unread)])
    with tabs[0]:
        _render_performance_tab(campaign_cfg, leads, responses, send_log, error_log)
    with tabs[1]:
        _render_data_tab(campaign_cfg, leads)
    with tabs[2]:
        _render_sequences_tab(campaign_cfg, leads, responses, send_log, error_log, just_arrived)
    with tabs[3]:
        _render_schedule_tab(campaign_cfg, leads, send_log)
    with tabs[4]:
        _render_settings_tab(campaign_cfg, leads, send_log)
    with tabs[5]:
        _render_responses_tab(campaign_cfg, leads, responses)
    st.markdown(build_html(UI_BUILD), unsafe_allow_html=True)


# =============================================================================
# Called exactly once per page visit, before branching into hub vs detail —
# both the New Campaign dialog (hub) and the Launch confirmation (detail)
# rely on this to tell "just arrived here from elsewhere" apart from "still
# interacting with something on this page", regardless of which of the two
# views is currently showing.
_just_arrived = mark_active_page("campaigns")

selected = st.session_state.get("selected_campaign")
if selected:
    _render_campaign_detail(selected, _just_arrived)
else:
    _render_hub(_just_arrived)
