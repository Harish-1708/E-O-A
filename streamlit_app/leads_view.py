"""The Data tab's lead browser: filters, a table of leads (one page at a time), and a side panel for the one you click.

This module only LOOKS and SELECTS. Clicking a row remembers which lead is open and shows its details and timeline; the controls
that change anything (stop sending, Asana stage, remove) are drawn UNDER the details by `render_actions`, which the Data tab passes
in (see leads_actions) — nothing in this file sends, imports, removes or changes a lead.
"""
from typing import Callable, Dict, List, Optional, Tuple

import streamlit as st

import leads_html as LH
import leads_logic as L
from leads_actions import CONFIRM_KEY
from inbox_logic import md_escape

SELECTED_KEY, PAGE_KEY, LAST_QUERY_KEY = "data_selected", "data_page", "data_last_query"
FILTER_KEYS = {"sender": "data_filter_sender", "stage": "data_filter_stage", "recent": "data_filter_recent"}
CHIP_KEY, SEARCH_KEY = "data_filter", "data_search"


def _select(key: str) -> None:
    st.session_state[SELECTED_KEY] = key
    st.session_state[CONFIRM_KEY] = None                  # a removal waiting for its second click is cancelled by moving to another lead


def _clear_filters() -> None:
    """Back to everything: no search, the 'All' chip, and the three extra filters at their first option."""
    st.session_state[SEARCH_KEY] = ""
    st.session_state[CHIP_KEY] = "All"
    st.session_state[FILTER_KEYS["sender"]] = L.SENDER_ALL
    st.session_state[FILTER_KEYS["stage"]] = L.STAGE_ANY
    st.session_state[FILTER_KEYS["recent"]] = L.RECENT_ANY


def _turn_page(change: int) -> None:
    st.session_state[PAGE_KEY] = st.session_state.get(PAGE_KEY, 0) + change


def render_extra_filters(leads: List[Dict], stage_names: Optional[List[str]]) -> Tuple[str, str, str]:
    """The three extra filters under the status chips: which sender account, which stage was emailed most recently, and how long
    ago. Returns (sender, stage, recency). A choice that no longer exists (that sender is gone) quietly goes back to the first."""
    count = L.stage_count(stage_names, leads)
    senders, stages = L.sender_options(leads), L.stage_options(L.stage_labels(stage_names, count))
    for key, options in ((FILTER_KEYS["sender"], senders), (FILTER_KEYS["stage"], stages), (FILTER_KEYS["recent"], L.RECENCY_OPTIONS)):
        if st.session_state.get(key, options[0]) not in options:
            st.session_state[key] = options[0]
    first, second, third = st.columns(3)
    sender = first.selectbox("Sender account", senders, key=FILTER_KEYS["sender"])
    stage = second.selectbox("Sequence stage", stages, key=FILTER_KEYS["stage"])
    recency = third.selectbox("Last email sent", L.RECENCY_OPTIONS, key=FILTER_KEYS["recent"])
    return sender, stage, recency


def render_clear_filters(active: bool) -> None:
    """A button that appears only while something is filtering, to put everything back."""
    if active:
        st.button("Clear all filters", key="data_filters_clear", on_click=_clear_filters)


def render_lead_browser(*, leads: List[Dict], filtered: List[Dict], search_query: str, status_filter: str,
                        stage_names: Optional[List[str]], extra_filters: Tuple[str, str, str] = ("", "", ""),
                        render_actions: Optional[Callable[[Dict, str], None]] = None) -> Optional[Dict]:
    """Draws the table and the panel for `filtered` (a subset of `leads`, the very same dicts). Returns the open lead, or None.
    `extra_filters` is only part of what decides when to go back to page 1; `render_actions(lead, key)` draws the controls for the
    open lead under its details."""
    st.markdown(f"<style>{LH.LEADS_CSS}</style>", unsafe_allow_html=True)
    count = L.stage_count(stage_names, leads)
    labels = L.stage_labels(stage_names, count)
    key_of = dict(zip((id(lead) for lead in leads), L.lead_keys(leads)))        # a key per lead, the same whatever the filter

    signature = (search_query, status_filter, tuple(extra_filters))
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
                    st.button(f"Open lead {md_escape(row['name'])}", key=f"data_open_{key}", on_click=_select, args=(key,))
        if filtered:
            info, previous, following = st.columns([3, 1, 1])
            info.caption(f"Showing {start + 1}–{end} of {len(filtered)} · page {page + 1} of {pages}")
            previous.button("‹ Prev", key="data_prev", on_click=_turn_page, args=(-1,), disabled=page == 0, use_container_width=True)
            following.button("Next ›", key="data_next", on_click=_turn_page, args=(1,), disabled=page >= pages - 1, use_container_width=True)
    with panel_col, st.container(key="ld_panel_card"):
        if open_lead is None:
            st.markdown(LH.empty_panel_html(), unsafe_allow_html=True)
        else:
            row = L.lead_row(open_lead, count)
            facts = [("Lead ID", str(open_lead.get("LeadID") or "")), ("Company", row["company"]), ("Sender", row["sender"]),
                     ("Approval", str(open_lead.get("Approval") or "")), ("Last sent", row["last_sent"]),
                     ("Asana stage", str(open_lead.get("ManualAsanaStage") or "") or "automatic")]
            facts = [(label, value) for label, value in facts if value or label not in ("Company", "Approval")]     # no empty noise rows
            st.markdown(LH.panel_html(row, facts, L.timeline(open_lead, labels)), unsafe_allow_html=True)
            if render_actions is not None:
                render_actions(open_lead, open_key)
    return open_lead
