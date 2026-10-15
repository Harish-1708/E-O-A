"""Connections and cached reads shared by every page, the sidebar and the Home page.

Before this module each page built its own connectors and its own cached copy of the same Sheets data, so opening Home and then Campaigns would have read every
campaign's Sheets twice. Now there is ONE cached load (`load_hub_bundle`, kept for 30 seconds) that returns the Campaigns list's rows and, with them, the raw data of
each campaign (its settings, leads, replies and send log), which is what Home and the sidebar markers need (sent today, unread replies). Whichever page asks first pays
for the load; the others reuse it.
"""
import os
from typing import Dict

import streamlit as st

import campaigns_hub_logic as HL
from config import CAMPAIGNS_DIR, EMAIL_ACCOUNT_SLOT_MAPPING_ABS_PATH, REPO_ROOT
from accounts_logic import build_health_lookup
from campaign_header_logic import read_cron_minute
from github_client import GitHubClient
from inbox_senders import build_sender_options
from preview_logic import get_campaign_cfg, list_campaigns_live
from settings_logic import load_raw_override_live, merge_live_override_into_cfg
from sheets_readonly import ReadOnlySheetsConnector


@st.cache_resource(show_spinner=False)
def get_github_client() -> GitHubClient:
    gh = st.secrets["github"]
    return GitHubClient(token=gh["token"], owner=gh["owner"], repo=gh["repo"], cache_reads=True)


def safe_github_client():
    """The GitHub client, or None if it can't be built (no token, bad config). Returning None rather than raising lets every live read degrade to the local checkout
    instead of breaking the whole page: a read must never be harder to do than it was before going live."""
    try:
        return get_github_client()
    except Exception:  # noqa: BLE001 - missing/invalid token, etc.
        return None


@st.cache_resource(show_spinner=False)
def get_connector() -> ReadOnlySheetsConnector:
    sa_info = dict(st.secrets["google_sheets_readonly"]["service_account_json"])
    sheet_id = st.secrets.get("shared_sheet_id", "")
    return ReadOnlySheetsConnector(service_account_info=sa_info, sheet_id=sheet_id)


def fetch_sheet_data(campaign_cfg, connector=None):
    # `connector` is passed in by callers that run this on a worker thread (see load_hub_bundle): the cached connector is resolved once on the normal script thread and
    # handed over, so no worker thread ever calls into Streamlit's cache itself.
    connector = connector or get_connector()
    leads = connector.get_all_leads(campaign_cfg["master_tab"])
    responses = connector.get_all_responses(campaign_cfg["responses_tab"])
    send_log = connector.get_all_send_log(campaign_cfg["send_log_tab"])
    return leads, responses, send_log


def get_campaign_cfg_live(campaign_name: str):
    """get_campaign_cfg, with the live GitHub override overlaid: the same single-point pattern used on the campaign detail page. (get_campaign_cfg alone reads the LOCAL
    disk only, which can be behind a very recent change until the app redeploys.)"""
    campaign_cfg = get_campaign_cfg(campaign_name)
    return merge_live_override_into_cfg(campaign_cfg, load_raw_override_live(campaign_name, safe_github_client(), CAMPAIGNS_DIR))


@st.cache_data(ttl=30, show_spinner=False)
def load_hub_bundle():
    """(rows, archived rows, errors, details): the Campaigns list exactly as build_campaigns_hub makes it, plus `details`, {campaign name: {'cfg', 'leads', 'responses',
    'send_log'}} for every campaign whose Sheets were read (the data each row was built from, kept so Home and the sidebar need not read it again)."""
    campaign_names = list_campaigns_live(safe_github_client())
    # Resolved here, on the script thread, then handed to the worker threads that fetch each campaign's Sheets in parallel. If it can't be built, every active campaign
    # reports that error individually.
    try:
        connector = get_connector()
        connector_error = None
    except Exception as exc:  # noqa: BLE001
        connector, connector_error = None, exc
    details: Dict[str, Dict] = {}

    def _fetch_for_hub(campaign_cfg):
        if connector_error is not None:
            raise connector_error
        leads, responses, send_log = fetch_sheet_data(campaign_cfg, connector=connector)
        details[campaign_cfg["_campaign_name"]] = {"cfg": campaign_cfg, "leads": leads, "responses": responses, "send_log": send_log}
        return leads, responses, send_log

    rows, deleted_rows, errors = HL.build_campaigns_hub(campaign_names, get_campaign_cfg_live, _fetch_for_hub, max_workers=6)
    return rows, deleted_rows, errors, details


@st.cache_resource(ttl=300, show_spinner=False)
def load_account_health():
    """{account name: {status, detail, checked_at}} from the Email Accounts Health tab, held for five minutes; an empty answer on any failure (nothing then shows as
    connected or disconnected, rather than the page breaking)."""
    try:
        return build_health_lookup(get_connector().get_account_health())
    except Exception:  # noqa: BLE001 - no health data just means none is shown
        return {}


def load_sender_overview():
    """{'directory': {name: address}, 'health': {...}} for every configured account (the old secrets and the live slot file merged)."""
    return build_sender_options(dict(st.secrets.get("email_accounts_directory", {})), safe_github_client(), EMAIL_ACCOUNT_SLOT_MAPPING_ABS_PATH, load_account_health)


def auto_send_cron_minute():
    """The minute past each hour the auto-send workflow runs at, read from its own file (None if it can't be read or isn't plain hourly)."""
    try:
        with open(os.path.join(REPO_ROOT, ".github", "workflows", "auto_send.yml"), encoding="utf-8") as handle:
            return read_cron_minute(handle.read())
    except OSError:
        return None
