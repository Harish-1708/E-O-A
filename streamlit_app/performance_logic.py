"""Pure logic behind the Performance page: the business questions the raw
email counts do not answer — how many replies were positive, which follow-up
and which template variant actually earn replies, how long a reply takes,
and how many creators reached negotiation or secured rights.

READ-ONLY. The headline totals (contacted, sent, replies, bounces) come
from the engine's own outreach.compute_all_campaigns_row — the very
function behind the Overview page and the dashboard tabs — so this page can
never show a different number for the same thing. Everything here is added
on top of that, not a second count.

Honest about what the data can and cannot say:
  * Every reply timestamp in the Sheet is when the engine NOTICED the reply,
    not when the creator sent it. "Time to first reply" is therefore "time
    until it was logged" and is labelled so.
  * A reply is credited to the exact email it answered when its In-Reply-To
    header matches one of our sent message IDs; only when that is missing is
    it credited by timing (the latest email sent before it was logged).
  * Open and click rates are deliberately absent: mail privacy features and
    image blocking make them unreliable.
"""
import os
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import Callable, Dict, List, Optional, Tuple

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import outreach  # noqa: E402
from campaign_status_logic import compute_campaign_status  # noqa: E402
from campaign_analytics_logic import build_error_summary  # noqa: E402
from parallel_logic import run_in_parallel  # noqa: E402

# A ranking of two things on a handful of emails is noise, not insight. The
# "what worked best" report only ranks groups with at least this many.
MIN_CONTACTED_FOR_RANKING = 30
MIN_SENT_FOR_RANKING = 30

INTENT_NOT_CLASSIFIED = "Not classified"
INTENT_ORDER = [outreach.INTENT_INTERESTED, outreach.INTENT_LEAD_FOLLOWUP, outreach.INTENT_NOT_INTERESTED,
                outreach.INTENT_UNCLEAR, INTENT_NOT_CLASSIFIED]

ASANA_NEGOTIATING = "Negotiating"
ASANA_RIGHTS_SECURED = "Rights Secured"


# ---------------------------------------------------------------- small helpers

def rate(numerator: int, denominator: int) -> Optional[float]:
    """numerator / denominator, or None when there is nothing to divide by —
    so "no data" is never displayed as a misleading 0.0%."""
    return (numerator / denominator) if denominator > 0 else None


def format_rate(value: Optional[float]) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def format_duration_hours(hours: Optional[float]) -> str:
    if hours is None:
        return "—"
    if hours < 1:
        return f"{hours * 60:.0f} min"
    if hours < 48:
        return f"{hours:.1f} hours"
    return f"{hours / 24:.1f} days"


def _dt(value) -> Optional[datetime]:
    parsed = outreach._parse_dt(value) if value else None
    return parsed.replace(tzinfo=None) if parsed and parsed.tzinfo else parsed


def _norm_message_id(value) -> str:
    return (value or "").strip().strip("<>").strip().lower()


def _ints(row: List[str]) -> Dict[str, int]:
    """outreach.compute_all_campaigns_row -> named integers."""
    return {"total_leads": int(row[1]), "contacted": int(row[2]), "sent": int(row[3]), "delivered_est": int(row[4]),
            "bounced_hard": int(row[5]), "bounced_soft": int(row[6]), "replies": int(row[7])}


# ---------------------------------------------------------------- the 7-day trend

TREND_DAYS = 7
MIN_EMAILS_PER_PERIOD = 5      # fewer emails than this in either week and the comparison is not shown: one reply would swing it by 20 points or more


