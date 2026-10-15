"""Markup and styling of the sidebar and the Home page. Anything that came from a person or a Sheet (a campaign's name, an account's address or its health detail, the user's
name) goes through esc() and can never become markup. The only SVG here is fixed icon artwork written in this file.
"""
import html
from datetime import datetime
from typing import Dict, Optional

from campaigns_hub_html import relative_time

ICON_PATHS = {
    "send": '<path d="M22 2L11 13M22 2l-7 20-4-9-9-4z"/>',
    "chat": '<path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 8.5 8.5 0 0 1-3.8-.9L3 21l1.9-5.2A8.4 8.4 0 1 1 21 11.5z"/>',
    "mail": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/>',
    "alert": '<path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9L2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>',
    "check": '<path d="M5 13l4 4L19 7"/>', "today": '<rect x="3" y="4" width="18" height="17" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
    "perf": '<path d="M18 20V10M12 20V4M6 20v-6"/>', "camp": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
}
KIND_ICON = {"unread": ("chat", "#2563eb"), "account": ("mail", "#dc2626"), "campaign": ("alert", "#d97706")}


def esc(value) -> str:
    return html.escape("" if value is None else str(value).replace("\x00", "").replace("\u202e", ""), quote=True)


def _one_line(markup: str) -> str:
    return " ".join(line.strip() for line in markup.splitlines() if line.strip())


def icon_svg(name: str, size: int = 18, color: str = "currentColor", stroke: float = 1.8) -> str:
    path = ICON_PATHS.get(name, "")
    return (f'<svg width="{int(size)}" height="{int(size)}" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="{stroke}" stroke-linecap="round" '
            f'stroke-linejoin="round" style="flex:none">{path}</svg>')


def brand_html() -> str:
    return _one_line(f'<div class="sh-brand"><div class="sh-brand-tile">{icon_svg("send", 20, "#fff")}</div><div><b>Email Outreach</b><span>Control panel</span></div></div>')


def user_card_html(name: str) -> str:
    letter = (str(name or "").strip()[:1] or "?").upper()
    return _one_line(f'<div class="sh-user"><div class="sh-user-av">{esc(letter)}</div><div><b>{esc(name)}</b><span>Signed in</span></div></div>')


def greeting_html(greeting: str, date_text: str) -> str:
    return _one_line(f'<div class="sh-greet">{esc(greeting)}</div><div class="sh-greet-sub">{esc(date_text)} · here is where things stand.</div>')


def section_html(title: str) -> str:
    return f'<div class="sh-section">{esc(title)}</div>'


def attention_card_html(item: Dict) -> str:
    """The text of one 'needs your attention' card: an icon with the title, and the detail line. (Its link is a real link beside it.)"""
    icon, color = KIND_ICON.get(item.get("kind"), KIND_ICON["campaign"])
    return _one_line(f'<div class="sh-att-t">{icon_svg(icon, 18, color)}<span>{esc(item.get("title"))}</span></div><div class="sh-att-d">{esc(item.get("detail"))}</div>')


def all_clear_html() -> str:
    return _one_line(f'<div class="sh-clear">{icon_svg("check", 20, "#15803d", 2.4)}<div><b>All clear</b><span>No unread replies, every account is connected, and no campaign needs attention.</span></div></div>')


def stat_card_html(label: str, value_html: str, sub: str) -> str:
    return f'<div class="sh-stat"><div class="sh-stat-l">{esc(label)}</div><div class="sh-stat-v">{value_html}</div><div class="sh-stat-s">{esc(sub)}</div></div>'


def stats_html(summary: Dict, now: Optional[datetime] = None) -> str:
    """The four figures: running campaigns, sent today (against the total daily limit of the running ones), replies waiting, and accounts connected."""
    limit = summary.get("limit_total")
    accounts = summary["accounts"]
    checked = f"checked {relative_time(accounts['last_checked'], now)}" if accounts.get("last_checked") else "not checked yet"
    cards = [stat_card_html("Running campaigns", esc(summary["running"]), f"of {int(summary['campaigns'])} campaign{'' if summary['campaigns'] == 1 else 's'}"),
             stat_card_html("Sent today", esc(summary["sent_today"]), f"of {int(limit)} daily limit" if limit else "across all campaigns"),
             stat_card_html("Replies waiting", esc(summary["unread_total"]), "unread conversations"),
             stat_card_html("Accounts connected", f'{int(accounts["connected"])} <span class="sh-of">of {int(accounts["total"])}</span>', checked)]
    return f'<div class="sh-stats">{"".join(cards)}</div>'


