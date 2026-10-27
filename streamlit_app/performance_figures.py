"""The Performance headline figures and the CSV export, in one place so the cards on the page and the downloaded file can never disagree:
both are built from `headline_figures`, and every value in the export is formatted by the same functions the page uses.

PURE and READ-ONLY: it only formats what performance_logic already computed.
"""
import csv
import io
import re
from typing import Dict, List, Optional, Tuple

from leads_logic import humanize_stage
from performance_logic import INTENT_ORDER, INTENT_NOT_CLASSIFIED, OUTCOME_NAMES, OUTCOME_ORDER, format_duration_hours, format_rate

_DASH = "—"


def format_trend(trend: Optional[Dict]) -> Optional[Tuple[str, str]]:
    """('▲ 2.1 pts vs previous 7 days', 'up'), ('▼ 0.8 pts …', 'down') or ('▬ no change …', 'flat'); None when there is no trend to show."""
    if not trend:
        return None
    delta, days = trend["delta_pts"], trend["days"]
    if delta > 0:
        return f"▲ {delta:.1f} pts vs previous {days} days", "up"
    if delta < 0:
        return f"▼ {abs(delta):.1f} pts vs previous {days} days", "down"
    return f"▬ no change vs previous {days} days", "flat"


def trend_help(trend: Optional[Dict]) -> str:
    """The tooltip on the Reply rate card: the two periods behind the change, and why the latest one can still move."""
    if not trend:
        return ""
    days = trend["days"]
    return (f"Share of emails answered so far. Last {days} days: {format_rate(trend['current_rate'])} of {trend['sent_current']} emails. "
            f"The {days} days before: {format_rate(trend['previous_rate'])} of {trend['sent_previous']} emails. Replies can take several days to arrive, "
            "so the most recent days are still filling in and their rate may rise.")


def headline_figures(perf: Dict) -> List[Dict]:
    """The ten headline figures in the order the page shows them (six on the first row, four on the second), each as
    {label, value, caption, help}."""
    n_replies = len(perf["reply_hours"])
    trend = perf.get("reply_trend")
    figures = [
        {"label": "Contacted", "value": perf["contacted"], "caption": f"of {perf['total_leads']} leads · {perf['pending']} not yet contacted", "help": ""},
        {"label": "Emails sent", "value": perf["sent"], "caption": f"{perf['send_failures']} failed" if perf["send_failures"] else "", "help": ""},
        {"label": "Reply rate", "value": format_rate(perf["reply_rate"]), "caption": f"{perf['replies']} replied", "help": trend_help(trend)},
        {"label": "Positive replies", "value": perf["positive"], "caption": f"{format_rate(perf['positive_rate'])} of contacted",
         "help": "Replies the AI read as Interested. Replies without an AI reading are counted under 'Not classified' below."},
        {"label": "Negotiating", "value": _DASH if perf["negotiating"] is None else perf["negotiating"], "caption": "from the Asana sync",
         "help": "Leads whose Asana task the sync last left in Negotiating. Shows — until the Asana sync has recorded stages."},
        {"label": "Rights secured", "value": _DASH if perf["rights_secured"] is None else perf["rights_secured"], "caption": "from the Asana sync",
         "help": "Leads whose Asana task the sync last left in Rights Secured. Shows — until the Asana sync has recorded stages."},
        {"label": "Median time to first reply", "value": format_duration_hours(perf["median_reply_hours"]), "caption": f"from {n_replies} repl{'y' if n_replies == 1 else 'ies'}",
         "help": "Measured from the intro email to when the reply was LOGGED, which can be hours after the creator sent it."},
        {"label": "Needs follow-up", "value": perf["intents"]["Lead / Needs Follow-up"], "caption": "replies worth a personal answer", "help": ""},
        {"label": "Hard bounce rate", "value": format_rate(perf["bounce_rate_hard"]), "caption": f"{perf['bounced_hard']} bounced", "help": ""},
        {"label": "Sequence completion", "value": format_rate(perf["sequence_completion"]), "caption": f"{perf['completed']} reached the final stage", "help": ""},
    ]
    for figure in figures:
        figure["trend"] = format_trend(trend) if figure["label"] == "Reply rate" else None
    return figures


def export_filename(name: str) -> str:
    """'Kelson_Creators_Licensing_17SEP' -> 'Kelson_Creators_Licensing_17SEP_performance.csv'. Anything in a campaign name that is not a letter,
    digit, dot, dash or underscore (a slash, a space, a quote) becomes an underscore, so the name can never make an odd or unsafe file name."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(name or "")).strip("._-")
    return f"{safe or 'performance'}_performance.csv"


def safe_cell(value) -> str:
    """A spreadsheet treats a cell that starts with = + - @ (or a tab or return) as a FORMULA, and sender accounts and error text come from
    outside, so such a cell gets a leading apostrophe and is read as plain text."""
    text = str(value if value is not None else "")
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def build_performance_csv(perf: Dict, title: str = "Performance") -> str:
    """The Performance page as one CSV: the headline figures, what the replies say, and the stage, variant, sender and error tables."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")

    def section(name, header, rows):
        writer.writerow([])
        writer.writerow([safe_cell(name)])
        writer.writerow([safe_cell(h) for h in header])
        for row in rows:
            writer.writerow([safe_cell(cell) for cell in row])

    writer.writerow([safe_cell(f"{title} — Performance")])
    section("Headline figures", ["Figure", "Value", "Note"], [[f["label"], f["value"], f["caption"] + (f" · {f['trend'][0]}" if f["trend"] else "")] for f in headline_figures(perf)])
    outcomes = perf.get("outcomes") or {}
    total_leads = max(int(perf.get("total_leads") or 0), 0)
    section("Where the leads are now", ["Where", "Leads", "Share of leads"],
            [[OUTCOME_NAMES.get(label, label), outcomes.get(label, 0), format_rate(outcomes.get(label, 0) / total_leads) if total_leads else _DASH] for label in OUTCOME_ORDER])
    section("What the replies say", ["Reading", "Creators"], [[label, perf["intents"][label]] for label in INTENT_ORDER] + [[INTENT_NOT_CLASSIFIED, perf["intents"][INTENT_NOT_CLASSIFIED]]])
    rate_header = ["Emails sent", "Replies", "Reply rate", "Positive", "Positive rate"]
    rate = lambda r: [r["sent"], r["replies"], format_rate(r["reply_rate"]), r["positive"], format_rate(r["positive_rate"])]      # noqa: E731
    section("Follow-up stage effectiveness", ["Stage"] + rate_header, [[humanize_stage(r["stage"])] + rate(r) for r in perf["stages"]])
    section("Template variants", ["Variant"] + rate_header, [[r["variant"]] + rate(r) for r in perf["variants"]])
    section("By sender account", ["Account", "Emails sent", "Leads contacted", "Replies", "Reply rate"],
            [[r["account"], r["sent"], r["leads"], r["replies"], format_rate(r["reply_rate"])] for r in perf["sender_rows"]])
    section("Deliverability", ["Figure", "Value"], [["Hard bounces", perf["bounced_hard"]], ["Soft bounces", perf["bounced_soft"]], ["Failed sends", perf["send_failures"]]])
    section("Errors (all time)", ["Error type", "Count"], [[r["error_type"], r["count"]] for r in perf["error_rows"]])
    return out.getvalue()
