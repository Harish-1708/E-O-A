"""Markup and styling for the campaign page's frame: the header chips, the sub-line, the tab bar, buttons, inputs, filter chips and
expanders — so the whole page, not only the cards inside the tabs, has one look.

PURE. Every piece of text goes through performance_html.esc (a campaign's name, schedule or sender account are data), and every
builder returns one line. The stylesheet restyles Streamlit's own elements by their stable attributes (role, data-testid, the
keys given to containers) rather than by its generated class names, so it keeps working when Streamlit changes its internals.
"""
from typing import Iterable

from performance_html import esc, _one_line

UI_BUILD = "campaign-frame-3"        # shown at the foot of the campaign page, so it is always clear which version of this layout is live

STATUS_KIND = {"running": "good", "paused": "waiting", "attention": "bad", "deleted": "bad", "draft": "neutral", "completed": "neutral"}


def chips_html(status: str, items: Iterable[str]) -> str:
    """The chips beside the title (schedule, senders). The wrapper carries the status colour family so the status pill, which is
    Streamlit's own heading, can be coloured to match (see the stylesheet)."""
    kind = STATUS_KIND.get(status, "neutral")
    return _one_line(f'<div class="cp-chips cp-s-{kind}">' + "".join(f'<span class="cp-chip">{esc(text)}</span>' for text in items if str(text or "").strip()) + "</div>")


def crumb_html(campaign_name: str) -> str:
    """The campaign's name after the 'Campaigns' link in the breadcrumb: '/ Kelson_Creators_Licensing'."""
    return f'<span class="cp-crumb-name">/ {esc(campaign_name)}</span>'


def build_html(build: str) -> str:
    return f'<div class="cp-build">UI build {esc(build)}</div>'


def subline_html(text: str) -> str:
    return f'<div class="cp-subline">{esc(text)}</div>'