def running_card_html(item: Dict) -> str:
    """A running campaign: its name, when it next sends, and today's progress against its daily limit."""
    if item.get("limit"):
        progress = (f'<div class="sh-run-t"><b>{int(item["sent_today"])}</b> of {int(item["limit"])} sent today</div>'
                    f'<div class="sh-bar"><i style="width:{max(0, min(100, int(item.get("percent") or 0)))}%"></i></div>')
    else:
        progress = f'<div class="sh-run-t"><b>{int(item["sent_today"])}</b> sent today · no daily limit</div>'
    nxt = esc(item.get("next_send")) if item.get("next_send") else "not scheduled in the next 8 days"
    return _one_line(f'<div class="sh-run"><div><div class="sh-run-name">{esc(item.get("name"))}</div><span class="sh-pill">● Running</span></div>'
                     f'<div><div class="sh-run-l">Next automatic send</div><div class="sh-run-v">{nxt}</div></div><div>{progress}</div></div>')


def guide_card_html(icon: str, title: str, text: str) -> str:
    return _one_line(f'<div class="sh-gi">{icon_svg(icon, 18)}</div><div class="sh-gt">{esc(title)}</div><div class="sh-gd">{esc(text)}</div>')


def skeleton_html() -> str:
    return '<div class="sh-skel-wrap"><div class="sh-skel" style="width:38%"></div><div class="sh-skel" style="width:92%"></div><div class="sh-skel" style="width:70%"></div></div>'


def load_error_html(message: str) -> str:
    return f'<div class="sh-banner">⚠ Couldn’t load the live status just now ({esc(message)}). The rest of this page still works.</div>'


