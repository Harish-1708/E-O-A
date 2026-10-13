"""The look of the Inbox: pure builders for the HTML and CSS of the Unibox-style layout — a status rail, a compact
conversation list, and each email as its own card — kept apart from Streamlit so they can be tested exhaustively.

SAFETY. Everything that comes from an email (a name, a subject, a preview, a body) is HTML-escaped here, once, by
`esc`, before it can reach the page; none of these builders ever passes email text through unescaped. Each builder
returns ONE line of HTML (no raw line breaks, no blank lines) because Streamlit parses Markdown around HTML: a
blank line would end the HTML block and let the rest be read as Markdown. Line breaks inside an email body are kept
by turning them into separate <div> lines, not by emitting newline characters.

Nothing here reads a Sheet, sends anything or knows about Streamlit.
"""
import html
import re
from datetime import datetime
from typing import Iterable, Optional

# ----------------------------------------------------------------------------- small pure helpers

AVATAR_COLORS = ["#16a34a", "#2563eb", "#7c3aed", "#ea580c", "#db2777", "#0d9488", "#4f46e5", "#ca8a04"]


_UNSAFE_CHARACTERS = re.compile("[\x00-\x08\x0b-\x1f\x7f\u202a-\u202e\u2066-\u2069]")


def esc(value) -> str:
    """The ONLY way email-derived text enters the markup.

    Besides escaping < > & and quotes, it removes what could change how the page is READ rather than what it says:
    line breaks become spaces (a blank line would end Streamlit's HTML block and let the rest be parsed as Markdown),
    control characters are dropped, and so are the Unicode text-direction overrides that can make a subject read
    backwards to disguise what it is. A body is split into lines BEFORE it gets here, so nothing is lost there."""
    text = str(value if value is not None else "").replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    return html.escape(_UNSAFE_CHARACTERS.sub("", text), quote=True)


def initials(name: str) -> str:
    """'Gertrud Liebhart' -> 'GL'; one word -> its first letter; '(no sender)' -> 'NS'; nothing usable -> '?'."""
    words = re.findall(r"[^\W\d_]+", str(name or ""))
    if not words:
        return "?"
    return (words[0][0] + (words[1][0] if len(words) > 1 else "")).upper()


def avatar_color(name: str) -> str:
    """The same name always gets the same colour, so a person is recognisable at a glance."""
    total = sum(ord(c) for c in str(name or "").strip().lower())
    return AVATAR_COLORS[total % len(AVATAR_COLORS)]


def _parse(value) -> Optional[datetime]:
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def list_date(value) -> str:
    """'Oct 12, 2026' — the date shown at the right of a list row. '' if unreadable."""
    moment = _parse(value)
    return f"{moment:%b} {moment.day}, {moment.year}" if moment else ""


def card_date(value) -> str:
    """'Oct 12, 2026, 2:17 PM' — the date shown on an email card. '' if unreadable."""
    moment = _parse(value)
    if not moment:
        return ""
    hour = moment.strftime("%I:%M %p").lstrip("0")
    return f"{moment:%b} {moment.day}, {moment.year}, {hour}"


def waiting_nudge(unread: bool, received_at, now: Optional[datetime] = None) -> str:
    """'Received 3 days ago. Reply?' — shown only on a conversation that is still unread a full day or more
    after the person wrote, as a prompt that someone is waiting. '' otherwise."""
    moment = _parse(received_at)
    if not unread or not moment:
        return ""
    days = ((now or datetime.utcnow()) - moment).days
    if days < 1:
        return ""
    return f"Received {days} day{'s' if days != 1 else ''} ago. Reply?"


# ----------------------------------------------------------------------------- statuses (the rail, the row icons, the pills)

# key, label, icon, colour — in the order the rail lists them
STATUSES = [
    ("lead", "Lead", "👤", "#2563eb"),
    ("interested", "Interested", "👍", "#16a34a"),
    ("not_interested", "Not interested", "👎", "#dc2626"),
    ("unclear", "Unclear", "❔", "#6b7280"),
    ("auto", "Auto-reply", "✈️", "#7c3aed"),
    ("bounced", "Bounced", "⚠️", "#b45309"),
]
_STATUS_BY_KEY = {key: (label, icon, color) for key, label, icon, color in STATUSES}


