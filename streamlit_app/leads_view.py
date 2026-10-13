"""The Data tab's lead browser: a table of leads (one page at a time) with a side panel for the one you click.

It only LOOKS and SELECTS. It never sends, imports, removes or changes anything: those stay exactly where they were in the
Data tab (the CSV import, 'Remove leads' and 'Manage a lead'), with the same widgets and keys. Clicking a row only
(1) remembers which lead is open and (2) points the existing 'Manage a lead' menu at it, so the controls are one click away.
"""
from typing import Dict, List, Optional

import streamlit as st

import leads_html as LH
import leads_logic as L
from inbox_logic import md_escape

SELECTED_KEY, PAGE_KEY, LAST_QUERY_KEY, MANAGE_KEY = "data_selected", "data_page", "data_last_query", "manage_lead_select"


def _select(key: str, manage_label: str) -> None:
    st.session_state[SELECTED_KEY] = key
    st.session_state[MANAGE_KEY] = manage_label            # the existing 'Manage a lead' menu now has this lead picked


def _turn_page(change: int) -> None:
    st.session_state[PAGE_KEY] = st.session_state.get(PAGE_KEY, 0) + change


def render_lead_browser(*, leads: List[Dict], filtered: List[Dict], search_query: str, status_filter: str,
                        stage_names: Optional[List[str]]) -> Optional[Dict]:
    """Draws the table and the panel for `filtered` (a subset of `leads`, the very same dicts). Returns the open lead, or None."""
    st.markdown(f"<style>{LH.LEADS_CSS}</style>", unsafe_allow_html=True)
    count = L.stage_count(stage_names, leads)
    labels = L.stage_labels(stage_names, count)
    key_of = dict(zip((id(lead) for lead in leads), L.lead_keys(leads)))        # a key per lead, the same whatever the filter

    signature = (search_query, status_filter)
    if st.session_state.get(LAST_QUERY_KEY) != signature:                       # a new search or filter starts again at page 1
        st.session_state[LAST_QUERY_KEY] = signature
        st.session_state[PAGE_KEY] = 0
    page, start, end, pages = L.paginate(len(filtered), st.session_state.get(PAGE_KEY, 0))
    st.session_state[PAGE_KEY] = page

    open_key = st.session_state.get(SELECTED_KEY)
    shown_keys = {key_of[id(lead)] for lead in filtered}
    if open_key not in shown_keys:                                              # filtered out (or never chosen): nothing is open
        open_key = None
    open_lead = next((lead for lead in filtered if key_of[id(lead)] == open_key), None)

    table_col, panel_col = st.columns([2.6, 1], gap="medium")
    with table_col:
        with st.container(key="ld_table_data"):
            st.markdown(LH.head_html(), unsafe_allow_html=True)
            if not filtered:
                st.markdown(LH.empty_table_html("No leads match this search or filter."), unsafe_allow_html=True)
            for index, lead in enumerate(filtered[start:end]):
                key = key_of[id(lead)]
                row = L.lead_row(lead, count)
                with st.container(key=f"ld_row_{index}"):
                    st.markdown(LH.row_html(row, selected=key == open_key), unsafe_allow_html=True)
                    # the real button, invisible, laid over the whole row; its label is only for screen readers
                    st.button(f"Open lead {md_escape(row['name'])}", key=f"data_open_{key}", on_click=_select, args=(key, L.manage_label(lead)))
        if filtered:
            info, previous, following = st.columns([3, 1, 1])
            info.caption(f"Showing {start + 1}–{end} of {len(filtered)} · page {page + 1} of {pages}")
            previous.button("‹ Prev", key="data_prev", on_click=_turn_page, args=(-1,), disabled=page == 0, use_container_width=True)
            following.button("Next ›", key="data_next", on_click=_turn_page, args=(1,), disabled=page >= pages - 1, use_container_width=True)
    with panel_col:
        if open_lead is None:
            st.markdown(LH.empty_panel_html(), unsafe_allow_html=True)
        else:
            row = L.lead_row(open_lead, count)
            facts = [("Lead ID", str(open_lead.get("LeadID") or "")), ("Company", row["company"]), ("Sender", row["sender"]),
                     ("Approval", str(open_lead.get("Approval") or "")), ("Last sent", row["last_sent"]),
                     ("Asana stage", str(open_lead.get("ManualAsanaStage") or "") or "automatic")]
            st.markdown(LH.panel_html(row, facts, L.timeline(open_lead, labels),
                                      "To stop sending to this lead or set its Asana stage, use 'Manage a lead' just below the table."),
                        unsafe_allow_html=True)
    return open_lead
