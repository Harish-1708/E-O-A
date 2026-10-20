"""Pure logic for the Campaigns Hub page (Phase A). Combines
campaign_status_logic's status computation with the same
outreach.compute_all_campaigns_row math the Overview page already uses.
"""
import os
import sys
from datetime import date, datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import outreach  # noqa: E402
from campaign_builder import validate_campaign_name  # noqa: E402
from campaign_status_logic import (  # noqa: E402
    STATUS_ATTENTION, STATUS_COMPLETED, STATUS_DRAFT, STATUS_PAUSED, STATUS_RUNNING, compute_campaign_status, status_label,
)
from parallel_logic import run_in_parallel  # noqa: E402


def compute_last_activity_timestamp(send_log: List[Dict]) -> str:
    """Most recent SendLog Timestamp string, or "" if the campaign has
    never sent anything. Returns the raw timestamp, not a relative
    string ("2 min ago") — that formatting depends on "now", which stays
    out of pure logic; the page computes it for display."""
    timestamps = [r.get("Timestamp", "") for r in send_log if r.get("Timestamp")]
    return max(timestamps) if timestamps else ""


EMPTY_SERIES = {"bars": [], "unit": "day", "start": "", "end": "", "total": 0}
MAX_DAILY_BARS = 30            # a campaign up to a month long is drawn day by day; a longer one by week
MAX_WEEKLY_BARS = 26           # and only its last half year, so the chart stays readable


