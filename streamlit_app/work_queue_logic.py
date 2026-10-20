"""Pure logic behind the Today page: for each running campaign, who is due
to be emailed, who is waiting and until when, who is out of play and why,
and whether sending is open right now.

READ-ONLY, and deliberately built on the engine's own functions rather
than a second copy of its rules: "due" is exactly
outreach.get_eligible_leads, "waiting" is the same call with the wait
ignored, the sending window is outreach.is_within_sending_window, and the
daily count is outreach._count_sent_today. A page that re-implemented
those would, sooner or later, disagree with what Auto Send actually does
— which is the one thing this page must never do. Nothing here writes
anywhere or sends anything.
"""
import os
import sys
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import outreach  # noqa: E402
from parallel_logic import run_in_parallel  # noqa: E402

# The team's own timezone, shown next to the campaign's (a New York
# campaign's 9:00 AM is 6:30 PM here). Display only — it never affects
# what is due.
DISPLAY_TIMEZONE = "Asia/Kolkata"

# The most due leads listed per stage on the page — enough to act on,
# small enough that a huge campaign cannot make the page slow.
MAX_LEADS_LISTED = 200

# Why a lead is out of play, in the order the checks are applied. A lead
# gets exactly ONE label (the first that matches), so the counts add up.
LABEL_NO_EMAIL = "No email address"
LABEL_REPLIED = "Replied"
LABEL_BOUNCED = "Bounced"
LABEL_DECLINED = "Declined"
LABEL_UNSUBSCRIBED = "Unsubscribed"
LABEL_STOPPED_MANUALLY = "Stopped by hand"
LABEL_PAUSED = "Paused"
LABEL_REMOVED = "Removed"
LABEL_COMPLETED = "Completed the sequence"

_STATUS_LABELS = {
    outreach.STATUS_STOPPED_REPLIED: LABEL_REPLIED,
    outreach.STATUS_STOPPED_BOUNCED: LABEL_BOUNCED,
    outreach.STATUS_STOPPED_REJECTED: LABEL_DECLINED,
    outreach.STATUS_STOPPED_UNSUBSCRIBED: LABEL_UNSUBSCRIBED,
    outreach.STATUS_STOPPED_MANUAL: LABEL_STOPPED_MANUALLY,
    outreach.STATUS_PAUSED: LABEL_PAUSED,
    outreach.STATUS_REMOVED: LABEL_REMOVED,
    outreach.STATUS_COMPLETED: LABEL_COMPLETED,
}
NOT_IN_PLAY_LABELS = [LABEL_NO_EMAIL, LABEL_REPLIED, LABEL_BOUNCED, LABEL_DECLINED, LABEL_UNSUBSCRIBED, LABEL_STOPPED_MANUALLY,
                      LABEL_PAUSED, LABEL_REMOVED, LABEL_COMPLETED]


# ---------------------------------------------------------------- time display

def format_moment(moment: datetime, tz_name: Optional[str]) -> str:
    """'Mon 12 Oct, 9:00 AM EDT' — `moment` (aware) shown in tz_name, or
    UTC if the name is missing/unknown."""
    try:
        tz = ZoneInfo(tz_name) if tz_name else timezone.utc
    except (ZoneInfoNotFoundError, ValueError):
        tz = timezone.utc
    local = moment.astimezone(tz)
    hour = local.hour % 12 or 12
    return f"{local:%a %d %b}, {hour}:{local:%M} {'AM' if local.hour < 12 else 'PM'} {local.tzname()}"


def format_date(day) -> str:
    return f"{day:%a %d %b}"


def describe_moment(moment: datetime, campaign_tz: Optional[str], display_tz: str = DISPLAY_TIMEZONE) -> str:
    """The same instant in the campaign's timezone and in the team's."""
    in_campaign = format_moment(moment, campaign_tz)
    in_display = format_moment(moment, display_tz)
    campaign_date, _campaign_time = in_campaign.split(", ", 1)
    display_date, display_time = in_display.split(", ", 1)
    if in_campaign.split(" ")[-1] == in_display.split(" ")[-1]:
        return in_campaign                       # same timezone: nothing to add
    # Show the date again only when it differs (9 PM Friday in New York is
    # already Saturday morning in India) — a bare time there would mislead.
    return f"{in_campaign} · {display_time}" if campaign_date == display_date else f"{in_campaign} · {in_display}"


# ---------------------------------------------------------------- sending window

