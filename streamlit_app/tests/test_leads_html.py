"""Markup for the Data tab's lead table and panel: nothing from an uploaded CSV can break out of it."""
import os
import re
import sys
from html.parser import HTMLParser

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import leads_html as LH  # noqa: E402

HOSTILE = '<script>alert(1)</script> "><img src=x onerror=alert(1)> [a](http://evil.example) **b** $$x$$ \u202e reversed \x00 end\n\nsecond paragraph'


def hostile_row(**kw):
    return {"name": HOSTILE, "company": HOSTILE, "email": HOSTILE, "status": HOSTILE, "status_kind": "x\" onclick=\"y", "progress": ["sent", HOSTILE, "pending"],
            "last_sent": HOSTILE, "sender": HOSTILE, **kw}


class _Scan(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.tags, self.attrs, self.classes = set(), set(), set()

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag)
        self.attrs |= {name for name, _ in attrs}
        for name, value in attrs:
            if name == "class":
                self.classes |= set((value or "").split())


def _scan(markup):
    scanner = _Scan()
    scanner.feed(markup)
    return scanner


def _all():
    row = hostile_row()
    return [LH.status_pill_html(HOSTILE, HOSTILE), LH.dots_html(["sent", HOSTILE, "replied"]), LH.head_html(), LH.row_html(row), LH.row_html(row, selected=True),
            LH.empty_table_html(HOSTILE), LH.panel_html(row, [(HOSTILE, HOSTILE), ("Empty", "")], [{"icon": HOSTILE, "text": HOSTILE, "when": HOSTILE}, {"icon": "x", "text": "y", "when": ""}], HOSTILE),
            LH.panel_html(row, [], []), LH.empty_panel_html()]


# ================================================================ safety

def test_hostile_text_never_becomes_markup_in_any_builder():
    for markup in _all():
        found = _scan(markup)
        assert found.tags <= {"div", "span", "i"}, found.tags
        assert found.attrs <= {"class"}, found.attrs                                   # no style, title, href, src or event handlers at all
        assert "<script" not in markup.lower() and "<img" not in markup.lower() and "onclick" not in "".join(found.attrs)


def test_every_class_comes_from_the_stylesheets_own_list_never_from_the_data():
    css = LH.LEADS_CSS
    for markup in _all():
        for cls in _scan(markup).classes:
            assert f".{cls}" in css, cls


def test_every_builder_returns_one_line_even_when_the_data_has_blank_lines():
    for markup in _all():
        assert "\n" not in markup and "\r" not in markup


def test_dangerous_characters_appear_only_escaped_and_direction_tricks_are_removed():
    markup = LH.row_html(hostile_row())
    assert "&lt;script&gt;" in markup and "&quot;" in markup and "\u202e" not in markup and "\x00" not in markup


def test_an_unknown_status_colour_or_dot_state_gets_the_plain_style_and_injects_nothing():
    assert LH.status_pill_html("x", 'evil" onclick="alert(1)') == '<span class="ld-pill">x</span>'
    assert LH.dots_html(["nope", 'evil" onclick="x']) == '<div class="ld-dots"><i class="ld-dot"></i><i class="ld-dot"></i></div>'


# ================================================================ content

def _row(**kw):
    return {"name": "Maya Okafor", "company": "BrightLeaf", "email": "maya@example.com", "status": "Replied", "status_kind": "good",
            "progress": ["sent", "replied", "pending"], "last_sent": "Oct 12", "sender": "sales1@x.com", **kw}


@pytest.mark.parametrize("kind, cls", [("good", "ld-pill-good"), ("active", "ld-pill-active"), ("bad", "ld-pill-bad"), ("waiting", "ld-pill-waiting"), ("neutral", "ld-pill-neutral")])
def test_each_colour_family_has_its_own_pill_style(kind, cls):
    assert LH.status_pill_html("x", kind) == f'<span class="ld-pill {cls}">x</span>'


