"""Markup and styling of the standalone Responses page's header, its Replies menu and its filter row. Text is escaped; nothing from a Sheet is placed here."""
import html

TITLE = "Responses"
SUBTITLE = "Every reply across every campaign, in one place."
CHECK_REPLIES_HELP = "Asks every campaign's mailbox for new replies. They appear within a minute or two."
SYNC_HELP = "Saves what you have read to the Sheet, so it stays read everywhere."
NOTHING_TO_SYNC = "Nothing is waiting to be saved: your read status is up to date."


def esc(value) -> str:
    return html.escape("" if value is None else str(value).replace("\x00", "").replace("\u202e", ""), quote=True)


def title_html(title: str = TITLE, subtitle: str = SUBTITLE) -> str:
    return f'<div class="rp-title"><span class="rp-ico">💬</span><div><h1>{esc(title)}</h1><div class="rp-sub">{esc(subtitle)}</div></div></div>'


def group_label_html(text: str) -> str:
    """The small capital heading of a group inside the Replies menu ('REPLIES', 'READ STATUS')."""
    return f'<div class="rp-group">{esc(text)}</div>'


RESPONSES_PAGE_CSS = """
.rp-title { display: flex; align-items: center; gap: 14px; }
.rp-ico { width: 44px; height: 44px; border-radius: 12px; background: #eef2ff; display: inline-flex; align-items: center; justify-content: center; font-size: 22px; flex: none; }
.rp-title h1 { font-size: 30px; font-weight: 800; letter-spacing: -.02em; line-height: 1.15; margin: 0; padding: 0; color: #111827; }
.rp-sub { color: #6b7280; font-size: 14px; margin-top: 4px; }
.rp-group { font-size: 11px; letter-spacing: .07em; font-weight: 700; color: #9ca3af; text-transform: uppercase; margin: 2px 0 6px; }
[class*="st-key-rp_head"] [data-testid="stHorizontalBlock"], [class*="st-key-rp_toolbar"] [data-testid="stHorizontalBlock"] { align-items: center; gap: .75rem; }
/* the All / Unread choice is drawn as one small switch instead of two round radio buttons */
[class*="st-key-responses_inbox_chip"] [role="radiogroup"] { display: inline-flex; gap: 0; border: 1px solid #d1d5db; border-radius: 9px; overflow: hidden; background: #fff; }
[class*="st-key-responses_inbox_chip"] label[data-testid="stRadioOption"] { margin: 0; padding: 8px 14px; font-weight: 600; color: #4b5563; }
[class*="st-key-responses_inbox_chip"] label[data-testid="stRadioOption"] > div > div:first-child { display: none; }
[class*="st-key-responses_inbox_chip"] label[data-testid="stRadioOption"]:has(input:checked) { background: #eaf1ff; color: #1d4ed8; }
"""
