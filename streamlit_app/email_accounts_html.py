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


_ICON = {"mail": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/>', "lock": '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
         "check": '<path d="M5 13l4 4L19 7"/>'}


def _svg(name: str, size: int, color: str = "currentColor", stroke: float = 1.8) -> str:
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round" '
            f'style="flex:none">{_ICON[name]}</svg>')


def dialog_header_html(subtitle: str) -> str:
    """The icon and one line under the dialog's own title (repeating the title here would show it twice)."""
    return _one_line(f'<div class="ea-dh"><div class="ea-dh-tile">{_svg("mail", 20)}</div><div class="ea-dh-s">{esc(subtitle)}</div></div>')


def field_feedback_html(kind: str, text: str) -> str:
    """The line under a field as you type: red for a problem, green for good, blue for a note, grey for a hint. Nothing at all when there is nothing to say."""
    if not text:
        return ""
    css = kind if kind in ("ok", "error", "info", "hint") else "hint"
    mark = {"ok": "✓ ", "error": "✗ ", "info": "ℹ "}.get(css, "")
    return f'<div class="ea-fl ea-fl-{css}">{mark}{esc(text)}</div>'


def group_title_html(text: str) -> str:
    return f'<div class="ea-grp-t">{esc(text)}</div>'


def secure_note_html() -> str:
    return _one_line(f'<div class="ea-secure">{_svg("lock", 16, "#1e3a8a")}<span>Encrypted on its way to GitHub. The password is never stored here.</span></div>')


def result_panel_html(name: str, address: str, slot) -> str:
    """What the dialog shows once an account has been added: what was done, and where."""
    steps = "".join(f'<div class="ea-step"><i>{_svg("check", 12, "#15803d", 3)}</i>{esc(text)}</div>' for text in
                    ("Password encrypted and sent to GitHub", f"Slot {slot} assigned to the account", "Connection check started"))
    return _one_line(f'<div class="ea-done"><div class="ea-done-ico">{_svg("check", 28, "#15803d", 2.6)}</div><div class="ea-done-t">{esc(name)} was added</div>'
                     f'<div class="ea-done-s">{esc(address)} · slot {esc(slot)}</div><div class="ea-steps">{steps}</div>'
                     f'<div class="ea-done-n">It can take a minute to appear in the list while the app redeploys.</div></div>')


def bulk_step_html(number: int, text: str) -> str:
    """A numbered heading inside the Bulk add dialog ('1  Your file needs these columns')."""
    return f'<div class="ea-bstep"><span class="ea-bnum">{int(number)}</span><b>{esc(text)}</b></div>'


def column_chips_html(required, optional) -> str:
    """The columns a file needs: required ones as blue chips, optional ones as grey chips, each group under its own small heading."""
    req = "".join(f'<span class="ea-cc ea-cc-req">{esc(c)}</span>' for c in required)
    opt = "".join(f'<span class="ea-cc ea-cc-opt">{esc(c)}</span>' for c in optional)
    return _one_line(f'<div class="ea-clabel">Required</div><div>{req}</div><div class="ea-clabel">Optional: a blank Name is taken from the address; the rest only matter for a non-Gmail provider (Hostinger and the like)</div><div>{opt}</div>')


def bulk_summary_html(ready: int, skipped: int) -> str:
    """The two chips over the review table: how many rows are ready (green) and how many were skipped (amber, only when some were)."""
    chips = f'<span class="ea-sumc ea-sumc-ok">✓ {int(ready)} ready</span>'
    if skipped:
        chips += f'<span class="ea-sumc ea-sumc-warn">⚠ {int(skipped)} skipped</span>'
    return f'<div class="ea-sumrow">{chips}</div>'


def bulk_skipped_html(messages) -> str:
    """Why rows were skipped, one line each, in an amber box."""
    lines = "".join(f"<div>{esc(m)}</div>" for m in messages)
    return f'<div class="ea-warnbox"><b>Skipped:</b>{lines}</div>' if lines else ""


def bulk_progress_html(done, remaining) -> str:
    """The list under the progress bar while accounts are being added: the ones done with a tick, the one in hand 'adding…', and the rest greyed."""
    rows = "".join(f'<div class="ea-prow"><i class="ea-ptick">✓</i>{esc(n)} — added</div>' for n in done)
    rows += "".join(f'<div class="ea-prow ea-prest"><i class="ea-ptick ea-pnone">·</i>{esc(n)}{" — adding…" if i == 0 else ""}</div>' for i, n in enumerate(remaining))
    return f'<div class="ea-plist">{rows}</div>'