def status_of(intent: str = "", classification: str = "") -> str:
    """Which rail category a conversation belongs to, from its latest message's classification and intent."""
    kind, reading = str(classification or "").lower(), str(intent or "").lower()
    if "bounce" in kind:
        return "bounced"
    if "auto" in kind or "out of office" in kind:
        return "auto"
    if "not interested" in reading:
        return "not_interested"
    if "interested" in reading:
        return "interested"
    if "follow" in reading or "lead" in reading:
        return "lead"
    return "unclear"


def status_label(key: str) -> str:
    return _STATUS_BY_KEY.get(key, _STATUS_BY_KEY["unclear"])[0]


def status_icon(key: str) -> str:
    return _STATUS_BY_KEY.get(key, _STATUS_BY_KEY["unclear"])[1]


def status_color(key: str) -> str:
    return _STATUS_BY_KEY.get(key, _STATUS_BY_KEY["unclear"])[2]


# ----------------------------------------------------------------------------- markup builders (each returns ONE line)

def rail_item_html(label: str, count: Optional[int], icon: str, selected: bool) -> str:
    badge = f'<span class="ux-count">{count}</span>' if count is not None else ""
    return (f'<div class="ux-rail-item{" ux-selected" if selected else ""}"><span class="ux-rail-icon">{esc(icon)}</span>'
            f'<span class="ux-rail-label">{esc(label)}</span>{badge}</div>')


def rail_title_html(text: str) -> str:
    return f'<div class="ux-rail-title">{esc(text)}</div>'


def list_header_html(title: str, shown: int, total: int) -> str:
    detail = f"{shown} of {total}" if shown != total else f"{total}"
    return f'<div class="ux-list-head"><span class="ux-list-title">{esc(title)}</span><span class="ux-list-count">{esc(detail)}</span></div>'


def row_html(name: str, subject: str, preview: str, date_text: str, status_key: str, selected: bool, unread: bool,
             nudge: str = "", message_count: int = 1, unread_count: int = 0, campaign: str = "") -> str:
    classes = "ux-row" + (" ux-selected" if selected else "") + (" ux-unread" if unread else "")
    meta = []
    if campaign:
        meta.append(esc(campaign))
    if message_count > 1:
        meta.append(f"{message_count} messages")
    if unread_count:
        meta.append(f"{unread_count} new")
    nudge_html = f'<div class="ux-nudge">{esc(nudge)}</div>' if nudge else ""
    meta_html = f'<div class="ux-meta">{" · ".join(meta)}</div>' if meta else ""
    dot = '<span class="ux-dot"></span>' if unread else ""
    return (f'<div class="{classes}">{nudge_html}'
            f'<div class="ux-top"><span class="ux-icon" title="{esc(status_label(status_key))}">{esc(status_icon(status_key))}</span>'
            f'<span class="ux-name">{dot}{esc(name)}</span><span class="ux-date">{esc(date_text)}</span></div>'
            f'<div class="ux-subject">{esc(subject)}</div><div class="ux-preview">{esc(preview)}</div>{meta_html}</div>')


def thread_header_html(name: str, email: str, status_key: str, detail_chips: Iterable[str] = (), status_note: str = "") -> str:
    """The header above a conversation: avatar, name, address, any muted detail chips, and the coloured status pill
    (with an optional short note after the status, such as how sure the AI was)."""
    chips = "".join(f'<span class="ux-chip ux-chip-muted">{esc(c)}</span>' for c in detail_chips if c)
    note = f" · {esc(status_note)}" if status_note else ""
    return (f'<div class="ux-thread-head"><span class="ux-avatar" style="background:{avatar_color(name)}">{esc(initials(name))}</span>'
            f'<span class="ux-head-name">{esc(name)}</span><span class="ux-head-email">{esc(email)}</span>'
            f'<span class="ux-spacer"></span>{chips}'
            f'<span class="ux-chip" style="color:{status_color(status_key)};border-color:{status_color(status_key)}55">'
            f'{esc(status_icon(status_key))} {esc(status_label(status_key))}{note}</span></div>')


