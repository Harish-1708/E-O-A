"""Markup and styling for the Sequences tab: stage cards, the Editing / Locked badge, a variant's result chips, the placeholders line, the email
preview and the 'Check before saving' card.

PURE. Everything that comes from a campaign, a template or a lead goes through performance_html.esc, and every builder returns one line.
"""
import re
from typing import Dict, List

from performance_html import esc, _one_line


def section_label_html(text: str) -> str:
    return f'<div class="sq-label">{esc(text)}</div>'


def stage_card_html(card: Dict, selected: bool) -> str:
    """One stage in the list: its name, the day badge and how many variants it has."""
    badge = f'<span class="sq-badge">{esc(card.get("badge"))}</span>' if card.get("badge") else ""
    return _one_line(f'<div class="sq-stage{" sq-stage-on" if selected else ""}"><div class="sq-stage-top"><span class="sq-stage-name">{esc(card.get("title"))}</span>{badge}</div>'
                     f'<div class="sq-stage-sub">{esc(card.get("variants"))}</div></div>')


def new_stage_card_html(title: str, badge: str, sub: str) -> str:
    """The stage that is about to be added, drawn in the list as a dashed card with how far along it is."""
    badge_html = f'<span class="sq-badge">{esc(badge)}</span>' if badge else ""
    return _one_line(f'<div class="sq-stage sq-stage-new"><div class="sq-stage-top"><span class="sq-stage-name">{esc(title)}</span>{badge_html}</div><div class="sq-stage-sub">{esc(sub)}</div></div>')


def add_intro_html(title: str, details: str) -> str:
    """The line under the add form's title: which stage is next, and what is known about it."""
    return _one_line(f'<div class="sq-add-intro">Next stage: <b>{esc(title)}</b>{f" · {esc(details)}" if details else ""}</div>')


def add_progress_html(text: str) -> str:
    return f'<div class="sq-add-progress">{esc(text)}</div>'


def add_pane_css(selected_index: int) -> str:
    """The add forms keep EVERY tab's inputs on the page (Streamlit forgets an input that is not drawn, which would lose what was typed) and show only the chosen one."""
    return ('[class*="st-key-sq_addpane_"] { display: none !important; }\n'
            f'[class*="st-key-sq_addpane_{int(selected_index)}_x"] {{ display: flex !important; }}')


def editor_title_html(title: str) -> str:
    """The editor's heading. (The Editing / Locked state is the unlock box itself, shown as a badge at the card's top right.)"""
    return _one_line(f'<div class="sq-editor-title"><span>{esc(title)}</span></div>')


def stats_chips_html(stats: Dict, scope: str = "") -> str:
    """A variant's results as three chips, followed by what they are for ('Intro · Variant A'), so it is always clear which stage and variant they describe."""
    label = f'<span class="sq-scope">{esc(scope)}</span>' if scope else ""
    return _one_line(f'<div class="sq-chips"><span class="sq-chip sq-chip-blue">Sent {esc(stats.get("sent"))}</span><span class="sq-chip">Replies {esc(stats.get("replies"))}</span>'
                     f'<span class="sq-chip sq-chip-green">Reply rate {esc(stats.get("rate"))}</span>{label}</div>')


def placeholders_html() -> str:
    """The line above the clickable placeholders (each one is a button that adds itself to the end of the body)."""
    return '<div class="sq-placeholders">Placeholders — click one to add it to the end of the body</div>'


