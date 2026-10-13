"""What the campaign page's header says under the title: a schedule chip, a sender chip and a "sent today" line.

PURE and READ-ONLY: everything is worked out from the campaign's own settings and the data the page has already loaded.
Nothing here decides anything about sending; it only DESCRIBES what the settings and the logs already say, and it says
"sends anytime" for a campaign with no schedule rather than inventing hours.
"""
from datetime import datetime, timezone
from typing import Dict, List, Optional

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


def subline(campaign_cfg: Dict, send_log: List[Dict], now: Optional[datetime] = None) -> str:
    """'41 sent today · daily limit 100' (the limit only when there is one)."""
    sent = sent_today(send_log, (campaign_cfg.get("schedule") or {}).get("timezone"), now)
    limit = daily_limit(campaign_cfg)
    return f"{sent} sent today" + (f" · daily limit {limit}" if limit else "")
