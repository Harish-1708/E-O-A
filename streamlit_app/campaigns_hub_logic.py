"""Pure logic for the Campaigns Hub page (Phase A). Combines
campaign_status_logic's status computation with the same
outreach.compute_all_campaigns_row math the Overview page already uses.
"""
import os
import sys
from typing import Callable, Dict, List, Tuple

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import outreach  # noqa: E402
from campaign_status_logic import compute_campaign_status, status_label  # noqa: E402
from parallel_logic import run_in_parallel  # noqa: E402


def compute_last_activity_timestamp(send_log: List[Dict]) -> str:
    """Most recent SendLog Timestamp string, or "" if the campaign has
    never sent anything. Returns the raw timestamp, not a relative
    string ("2 min ago") — that formatting depends on "now", which stays
    out of pure logic; the page computes it for display."""
    timestamps = [r.get("Timestamp", "") for r in send_log if r.get("Timestamp")]
    return max(timestamps) if timestamps else ""


def build_draft_campaign_row(campaign_cfg: Dict) -> Dict:
    """A brand new Draft campaign may not have any Sheet tabs yet (they're
    created on first real activity) — its status is fully knowable from
    config alone, so this never needs a Sheet fetch at all."""
    return {
        "name": campaign_cfg["_campaign_name"], "status": "draft",
        "status_label": status_label("draft"), "problems": [],
        "total_leads": 0, "sent": 0, "replies": 0, "reply_rate": "—",
        "last_activity": "",
    }


def build_deleted_campaign_row(campaign_cfg: Dict) -> Dict:
    """Same lightweight shape as a Draft row (no Sheet fetch needed) — a
    temporarily-removed campaign's history stays in the Sheet untouched,
    but the Deleted Campaigns list itself only needs to show what it is
    and let you Restore it, not a full analytics recompute."""
    return {
        "name": campaign_cfg["_campaign_name"], "status": "deleted",
        "status_label": status_label("deleted"), "problems": [],
        "total_leads": 0, "sent": 0, "replies": 0, "reply_rate": "—",
        "last_activity": "",
    }


def build_campaign_hub_row(campaign_cfg: Dict, leads: List[Dict], responses: List[Dict],
                            send_log: List[Dict]) -> Dict:
    stages = campaign_cfg["stages"]
    base_row = outreach.compute_all_campaigns_row(campaign_cfg["_campaign_name"], leads, responses,
                                                    send_log, stages)
    status, problems = compute_campaign_status(campaign_cfg, leads)
    return {
        "name": campaign_cfg["_campaign_name"], "status": status,
        "status_label": status_label(status), "problems": problems,
        "total_leads": int(base_row[1]), "sent": int(base_row[3]),
        "replies": int(base_row[7]), "reply_rate": base_row[8],
        "last_activity": compute_last_activity_timestamp(send_log),
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