def reply_rate_trend(sent_rows: List[Dict], answered_emails, today: Optional[date] = None, days: int = TREND_DAYS, min_emails: int = MIN_EMAILS_PER_PERIOD) -> Optional[Dict]:
    """How the reply rate moved: emails sent in the last `days` days against the `days` before them (UTC days, the Sheet's own clock), each as the share
    of those emails that have been answered. An email counts as answered when a lead's first reply is credited to it — the same rule the rest of the
    page uses. Returns None when either period has fewer than `min_emails` emails: not enough to compare.

    Replies take days to arrive, so the most recent period is always still filling in: its rate can rise later, and a small dip may only mean replies have
    not come in yet."""
    today = today or datetime.now(timezone.utc).date()
    current_days = {(today - timedelta(days=i)) for i in range(days)}
    previous_days = {(today - timedelta(days=days + i)) for i in range(days)}
    counts = {"current": [0, 0], "previous": [0, 0]}                 # [sent, answered]
    for row in sent_rows:
        when = _dt(row.get("Timestamp"))
        if not when:
            continue
        period = "current" if when.date() in current_days else "previous" if when.date() in previous_days else None
        if period:
            counts[period][0] += 1
            counts[period][1] += (str(row.get("LeadID", "")), row.get("Stage", "") or "") in answered_emails
    (sent_now, answered_now), (sent_before, answered_before) = counts["current"], counts["previous"]
    if sent_now < min_emails or sent_before < min_emails:
        return None
    rate_now, rate_before = answered_now / sent_now, answered_before / sent_before
    return {"days": days, "current_rate": rate_now, "previous_rate": rate_before, "delta_pts": round((rate_now - rate_before) * 100, 1),
            "sent_current": sent_now, "sent_previous": sent_before, "answered_current": answered_now, "answered_previous": answered_before}


# ---------------------------------------------------------------- one campaign

