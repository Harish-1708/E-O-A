"""Pure logic behind the Data tab's lead list and lead panel: what each row shows, how far through the sequence a lead is,
the timeline of what has happened to it, and how a long list is cut into pages.

READ-ONLY and PURE: everything here is worked out from the lead's own Master Sheet columns (IntroSentAt, FollowUp1SentAt …,
ReplyStatus, Status, SenderAccount …) that the Data tab has already loaded. Nothing reads a Sheet, nothing is written, and
nothing here decides anything about SENDING — it only describes what the Sheet already says.
"""
import re
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

PAGE_SIZE = 25
MAX_STAGES = 11                       # the Master Sheet has Intro plus FollowUp1 … FollowUp10

STATUS_REMOVED, STATUS_REPLIED, STATUS_BOUNCED = "Removed", "Stopped - Replied", "Stopped - Bounced"
STATUS_PAUSED, STATUS_COMPLETED = "Paused", "Completed"        # the other two the engine writes (see outreach.STATUS_*)

# a status pill's colour family (see leads_html): green, blue, red, amber, grey
KIND_GOOD, KIND_ACTIVE, KIND_BAD, KIND_WAITING, KIND_NEUTRAL = "good", "active", "bad", "waiting", "neutral"


def sent_column(index: int) -> str:
    """The Master Sheet column holding when stage `index` went out (0 = Intro, 1 = FollowUp1 …). The engine's own
    outreach.stage_field_names says the same; a test holds the two together."""
    return "IntroSentAt" if index == 0 else f"FollowUp{index}SentAt"


def humanize_stage(name: str) -> str:
    """A template-style stage name as people say it: 'intro' -> 'Intro', 'followup2' -> 'Follow-up 2', 'follow_up_3' -> 'Follow-up 3';
    anything else has its underscores turned into spaces and its first letter capitalised."""
    text = str(name or "").strip()
    follow = re.fullmatch(r"follow[\s_-]*up[\s_-]*(\d+)", text, flags=re.IGNORECASE)
    if follow:
        return f"Follow-up {int(follow.group(1))}"
    text = text.replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else ""


def stage_labels(stage_names: Optional[List[str]], count: int) -> List[str]:
    """One label per stage: the campaign's own stage name (made readable) when it has one, otherwise 'Intro', 'Follow-up 1', …"""
    names = list(stage_names or [])
    labels = []
    for i in range(count):
        given = humanize_stage(names[i]) if i < len(names) else ""
        labels.append(given or ("Intro" if i == 0 else f"Follow-up {i}"))
    return labels


def stage_count(stage_names: Optional[List[str]], leads: List[Dict]) -> int:
    """How many stages to draw: the campaign's own number when known, otherwise the highest stage any lead has actually
    been sent (at least 1, at most MAX_STAGES)."""
    if stage_names:
        return max(1, min(len(stage_names), MAX_STAGES))
    highest = 0
    for lead in leads:
        for i in range(MAX_STAGES):
            if str(lead.get(sent_column(i)) or "").strip():
                highest = max(highest, i + 1)
    return max(1, highest)


# ---------------------------------------------------------------- identity and names

def lead_keys(leads: List[Dict]) -> List[str]:
    """One stable, unique key per lead, in order: its LeadID (or, without one, its email), and if two leads would share a key
    the later ones get '#2', '#3' … so every row has its own."""
    seen: Dict[str, int] = {}
    keys = []
    for lead in leads:
        base = str(lead.get("LeadID") or "").strip() or str(lead.get("Email") or "").strip() or "lead"
        seen[base] = seen.get(base, 0) + 1
        keys.append(base if seen[base] == 1 else f"{base}#{seen[base]}")
    return keys


def display_name(lead: Dict) -> str:
    name = " ".join(p for p in (str(lead.get("FirstName") or "").strip(), str(lead.get("LastName") or "").strip()) if p)
    if name:
        return name
    email = str(lead.get("Email") or "").strip()
    return email.split("@")[0] if email else "(no name)"


# ---------------------------------------------------------------- status

_NEVER_COMPLETED_STATUSES = (STATUS_REMOVED, STATUS_PAUSED)      # (and anything that starts with "Stopped"): the lead's outcome was already decided


def sequence_completed(lead: Dict, count: int) -> bool:
    """True when the lead has been sent the LAST email of the sequence (`count` stages) and nothing else has decided its outcome. It is the rule Home's counts use, so Home,
    the Data tab and Performance always agree. A reply, a bounce, an unsubscribe, a manual stop, a pause or a removal always wins, so a lead that finished the sequence and
    LATER replies stops being 'completed' straight away: the answer is worked out from the lead's columns every time and is never stored."""
    if count <= 0 or not str(lead.get("Email") or "").strip():
        return False
    status = str(lead.get("Status") or "").strip()
    if status in _NEVER_COMPLETED_STATUSES or status.startswith("Stopped"):
        return False
    if str(lead.get("ReplyStatus") or "").strip() == "Replied":
        return False
    if status == STATUS_COMPLETED:
        return True
    return bool(str(lead.get(sent_column(count - 1)) or "").strip())


