"""Markup and styling for the Schedule tab: the form card's heading, the hour strip, the follow-up waits table, and the three cards on the right
(next automatic run, today, this week).

PURE. Everything that comes from a campaign or the Sheet goes through performance_html.esc, and every builder returns one line.
"""
from typing import Dict, List, Optional

from performance_html import esc, _one_line


def when_heading_html() -> str:
    return _one_line('<div class="sc-title">When emails go out</div><div class="sc-sub">Auto Send only sends inside this window, in the campaign\'s own time zone.</div>')


def field_label_html(text: str) -> str:
    return f'<div class="sc-field-label">{esc(text)}</div>'


def strip_html(blocks: List[Dict]) -> str:
    """One block per hour, blue when sending is allowed in it, with the hour number under each."""
    cells = "".join(f'<div class="sc-block{" sc-on" if block["active"] else ""}"></div>' for block in blocks)
    labels = "".join(f'<div class="sc-hour">{esc(block["hour"])}</div>' for block in blocks)
    columns = f"grid-template-columns: repeat({len(blocks)}, 1fr)"
    return _one_line(f'<div class="sc-strip-title">Sending window across the day</div><div class="sc-strip" style="{columns}">{cells}</div><div class="sc-hours" style="{columns}">{labels}</div>')


def waits_heading_html() -> str:
    return _one_line('<div class="sc-title">Follow-up waits</div><div class="sc-sub">Days after the previous email. Counted in calendar days in this campaign\'s time zone.</div>')


def waits_header_html(text: str) -> str:
    """A column heading of the waits table: small grey capitals."""
    return f'<div class="sc-th">{esc(text)}</div>'


def wait_stage_html(label: str) -> str:
    return f'<div class="sc-stage">{esc(label)}</div>'


def wait_sends_on_html(sends_on: str, day: int) -> str:
    """What the wait means in words, with the day it lands on counted from the intro."""
    return _one_line(f'<div class="sc-wait-info">{esc(sends_on)}<span class="sc-day"> · day {esc(day)}</span></div>')


def waits_empty_html() -> str:
    return '<div class="sc-muted sc-empty">This campaign has only its intro, so there are no follow-up waits to set.</div>'


def next_run_html(card: Optional[Dict], note: str = "") -> str:
    """The 'Next automatic run' card. With no run to show (a campaign that is not running), `note` says why instead."""
    if card is None:
        return _one_line(f'<div class="sc-card sc-next"><div class="sc-eyebrow">Next automatic run</div><div class="sc-next-none">{esc(note or "Nothing is scheduled")}</div></div>')
    viewer = f' · <b>{esc(card["viewer"])}</b>' if card.get("viewer") else ""
    return _one_line(f'<div class="sc-card sc-next"><div class="sc-eyebrow">Next automatic run</div><div class="sc-next-time">{esc(card["headline"])}</div>'
                     f'<div class="sc-next-sub">{esc(card["relative"])}{viewer}</div></div>')


def today_html(sent: int, limit: Optional[int], fraction: float, window: Dict, due_now: int, due_tomorrow: int, weekend: str) -> str:
    """The 'Today' card: progress against the daily limit, whether the window is open, how many leads are due, and the weekend rule."""
    try:
        width = max(0, min(100, round(float(fraction) * 100)))
    except (TypeError, ValueError):
        width = 0
    count = f"{esc(sent)} / {esc(limit)}" if limit else f"{esc(sent)} sent"
    state = "sc-open" if window.get("open") else "sc-closed"
    text = esc(window.get("text", ""))
    text = text.replace("open", '<b class="sc-open">open</b>', 1) if window.get("open") and "open" in text else text
    return _one_line(f'<div class="sc-card"><div class="sc-title">Today</div><div class="sc-progress"><div class="sc-track"><i style="width:{width}%"></i></div><div class="sc-count">{count}</div></div>'
                     f'<div class="sc-line {state}">{text}</div><div class="sc-line">Due now: <b>{esc(due_now)}</b> leads · Due tomorrow: <b>{esc(due_tomorrow)}</b> leads</div>'
                     f'<div class="sc-line">{esc(weekend)}</div></div>')


def week_html(rows: List[Dict]) -> str:
    """This week's sends, one bar per day; a day that has not happened yet shows a dash."""
    items = []
    for row in rows:
        try:
            width = max(0, min(100, round(float(row.get("fraction") or 0) * 100)))
        except (TypeError, ValueError):
            width = 0
        count = "—" if row.get("count") is None else esc(row["count"])
        items.append(f'<div class="sc-week-row"><div class="sc-week-day">{esc(row["day"])}</div><div class="sc-track"><i style="width:{width}%"></i></div><div class="sc-week-count">{count}</div></div>')
    body = "".join(items) or '<div class="sc-muted">No send days are set.</div>'
    return _one_line(f'<div class="sc-card"><div class="sc-title">This week</div>{body}</div>')


