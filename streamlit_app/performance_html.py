"""Markup for the Performance display: cards, tables, bars and an SVG activity chart, in the Inbox's visual style.

PURE. Every function takes plain values already computed by performance_logic and returns one string; nothing here
reads a Sheet, computes a figure or touches Streamlit, so the numbers on screen are exactly the numbers it was given.

SAFETY. Several values come from outside the app — sender accounts, template variant names, sending-error text — so
every piece of text goes through html.escape, the markup uses no scripts, no external addresses and no event handlers,
and each builder returns ONE line (a blank line would end an HTML block in Markdown and let the rest be read as
Markdown).
"""
import html
import math
import re
from datetime import date
from typing import Dict, Iterable, Optional, Sequence, Tuple

CHART_WIDTH, CHART_HEIGHT, CHART_PAD_X, CHART_PAD_TOP, CHART_PAD_BOTTOM = 760, 240, 38, 14, 30
SENT_COLOUR, REPLIES_COLOUR = "#2563eb", "#16a34a"
INTENT_COLOURS = {"Interested": "#16a34a", "Lead / Needs Follow-up": "#2563eb", "Not Interested": "#dc2626",
                  "Unclear": "#9ca3af", "Not classified": "#d1d5db"}
DEFAULT_COLOUR = "#9ca3af"
_CONTROL_AND_BIDI = re.compile(r"[\x00-\x1f\x7f\u200e\u200f\u202a-\u202e\u2066-\u2069]")


def esc(value) -> str:
    """Text made safe for HTML: control and text-direction characters removed (so a name cannot reorder what is
    around it), line breaks flattened, then escaped."""
    return html.escape(_CONTROL_AND_BIDI.sub(" ", str(value if value is not None else "")).strip(), quote=True)


def _one_line(markup: str) -> str:
    return re.sub(r"\s*\n\s*", "", markup)


# ---------------------------------------------------------------- the stylesheet

