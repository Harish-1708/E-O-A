"""The markup and styling of the Campaigns list: the totals above it, a campaign's card (name, status, numbers and send chart), the empty states and the two dialogs'
pieces. Everything that came from a person or a Sheet (a campaign's name, a problem sentence) goes through esc(), so it can never become markup.
"""
import html
from datetime import datetime
from typing import Dict, List, Optional

from campaigns_hub_logic import series_caption

CHART_HEIGHT = 36

STATUS_CHIPS = {
    "running": ("live", "● Running"), "attention": ("warn", "⚠ Attention needed"), "paused": ("paused", "⏸ Paused"),
    "draft": ("draft", "📝 Draft"), "completed": ("done", "✓ Completed"),
}


def esc(value) -> str:
    return html.escape("" if value is None else str(value).replace("\x00", "").replace("\u202e", ""), quote=True)


def _one_line(markup: str) -> str:
    return " ".join(line.strip() for line in markup.splitlines() if line.strip())


def relative_time(timestamp: str, now: Optional[datetime] = None) -> str:
    """'just now', '12 min ago', '10 hr ago', '1 day ago', '40 days ago'; '—' for no time and the text itself when it cannot be read."""
    if not timestamp:
        return "—"
    try:
        then = datetime.strptime(str(timestamp), "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return str(timestamp)
    seconds = ((now or datetime.now()) - then).total_seconds()
    if seconds < 0:
        return str(timestamp)
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} hr ago"
    days = int(seconds // 86400)
    return f"{days} day{'' if days == 1 else 's'} ago"


def stat_card_html(label: str, value, sub: str) -> str:
    """One of the four totals above the list."""
    return _one_line(f'<div class="hb-stat"><div class="hb-stat-l">{esc(label)}</div><div class="hb-stat-v">{esc(value)}</div><div class="hb-stat-s">{esc(sub)}</div></div>')


def stats_html(summary: Dict) -> str:
    cards = [stat_card_html("Running now", summary["running"], f"of {summary['count']} campaign{'' if summary['count'] == 1 else 's'}"),
             stat_card_html("Leads", summary["leads"], "across all campaigns"),
             stat_card_html("Emails sent", summary["sent"], "all time"),
             stat_card_html("Replies", summary["replies"], f"{summary['reply_rate']} of {int(summary.get('contacted') or 0)} leads replied" if summary["reply_rate"] != "—" else "no reply rate yet")]
    return f'<div class="hb-stats">{"".join(cards)}</div>'


def chart_html(series: Optional[Dict]) -> str:
    """The campaign's sends as small bars, tallest = the busiest day (or week), with a caption saying what the bars are and over which dates."""
    bars = [max(0, int(n)) for n in (series or {}).get("bars") or []]
    caption = esc(series_caption(series or {}))
    if not bars:
        return f'<div class="hb-chart-none">{caption}</div>'
    top = max(bars) or 1
    drawn = "".join(f'<i style="height:{max(3, round(n / top * CHART_HEIGHT))}px"></i>' for n in bars)
    return _one_line(f'<div class="hb-spark">{drawn}</div><div class="hb-chart-cap">{caption}</div>')


def format_badge_html(fmt: str) -> str:
    """The small Text / HTML badge beside a campaign's name."""
    return '<span class="hb-fmt hb-fmt-html">HTML</span>' if fmt == "html" else '<span class="hb-fmt hb-fmt-text">Text</span>'


def campaign_card_html(row: Dict, now: Optional[datetime] = None, show_format: bool = False) -> str:
    """The body of a campaign's card (its Open and Duplicate buttons are real buttons beside it). An HTML campaign always carries its badge; a plain-text campaign carries one
    only when show_format is on (the page turns it on once there is an HTML campaign to tell it apart from), so until then every card is exactly what it was."""
    kind, chip_text = STATUS_CHIPS.get(row.get("status"), ("done", str(row.get("status_label") or row.get("status") or "")))
    activity = "No activity yet" if not row.get("last_activity") else f"Last activity {relative_time(row['last_activity'], now)}"
    if row.get("status") == "draft":
        activity = "Not launched yet"
    problem_chips = "".join(f'<span class="hb-problem">{esc(p)}</span>' for p in row.get("problems") or [])
    problems_html = f'<div class="hb-problems">{problem_chips}</div>' if problem_chips else ""
    faded = " hb-faded" if not row.get("sent") and row.get("status") in ("draft", "attention") else ""
    metrics = [("Leads", row.get("total_leads", 0)), ("Sent", row.get("sent", 0)), ("Replies", row.get("replies", 0)), ("Reply rate", row.get("reply_rate", "—"))]
    metrics_html = "".join(f'<div><b>{esc(value)}</b><span>{label}</span></div>' for label, value in metrics)
    badge = format_badge_html("html") if row.get("format") == "html" else (format_badge_html("text") if show_format else "")
    return _one_line(f'<div class="hb-row"><div><div class="hb-name">{esc(row.get("name"))}{badge}</div><div class="hb-meta"><span class="hb-chip hb-chip-{kind}">{esc(chip_text)}</span>'
                     f'<span>{esc(activity)}</span></div>{problems_html}</div>'
                     f'<div class="hb-metrics{faded}">{metrics_html}</div><div class="hb-chart">{chart_html(row.get("series"))}</div></div>')


def empty_state_html(icon: str, title: str, text: str) -> str:
    return _one_line(f'<div class="hb-empty"><div class="hb-empty-icon">{esc(icon)}</div><div class="hb-empty-title">{esc(title)}</div><div class="hb-empty-text">{esc(text)}</div></div>')


def load_error_html(count: int) -> str:
    return f'<div class="hb-banner">⚠ <b>{int(count)} campaign{"" if count == 1 else "s"} couldn’t be loaded</b> and {"is" if count == 1 else "are"} left out of the list.</div>'


def name_feedback_html(kind: str, text: str) -> str:
    """The line under a name box: grey while empty, red while the name cannot be used, green when it can."""
    css = kind if kind in ("hint", "error", "ok") else "hint"
    mark = {"error": "✗ ", "ok": "✓ "}.get(css, "")
    return f'<div class="hb-feedback hb-feedback-{css}">{mark}{esc(text)}</div>'


def copy_list_html(items: List[tuple]) -> str:
    """The 'what gets copied' list in the Duplicate dialog: [(copied?, text), ...]; a ✓ for what is copied and a ✗ for what never is."""
    rows = "".join(f'<div class="hb-copy-{"yes" if yes else "no"}">{"✓" if yes else "✗"} {esc(text)}</div>' for yes, text in items)
    return f'<div class="hb-copy">{rows}</div>'


def source_chip_html(name: str) -> str:
    return f'<div class="hb-source">Copying from <span class="hb-chip hb-chip-done">{esc(name)}</span></div>'


FORMAT_LABELS = {"text": "Plain text", "html": "Designed (HTML)"}
FORMAT_CAPTIONS = {
    "text": "Reads like a personal email, from a real person. The safer choice for first contact with people who do not know you yet, such as creators and partners.",
    "html": "A branded layout with sections, images and buttons. Best for people who opted in (customers, subscribers) and for announcements.",
}


def created_files_html(fmt: str, campaign_name: str) -> str:
    """What creating the campaign will make, shown before anything is created."""
    name = esc(campaign_name or "Your_Campaign")
    lines = [f'<span class="hb-td">templates/{name}/</span>', '&nbsp;&nbsp;<span class="hb-tn">intro_A.txt</span> <span class="hb-tm">&nbsp;subject and the plain-text email</span>' if fmt != "html"
             else '&nbsp;&nbsp;<span class="hb-tn">intro_A.txt</span> <span class="hb-tm">&nbsp;subject and the plain-text copy</span>']
    if fmt == "html":
        lines.append('&nbsp;&nbsp;<span class="hb-tn">intro_A.html</span> <span class="hb-tm">&nbsp;the designed email (the starter layout)</span>')
        lines += ["", '<span class="hb-td">config/campaigns/</span>', f'&nbsp;&nbsp;<span class="hb-tn">{name}.yaml</span> <span class="hb-tm">&nbsp;format: html</span>']
    return _one_line('<div class="hb-tree">' + "<br>".join(lines) + "</div>")


def dialog_intro_html(text: str) -> str:
    return f'<div class="hb-dialog-intro">{esc(text)}</div>'


def page_head_html(title: str, subtitle: str) -> str:
    return _one_line(f'<div class="hb-title">{esc(title)}</div><div class="hb-subtitle">{esc(subtitle)}</div>')


HUB_CSS = """
.hb-fmt { display: inline-block; margin-left: 8px; vertical-align: middle; border-radius: 999px; padding: 2px 9px; font-size: 11.5px; font-weight: 800; white-space: nowrap; }
.hb-fmt-html { background: #ede9fe; color: #6d28d9; }
.hb-fmt-text { background: #e0f2fe; color: #0369a1; }
/* the format and layout choices in the New campaign dialog: each option is a card, the chosen one outlined in blue (Streamlit wraps every option in its own element, marked data-selected) */
[class*="st-key-hub_new_campaign_format"] [data-testid="stRadioGroup"] > div, [class*="st-key-hub_new_campaign_layout"] [data-testid="stRadioGroup"] > div { border: 1.5px solid #d1d5db; border-radius: 12px; padding: 10px 14px; margin-bottom: 8px; background: #fff; }
[class*="st-key-hub_new_campaign_format"] [data-testid="stRadioGroup"] > div[data-selected="true"], [class*="st-key-hub_new_campaign_layout"] [data-testid="stRadioGroup"] > div[data-selected="true"] { border-color: #2563eb; background: #f3f7ff; box-shadow: 0 0 0 3px rgba(37, 99, 235, .12); }
.hb-tree { font-family: ui-monospace, Menlo, monospace; font-size: 12.5px; line-height: 1.75; background: #0f172a; color: #e2e8f0; border-radius: 12px; padding: 12px 16px; margin: 6px 0 12px 0; }
.hb-td { color: #93c5fd; } .hb-tn { color: #86efac; } .hb-tm { color: #94a3b8; }
/* ---- the Campaigns list */
.hb-title { font-size: 32px; font-weight: 800; letter-spacing: -.02em; line-height: 1.15; color: #111827; }
.hb-subtitle { color: #6b7280; font-size: 14px; margin-top: 6px; }
.hb-stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin: 20px 0 16px; }
.hb-stat { background: #fff; border: 1px solid #e5e7eb; border-radius: 14px; padding: 16px 20px; }
.hb-stat-l { font-size: 12.5px; color: #6b7280; font-weight: 600; }
.hb-stat-v { font-size: 27px; font-weight: 800; letter-spacing: -.02em; margin-top: 4px; color: #111827; }
.hb-stat-s { font-size: 12.5px; color: #6b7280; margin-top: 3px; }

[class*="st-key-hub_card_"] { background: #fff; border: 1px solid #e5e7eb; border-left: 4px solid #d1d5db; border-radius: 14px; padding: 22px 24px; margin-bottom: 12px; min-height: 96px; gap: 0 !important; justify-content: center; }
/* Streamlit puts a negative bottom margin under every Markdown block (to cancel a trailing paragraph); inside a card it pulled the content down toward the bottom edge */
[class*="st-key-hub_card_"] [data-testid="stMarkdownContainer"], [class*="st-key-hub_card_"] [data-testid="stElementContainer"] { margin-bottom: 0 !important; }
[class*="st-key-hub_card_running"] { border-left-color: #22c55e; }
[class*="st-key-hub_card_attention"] { border-left-color: #f59e0b; }
[class*="st-key-hub_card_draft"] { border-left: 4px dashed #93c5fd; }
[class*="st-key-hub_card_"] [data-testid="stHorizontalBlock"] { align-items: center; gap: 1rem; }
.hb-row { display: grid; grid-template-columns: minmax(230px, 1.2fr) 1.3fr 170px; gap: 18px; align-items: center; }
.hb-name { font-size: 16px; font-weight: 700; margin-bottom: 7px; color: #111827; word-break: break-word; }
.hb-meta { display: flex; align-items: center; gap: 10px; color: #6b7280; font-size: 12.5px; flex-wrap: wrap; }
.hb-chip { display: inline-flex; align-items: center; font-size: 12.5px; font-weight: 600; border-radius: 999px; padding: 3px 11px; border: 1px solid #d1d5db; background: #fff; color: #374151; white-space: nowrap; }
.hb-chip-live { color: #15803d; border-color: #bbe3c9; background: #f0fbf4; }
.hb-chip-warn { color: #b45309; border-color: #f3d9a8; background: #fffaf0; }
.hb-chip-draft { color: #1d4ed8; border-color: #c7d7fb; background: #f3f7ff; }
.hb-chip-paused { color: #6b7280; background: #f9fafb; }
.hb-problems { margin-top: 8px; display: flex; gap: 6px; flex-wrap: wrap; }
.hb-problem { font-size: 12px; font-weight: 600; color: #92400e; background: #fffaf0; border: 1px solid #f3d9a8; border-radius: 8px; padding: 2px 9px; }
.hb-metrics { display: flex; gap: 28px; }
.hb-metrics b { display: block; font-size: 19px; font-weight: 800; letter-spacing: -.01em; color: #111827; }
.hb-metrics span { font-size: 12px; color: #6b7280; font-weight: 600; }
.hb-faded b { color: #9ca3af; }
.hb-spark { display: flex; align-items: flex-end; gap: 3px; height: 36px; }
.hb-spark i { display: block; flex: 1 1 0; max-width: 9px; min-width: 2px; background: #93c5fd; border-radius: 2px; }
.hb-chart-cap, .hb-chart-none { font-size: 11.5px; color: #9ca3af; margin-top: 5px; }
.hb-chart-none { margin-top: 0; }
[class*="st-key-hub_card_"] .stButton button { white-space: nowrap; font-weight: 600; }
[class*="st-key-hub_card_"] [class*="st-key-open_"] button { color: #1d4ed8; border-color: #b7cff7; background: #f3f7ff; }

.hb-empty { text-align: center; padding: 34px 20px 12px; }
.hb-empty-icon { width: 62px; height: 62px; border-radius: 50%; background: #eef2ff; display: inline-flex; align-items: center; justify-content: center; font-size: 28px; }
.hb-empty-title { font-size: 17px; font-weight: 700; margin: 12px 0 6px; color: #111827; }
.hb-empty-text { color: #6b7280; font-size: 13.5px; margin-bottom: 10px; }
.hb-banner { background: #fffaf0; border: 1px solid #f3d9a8; color: #92400e; border-radius: 12px; padding: 12px 16px; font-size: 13.5px; margin: 8px 0; }

[class*="st-key-hub_arch_"] { background: #fff; border: 1px solid #e5e7eb; border-radius: 12px; padding: 10px 16px; margin-bottom: 6px; }
[class*="st-key-hub_arch_"] [data-testid="stHorizontalBlock"] { align-items: center; }
.hb-archname { font-weight: 700; color: #111827; }
[class*="st-key-hub_toolbar"] [data-testid="stHorizontalBlock"] { align-items: center; gap: .75rem; }

/* ---- the New campaign and Duplicate campaign dialogs */
.hb-dialog-intro { color: #6b7280; font-size: 13.5px; line-height: 1.55; margin-bottom: 12px; }
.hb-feedback { font-size: 12.5px; margin: -4px 0 10px; }
.hb-feedback-hint { color: #6b7280; }
.hb-feedback-error { color: #b91c1c; }
.hb-feedback-ok { color: #15803d; }
.hb-source { font-size: 13.5px; color: #374151; margin-bottom: 12px; display: flex; align-items: center; gap: 8px; }
.hb-copy { background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 12px; padding: 12px 16px; margin-bottom: 14px; font-size: 13.5px; line-height: 1.9; }
.hb-copy-yes { color: #166534; }
.hb-copy-no { color: #6b7280; }
"""
