"""The Performance display, shared by the Performance page (all campaigns, or
one) and by every campaign's own Performance tab — one renderer, so the two
can never show different things.

READ-ONLY: it draws what performance_logic computed and nothing else. It
deliberately uses no nested tabs, so the campaign page keeps exactly its six
tabs.
"""
from typing import Dict, List, Optional

import pandas as pd
import streamlit as st

from performance_logic import (
    daily_series, format_rate, format_duration_hours, INTENT_ORDER, INTENT_NOT_CLASSIFIED,
)

RANGES = {"Last 7 days": 7, "Last 30 days": 30, "Last 90 days": 90, "All time": None}


def _card(label: str, value, caption: str = "", help_text: Optional[str] = None):
    with st.container(border=True):
        st.metric(label, value, help=help_text)
        if caption:
            st.caption(caption)


def _table(rows: List[Dict], key: str, header: str):
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


def render_performance(perf: Dict, *, key_prefix: str, best: Optional[Dict] = None,
                       show_campaign_ranking: bool = True) -> None:
    """Draws every Performance section for `perf` (one campaign, or the
    combined totals of all of them). `best` is build_best_performers' result,
    or None to leave the "what worked best" report out;
    `show_campaign_ranking` is False inside a single campaign, where ranking
    campaigns against each other would be meaningless."""
    # ---- headline cards
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    with c1:
        _card("Contacted", perf["contacted"], f"of {perf['total_leads']} leads · {perf['pending']} not yet contacted")
    with c2:
        _card("Emails sent", perf["sent"], f"{perf['send_failures']} failed" if perf["send_failures"] else "")
    with c3:
        _card("Reply rate", format_rate(perf["reply_rate"]), f"{perf['replies']} replied")
    with c4:
        _card("Positive replies", perf["positive"], f"{format_rate(perf['positive_rate'])} of contacted",
              "Replies the AI read as Interested. Replies without an AI reading are counted under 'Not classified' below.")
    with c5:
        _card("Negotiating", "—" if perf["negotiating"] is None else perf["negotiating"], "from the Asana sync",
              "Leads whose Asana task the sync last left in Negotiating. Shows — until the Asana sync has recorded stages.")
    with c6:
        _card("Rights secured", "—" if perf["rights_secured"] is None else perf["rights_secured"], "from the Asana sync",
              "Leads whose Asana task the sync last left in Rights Secured. Shows — until the Asana sync has recorded stages.")

    d1, d2, d3, d4 = st.columns(4)
    n_replies = len(perf["reply_hours"])
    with d1:
        _card("Median time to first reply", format_duration_hours(perf["median_reply_hours"]),
              f"from {n_replies} repl{'y' if n_replies == 1 else 'ies'}",
              "Measured from the intro email to when the reply was LOGGED, which can be hours after the creator sent it.")
    with d2:
        _card("Needs follow-up", perf["intents"]["Lead / Needs Follow-up"], "replies worth a personal answer")
    with d3:
        _card("Hard bounce rate", format_rate(perf["bounce_rate_hard"]), f"{perf['bounced_hard']} bounced")
    with d4:
        _card("Sequence completion", format_rate(perf["sequence_completion"]),
              f"{perf['completed']} reached the final stage")

    # ---- activity over time
    st.subheader("Activity")
    range_label = st.selectbox("Chart range", list(RANGES), index=1, key=f"{key_prefix}_range")
    series = daily_series(perf, RANGES[range_label])
    if series:
        frame = pd.DataFrame(series).set_index("date")
        st.area_chart(frame[["Sent", "Replies"]])
        st.caption("Days are UTC days (the Sheet's own clock). Replies are counted on the day they were logged.")
    else:
        st.info("Nothing has been sent or replied to yet.")

    # ---- reply quality
    st.subheader("What the replies say")
    intent_counts = [perf["intents"][label] for label in INTENT_ORDER]
    if sum(intent_counts):
        st.bar_chart(pd.DataFrame({"Creators": intent_counts}, index=INTENT_ORDER))
        unclassified = perf["intents"][INTENT_NOT_CLASSIFIED]
        if unclassified:
            st.caption(f"{unclassified} of {perf['responders']} replying creators have no AI reading yet "
                       "(older replies, or the AI key was not available when they arrived).")
    else:
        st.info("No genuine replies yet.")

    # ---- what worked best
    if best is not None:
        st.subheader("What worked best?")
        with st.container(border=True):
            lines = []
            entries = [("variant", "Template variant"), ("stage", "Follow-up stage")]
            if show_campaign_ranking:
                entries.insert(0, ("campaign", "Campaign"))
            for key, noun in entries:
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
    if perf["stages"]:
        _table(perf["stages"], "stage", "Stage")
    else:
        st.info("No emails sent yet.")
    st.subheader("Template variants")
    if perf["variants"]:
        _table(perf["variants"], "variant", "Variant")
        if perf["variant_stage_rows"]:
            with st.expander("Each variant, stage by stage"):
                st.dataframe(
                    {
                        "Stage": [r["stage"] for r in perf["variant_stage_rows"]],
                        "Variant": [r["variant"] for r in perf["variant_stage_rows"]],
                        "Emails sent": [r["sent"] for r in perf["variant_stage_rows"]],
                        "Replies": [r["replies"] for r in perf["variant_stage_rows"]],
                        "Reply rate": [format_rate(r["reply_rate"]) for r in perf["variant_stage_rows"]],
                        "Positive": [r["positive"] for r in perf["variant_stage_rows"]],
                    },
                    width="stretch",
                    hide_index=True,
                )
    else:
        st.info("No variants sent yet.")

    # ---- by sender account
    st.subheader("By sender account")
    if perf["sender_rows"]:
        st.dataframe(
            {
                "Account": [r["account"] for r in perf["sender_rows"]],
                "Emails sent": [r["sent"] for r in perf["sender_rows"]],
                "Leads contacted": [r["leads"] for r in perf["sender_rows"]],
                "Replies": [r["replies"] for r in perf["sender_rows"]],
                "Reply rate": [format_rate(r["reply_rate"]) for r in perf["sender_rows"]],
            },
            width="stretch",
            hide_index=True,
        )
    else:
        st.caption("No sends yet.")

    # ---- deliverability and errors
    st.subheader("Deliverability")
    e1, e2, e3 = st.columns(3)
    e1.metric("Hard bounces", perf["bounced_hard"], format_rate(perf["bounce_rate_hard"]), delta_color="off")
    e2.metric("Soft bounces", perf["bounced_soft"], format_rate(perf["bounce_rate_soft"]), delta_color="off")
    e3.metric("Failed sends", perf["send_failures"])
    if perf["top_send_errors"]:
        with st.expander("Most common sending errors"):
            for text, count in perf["top_send_errors"]:
                st.write(f"{count} × {text}")
    if perf["error_rows"]:
        st.subheader("Errors (all time)")
        st.dataframe(
            {"Error type": [r["error_type"] for r in perf["error_rows"]], "Count": [r["count"] for r in perf["error_rows"]]},
            width="stretch",
            hide_index=True,
        )
    elif not perf["error_log_read"]:
        st.caption("The Error Log tab could not be read, so all-time errors are not shown.")

    # ---- the engine's raw figures (what the old Dashboard page showed)
    if perf.get("dashboard_rows"):
        with st.expander("Full details — the same figures as the Sheet's Dashboard tab"):
            sections: Dict[str, List] = {}
            for section, metric, value in perf["dashboard_rows"]:
                sections.setdefault(section, []).append((metric, value))
            for section, metrics in sections.items():
                st.markdown(f"**{section}**")
                st.table({"Metric": [m for m, _ in metrics], "Value": [v for _, v in metrics]})
