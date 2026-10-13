"""The controls for ONE lead, shown in the Data tab's side panel under that lead's details: change its sending status or Asana
stage, or remove it.

These do exactly what the old 'Manage a lead' and 'Remove leads' sections did — the same workflows, the same inputs, the same
payload file — only for the lead that is open, so there is no menu to pick a lead from. Nothing happens until a button is
pressed, and removing asks a second time first.

Every widget's key includes the lead's own key. A widget's state lives in the browser session, so with fixed keys a status chosen
for one lead would still be showing when you clicked the next, and an unthinking Save would change the wrong person.
"""
import time
from typing import Callable, Dict

import streamlit as st

from config import WORKFLOW_REMOVE_LEADS, WORKFLOW_SET_LEAD_OVERRIDE
from data_import_logic import build_removal_payload, removal_payload_path, payload_to_bytes
from github_client import GitHubActionsError
from inbox_logic import md_escape
import outreach

NO_CHANGE = "(no change)"
STATUS_STOP = "Stop sending to this lead"
STATUS_CLEAR = "Clear override (resume normal sending)"
STATUS_CHOICES = [NO_CHANGE, STATUS_STOP, STATUS_CLEAR]
ASANA_CLEAR = "Clear override (resume automatic tracking)"
CONFIRM_KEY = "ld_confirm_remove"          # the key of the lead whose removal is waiting for a second click
RESULT_PREFIX = "ld_result_"               # + the lead's key: the outcome of the last removal, shown once


def asana_choices():
    return [NO_CHANGE, ASANA_CLEAR, outreach.ASANA_STAGE_SOURCED, outreach.ASANA_STAGE_OUTREACH_SENT, outreach.ASANA_STAGE_FOLLOWUP,
            outreach.ASANA_STAGE_NEGOTIATING, outreach.ASANA_STAGE_RIGHTS_SECURED, outreach.ASANA_STAGE_DECLINED_DEAD]


def _ask_to_remove(key: str) -> None:
    st.session_state[CONFIRM_KEY] = key


def _cancel_removal() -> None:
    st.session_state[CONFIRM_KEY] = None


def _confirm_removal(key: str, lead_id: str, campaign_name: str, github_client: Callable, current_user: Callable) -> None:
    """Runs when 'Yes, remove it' is pressed — BEFORE the page is drawn again — so the question is already gone when it repaints
    and a second click cannot submit the same removal twice. The outcome is left for the panel to show."""
    try:
        client = github_client()
        payload = build_removal_payload([lead_id])
        path = removal_payload_path(campaign_name)
        client.create_file(path, payload_to_bytes(payload),
                           message=f"Remove 1 lead(s) from {campaign_name} (via Streamlit, by {current_user()})")
        time.sleep(1)
        client.dispatch_workflow(WORKFLOW_REMOVE_LEADS, {"campaign": campaign_name, "payload_path": path})
    except GitHubActionsError as exc:
        st.session_state[f"{RESULT_PREFIX}{key}"] = ("error", f"Removal failed: {exc}")        # the question stays, so it can be tried again
        return
    st.session_state[CONFIRM_KEY] = None
    st.session_state[f"{RESULT_PREFIX}{key}"] = ("success", "Removal triggered — it will show as Removed within a minute or two.")


def render_lead_actions(lead: Dict, key: str, *, campaign_name: str, github_client: Callable, current_user: Callable) -> None:
    """Draws the Manage and Remove controls for `lead` (whose key in the table is `key`)."""
    email = str(lead.get("Email") or "").strip()
    with st.container(key="ld_actions_panel"):
        # ------------------------------------------------------------ manage
        st.markdown("**Manage this lead**")
        st.caption(
            "Stop sending to just this lead without removing it (it stays visible, only excluded from future sends), or set its Asana "
            "pipeline stage by hand — for a lead handled outside the automated pipeline. Both apply on the next sync, manual or scheduled."
        )
        st.caption(f"Current status: **{md_escape(lead.get('Status') or '(none)')}** · Asana stage override: "
                   f"**{md_escape(lead.get('ManualAsanaStage') or '(none — automatic)')}**")
        if not email:
            st.caption("This lead has no email address, so its status can't be changed from here.")
        else:
            status_choice = st.selectbox("Sending status", STATUS_CHOICES, key=f"manage_status_{key}")
            asana_choice = st.selectbox("Asana stage override", asana_choices(), key=f"manage_asana_{key}")
            if st.button("Save changes", key=f"manage_save_{key}", type="primary"):
                if status_choice == NO_CHANGE and asana_choice == NO_CHANGE:
                    st.warning("Nothing selected to change.")
                else:
                    try:
                        github_client().dispatch_workflow(WORKFLOW_SET_LEAD_OVERRIDE, {
                            "campaign": campaign_name, "email": email, "status": status_choice, "asana_stage": asana_choice})
                        st.success(f"Update triggered for {email}. May take a minute to actually reflect here while the app redeploys.")
                    except GitHubActionsError as exc:
                        st.error(f"Failed to trigger update: {exc}")

        # ------------------------------------------------------------ remove
        st.divider()
        st.markdown("**Remove this lead**")
        lead_id = str(lead.get("LeadID") or "").strip()
        if not lead_id:
            st.caption("This lead has no Lead ID, so it can't be removed from here.")
            return
        st.caption("Removed leads are never deleted — their row and history stay intact, they're just marked Removed and excluded from all future sends.")
        outcome = st.session_state.pop(f"{RESULT_PREFIX}{key}", None)
        if outcome:
            (st.success if outcome[0] == "success" else st.error)(outcome[1])
        if st.session_state.get(CONFIRM_KEY) != key:
            st.button("Remove this lead…", key=f"remove_ask_{key}", on_click=_ask_to_remove, args=(key,))
            return
        st.warning(f"Remove {md_escape(lead.get('FirstName') or '')} {md_escape(lead.get('LastName') or '')} <{md_escape(email)}>? "
                   "It will be marked Removed and no more emails will go to it.")
        confirm, cancel = st.columns(2)
        cancel.button("Cancel", key=f"remove_cancel_{key}", on_click=_cancel_removal, use_container_width=True)
        confirm.button("Yes, remove it", key=f"remove_yes_{key}", type="primary", use_container_width=True, on_click=_confirm_removal,
                       args=(key, lead_id, campaign_name, github_client, current_user))