def build_campaign_performance(campaign_cfg: Dict, leads: List[Dict], responses: List[Dict],
                               send_log: List[Dict], error_log: Optional[List[Dict]] = None, today: Optional[date] = None) -> Dict:
    """`error_log=None` means the Error Log tab could not be read (as opposed
    to read and empty): the error table is then left empty and flagged
    (`error_log_read` False) rather than failing the whole report."""
    stages = campaign_cfg.get("stages") or []
    stage_names = [s["name"] for s in stages]
    name = campaign_cfg.get("_campaign_name", "")

    totals = _ints(outreach.compute_all_campaigns_row(name, leads, responses, send_log, stages))
    leads_by_id = {str(l.get("LeadID", "")): l for l in leads if str(l.get("LeadID", ""))}

    # ---- what each sent email was (stage, variant), keyed by its message id
    sent_rows = [r for r in send_log if r.get("Status") == "sent"]
    email_by_message_id: Dict[str, Tuple[str, str]] = {}
    for r in sent_rows:
        mid = _norm_message_id(r.get("MessageID"))
        if mid:
            email_by_message_id[mid] = (r.get("Stage", "") or "", r.get("Variant", "") or "")

    stage_sent = Counter(r.get("Stage", "") or "" for r in sent_rows)
    variant_sent = Counter((r.get("Variant", "") or "") for r in sent_rows if r.get("Variant"))
    stage_variant_sent = Counter((r.get("Stage", "") or "", r.get("Variant", "") or "") for r in sent_rows
                                 if r.get("Stage") and r.get("Variant"))

    # ---- who replied (genuine replies only), one entry per lead
    genuine_by_lead: Dict[str, List[Dict]] = {}
    for r in responses:
        if r.get("Classification") == outreach.CLASSIFICATION_GENUINE:
            genuine_by_lead.setdefault(str(r.get("LeadID", "")), []).append(r)

    intents = {label: 0 for label in INTENT_ORDER}
    stage_replies: Counter = Counter()
    stage_positive: Counter = Counter()
    variant_replies: Counter = Counter()
    variant_positive: Counter = Counter()
    stage_variant_replies: Counter = Counter()
    stage_variant_positive: Counter = Counter()
    reply_hours: List[float] = []
    replies_by_day: Counter = Counter()
    exact_matches = 0
    answered_emails = set()                    # (lead, stage) of every email a first reply answered: what the 7-day trend counts as 'answered'

    for lead_id, rows in genuine_by_lead.items():
        ordered = sorted(rows, key=lambda r: (_dt(r.get("ReceivedAt")) is None, _dt(r.get("ReceivedAt")) or datetime.min))
        first = ordered[0]
        first_at = _dt(first.get("ReceivedAt"))

        intent = next((r.get("Intent") for r in reversed(ordered) if (r.get("Intent") or "").strip()), None)
        intent = intent if intent in outreach.INTENT_OPTIONS else INTENT_NOT_CLASSIFIED
        intents[intent] += 1
        positive = intent == outreach.INTENT_INTERESTED

        if first_at:
            replies_by_day[first_at.date().isoformat()] += 1
            lead = leads_by_id.get(lead_id, {})
            intro_at = _dt(lead.get(outreach.stage_field_names(0)["sent_at"]))
            if intro_at and first_at >= intro_at:
                reply_hours.append((first_at - intro_at).total_seconds() / 3600)

        # ---- which email did they answer?
        answered: Optional[Tuple[str, str]] = None
        for token in (first.get("InReplyTo") or "").split():
            answered = email_by_message_id.get(_norm_message_id(token))
            if answered:
                exact_matches += 1
                break
        if answered is None and first_at:
            answered = _answered_by_timing(leads_by_id.get(lead_id, {}), stage_names, first_at)
        if answered:
            stage, variant = answered
            if stage:
                answered_emails.add((lead_id, stage))
                stage_replies[stage] += 1
                stage_positive[stage] += positive
            if variant:
                variant_replies[variant] += 1
                variant_positive[variant] += positive
            if stage and variant:
                stage_variant_replies[(stage, variant)] += 1
                stage_variant_positive[(stage, variant)] += positive

    def _group_rows(labels: List[str], sent: Counter, replies: Counter, positive: Counter, key: str) -> List[Dict]:
        rows = []
        for label in labels:
            if not sent[label] and not replies[label]:
                continue
            rows.append({key: label, "sent": sent[label], "replies": replies[label], "positive": positive[label],
                         "reply_rate": rate(replies[label], sent[label]),
                         "positive_rate": rate(positive[label], sent[label])})
        return rows

    known_stage_labels = stage_names + sorted((set(stage_sent) | set(stage_replies)) - set(stage_names) - {""})
    stage_rows = _group_rows(known_stage_labels, stage_sent, stage_replies, stage_positive, "stage")
    variant_rows = _group_rows(sorted(set(variant_sent) | set(variant_replies)), variant_sent, variant_replies,
                               variant_positive, "variant")

    stage_order = known_stage_labels
    variant_stage_keys = sorted(set(stage_variant_sent) | set(stage_variant_replies),
                                key=lambda k: (stage_order.index(k[0]) if k[0] in stage_order else len(stage_order), k[1]))
    variant_stage_rows = [{"stage": st_, "variant": v_, "sent": stage_variant_sent[(st_, v_)],
                           "replies": stage_variant_replies[(st_, v_)], "positive": stage_variant_positive[(st_, v_)],
                           "reply_rate": rate(stage_variant_replies[(st_, v_)], stage_variant_sent[(st_, v_)]),
                           "positive_rate": rate(stage_variant_positive[(st_, v_)], stage_variant_sent[(st_, v_)])}
                          for st_, v_ in variant_stage_keys]

    # ---- by sender account: the engine's own definition, kept exactly (replies
    # are leads this account contacted whose sequence stopped on a reply; the
    # rate is over the leads it contacted) so it matches the old Analytics tab
    contacted_leads = [l for l in leads
                       if any((l.get(outreach.stage_field_names(i)["sent_at"]) or "").strip() for i in range(len(stages)))]
    sender_rows = []
    for account in sorted({(l.get("SenderAccount") or "").strip() for l in contacted_leads if (l.get("SenderAccount") or "").strip()}):
        account_leads = [l for l in contacted_leads if (l.get("SenderAccount") or "").strip() == account]
        replies_n = sum(1 for l in account_leads if l.get("Status") == outreach.STATUS_STOPPED_REPLIED)
        sender_rows.append({"account": account, "leads": len(account_leads), "replies": replies_n,
                            "sent": sum(1 for r in sent_rows if r.get("SenderAccount") == account),
                            "reply_rate": rate(replies_n, len(account_leads))})

    # ---- sending problems
    failures = [r for r in send_log if (r.get("Status") or "").strip() not in ("", "sent")]
    top_errors = Counter(((r.get("Error") or "").strip()[:90] or "(no detail)") for r in failures).most_common(3)

    # ---- pipeline, from where the Asana sync last left each task
    synced = [(l.get("LastSyncedAsanaStage") or "").strip() for l in leads]
    pipeline_tracked = any(synced)

    sent_by_day: Counter = Counter()
    for r in sent_rows:
        stamp = _dt(r.get("Timestamp"))
        if stamp:
            sent_by_day[stamp.date().isoformat()] += 1

    responders = len(genuine_by_lead)
    positive_total = intents[outreach.INTENT_INTERESTED]

    # ---- the engine's own dashboard rows: the "Full details" view, and the
    # all-time error table (read from the Error Log tab)
    dashboard_rows = outreach.compute_campaign_dashboard(campaign_cfg, leads, responses, send_log, error_log or [])
    error_rows = []
    for item in build_error_summary(dashboard_rows):
        try:
            error_rows.append({"error_type": item["error_type"], "count": int(item["count"])})
        except (TypeError, ValueError):
            error_rows.append({"error_type": item["error_type"], "count": 0})

    last_stage_field = outreach.stage_field_names(len(stages) - 1)["sent_at"] if stages else None
    completed = sum(1 for l in leads if last_stage_field and (l.get(last_stage_field) or "").strip())
    return {
        "campaign": name,
        **totals,
        "pending": max(totals["total_leads"] - totals["contacted"], 0),
        "completed": completed,
        "sequence_completion": rate(completed, totals["contacted"]),
        "sender_rows": sender_rows,
        "error_rows": error_rows,
        "error_log_read": error_log is not None,
        "variant_stage_rows": [{**r} for r in variant_stage_rows],
        "reply_trend": reply_rate_trend(sent_rows, answered_emails, today),
        "dashboard_rows": dashboard_rows,
        "responders": responders,
        "reply_rate": rate(totals["replies"], totals["contacted"]),
        "intents": intents,
        "positive": positive_total,
        "positive_rate": rate(positive_total, totals["contacted"]),
        "bounce_rate_hard": rate(totals["bounced_hard"], totals["sent"]),
        "bounce_rate_soft": rate(totals["bounced_soft"], totals["sent"]),
        "send_failures": len(failures),
        "top_send_errors": top_errors,
        "negotiating": sum(1 for s in synced if s == ASANA_NEGOTIATING) if pipeline_tracked else None,
        "rights_secured": sum(1 for s in synced if s == ASANA_RIGHTS_SECURED) if pipeline_tracked else None,
        "reply_hours": reply_hours,
        "median_reply_hours": median(reply_hours) if reply_hours else None,
        "exact_matches": exact_matches,
        "stages": stage_rows,
        "variants": variant_rows,
        "sent_by_day": dict(sent_by_day),
        "replies_by_day": dict(replies_by_day),
    }