def _body_lines_html(body: str) -> str:
    """The body as one <div> per line, so line breaks and blank lines survive WITHOUT any newline character in the
    markup. A quoted line (one that starts with '>') loses its '>' markers and gets a muted left bar instead, one
    step further in for each level of quoting — the way a mail app shows the earlier messages it replies to."""
    lines = str(body or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = []
    for line in lines:
        match = re.match(r"^\s*((?:>\s?)+)(.*)$", line)
        if match:
            depth = match.group(1).count(">")
            text = match.group(2)
            out.append(f'<div class="ux-qline" style="margin-left:{min(depth, 6) * 10 - 10}px">{esc(text) if text.strip() else "&nbsp;"}</div>')
        else:
            out.append(f'<div class="ux-line">{esc(line) if line.strip() else "&nbsp;"}</div>')
    return "".join(out)


def card_html(subject: str, from_text: str, to_text: str, timestamp, body: str, empty_note: str = "") -> str:
    """One email as a card: large subject, From / To with the date at the right, a divider, then the body."""
    shown_date = card_date(timestamp)
    date_html = f'<span class="ux-card-date">{esc(shown_date)} UTC</span>' if shown_date else ""
    content = (f'<div class="ux-body">{_body_lines_html(body)}</div>' if str(body or "").strip()
               else f'<div class="ux-empty">{esc(empty_note)}</div>')
    return (f'<div class="ux-card"><div class="ux-title">{esc(subject) or "(no subject)"}</div>'
            f'<div class="ux-fromrow"><div class="ux-meta-line"><b>From:</b> {esc(from_text)}</div>{date_html}</div>'
            f'<div class="ux-meta-line"><b>To:</b> {esc(to_text)}</div><div class="ux-rule"></div>{content}</div>')


def empty_thread_html(text: str) -> str:
    return f'<div class="ux-placeholder">{esc(text)}</div>'


# ----------------------------------------------------------------------------- the stylesheet

_BASE_CSS = """
.ux-row, .ux-card, .ux-thread-head, .ux-list-head, .ux-rail-item, .ux-rail-title, .ux-placeholder {
  font-family: Inter, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; box-sizing: border-box; }

/* Streamlit gives the element that wraps every Markdown block a -16px bottom margin (to cancel a trailing paragraph
   margin). These blocks are not paragraphs, so that margin would pull the next block up underneath them. */
div:has(> .ux-row), div:has(> .ux-rail-title), div:has(> .ux-rail-item), div:has(> .ux-list-head), div:has(> .ux-thread-head),
div:has(> .ux-card), div:has(> .ux-placeholder) { margin-bottom: 0 !important; }

/* ---- the three panes. Every container key carries the page's name after a trailing underscore
   (ux_railpane_hub, ux_railpane_tab_Camp...), and each pattern ends in that underscore so that, for example,
   "ux_list_" can never also match "ux_listpane_" or an invisible button. */
[class*="st-key-ux_shell_"] { --ux-h: max(700px, calc(100vh - 230px)); border: 1px solid #e5e7eb; border-radius: 12px; background: #fff; overflow: hidden; }
/* use the whole width of the page: Streamlit's default content column is narrow, which squeezes three panes */
[data-testid="stMainBlockContainer"]:has([class*="st-key-ux_shell_"]) { max-width: 100% !important; padding-left: 1.5rem !important; padding-right: 1.5rem !important; }
/* the panes are as tall as the window allows (never shorter than 700px), instead of a fixed size */
[class*="st-key-ux_list_"] { height: calc(var(--ux-h) - 44px) !important; }
[class*="st-key-ux_threadbody_"] { height: calc(var(--ux-h) - 114px) !important; }
[class*="st-key-ux_shell_"] [data-testid="stHorizontalBlock"] { gap: 0 !important; }
[class*="st-key-ux_railpane_"] { gap: 0 !important; padding: 6px 8px; border-right: 1px solid #eef0f3; }
@media (min-width: 900px) {
  [class*="st-key-ux_railpane_"] { min-height: var(--ux-h); }
  /* the rail keeps a width that fits its longest label ("Not interested") and the counts, whatever the text size;
     the list and the conversation share the rest */
  [class*="st-key-ux_shell_"] [data-testid="stHorizontalBlock"] { flex-wrap: nowrap !important; }
  [class*="st-key-ux_shell_"] [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child { flex: 0 0 __RAIL_W__ !important; width: __RAIL_W__ !important; min-width: __RAIL_W__ !important; }
  [class*="st-key-ux_shell_"] [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(2) { flex: 1 1 0 !important; width: auto !important; min-width: 0 !important; }
  [class*="st-key-ux_shell_"] [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(3) { flex: 2 1 0 !important; width: auto !important; min-width: 0 !important; }
}
[class*="st-key-ux_listpane_"], [class*="st-key-ux_list_"], [class*="st-key-ux_threadpane_"] { gap: 0 !important; }
[class*="st-key-ux_listpane_"] { border-right: 1px solid #eef0f3; }
[class*="st-key-ux_threadbody_"] { gap: 0 !important; background: #fff; padding: 18px 20px; }

/* ---- rail */
.ux-rail-title { font-size: 11px; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; color: #6b7280; padding: 10px 10px 6px; }
.ux-rail-item { display: flex; align-items: center; gap: 10px; padding: 7px 10px; border-radius: 8px; font-size: 13px; color: #374151; margin: 1px 0; white-space: nowrap; }
.ux-rail-item.ux-selected { background: #dde9fb; color: #111827; font-weight: 600; }
.ux-rail-icon { width: 18px; text-align: center; font-size: 13px; }
.ux-rail-label { overflow: hidden; text-overflow: ellipsis; }
.ux-count { margin-left: auto; font-size: 11.5px; color: #6b7280; font-weight: 600; }

/* ---- list */
.ux-list-head { display: flex; align-items: center; padding: 12px 16px; border-bottom: 1px solid #eef0f3; background: #fff; }
.ux-list-title { font-size: 13px; font-weight: 700; color: #111827; }
.ux-list-count { margin-left: auto; font-size: 11.5px; color: #6b7280; }
.ux-row { padding: 11px 16px 12px; border-bottom: 1px solid #eef0f3; background: #fff; min-height: 76px; }
.ux-row.ux-selected { background: #dde9fb; }
.ux-nudge { font-size: 11.5px; font-weight: 700; color: #b45309; margin-bottom: 3px; }
.ux-top { display: flex; align-items: center; gap: 8px; min-height: 18px; }
.ux-icon { width: 16px; text-align: center; font-size: 12px; opacity: .85; }
.ux-name { font-size: 12px; color: #4b5563; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
.ux-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #2563eb; margin-right: 6px; vertical-align: middle; }
.ux-date { margin-left: auto; font-size: 11.5px; font-weight: 600; color: #4b5563; white-space: nowrap; padding-left: 8px; }
.ux-subject { font-size: 13.5px; font-weight: 500; color: #111827; margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ux-unread .ux-subject { font-weight: 700; }
.ux-preview { font-size: 12px; line-height: 1.4; color: #6b7280; margin-top: 3px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; word-break: break-word; }
.ux-meta { font-size: 11px; color: #9ca3af; margin-top: 4px; }

/* ---- a whole row (or rail item) is one click target: the real Streamlit button sits invisibly on top of its markup */
[class*="st-key-ux_row_"], [class*="st-key-ux_ritem_"] { position: relative; gap: 0 !important; cursor: pointer; }
[class*="st-key-ux_row_"] [class*="_open_"], [class*="st-key-ux_ritem_"] [class*="_railbtn_"] {
  position: absolute !important; inset: 0; width: 100% !important; height: 100% !important; margin: 0 !important; z-index: 3; }
/* the button's own wrapper must also fill the row, or only the top 40px of a tall row would respond to a click */
[class*="st-key-ux_row_"] [class*="_open_"] > div, [class*="st-key-ux_ritem_"] [class*="_railbtn_"] > div { width: 100% !important; height: 100% !important; }
[class*="st-key-ux_row_"] [class*="_open_"] button, [class*="st-key-ux_ritem_"] [class*="_railbtn_"] button {
  width: 100%; height: 100%; opacity: 0; cursor: pointer; border: 0; background: transparent; }
[class*="st-key-ux_row_"]:hover .ux-row:not(.ux-selected) { background: #f3f6fb; }
[class*="st-key-ux_ritem_"]:hover .ux-rail-item:not(.ux-selected) { background: #f3f4f6; }

/* ---- thread */
.ux-thread-head { display: flex; align-items: center; gap: 10px; padding: 12px 20px; background: #fff; border-bottom: 1px solid #e5e7eb; flex-wrap: wrap; }
.ux-avatar { width: 28px; height: 28px; border-radius: 50%; color: #fff; font-size: 11px; font-weight: 700; display: inline-flex; align-items: center; justify-content: center; flex: none; }
.ux-head-name { font-size: 15px; font-weight: 700; color: #111827; }
.ux-head-email { font-size: 12.5px; color: #6b7280; }
.ux-spacer { flex: 1; }
.ux-chip { font-size: 12px; font-weight: 600; border: 1px solid #d1d5db; border-radius: 8px; padding: 4px 10px; color: #374151; background: #fff; white-space: nowrap; }
.ux-chip-muted { font-weight: 500; color: #6b7280; background: #f9fafb; }

.ux-card { background: #fff; border: 1px solid #e5e7eb; border-radius: 10px; padding: 22px 28px 24px; margin: 0 0 16px; }
.ux-title { font-size: 21px; line-height: 1.3; font-weight: 700; color: #111827; margin-bottom: 10px; word-break: break-word; }
.ux-fromrow { display: flex; align-items: baseline; gap: 12px; }
.ux-meta-line { font-size: 12px; color: #4b5563; line-height: 1.7; word-break: break-word; }
.ux-meta-line b { color: #111827; font-weight: 600; }
.ux-card-date { margin-left: auto; font-size: 12px; color: #4b5563; white-space: nowrap; }
.ux-rule { height: 1px; background: #e5e7eb; margin: 14px 0 18px; }
.ux-body { font-size: 14px; line-height: 1.6; color: #111827; word-break: break-word; }
.ux-line { min-height: 1.6em; white-space: pre-wrap; }
.ux-qline { min-height: 1.6em; white-space: pre-wrap; color: #6b7280; border-left: 3px solid #e5e7eb; padding-left: 12px; }
.ux-empty { font-size: 13px; color: #6b7280; background: #f9fafb; border-radius: 8px; padding: 12px 14px; }
.ux-placeholder { text-align: center; color: #6b7280; font-size: 14px; padding: 80px 20px; }

/* ---- the Reply bar: a row of buttons sized to their text */
[class*="st-key-ux_bar_"] { flex-direction: row !important; align-items: center; background: #fff; border-top: 1px solid #e5e7eb; padding: 12px 20px; gap: 10px !important; }
[class*="st-key-ux_bar_"] > div { width: auto !important; flex: 0 0 auto; }
[class*="st-key-ux_reply_"] button, [class*="st-key-ux_bar_"] [class*="_mark_read_"] button, [class*="st-key-ux_bar_"] [data-testid="stPopover"] button {
  border-radius: 8px; font-size: 13px; font-weight: 600; padding: 6px 20px; min-height: 36px; white-space: nowrap; }
[class*="st-key-ux_reply_"] button { background: #2563eb; color: #fff; border: 1px solid #2563eb; }
[class*="st-key-ux_reply_"] button:hover { background: #1d4ed8; border-color: #1d4ed8; color: #fff; }
[class*="st-key-ux_bar_"] [class*="_mark_read_"] button, [class*="st-key-ux_bar_"] [data-testid="stPopover"] button { background: #fff; color: #374151; border: 1px solid #d1d5db; }

/* ---- small screens: the rail becomes one row of pills so it does not push everything down */
@media (max-width: 899px) {
  [class*="st-key-ux_shell_"] { --ux-h: 700px; }
  [class*="st-key-ux_railpane_"] { flex-direction: row !important; overflow-x: auto; gap: 6px !important; padding: 8px 10px; border-right: 0; border-bottom: 1px solid #eef0f3; min-height: 0; }
  [class*="st-key-ux_railpane_"] > div { width: auto !important; flex: 0 0 auto; }
  [class*="st-key-ux_railpane_"] [data-testid="stElementContainer"]:has(.ux-rail-title), .ux-rail-title { display: none; }
  .ux-rail-item { border: 1px solid #e5e7eb; border-radius: 999px; padding: 5px 12px; margin: 0; }
  .ux-rail-item.ux-selected { border-color: #bcd2f6; }
}
"""


# ----------------------------------------------------------------------------- text size

SIZES = {"Normal": 1.0, "Large": 1.15, "Extra large": 1.3}
DEFAULT_SIZE = "Large"


def inbox_css(scale: float = 1.0) -> str:
    """The stylesheet with every font size multiplied by `scale` (spacing and panes are left alone, so a larger text
    size reads larger without the layout shifting around)."""
    css = re.sub(r"font-size: (\d+(?:\.\d+)?)px", lambda m: f"font-size: {round(float(m.group(1)) * scale, 1):g}px", _BASE_CSS)
    return css.replace("__RAIL_W__", f"{round(190 * max(scale, 1.0))}px")


INBOX_CSS = inbox_css(1.0)