PERFORMANCE_CSS = """
.pf-grid { display: grid; gap: 14px; margin: 0 0 14px; }
.pf-g6 { grid-template-columns: repeat(6, minmax(0, 1fr)); } .pf-g4 { grid-template-columns: repeat(4, minmax(0, 1fr)); }
.pf-g3 { grid-template-columns: repeat(3, minmax(0, 1fr)); } .pf-g2 { grid-template-columns: minmax(0, 1.6fr) minmax(0, 1fr); }
.pf-g2e { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
@media (max-width: 1200px) { .pf-g6 { grid-template-columns: repeat(3, minmax(0, 1fr)); } .pf-g4 { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 900px) { .pf-g2, .pf-g2e { grid-template-columns: minmax(0, 1fr); } .pf-g6 { grid-template-columns: repeat(2, minmax(0, 1fr)); } .pf-g3 { grid-template-columns: minmax(0, 1fr); } }
.pf-kpi { background: #fff; border: 1px solid #e5e7eb; border-radius: 14px; padding: 15px 17px; }
.pf-kpi-label { font-size: 12.5px; color: #6b7280; font-weight: 600; }
.pf-kpi-value { font-size: 27px; font-weight: 700; margin: 5px 0 2px; letter-spacing: -.02em; color: #111827; line-height: 1.15; }
.pf-kpi-caption { font-size: 12px; color: #6b7280; min-height: 1.2em; }
.pf-card { background: #fff; border: 1px solid #e5e7eb; border-radius: 14px; padding: 18px 20px; min-width: 0; }
.pf-card-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.pf-card-title { margin: 0 !important; padding: 0 !important; font-size: 16px !important; line-height: 1.3 !important; font-weight: 700 !important; color: #111827; }
.pf-card-hint { color: #6b7280; font-size: 13px; margin: 3px 0 12px; }
.pf-spacer { flex: 1; }
.pf-chip { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; font-weight: 600; border-radius: 999px; padding: 3px 10px; border: 1px solid #d1d5db; background: #fff; color: #374151; white-space: nowrap; }
.pf-chip-sent { color: #1d4ed8; border-color: #c7d7fb; background: #f3f7ff; } .pf-chip-replies { color: #15803d; border-color: #bbe3c9; background: #f0fbf4; }
/* Streamlit draws a grid border around every table cell by default; these keep only the thin horizontal lines */
.pf-table { width: 100%; border-collapse: collapse; border: 0 !important; margin: 0 !important; font-size: 13.5px; }
.pf-table th, .pf-table td { border-left: 0 !important; border-right: 0 !important; border-top: 0 !important; background: transparent !important; }
.pf-table th { text-align: left; color: #6b7280; font-weight: 600; font-size: 12.5px; text-transform: none; letter-spacing: 0; padding: 8px 10px; border-bottom: 1px solid #e5e7eb !important; white-space: nowrap; }
.pf-table td { padding: 11px 10px; border-bottom: 1px solid #f0f2f5 !important; color: #111827 !important; overflow-wrap: anywhere; }       /* (the theme colour otherwise leaks into some cells) */
.pf-table tr:last-child td { border-bottom: 0 !important; } .pf-table td:first-child { font-weight: 600; }
.pf-scroll { overflow-x: auto; }
.pf-cellbar { display: flex; align-items: center; gap: 10px; }
.pf-minibar { display: inline-block; width: 90px; height: 8px; border-radius: 99px; background: #eef0f4; overflow: hidden; flex: none; }
.pf-minibar i { display: block; height: 100%; border-radius: 99px; background: #2563eb; }
.pf-bar-row { display: flex; align-items: center; gap: 12px; margin: 10px 0; font-size: 13px; }
.pf-bar-label { width: 150px; flex: none; color: #111827; } .pf-bar-track { flex: 1; height: 8px; border-radius: 99px; background: #eef0f4; overflow: hidden; }
.pf-bar-fill { display: block; height: 100%; border-radius: 99px; } .pf-bar-count { width: 34px; text-align: right; font-weight: 700; flex: none; }
.pf-callout { background: linear-gradient(180deg, #f3f7ff, #fff); border: 1px solid #c7d7fb; border-radius: 14px; padding: 16px 20px; margin: 18px 0 14px; }     /* the top margin: Streamlit's markdown wrapper would otherwise pull it up against the card above */
.pf-callout-title { font-weight: 700; font-size: 15.5px; color: #111827; margin-bottom: 8px; }
.pf-callout-line { color: #374151; font-size: 13.5px; line-height: 1.6; margin: 4px 0; }
.pf-note { color: #6b7280; font-size: 12.5px; margin-top: 10px; }
.pf-chart { width: 100%; height: auto; display: block; }
"""


# ---------------------------------------------------------------- headline cards

def kpi_html(label, value, caption="", help_text="") -> str:
    """One headline figure. `help_text` becomes the card's tooltip."""
    tip = f' title="{esc(help_text)}"' if help_text else ""
    return _one_line(f'<div class="pf-kpi"{tip}><div class="pf-kpi-label">{esc(label)}</div>'
                     f'<div class="pf-kpi-value">{esc(value)}</div><div class="pf-kpi-caption">{esc(caption)}</div></div>')


def grid_html(columns: str, cells: Iterable[str]) -> str:
    """Cells (already-built markup) laid out in a grid. `columns` is one of g6, g4, g3, g2, g2e."""
    if columns not in ("g6", "g4", "g3", "g2", "g2e"):
        raise ValueError(f"unknown grid {columns!r}")
    return _one_line(f'<div class="pf-grid pf-{columns}">{"".join(cells)}</div>')


