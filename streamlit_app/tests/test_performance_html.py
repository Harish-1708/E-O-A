"""Markup for the Performance display: nothing from outside can break out of it, every builder returns one line, and
the SVG chart handles every shape of data."""
import os
import re
import sys
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import performance_html as P  # noqa: E402

HOSTILE = '<script>alert(1)</script> "><img src=x onerror=alert(1)> [a](http://evil.example) **b** $$x$$ \u202e reversed &amp; \x00 end'


class _Scan(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.tags, self.attrs = set(), set()

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag)
        self.attrs |= {name for name, _ in attrs}


def _scan(markup):
    scanner = _Scan()
    scanner.feed(markup)
    return scanner


SAFE_TAGS = {"div", "span", "b", "i", "h3", "table", "tr", "th", "td", "svg", "defs", "lineargradient", "stop", "line", "text", "path", "circle"}
SAFE_ATTRS = {"class", "style", "title", "viewbox", "role", "aria-label", "id", "x1", "x2", "y1", "y2", "x", "y", "d", "fill", "stroke", "stroke-width",
              "stroke-linejoin", "stop-color", "stop-opacity", "offset", "font-size", "text-anchor", "cx", "cy", "r"}


def _everything(**overrides):
    series = [{"date": "2026-10-01", "Sent": 5, "Replies": 1}, {"date": "2026-10-02", "Sent": 9, "Replies": 2}]
    return [
        P.kpi_html(HOSTILE, HOSTILE, HOSTILE, HOSTILE), P.card_html(HOSTILE, HOSTILE, P.note_html(HOSTILE), [(HOSTILE, "sent"), (HOSTILE, "replies"), (HOSTILE, HOSTILE)]),
        P.table_html([HOSTILE, "B"], [[HOSTILE, HOSTILE], [HOSTILE]]), P.bars_html([(HOSTILE, 3), ("ok", 1)]),
        P.callout_html(HOSTILE, [(HOSTILE, HOSTILE)], HOSTILE), P.grid_html("g2", [P.kpi_html(HOSTILE, 1)]),
        P.area_chart_svg([{"date": HOSTILE, "Sent": 5, "Replies": 1}, {"date": "x" + HOSTILE, "Sent": 2, "Replies": 0}]), P.area_chart_svg(series),
    ]


# ================================================================ text is always made safe

@pytest.mark.parametrize("text, expected", [("plain", "plain"), (None, ""), (0, "0"), (3.5, "3.5"), ("a<b", "a&lt;b"), ('say "hi"', "say &quot;hi&quot;"),
                                            ("it's", "it&#x27;s"), ("line1\nline2\r\nline3", "line1 line2  line3"), ("  padded  ", "padded"),
                                            ("a\u202eb\u200fc\u2066d", "a b c d"), ("\x00\x1f x", "x")])
def test_esc(text, expected):
    assert P.esc(text) == expected


def test_hostile_text_never_becomes_markup_in_any_builder():
    for markup in _everything():
        found = _scan(markup)
        assert found.tags <= SAFE_TAGS, found.tags - SAFE_TAGS
        assert found.attrs <= SAFE_ATTRS, found.attrs - SAFE_ATTRS
        assert "<script" not in markup.lower() and "<img" not in markup.lower() and "onerror=" not in markup.replace("&quot;", '"').split("<")[0]
        assert "javascript:" not in markup.lower() and "http://evil" not in re.sub(r"&[a-z#0-9x]+;", "", markup).replace("[a]", "") or "href" not in markup


def test_the_dangerous_characters_are_present_only_in_escaped_form():
    markup = P.kpi_html(HOSTILE, HOSTILE, HOSTILE, HOSTILE)
    assert "&lt;script&gt;" in markup and "&lt;img" in markup and "&quot;" in markup
    assert "\u202e" not in markup and "\x00" not in markup


def test_every_builder_returns_exactly_one_line_even_when_the_text_contains_blank_lines():
    nasty = "first\n\n\nsecond\r\n\r\nthird"
    for markup in [P.kpi_html(nasty, nasty, nasty, nasty), P.card_html(nasty, nasty, "", [(nasty, "sent")]), P.table_html([nasty], [[nasty]]),
                   P.bars_html([(nasty, 1)]), P.callout_html(nasty, [(nasty, nasty)], nasty), P.note_html(nasty), P.area_chart_svg([{"date": nasty, "Sent": 1, "Replies": 0}])] + _everything():
        assert "\n" not in markup and "\r" not in markup


# ================================================================ cards, grids, tables, bars, callouts

