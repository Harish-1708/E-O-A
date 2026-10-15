import os
import sys
import time

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from page_state import mark_active_page  # noqa: E402
from auth import login_gate, current_user  # noqa: E402
from config import REPO_ROOT, WORKFLOW_CHECK_REPLIES, WORKFLOW_MARK_RESPONSES_READ, EMAIL_ACCOUNT_SLOT_MAPPING_ABS_PATH  # noqa: E402
from preview_logic import list_campaigns_live, get_campaign_cfg  # noqa: E402
from parallel_logic import run_in_parallel  # noqa: E402
from sheets_readonly import ReadOnlySheetsConnector  # noqa: E402
from github_client import GitHubClient, GitHubActionsError  # noqa: E402
from send_logic import build_check_replies_inputs  # noqa: E402
from responses_hub_logic import (  # noqa: E402
    tag_responses_with_campaign, filter_responses, count_unread,
    get_campaign_names_present, CLASSIFICATION_OPTIONS,
    STATUS_FILTER_ALL, INBOX_FILTER_ALL, INBOX_FILTER_UNREAD, search_responses,
    split_keys_by_campaign, build_mark_read_payload,
    group_responses_by_lead, sort_conversation_groups_newest_first, filter_groups_by_unread,
)
from conversation_logic import filter_responses_for_lead, filter_sent_mail_for_lead  # noqa: E402
from data_import_logic import payload_to_bytes  # noqa: E402
from inbox_view import InboxContext, render_inbox  # noqa: E402
from inbox_senders import build_sender_options  # noqa: E402
from accounts_logic import build_health_lookup  # noqa: E402

if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

_just_arrived = mark_active_page("responses")

if not login_gate():
    st.stop()