SHELL_CSS = """
/* ---- the sidebar */
[data-testid="stSidebar"] { background: #fff; border-right: 1px solid #e5e7eb; }
[data-testid="stSidebarUserContent"] > div > [data-testid="stVerticalBlock"] { gap: 0 !important; min-height: calc(100vh - 7rem); }
[data-testid="stSidebarUserContent"] > div > [data-testid="stVerticalBlock"] > [data-testid="stLayoutWrapper"]:has([class*="st-key-nav_user"]) { margin-top: auto; }
.sh-brand { display: flex; gap: 12px; align-items: center; padding: 2px 8px 20px; }
.sh-brand-tile { width: 38px; height: 38px; border-radius: 11px; background: linear-gradient(135deg, #2563eb, #4f46e5); display: flex; align-items: center; justify-content: center; flex: none; }
.sh-brand b { display: block; font-size: 16.5px; letter-spacing: -.01em; color: #111827; }
.sh-brand span { font-size: 12px; color: #6b7280; }
[class*="st-key-nav_"] a[data-testid="stPageLink-NavLink"] { display: flex; align-items: center; gap: 12px; padding: 10px 12px; border-radius: 10px; color: #4b5563; font-weight: 600; margin-bottom: 3px; position: relative; text-decoration: none; }
[class*="st-key-nav_"] a[data-testid="stPageLink-NavLink"]:hover { background: #f3f4f6; }
[class*="st-key-nav_"][class*="__active"] a[data-testid="stPageLink-NavLink"] { background: #eaf1ff !important; color: #1d4ed8 !important; }
[class*="st-key-nav_"][class*="__active"] a[data-testid="stPageLink-NavLink"] p, [class*="st-key-nav_"][class*="__active"] a[data-testid="stPageLink-NavLink"] span { color: #1d4ed8 !important; }
[class*="st-key-nav_"][class*="__active"] a[data-testid="stPageLink-NavLink"]::before { content: ""; position: absolute; left: -16px; top: 8px; bottom: 8px; width: 4px; border-radius: 0 4px 4px 0; background: #2563eb; }
[class*="st-key-nav_user"] { border-top: 1px solid #eef0f3; padding-top: 14px; }
.sh-user { display: flex; gap: 11px; align-items: center; padding: 0 6px 12px; }
.sh-user-av { width: 36px; height: 36px; border-radius: 50%; background: #dbeafe; color: #1d4ed8; font-weight: 800; display: flex; align-items: center; justify-content: center; flex: none; }
.sh-user b { display: block; font-size: 14px; color: #111827; }
.sh-user span { font-size: 12px; color: #6b7280; }

/* ---- the Home page */
.sh-greet { font-size: 32px; font-weight: 800; letter-spacing: -.02em; color: #111827; line-height: 1.15; }
.sh-greet-sub { color: #6b7280; font-size: 14.5px; margin-top: 6px; }
.sh-section { font-size: 13px; font-weight: 700; letter-spacing: .05em; text-transform: uppercase; color: #6b7280; margin: 26px 2px 10px; }
[class*="st-key-home_att_"] { background: #fff; border: 1px solid #e5e7eb; border-left: 4px solid #f59e0b; border-radius: 14px; padding: 16px 18px 18px; gap: .5rem !important; min-height: 128px; }
[class*="st-key-home_att_unread"] { border-left-color: #2563eb; }
[class*="st-key-home_att_account"] { border-left-color: #dc2626; }
.sh-att-t { display: flex; align-items: center; gap: 8px; font-weight: 700; font-size: 15px; color: #111827; }
.sh-att-d { color: #6b7280; font-size: 13px; line-height: 1.5; margin: 6px 0 4px; }
[class*="st-key-home_att_"] a, [class*="st-key-home_guide_"] a { font-weight: 700; font-size: 13px; color: #1d4ed8; padding: 0 !important; min-height: 0 !important; gap: 0 !important; }
[class*="st-key-home_att_"] a p, [class*="st-key-home_guide_"] a p, [class*="st-key-home_att_"] a span, [class*="st-key-home_guide_"] a span { color: #1d4ed8 !important; font-weight: 700; }
[class*="st-key-home_att_"] [data-testid="stMarkdownContainer"], [class*="st-key-home_guide_"] [data-testid="stMarkdownContainer"] { margin-bottom: 0 !important; }
[class*="st-key-home_att_"] a [data-testid="stIconEmoji"], [class*="st-key-home_att_"] a [data-testid="stIconMaterial"],
[class*="st-key-home_guide_"] a [data-testid="stIconEmoji"], [class*="st-key-home_guide_"] a [data-testid="stIconMaterial"] { display: none; }
[class*="st-key-home_att_"] .stButton button { border: 0 !important; background: transparent !important; color: #1d4ed8 !important; font-weight: 700; font-size: 13px; padding: 0 !important; min-height: 0 !important; }
[class*="st-key-home_qa_row"] [data-testid="stHorizontalBlock"] { align-items: center; }
[class*="st-key-home_qa_"] a[data-testid="stPageLink-NavLink"] { border: 1px solid #d1d5db; border-radius: 8px; background: #fff; justify-content: center; min-height: 40px; font-weight: 600; color: #111827; }
[class*="st-key-home_qa_"] a[data-testid="stPageLink-NavLink"]:hover { background: #f9fafb; }
.sh-clear { display: flex; gap: 12px; align-items: center; background: #f0fbf4; border: 1px solid #bbe3c9; border-radius: 14px; padding: 16px 20px; color: #166534; }
.sh-clear b { display: block; font-size: 15px; } .sh-clear span { font-size: 13px; }
.sh-stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; }
.sh-stat { background: #fff; border: 1px solid #e5e7eb; border-radius: 14px; padding: 16px 18px; }
.sh-stat-l { font-size: 12.5px; color: #6b7280; font-weight: 600; }
.sh-stat-v { font-size: 27px; font-weight: 800; letter-spacing: -.02em; margin-top: 4px; color: #111827; }
.sh-of { font-size: 15px; color: #6b7280; font-weight: 600; letter-spacing: 0; }
.sh-stat-s { font-size: 12.5px; color: #6b7280; margin-top: 3px; }
[class*="st-key-home_run_"] { background: #fff; border: 1px solid #e5e7eb; border-left: 4px solid #22c55e; border-radius: 14px; padding: 16px 20px 18px; margin-bottom: 4px; gap: 0 !important; }
[class*="st-key-home_run_"] [data-testid="stHorizontalBlock"] { align-items: center; gap: 1rem; }
.sh-run { display: grid; grid-template-columns: minmax(200px, 1.3fr) 1.2fr 1.4fr; gap: 16px; align-items: center; }
.sh-run-name { font-weight: 700; font-size: 15.5px; color: #111827; margin-bottom: 6px; word-break: break-word; }
.sh-pill { display: inline-flex; font-size: 12.5px; font-weight: 600; border-radius: 999px; padding: 3px 11px; border: 1px solid #bbe3c9; background: #f0fbf4; color: #15803d; }
.sh-run-l { font-size: 12px; color: #6b7280; } .sh-run-v { font-weight: 700; font-size: 14px; color: #111827; }
.sh-run-t { font-size: 12.5px; color: #6b7280; } .sh-run-t b { color: #111827; }
.sh-bar { height: 8px; background: #eef0f3; border-radius: 99px; overflow: hidden; margin-top: 6px; }
.sh-bar i { display: block; height: 100%; background: #2563eb; border-radius: 99px; }
[class*="st-key-home_guide_"] { background: #fff; border: 1px solid #e5e7eb; border-radius: 14px; padding: 16px 16px 18px; gap: .5rem !important; min-height: 196px; }
.sh-gi { width: 34px; height: 34px; border-radius: 10px; background: #eef2ff; color: #1d4ed8; display: flex; align-items: center; justify-content: center; margin-bottom: 8px; }
.sh-gt { font-size: 14.5px; font-weight: 700; color: #111827; }
.sh-gd { color: #6b7280; font-size: 12.5px; line-height: 1.5; margin-bottom: 4px; }
[class*="st-key-home_guide_"] a { font-weight: 700; font-size: 13px; color: #1d4ed8; }
.sh-skel-wrap { display: flex; flex-direction: column; gap: 12px; padding: 18px 4px; }
.sh-skel { height: 14px; border-radius: 10px; background: linear-gradient(90deg, #eef0f3, #f6f7f9, #eef0f3); background-size: 200% 100%; animation: sh-shimmer 1.4s infinite; }
@keyframes sh-shimmer { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }
.sh-banner { background: #fffaf0; border: 1px solid #f3d9a8; color: #92400e; border-radius: 12px; padding: 12px 16px; font-size: 13.5px; }
"""
