"""Markup and styling for the Settings tab: card headings, an account's name and usage, the status pill, the Readiness list and the Danger zone.

PURE. Everything that comes from a campaign, an account or the Sheet goes through performance_html.esc, and every builder returns one line.
The cards themselves are native containers (their widgets are Streamlit's own) named with the prefix 'se_card_'; no other element's name contains that text.
"""
import re
from typing import Dict, List, Optional

from campaign_html import STATUS_KIND
from performance_html import esc, _one_line


def card_heading_html(title: str, subtitle: str = "", danger: bool = False) -> str:
    sub = f'<div class="se-sub">{esc(subtitle)}</div>' if subtitle else ""
    return _one_line(f'<div class="se-title{" se-danger-title" if danger else ""}">{esc(title)}</div>{sub}')


def account_info_html(address: str, note: str, kind: str) -> str:
    """An account's address with, under it, its health or why it is not in use. An unknown kind is drawn as plain grey, so a bad value can never become a class."""
    css = kind if kind in ("ok", "bad", "unknown") else "unknown"
    return _one_line(f'<div class="se-acct"><div class="se-acct-name">{esc(address)}</div><div class="se-acct-note se-note-{css}">{esc(note)}</div></div>')


def usage_html(sent: int, limit: Optional[int], fraction: float) -> str:
    """Today's sends for an account: a bar against its per-account limit with 'N sent today · limit L', or just 'N sent today' when there is no limit."""
    try:
        width = max(0, min(100, round(float(fraction) * 100)))
    except (TypeError, ValueError):
        width = 0
    if limit:
        return _one_line(f'<div class="se-usage"><div class="se-track"><i style="width:{width}%"></i></div><div class="se-usage-text">{esc(sent)} sent today · limit {esc(limit)}</div></div>')
    return _one_line(f'<div class="se-usage se-usage-plain"><div class="se-usage-text">{esc(sent)} sent today</div></div>')


def note_html(text: str, kind: str = "info") -> str:
    """A line of explanation (what the account choices do; whether the limits fit). 'ok', 'warn' and 'info' have their own colours; anything else is plain."""
    css = kind if kind in ("ok", "warn", "info") else "info"
    return f'<div class="se-line se-line-{css}">{esc(text)}</div>'


def status_pill_html(status: str, label: str) -> str:
    """The campaign's status as a pill: a dot, then the words without any emoji the label may start with. The colour family is the header's (good / waiting / bad)."""
    kind = STATUS_KIND.get(status, "neutral")
    words = re.sub(r"^[^\w]+", "", str(label or "")).strip()
    return f'<span class="se-pill se-pill-{kind}">● {esc(words)}</span>'


_ICONS = {"ok": "✅", "bad": "❌", "info": "ℹ️", "warn": "⚠️"}


def readiness_html(items: List[Dict]) -> str:
    """The Readiness checklist: an icon and a line for each check."""
    rows = "".join(f'<div class="se-check"><span class="se-check-icon">{_ICONS.get(item.get("kind"), "ℹ️")}</span><span>{esc(item.get("text"))}</span></div>' for item in items)
    return _one_line(f'<div class="se-checks">{rows}</div>')


def section_label_html(text: str) -> str:
    """A small grey capitals label above a group of cards ('Integrations', 'Maintenance')."""
    return f'<div class="se-section">{esc(text)}</div>'


def chip_html(kind: str, text: str) -> str:
    """A small pill: 'on' (green), 'unsaved' (amber) or anything else (grey, as 'off'). An unknown kind can never become a class name."""
    css = kind if kind in ("on", "unsaved") else "off"
    return f'<span class="se-chip se-chip-{css}">{esc(text)}</span>'


def tool_heading_html(icon: str, title: str, subtitle: str, chip_kind: str, chip_text: str) -> str:
    """The head of an integration card: an icon, the title with its state pill, and a one-line purpose."""
    return _one_line(f'<div class="se-tool"><div class="se-ico">{esc(icon)}</div><div><div class="se-tool-title"><span class="se-title">{esc(title)}</span>'
                     f'{chip_html(chip_kind, chip_text)}</div><div class="se-sub" style="margin:3px 0 0">{esc(subtitle)}</div></div></div>')


def tool_note_html(text: str) -> str:
    """The short explanation inside an integration card."""
    return f'<div class="se-tool-note">{esc(text)}</div>'


