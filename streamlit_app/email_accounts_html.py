"""The markup and styling of the Email Accounts page: the header, the health summary, an account's card (avatar, address, status, sent today) and the empty state. Anything that
came from a Sheet or a person (an address, an account name, a health detail) goes through esc() and can never become markup.
"""
import html
import re
from datetime import datetime
from typing import Dict, Optional

from campaigns_hub_html import relative_time

STATUS_CHIPS = {"ok": ("ok", "● Connected"), "bad": ("bad", "● Disconnected"), "unknown": ("unknown", "○ Unknown")}
LEGACY_NOTE = "Set up in Streamlit secrets. Add it again here with the same name to manage it."
SECURITY_NOTE = ("Passwords are never stored, logged or shown. Adding or editing an account passes its password through this app only for the instant it takes to encrypt it "
                 "and send it to GitHub.")
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def esc(value) -> str:
    return html.escape("" if value is None else str(value).replace("\x00", "").replace("\u202e", ""), quote=True)


def _one_line(markup: str) -> str:
    return " ".join(line.strip() for line in markup.splitlines() if line.strip())


def page_head_html(title: str, subtitle: str) -> str:
    return _one_line(f'<div class="ea-head"><div class="ea-tile">📧</div><div><div class="ea-title">{esc(title)}</div><div class="ea-sub">{esc(subtitle)}</div></div></div>')


def security_banner_html() -> str:
    return f'<div class="ea-banner">🔒 <span><b>Passwords are never stored, logged or shown.</b> {esc(SECURITY_NOTE.split(". ", 1)[1])}</span></div>'


def _stat(label: str, value_html: str, sub: str, bad: bool = False) -> str:
    return f'<div class="ea-stat{" ea-stat-bad" if bad else ""}"><div class="ea-stat-l">{esc(label)}</div><div class="ea-stat-v">{value_html}</div><div class="ea-stat-s">{esc(sub)}</div></div>'


def stats_html(summary: Dict, now: Optional[datetime] = None) -> str:
    """Connected of total, how many need attention, sent today across all campaigns, and when the health was last checked."""
    last = relative_time(summary.get("last_checked"), now) if summary.get("last_checked") else "Not yet"
    cards = [_stat("Connected", f'{int(summary["connected"])} <span class="ea-of">of {int(summary["total"])}</span>', "accounts"),
             _stat("Need attention", f'{int(summary["bad"])}', "disconnected", bad=bool(summary["bad"])),
             _stat("Sent today", f'{int(summary["sent_today"])}', "across all campaigns"),
             _stat("Last checked", f'<span class="ea-when">{esc(last)}</span>', "automatic checks every 2 hours")]
    return f'<div class="ea-stats">{"".join(cards)}</div>'


def account_card_html(card: Dict, now: Optional[datetime] = None) -> str:
    """The body of an account's card: who it is, how it is doing, and how much it sent today (its buttons are real buttons beside it)."""
    kind = card.get("kind") if card.get("kind") in STATUS_CHIPS else "unknown"
    css, text = STATUS_CHIPS[kind]
    color = card.get("color") if _COLOR.match(str(card.get("color") or "")) else "#9ca3af"
    chips = f'<span class="ea-chip">{esc(card.get("name"))}</span>'
    if card.get("is_default"):
        chips += '<span class="ea-chip ea-chip-info">⭐ Default</span>'
    if card.get("managed"):
        chips += f'<span class="ea-slot">Slot {esc(card.get("slot"))}</span>'
    else:
        chips += '<span class="ea-chip">Legacy</span>'
    reason = f'<div class="ea-why">{esc(card["detail"])}</div>' if kind == "bad" and card.get("detail") else ""
    when = f"Checked {relative_time(card['checked_at'], now)}" if card.get("checked_at") else "Not checked yet"
    return _one_line(f'<div class="ea-row"><div class="ea-id"><div class="ea-av" style="background:{color}">{esc(card.get("initial"))}</div><div><div class="ea-addr">{esc(card.get("address"))}</div>'
                     f'<div class="ea-chips">{chips}</div></div></div><div><span class="ea-chip ea-chip-{css}">{text}</span>{reason}<div class="ea-when-s">{esc(when)}</div></div>'
                     f'<div class="ea-num"><b>{int(card.get("sent_today") or 0)}</b><span>sent today</span></div></div>')


def legacy_note_html() -> str:
    return f'<div class="ea-legacy">{esc(LEGACY_NOTE)}</div>'


def empty_state_html(title: str, text: str) -> str:
    return _one_line(f'<div class="ea-empty"><div class="ea-empty-icon">📧</div><div class="ea-empty-title">{esc(title)}</div><div class="ea-empty-text">{esc(text)}</div></div>')


def dialog_note_html(text: str) -> str:
    return f'<div class="ea-dialog-note">{esc(text)}</div>'


def warn_box_html(text: str) -> str:
    return f'<div class="ea-warnbox">{esc(text)}</div>'


