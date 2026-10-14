"""What the Schedule tab shows around its form: the hour-by-hour window strip, today's progress, how many leads are due, the week's sends, the next
automatic run, and the follow-up waits.

PURE and READ-ONLY: nothing here saves or sends anything. 'Due' is never worked out by a rule of its own: it asks the ENGINE (outreach.get_eligible_leads),
so it can never disagree with what the sender will really do; 'tomorrow' asks the same engine function after moving each lead's earlier sends back by a day.
"""
import re
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import outreach
from campaign_header_logic import ZoneInfo, days_summary

_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_DAY_LABELS = {"mon": "Mon", "tue": "Tue", "wed": "Wed", "thu": "Thu", "fri": "Fri", "sat": "Sat", "sun": "Sun"}


# ---------------------------------------------------------------- times

def parse_hhmm(text) -> Optional[int]:
    """'09:30' -> 570 (minutes after midnight); anything that is not a real 24-hour HH:MM -> None."""
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(text or ""))
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    return hour * 60 + minute if hour < 24 and minute < 60 else None


def clock12(hhmm) -> str:
    """'17:00' -> '5:00 PM', '00:15' -> '12:15 AM'; text that is not a time is returned as it was."""
    minutes = parse_hhmm(hhmm)
    if minutes is None:
        return str(hhmm or "")
    hour, minute = divmod(minutes, 60)
    return f"{hour % 12 or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"


def _zone(tz_name):
    if tz_name and ZoneInfo is not None:
        try:
            return ZoneInfo(tz_name)
        except Exception:  # noqa: BLE001 - an unknown name falls back to UTC rather than breaking the tab
            return None
    return None


def tz_abbr(moment: datetime, tz_name: Optional[str]) -> str:
    """'EDT', 'CDT', 'IST' … for `moment` in the named zone ('UTC' when the zone is unknown)."""
    zone = _zone(tz_name)
    return (moment.astimezone(zone) if zone else moment.astimezone(timezone.utc)).tzname() or "UTC"