CAMPAIGN_SKIN_CSS = """
/* ---- the frame of the page: full width with the design's margins */
[data-testid="stMainBlockContainer"]:has([class*="st-key-cp_titlebar"]) { max-width: 100% !important; padding: 3.4rem 2.2rem 4rem !important; }
[data-testid="stMainBlockContainer"]:has([class*="st-key-cp_titlebar"]) [data-testid="stVerticalBlock"] { gap: 0.9rem; }

/* ---- the page font: the design's system sans-serif stack, on text only (icon fonts are left alone) */
.stApp { --cp-font: Inter, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; font-family: var(--cp-font); }
.stApp [data-testid="stMarkdownContainer"], .stApp [data-testid="stWidgetLabel"], .stApp [data-testid="stCaptionContainer"], .stApp button, .stApp input, .stApp textarea,
.stApp [role="tab"], .stApp [role="tab"] p, .stApp h1, .stApp h2, .stApp h3, .stApp summary, .stApp [data-testid="stTable"], .stApp [data-testid="stAlertContainer"] { font-family: var(--cp-font) !important; }

/* ---- breadcrumb row: Back as a link, Refresh as a small outline button */
[class*="st-key-cp_crumb"] { flex-direction: row !important; align-items: center; justify-content: flex-start; gap: 8px !important; }
[class*="st-key-cp_crumb"] > div { width: auto !important; flex: 0 0 auto; }
[class*="st-key-cp_back"] button { border: 0 !important; background: transparent !important; color: #2563eb !important; font-weight: 600; padding: 0 !important; min-height: 0 !important; box-shadow: none !important; }
[class*="st-key-cp_back"] button:hover { text-decoration: underline; }
.cp-crumb-name { color: #6b7280; font-size: 13px; }

/* ---- the header's ... menu: labelled groups, each action a full-width item with a short line of explanation under it */
.cp-menu-label { font-size: 11.5px; font-weight: 700; letter-spacing: .05em; text-transform: uppercase; color: #6b7280; margin: 2px 0 2px; }
[data-testid="stPopoverBody"]:has([class*="st-key-cp_menu_"]) { min-width: 340px; }
[class*="st-key-cp_menu_"] button { justify-content: flex-start; }
[class*="st-key-cp_menu_"] button > div { width: 100%; }
[class*="st-key-cp_menu_"] button p { text-align: left; }
[class*="st-key-cp_menu_"] [data-testid="stCaptionContainer"] { margin-top: -2px; }
[data-testid="stPopoverBody"]:has([class*="st-key-cp_menu_"]) hr { margin: 4px 0 !important; }
.cp-build { color: #9ca3af; font-size: 11px; margin: 28px 0 0; text-align: right; }
[class*="st-key-cp_actions"] { flex-direction: row !important; align-items: center; gap: 10px !important; }
[class*="st-key-cp_actions"] > div { width: auto !important; flex: 0 0 auto; }
[class*="st-key-cp_actions"] [class*="st-key-cp_send"] button { background: #2563eb !important; border-color: #2563eb !important; color: #fff !important; }
[class*="st-key-cp_actions"] [class*="st-key-cp_send"] button:hover { background: #1d4ed8 !important; border-color: #1d4ed8 !important; }
[class*="st-key-cp_refresh"] button { border-radius: 9px !important; font-weight: 600; border: 1px solid #d1d5db !important; background: #fff !important; color: #374151 !important; }

/* ---- the title bar: name, status pill, chips and the action button in one row */
[class*="st-key-cp_titlebar"] { flex-direction: row !important; flex-wrap: wrap; align-items: center; gap: 10px 14px !important; }
[class*="st-key-cp_titlebar"] > div { width: auto !important; flex: 0 0 auto; }
[class*="st-key-cp_titlebar"] h1 { font-size: 26px !important; letter-spacing: -.01em; line-height: 1.25 !important; padding: 0 !important; margin: 0 !important; }
[class*="st-key-cp_titlebar"] h3 { font-size: 12.5px !important; font-weight: 600 !important; line-height: 1.4 !important; padding: 4px 11px !important; margin: 0 !important;
  border: 1px solid #d1d5db; border-radius: 999px; background: #fff; color: #374151; white-space: nowrap; }
[class*="st-key-cp_titlebar"]:has(.cp-s-good) h3 { color: #15803d; border-color: #bbe3c9; background: #f0fbf4; }
[class*="st-key-cp_titlebar"]:has(.cp-s-waiting) h3 { color: #b45309; border-color: #f3d9a8; background: #fffaf0; }
[class*="st-key-cp_titlebar"]:has(.cp-s-bad) h3 { color: #b91c1c; border-color: #f3c4c4; background: #fff5f5; }
.cp-chips { display: flex; flex-wrap: wrap; gap: 10px; }
.cp-chip { display: inline-block; font-size: 12.5px; font-weight: 600; line-height: 1.4; border-radius: 999px; padding: 4px 11px; border: 1px solid #d1d5db; background: #fff; color: #374151; white-space: nowrap; }
[class*="st-key-cp_titlebar"] > div:last-child { margin-left: auto; }
[class*="st-key-cp_actions"] button { border-radius: 9px !important; font-weight: 600; padding: 8px 15px; }
.cp-subline { color: #6b7280; font-size: 13.5px; margin: -4px 0 0; }

/* ---- the tab bar */
[role="tablist"] { gap: 4px; border-bottom: 1px solid #e5e7eb; }
[role="tab"] { padding: 11px 18px !important; height: auto !important; }
[role="tab"] p, [role="tab"] span { font-size: 14.5px !important; font-weight: 600 !important; }
[role="tab"][aria-selected="true"] { box-shadow: inset 0 -3px 0 #2563eb; }

/* ---- inputs and dropdowns: white with a thin border (Streamlit's default is a grey fill) */
[data-testid="stTextInputRootElement"], [data-testid="stSelectbox"] [role="group"], [data-testid="stNumberInputContainer"] { background: #fff !important; border: 1px solid #d1d5db !important; border-radius: 9px !important; }
[data-testid="stTextInputRootElement"] input, [data-testid="stSelectbox"] input { background: transparent !important; }
[data-testid="stTextArea"] textarea { background: #fff !important; border: 1px solid #d1d5db !important; border-radius: 9px !important; }

/* ---- expanders as slim cards */
[data-testid="stExpander"] { background: #fff; border: 1px solid #e5e7eb !important; border-radius: 12px !important; }
[data-testid="stExpander"] summary { font-size: 13.5px; font-weight: 600; }

/* ---- this page's own buttons (scoped: the Inbox on the Responses tab styles its own) */
[class*="st-key-cp_"] button, [class*="st-key-data_prev"] button, [class*="st-key-data_next"] button, [class*="st-key-data_filters_clear"] button {
  border-radius: 9px !important; font-weight: 600 !important; border: 1px solid #d1d5db !important; background: #fff !important; color: #374151 !important; }
[class*="st-key-cp_back"] button { border: 0 !important; background: transparent !important; color: #2563eb !important; padding: 0 !important; min-height: 0 !important; box-shadow: none !important; }
[class*="st-key-cp_actions"] button[kind="primary"], [class*="st-key-cp_actions"] [data-testid="stBaseButton-primary"] { background: #2563eb !important; border-color: #2563eb !important; color: #fff !important; }
[class*="st-key-data_prev"] button:disabled, [class*="st-key-data_next"] button:disabled { opacity: .45; }

/* ---- "+ Add leads" is the toolbar's one primary action; closing the import panel is a plain outline button */
[class*="st-key-ld_add_leads"] button { background: #2563eb !important; border: 1px solid #2563eb !important; color: #fff !important; border-radius: 9px !important; font-weight: 600 !important; }
[class*="st-key-ld_add_leads"] button:hover { background: #1d4ed8 !important; border-color: #1d4ed8 !important; }
[class*="st-key-ld_import_close"] button { border-radius: 9px !important; font-weight: 600 !important; border: 1px solid #d1d5db !important; background: #fff !important; color: #374151 !important; }

/* ---- the Data toolbar: search, status chips and the three filters in one card */
[class*="st-key-ld_toolbar"] { background: #fff; border: 1px solid #e5e7eb; border-radius: 14px; padding: 16px 18px; }

/* ---- status chips (the Data filter) */
[class*="st-key-data_filter"] [role="radiogroup"] { gap: 8px; flex-wrap: wrap; }
[class*="st-key-data_filter"] label[data-testid="stRadioOption"] { border: 1px solid #d1d5db; border-radius: 999px; padding: 3px 13px; background: #fff; margin: 0; cursor: pointer; }
[class*="st-key-data_filter"] label[data-testid="stRadioOption"] > div > div:first-child { display: none; }
[class*="st-key-data_filter"] label[data-testid="stRadioOption"]:has(input:checked) { background: #dde9fb; border-color: #bcd0f5; }
[class*="st-key-data_filter"] label[data-testid="stRadioOption"]:has(input:checked) p { color: #1d4ed8; font-weight: 700; }
[class*="st-key-data_filter"] label[data-testid="stRadioOption"] p { font-size: 12.5px; font-weight: 600; }

/* ---- the Performance toolbar: the range control at the left, Refresh and Export at the right */
[class*="st-key-pf_toolbar_"] { flex-direction: row !important; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px !important; }
[class*="st-key-pf_toolbar_"] > div { width: auto !important; flex: 0 0 auto; }
[class*="st-key-pf_tools_"] { flex-direction: row !important; align-items: center; gap: 10px !important; }
[class*="st-key-pf_tools_"] > div { width: auto !important; flex: 0 0 auto; }
[class*="st-key-pf_tools_"] button, [class*="st-key-pf_tools_"] a { border-radius: 9px !important; font-weight: 600 !important; border: 1px solid #d1d5db !important; background: #fff !important; color: #374151 !important; }

/* ---- the chart range as one connected segmented control */
[class*="st-key-"][class*="_range"] [role="radiogroup"] { display: inline-flex; gap: 0; border: 1px solid #d1d5db; border-radius: 9px; overflow: hidden; background: #fff; }
[class*="st-key-"][class*="_range"] label[data-testid="stRadioOption"] { margin: 0; padding: 7px 16px; border-right: 1px solid #e5e7eb; background: #fff; cursor: pointer; }
[class*="st-key-"][class*="_range"] label[data-testid="stRadioOption"]:last-child { border-right: 0; }
[class*="st-key-"][class*="_range"] label[data-testid="stRadioOption"] > div > div:first-child { display: none; }
[class*="st-key-"][class*="_range"] label[data-testid="stRadioOption"]:has(input:checked) { background: #dde9fb; }
[class*="st-key-"][class*="_range"] label[data-testid="stRadioOption"]:has(input:checked) p { color: #1d4ed8; }
[class*="st-key-"][class*="_range"] label[data-testid="stRadioOption"] p { font-size: 13px; font-weight: 600; }
"""