def bulk_result_html(added, skipped) -> str:
    """What the dialog shows when a bulk add has finished: every account added with its slot, and anything skipped with the reason."""
    if added:
        head = (f'<div class="ea-done-ico">{_svg("check", 28, "#15803d", 2.6)}</div><div class="ea-done-t">{len(added)} account{"" if len(added) == 1 else "s"} added</div>'
                '<div class="ea-done-s">A connection check has started for each.</div>')
        rows = "".join(f'<div class="ea-step"><i>{_svg("check", 12, "#15803d", 3)}</i>{esc(n)} <span class="ea-slot">· slot {esc(s)}</span></div>' for n, s in added)
        note = '<div class="ea-done-n">They can take a minute to appear in the list while the app redeploys.</div>'
    else:
        head = '<div class="ea-done-t">No accounts were added</div><div class="ea-done-s">Everything in the file was skipped.</div>'
        rows, note = "", ""
    return _one_line(f'<div class="ea-done">{head}<div class="ea-steps">{rows}</div>{bulk_skipped_html(skipped)}{note}</div>')


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
.ea-dh { display: flex; gap: 12px; align-items: center; margin-bottom: 14px; }
.ea-dh-tile { width: 40px; height: 40px; border-radius: 12px; background: #eef2ff; color: #1d4ed8; display: flex; align-items: center; justify-content: center; flex: none; }
.ea-dh-s { font-size: 14px; color: #4b5563; }
.ea-fl { font-size: 12.5px; margin: -6px 0 10px; line-height: 1.45; }
.ea-fl-ok { color: #15803d; } .ea-fl-error { color: #b91c1c; } .ea-fl-info { color: #1d4ed8; } .ea-fl-hint { color: #6b7280; }
.ea-grp-t { font-size: 13px; font-weight: 700; color: #111827; margin-bottom: 6px; }
[class*="st-key-ea_grp_"] { border: 1px solid #e5e7eb; border-radius: 12px; padding: 12px 14px; background: #fafbfc; }
.ea-secure { display: flex; gap: 9px; align-items: center; background: #f3f7ff; border: 1px solid #c7d7fb; color: #1e3a8a; border-radius: 10px; padding: 9px 12px; font-size: 12.5px; margin: 10px 0; }
[class*="st-key-add_account_provider"], [class*="st-key-add_account_provider"] [data-testid="stRadio"], [class*="st-key-add_account_provider"] [data-testid="stRadio"] > div { width: 100% !important; }
[class*="st-key-add_account_provider"] [role="radiogroup"] { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; width: 100%; }
[class*="st-key-add_account_provider"] label[data-testid="stRadioOption"] { border: 1.5px solid #d1d5db; border-radius: 12px; padding: 12px 14px; background: #fff; margin: 0; align-items: flex-start; }
[class*="st-key-add_account_provider"] label[data-testid="stRadioOption"]:has(input:checked) { border-color: #2563eb; background: #f3f7ff; box-shadow: 0 0 0 3px rgba(37, 99, 235, .12); }
.ea-done { text-align: center; padding: 6px 0 2px; }
.ea-done-ico { width: 56px; height: 56px; border-radius: 50%; background: #dcfce7; display: inline-flex; align-items: center; justify-content: center; }
.ea-done-t { font-size: 20px; font-weight: 800; color: #111827; margin: 10px 0 2px; } .ea-done-s { font-size: 13px; color: #6b7280; margin-bottom: 12px; }
.ea-steps { display: inline-flex; flex-direction: column; gap: 8px; text-align: left; }
.ea-step { display: flex; gap: 10px; align-items: center; font-size: 13.5px; color: #374151; }
.ea-step i { width: 20px; height: 20px; border-radius: 50%; background: #dcfce7; display: flex; align-items: center; justify-content: center; flex: none; }
.ea-done-n { font-size: 12px; color: #6b7280; margin: 14px 0 4px; }
.ea-bstep { display: flex; gap: 10px; align-items: center; margin: 16px 0 8px; font-size: 14px; color: #111827; }
.ea-bnum { width: 22px; height: 22px; border-radius: 50%; background: #2563eb; color: #fff; font-weight: 800; font-size: 12px; display: inline-flex; align-items: center; justify-content: center; flex: none; }
.ea-clabel { font-size: 12px; color: #6b7280; font-weight: 700; text-transform: uppercase; letter-spacing: .03em; margin: 8px 0 2px; }
.ea-cc { display: inline-block; border-radius: 999px; padding: 3px 10px; font-size: 12px; font-weight: 700; margin: 3px 4px 3px 0; }
.ea-cc-req { background: #dde9fb; color: #1d4ed8; } .ea-cc-opt { background: #f1f2f4; color: #4b5563; }
.ea-sumrow { margin: 4px 0 6px; }
.ea-sumc { display: inline-flex; align-items: center; border-radius: 999px; padding: 4px 12px; font-size: 13px; font-weight: 700; margin-right: 8px; }
.ea-sumc-ok { background: #f0fbf4; color: #15803d; border: 1px solid #bbe3c9; } .ea-sumc-warn { background: #fffaf0; color: #b45309; border: 1px solid #f3d9a8; }
.ea-plist { margin-top: 12px; } .ea-prow { display: flex; gap: 10px; align-items: center; font-size: 13.5px; color: #374151; margin: 7px 0; } .ea-prest { color: #9ca3af; }
.ea-ptick { width: 20px; height: 20px; border-radius: 50%; background: #dcfce7; color: #15803d; display: inline-flex; align-items: center; justify-content: center; font-size: 12px; font-style: normal; flex: none; } .ea-pnone { background: #eef0f3; color: #9ca3af; }
.ea-slot { color: #6b7280; }
/* the upload box becomes a dashed drop area */
[class*="st-key-bulk_accounts_csv_upload"] [data-testid="stFileUploaderDropzone"] { border: 2px dashed #c7d7fb; background: #f8faff; border-radius: 14px; padding: 22px; }
.ea-warnbox { background: #fffaf0; border: 1px solid #f3d9a8; color: #92400e; border-radius: 10px; padding: 10px 14px; font-size: 13px; line-height: 1.5; margin: 6px 0 10px; }
"""