EA_CSS = """
/* ---- the Email Accounts page */
.ea-head { display: flex; align-items: center; gap: 14px; }
.ea-tile { width: 46px; height: 46px; border-radius: 13px; background: #eef2ff; display: flex; align-items: center; justify-content: center; font-size: 23px; flex: none; }
.ea-title { font-size: 32px; font-weight: 800; letter-spacing: -.02em; line-height: 1.15; color: #111827; }
.ea-sub { color: #6b7280; font-size: 14px; margin-top: 4px; }
.ea-banner { display: flex; gap: 12px; align-items: flex-start; background: #f3f7ff; border: 1px solid #c7d7fb; color: #1e3a8a; border-radius: 12px; padding: 12px 16px; font-size: 13.5px; line-height: 1.55; margin: 14px 0 0; }
.ea-stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin: 18px 0 16px; }
.ea-stat { background: #fff; border: 1px solid #e5e7eb; border-radius: 14px; padding: 16px 18px; }
.ea-stat-l { font-size: 12.5px; color: #6b7280; font-weight: 600; }
.ea-stat-v { font-size: 27px; font-weight: 800; letter-spacing: -.02em; margin-top: 4px; color: #111827; }
.ea-stat-bad .ea-stat-v { color: #b91c1c; }
.ea-of { font-size: 15px; color: #6b7280; font-weight: 600; letter-spacing: 0; }
.ea-when { font-size: 21px; }
.ea-stat-s { font-size: 12.5px; color: #6b7280; margin-top: 3px; }

[class*="st-key-ea_card_"] { background: #fff; border: 1px solid #e5e7eb; border-radius: 14px; padding: 16px 18px 20px; margin-bottom: 4px; gap: 0 !important; }
[class*="st-key-ea_card_bad"] { border-color: #f3c4c4; background: #fffafa; }
[class*="st-key-ea_card_"] [data-testid="stHorizontalBlock"] { align-items: center; gap: 1rem; }
.ea-row { display: grid; grid-template-columns: minmax(250px, 1.5fr) 220px 100px; gap: 16px; align-items: center; }
.ea-id { display: flex; gap: 13px; align-items: center; }
.ea-av { width: 42px; height: 42px; border-radius: 50%; color: #fff; font-weight: 800; font-size: 17px; display: flex; align-items: center; justify-content: center; flex: none; }
.ea-addr { font-weight: 700; font-size: 15px; color: #111827; word-break: break-all; }
.ea-chips { display: flex; gap: 7px; margin-top: 5px; flex-wrap: wrap; align-items: center; }
.ea-chip { display: inline-flex; align-items: center; font-size: 12.5px; font-weight: 600; border-radius: 999px; padding: 3px 11px; border: 1px solid #d1d5db; background: #fff; color: #374151; white-space: nowrap; }
.ea-chip-ok { color: #15803d; border-color: #bbe3c9; background: #f0fbf4; }
.ea-chip-bad { color: #b91c1c; border-color: #f3c4c4; background: #fff5f5; }
.ea-chip-info { color: #1d4ed8; border-color: #c7d7fb; background: #f3f7ff; }
.ea-chip-unknown { color: #6b7280; background: #f9fafb; }
.ea-slot { font-size: 12px; color: #6b7280; }
.ea-why { font-size: 12.5px; color: #b91c1c; margin-top: 5px; line-height: 1.4; }
.ea-when-s { font-size: 12px; color: #9ca3af; margin-top: 5px; }
.ea-num b { font-size: 21px; font-weight: 800; display: block; color: #111827; }
.ea-num span { font-size: 12px; color: #6b7280; font-weight: 600; }
.ea-legacy { font-size: 12px; color: #6b7280; line-height: 1.45; text-align: right; }
[class*="st-key-ea_card_"] .stButton button { white-space: nowrap; font-weight: 600; }
[class*="st-key-ea_card_"] [class*="st-key-ea_fix_"] button { color: #1d4ed8; border-color: #b7cff7; background: #f3f7ff; }
[class*="st-key-ea_card_"] [class*="st-key-ea_remove_"] button { color: #b91c1c; border-color: #f3c4c4; }
[class*="st-key-ea_toolbar"] [data-testid="stHorizontalBlock"] { align-items: center; gap: .75rem; }
.ea-empty { text-align: center; padding: 34px 20px 12px; }
.ea-empty-icon { width: 62px; height: 62px; border-radius: 50%; background: #eef2ff; display: inline-flex; align-items: center; justify-content: center; font-size: 28px; }
.ea-empty-title { font-size: 17px; font-weight: 700; margin: 12px 0 6px; color: #111827; }
.ea-empty-text { color: #6b7280; font-size: 13.5px; margin-bottom: 10px; }

/* ---- the dialogs (add, bulk add, edit, remove) */
.ea-dialog-note { color: #6b7280; font-size: 13.5px; line-height: 1.55; margin-bottom: 12px; }
[class*="st-key-manage_remove_"] button:not(:disabled) { background: #dc2626; border-color: #dc2626; color: #fff; }
.ea-warnbox { background: #fffaf0; border: 1px solid #f3d9a8; color: #92400e; border-radius: 10px; padding: 10px 14px; font-size: 13px; line-height: 1.5; margin: 6px 0 10px; }
"""