def test_the_dots_show_one_per_stage_in_order():
    assert LH.dots_html(["sent", "replied", "pending"]) == '<div class="ld-dots"><i class="ld-dot ld-dot-sent"></i><i class="ld-dot ld-dot-replied"></i><i class="ld-dot"></i></div>'
    assert LH.dots_html([]) == '<div class="ld-dots"></div>'


def test_the_header_names_the_six_columns_and_hides_two_on_small_screens():
    markup = LH.head_html()
    assert re.findall(r'<div class="ld-cell[^"]*">([^<]+)</div>', markup) == ["Lead", "Email", "Status", "Sequence", "Last sent", "Sender"]
    assert markup.count("ld-hide-small") == 2


def test_a_row_shows_every_column_and_marks_the_selected_one():
    markup = LH.row_html(_row())
    for text in ("Maya Okafor", "BrightLeaf", "maya@example.com", "Replied", "Oct 12", "sales1@x.com"):
        assert text in markup
    assert "ld-selected" not in markup and "ld-selected" in LH.row_html(_row(), selected=True)


def test_a_row_without_a_company_date_or_sender_shows_a_dash_not_a_blank():
    markup = LH.row_html(_row(company="", last_sent="", sender=""))
    assert "ld-sub" not in markup and markup.count(">—<") == 2


def test_the_panel_shows_the_name_address_status_facts_and_timeline():
    markup = LH.panel_html(_row(), [("Lead ID", "7"), ("Sender", "")], [{"icon": "📨", "text": "Intro sent", "when": "Oct 9"}, {"icon": "⏹️", "text": "Stopped", "when": ""}], "Use Manage below.")
    assert "Maya Okafor" in markup and "maya@example.com" in markup and "Replied" in markup and "Lead ID" in markup and ">7<" in markup
    assert "📨 Intro sent <span class=\"ld-event-when\">· Oct 9</span>" in markup and "⏹️ Stopped</div>" in markup and "Use Manage below." in markup
    assert "ld-fact-value\">—<" in markup                                                       # an empty fact is a dash


def test_a_lead_nothing_has_been_sent_to_says_so():
    markup = LH.panel_html(_row(), [], [])
    assert "Nothing has been sent to this lead yet." in markup and "ld-timeline" not in markup


def test_the_empty_states_have_plain_messages():
    assert "Select a lead in the table" in LH.empty_panel_html() and "No leads match" in LH.empty_table_html("No leads match.")


# ================================================================ the stylesheet

def test_the_stylesheet_is_well_formed_and_self_contained():
    css = LH.LEADS_CSS
    assert css.count("{") == css.count("}") and "@import" not in css and "http" not in css and "expression(" not in css.lower()


def test_each_whole_row_is_one_click_target_and_the_buttons_wrapper_fills_it():
    css = LH.LEADS_CSS
    assert '[class*="st-key-ld_row_"] [class*="_open_"] > div { width: 100% !important; height: 100% !important; }' in css      # else only the top 40px responds
    assert "opacity: 0" in css and "position: absolute !important; inset: 0" in css


def test_every_key_pattern_ends_in_an_underscore_so_it_cannot_catch_a_neighbour():
    patterns = re.findall(r'\[class\*="(st-key-ld_[a-z]+_?)"\]', LH.LEADS_CSS)
    assert patterns and all(p.endswith("_") for p in patterns) and {"st-key-ld_table_", "st-key-ld_row_"} <= set(patterns)


def test_small_screens_hide_the_two_least_important_columns():
    css = LH.LEADS_CSS
    assert "@media (max-width: 900px)" in css and ".ld-hide-small { display: none; }" in css
    narrow = css[css.index("@media (max-width: 900px)"):]
    assert "grid-template-columns: minmax(0, 1.6fr) minmax(0, 1.4fr) minmax(0, 1fr) minmax(0, 1fr)" in narrow          # four visible columns, four grid columns


def test_every_row_has_the_same_minimum_height_whether_or_not_it_has_a_company_line():
    css = LH.LEADS_CSS
    assert ".ld-row { min-height: 58px; box-sizing: border-box;" in css