def status_summary(lead: Dict, count: Optional[int] = None) -> Tuple[str, str]:
    """(label, colour family) for the status pill — what is true of the lead right now. With `count` (how many stages the campaign has) a lead that has been sent its last
    email, and has not replied or been stopped, shows as Completed instead of 'In sequence'."""
    status = str(lead.get("Status") or "").strip()
    contacted = bool(str(lead.get("IntroSentAt") or "").strip())
    if status == STATUS_REMOVED:
        return "Removed", KIND_BAD
    if status == STATUS_REPLIED:
        return "Replied", KIND_GOOD
    if status == STATUS_BOUNCED:
        return "Bounced", KIND_BAD
    if status == STATUS_PAUSED:
        return "Paused", KIND_WAITING
    if status == STATUS_COMPLETED:
        return "Completed", KIND_NEUTRAL
    if count is not None and sequence_completed(lead, count):
        return "Completed", KIND_NEUTRAL
    if status.startswith("Stopped"):
        return status.replace("Stopped - ", "Stopped · ", 1) if status.startswith("Stopped - ") else status, KIND_NEUTRAL
    if str(lead.get("ReplyStatus") or "").strip() == "Replied":
        return "Replied", KIND_GOOD
    if contacted:
        return "In sequence", KIND_ACTIVE
    return "Not contacted", KIND_WAITING


def sequence_progress(lead: Dict, count: int) -> List[str]:
    """One entry per stage: 'sent', 'replied' (the last stage sent, when the lead replied), or 'pending'."""
    sent = [bool(str(lead.get(sent_column(i)) or "").strip()) for i in range(count)]
    states = ["sent" if done else "pending" for done in sent]
    if status_summary(lead)[0] == "Replied" and any(sent):
        states[max(i for i, done in enumerate(sent) if done)] = "replied"
    return states


# ---------------------------------------------------------------- dates

def _parse(value) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:19] if "T" in text or " " in text else text[:10], fmt)
        except ValueError:
            continue
    return None


def short_date(value, today: Optional[date] = None) -> str:
    """'Oct 12' (with the year when it is not this year); an unreadable value is shown as it is, an empty one as ''."""
    moment = _parse(value)
    if moment is None:
        return str(value or "").strip()
    today = today or date.today()
    text = f"{moment.strftime('%b')} {moment.day}"
    return text if moment.year == today.year else f"{text}, {moment.year}"


def last_sent(lead: Dict, count: int) -> str:
    """When the most recent email to this lead went out ('' if none): the latest of its stage dates."""
    stamps = [(_parse(lead.get(sent_column(i))), str(lead.get(sent_column(i)) or "").strip()) for i in range(count)]
    parsed = [p for p, raw in stamps if p is not None]
    if parsed:
        return max(parsed).strftime("%Y-%m-%d %H:%M:%S")
    return next((raw for _, raw in reversed(stamps) if raw), "")


# ---------------------------------------------------------------- one row, one panel

def lead_row(lead: Dict, count: int, today: Optional[date] = None) -> Dict:
    label, kind = status_summary(lead, count)
    return {
        "name": display_name(lead), "company": str(lead.get("Company") or "").strip(), "email": str(lead.get("Email") or "").strip(),
        "status": label, "status_kind": kind, "progress": sequence_progress(lead, count),
        "last_sent": short_date(last_sent(lead, count), today), "sender": str(lead.get("SenderAccount") or "").strip(),
    }


def timeline(lead: Dict, labels: List[str], today: Optional[date] = None) -> List[Dict]:
    """What has happened to the lead, oldest first: each stage sent, the reply, why sending stopped, and when the next
    follow-up is due. Each event is {when, icon, text}; an event whose time is unreadable goes last."""
    events: List[Tuple[Optional[datetime], str, str, str]] = []
    for i, label in enumerate(labels):
        raw = str(lead.get(sent_column(i)) or "").strip()
        if raw:
            events.append((_parse(raw), raw, "📨", f"{label} sent"))
    status = str(lead.get("Status") or "").strip()
    finished = sequence_completed(lead, len(labels))
    reply_raw = str(lead.get("ReplyAt") or lead.get("LastInboundAt") or "").strip()
    if str(lead.get("ReplyStatus") or "").strip() == "Replied" or status == STATUS_REPLIED:
        events.append((_parse(reply_raw), reply_raw, "💬", "Replied"))
    stopped_raw = str(lead.get("LastActionAt") or "").strip()
    if status == STATUS_BOUNCED:
        events.append((_parse(stopped_raw), stopped_raw, "⚠️", "Bounced — sending stopped"))
    elif status == STATUS_REMOVED:
        events.append((_parse(stopped_raw), stopped_raw, "🗑️", "Removed from the campaign"))
    elif status.startswith("Stopped") and status != STATUS_REPLIED:
        events.append((_parse(stopped_raw), stopped_raw, "⏹️", f"{status} — sending stopped"))
    elif status == STATUS_REPLIED:
        events.append((None, "", "⏹️", "Follow-ups stopped automatically"))
    elif status == STATUS_COMPLETED:
        events.append((_parse(stopped_raw), stopped_raw, "✅", "Sequence completed"))
    elif finished:
        last_raw = str(lead.get(sent_column(len(labels) - 1)) or "").strip()
        events.append((_parse(last_raw), last_raw, "✅", "Sequence completed"))             # the last email went out and nothing has happened since
    elif status == STATUS_PAUSED:
        events.append((_parse(stopped_raw), stopped_raw, "⏸️", "Paused"))
    due_raw = str(lead.get("NextEligibleAt") or "").strip()
    active = (not status.startswith("Stopped") and status not in (STATUS_REMOVED, STATUS_COMPLETED, STATUS_PAUSED) and not finished
              and str(lead.get("ReplyStatus") or "").strip() != "Replied")
    if due_raw and active:
        events.append((_parse(due_raw), due_raw, "⏳", "Next follow-up due"))
    dated = sorted((e for e in events if e[0] is not None), key=lambda e: e[0])
    undated = [e for e in events if e[0] is None]
    return [{"when": short_date(raw, today), "icon": icon, "text": text} for _, raw, icon, text in dated + undated]


