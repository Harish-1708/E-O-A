import os
import sys

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from page_state import mark_active_page  # noqa: E402
from auth import login_gate  # noqa: E402
from preview_logic import list_campaigns, get_campaign_cfg  # noqa: E402
from sheets_readonly import ReadOnlySheetsConnector  # noqa: E402
from work_queue_logic import build_all_queues, summarize_queue, NOT_IN_PLAY_LABELS  # noqa: E402

# Page config is set once, centrally, in app.py via st.navigation/st.Page —
# calling st.set_page_config here too would raise an error.

mark_active_page("today")

if not login_gate():
    st.stop()

st.title("📋 Today")
st.caption(
    "What is due to be emailed, what is waiting and until when, and why anyone is out of play — for every "
    "running campaign. Read-only, and it uses the same rule Auto Send uses, so what you see here is what the "
    "next run will do. Dates are in each campaign's own timezone; the times stored in the Sheet are UTC."
)


@st.cache_resource(show_spinner=False)
def _get_connector() -> ReadOnlySheetsConnector:
    sa_info = dict(st.secrets["google_sheets_readonly"]["service_account_json"])
    sheet_id = st.secrets.get("shared_sheet_id", "")
    return ReadOnlySheetsConnector(service_account_info=sa_info, sheet_id=sheet_id)


@st.cache_data(ttl=30, show_spinner=False)
def _load_queues():
    campaign_names = list_campaigns()
    # Resolved on the script thread and handed to the worker threads that
    # read each campaign at the same time, as the Overview page does.
    try:
        connector, connector_error = _get_connector(), None
    except Exception as exc:  # noqa: BLE001
        connector, connector_error = None, exc

    def _leads(campaign_cfg):
        if connector_error is not None:
            raise connector_error
        return connector.get_all_leads(campaign_cfg["master_tab"])

    return build_all_queues(campaign_names, get_campaign_cfg, _leads, max_workers=6)


if st.button("🔄 Refresh now"):
    _load_queues.clear()

try:
    queues, not_running, errors = _load_queues()
except Exception as exc:  # noqa: BLE001
    st.error(f"Couldn't build the list: {exc}")
    st.stop()

if not queues:
    st.info("No campaign is running right now, so nothing is due.")
else:
    total_due = sum(q["due_total"] for q in queues)
    total_waiting = sum(q["waiting_total"] for q in queues)
    open_now = sum(1 for q in queues if q["window"]["open"])
    col1, col2, col3 = st.columns(3)
    col1.metric("Due now (all running campaigns)", total_due)
    col2.metric("Waiting on a follow-up date", total_waiting)
    col3.metric("Sending open right now", f"{open_now} of {len(queues)} campaigns")

    for queue in queues:
        st.divider()
        st.subheader(queue["campaign"])
        window = queue["window"]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Due now", queue["due_total"])
        c2.metric("Waiting", queue["waiting_total"])
        limit = queue["daily_limit"]
        c3.metric("Sent today", f"{queue['sent_today']} / {limit}" if limit is not None else queue["sent_today"])
        c4.metric("Sending window", {True: "Open", False: "Closed", None: "Unknown"}[window["open"]])

        if window["open"] is False:
            opens = f" Next opens: {window['next_open']}." if window["next_open"] else ""
            st.caption(f"Closed — {window['reason']}.{opens}")
        elif window["open"] is None and window["reason"]:
            st.warning(window["reason"])

        st.write(summarize_queue(queue))

        shown = [row for row in queue["stages"] if row["due_now"] or row["waiting"]]
        if shown:
            st.dataframe(
                {
                    "Stage": [r["stage"] for r in shown],
                    "Due now": [r["due_now"] for r in shown],
                    "Waiting": [r["waiting"] for r in shown],
                    "Next lead due": [r["next_due_text"] or "—" for r in shown],
                },
                width="stretch",
                hide_index=True,
            )
        for row in queue["stages"]:
            if row["due_now"]:
                with st.expander(f"{row['stage']} — {row['due_now']} due now"):
                    st.dataframe(row["due_leads"], width="stretch", hide_index=True)
                    if row["due_leads_hidden"]:
                        st.caption(f"…and {row['due_leads_hidden']} more not listed.")

        out = [f"{label} {queue['out_of_play'][label]}" for label in NOT_IN_PLAY_LABELS if queue["out_of_play"][label]]
        st.caption(
            f"In the sequence: {queue['in_sequence']}"
            + (" · Out of play: " + " · ".join(out) if out else "")
            + f" · Total leads: {queue['total_leads']}"
        )

if not_running:
    st.divider()
    st.caption("Not running (never read): " + ", ".join(f"{name} ({status})" for name, status in not_running))

if errors:
    with st.expander(f"{len(errors)} campaign(s) could not be read"):
        for name, message in errors:
            st.write(f"**{name}** — {message}")