def card_html(title: str, hint: str = "", inner: str = "", chips: Sequence[Tuple[str, str]] = ()) -> str:
    """A white card with a title, an optional hint line, optional chips (text, 'sent' | 'replies' | '') at the right,
    and `inner` (already-built markup)."""
    chip_html = "".join(f'<span class="pf-chip{" pf-chip-" + kind if kind in ("sent", "replies") else ""}">{esc(text)}</span>'
                        for text, kind in chips)
    head = f'<div class="pf-card-head"><h3 class="pf-card-title">{esc(title)}</h3><span class="pf-spacer"></span>{chip_html}</div>'
    hint_html = f'<div class="pf-card-hint">{esc(hint)}</div>' if hint else ""
    return _one_line(f'<div class="pf-card">{head}{hint_html}{inner}</div>')


# ---------------------------------------------------------------- tables and bars

def bar_cell(fraction, text) -> tuple:
    """A table cell that shows `text` with a small bar in front of it. `fraction` (0 to 1, relative to the biggest in the column) sets the
    bar's length; anything that is not a number between 0 and 1 draws an empty bar."""
    return ("bar", fraction, text)


def _cell_html(cell) -> str:
    if isinstance(cell, tuple) and len(cell) == 3 and cell[0] == "bar":
        try:
            width = max(0, min(100, round(float(cell[1]) * 100)))
        except (TypeError, ValueError):
            width = 0
        return f'<div class="pf-cellbar"><span class="pf-minibar"><i style="width:{width}%"></i></span><span>{esc(cell[2])}</span></div>'
    return esc(cell)


def table_html(headers: Sequence[str], rows: Sequence[Sequence]) -> str:
    """A table. Every cell is text and is escaped (or a bar_cell); a row shorter than the header is padded rather than dropped."""
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{_cell_html(row[i]) if i < len(row) else ''}</td>" for i in range(len(headers))) + "</tr>" for row in rows)
    return _one_line(f'<div class="pf-scroll"><table class="pf-table"><tr>{head}</tr>{body}</table></div>')


def bars_html(rows: Sequence[Tuple[str, int]], colours: Optional[Dict[str, str]] = None) -> str:
    """Horizontal bars, one per (label, count), each scaled against the largest count (a zero stays empty)."""
    colours = colours or {}
    biggest = max([count for _, count in rows] + [0])
    out = []
    for label, count in rows:
        width = round(count / biggest * 100) if biggest > 0 and count > 0 else 0
        out.append(f'<div class="pf-bar-row"><div class="pf-bar-label">{esc(label)}</div><div class="pf-bar-track">'
                   f'<i class="pf-bar-fill" style="width:{width}%;background:{colours.get(label, DEFAULT_COLOUR)}"></i></div>'
                   f'<div class="pf-bar-count">{esc(count)}</div></div>')
    return _one_line("".join(out))


def note_html(text: str) -> str:
    return f'<div class="pf-note">{esc(text)}</div>'


def callout_html(title: str, lines: Sequence[Tuple[str, str]], footnote: str = "") -> str:
    """The 'what worked best' box: each line is a (bold label, plain text) pair."""
    body = "".join(f'<div class="pf-callout-line"><b>{esc(label)}</b> {esc(text)}</div>' for label, text in lines)
    foot = note_html(footnote) if footnote else ""
    return _one_line(f'<div class="pf-callout"><div class="pf-callout-title">🏆 {esc(title)}</div>{body}{foot}</div>')


# ---------------------------------------------------------------- the activity chart

def nice_ceiling(value: float) -> Tuple[float, float]:
    """(step, top) for a y axis of four intervals that covers `value`. Counts are whole numbers, so the step is never
    below 1; it is 1, 2 or 5 times a power of ten (and 2.5 times from 10 up, which keeps a peak of 82 on a 0-100 axis
    instead of 0-200)."""
    if not value or value <= 0 or math.isnan(value) or math.isinf(value):
        return 1.0, 4.0
    raw = max(value / 4, 1.0)
    exponent = 10 ** math.floor(math.log10(raw))
    for multiple in ((1, 2, 2.5, 5, 10) if exponent >= 10 else (1, 2, 5, 10)):
        if raw <= multiple * exponent:
            step = multiple * exponent
            return float(step), float(step * 4)
    return float(10 * exponent), float(40 * exponent)


