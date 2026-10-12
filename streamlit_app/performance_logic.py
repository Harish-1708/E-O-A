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


# ---------------------------------------------------------------- one campaign

def build_campaign_performance(campaign_cfg: Dict, leads: List[Dict], responses: List[Dict],
                               send_log: List[Dict]) -> Dict:
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
    reply_hours: List[float] = []
    replies_by_day: Counter = Counter()
    exact_matches = 0

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
                stage_replies[stage] += 1
                stage_positive[stage] += positive
            if variant:
                variant_replies[variant] += 1
                variant_positive[variant] += positive

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
    return {
        "campaign": name,
        **totals,
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

    contacted, sent = total("contacted"), total("sent")
    return {
        "campaign": name,
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
                          fetch_campaign_data: Callable[[str], Tuple[Dict, List[Dict], List[Dict], List[Dict]]],
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
        campaign_cfg, leads, responses, send_log = value
        if (campaign_cfg.get("status") or "active") == "deleted":
            continue
        perf = build_campaign_performance(campaign_cfg, leads, responses, send_log)
        perf["campaign"] = name
        perf["status"], perf["status_problems"] = compute_campaign_status(campaign_cfg, leads)
        perfs.append(perf)
    return perfs, errors