def _answered_by_timing(lead: Dict, stage_names: List[str], reply_at: datetime) -> Optional[Tuple[str, str]]:
    """The fallback when a reply's In-Reply-To cannot be matched to one of
    our message ids: the latest email sent to this lead before the reply was
    logged. Can be wrong if the next email went out before the reply was
    noticed — which is why the exact match is preferred."""
    best: Optional[Tuple[datetime, int]] = None
    for index in range(len(stage_names)):
        sent_at = _dt(lead.get(outreach.stage_field_names(index)["sent_at"]))
        if sent_at and sent_at <= reply_at and (best is None or sent_at >= best[0]):
            best = (sent_at, index)
    if best is None:
        return None
    index = best[1]
    return stage_names[index], (lead.get(outreach.stage_field_names(index)["variant"]) or "")


# ---------------------------------------------------------------- all campaigns together

def combine_performance(perfs: List[Dict], name: str = "All campaigns") -> Dict:
    """Totals across campaigns. Counts are added and every rate is recomputed
    from the added counts — never an average of rates, which would give a
    10-lead campaign the same weight as a 1,000-lead one."""
    def total(key: str) -> int:
        return sum(p[key] for p in perfs)

    def total_optional(key: str) -> Optional[int]:
        values = [p[key] for p in perfs if p[key] is not None]
        return sum(values) if values else None

    intents = {label: sum(p["intents"][label] for p in perfs) for label in INTENT_ORDER}
    reply_hours = [h for p in perfs for h in p["reply_hours"]]
    errors: Counter = Counter()
    for p in perfs:
        for text, count in p["top_send_errors"]:
            errors[text] += count

    def merge(rows_key: str, key: str, order: List[str]) -> List[Dict]:
        merged: Dict[str, Dict] = {}
        for p in perfs:
            for row in p[rows_key]:
                acc = merged.setdefault(row[key], {key: row[key], "sent": 0, "replies": 0, "positive": 0})
                acc["sent"] += row["sent"]
                acc["replies"] += row["replies"]
                acc["positive"] += row["positive"]
        ordered = [k for k in order if k in merged] + sorted(k for k in merged if k not in order)
        return [{**merged[k], "reply_rate": rate(merged[k]["replies"], merged[k]["sent"]),
                 "positive_rate": rate(merged[k]["positive"], merged[k]["sent"])} for k in ordered]

    sent_by_day: Counter = Counter()
    replies_by_day: Counter = Counter()
    for p in perfs:
        sent_by_day.update(p["sent_by_day"])
        replies_by_day.update(p["replies_by_day"])

    senders: Dict[str, Dict] = {}
    for p in perfs:
        for row in p["sender_rows"]:
            acc = senders.setdefault(row["account"], {"account": row["account"], "leads": 0, "replies": 0, "sent": 0})
            acc["leads"] += row["leads"]
            acc["replies"] += row["replies"]
            acc["sent"] += row["sent"]
    sender_rows = [{**senders[a], "reply_rate": rate(senders[a]["replies"], senders[a]["leads"])} for a in sorted(senders)]

    error_totals: Counter = Counter()
    for p in perfs:
        for row in p["error_rows"]:
            error_totals[row["error_type"]] += row["count"]
    error_rows = [{"error_type": k, "count": v} for k, v in sorted(error_totals.items(), key=lambda kv: (-kv[1], kv[0]))]

    variant_stage: Dict[Tuple[str, str], Dict] = {}
    for p in perfs:
        for row in p["variant_stage_rows"]:
            acc = variant_stage.setdefault((row["stage"], row["variant"]),
                                           {"stage": row["stage"], "variant": row["variant"], "sent": 0, "replies": 0, "positive": 0})
            acc["sent"] += row["sent"]
            acc["replies"] += row["replies"]
            acc["positive"] += row["positive"]
    stage_rank = {n: i for i, n in enumerate(outreach.CANONICAL_STAGE_ORDER)}
    variant_stage_rows = [{**variant_stage[k], "reply_rate": rate(variant_stage[k]["replies"], variant_stage[k]["sent"]),
                           "positive_rate": rate(variant_stage[k]["positive"], variant_stage[k]["sent"])}
                          for k in sorted(variant_stage, key=lambda k: (stage_rank.get(k[0], len(stage_rank)), k[0], k[1]))]

    contacted, sent = total("contacted"), total("sent")
    return {
        "campaign": name,
        "pending": total("pending"), "completed": total("completed"),
        "sequence_completion": rate(total("completed"), contacted),
        "sender_rows": sender_rows, "error_rows": error_rows,
        "error_log_read": all(p["error_log_read"] for p in perfs),
        "variant_stage_rows": variant_stage_rows,
        "reply_trend": None,                      # (a trend is only worked out inside one campaign)
        "dashboard_rows": None,       # raw engine rows only make sense for one campaign
        "total_leads": total("total_leads"), "contacted": contacted, "sent": sent,
        "delivered_est": total("delivered_est"), "bounced_hard": total("bounced_hard"),
        "bounced_soft": total("bounced_soft"), "replies": total("replies"), "responders": total("responders"),
        "reply_rate": rate(total("replies"), contacted),
        "intents": intents, "positive": intents[outreach.INTENT_INTERESTED],
        "positive_rate": rate(intents[outreach.INTENT_INTERESTED], contacted),
        "bounce_rate_hard": rate(total("bounced_hard"), sent), "bounce_rate_soft": rate(total("bounced_soft"), sent),
        "send_failures": total("send_failures"), "top_send_errors": errors.most_common(3),
        "negotiating": total_optional("negotiating"), "rights_secured": total_optional("rights_secured"),
        "reply_hours": reply_hours, "median_reply_hours": median(reply_hours) if reply_hours else None,
        "exact_matches": total("exact_matches"),
        "stages": merge("stages", "stage", outreach.CANONICAL_STAGE_ORDER),
        "variants": merge("variants", "variant", []),
        "sent_by_day": dict(sent_by_day), "replies_by_day": dict(replies_by_day),
    }


