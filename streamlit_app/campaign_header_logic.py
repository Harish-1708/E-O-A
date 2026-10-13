"""What the campaign page's header says under the title: a schedule chip, a sender chip and a "sent today" line.

PURE and READ-ONLY: everything is worked out from the campaign's own settings and the data the page has already loaded.
Nothing here decides anything about sending; it only DESCRIBES what the settings and the logs already say, and it says
"sends anytime" for a campaign with no schedule rather than inventing hours.
"""
import re
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple

try:                                         # Python 3.9+; the app's own schedule code uses it too
    from zoneinfo import ZoneInfo
except ImportError:                          # pragma: no cover
    ZoneInfo = None

_DAY_ORDER = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_DAY_NAMES = {"mon": "Mon", "tue": "Tue", "wed": "Wed", "thu": "Thu", "fri": "Fri", "sat": "Sat", "sun": "Sun"}


def days_summary(send_days: Optional[List[str]]) -> str:
    """['mon','tue','wed','thu','fri'] -> 'Mon–Fri'; all seven -> 'Every day'; ['fri'] -> 'Fri'; ['mon','wed','fri'] -> 'Mon, Wed, Fri'.
    Runs of three or more consecutive days are shortened; unknown codes are ignored; none at all -> 'No days'."""
    chosen = sorted({str(d).strip().lower()[:3] for d in (send_days or [])} & set(_DAY_ORDER), key=_DAY_ORDER.index)
    if not chosen:
        return "No days"
    if len(chosen) == 7:
        return "Every day"
    indexes = [_DAY_ORDER.index(d) for d in chosen]
    parts, run = [], [indexes[0]]
    for i in indexes[1:]:
        if i == run[-1] + 1:
            run.append(i)
        else:
            parts.append(run)
            run = [i]
    parts.append(run)
    return ", ".join(f"{_DAY_NAMES[_DAY_ORDER[r[0]]]}–{_DAY_NAMES[_DAY_ORDER[r[-1]]]}" if len(r) >= 3 else ", ".join(_DAY_NAMES[_DAY_ORDER[i]] for i in r) for r in parts)


def city_of(timezone_name: Optional[str]) -> str:
    """'America/Chicago' -> 'Chicago'; 'America/Los_Angeles' -> 'Los Angeles'; blank -> ''."""
    text = str(timezone_name or "").strip()
    return text.split("/")[-1].replace("_", " ") if text else ""


def schedule_summary(campaign_cfg: Dict) -> str:
    """'Sends anytime' when the campaign has no schedule, else e.g. 'Mon–Fri · 09:00–17:00 · Chicago'."""
    schedule = campaign_cfg.get("schedule") or {}
    if not schedule:
        return "Sends anytime"
    parts = [days_summary(schedule.get("send_days") or ["mon", "tue", "wed", "thu", "fri"])]
    start, end = str(schedule.get("window_start") or "").strip(), str(schedule.get("window_end") or "").strip()
    if start and end:
        parts.append(f"{start}–{end}")
    city = city_of(schedule.get("timezone"))
    if city:
        parts.append(city)
    return " · ".join(parts)


def sender_summary(leads: List[Dict]) -> str:
    """How many different sender accounts the campaign's leads actually use: '1 sender account', '3 sender accounts', 'No sender yet'."""
    accounts = {str(lead.get("SenderAccount") or "").strip() for lead in leads} - {""}
    if not accounts:
        return "No sender yet"
    return f"{len(accounts)} sender account{'s' if len(accounts) != 1 else ''}"


def daily_limit(campaign_cfg: Dict) -> Optional[int]:
    """The campaign's daily sending limit, or None when it has none or it is not a positive whole number."""
    value = (campaign_cfg.get("sending") or {}).get("daily_limit")
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _local_date(stamp, zone):
    text = str(stamp or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            moment = datetime.strptime(text[:19], fmt).replace(tzinfo=timezone.utc)      # the Sheet's own clock is UTC
            return moment.astimezone(zone).date() if zone else moment.date()
        except ValueError:
            continue
    return None


def sent_today(send_log: List[Dict], timezone_name: Optional[str] = None, now: Optional[datetime] = None) -> int:
    """How many emails went out today (the campaign's own day when it has a time zone, otherwise the UTC day). Counts only
    rows whose Status is 'sent'; a row with an unreadable time is not counted."""
    zone = None
    if timezone_name and ZoneInfo is not None:
        try:
            zone = ZoneInfo(timezone_name)
        except Exception:  # noqa: BLE001 - an unknown zone name falls back to UTC rather than breaking the header
            zone = None
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)                                          # a time without a zone is the Sheet's own (UTC)
    today = (now.astimezone(zone) if zone else now.astimezone(timezone.utc)).date()
    return sum(1 for row in send_log if str(row.get("Status") or "").strip().lower() == "sent" and _local_date(row.get("Timestamp"), zone) == today)


