import os
import sys

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from page_state import mark_active_page  # noqa: E402
from auth import login_gate  # noqa: E402
from preview_logic import list_campaigns, get_campaign_cfg  # noqa: E402
from sheets_readonly import ReadOnlySheetsConnector  # noqa: E402
from campaign_status_logic import status_label, STATUS_RUNNING, STATUS_PAUSED, STATUS_DRAFT, STATUS_COMPLETED  # noqa: E402
from performance_logic import (  # noqa: E402
    build_all_performance, combine_performance, daily_series, build_best_performers,
    format_rate, format_duration_hours, INTENT_ORDER, INTENT_NOT_CLASSIFIED,
)

# Page config is set once, centrally, in app.py via st.navigation/st.Page —
# calling st.set_page_config here too would raise an error.

mark_active_page("performance")

if not login_gate():
    st.stop()

st.title("🎯 Performance")
st.caption(
    "The business questions behind the email counts: how many replies were positive, which follow-up and "
    "which template earn replies, how long a reply takes, and how many creators reach negotiation or rights. "
    "Read-only. The headline totals come from the same calculation as the Overview page. Open and click rates "
    "are deliberately not shown — mail privacy features and image blocking make them unreliable."
)

ALL_CAMPAIGNS = "All campaigns"
RANGES = {"Last 7 days": 7, "Last 30 days": 30, "Last 90 days": 90, "All time": None}
STATUS_CHIP = {STATUS_RUNNING: "🟢", STATUS_PAUSED: "🟡", STATUS_DRAFT: "⚪", STATUS_COMPLETED: "🔵"}


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
    return campaign_cfg, leads, responses, send_log


@st.cache_data(ttl=30, show_spinner=False)
def _load_performance():
    names = list_campaigns()
    # Resolved on the script thread and handed to the worker threads that
    # read each campaign at the same time, as the Overview page does.
    try:
        connector, connector_error = _get_connector(), None
    except Exception as exc:  # noqa: BLE001
        connector, connector_error = None, exc

    def _fetch(name):
        if connector_error is not None:
            raise connector_error
        return _fetch_campaign_data(name, connector)

    return build_all_performance(names, _fetch, max_workers=6)


def _card(label: str, value, caption: str = "", help_text: str = None):
    with st.container(border=True):
        st.metric(label, value, help=help_text)
        if caption:
            st.caption(caption)


