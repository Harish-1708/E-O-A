"""Pure logic behind the two-pane Inbox view of the Responses page and of
each campaign's Responses tab: what each conversation row shows, how long ago
it was, and — importantly — how untrusted email text is made safe to display.

Nothing here reads a Sheet or writes anything. Grouping, filtering, read
state and the thread itself all come from the existing logic modules
(responses_hub_logic, conversation_logic, responses_reply_logic); this adds
only what the new layout needs on top.
"""
import os
import re
import sys
from datetime import datetime, timezone
from email.utils import parseaddr
from typing import Callable, Dict, List, Optional, Set

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import outreach  # noqa: E402
from responses_hub_logic import group_unread_count  # noqa: E402
from responses_reply_logic import find_lead_for_response  # noqa: E402

# The list shows at most this many conversations (newest first). Every row is
# several widgets, so an unbounded list would make a large inbox slow; search
# and the filters narrow it, and the page says when rows are hidden.
MAX_ROWS = 100
PREVIEW_LENGTH = 90

_MARKDOWN_PUNCTUATION = re.compile(r"([!-/:-@\[-`{-~])")


def md_escape(text) -> str:
    """Makes untrusted text (a sender's name, a subject, a snippet — all
    written by strangers) safe to put inside Markdown.

    Streamlit renders Markdown in captions, labels and headings, so raw email
    text could otherwise inject links, images (a tracking pixel), colour
    directives (`:red[...]`), LaTeX (`$...$`) or emoji codes. CommonMark lets
    ANY ASCII punctuation be backslash-escaped and the backslash vanishes on
    display, so every punctuation character is escaped — the text reads
    exactly as written. Line breaks become spaces so one field stays on one
    line. Message BODIES never go through here: they are shown with st.text,
    which does not interpret Markdown at all."""
    flattened = re.sub(r"\s+", " ", str(text or "")).strip()
    return _MARKDOWN_PUNCTUATION.sub(r"\\\1", flattened)


# ---------------------------------------------------------------- display text

def _parse(value) -> Optional[datetime]:
    parsed = outreach._parse_dt(value) if value else None
    return parsed.replace(tzinfo=None) if parsed is not None and parsed.tzinfo else parsed


def relative_time(value, now: Optional[datetime] = None) -> str:
    """'just now', '5m ago', '3h ago', 'Yesterday', '4d ago', else '12 Oct'
    (with the year if it is not this year). Sheet times are UTC, so `now` is
    UTC too. An unreadable time gives '' rather than a wrong guess, and a
    time slightly in the future (clock skew) reads as 'just now'."""
    moment = _parse(value)
    if moment is None:
        return ""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    seconds = (now - moment).total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    days = (now.date() - moment.date()).days
    if days <= 1:
        return "Yesterday"
    if days < 7:
        return f"{days}d ago"
    return moment.strftime("%d %b") if moment.year == now.year else moment.strftime("%d %b %Y")


def preview_text(snippet, limit: int = PREVIEW_LENGTH) -> str:
    text = re.sub(r"\s+", " ", str(snippet or "")).strip()
    if not text:
        return "(no preview)"
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def sender_display(raw_from) -> Dict[str, str]:
    """{'name', 'email'} from a From header such as '"Kari B" <kari@x.com>'.
    A bare address uses its local part as the name; nothing at all gives
    '(unknown sender)'."""
    name, address = parseaddr(str(raw_from or ""))
    name = name.strip()
    if not name:
        name = address.split("@")[0] if address else ""
    return {"name": name or "(unknown sender)", "email": address}


def conversation_key(group: Dict) -> str:
    """The same identifier the classic layout keys its widgets by — one per
    (campaign, lead), stable whichever message is latest."""
    return f"{group['campaign']}:{group['lead_id']}"


# ---------------------------------------------------------------- the list

def build_conversation_rows(groups: List[Dict], read_keys: Optional[Set[str]],
                            leads_for: Callable[[str], List[Dict]],
                            now: Optional[datetime] = None) -> List[Dict]:
    """One display row per conversation, in the order given (the caller has
    already filtered and sorted newest first).

    `read_keys=None` means this space does not track read state (a campaign's
    own Responses tab): nothing is ever shown as unread there."""
    rows = []
    for group in groups:
        latest = group["latest"]
        who = sender_display(latest.get("From", ""))
        lead = find_lead_for_response(latest, leads_for(group["campaign"]))
        unread = group_unread_count(group, read_keys) if read_keys is not None else 0
        intent = (latest.get("Intent") or "").strip()
        rows.append({
            "key": conversation_key(group),
            "campaign": group["campaign"],
            "name": who["name"],
            "email": who["email"],
            "subject": (latest.get("Subject") or "").strip() or "(no subject)",
            "preview": preview_text(latest.get("Snippet", "")),
            "when": relative_time(latest.get("ReceivedAt", ""), now),
            "unread_count": unread,
            "unread": unread > 0,
            "message_count": len(group["messages"]),
            "intent": intent,
            "intent_confidence": (latest.get("IntentConfidence") or "").strip(),
            "action": (latest.get("ActionTaken") or "").strip(),
            "classification": (latest.get("Classification") or "").strip(),
            "stopped_status": (lead.get("Status") or "") if lead is not None and (lead.get("Status") or "").startswith("Stopped") else "",
        })
    return rows


def resolve_selection(row_keys: List[str], selected: Optional[str]) -> Optional[str]:
    """The conversation to show on the right: the one picked, if it is still
    in the (filtered) list, otherwise the first; None for an empty list."""
    if not row_keys:
        return None
    return selected if selected in row_keys else row_keys[0]


def bubble_side(message: Dict) -> str:
    """Our own messages sit on the right, theirs on the left."""
    return "right" if message.get("direction") == "outgoing" else "left"