SCHEDULE_CSS = """
.sc-card { background: #fff; border: 1px solid #e5e7eb; border-radius: 14px; padding: 20px 22px; margin-bottom: 14px; }
.sc-title { font-size: 18px; font-weight: 700; color: #111827; }
.sc-sub { font-size: 13.5px; color: #6b7280; margin: 2px 0 10px; }
.sc-field-label { font-size: 13px; font-weight: 700; color: #111827; margin: 6px 0 4px; }
.sc-muted { color: #6b7280; }
.sc-empty { padding: 12px 0 4px; font-size: 13.5px; }

/* ---- the form card: a native container styled as a card (its widgets are Streamlit's own) */
[class*="st-key-sc_when"] { background: #fff; border: 1px solid #e5e7eb; border-radius: 14px; padding: 20px 22px; margin-bottom: 14px; }

/* ---- the seven send-day buttons: the checkbox square is hidden, the label is the button */
[class*="st-key-schedule_day_"] { width: 100%; }
[class*="st-key-schedule_day_"] label > div:first-of-type { display: none; }
[class*="st-key-schedule_day_"] label { display: flex; justify-content: center; align-items: center; width: 100%; box-sizing: border-box; padding: 10px 0; border: 1px solid #d1d5db; border-radius: 10px; background: #fff; cursor: pointer; margin: 0; }
[class*="st-key-schedule_day_"] label p { font-size: 14px; font-weight: 700; color: #9ca3af; margin: 0; }
[class*="st-key-schedule_day_"] label:has(input:checked) { background: #eaf1ff; border-color: #2563eb; }
[class*="st-key-schedule_day_"] label:has(input:checked) p { color: #1d4ed8; }

/* ---- the hour strip */
.sc-strip-title { font-size: 13px; font-weight: 700; color: #111827; margin: 12px 0 8px; }
.sc-strip, .sc-hours { display: grid; gap: 4px; }
.sc-block { height: 30px; border-radius: 6px; background: #eef0f4; }
.sc-block.sc-on { background: #2563eb; }
.sc-hours { margin-top: 6px; }
.sc-hour { text-align: center; font-size: 12px; color: #6b7280; }

/* ---- the Save schedule button and its note */
[class*="st-key-sc_save"] button { background: #2563eb !important; border: 1px solid #2563eb !important; color: #fff !important; border-radius: 9px !important; font-weight: 700 !important; }
[class*="st-key-sc_save"] button:disabled { opacity: .45; }
[class*="st-key-sc_saverow"] { margin-top: 20px; }
.sc-save-note { color: #6b7280; font-size: 13.5px; padding-top: 9px; }

/* ---- the follow-up waits: a native container styled as a card, one row per follow-up with a number box (- and + buttons, or type) in the middle */
[class*="st-key-sc_waits"] { background: #fff; border: 1px solid #e5e7eb; border-radius: 14px; padding: 20px 22px; margin-bottom: 14px; }
.sc-th { font-size: 12px; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; color: #6b7280; padding: 6px 0 4px; }
[class*="st-key-sc_waitrow_"] { border-top: 1px solid #f0f2f5; padding: 6px 0; }
.sc-stage { font-weight: 700; font-size: 14px; color: #111827; padding-top: 7px; }
.sc-wait-info { font-size: 14px; color: #6b7280; padding-top: 7px; }
.sc-day { color: #9ca3af; }
/* The number box is kept compact: 136 x 34 px, not the 225 x 40 it was. Do NOT make it narrower than about 130px: below roughly 125px Streamlit hides the - and + buttons. */
[class*="st-key-wait_"] { width: 136px !important; max-width: 136px !important; }
[class*="st-key-wait_"] [data-testid="stNumberInputContainer"] { height: 34px !important; min-height: 0 !important; }
[class*="st-key-wait_"] input { height: 30px !important; min-height: 30px !important; padding: 0 4px !important; font-size: 14px !important; text-align: center; font-weight: 700; }
[class*="st-key-wait_"] button { width: 28px !important; min-width: 28px !important; height: 32px !important; padding: 0 !important; }

/* ---- the cards on the right */
.sc-next { background: linear-gradient(180deg, #f3f7ff, #fff); border-color: #c7d7fb; }
.sc-eyebrow { font-size: 12px; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; color: #6b7280; }
.sc-next-time { font-size: 28px; font-weight: 800; color: #111827; margin: 6px 0 4px; }
.sc-next-sub { font-size: 14px; color: #6b7280; }
.sc-next-sub b { color: #374151; }
.sc-next-none { font-size: 16px; font-weight: 600; color: #374151; margin-top: 8px; }
.sc-progress { display: flex; align-items: center; gap: 14px; margin: 12px 0 14px; }
.sc-track { flex: 1; height: 8px; border-radius: 99px; background: #eef0f4; overflow: hidden; }
.sc-track i { display: block; height: 100%; border-radius: 99px; background: #2563eb; }
.sc-count { font-size: 14px; font-weight: 700; color: #111827; white-space: nowrap; }
.sc-line { font-size: 13.5px; color: #4b5563; line-height: 1.9; }
.sc-line b { color: #111827; }
.sc-line b.sc-open { color: #15803d; }
.sc-closed { color: #b45309; font-weight: 600; }
.sc-week-row { display: flex; align-items: center; gap: 14px; margin: 10px 0; }
.sc-week-day { width: 34px; font-size: 13.5px; color: #374151; }
.sc-week-count { width: 36px; text-align: right; font-size: 14px; font-weight: 700; color: #111827; }
"""
