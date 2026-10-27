"""Markup for the Data tab's lead table and lead panel, in the Inbox's visual style.

PURE: it takes the plain dicts leads_logic builds and returns strings. Names, emails, companies, sender accounts and every
custom field come from a CSV someone uploaded, so ALL text goes through performance_html.esc (html.escape plus removal of
control and text-direction characters), there are no scripts, no external addresses and no event handlers, and every builder
returns ONE line (a blank line would end an HTML block in Markdown).
"""
from typing import Dict, Iterable, Sequence, Tuple

from performance_html import esc, _one_line

KIND_CLASS = {"good": "ld-pill-good", "active": "ld-pill-active", "bad": "ld-pill-bad", "waiting": "ld-pill-waiting", "neutral": "ld-pill-neutral"}
DOT_CLASS = {"sent": "ld-dot-sent", "replied": "ld-dot-replied", "pending": ""}

LEADS_CSS = """
.ld-grid { display: grid; grid-template-columns: minmax(0, 1.9fr) minmax(0, 1.9fr) minmax(0, 1.5fr) minmax(0, 1.5fr) minmax(0, .85fr) minmax(0, 1.5fr); gap: 14px; align-items: center; padding: 0 16px; }
.ld-head { padding-top: 11px; padding-bottom: 9px; border-bottom: 1px solid #e5e7eb; color: #6b7280; font-size: 12.5px; font-weight: 600; }
.ld-row { min-height: 68px; box-sizing: border-box; padding-top: 14px; padding-bottom: 14px; border-bottom: 1px solid #f0f2f5; font-size: 13.5px; line-height: 1.4; color: #111827; background: #fff; }
.ld-row.ld-selected { background: #dde9fb; }
.ld-cell { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; line-height: 1.4; }
.ld-name { font-weight: 700; } .ld-sub { color: #6b7280; font-size: 12px; margin-top: 1px; } .ld-muted { color: #6b7280; }
.ld-pill { display: inline-block; max-width: 100%; overflow: hidden; text-overflow: ellipsis; vertical-align: middle; box-sizing: border-box; line-height: 1.4; font-size: 12px; font-weight: 600; border-radius: 8px; padding: 3px 9px; border: 1px solid #d1d5db; background: #fff; color: #374151; white-space: nowrap; }
.ld-pill-good { color: #15803d; border-color: #bbe3c9; background: #f0fbf4; } .ld-pill-active { color: #1d4ed8; border-color: #c7d7fb; background: #f3f7ff; }
.ld-pill-bad { color: #b91c1c; border-color: #f3c4c4; background: #fff5f5; } .ld-pill-waiting { color: #b45309; border-color: #f3d9a8; background: #fffaf0; }
.ld-pill-neutral { color: #4b5563; background: #f9fafb; }
.ld-dots { display: flex; flex-wrap: wrap; gap: 3px; row-gap: 4px; align-items: center; }       /* 11 dots (the longest sequence) need 129px */ .ld-dot { width: 9px; height: 9px; border-radius: 50%; background: #e5e7eb; display: inline-block; }
.ld-dot-sent { background: #2563eb; } .ld-dot-replied { background: #16a34a; }
.ld-empty { padding: 28px 16px; text-align: center; color: #6b7280; font-size: 14px; }
.ld-panel { background: #fff; border: 1px solid #e5e7eb; border-radius: 14px; padding: 18px 20px; }
.ld-panel-name { font-size: 18px; font-weight: 700; color: #111827; margin: 0 0 4px; overflow-wrap: anywhere; }
.ld-panel-sub { color: #6b7280; font-size: 13px; overflow-wrap: anywhere; margin-bottom: 12px; }
.ld-facts { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 7px 14px; font-size: 13px; margin: 14px 0 6px; }
.ld-fact-label { color: #6b7280; font-weight: 600; } .ld-fact-value { color: #111827; overflow-wrap: anywhere; }
.ld-panel-title { font-size: 12.5px; font-weight: 700; color: #374151; margin: 18px 0 8px; }
.ld-timeline { border-left: 2px solid #dbe3f0; padding-left: 14px; margin-left: 4px; }
.ld-event { font-size: 13px; line-height: 1.5; margin: 0 0 8px; color: #111827; overflow-wrap: anywhere; } .ld-event-when { color: #6b7280; }
.ld-hint { color: #6b7280; font-size: 12.5px; margin-top: 14px; }

/* ---- the table is one bordered card; each row is one click target (the real Streamlit button sits invisibly on top of it) */
[class*="st-key-ld_table_"] { border: 1px solid #e5e7eb; border-radius: 14px; background: #fff; overflow: hidden; gap: 0 !important; }
/* Streamlit gives its Markdown wrapper a negative bottom margin (to cancel paragraph spacing), which shrank each row's container,
   and so its click target, by 16px: rows overlapped the next one and a click on the lower part of a row opened its neighbour.
   The margin is removed here, and the container also carries the row's own height so it holds even if that wrapper changes. */
[class*="st-key-ld_row_"] { position: relative; gap: 0 !important; cursor: pointer; min-height: 68px; }
[class*="st-key-ld_row_"] [data-testid="stMarkdownContainer"], [class*="st-key-ld_table_"] [data-testid="stMarkdownContainer"],
[class*="st-key-ld_panel_"] [data-testid="stMarkdownContainer"] { margin-bottom: 0 !important; }

/* ---- the controls for the open lead: their own card under its details, with a blue Save and clearly dangerous removal buttons */
[class*="st-key-ld_actions_"] { background: #fff; border: 1px solid #e5e7eb; border-radius: 14px; padding: 16px 20px; margin-top: 14px; }
[class*="st-key-manage_save_"] button { background: #2563eb !important; border: 1px solid #2563eb !important; color: #fff !important; border-radius: 8px; font-weight: 600; }
[class*="st-key-manage_save_"] button:hover { background: #1d4ed8 !important; border-color: #1d4ed8 !important; }
[class*="st-key-remove_ask_"] button { background: #fff !important; border: 1px solid #f3c4c4 !important; color: #b91c1c !important; border-radius: 8px; font-weight: 600; }
[class*="st-key-remove_ask_"] button:hover { background: #fff5f5 !important; border-color: #e9a3a3 !important; color: #991b1b !important; }
[class*="st-key-remove_yes_"] button { background: #dc2626 !important; border: 1px solid #dc2626 !important; color: #fff !important; border-radius: 8px; font-weight: 600; }
[class*="st-key-remove_yes_"] button:hover { background: #b91c1c !important; border-color: #b91c1c !important; }
[class*="st-key-ld_row_"] [class*="_open_"] { position: absolute !important; inset: 0; width: 100% !important; height: 100% !important; margin: 0 !important; z-index: 3; }
/* the button's own wrapper must also fill the row, or only its top 40px would respond to a click */
[class*="st-key-ld_row_"] [class*="_open_"] > div { width: 100% !important; height: 100% !important; }
[class*="st-key-ld_row_"] [class*="_open_"] button { width: 100%; height: 100%; opacity: 0; cursor: pointer; border: 0; background: transparent; }
[class*="st-key-ld_row_"]:hover .ld-row:not(.ld-selected) { background: #f3f6fb; }
@media (max-width: 900px) { .ld-grid { grid-template-columns: minmax(0, 1.6fr) minmax(0, 1.4fr) minmax(0, 1fr) minmax(0, 1fr); } .ld-hide-small { display: none; } }
"""