def wait_words(seconds: float) -> str:
    """'55 minutes', '1 minute', '2 hours 5 minutes', '3 days 4 hours' — rounded down; never 'in 0 minutes'."""
    minutes = max(1, int(seconds // 60))
    if minutes < 60:
        return f"{minutes} minute" + ("" if minutes == 1 else "s")
    hours, rest = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} hour" + ("" if hours == 1 else "s") + (f" {rest} minute" + ("" if rest == 1 else "s") if rest and hours < 6 else "")
    days, left = divmod(hours, 24)
    return f"{days} day" + ("" if days == 1 else "s") + (f" {left} hour" + ("" if left == 1 else "s") if left else "")


def _day_word(when: datetime, now: datetime, zone) -> str:
    local, local_now = (when.astimezone(zone) if zone else when.astimezone(timezone.utc)), (now.astimezone(zone) if zone else now.astimezone(timezone.utc))
    gap = (local.date() - local_now.date()).days
    if gap == 0:
        return "Today"
    if gap == 1:
        return "Tomorrow"
    if 1 < gap < 7:
        return _DAY_LABELS[_DAYS[local.weekday()]]
    return f"{local.strftime('%b')} {local.day}"


def next_run_card(next_utc: Optional[datetime], now_utc: datetime, tz_name: Optional[str], viewer_tz: Optional[str] = None) -> Optional[Dict]:
    """The 'Next automatic run' card: {headline: 'Today 2:15 PM EDT', relative: 'in 55 minutes', viewer: '11:45 PM IST' or ''}. None when there is no next run.
    `viewer` is the same moment in the visitor's own time zone, and is left out when that is the campaign's zone or an unknown one."""
    if next_utc is None:
        return None
    zone = _zone(tz_name)
    local = next_utc.astimezone(zone) if zone else next_utc.astimezone(timezone.utc)
    clock = f"{local.hour % 12 or 12}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'}"
    card = {"headline": f"{_day_word(next_utc, now_utc, zone)} {clock} {tz_abbr(next_utc, tz_name)}", "relative": f"in {wait_words((next_utc - now_utc).total_seconds())}", "viewer": ""}
    viewer_zone = _zone(viewer_tz)
    if viewer_zone is not None and viewer_tz != tz_name and next_utc.astimezone(viewer_zone).utcoffset() != local.utcoffset():
        mine = next_utc.astimezone(viewer_zone)
        mine_clock = f"{mine.hour % 12 or 12}:{mine.minute:02d} {'AM' if mine.hour < 12 else 'PM'} {mine.tzname()}"
        card["viewer"] = mine_clock if mine.date() == local.date() else f"{_DAY_LABELS[_DAYS[mine.weekday()]]} {mine_clock}"
    return card


# ---------------------------------------------------------------- the window across the day

def hour_strip(window_start, window_end, first: int = 6, last: int = 21) -> List[Dict]:
    """One block per hour from `first` to `last` (widened to include every hour the window touches): {hour, active}. The engine's own rules:
      * normal window ('09:00'-'17:00'): the hours it covers, 9 to 16 (the 17:00 hour is not shown as covered);
      * an OVERNIGHT window (the end is before the start, '22:00'-'06:00'): from the start to midnight and from midnight to the end — 22, 23, 0 to 5;
      * the same time twice ('09:00'-'09:00'): the engine allows exactly that one minute, so only that hour is marked.
    A time that is not a real HH:MM gives a strip with nothing marked."""
    start, end = parse_hhmm(window_start), parse_hhmm(window_end)
    if start is None or end is None:
        return [{"hour": hour, "active": False} for hour in range(first, last + 1)]

    def covered(hour: int) -> bool:
        if start < end:
            return start < (hour + 1) * 60 and end > hour * 60
        if start == end:
            return hour == start // 60
        return (hour + 1) * 60 > start or hour * 60 < end

    marked = [hour for hour in range(24) if covered(hour)]
    first, last = min([first] + marked), max([last] + marked)
    return [{"hour": hour, "active": hour in marked} for hour in range(first, last + 1)]


# ---------------------------------------------------------------- days

def weekend_line(send_days: List[str]) -> str:
    """'Weekend sends: none (Sat–Sun off)', 'Weekend sends: Sat (Sun off)', 'Weekend sends: Sat, Sun'."""
    on = {str(d).strip().lower()[:3] for d in (send_days or [])}
    weekend = [d for d in ("sat", "sun") if d in on]
    off = [d for d in ("sat", "sun") if d not in on]
    if not weekend:
        return "Weekend sends: none (" + days_summary(off) + " off)"
    if not off:
        return "Weekend sends: " + days_summary(weekend)
    return f"Weekend sends: {days_summary(weekend)} ({days_summary(off)} off)"


def window_status(schedule: Dict, now_utc: datetime, within_window=None) -> Dict:
    """Is sending open right now? {open: bool, text}. With no schedule it sends anytime; the engine's own rule (`within_window`) decides otherwise."""
    if not schedule:
        return {"open": True, "text": "No schedule: this campaign sends anytime"}
    within_window = within_window or outreach.is_within_sending_window
    try:
        allowed, _reason = within_window(schedule, now_utc)
    except Exception:  # noqa: BLE001 - an invalid schedule is reported by the engine itself; here it just means 'cannot say'
        return {"open": False, "text": "Window can't be worked out — check the time zone and times"}
    end = clock12(schedule.get("window_end"))
    return {"open": bool(allowed), "text": f"Window is open until {end}" if allowed else "Window is closed right now"}


# ---------------------------------------------------------------- how many leads are due

def due_counts(leads: List[Dict], stages: List[Dict], timezone_name: Optional[str] = None) -> Tuple[int, int]:
    """(due now, due tomorrow): leads whose next email is due today, and leads for whom it becomes due by tomorrow. Both come from the engine's own
    eligibility rule (replied, finished and duplicate leads are left out by it). 'Tomorrow' asks that rule again after moving every earlier send back one
    day, and keeps only the leads that were not already due."""
    if not leads or not stages:
        return 0, 0
    now_ids, tomorrow_copies = set(), []
    shifted = []
    for lead in leads:
        copy = dict(lead)
        for index in range(len(stages)):
            field = outreach.stage_field_names(index)["sent_at"]
            moment = outreach._parse_dt(copy.get(field)) if copy.get(field) else None
            if moment is not None:
                copy[field] = (moment - timedelta(days=1)).strftime(outreach.DATETIME_FMT)
        shifted.append((lead, copy))
    for index in range(len(stages)):
        now_ids |= {id(lead) for lead in outreach.get_eligible_leads(leads, stages, index, timezone_name=timezone_name)}
        copies = [copy for _lead, copy in shifted]
        original_of = {id(copy): lead for lead, copy in shifted}
        tomorrow_copies += [original_of[id(copy)] for copy in outreach.get_eligible_leads(copies, stages, index, timezone_name=timezone_name)]
    return len(now_ids), len({id(lead) for lead in tomorrow_copies} - now_ids)


# ---------------------------------------------------------------- today and this week

def progress_fraction(sent: int, limit: Optional[int]) -> float:
    """0 to 1: how much of today's limit is used (0 when there is no limit; never more than 1)."""
    if not limit or limit <= 0:
        return 0.0
    return max(0.0, min(1.0, sent / limit))


def week_rows(send_log: List[Dict], tz_name: Optional[str], now_utc: datetime, send_days: List[str], limit: Optional[int] = None) -> List[Dict]:
    """This week, Monday first, in the campaign's own days: {day, count, fraction}. `count` is None for a day that has not happened yet (shown as a dash).
    The bar is the day's sends against the daily limit, or against the busiest day when there is no limit. Only send days are listed, plus any other day that
    actually had sends."""
    zone = _zone(tz_name)
    today = (now_utc.astimezone(zone) if zone else now_utc.astimezone(timezone.utc)).date()
    monday = today - timedelta(days=today.weekday())
    counts: Dict[date, int] = {}
    for row in send_log:
        if str(row.get("Status") or "").strip().lower() != "sent":
            continue
        moment = outreach._parse_dt(row.get("Timestamp")) if row.get("Timestamp") else None
        if moment is None:
            continue
        local = moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment
        day = (local.astimezone(zone) if zone else local.astimezone(timezone.utc)).date()
        counts[day] = counts.get(day, 0) + 1
    on = {str(d).strip().lower()[:3] for d in (send_days or [])}
    rows = []
    for offset in range(7):
        day = monday + timedelta(days=offset)
        code = _DAYS[offset]
        if code not in on and not counts.get(day):
            continue
        rows.append({"day": _DAY_LABELS[code], "count": None if day > today else counts.get(day, 0)})
    scale = limit if limit and limit > 0 else max([r["count"] or 0 for r in rows] + [0])
    for row in rows:
        row["fraction"] = 0.0 if not scale or row["count"] is None else max(0.0, min(1.0, row["count"] / scale))
    return rows


# ---------------------------------------------------------------- follow-up waits

def sends_on_text(wait: int, after: str) -> str:
    """'3 days after follow-up 1', '1 day after the intro', 'Same day as the intro' (a wait of 0 goes out on the next run that same day)."""
    if wait == 0:
        return f"Same day as {after}"
    return f"{wait} day" + ("" if wait == 1 else "s") + f" after {after}"


def wait_edit_rows(stages: List[Dict]) -> List[Dict]:
    """One row per follow-up, in order (the intro has no wait and is not listed): {prefix, label, saved, after}. `saved` is the wait the campaign has now, a whole
    number of days from 0 to the engine's limit, or None when the settings do not give one (it is then offered as 0, which is what the engine would use).
    `after` says what the wait counts from: 'the intro', or the previous follow-up in lower case."""
    from leads_logic import humanize_stage
    rows, previous = [], None
    for index, stage in enumerate(stages or []):
        prefix = str(stage.get("template_prefix") or stage.get("name") or "")
        if index > 0:
            wait = stage.get("wait_days_after_previous")
            known = isinstance(wait, int) and not isinstance(wait, bool) and 0 <= wait <= outreach.MAX_STAGE_WAIT_DAYS
            rows.append({"prefix": prefix, "label": humanize_stage(prefix), "saved": wait if known else None,
                         "after": "the intro" if index == 1 else humanize_stage(previous).lower()})
        previous = prefix
    return rows


def describe_waits(rows: List[Dict], values: Dict[str, int]) -> List[Dict]:
    """The rows with the waits as they are being edited: {label, wait, sends_on, day}. `day` counts from the intro (day 0) through every wait before it, so a
    campaign reads as a timeline: 'Follow-up 2: day 5'."""
    described, day = [], 0
    for row in rows:
        wait = values.get(row["prefix"], row["saved"] or 0)
        day += wait
        described.append({"label": row["label"], "wait": wait, "sends_on": sends_on_text(wait, row["after"]), "day": day})
    return described


def changed_waits(rows: List[Dict], values: Dict[str, int]) -> List[Dict]:
    """The follow-ups whose edited wait differs from the saved one: {label, was, now}. `was` is None when nothing usable was saved for it, in which case the
    comparison is against 0, the wait the engine would use, so merely looking at such a row is never a change."""
    changes = []
    for row in rows:
        if row["prefix"] not in values:
            continue
        current = row["saved"] if row["saved"] is not None else 0
        if values[row["prefix"]] != current:
            changes.append({"label": row["label"], "was": row["saved"], "now": values[row["prefix"]]})
    return changes


def shortened(changes: List[Dict]) -> List[Dict]:
    """The changes that make a wait SHORTER: those can make leads due straight away."""
    return [change for change in changes if change["was"] is not None and change["now"] < change["was"]]