def _label_for(day) -> str:
    try:
        return date.fromisoformat(str(day)).strftime("%b %d").replace(" 0", " ")
    except ValueError:
        return str(day)


def area_chart_svg(series: Sequence[Dict]) -> str:
    """Emails sent (blue, filled) and replies (green) per day, from performance_logic.daily_series' rows
    ({date, Sent, Replies}). An empty series gives an empty string; a single day draws two dots."""
    if not series:
        return ""
    sent = [max(0, int(row.get("Sent") or 0)) for row in series]
    replies = [max(0, int(row.get("Replies") or 0)) for row in series]
    step, top = nice_ceiling(max(sent + replies + [0]))
    inner_w, inner_h = CHART_WIDTH - 2 * CHART_PAD_X, CHART_HEIGHT - CHART_PAD_TOP - CHART_PAD_BOTTOM
    base = CHART_HEIGHT - CHART_PAD_BOTTOM
    count = len(series)

    def x_at(i):
        return CHART_PAD_X + (inner_w / 2 if count == 1 else i * inner_w / (count - 1))

    def y_at(v):
        return base - v / top * inner_h

    grid = ""
    for k in range(5):
        y = base - k * inner_h / 4
        value = k * step
        grid += (f'<line x1="{CHART_PAD_X}" x2="{CHART_WIDTH - CHART_PAD_X}" y1="{y:.1f}" y2="{y:.1f}" stroke="#eef0f4"/>'
                 f'<text x="{CHART_PAD_X - 8}" y="{y + 4:.1f}" font-size="9.5" fill="#9ca3af" text-anchor="end">{value:g}</text>')
    picks = sorted({0, count // 4, count // 2, (3 * count) // 4, count - 1})
    labels = "".join(f'<text x="{x_at(i):.1f}" y="{CHART_HEIGHT - 8}" font-size="9.5" fill="#9ca3af" text-anchor="middle">{esc(_label_for(series[i].get("date")))}</text>' for i in picks)

    def line(values):
        return "M" + " L".join(f"{x_at(i):.1f},{y_at(v):.1f}" for i, v in enumerate(values))

    if count == 1:
        shapes = (f'<circle cx="{x_at(0):.1f}" cy="{y_at(sent[0]):.1f}" r="4" fill="{SENT_COLOUR}"/>'
                  f'<circle cx="{x_at(0):.1f}" cy="{y_at(replies[0]):.1f}" r="4" fill="{REPLIES_COLOUR}"/>')
    else:
        area = f'{line(sent)} L{x_at(count - 1):.1f},{base} L{x_at(0):.1f},{base} Z'
        shapes = (f'<path d="{area}" fill="url(#pf-sent-fill)"/><path d="{line(sent)}" fill="none" stroke="{SENT_COLOUR}" stroke-width="2.4" stroke-linejoin="round"/>'
                  f'<path d="{line(replies)}" fill="none" stroke="{REPLIES_COLOUR}" stroke-width="2.4" stroke-linejoin="round"/>')
    summary = f"Emails sent and replies per day over {count} day{'s' if count != 1 else ''}; most in one day: {max(sent + replies)}."
    return _one_line(
        f'<svg class="pf-chart" viewBox="0 0 {CHART_WIDTH} {CHART_HEIGHT}" role="img" aria-label="{esc(summary)}">'
        f'<defs><linearGradient id="pf-sent-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{SENT_COLOUR}" stop-opacity=".22"/>'
        f'<stop offset="1" stop-color="{SENT_COLOUR}" stop-opacity="0"/></linearGradient></defs>{grid}{labels}{shapes}</svg>')
