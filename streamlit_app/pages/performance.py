import os
import sys

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from page_state import mark_active_page  # noqa: E402
from auth import login_gate  # noqa: E402
from preview_logic import list_campaigns, get_campaign_cfg  # noqa: E402
from sheets_readonly import ReadOnlySheetsConnector  # noqa: E402
from campaign_status_logic import status_label  # noqa: E402
from performance_logic import (  # noqa: E402
    build_all_performance, combine_performance, build_best_performers,
    GLANCE_SORT_OPTIONS, glance_rows, glance_totals, glance_csv,
)
from performance_html import glance_header_html, glance_table_html  # noqa: E402
from performance_view import render_performance  # noqa: E402
from campaign_html import CAMPAIGN_SKIN_CSS  # noqa: E402

# Page config is set once, centrally, in app.py via st.navigation/st.Page —
# calling st.set_page_config here too would raise an error.

mark_active_page("performance")

if not login_gate():
    st.stop()

st.markdown(f"<style>{CAMPAIGN_SKIN_CSS}</style>", unsafe_allow_html=True)         # the same look as a campaign's own Performance tab
st.title("🎯 Performance")
st.caption(
    "The business questions behind the email counts: how many replies were positive, which follow-up and "
    "which template earn replies, how long a reply takes, and how many creators reach negotiation or rights. "
    "Read-only. The headline totals come from the same calculation as the Sheet's Dashboard tab. Each campaign "
    "also shows this same view in its own Performance tab. Open and click rates are deliberately not shown — mail "
    "privacy features and image blocking make them unreliable."
)

ALL_CAMPAIGNS = "All campaigns"


@st.cache_resource(show_spinner=False)
def _get_connector() -> ReadOnlySheetsConnector:
    sa_info = dict(st.secrets["google_sheets_readonly"]["service_account_json"])
    sheet_id = st.secrets.get("shared_sheet_id", "")
    return ReadOnlySheetsConnector(service_account_info=sa_info, sheet_id=sheet_id)


def _fetch_campaign_data(campaign_name: str, connector):
    campaign_cfg = get_campaign_cfg(campaign_name)
    leads = connector.get_all_leads(campaign_cfg["master_tab"])
    responses = connector.get_all_responses(campaign_cfg["responses_tab"])
    send_log = connector.get_all_send_log(campaign_cfg["send_log_tab"])
    try:
        error_log = connector.get_all_error_log(campaign_cfg["error_log_tab"])
    except Exception:  # noqa: BLE001 - only the all-time error table depends on this tab; never lose the campaign over it
        error_log = None
    return campaign_cfg, leads, responses, send_log, error_log


@st.cache_data(ttl=30, show_spinner=False)
def _load_performance():
    names = list_campaigns()
    # Resolved on the script thread and handed to the worker threads that
    # read each campaign at the same time, as the other pages do.
    try:
        connector, connector_error = _get_connector(), None
    except Exception as exc:  # noqa: BLE001
        connector, connector_error = None, exc

    def _fetch(name):
        if connector_error is not None:
            raise connector_error
        return _fetch_campaign_data(name, connector)

    return build_all_performance(names, _fetch, max_workers=6)


if st.button("🔄 Refresh now"):
    _load_performance.clear()

try:
    perfs, errors = _load_performance()
except Exception as exc:  # noqa: BLE001
    st.error(f"Couldn't build the report: {exc}")
    st.stop()

if not perfs:
    st.info("No campaign has any activity to report yet.")
else:
    choice = st.selectbox("Campaign", [ALL_CAMPAIGNS] + [p["campaign"] for p in perfs], key="performance_campaign")

    combined = combine_performance(perfs)
    selected = combined if choice == ALL_CAMPAIGNS else next(p for p in perfs if p["campaign"] == choice)
    render_performance(selected, key_prefix="performance", best=build_best_performers(perfs, combined),
                       show_campaign_ranking=choice == ALL_CAMPAIGNS)

    # "All campaigns at a glance": a comparison table with its own header, a Sort choice and a CSV download (which keeps every column the old table had)
    with st.container(key="pg_glance"):
        head_column, sort_column, csv_column = st.columns([5, 1.7, 1], vertical_alignment="center")
        sort_choice = sort_column.selectbox("Sort", GLANCE_SORT_OPTIONS, key="performance_glance_sort", label_visibility="collapsed", format_func=lambda v: f"Sort: {v}")
        head_column.markdown(glance_header_html(sort_choice), unsafe_allow_html=True)
        csv_column.download_button("⬇ CSV", data=glance_csv(perfs, status_label, sort_choice), file_name="all_campaigns_at_a_glance.csv", mime="text/csv",
                                   key="performance_glance_csv", width="stretch")
        st.markdown(glance_table_html(glance_rows(perfs, status_label, sort_choice), glance_totals(combined, len(perfs))), unsafe_allow_html=True)

if errors:
    with st.expander(f"{len(errors)} campaign(s) not shown yet (no runs made for them)"):
        for name, message in errors:
            st.write(f"**{name}** — {message}")