def next_window_open(schedule: Optional[Dict], now_utc: datetime) -> Optional[datetime]:
    """When sending next opens (aware UTC), or None if it is open right now,
    there is no window to wait for, or it cannot be worked out.

    Looks up to a week ahead through the campaign's send days at its
    window_start in its own timezone (so DST is handled by the timezone
    database, never by a fixed offset). A window that crosses midnight is
    treated as opening at its start time."""
    if not schedule or not schedule.get("timezone"):
        return None
    try:
        tz = ZoneInfo(schedule["timezone"])
        is_open, _ = outreach.is_within_sending_window(schedule, now_utc=now_utc)
        if is_open:
            return None
        start_text = schedule.get("window_start")
        start_t = datetime.strptime(start_text, "%H:%M").time() if start_text else dtime(0, 0)
    except (ZoneInfoNotFoundError, ValueError, outreach.ConfigError):
        return None

    send_days = [d.strip().lower()[:3] for d in (schedule.get("send_days") or [])]
    local_now = now_utc.astimezone(tz)
    for offset in range(0, 9):
        day = (local_now + timedelta(days=offset)).date()
        if send_days and outreach.VALID_SEND_DAYS[day.weekday()] not in send_days:
            continue
        opens = datetime.combine(day, start_t, tzinfo=tz)
        if opens > local_now:
            return opens.astimezone(timezone.utc)
    return None


# ---------------------------------------------------------------- one lead

def not_in_play_label(lead: Dict, stages: List[Dict]) -> Optional[str]:
    """Why this lead will not be emailed again — or None if it is still
    in the sequence. One label per lead, first match wins, so a campaign's
    counts always add up to its number of leads."""
    if not (lead.get("Email") or "").strip():
        return LABEL_NO_EMAIL
    status = lead.get("Status", "") or ""
    if status in _STATUS_LABELS:
        return _STATUS_LABELS[status]
    if (lead.get("ReplyStatus", "") or "") == "Replied":
        return LABEL_REPLIED
    if stages and (lead.get(outreach.stage_field_names(len(stages) - 1)["sent_at"], "") or "").strip():
        return LABEL_COMPLETED
    return None


def _lead_name(lead: Dict) -> str:
    return f"{lead.get('FirstName', '') or ''} {lead.get('LastName', '') or ''}".strip() or "(no name)"


# ---------------------------------------------------------------- one campaign

def _due_date_for(lead: Dict, stages: List[Dict], stage_index: int, tz_name: Optional[str]):
    """The calendar date this lead's `stage_index` follow-up falls due
    (campaign timezone) — the same arithmetic as get_eligible_leads."""
    prev_raw = lead.get(outreach.stage_field_names(stage_index - 1)["sent_at"], "")
    prev_dt = outreach._parse_dt(prev_raw)
    if prev_dt is None:
        return None
    wait_days = stages[stage_index].get("wait_days_after_previous", 0)
    return outreach._local_calendar_date(prev_dt, tz_name) + timedelta(days=wait_days)


def build_campaign_queue(campaign_cfg: Dict, leads: List[Dict], now_utc: Optional[datetime] = None,
                         display_tz: str = DISPLAY_TIMEZONE) -> Dict:
    """Everything the Today page shows for ONE running campaign."""
    now_utc = now_utc or datetime.now(timezone.utc)
    stages = campaign_cfg.get("stages") or []
    schedule = campaign_cfg.get("schedule") or {}
    tz_name = outreach._campaign_timezone_name(campaign_cfg)

    # ---- sending window
    window: Dict = {"open": None, "reason": "", "next_open": ""}
    try:
        is_open, reason = outreach.is_within_sending_window(schedule, now_utc=now_utc)
        window["open"], window["reason"] = is_open, reason
        if not is_open:
            opens = next_window_open(schedule, now_utc)
            window["next_open"] = describe_moment(opens, tz_name, display_tz) if opens else ""
    except Exception as exc:  # noqa: BLE001 - a bad schedule must not hide the rest of the page
        window["reason"] = f"The sending window could not be read: {exc}"

    # ---- per stage: due now / waiting
    stage_rows: List[Dict] = []
    due_total = 0
    for index, stage in enumerate(stages):
        due = outreach.get_eligible_leads(leads, stages, index, timezone_name=tz_name)
        would_be = outreach.get_eligible_leads(leads, stages, index, ignore_wait_days=True, timezone_name=tz_name)
        # People, not rows: the engine de-duplicates by email address, so
        # an address that is due is not ALSO counted as waiting because a
        # second row for it is still within its wait.
        due_emails = {(lead.get("Email") or "").strip().lower() for lead in due}
        waiting = [lead for lead in would_be if (lead.get("Email") or "").strip().lower() not in due_emails]
        due_total += len(due)

        waiting_dates = [d for d in (_due_date_for(lead, stages, index, tz_name) for lead in waiting) if d]
        next_due = min(waiting_dates) if waiting_dates else None
        listed = []
        for lead in due[:MAX_LEADS_LISTED]:
            listed.append({
                "Name": _lead_name(lead),
                "Email": (lead.get("Email") or "").strip(),
                "Previous stage sent (UTC)": (lead.get(outreach.stage_field_names(index - 1)["sent_at"], "") if index else "") or "",
            })
        stage_rows.append({
            "stage": stage["name"],
            "due_now": len(due),
            "waiting": len(waiting),
            "next_due": next_due,
            "next_due_text": format_date(next_due) if next_due else "",
            "due_leads": listed,
            "due_leads_hidden": max(len(due) - MAX_LEADS_LISTED, 0),
        })

    # ---- who is out of play, and why
    out_of_play = {label: 0 for label in NOT_IN_PLAY_LABELS}
    in_sequence = 0
    for lead in leads:
        label = not_in_play_label(lead, stages)
        if label is None:
            in_sequence += 1
        else:
            out_of_play[label] += 1

    # ---- today's budget
    sending = campaign_cfg.get("sending") or {}
    daily_limit = sending.get("daily_limit")
    sent_today = outreach._count_sent_today(leads, stages)
    remaining = max(int(daily_limit) - sent_today, 0) if daily_limit is not None else None

    return {
        "campaign": campaign_cfg.get("_campaign_name", ""),
        "timezone": tz_name or "",
        "window": window,
        "stages": stage_rows,
        "due_total": due_total,
        "waiting_total": sum(row["waiting"] for row in stage_rows),
        "next_due_text": _earliest_text(stage_rows),
        "out_of_play": out_of_play,
        "in_sequence": in_sequence,
        "total_leads": len(leads),
        "daily_limit": daily_limit,
        "sent_today": sent_today,
        "remaining_today": remaining,
    }