SETTINGS_CSS = """
.se-title { font-size: 18px; font-weight: 700; color: #111827; }
.se-sub { font-size: 13.5px; color: #6b7280; margin: 2px 0 10px; }
.se-danger-title { color: #b91c1c; }

/* ---- the cards: native containers styled as cards (their widgets are Streamlit's own) */
[class*="st-key-se_card_"] { background: #fff; border: 1px solid #e5e7eb; border-radius: 14px; padding: 20px 22px; margin-bottom: 14px; }
[class*="st-key-se_card_danger"] { border-color: #f3c4c4; }

/* ---- sender accounts */
[class*="st-key-se_rotation_"] label p { font-weight: 700; font-size: 15px; color: #111827; }
[class*="st-key-se_selectall_"] { display: flex; flex-direction: column; align-items: flex-end; }
[class*="st-key-se_selectall_"] button { border: 0 !important; background: transparent !important; color: #6b7280 !important; font-size: 13.5px !important; padding: 0 !important; min-height: 0 !important; box-shadow: none !important; }
[class*="st-key-se_selectall_"] button:hover { color: #1d4ed8 !important; }
[class*="st-key-se_acctrow_"] { border-top: 1px solid #eef0f3; padding: 10px 0 4px; }
.se-acct-name { font-weight: 700; font-size: 15px; color: #111827; }
.se-acct-note { font-size: 13px; margin-top: 1px; }
.se-note-ok, .se-note-unknown { color: #6b7280; }
.se-note-bad { color: #b91c1c; }
.se-usage { text-align: left; padding-top: 2px; width: 176px; max-width: 100%; margin-left: auto; }
.se-track { height: 8px; border-radius: 99px; background: #eef0f4; overflow: hidden; }
.se-track i { display: block; height: 100%; border-radius: 99px; background: #2563eb; }
.se-usage-text { font-size: 12.5px; color: #6b7280; margin-top: 5px; }
.se-usage-plain .se-usage-text { text-align: right; margin-top: 0; }
.se-line { font-size: 13.5px; line-height: 1.5; margin: 12px 0 14px; }
.se-line-info { color: #4b5563; }
.se-line-ok { color: #15803d; }
.se-line-warn { color: #b45309; }

/* ---- sending limits */
[class*="st-key-se_limit_"] label p { font-weight: 700; font-size: 14px; color: #111827; }
[class*="st-key-se_save"] button { background: #2563eb !important; border: 1px solid #2563eb !important; color: #fff !important; border-radius: 9px !important; font-weight: 700 !important; }

/* ---- the integration and maintenance cards */
.se-section { font-size: 12px; font-weight: 700; letter-spacing: .05em; text-transform: uppercase; color: #6b7280; margin: 8px 2px 8px; }
.se-tool { display: flex; align-items: center; gap: 12px; }
.se-ico { width: 34px; height: 34px; border-radius: 10px; background: #eef2ff; display: flex; align-items: center; justify-content: center; font-size: 17px; flex: none; }
.se-tool-title { display: flex; align-items: center; gap: 10px; }
.se-chip { font-size: 12px; font-weight: 700; border-radius: 99px; padding: 2px 10px; border: 1px solid #d1d5db; color: #6b7280; background: #f9fafb; }
.se-chip-on { color: #15803d; border-color: #bbe3c9; background: #f0fbf4; }
.se-chip-unsaved { color: #b45309; border-color: #f3d9a8; background: #fffaf0; }
.se-tool-note { font-size: 13px; line-height: 1.55; color: #6b7280; margin: 10px 0 4px; }
[class*="st-key-se_card_maint"] details, [class*="st-key-se_card_asana"] details, [class*="st-key-se_card_tracker"] details,
[class*="st-key-se_card_maint"] [data-testid="stExpander"], [class*="st-key-se_card_asana"] [data-testid="stExpander"], [class*="st-key-se_card_tracker"] [data-testid="stExpander"] { border: 0 !important; background: transparent !important; box-shadow: none !important; }
[class*="st-key-se_card_maint"] summary, [class*="st-key-se_card_asana"] summary, [class*="st-key-se_card_tracker"] summary { background: transparent !important; padding-left: 0 !important; font-weight: 700; }
[class*="st-key-se_card_asana"] summary, [class*="st-key-se_card_tracker"] summary { color: #1d4ed8; font-weight: 600; font-size: 13px; }
[class*="st-key-se_card_asana"] [data-testid="stHorizontalBlock"], [class*="st-key-se_card_tracker"] [data-testid="stHorizontalBlock"] { align-items: center; }

/* ---- campaign status: the pill on the left, the button at the right of the same row */
[class*="st-key-se_card_status"] [data-testid="stHorizontalBlock"] { align-items: center; }
[class*="st-key-se_statusaction"] { display: flex; flex-direction: column; align-items: flex-end; }
.se-pill { display: inline-block; border: 1px solid #d1d5db; border-radius: 99px; padding: 4px 12px; font-size: 13px; font-weight: 700; color: #4b5563; background: #f9fafb; }
.se-pill-good { color: #15803d; border-color: #bbe3c9; background: #f0fbf4; }
.se-pill-waiting { color: #b45309; border-color: #f3d9a8; background: #fffaf0; }
.se-pill-bad { color: #b91c1c; border-color: #f3c4c4; background: #fff5f5; }

/* ---- readiness */
.se-checks { margin-top: 6px; }
.se-check { display: flex; gap: 8px; align-items: flex-start; font-size: 14px; color: #111827; line-height: 1.5; margin: 9px 0; }
.se-check-icon { flex: 0 0 auto; }

/* ---- danger zone: outlined red buttons */
[class*="st-key-se_archive"] button, [class*="st-key-se_delete_open"] button { background: #fff !important; border: 1px solid #f3c4c4 !important; color: #b91c1c !important; font-weight: 700 !important; border-radius: 9px !important; }
[class*="st-key-se_archive"] button:hover, [class*="st-key-se_delete_open"] button:hover { background: #fff5f5 !important; }
"""