def send_series(send_log: List[Dict], today: Optional[date] = None, through_today: bool = False) -> Dict:
    """How many emails went out in each day (or each week, for a very long campaign) of the WHOLE campaign: from its first send to its last, or to today while it is
    still running (so a running campaign that has gone quiet shows the quiet days). Counted the same way as the Sent number: send-log rows whose Status is 'sent'.
    Returns {'bars': [counts], 'unit': 'day'|'week', 'start': 'YYYY-MM-DD', 'end': 'YYYY-MM-DD', 'total': n}; no sends gives the empty series."""
    per_day: Dict[date, int] = {}
    for row in send_log or []:
        if row.get("Status") != "sent":
            continue
        try:
            when = datetime.strptime(str(row.get("Timestamp") or "")[:19], "%Y-%m-%d %H:%M:%S").date()
        except ValueError:
            continue
        per_day[when] = per_day.get(when, 0) + 1
    if not per_day:
        return dict(EMPTY_SERIES)
    first, last = min(per_day), max(per_day)
    end = max(last, today) if (through_today and today) else last
    span = (end - first).days + 1
    total = sum(per_day.values())
    if span <= MAX_DAILY_BARS:
        return {"bars": [per_day.get(first + timedelta(days=offset), 0) for offset in range(span)], "unit": "day", "start": first.isoformat(), "end": end.isoformat(), "total": total}
    weeks = -(-span // 7)
    bars = [0] * weeks
    for when, count in per_day.items():
        bars[(when - first).days // 7] += count
    skipped = max(0, weeks - MAX_WEEKLY_BARS)
    return {"bars": bars[skipped:], "unit": "week", "start": (first + timedelta(days=7 * skipped)).isoformat(), "end": end.isoformat(), "total": total}


def series_caption(series: Dict) -> str:
    """'Sends per day · Sep 3 – Sep 17' (or 'per week'); 'No sends yet' when there are none."""
    if not series or not series.get("bars"):
        return "No sends yet"
    start, end = date.fromisoformat(series["start"]), date.fromisoformat(series["end"])
    return f"Sends per {series['unit']} · {start.strftime('%b')} {start.day} – {end.strftime('%b')} {end.day}"


def _format_field(campaign_cfg: Dict) -> Dict:
    """{"format": "html"} for an HTML campaign and nothing at all for any other, so every plain-text campaign's row is exactly what it always was."""
    return {"format": "html"} if campaign_cfg.get("format") == "html" else {}


def build_draft_campaign_row(campaign_cfg: Dict) -> Dict:
    """A brand new Draft campaign may not have any Sheet tabs yet (they're
    created on first real activity) — its status is fully knowable from
    config alone, so this never needs a Sheet fetch at all."""
    return {
        "name": campaign_cfg["_campaign_name"], "status": "draft",
        "status_label": status_label("draft"), "problems": [],
        "total_leads": 0, "contacted": 0, "sent": 0, "replies": 0, "reply_rate": "—",
        "last_activity": "", "series": dict(EMPTY_SERIES), **_format_field(campaign_cfg),
    }


def build_deleted_campaign_row(campaign_cfg: Dict) -> Dict:
    """Same lightweight shape as a Draft row (no Sheet fetch needed) — a
    temporarily-removed campaign's history stays in the Sheet untouched,
    but the Deleted Campaigns list itself only needs to show what it is
    and let you Restore it, not a full analytics recompute."""
    return {
        "name": campaign_cfg["_campaign_name"], "status": "deleted",
        "status_label": status_label("deleted"), "problems": [],
        "total_leads": 0, "contacted": 0, "sent": 0, "replies": 0, "reply_rate": "—",
        "last_activity": "", "series": dict(EMPTY_SERIES), **_format_field(campaign_cfg),
    }


def build_campaign_hub_row(campaign_cfg: Dict, leads: List[Dict], responses: List[Dict],
                            send_log: List[Dict], today: Optional[date] = None) -> Dict:
    stages = campaign_cfg["stages"]
    base_row = outreach.compute_all_campaigns_row(campaign_cfg["_campaign_name"], leads, responses,
                                                    send_log, stages)
    status, problems = compute_campaign_status(campaign_cfg, leads)
    return {
        "name": campaign_cfg["_campaign_name"], "status": status,
        "status_label": status_label(status), "problems": problems,
        "total_leads": int(base_row[1]), "contacted": int(base_row[2]), "sent": int(base_row[3]),
        "replies": int(base_row[7]), "reply_rate": base_row[8],
        "last_activity": compute_last_activity_timestamp(send_log),
        "series": send_series(send_log, today or date.today(), through_today=(status == STATUS_RUNNING)),
        **_format_field(campaign_cfg),
    }


def build_campaigns_hub(
    campaign_names: List[str],
    get_campaign_cfg: Callable[[str], Dict],
    fetch_sheet_data: Callable[[Dict], Tuple[List[Dict], List[Dict], List[Dict]]],
    max_workers: int = 1,
) -> Tuple[List[Dict], List[Dict], List[Tuple[str, str]]]:
    """get_campaign_cfg(name) -> campaign_cfg (local, no network — never
    fails for a missing Sheet tab, only a missing templates folder).
    fetch_sheet_data(campaign_cfg) -> (leads, responses, send_log),
    called ONLY for non-draft, non-deleted campaigns, since neither needs
    Sheet data to show its status. Returns (rows, deleted_rows, errors):
    rows is the normal, everyday campaign list (deleted campaigns are
    deliberately never in here — that's the whole point of a temporary
    removal actually hiding them); deleted_rows is separate, for the
    Deleted Campaigns view; errors is [(campaign_name, message)] for a
    non-draft, non-deleted campaign whose Sheet data couldn't be read
    (e.g. a race right after creation, before its tabs exist yet) — those
    are skipped from rows rather than failing the page.

    max_workers > 1 fetches the active campaigns' sheet data at the same
    time instead of one campaign after another — the whole point, since
    each fetch is several slow Google round trips and a list of N
    campaigns used to cost N times that in a row. Output is unchanged:
    rows still come out in campaign_names order (never completion
    order), drafts and deleted campaigns are still never fetched, and a
    campaign whose fetch fails is still skipped into `errors` alone
    without affecting any other. The default of 1 is exactly the old
    sequential behavior.
    """
    rows: List[Dict] = []
    deleted_rows: List[Dict] = []
    errors: List[Tuple[str, str]] = []

    # Pass 1 — classify every campaign, in order, without touching any
    # Sheet. Only "active" entries need slow Sheet data.
    entries: List[Tuple] = []
    for name in campaign_names:
        try:
            campaign_cfg = get_campaign_cfg(name)
        except Exception as exc:  # noqa: BLE001
            entries.append(("error", name, str(exc)))
            continue
        raw_status = campaign_cfg.get("status") or "active"
        if raw_status == "deleted":
            entries.append(("deleted", name, campaign_cfg))
        elif raw_status == "draft":
            entries.append(("draft", name, campaign_cfg))
        else:
            entries.append(("active", name, campaign_cfg))

    # Pass 2 — fetch every active campaign's Sheet data together.
    active_entries = [e for e in entries if e[0] == "active"]
    fetched = run_in_parallel([(lambda cfg=e[2]: fetch_sheet_data(cfg)) for e in active_entries], max_workers)
    fetch_result_by_name = {e[1]: result for e, result in zip(active_entries, fetched)}

    # Pass 3 — assemble in the original order.
    for kind, name, payload in entries:
        if kind == "error":
            errors.append((name, payload))
        elif kind == "deleted":
            deleted_rows.append(build_deleted_campaign_row(payload))
        elif kind == "draft":
            rows.append(build_draft_campaign_row(payload))
        else:
            ok, value = fetch_result_by_name[name]
            if not ok:
                errors.append((name, str(value)))
                continue
            leads, responses, send_log = value
            rows.append(build_campaign_hub_row(payload, leads, responses, send_log))
    return rows, deleted_rows, errors


def filter_campaigns_by_search(rows: List[Dict], query: str) -> List[Dict]:
    if not query or not query.strip():
        return rows
    q = query.strip().lower()
    return [r for r in rows if q in r["name"].lower()]


# ---------------------------------------------------------------- the list's summary, filters and order

STATUS_FILTERS = [("all", "All"), (STATUS_RUNNING, "Running"), (STATUS_ATTENTION, "Attention"), (STATUS_PAUSED, "Paused"), (STATUS_DRAFT, "Draft"), (STATUS_COMPLETED, "Completed")]
SORT_OPTIONS = ["Active first", "Last activity", "Name", "Most replies"]
_ACTIVE_ORDER = [STATUS_RUNNING, STATUS_ATTENTION, STATUS_PAUSED, STATUS_DRAFT, STATUS_COMPLETED]


def reply_rate_text(replies: int, contacted: int) -> str:
    """The reply rate, one decimal ('58.7%'): leads who replied out of leads who were contacted (sent at least one email). This is the SAME measure each campaign uses for its own
    rate, so one campaign's total always equals its own figure. (It was once replies divided by EMAILS sent, a different measure that gave a misleadingly low 14.4%.) '—' when
    nobody has been contacted."""
    return f"{replies / contacted * 100:.1f}%" if contacted else "—"


def summarize_campaigns(rows: List[Dict]) -> Dict:
    """The totals above the list, added up from the rows (archived campaigns are never in `rows`)."""
    sent = sum(int(r.get("sent") or 0) for r in rows)
    replies = sum(int(r.get("replies") or 0) for r in rows)
    contacted = sum(int(r.get("contacted") or 0) for r in rows)
    return {"count": len(rows), "running": sum(1 for r in rows if r.get("status") == STATUS_RUNNING), "attention": sum(1 for r in rows if r.get("status") == STATUS_ATTENTION),
            "leads": sum(int(r.get("total_leads") or 0) for r in rows), "contacted": contacted, "sent": sent, "replies": replies, "reply_rate": reply_rate_text(replies, contacted)}


def hub_subtitle(summary: Dict) -> str:
    """'5 campaigns · 1 running · 1 needs attention'; the parts that are zero are left out."""
    count = summary["count"]
    parts = [f"{count} campaign{'' if count == 1 else 's'}"]
    if summary["running"]:
        parts.append(f"{summary['running']} running")
    if summary["attention"]:
        parts.append(f"{summary['attention']} needs attention" if summary["attention"] == 1 else f"{summary['attention']} need attention")
    return " · ".join(parts)


def status_filter_options(rows: List[Dict]) -> List[Tuple[str, str]]:
    """[(filter key, 'Running 1'), ...] for every filter, in a fixed order, the first being All with the total."""
    options = []
    for key, label in STATUS_FILTERS:
        count = len(rows) if key == "all" else sum(1 for r in rows if r.get("status") == key)
        options.append((key, f"{label} {count}"))
    return options


FORMAT_FILTERS = [("all", "All formats"), ("text", "Text"), ("html", "HTML")]


def format_filter_options(rows: List[Dict]) -> List[Tuple[str, str]]:
    """[(filter key, 'HTML 1'), ...] — but ONLY once at least one campaign is an HTML campaign (until then there is nothing to tell apart, so no filter is shown)."""
    if not any(r.get("format") == "html" for r in rows):
        return []
    options = []
    for key, label in FORMAT_FILTERS:
        count = len(rows) if key == "all" else sum(1 for r in rows if (r.get("format") == "html") == (key == "html"))
        options.append((key, f"{label} {count}"))
    return options


def filter_campaigns_by_format(rows: List[Dict], fmt: str) -> List[Dict]:
    """'html' keeps the HTML campaigns, 'text' keeps every other one (a campaign with no format is plain text), anything else keeps all."""
    if fmt == "html":
        return [r for r in rows if r.get("format") == "html"]
    if fmt == "text":
        return [r for r in rows if r.get("format") != "html"]
    return list(rows)


def filter_campaigns_by_status(rows: List[Dict], status: str) -> List[Dict]:
    return list(rows) if status in ("", "all", None) else [r for r in rows if r.get("status") == status]


def sort_campaigns(rows: List[Dict], option: str) -> List[Dict]:
    """'Active first' (running, attention, paused, draft, completed; the most recent activity first within each), 'Last activity', 'Name' or 'Most replies'. Ties keep the
    order they came in."""
    rows = list(rows)
    if option == "Name":
        return sorted(rows, key=lambda r: r["name"].lower())
    if option == "Most replies":
        return sorted(rows, key=lambda r: -int(r.get("replies") or 0))
    by_recent = sorted(rows, key=lambda r: r.get("last_activity") or "", reverse=True)
    if option == "Last activity":
        return by_recent
    return sorted(by_recent, key=lambda r: _ACTIVE_ORDER.index(r.get("status")) if r.get("status") in _ACTIVE_ORDER else len(_ACTIVE_ORDER))


def name_feedback(name: str, existing_campaigns: List[str]) -> Tuple[str, str]:
    """What to tell someone typing a campaign name, as they type: ('hint', ...) while the box is empty, ('error', the reason) while it cannot be used, ('ok', ...) when it can.
    The same rules as validate_campaign_name, which still has the last word when the button is pressed."""
    if not name or not name.strip():
        return "hint", "Letters, numbers and underscores only. This becomes the campaign's folder name."
    error = validate_campaign_name(name, existing_campaigns)
    return ("error", error) if error else ("ok", "Looks good — this name is free.")