st.title("💬 Responses")
st.caption(
    "Every reply across every campaign, in one place. For working inside a single campaign's own "
    "context, the Campaigns page has the same reply tools under each campaign's Responses tab."
)


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
    rather than raising lets live reads degrade to the local checkout
    instead of breaking the whole page."""
    try:
        return _get_github_client()
    except Exception:  # noqa: BLE001 - missing/invalid token, etc.
        return None


@st.cache_resource(ttl=300, show_spinner=False)
def _load_account_health():
    """{account name: {status, detail, checked_at}} from the Email Accounts Health tab, held for five minutes; an empty answer on any failure (the reply composer then
    simply shows no health beside the accounts)."""
    try:
        return build_health_lookup(_get_connector().get_account_health())
    except Exception:  # noqa: BLE001 - no health data just means none is shown
        return {}


@st.cache_data(ttl=30, show_spinner=False)
def _load_sent_mail_cached(campaign_name):
    """One campaign's Sent Mail tab (the emails the team sent by hand or from this page). Read only
    for the campaign whose conversation is open; [] until the tab exists."""
    campaign_cfg = get_campaign_cfg(campaign_name)
    return _get_connector().get_all_sent_mail(campaign_cfg.get("sent_mail_tab") or f"{campaign_name} Sent Mail")


@st.cache_data(ttl=30, show_spinner=False)
def _load_all_responses(campaign_names):
    connector = _get_connector()
    all_responses = []
    unavailable = []
    # Each campaign's tab is an independent Google round trip — read them
    # at the same time instead of one campaign after another. Configs are
    # loaded here first (local, instant); any campaign whose config can't
    # be loaded is unavailable exactly as before, and results are still
    # assembled in campaign_names order.
    configs = {}
    for name in campaign_names:
        try:
            configs[name] = get_campaign_cfg(name)
        except Exception:  # noqa: BLE001 - a campaign missing config shouldn't sink the whole page
            unavailable.append(name)
    readable = [n for n in campaign_names if n in configs]
    results = run_in_parallel(
        [(lambda n=n: connector.get_all_responses(configs[n]["responses_tab"])) for n in readable])
    result_by_name = dict(zip(readable, results))
    for name in campaign_names:
        if name not in configs:
            continue
        ok, value = result_by_name[name]
        if ok:
            all_responses.extend(tag_responses_with_campaign(value, name))
        else:  # ReadOnlySheetsError or anything else — this campaign alone is unavailable
            unavailable.append(name)
    return all_responses, unavailable


@st.cache_data(ttl=30, show_spinner=False)
def _load_all_leads(campaign_names):
    """Needed to resolve each response's SenderAccount for the reply
    form's default — same lookup the per-campaign Responses tab does,
    just across every campaign here instead of one."""
    connector = _get_connector()
    leads_by_campaign = {}
    configs = {}
    for name in campaign_names:
        try:
            configs[name] = get_campaign_cfg(name)
        except Exception:  # noqa: BLE001
            leads_by_campaign[name] = []
    readable = [n for n in campaign_names if n in configs]
    results = run_in_parallel(
        [(lambda n=n: connector.get_all_leads(configs[n]["master_tab"])) for n in readable])
    for name, (ok, value) in zip(readable, results):
        leads_by_campaign[name] = value if ok else []
    return {name: leads_by_campaign[name] for name in campaign_names}


try:
    campaign_names = list_campaigns_live(_safe_github_client())
except Exception as exc:  # noqa: BLE001
    st.error(f"Couldn't list campaigns: {exc}")
    st.stop()

if st.button("🔄 Refresh"):
    _load_all_responses.clear()
    _load_all_leads.clear()
    _load_sent_mail_cached.clear()

all_responses, unavailable_campaigns = _load_all_responses(tuple(campaign_names))
leads_by_campaign = _load_all_leads(tuple(campaign_names))

if "read_response_keys" not in st.session_state:
    st.session_state["read_response_keys"] = set()
read_keys = st.session_state["read_response_keys"]

if "pending_sync_keys" not in st.session_state:
    st.session_state["pending_sync_keys"] = set()
pending_sync_keys = st.session_state["pending_sync_keys"]

# Read/unread is now persistent — each response's own IsRead column in
# the Response Sheet is the durable source of truth (see
# responses_hub_logic.is_response_read). read_keys above is only an
# OPTIMISTIC local overlay for the gap between "you opened this" and
# "the sync workflow actually wrote IsRead=Yes" — Streamlit itself never
# writes to the Sheet directly, so that write always goes through
# mark_responses_read.yml, batched rather than one run per response.
unread_count = count_unread(all_responses, read_keys)

if pending_sync_keys:
    if st.button(f"🔄 Sync read status ({len(pending_sync_keys)} pending)"):
        grouped = split_keys_by_campaign(pending_sync_keys)
        client = _get_github_client()
        synced_campaigns = []
        failed_campaigns = []
        for campaign_for_sync, response_ids in grouped.items():
            try:
                payload = build_mark_read_payload(response_ids)
                path = f"mark_read/{campaign_for_sync}/{time.strftime('%Y-%m-%d-%H%M%S')}.json"
                client.create_file(
                    path, payload_to_bytes(payload),
                    message=f"Mark {len(response_ids)} response(s) read in {campaign_for_sync} "
                            f"(via Streamlit, by {current_user()})",
                )
                client.dispatch_workflow(WORKFLOW_MARK_RESPONSES_READ,
                                          {"campaign": campaign_for_sync, "payload_path": path})
                synced_campaigns.append(campaign_for_sync)
            except GitHubActionsError:
                failed_campaigns.append(campaign_for_sync)
        keys_to_remove = {f"{c}:{rid}" for c in synced_campaigns for rid in grouped[c]}
        st.session_state["pending_sync_keys"] = pending_sync_keys - keys_to_remove
        if synced_campaigns:
            st.success(f"Synced read status for {len(synced_campaigns)} campaign(s).")
        if failed_campaigns:
            st.warning(f"Failed to sync: {', '.join(failed_campaigns)}")

search_query = st.text_input("🔍 Search responses (sender, subject, snippet, campaign)",
                              key="responses_search_query")

col1, col2, col3 = st.columns(3)
with col1:
    status_filter = st.selectbox("Status", CLASSIFICATION_OPTIONS, key="responses_status_filter")
with col2:
    campaign_options = [STATUS_FILTER_ALL] + get_campaign_names_present(all_responses)
    campaign_filter = st.selectbox("Campaign", campaign_options, key="responses_campaign_filter")
with col3:
    inbox_filter = st.radio(
        "Show", [INBOX_FILTER_ALL, INBOX_FILTER_UNREAD], key="responses_inbox_chip", horizontal=True,
        format_func=lambda v: "All" if v == INBOX_FILTER_ALL else f"Unread ({unread_count})",
    )

if unavailable_campaigns:
    st.caption(f"{len(unavailable_campaigns)} campaign(s) not counted yet (no Response Sheet tab exists — "
               f"no replies for them yet): {', '.join(unavailable_campaigns)}")

filtered = search_responses(all_responses, search_query)
# inbox_filter deliberately NOT applied here — unread/read is handled at
# the CONVERSATION level below (filter_groups_by_unread), not per
# message, so a conversation with one unread message among several
# older, already-read ones still surfaces correctly.
filtered = filter_responses(filtered, status_filter, campaign_filter, INBOX_FILTER_ALL, read_keys)

# One card per CREATOR/THREAD, not one per individual reply — the
# actual reported production bug: the same creator sending several
# messages over time showed as a separate card for every single
# message. See group_responses_by_lead's own docstring for the full
# reasoning; grouping happens on the already-filtered list, so a
# conversation shows up whenever any one of its messages matches the
# current search/status/campaign filters.
groups = group_responses_by_lead(filtered)
groups = filter_groups_by_unread(groups, inbox_filter, read_keys)
groups = sort_conversation_groups_newest_first(groups)

if groups:
    render_inbox(groups, InboxContext(
        namespace="hub",
        get_campaign_cfg=get_campaign_cfg,
        leads_for=lambda name: leads_by_campaign.get(name, []),
        responses_for=lambda name, lead_id: filter_responses_for_lead(all_responses, lead_id, name),
        github_client=_get_github_client,
        current_user=current_user,
        read_keys=read_keys,
        pending_sync_keys=pending_sync_keys,
        show_campaign=True,
        sent_for=lambda name, lead_id: filter_sent_mail_for_lead(_load_sent_mail_cached(name), lead_id),
        sender_options=lambda: build_sender_options(dict(st.secrets.get("email_accounts_directory", {})), _safe_github_client(), EMAIL_ACCOUNT_SLOT_MAPPING_ABS_PATH, _load_account_health),
    ))
else:
    st.info("No responses match these filters.")

st.divider()
if st.button("📥 Check Replies Now (all campaigns)"):
    client = _get_github_client()
    triggered = []
    failed = []
    for name in campaign_names:
        try:
            inputs = build_check_replies_inputs(name)
            client.dispatch_workflow(WORKFLOW_CHECK_REPLIES, inputs)
            triggered.append(name)
        except GitHubActionsError:
            failed.append(name)
    st.success(f"Triggered a check for {len(triggered)} campaign(s).")
    if failed:
        st.warning(f"Failed to trigger for: {', '.join(failed)}")