def _earliest_text(stage_rows: List[Dict]) -> str:
    dates = [row["next_due"] for row in stage_rows if row["next_due"]]
    return format_date(min(dates)) if dates else ""


def summarize_queue(queue: Dict) -> str:
    """One plain sentence on what the next Auto Send run will do for this
    campaign — the thing the page exists to answer."""
    due, waiting, window = queue["due_total"], queue["waiting_total"], queue["window"]
    if due == 0:
        if waiting == 0:
            return "Nothing is due, and nobody is waiting on a follow-up."
        when = f" The next lead falls due on {queue['next_due_text']}." if queue["next_due_text"] else ""
        return f"Nothing is due right now.{when}"
    leads = f"{due} lead{'s are' if due != 1 else ' is'} due"
    if window["open"] is False:
        when = f" when sending next opens: {window['next_open']}" if window["next_open"] else " when sending next opens"
        return f"{leads}. They will go out{when}."
    if window["open"] is None:
        return f"{leads}, but the sending window could not be read — see the message above."
    remaining = queue["remaining_today"]
    if remaining is not None and remaining <= 0:
        return f"{leads}, but today's daily limit is already used up — they go out on the next sending day."
    cap = f" (up to {remaining} more today)" if remaining is not None else ""
    return f"{leads} and sending is open — the next Auto Send run will email them{cap}."


# ---------------------------------------------------------------- every campaign

def build_all_queues(campaign_names: List[str],
                     get_campaign_cfg: Callable[[str], Dict],
                     get_leads: Callable[[Dict], List[Dict]],
                     max_workers: int = 1,
                     now_utc: Optional[datetime] = None) -> Tuple[List[Dict], List[Tuple[str, str]], List[Tuple[str, str]]]:
    """Returns (queues, not_running, errors).

    Only campaigns that are in progress get a queue, and a campaign that
    is not (paused, draft, deleted) is never read from Google at all — the
    same rule Auto Send follows. `errors` is [(campaign, message)] for a
    campaign whose config or leads could not be read; it never blocks the
    others. Queues come out in campaign_names order."""
    names = list(campaign_names)
    configs: Dict[str, Dict] = {}
    not_running: List[Tuple[str, str]] = []
    errors: List[Tuple[str, str]] = []
    running: List[str] = []

    for name in names:
        try:
            cfg = get_campaign_cfg(name)
        except Exception as exc:  # noqa: BLE001
            errors.append((name, str(exc)))
            continue
        status = cfg.get("status") or "active"
        if status != "active":
            not_running.append((name, status))
            continue
        configs[name] = cfg
        running.append(name)

    results = run_in_parallel([(lambda n=name: get_leads(configs[n])) for name in running], max_workers)
    queues: List[Dict] = []
    for name, (ok, value) in zip(running, results):
        if not ok:
            errors.append((name, str(value)))
            continue
        queue = build_campaign_queue(configs[name], value, now_utc=now_utc)
        queue["campaign"] = name
        queues.append(queue)
    return queues, not_running, errors