def _paragraphs(text: str) -> str:
    """Plain text as paragraphs: a blank line starts a new paragraph, a single line break stays a line break. Each LINE is escaped on its own (the
    escaper flattens line breaks to spaces, so the breaks must be made first, or 'Thanks,' and 'Harish' would run together)."""
    blocks = [block for block in re.split(r"\n\s*\n", (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()) if block.strip()]
    return "".join("<p>" + "<br>".join(esc(line.strip()) for line in block.strip().split("\n")) + "</p>" for block in blocks)


def preview_html(from_text: str, to_text: str, subject: str, body: str) -> str:
    """The email as the lead will see it: who it is from and to, the subject, and the body."""
    return _one_line(f'<div class="sq-preview"><div class="sq-preview-meta">From: {esc(from_text)} → To: {esc(to_text)}</div><div class="sq-preview-subject">{esc(subject)}</div>'
                     f'<div class="sq-preview-body">{_paragraphs(body)}</div></div>')


def checks_html(items: List[Dict], title: str = "Check before saving") -> str:
    rows = "".join(f'<div class="sq-check {"sq-ok" if item["ok"] else "sq-warn"}">{"✓" if item["ok"] else "⚠"} {esc(item["text"])}</div>' for item in items)
    return _one_line(f'<div class="sq-checks"><div class="sq-checks-title">{esc(title)}</div>{rows}</div>')


SEQUENCES_CSS = """
.sq-stage-new { border: 1.5px dashed #2563eb !important; background: #f3f7ff !important; }
.sq-stage-new .sq-stage-sub { color: #1d4ed8; }
.sq-add-intro { font-size: 13.5px; color: #4b5563; margin: 2px 0 12px; }
.sq-add-intro b { color: #111827; }
.sq-add-progress { font-size: 13.5px; color: #6b7280; }
[class*="st-key-sq_addfoot"] { flex-direction: row !important; align-items: center; justify-content: space-between; gap: 12px !important; border-top: 1px solid #eef0f3; padding-top: 12px; margin-top: 4px; }
[class*="st-key-sq_addfoot"] > div { width: auto !important; flex: 0 0 auto; }
.sq-label { font-size: 12.5px; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; color: #374151; margin: 2px 0 10px; }

/* ---- the stage list: one card per stage; the whole card is the (invisible) button on top of it */
.sq-stage { box-sizing: border-box; min-height: 68px; background: #fff; border: 1px solid #e5e7eb; border-radius: 12px; padding: 13px 15px; }
.sq-stage-on { border-color: #2563eb; background: #f3f7ff; }
.sq-stage-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.sq-stage-name { font-weight: 700; font-size: 14.5px; color: #111827; }
.sq-badge { font-size: 12px; font-weight: 700; color: #374151; border: 1px solid #d1d5db; border-radius: 999px; padding: 2px 10px; background: #fff; white-space: nowrap; }
.sq-stage-sub { margin-top: 5px; font-size: 13px; color: #6b7280; }
[class*="st-key-sq_stage_"] { position: relative; gap: 0 !important; min-height: 68px; margin-bottom: 10px; }
[class*="st-key-sq_stage_"] [data-testid="stMarkdownContainer"] { margin-bottom: 0 !important; }
[class*="st-key-sq_stage_"] [class*="st-key-sq_pick_"] { position: absolute !important; inset: 0; width: 100% !important; height: 100% !important; margin: 0 !important; z-index: 3; }
[class*="st-key-sq_stage_"] [class*="st-key-sq_pick_"] > div { width: 100% !important; height: 100% !important; }
[class*="st-key-sq_stage_"] [class*="st-key-sq_pick_"] button { width: 100%; height: 100%; opacity: 0; cursor: pointer; border: 0; background: transparent; }

/* ---- the editor card */
[class*="st-key-sq_editor"] { position: relative; background: #fff; border: 1px solid #e5e7eb; border-radius: 14px; padding: 20px 22px; }
.sq-editor-title { font-size: 17px; font-weight: 700; color: #111827; min-height: 40px; }          /* tall enough that the variant tabs sit BELOW the badge at the card's corner */

/* ---- the unlock box, shown as the card's Editing / Locked badge at its top right (the box itself is hidden, the label is the badge) */
[class*="st-key-sq_unlock"] { position: absolute; top: 20px; right: 22px; z-index: 2; width: auto !important; }
div:has(> [class*="st-key-sq_unlock"]) { margin-bottom: -16px !important; }          /* the unlock box floats over the card's corner, so it must not leave a gap above the title */
[class*="st-key-sq_unlock"] label > div:first-of-type { display: none; }
[class*="st-key-sq_unlock"] label { border: 1px solid #d1d5db; border-radius: 999px; padding: 3px 12px; background: #fff; cursor: pointer; margin: 0; }
[class*="st-key-sq_unlock"] label p { font-size: 12.5px; font-weight: 700; color: #6b7280; margin: 0; white-space: nowrap; }
[class*="st-key-sq_unlock"] label:has(input:checked) { border-color: #bbe3c9; background: #f0fbf4; }
[class*="st-key-sq_unlock"] label:has(input:checked) p { color: #15803d; }
.sq-chips { display: flex; flex-wrap: wrap; gap: 10px; margin: 2px 0 4px; }
.sq-chip { font-size: 12.5px; font-weight: 700; color: #374151; border: 1px solid #d1d5db; border-radius: 999px; padding: 4px 12px; background: #fff; }
.sq-scope { align-self: center; font-size: 12.5px; color: #6b7280; }
.sq-chip-blue { color: #1d4ed8; border-color: #bcd0f5; background: #eef4ff; }
.sq-chip-green { color: #15803d; border-color: #bbe3c9; background: #f0fbf4; }
.sq-placeholders { font-size: 13px; color: #6b7280; margin: 4px 0 2px; }

/* ---- the variant tabs as one connected segmented control */
[class*="st-key-sq_variant_"] [role="radiogroup"] { display: inline-flex; gap: 0; border: 1px solid #d1d5db; border-radius: 9px; overflow: hidden; background: #fff; }
[class*="st-key-sq_variant_"] label[data-testid="stRadioOption"] { margin: 0; padding: 7px 16px; border-right: 1px solid #e5e7eb; background: #fff; cursor: pointer; }
[class*="st-key-sq_variant_"] label[data-testid="stRadioOption"]:last-child { border-right: 0; }
[class*="st-key-sq_variant_"] label[data-testid="stRadioOption"] > div > div:first-child { display: none; }
[class*="st-key-sq_variant_"] label[data-testid="stRadioOption"]:has(input:checked) { background: #dde9fb; }
[class*="st-key-sq_variant_"] label[data-testid="stRadioOption"]:has(input:checked) p { color: #1d4ed8; }
[class*="st-key-sq_variant_"] label[data-testid="stRadioOption"] p { font-size: 13px; font-weight: 600; }

/* ---- this tab's own buttons (scoped to its containers) */
[class*="st-key-sq_add_stage"] button, [class*="st-key-sq_add_variant"] button, [class*="st-key-sq_discard"] button, [class*="st-key-sq_cancel"] button, [class*="st-key-sq_delete_variant"] button {
  border-radius: 9px !important; font-weight: 600 !important; border: 1px solid #d1d5db !important; background: #fff !important; color: #374151 !important; }
[class*="st-key-sq_delete_variant"] button { border-color: #f3c4c4 !important; color: #b91c1c !important; }
[class*="st-key-sq_save"] button { background: #2563eb !important; border: 1px solid #2563eb !important; color: #fff !important; border-radius: 9px !important; font-weight: 700 !important; }
[class*="st-key-sq_save"] button:disabled { opacity: .45; }
[class*="st-key-sq_actions"] { flex-direction: row !important; align-items: center; gap: 10px !important; flex-wrap: wrap; }
[class*="st-key-sq_actions"] > div { width: auto !important; flex: 0 0 auto; }
[class*="st-key-sq_actions"] > div:last-child { margin-left: auto; }
[class*="st-key-sq_variantbar"] { flex-direction: row !important; align-items: center; gap: 12px !important; flex-wrap: wrap; margin-top: 4px; }
[class*="st-key-sq_variantbar"] > div { width: auto !important; flex: 0 0 auto; }

/* ---- the preview and the checks */
.sq-preview { background: #fff; border: 1px solid #e5e7eb; border-radius: 14px; padding: 18px 20px; }
.sq-preview-meta { font-size: 12.5px; color: #6b7280; margin-bottom: 8px; }
.sq-preview-subject { font-size: 17px; font-weight: 700; color: #111827; padding-bottom: 12px; border-bottom: 1px solid #eef0f4; margin-bottom: 12px; overflow-wrap: anywhere; }
.sq-preview-body { font-size: 14px; color: #1f2937; line-height: 1.6; overflow-wrap: anywhere; }
.sq-preview-body p { margin: 0 0 12px; }
.sq-checks { margin-top: 14px; background: linear-gradient(180deg, #f3f7ff, #fff); border: 1px solid #c7d7fb; border-radius: 14px; padding: 14px 18px; }
.sq-checks-title { font-weight: 700; font-size: 14.5px; color: #111827; margin-bottom: 6px; }
.sq-check { font-size: 13.5px; line-height: 1.7; color: #4b5563; }
.sq-ok { color: #4b5563; }
.sq-warn { color: #b45309; font-weight: 600; }
"""


HTML_ADD_NOTE = ("This is an HTML campaign: what you write here is the subject and the plain-text copy. A starter design is created beside it automatically; "
                 "edit the design under \"Design (HTML)\" at the top of this tab once it exists.")