# ---------------------------------------------------------------- extra filters (sender, stage, how recently)

SENDER_ALL, SENDER_NONE = "All senders", "(no sender yet)"
STAGE_ANY, STAGE_NONE = "Any stage", "Not contacted yet"
RECENT_ANY, RECENT_TODAY, RECENT_7, RECENT_30, RECENT_OLDER = "Any time", "Today", "Last 7 days", "Last 30 days", "Older than 30 days"
RECENCY_OPTIONS = [RECENT_ANY, RECENT_TODAY, RECENT_7, RECENT_30, RECENT_OLDER]


def highest_stage(lead: Dict, count: int) -> int:
    """The index of the last stage sent to this lead (0 = Intro …), or -1 when nothing has been sent."""
    sent = [i for i in range(count) if str(lead.get(sent_column(i)) or "").strip()]
    return max(sent) if sent else -1


def sender_options(leads: List[Dict]) -> List[str]:
    """'All senders', then every sender account that appears (sorted), then '(no sender yet)' if any lead has none."""
    accounts = sorted({str(lead.get("SenderAccount") or "").strip() for lead in leads} - {""})
    has_blank = any(not str(lead.get("SenderAccount") or "").strip() for lead in leads)
    return [SENDER_ALL] + accounts + ([SENDER_NONE] if has_blank else [])


def stage_options(labels: List[str]) -> List[str]:
    """'Any stage', 'Not contacted yet', then one 'At <stage>' per stage — the stage whose email went out most recently."""
    return [STAGE_ANY, STAGE_NONE] + [f"At {label}" for label in labels]


def apply_extra_filters(leads: List[Dict], count: int, labels: List[str], sender: str = SENDER_ALL, stage: str = STAGE_ANY,
                        recency: str = RECENT_ANY, today: Optional[date] = None) -> List[Dict]:
    """The leads that pass all three extra filters (the same dicts, in the same order). An option that is not recognised
    filters nothing, so a stale choice can never hide every lead."""
    today = today or date.today()
    result = []
    for lead in leads:
        account = str(lead.get("SenderAccount") or "").strip()
        if sender == SENDER_NONE and account:
            continue
        if sender not in (SENDER_ALL, SENDER_NONE) and account != sender:
            continue
        top = highest_stage(lead, count)
        if stage == STAGE_NONE and top != -1:
            continue
        if stage.startswith("At ") and stage in stage_options(labels) and top != stage_options(labels).index(stage) - 2:
            continue
        if recency in (RECENT_TODAY, RECENT_7, RECENT_30, RECENT_OLDER):
            moment = _parse(last_sent(lead, count))
            if moment is None:
                continue                                  # never sent (or unreadable): not "recent" and not "older" either
            age = (today - moment.date()).days
            if (recency == RECENT_TODAY and age != 0) or (recency == RECENT_7 and not 0 <= age <= 6) \
                    or (recency == RECENT_30 and not 0 <= age <= 29) or (recency == RECENT_OLDER and age < 30):
                continue
        result.append(lead)
    return result


def any_extra_filter(sender: str, stage: str, recency: str) -> bool:
    return (sender, stage, recency) != (SENDER_ALL, STAGE_ANY, RECENT_ANY)


# ---------------------------------------------------------------- pages

def paginate(total: int, page: int, size: int = PAGE_SIZE) -> Tuple[int, int, int, int]:
    """(page, start, end, pages) for `total` items: `page` is clamped into range, `start`/`end` slice the list, and an empty
    list still has one (empty) page."""
    size = max(1, int(size))
    total = max(0, int(total))
    pages = max(1, -(-total // size))
    page = min(max(0, int(page)), pages - 1)
    start = page * size
    return page, start, min(total, start + size), pages


def filter_counts(leads: List[Dict], options: List[str], apply_filter) -> Dict[str, int]:
    """How many leads each filter option would show (`apply_filter(leads, option)` is data_import_logic.filter_leads)."""
    return {option: len(apply_filter(leads, option)) for option in options}