def daily_series(perf: Dict, days: Optional[int], today: Optional[date] = None) -> List[Dict]:
    """[{date, Sent, Replies}] one entry per day, zeros included so a quiet
    day shows as a dip, not a gap. `days` = the last N days up to `today`
    (UTC — the Sheet's own clock); None = from the first recorded day."""
    today = today or datetime.now(timezone.utc).date()
    recorded = sorted({date.fromisoformat(d) for d in list(perf["sent_by_day"]) + list(perf["replies_by_day"])})
    if not recorded:
        return []
    start = recorded[0] if days is None else today - timedelta(days=days - 1)
    end = max(today, recorded[-1]) if days is None else today
    series, day = [], start
    while day <= end:
        key = day.isoformat()
        series.append({"date": key, "Sent": perf["sent_by_day"].get(key, 0), "Replies": perf["replies_by_day"].get(key, 0)})
        day += timedelta(days=1)
    return series


# ---------------------------------------------------------------- "what worked best?"

def build_best_performers(perfs: List[Dict], combined: Dict) -> Dict:
    """The leader — by positive-reply rate, then reply rate — among campaigns,
    variants and follow-up stages that have enough volume to mean something.
    Any group below the minimum is left out entirely and the report says so
    (`enough_data` False) instead of crowning a winner on a handful of sends."""
    def best(candidates: List[Dict], label_key: str, base_key: str, minimum: int) -> Optional[Dict]:
        eligible = [c for c in candidates if c[base_key] >= minimum]
        if not eligible:
            return None
        top = max(eligible, key=lambda c: ((c["positive_rate"] or 0.0), (c["reply_rate"] or 0.0), c[base_key]))
        return {"label": top[label_key], "positive_rate": top["positive_rate"], "reply_rate": top["reply_rate"],
                "volume": top[base_key], "compared": len(eligible)}

    campaign_candidates = [{"label": p["campaign"], "positive_rate": p["positive_rate"], "reply_rate": p["reply_rate"],
                            "contacted": p["contacted"]} for p in perfs]
    return {
        "campaign": best(campaign_candidates, "label", "contacted", MIN_CONTACTED_FOR_RANKING),
        "variant": best(combined["variants"], "variant", "sent", MIN_SENT_FOR_RANKING),
        "stage": best(combined["stages"], "stage", "sent", MIN_SENT_FOR_RANKING),
        "min_contacted": MIN_CONTACTED_FOR_RANKING, "min_sent": MIN_SENT_FOR_RANKING,
    }