def _table(rows, key, header):
    if not rows:
        return
    st.dataframe(
        {
            header: [r[key] for r in rows],
            "Emails sent": [r["sent"] for r in rows],
            "Replies": [r["replies"] for r in rows],
            "Reply rate": [format_rate(r["reply_rate"]) for r in rows],
            "Positive": [r["positive"] for r in rows],
            "Positive rate": [format_rate(r["positive_rate"]) for r in rows],
        },
        width="stretch",
        hide_index=True,
    )


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
    filter_col, range_col = st.columns([2, 1])
    choice = filter_col.selectbox("Campaign", [ALL_CAMPAIGNS] + [p["campaign"] for p in perfs], key="performance_campaign")
    range_label = range_col.selectbox("Chart range", list(RANGES), index=1, key="performance_range")

    combined = combine_performance(perfs)
    selected = combined if choice == ALL_CAMPAIGNS else next(p for p in perfs if p["campaign"] == choice)

    # ---- headline cards
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    with c1:
        _card("Contacted", selected["contacted"], f"of {selected['total_leads']} leads")
    with c2:
        _card("Emails sent", selected["sent"], f"{selected['send_failures']} failed" if selected["send_failures"] else "")
    with c3:
        _card("Reply rate", format_rate(selected["reply_rate"]), f"{selected['replies']} replied")
    with c4:
        _card("Positive replies", selected["positive"], f"{format_rate(selected['positive_rate'])} of contacted",
              "Replies the AI read as Interested. Replies without an AI reading are counted under 'Not classified' below.")
    with c5:
        _card("Negotiating", "—" if selected["negotiating"] is None else selected["negotiating"], "from the Asana sync",
              "Leads whose Asana task the sync last left in Negotiating. Shows — until the Asana sync has recorded stages.")
    with c6:
        _card("Rights secured", "—" if selected["rights_secured"] is None else selected["rights_secured"],
              "from the Asana sync",
              "Leads whose Asana task the sync last left in Rights Secured. Shows — until the Asana sync has recorded stages.")

    d1, d2, d3 = st.columns(3)
    with d1:
        _card("Median time to first reply", format_duration_hours(selected["median_reply_hours"]),
              f"from {len(selected['reply_hours'])} repl{'y' if len(selected['reply_hours']) == 1 else 'ies'}",
              "Measured from the intro email to when the reply was LOGGED, which can be hours after the creator sent it.")
    with d2:
        _card("Needs follow-up", selected["intents"]["Lead / Needs Follow-up"], "replies worth a personal answer")
    with d3:
        _card("Hard bounce rate", format_rate(selected["bounce_rate_hard"]), f"{selected['bounced_hard']} bounced")

    # ---- activity over time
    st.subheader("Activity")
    series = daily_series(selected, RANGES[range_label])
    if series:
        frame = pd.DataFrame(series).set_index("date")
        st.area_chart(frame[["Sent", "Replies"]])
        st.caption("Days are UTC days (the Sheet's own clock). Replies are counted on the day they were logged.")
    else:
        st.info("Nothing has been sent or replied to yet.")

    # ---- reply quality
    st.subheader("What the replies say")
    intent_counts = [selected["intents"][label] for label in INTENT_ORDER]
    if sum(intent_counts):
        st.bar_chart(pd.DataFrame({"Creators": intent_counts}, index=INTENT_ORDER))
        unclassified = selected["intents"][INTENT_NOT_CLASSIFIED]
        if unclassified:
            st.caption(f"{unclassified} of {selected['responders']} replying creators have no AI reading yet "
                       "(older replies, or the AI key was not available when they arrived).")
    else:
        st.info("No genuine replies yet.")

    # ---- what worked best
    st.subheader("What worked best?")
    best = build_best_performers(perfs, combined)
    with st.container(border=True):
        lines = []
        for key, noun in (("campaign", "Campaign"), ("variant", "Template variant"), ("stage", "Follow-up stage")):
            entry = best[key]
            floor = best["min_contacted"] if key == "campaign" else best["min_sent"]
            unit = "contacted" if key == "campaign" else "sent"
            if entry is None:
                lines.append(f"**{noun}:** not enough volume yet to compare (needs {floor}+ {unit} in each).")
            else:
                lines.append(f"**{noun}:** {entry['label']} — {format_rate(entry['positive_rate'])} positive, "
                             f"{format_rate(entry['reply_rate'])} replied (of {entry['volume']} {unit}; "
                             f"{entry['compared']} compared).")
        st.markdown("\n\n".join(lines))
        st.caption("Ranked by positive replies first, then reply rate, and only among groups with enough volume. "
                   "A reply is credited to the exact email it answered when that can be matched, otherwise to the "
                   "latest email sent before it was logged.")

    # ---- follow-up stage and variant effectiveness
    st.subheader("Follow-up stage effectiveness")
    if selected["stages"]:
        _table(selected["stages"], "stage", "Stage")
    else:
        st.info("No emails sent yet.")
    st.subheader("Template variants")
    if selected["variants"]:
        _table(selected["variants"], "variant", "Variant")
    else:
        st.info("No variants sent yet.")

    # ---- deliverability
    st.subheader("Deliverability")
    e1, e2, e3 = st.columns(3)
    e1.metric("Hard bounces", selected["bounced_hard"], format_rate(selected["bounce_rate_hard"]), delta_color="off")
    e2.metric("Soft bounces", selected["bounced_soft"], format_rate(selected["bounce_rate_soft"]), delta_color="off")
    e3.metric("Failed sends", selected["send_failures"])
    if selected["top_send_errors"]:
        with st.expander("Most common sending errors"):
            for text, count in selected["top_send_errors"]:
                st.write(f"{count} × {text}")

    # ---- campaign cards
    st.divider()
    st.subheader("Campaigns")
    for perf in perfs:
        with st.container(border=True):
            chip = STATUS_CHIP.get(perf["status"], "🔴")
            st.markdown(f"{chip} **{perf['campaign']}** — {status_label(perf['status'])}")
            if perf["status_problems"]:
                st.caption("Needs attention: " + "; ".join(perf["status_problems"]))
            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("Contacted", perf["contacted"])
            m2.metric("Sent", perf["sent"])
            m3.metric("Replies", perf["replies"])
            m4.metric("Reply rate", format_rate(perf["reply_rate"]))
            m5.metric("Positive", perf["positive"])

if errors:
    with st.expander(f"{len(errors)} campaign(s) not shown yet (no runs made for them)"):
        for name, message in errors:
            st.write(f"**{name}** — {message}")