def test_a_headline_card_shows_its_label_value_and_caption_and_the_tooltip_only_when_given():
    markup = P.kpi_html("Reply rate", "18.3%", "112 replied", "Replies the AI read as Interested.")
    assert 'title="Replies the AI read as Interested."' in markup and "Reply rate" in markup and "18.3%" in markup and "112 replied" in markup
    assert "title=" not in P.kpi_html("Contacted", 612)
    assert "pf-kpi-caption\"></div>" in P.kpi_html("Contacted", 612)                                   # no caption: still an empty line, so cards line up


def test_numbers_and_dashes_are_shown_as_given():
    assert ">0<" in P.kpi_html("x", 0) and ">—<" in P.kpi_html("x", "—") and ">1486<" in P.kpi_html("x", 1486)


def test_a_grid_holds_its_cells_and_refuses_an_unknown_layout():
    markup = P.grid_html("g6", ["<div>a</div>", "<div>b</div>"])
    assert markup == '<div class="pf-grid pf-g6"><div>a</div><div>b</div></div>'
    for name in ("g4", "g3", "g2", "g2e"):
        assert f"pf-{name}" in P.grid_html(name, [])
    for bad in ("g7", "", "x\" onclick=\"y", None):
        with pytest.raises(ValueError):
            P.grid_html(bad, [])


def test_a_card_has_a_title_an_optional_hint_and_chips_of_a_known_kind_only():
    markup = P.card_html("Activity", "Days are UTC.", "<p>x</p>", [("Sent", "sent"), ("Replies", "replies"), ("Other", "weird")])
    assert "Activity" in markup and "Days are UTC." in markup and "<p>x</p>" in markup
    assert 'pf-chip pf-chip-sent' in markup and 'pf-chip pf-chip-replies' in markup and 'pf-chip-weird' not in markup
    assert "pf-card-hint" not in P.card_html("T")


def test_a_table_has_every_header_and_row_and_pads_a_short_row():
    markup = P.table_html(["Stage", "Sent", "Replies"], [["Intro", 612, 71], ["Follow-up 1", 470]])
    assert markup.count("<th>") == 3 and markup.count("<tr>") == 3
    assert "<td>Intro</td><td>612</td><td>71</td>" in markup and "<td>Follow-up 1</td><td>470</td><td></td>" in markup
    assert P.table_html(["A"], []).count("<td>") == 0


def test_bars_are_scaled_against_the_largest_and_a_zero_stays_empty():
    markup = P.bars_html([("Interested", 47), ("Not Interested", 31), ("Unclear", 0)], P.INTENT_COLOURS)
    assert "width:100%;background:#16a34a" in markup and "width:66%;background:#dc2626" in markup and "width:0%;background:#9ca3af" in markup


def test_bars_never_divide_by_zero_and_use_a_default_colour_for_an_unknown_label():
    assert P.bars_html([]) == "" and "width:0%" in P.bars_html([("a", 0), ("b", 0)])
    assert f"background:{P.DEFAULT_COLOUR}" in P.bars_html([("brand new label", 5)], P.INTENT_COLOURS)


def test_a_colour_comes_only_from_the_table_never_from_the_label():
    markup = P.bars_html([('red;background:url(http://evil.example)', 3)], {})
    assert "evil" not in re.sub(r"&[a-z#0-9x]+;", "", markup).split('style="')[1].split('"')[0]


def test_the_callout_bolds_only_its_labels():
    markup = P.callout_html("What worked best?", [("Template variant:", "<b>C</b> wins")], "Ranked only among groups with enough volume.")
    assert "<b>Template variant:</b> &lt;b&gt;C&lt;/b&gt; wins" in markup and "Ranked only among" in markup and markup.count("<b>") == 1


# ================================================================ the y axis

@pytest.mark.parametrize("value, step, top", [(0, 1, 4), (1, 1, 4), (4, 1, 4), (5, 2, 8), (7, 2, 8), (8, 2, 8), (9, 5, 20), (20, 5, 20), (21, 10, 40), (40, 10, 40),
                                              (41, 20, 80), (82, 25, 100), (100, 25, 100), (101, 50, 200), (1000, 250, 1000), (1001, 500, 2000), (0.5, 1, 4)])
def test_the_axis_step_and_top_for_known_values(value, step, top):
    assert P.nice_ceiling(value) == (step, top)


@pytest.mark.parametrize("value", [0, -5, float("nan"), float("inf"), None, 1, 3, 17, 82, 999, 1234, 98765, 10 ** 7])
def test_the_axis_always_covers_the_data_with_whole_number_steps(value):
    step, top = P.nice_ceiling(value)
    assert top > 0 and top == step * 4 and step >= 1 and step == int(step)
    if value and value > 0 and value != float("inf"):
        assert top >= value