# ---------------------------------------------------------------- loading every campaign

def build_all_performance(campaign_names: List[str],
                          fetch_campaign_data: Callable[[str], Tuple],
                          max_workers: int = 1) -> Tuple[List[Dict], List[Tuple[str, str]]]:
    """Returns (perfs, errors). Each perf carries `status` and `status_problems`
    (the same status the Campaigns page shows). A campaign whose data cannot
    be read is reported in `errors` and never blocks the others; deleted
    campaigns are skipped; results keep campaign_names order."""
    names = list(campaign_names)
    results = run_in_parallel([(lambda n=name: fetch_campaign_data(n)) for name in names], max_workers)
    perfs: List[Dict] = []
    errors: List[Tuple[str, str]] = []
    for name, (ok, value) in zip(names, results):
        if not ok:
            errors.append((name, str(value)))
            continue
        campaign_cfg, leads, responses, send_log, *rest = value
        error_log = rest[0] if rest else None            # None = the Error Log tab could not be read
        if (campaign_cfg.get("status") or "active") == "deleted":
            continue
        perf = build_campaign_performance(campaign_cfg, leads, responses, send_log, error_log)
        perf["campaign"] = name
        perf["status"], perf["status_problems"] = compute_campaign_status(campaign_cfg, leads)
        perfs.append(perf)
    return perfs, errors


AT_A_GLANCE_COLUMNS = ["Campaign", "Status", "Total leads", "Contacted", "Pending", "Emails sent", "Delivered (est.)",
                       "Hard bounces", "Soft bounces", "Replies", "Reply rate", "Sequence completion"]