def _classes(*names: str) -> str:
    return " ".join(name for name in names if name)


def status_pill_html(label: str, kind: str) -> str:
    """The status as a coloured pill; a colour family it does not know gets the plain pill (the class always comes from the table)."""
    return f'<span class="{_classes("ld-pill", KIND_CLASS.get(kind, ""))}">{esc(label)}</span>'


def dots_html(progress: Iterable[str]) -> str:
    """One dot per stage: blue = sent, green = the stage the reply answered, grey = not yet."""
    return '<div class="ld-dots">' + "".join(f'<i class="{_classes("ld-dot", DOT_CLASS.get(state, ""))}"></i>' for state in progress) + "</div>"


def head_html() -> str:
    cells = [("Lead", ""), ("Email", ""), ("Status", ""), ("Sequence", ""), ("Last sent", " ld-hide-small"), ("Sender", " ld-hide-small")]
    return _one_line('<div class="ld-grid ld-head">' + "".join(f'<div class="ld-cell{extra}">{esc(label)}</div>' for label, extra in cells) + "</div>")


def row_html(row: Dict, selected: bool = False) -> str:
    company = f'<div class="ld-sub ld-cell">{esc(row["company"])}</div>' if row.get("company") else ""
    return _one_line(
        f'<div class="ld-grid ld-row{" ld-selected" if selected else ""}">'
        f'<div class="ld-cell"><div class="ld-name ld-cell">{esc(row["name"])}</div>{company}</div>'
        f'<div class="ld-cell ld-muted">{esc(row["email"])}</div>'
        f'<div class="ld-cell">{status_pill_html(row["status"], row["status_kind"])}</div>'
        f'<div class="ld-cell">{dots_html(row["progress"])}</div>'
        f'<div class="ld-cell ld-hide-small">{esc(row["last_sent"]) or "—"}</div>'
        f'<div class="ld-cell ld-muted ld-hide-small">{esc(row["sender"]) or "—"}</div></div>')


def empty_table_html(message: str) -> str:
    return f'<div class="ld-empty">{esc(message)}</div>'


def panel_html(row: Dict, facts: Sequence[Tuple[str, str]], events: Sequence[Dict], hint: str = "") -> str:
    """The side panel for one lead: name, address, status, a few facts, and the timeline."""
    fact_html = "".join(f'<div class="ld-fact-label">{esc(label)}</div><div class="ld-fact-value">{esc(value) or "—"}</div>' for label, value in facts)
    if events:
        lines = "".join(f'<div class="ld-event">{esc(e["icon"])} {esc(e["text"])}' + (f' <span class="ld-event-when">· {esc(e["when"])}</span>' if e.get("when") else "") + "</div>" for e in events)
        timeline = f'<div class="ld-panel-title">Timeline</div><div class="ld-timeline">{lines}</div>'
    else:
        timeline = '<div class="ld-panel-title">Timeline</div><div class="ld-hint">Nothing has been sent to this lead yet.</div>'
    hint_html = f'<div class="ld-hint">{esc(hint)}</div>' if hint else ""
    return _one_line(
        f'<div class="ld-panel"><div class="ld-panel-name">{esc(row["name"])}</div><div class="ld-panel-sub">{esc(row["email"])}</div>'
        f'{status_pill_html(row["status"], row["status_kind"])}<div class="ld-facts">{fact_html}</div>{timeline}{hint_html}</div>')


def empty_panel_html() -> str:
    return '<div class="ld-panel"><div class="ld-empty">Select a lead in the table to see its details and timeline.</div></div>'
