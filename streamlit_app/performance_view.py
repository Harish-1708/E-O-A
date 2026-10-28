"""The Performance display, shared by the Performance page (all campaigns, or
one) and by every campaign's own Performance tab — one renderer, so the two
can never show different things.

READ-ONLY: it draws what performance_logic computed and nothing else — every
figure is passed straight to performance_html, which only formats it. It
deliberately uses no nested tabs, so the campaign page keeps exactly its six
tabs.
"""
from typing import Callable, Dict, List, Optional

import streamlit as st

import performance_html as H
from leads_logic import humanize_stage
from performance_figures import build_performance_csv, export_filename, headline_figures
from performance_logic import (
    daily_series, format_rate, INTENT_ORDER, INTENT_NOT_CLASSIFIED, OUTCOME_NAMES, outcome_order,
)

RANGES = {"Last 7 days": 7, "Last 30 days": 30, "Last 90 days": 90, "All time": None}
_RATE_HEADERS = ["Emails sent", "Replies", "Reply rate", "Positive", "Positive rate"]


def _show(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def _effectiveness_card(title: str, hint: str, first_header: str, key: str, rows: List[Dict], label=lambda value: value) -> str:
    table = H.table_html([first_header] + _RATE_HEADERS, [
        [label(r[key]), r["sent"], r["replies"], format_rate(r["reply_rate"]), r["positive"], format_rate(r["positive_rate"])] for r in rows])
    return H.card_html(title, hint, table)


def render_performance(perf: Dict, *, key_prefix: str, best: Optional[Dict] = None,
                       show_campaign_ranking: bool = True, actions: Optional[Callable[[], None]] = None,
                       export_name: str = "performance") -> None:
    """Draws every Performance section for `perf` (one campaign, or the
    combined totals of all of them). `best` is build_best_performers' result,
    or None to leave the "what worked best" report out;
    `show_campaign_ranking` is False inside a single campaign, where ranking
    campaigns against each other would be meaningless. `actions`, when given,
    draws extra buttons (such as Refresh) beside the Export button;
    `export_name` names the downloaded file."""
    _show(f"<style>{H.PERFORMANCE_CSS}</style>")

    # ---- headline cards (the very same figures the Export file contains)
    cards = [H.kpi_html(f["label"], f["value"], f["caption"], f["help"], f["trend"]) for f in headline_figures(perf)]
    _show(H.grid_html("g6", cards[:6]))
    _show(H.grid_html("g5" if len(cards) > 10 else "g4", cards[6:]))                # five cards on the second row for an HTML campaign (the Unsubscribed card)

    # ---- where every lead is right now (each lead is in exactly one place, so these add up to the leads)
    outcomes = perf.get("outcomes") or {}
    outcome_rows = [(OUTCOME_NAMES.get(label, label), int(outcomes.get(label, 0))) for label in outcome_order(perf) if int(outcomes.get(label, 0)) > 0]
    if outcome_rows:
        _show(H.card_html("Where your leads are now", f"Every lead is in exactly one place, so these add up to your {perf['total_leads']} leads. "
                          "A lead that finished the sequence moves to Replied if it answers later.", H.bars_html(outcome_rows, H.OUTCOME_COLOURS)))

    # ---- activity over time, and what the replies say
    with st.container(key=f"pf_toolbar_{key_prefix}"):
        range_label = st.radio("Chart range", list(RANGES), index=1, horizontal=True, key=f"{key_prefix}_range", label_visibility="collapsed")
        with st.container(key=f"pf_tools_{key_prefix}"):
            if actions is not None:
                actions()
            st.download_button("⤓ Export", data=build_performance_csv(perf, export_name).encode("utf-8"), file_name=export_filename(export_name),
                               mime="text/csv", key=f"{key_prefix}_export")
    series = daily_series(perf, RANGES[range_label])
    left, right = st.columns([1.6, 1])
    with left:
        if series:
            _show(H.card_html("Activity", "Days are UTC days (the Sheet's own clock). Replies are counted on the day they were logged.",
                              H.area_chart_svg(series), [("Sent", "sent"), ("Replies", "replies")]))
        else:
            st.subheader("Activity")
            st.info("Nothing has been sent or replied to yet.")
    with right:
        intent_counts = [(label, perf["intents"][label]) for label in INTENT_ORDER]
        if sum(count for _, count in intent_counts):
            unclassified = perf["intents"][INTENT_NOT_CLASSIFIED]
            note = (H.note_html(f"{unclassified} of {perf['responders']} replying creators have no AI reading yet "
                                "(older replies, or the AI key was not available when they arrived).") if unclassified else "")
            _show(H.card_html("What the replies say", f"{perf['responders']} replying creators",
                              H.bars_html(intent_counts, H.INTENT_COLOURS) + note))
        else:
            st.subheader("What the replies say")
            st.info("No genuine replies yet.")

    # ---- what worked best
    if best is not None:
        entries = [("variant", "Template variant"), ("stage", "Follow-up stage")]
        if show_campaign_ranking:
            entries.insert(0, ("campaign", "Campaign"))
        lines = []
        for key, noun in entries:
            entry = best[key]
            floor = best["min_contacted"] if key == "campaign" else best["min_sent"]
            unit = "contacted" if key == "campaign" else "sent"
            if entry is None:
                lines.append((f"{noun}:", f"not enough volume yet to compare (needs {floor}+ {unit} in each)."))
            else:
                lines.append((f"{noun}:", f"{entry['label']} — {format_rate(entry['positive_rate'])} positive, "
                                          f"{format_rate(entry['reply_rate'])} replied (of {entry['volume']} {unit}; "
                                          f"{entry['compared']} compared)."))
        _show(H.callout_html("What worked best?", lines,
                             "Ranked by positive replies first, then reply rate, and only among groups with enough volume. "
                             "A reply is credited to the exact email it answered when that can be matched, otherwise to the "
                             "latest email sent before it was logged."))

    # ---- follow-up stage and variant effectiveness (two cards side by side, the same height)
    stage_card = (_effectiveness_card("Follow-up stage effectiveness", "Positive = replies the AI read as interested.", "Stage", "stage", perf["stages"], humanize_stage)
                  if perf["stages"] else None)
    best_variant = ((best or {}).get("variant") or {}).get("label")                                   # the variant the "what worked best" box names
    variant_card = (_effectiveness_card("Template variants", "Each reply is credited to the exact email it answered.", "Variant", "variant", perf["variants"],
                                        lambda value: f"{value}  ★ best" if best_variant is not None and str(value) == str(best_variant) else value)
                    if perf["variants"] else None)
    if stage_card and variant_card:
        _show(H.grid_html("g2e", [stage_card, variant_card]))
    else:
        left, right = st.columns(2)
        with left:
            if stage_card:
                _show(stage_card)
            else:
                st.subheader("Follow-up stage effectiveness")
                st.info("No emails sent yet.")
        with right:
            if variant_card:
                _show(variant_card)
            else:
                st.subheader("Template variants")
                st.info("No variants sent yet.")
    if variant_card and perf["variant_stage_rows"]:
        with st.expander("Each variant, stage by stage"):
            st.dataframe(
                {
                    "Stage": [humanize_stage(r["stage"]) for r in perf["variant_stage_rows"]],
                    "Variant": [r["variant"] for r in perf["variant_stage_rows"]],
                    "Emails sent": [r["sent"] for r in perf["variant_stage_rows"]],
                    "Replies": [r["replies"] for r in perf["variant_stage_rows"]],
                    "Reply rate": [format_rate(r["reply_rate"]) for r in perf["variant_stage_rows"]],
                    "Positive": [r["positive"] for r in perf["variant_stage_rows"]],
                },
                width="stretch",
                hide_index=True,
            )

    # ---- by sender account, and deliverability (again the same height)
    top_rate = max([r["reply_rate"] or 0 for r in perf["sender_rows"]] + [0])
    sender_card = (H.card_html("By sender account", "", H.table_html(
        ["Account", "Emails sent", "Leads contacted", "Replies", "Reply rate"],
        [[r["account"], r["sent"], r["leads"], r["replies"], H.bar_cell((r["reply_rate"] or 0) / top_rate if top_rate else 0, format_rate(r["reply_rate"]))]
         for r in perf["sender_rows"]])) if perf["sender_rows"] else None)
    deliverability_card = H.card_html("Deliverability", "Hard bounces stop a lead; soft bounces are retried.", H.grid_html("g3", [
        H.kpi_html("Hard bounces", perf["bounced_hard"], format_rate(perf["bounce_rate_hard"])),
        H.kpi_html("Soft bounces", perf["bounced_soft"], format_rate(perf["bounce_rate_soft"])),
        H.kpi_html("Failed sends", perf["send_failures"]),
    ]))
    if sender_card:
        _show(H.grid_html("g2e", [sender_card, deliverability_card]))
    else:
        left, right = st.columns(2)
        with left:
            st.subheader("By sender account")
            st.caption("No sends yet.")
        with right:
            _show(deliverability_card)
    if perf["top_send_errors"]:
        with st.expander("Most common sending errors"):
            for text, count in perf["top_send_errors"]:
                st.write(f"{count} × {text}")
    if perf["error_rows"]:
        with st.expander("Errors (all time)"):
            _show(H.table_html(["Error type", "Count"], [[r["error_type"], r["count"]] for r in perf["error_rows"]]))
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