def at_a_glance_rows(perfs: List[Dict], status_labels: Callable[[str], str] = lambda s: s) -> List[List[str]]:
    """One row per campaign with every figure the old Overview page showed
    (it listed Total Leads, Unique Contacted, Pending, Total Sent, Delivered
    est., both bounce kinds, Replies, Reply Rate, Sequence Completion), in
    AT_A_GLANCE_COLUMNS order, as display strings."""
    return [[p["campaign"], status_labels(p.get("status", "")), str(p["total_leads"]), str(p["contacted"]),
             str(p["pending"]), str(p["sent"]), str(p["delivered_est"]), str(p["bounced_hard"]), str(p["bounced_soft"]),
             str(p["replies"]), format_rate(p["reply_rate"]), format_rate(p["sequence_completion"])] for p in perfs]


# ---------------------------------------------------------------- the "All campaigns at a glance" table

GLANCE_SORT_OPTIONS = ["Reply rate", "Replies", "Emails sent", "Name"]
# The words on a status chip in the table: the same ones the Campaigns list uses. A status not listed here falls back to the page's own label for it.
GLANCE_STATUS_TEXT = {"running": "● Running", "paused": "Paused", "draft": "Draft", "completed": "✓ Completed", "attention": "⚠ Attention needed"}


def sort_perfs(perfs: List[Dict], sort: str = "Reply rate") -> List[Dict]:
    """The campaigns in the order the table shows them: best reply rate first (a campaign with no rate yet last), most replies first, most emails first, or by name. An
    unknown choice falls back to the reply rate. Ties are always broken by name so the order is stable."""
    def name(p):
        return str(p.get("campaign") or "").lower()
    if sort == "Replies":
        return sorted(perfs, key=lambda p: (-int(p.get("replies") or 0), name(p)))
    if sort == "Emails sent":
        return sorted(perfs, key=lambda p: (-int(p.get("sent") or 0), name(p)))
    if sort == "Name":
        return sorted(perfs, key=name)
    return sorted(perfs, key=lambda p: (p.get("reply_rate") is None, -(p.get("reply_rate") or 0), name(p)))


def _glance_figures(p: Dict) -> Dict:
    return {"leads": int(p.get("total_leads") or 0), "contacted": int(p.get("contacted") or 0), "pending": int(p.get("pending") or 0), "sent": int(p.get("sent") or 0),
            "delivered": int(p.get("delivered_est") or 0), "hard": int(p.get("bounced_hard") or 0), "soft": int(p.get("bounced_soft") or 0), "replies": int(p.get("replies") or 0),
            "positive": int(p.get("positive") or 0), "reply_rate": p.get("reply_rate"), "reply_rate_text": format_rate(p.get("reply_rate")), "completion": p.get("sequence_completion"),
            "completion_text": format_rate(p.get("sequence_completion")), "completed": int(p.get("completed") or 0)}


def glance_rows(perfs: List[Dict], status_labels: Callable[[str], str] = lambda s: s, sort: str = "Reply rate") -> List[Dict]:
    """One dict per campaign for the table, in the chosen order. `best` marks the campaign with the single highest reply rate, but only when two or more campaigns have a rate
    and one of them is strictly ahead (a star on a tie, or on the only campaign, would mean nothing)."""
    ordered = sort_perfs(perfs, sort)
    rows = [{"name": p.get("campaign"), "status": p.get("status", ""), "status_label": GLANCE_STATUS_TEXT.get(p.get("status", "")) or status_labels(p.get("status", "")), "best": False,
             "problems": [str(m) for m in (p.get("status_problems") or [])], **_glance_figures(p)} for p in ordered]
    rated = [r for r in rows if r["reply_rate"] is not None and r["contacted"] > 0]
    if len(rated) >= 2:
        top = max(r["reply_rate"] for r in rated)
        leaders = [r for r in rated if r["reply_rate"] == top]
        if len(leaders) == 1:
            leaders[0]["best"] = True
    return rows


def glance_totals(combined: Dict, campaigns: int) -> Dict:
    """The total row: the combined figures (added up by combine_performance with the same rules as each row, so it always agrees with them) and how many campaigns."""
    return {"campaigns": int(campaigns), **_glance_figures(combined)}


def glance_csv(perfs: List[Dict], status_labels: Callable[[str], str] = lambda s: s, sort: str = "Reply rate") -> bytes:
    """The table as a CSV with every column the old table had, in the order shown on screen."""
    import csv
    import io
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(AT_A_GLANCE_COLUMNS)
    for row in at_a_glance_rows(sort_perfs(perfs, sort), status_labels):
        writer.writerow(row)
    return buffer.getvalue().encode("utf-8")