# ================================================================ the activity chart

def _series(n, sent=lambda i: i + 1, replies=lambda i: i // 3):
    return [{"date": f"2026-09-{(i % 28) + 1:02d}", "Sent": sent(i), "Replies": replies(i)} for i in range(n)]


@pytest.mark.parametrize("n", [1, 2, 3, 7, 30, 90, 400])
def test_the_chart_is_valid_xml_for_every_length_of_data(n):
    svg = P.area_chart_svg(_series(n))
    root = ET.fromstring(svg)
    assert root.tag == "svg" and root.get("viewBox") == f"0 0 {P.CHART_WIDTH} {P.CHART_HEIGHT}"


def test_no_data_gives_no_chart():
    assert P.area_chart_svg([]) == ""


def test_one_day_draws_two_dots_and_no_lines():
    svg = P.area_chart_svg([{"date": "2026-10-13", "Sent": 5, "Replies": 2}])
    assert svg.count("<circle") == 2 and "<path" not in svg


def test_several_days_draw_a_filled_sent_area_and_two_lines():
    svg = P.area_chart_svg(_series(10))
    assert svg.count("<path") == 3 and 'fill="url(#pf-sent-fill)"' in svg and P.SENT_COLOUR in svg and P.REPLIES_COLOUR in svg
    assert "<circle" not in svg


def test_every_point_stays_inside_the_drawing_area():
    for series in (_series(30), _series(2), [{"date": "2026-10-01", "Sent": 10**9, "Replies": 0}] * 5, [{"date": "2026-10-01", "Sent": 0, "Replies": 0}] * 4):
        svg = P.area_chart_svg(series)
        for x, y in re.findall(r"[ML ]\s?(-?[\d.]+),(-?[\d.]+)", " ".join(re.findall(r' d="([^"]+)"', svg))):
            assert 0 <= float(x) <= P.CHART_WIDTH and 0 <= float(y) <= P.CHART_HEIGHT


def test_an_all_zero_series_still_draws_a_flat_line_on_the_bottom_axis():
    svg = P.area_chart_svg([{"date": f"2026-10-0{i}", "Sent": 0, "Replies": 0} for i in range(1, 5)])
    ET.fromstring(svg)
    assert f"{P.CHART_HEIGHT - P.CHART_PAD_BOTTOM:.1f}" in svg


def test_missing_or_bad_numbers_are_treated_as_zero_not_as_a_crash():
    svg = P.area_chart_svg([{"date": "2026-10-01", "Sent": None, "Replies": -3}, {"date": "2026-10-02", "Sent": 4, "Replies": None}, {"date": "2026-10-03"}])
    ET.fromstring(svg)


def test_the_x_axis_labels_are_short_dates_at_up_to_five_places_without_repeats():
    labels = re.findall(r'text-anchor="middle">([^<]*)<', P.area_chart_svg(_series(30)))
    assert 2 <= len(labels) <= 5 and len(set(labels)) == len(labels) and all(re.fullmatch(r"[A-Z][a-z]{2} \d{1,2}", label) for label in labels)
    assert len(re.findall(r'text-anchor="middle">', P.area_chart_svg(_series(1)))) == 1
    assert len(re.findall(r'text-anchor="middle">', P.area_chart_svg(_series(2)))) == 2


def test_a_date_that_is_not_a_date_is_shown_escaped_and_does_not_break_the_chart():
    svg = P.area_chart_svg([{"date": "<b>soon</b>", "Sent": 1, "Replies": 0}, {"date": "2026-10-02", "Sent": 2, "Replies": 0}])
    ET.fromstring(svg)
    assert "&lt;b&gt;soon" in svg and "<b>" not in svg


def test_the_chart_describes_itself_for_screen_readers():
    svg = P.area_chart_svg(_series(7, sent=lambda i: 10 + i))
    assert 'role="img"' in svg and "over 7 days" in svg and "most in one day: 16" in svg
    assert "over 1 day;" in P.area_chart_svg(_series(1))


def test_the_y_axis_labels_run_from_zero_to_the_top_in_four_steps():
    svg = P.area_chart_svg([{"date": "2026-10-01", "Sent": 82, "Replies": 5}, {"date": "2026-10-02", "Sent": 40, "Replies": 1}])
    assert re.findall(r'text-anchor="end">([^<]*)<', svg) == ["0", "25", "50", "75", "100"]


# ================================================================ the stylesheet

def test_the_stylesheet_is_well_formed_self_contained_and_defines_every_class_the_builders_use():
    css = P.PERFORMANCE_CSS
    assert css.count("{") == css.count("}")
    assert "@import" not in css and "http" not in css and "expression(" not in css.lower()
    used = set()
    for markup in _everything() + [P.grid_html(g, []) for g in ("g6", "g4", "g3", "g2", "g2e")]:
        for value in re.findall(r'class="([^"]+)"', markup):
            used |= set(value.split())
    missing = {c for c in used if not c.startswith("pf-chip-") and f".{c}" not in css}
    assert not missing, missing


def test_the_grid_collapses_on_small_screens():
    css = P.PERFORMANCE_CSS
    assert "@media (max-width: 900px)" in css and "@media (max-width: 1200px)" in css


def test_the_card_title_size_is_pinned_because_streamlit_styles_a_bare_h3_much_larger():
    css = P.PERFORMANCE_CSS
    rule = css[css.index(".pf-card-title {"):css.index("}", css.index(".pf-card-title {"))]
    assert "font-size: 16px !important" in rule and "margin: 0 !important" in rule and "padding: 0 !important" in rule


def test_table_cells_keep_only_horizontal_lines_because_streamlit_adds_a_grid_by_default():
    css = P.PERFORMANCE_CSS
    assert ".pf-table th, .pf-table td { border-left: 0 !important; border-right: 0 !important; border-top: 0 !important;" in css
    assert "border-bottom: 1px solid #e5e7eb !important" in css and "border-bottom: 1px solid #f0f2f5 !important" in css


# ================================================================ gaps found by deliberately breaking the module

def test_prebuilt_markup_with_blank_lines_is_still_collapsed_to_one_line():
    """A blank line inside a block ends it in Markdown and the rest is then read as Markdown, so the builders collapse line breaks in
    the markup they are handed too, not only in the text they escape."""
    nasty = "<div>a</div>\n\n\n<div>b</div>\r\n\r\n<div>c</div>"
    for markup in (P.card_html("t", "h", nasty), P.grid_html("g2", [nasty, nasty]), P.card_html("t", "", P.grid_html("g3", [nasty]))):
        assert "\n" not in markup and "\r" not in markup
        assert markup.count("<div>a</div>") >= 1 and "<div>b</div>" in markup


def test_negative_counts_are_drawn_as_zero_and_never_below_the_axis():
    series = [{"date": "2026-10-01", "Sent": -50, "Replies": -9}, {"date": "2026-10-02", "Sent": 4, "Replies": -1}, {"date": "2026-10-03", "Sent": -7, "Replies": 2}]
    svg = P.area_chart_svg(series)
    ET.fromstring(svg)
    base = P.CHART_HEIGHT - P.CHART_PAD_BOTTOM
    ys = [float(y) for _, y in re.findall(r"[ML ]\s?(-?[\d.]+),(-?[\d.]+)", " ".join(re.findall(r' d="([^"]+)"', svg)))]
    assert ys and max(ys) <= base + 0.01 and min(ys) >= P.CHART_PAD_TOP - 0.01
    assert re.findall(r'text-anchor="end">([^<]*)<', svg)[-1] == "4"          # the axis is sized by the real data (max 4), not by the negatives


def test_the_stylesheet_collapses_the_grids_in_the_right_places_on_small_screens():
    css = P.PERFORMANCE_CSS
    block = lambda query: css[css.index(query):css.index("\n", css.index(query))]                  # noqa: E731
    narrow, medium = block("@media (max-width: 900px)"), block("@media (max-width: 1200px)")
    assert ".pf-g2, .pf-g2e { grid-template-columns: minmax(0, 1fr); }" in narrow and ".pf-g3 { grid-template-columns: minmax(0, 1fr); }" in narrow
    assert ".pf-g6 { grid-template-columns: repeat(2, minmax(0, 1fr)); }" in narrow
    assert ".pf-g6 { grid-template-columns: repeat(3, minmax(0, 1fr)); }" in medium and ".pf-g4 { grid-template-columns: repeat(2, minmax(0, 1fr)); }" in medium


def test_table_headers_are_plain_case_with_no_letter_spacing_because_spaced_capitals_rendered_with_gaps_inside_words():
    css = P.PERFORMANCE_CSS
    header_rule = css[css.index(".pf-table th {"):css.index("}", css.index(".pf-table th {"))]
    assert "letter-spacing: 0" in header_rule and "text-transform: none" in header_rule and "uppercase" not in header_rule