def subline(campaign_cfg: Dict, send_log: List[Dict], now: Optional[datetime] = None, next_send: Optional[str] = None) -> str:
    """'Next automatic send: Fri 9:43 AM (in 2 d 19 h) · 41 sent today · daily limit 100' (the first part only when given, the
    limit only when there is one)."""
    sent = sent_today(send_log, (campaign_cfg.get("schedule") or {}).get("timezone"), now)
    limit = daily_limit(campaign_cfg)
    parts = [next_send] if next_send else []
    parts.append(f"{sent} sent today" + (f" · daily limit {limit}" if limit else ""))
    return " · ".join(parts)


# ---------------------------------------------------------------- the next automatic send

def read_cron_minute(workflow_text: str) -> Optional[int]:
    """The minute past each hour at which the auto-send workflow is scheduled, read from its own file ('43 * * * *' -> 43), or None
    when the file does not have a plain hourly schedule (then the page simply shows no 'next automatic send')."""
    match = re.search(r"cron:\s*[\"']\s*(\d{1,2})\s+\*\s+\*\s+\*\s+\*\s*[\"']", str(workflow_text or ""))
    if not match or int(match.group(1)) > 59:
        return None
    return int(match.group(1))


def next_automatic_send(schedule: Dict, now_utc: datetime, minute: int, within_window: Callable[[Dict, datetime], Tuple[bool, str]],
                        horizon_hours: int = 24 * 8) -> Optional[datetime]:
    """The first hourly run (at `minute` past each UTC hour, strictly after now) at which the campaign's sending window is open,
    judged by the ENGINE's own rule (`within_window`, outreach.is_within_sending_window), so this can never disagree with what the
    sender will really do. None when no run within `horizon_hours` qualifies (for example a schedule with no send days)."""
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    now_utc = now_utc.astimezone(timezone.utc)
    candidate = now_utc.replace(minute=minute, second=0, microsecond=0)
    if candidate <= now_utc:
        candidate += timedelta(hours=1)
    for _ in range(horizon_hours):
        try:
            allowed, _reason = within_window(schedule, candidate)
        except Exception:  # noqa: BLE001 - an invalid schedule must hide the line, never break the page
            return None
        if allowed:
            return candidate
        candidate += timedelta(hours=1)
    return None


def humanize_wait(seconds: float) -> str:
    """'55 min', '3 h', '2 d 19 h' — rounded down to minutes, then hours, then days and hours."""
    minutes = max(0, int(seconds // 60))
    if minutes < 60:
        return f"{max(minutes, 1)} min"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} h"
    days, rest = divmod(hours, 24)
    return f"{days} d" + (f" {rest} h" if rest else "")


def _clock(moment: datetime) -> str:
    return f"{moment.hour % 12 or 12}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'}"


def describe_moment(when_utc: datetime, now_utc: datetime, timezone_name: Optional[str] = None) -> str:
    """'today 2:43 PM', 'tomorrow 9:43 AM', 'Fri 9:43 AM' (within a week), else 'Oct 20 9:43 AM' — in the campaign's own time zone when it
    has one, otherwise in UTC."""
    zone = None
    if timezone_name and ZoneInfo is not None:
        try:
            zone = ZoneInfo(timezone_name)
        except Exception:  # noqa: BLE001
            zone = None
    zone = zone or timezone.utc
    local, local_now = when_utc.astimezone(zone), now_utc.astimezone(zone)
    gap = (local.date() - local_now.date()).days
    if gap == 0:
        day = "today"
    elif gap == 1:
        day = "tomorrow"
    elif 1 < gap < 7:
        day = _DAY_NAMES[_DAY_ORDER[local.weekday()]]
    else:
        day = f"{local.strftime('%b')} {local.day}"
    return f"{day} {_clock(local)}"


def next_send_line(campaign_cfg: Dict, now_utc: datetime, minute: Optional[int], within_window: Callable[[Dict, datetime], Tuple[bool, str]]) -> Optional[str]:
    """'Next automatic send: Fri 9:43 AM (in 2 d 19 h)', or None when the schedule of runs is unknown or nothing is due in the next 8 days."""
    if minute is None:
        return None
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    schedule = campaign_cfg.get("schedule") or {}
    when = next_automatic_send(schedule, now_utc, minute, within_window)
    if when is None:
        return None
    return f"Next automatic send: {describe_moment(when, now_utc, schedule.get('timezone'))} (in {humanize_wait((when - now_utc).total_seconds())})"
