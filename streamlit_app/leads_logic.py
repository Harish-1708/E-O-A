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


def manage_label(lead: Dict) -> str:
    """The text the 'Manage a lead' menu uses for a lead. Kept here so the table's click and that menu can never disagree."""
    return f"{lead.get('LeadID', '')} — {lead.get('FirstName', '')} {lead.get('LastName', '')} <{lead.get('Email', '')}>"


# ---------------------------------------------------------------- status

def status_summary(lead: Dict) -> Tuple[str, str]:
    """(label, colour family) for the status pill — what is true of the lead right now."""
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
    label, kind = status_summary(lead)
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
    elif status == STATUS_PAUSED:
        events.append((_parse(stopped_raw), stopped_raw, "⏸️", "Paused"))
    due_raw = str(lead.get("NextEligibleAt") or "").strip()
    active = (not status.startswith("Stopped") and status not in (STATUS_REMOVED, STATUS_COMPLETED, STATUS_PAUSED)
              and str(lead.get("ReplyStatus") or "").strip() != "Replied")
    if due_raw and active:
        events.append((_parse(due_raw), due_raw, "⏳", "Next follow-up due"))
    dated = sorted((e for e in events if e[0] is not None), key=lambda e: e[0])
    undated = [e for e in events if e[0] is None]
    return [{"when": short_date(raw, today), "icon": icon, "text": text} for _, raw, icon, text in dated + undated]


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
