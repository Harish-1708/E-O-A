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


# ================================================================ table cells with a small bar

@pytest.mark.parametrize("fraction, width", [(0, 0), (0.5, 50), (1, 100), (0.333, 33), (1.7, 100), (-0.2, 0), (float("nan"), 0), (None, 0), ("lots", 0), ("0.25", 25)])
def test_a_bar_cell_is_scaled_clamped_and_never_breaks_on_a_bad_number(fraction, width):
    markup = P.table_html(["Account", "Rate"], [["a", P.bar_cell(fraction, "12.0%")]])
    assert f'class="pf-minibar"><i style="width:{width}%"></i></span><span>12.0%</span>' in markup


def test_a_bar_cells_text_is_escaped_and_the_width_can_never_carry_markup():
    markup = P.table_html(["A", "B"], [["x", P.bar_cell('"><script>alert(1)</script>', HOSTILE)]])
    assert "<script" not in markup and "&lt;script&gt;" in markup and 'style="width:0%"' in markup
    found = _scan(markup)
    assert found.tags <= SAFE_TAGS and found.attrs <= SAFE_ATTRS


def test_an_ordinary_cell_and_a_bar_cell_can_share_a_row_and_a_short_row_is_still_padded():
    markup = P.table_html(["A", "B", "C"], [["1", P.bar_cell(1, "x")], ["only"]])
    assert markup.count("<td>") == 6 and markup.count("pf-cellbar") == 1


def test_the_bar_is_styled_in_the_stylesheet():
    css = P.PERFORMANCE_CSS
    assert ".pf-cellbar { display: flex; align-items: center;" in css and ".pf-minibar i { display: block; height: 100%;" in css


def test_the_what_worked_best_box_has_room_above_it_so_it_never_touches_the_card_before_it():
    """It followed a pair of columns whose bottom edge Streamlit pulls up by about 16px: measured in a real browser it overlapped the Activity card by 2px."""
    css = P.PERFORMANCE_CSS
    rule = css[css.index(".pf-callout {"):css.index("}", css.index(".pf-callout {"))]
    assert "margin: 18px 0 14px" in rule


# ================================================================ the change line on a headline card

@pytest.mark.parametrize("kind, css", [("up", "pf-trend-up"), ("down", "pf-trend-down"), ("flat", "pf-trend-flat"), ("sideways", "pf-trend-flat"), ("", "pf-trend-flat"), ('x" onclick="y', "pf-trend-flat")])
def test_the_change_line_is_coloured_by_direction_and_an_unknown_direction_can_never_inject_a_class(kind, css):
    markup = P.kpi_html("Reply rate", "40.0%", "9 replied", "", ("▲ 2.1 pts vs previous 7 days", kind))
    assert f'<div class="pf-kpi-trend {css}">▲ 2.1 pts vs previous 7 days</div>' in markup and "onclick" not in markup and "\n" not in markup


def test_a_card_without_a_change_has_no_change_line_and_the_change_comes_after_the_caption():
    assert "pf-kpi-trend" not in P.kpi_html("Contacted", 5, "of 9") and "pf-kpi-trend" not in P.kpi_html("Contacted", 5, "of 9", "", None)
    markup = P.kpi_html("Reply rate", "40%", "9 replied", "", ("▲ 1.0 pts", "up"))
    assert markup.index("pf-kpi-caption") < markup.index("pf-kpi-trend")


def test_hostile_text_in_the_change_line_is_escaped():
    markup = P.kpi_html("Reply rate", "1", "c", HOSTILE, (HOSTILE, "up"))
    found = _scan(markup)
    assert found.tags <= SAFE_TAGS and found.attrs <= SAFE_ATTRS and "<script" not in markup and "&lt;script&gt;" in markup


def test_the_change_line_colours():
    css = P.PERFORMANCE_CSS

    def rule(selector):
        i = css.index(selector)
        return css[i:css.index("}", i)]                                   # only what is INSIDE this rule (the same colours are used by other rules)

    assert "color: #15803d" in rule(".pf-trend-up {") and "color: #b91c1c" in rule(".pf-trend-down {") and "color: #6b7280" in rule(".pf-trend-flat {")


# ================================================================ the "All campaigns at a glance" table

def _grow(**kw):
    base = {"name": "Camp_A", "status": "completed", "status_label": "✓ Completed", "leads": 39, "contacted": 39, "pending": 0, "sent": 235, "delivered": 233, "hard": 0, "soft": 2,
            "replies": 20, "reply_rate": 0.513, "reply_rate_text": "51.3%", "completion": 0.386, "completion_text": "38.6%", "completed": 15, "best": False}
    base.update(kw)
    return base


_GTOTAL = {"campaigns": 3, "leads": 126, "contacted": 126, "pending": 0, "sent": 525, "delivered": 523, "hard": 0, "soft": 2, "replies": 74, "reply_rate": 0.587, "reply_rate_text": "58.7%",
           "completion": 0.238, "completion_text": "23.8%", "completed": 30}


def test_the_table_has_the_seven_columns_one_row_per_campaign_and_a_total_row():
    markup = P.glance_table_html([_grow(), _grow(name="Camp_B")], _GTOTAL)
    for heading in ("Campaign", "Contacted", "Emails sent", "Replies", "Reply rate", "Bounces", "Sequence completion"):
        assert f"<th>{heading}</th>" in markup
    assert markup.count("<tr>") == 3 and '<tr class="pg-total">' in markup                          # the heading row and two campaign rows, then the total row
    assert "All 3 campaigns" in markup and "58.7%" in markup and "\n" not in markup


def test_a_row_shows_its_figures_with_a_note_or_a_bar_where_a_comparison_helps():
    markup = P.glance_table_html([_grow()], _GTOTAL)
    assert "39 <span class=\"pg-of\">of 39</span>" in markup and "233 delivered (est.)" in markup and "15 reached the last email" in markup and "none pending" in markup
    assert re.findall(r'style="width:(\d+)%"', markup) == ["100", "51", "39"]                      # contacted of leads, reply rate, sequence completion, in that order


def test_pending_leads_get_a_chip_and_no_pending_says_so_quietly():
    assert '<span class="pg-pend">4 pending</span>' in P.glance_table_html([_grow(leads=43, contacted=39, pending=4)], _GTOTAL)
    assert "none pending" in P.glance_table_html([_grow()], _GTOTAL)


def test_zero_bounces_are_quiet_grey_and_only_real_ones_are_coloured():
    quiet = P.glance_table_html([_grow(hard=0, soft=0)], {**_GTOTAL, "soft": 0})
    assert quiet.count("pg-bc-zero") >= 2 and "pg-bc-warn" not in quiet and "pg-bc-bad" not in quiet
    coloured = P.glance_table_html([_grow(hard=3, soft=2)], _GTOTAL)
    assert "pg-bc pg-bc-bad\">3 hard" in coloured and "pg-bc pg-bc-warn\">2 soft" in coloured


def test_only_the_best_campaign_gets_the_star():
    assert P.glance_table_html([_grow(best=True)], _GTOTAL).count("★ best rate") == 1
    assert "★" not in P.glance_table_html([_grow()], _GTOTAL)


@pytest.mark.parametrize("status, kind", [("running", "live"), ("attention", "warn"), ("draft", "draft"), ("paused", "paused"), ("completed", "done"), ("mystery", "done")])
def test_the_status_chip_uses_the_campaign_lists_colour_families_and_an_unknown_one_is_the_neutral_grey(status, kind):
    assert f'pg-chip pg-chip-{kind}"' in P.glance_table_html([_grow(status=status)], _GTOTAL)


@pytest.mark.parametrize("fraction", [0, 0.5, 1, 1.7, -0.4, None])
def test_no_bar_ever_goes_past_full_or_below_empty(fraction):
    markup = P.glance_table_html([_grow(reply_rate=fraction, completion=fraction)], _GTOTAL)
    assert all(0 <= int(w) <= 100 for w in re.findall(r'style="width:(-?\d+)%"', markup))


def test_the_total_row_says_campaign_in_the_singular_for_one():
    assert "All 1 campaign<" in P.glance_table_html([_grow()], {**_GTOTAL, "campaigns": 1})


@pytest.mark.parametrize("hostile", [HOSTILE, '"><img src=x onerror=alert(1)>', "</td></tr><tr class=\"pg-total\">forged"])
def test_hostile_text_in_a_campaign_name_or_status_can_never_become_markup(hostile):
    markup = P.glance_table_html([_grow(name=hostile, status_label=hostile)], _GTOTAL) + P.glance_header_html(hostile)
    assert "<script" not in markup and "<img" not in markup and "\u202e" not in markup and "\x00" not in markup and "forged</td>" not in markup
    assert markup.count("<td") == markup.count("</td>") and markup.count("<tr") == markup.count("</tr>")


def test_the_header_says_how_the_table_is_sorted():
    assert "All campaigns at a glance" in P.glance_header_html("Reply rate") and "Sorted by reply rate." in P.glance_header_html("Reply rate") and "Sorted by name." in P.glance_header_html("Name")


def _grule(start):
    i = P.GLANCE_CSS.index(start)
    return P.GLANCE_CSS[i:P.GLANCE_CSS.index("}", i)]


def test_the_table_sits_in_a_card_that_scrolls_sideways_instead_of_squeezing_on_a_narrow_window():
    assert "border-radius: 14px" in _grule('[class*="st-key-pg_glance"] {') and "overflow-x: auto" in _grule(".pg-scroll {")
    assert "white-space: nowrap" in _grule(".pg-table th {")


def test_the_glance_styles_are_part_of_the_performance_stylesheet_and_scoped_to_themselves():
    assert P.GLANCE_CSS in P.PERFORMANCE_CSS
    for part in P.GLANCE_CSS.split("}"):
        selector = part.split("{")[0].strip()
        if selector:
            assert re.search(r"pg-|pg_", selector), f"an unscoped rule: {selector}"


def test_the_table_has_only_horizontal_lines_between_rows_not_the_vertical_ones_streamlit_adds():
    rule = _grule(".pg-table th, .pg-table td {")
    assert "border-left: 0 !important" in rule and "border-right: 0 !important" in rule


def test_the_replies_cell_says_how_many_of_the_replies_were_positive():
    assert '<div class="pg-num">20</div><div class="pg-sub">replied · 12 positive</div>' in P.glance_table_html([_grow(positive=12)], {**_GTOTAL, "positive": 12})
    assert "replied · 0 positive" in P.glance_table_html([_grow()], _GTOTAL)                       # a row without the figure says 0, never fails


def test_the_reasons_a_campaign_needs_attention_are_amber_chips_under_its_status_and_absent_when_there_are_none():
    markup = P.glance_table_html([_grow(problems=["No leads with an email address", "No templates"])], _GTOTAL)
    assert markup.count('class="pg-problem"') == 2 and "⚠ No leads with an email address" in markup and '<div class="pg-problems">' in markup
    assert "pg-problem" not in P.glance_table_html([_grow(problems=[])], _GTOTAL).replace(".pg-problem", "") and "pg-problems" not in P.glance_table_html([_grow()], _GTOTAL)


@pytest.mark.parametrize("hostile", [HOSTILE, '"><img src=x onerror=alert(1)>'])
def test_hostile_text_in_an_attention_reason_can_never_become_markup(hostile):
    markup = P.glance_table_html([_grow(problems=[hostile])], _GTOTAL)
    assert "<script" not in markup and "<img" not in markup and "\u202e" not in markup and markup.count("<td") == markup.count("</td>")



def test_a_five_card_row_is_a_known_grid_and_unknown_grids_are_still_refused():
    assert 'class="pf-grid pf-g5"' in P.grid_html("g5", ["<i></i>"] * 5)
    with pytest.raises(ValueError):
        P.grid_html("g7", [])


def test_the_stylesheet_lays_five_cards_out_five_three_and_two_across():
    css = P.PERFORMANCE_CSS
    wide = next(line for line in css.splitlines() if line.startswith("@media (max-width: 1200px)"))
    narrow = next(line for line in css.splitlines() if line.startswith("@media (max-width: 900px)"))
    assert ".pf-g5 { grid-template-columns: repeat(5, minmax(0, 1fr)); }" in css
    assert ".pf-g5 { grid-template-columns: repeat(3, minmax(0, 1fr)); }" in wide and ".pf-g5 { grid-template-columns: repeat(2, minmax(0, 1fr)); }" in narrow



# ================================================================ unsubscribes on the Activity chart, the legend chip and the spacing wrapper

DAYS = [{"date": "2026-10-26", "Sent": 3, "Replies": 1, "Unsubscribes": 0}, {"date": "2026-10-27", "Sent": 2, "Replies": 0, "Unsubscribes": 7}, {"date": "2026-10-28", "Sent": 1, "Replies": 0, "Unsubscribes": 1}]


def test_an_html_campaigns_chart_draws_a_third_amber_line_for_unsubscribes():
    svg = P.area_chart_svg(DAYS)
    assert P.UNSUBS_COLOUR == "#d97706" and svg.count('stroke="#d97706"') == 1 and svg.count('stroke="#2563eb"') == 1 and svg.count('stroke="#16a34a"') == 1
    assert "Emails sent, replies and unsubscribes per day over 3 days; most in one day: 7." in svg


def test_a_plain_text_campaigns_chart_has_no_amber_line_and_says_so():
    svg = P.area_chart_svg([{k: v for k, v in day.items() if k != "Unsubscribes"} for day in DAYS])
    assert "#d97706" not in svg and "Emails sent, replies per day over 3 days; most in one day: 3." in svg and "unsubscribes" not in svg


def test_the_chart_scale_leaves_room_for_the_biggest_unsubscribe_day():
    top = P.nice_ceiling(7)[1]
    assert f">{top:g}<" in P.area_chart_svg(DAYS) and P.nice_ceiling(3)[1] < top


def test_a_single_day_draws_a_dot_for_each_line_including_the_amber_one():
    one = P.area_chart_svg([DAYS[1]])
    assert one.count("<circle") == 3 and 'fill="#d97706"' in one and P.area_chart_svg([{k: v for k, v in DAYS[1].items() if k != "Unsubscribes"}]).count("<circle") == 2


def test_the_amber_line_follows_the_unsubscribe_numbers():
    svg = P.area_chart_svg(DAYS)
    amber = re.search(r'<path d="(M[^"]+)" fill="none" stroke="#d97706"', svg).group(1)
    ys = [float(point.split(",")[1]) for point in amber.replace("M", "").split(" L")]
    assert len(ys) == 3 and ys[1] < ys[0] and ys[1] < ys[2]                              # the 7 is the highest point (smallest y)


def test_a_legend_chip_for_unsubscribes_is_amber_and_an_unknown_kind_gets_no_colour():
    chips = P.card_html("Activity", "", "", [("Unsubscribes", "unsubs"), ("Other", "weird")])
    assert 'class="pf-chip pf-chip-unsubs">Unsubscribes<' in chips and 'class="pf-chip">Other<' in chips
    assert ".pf-chip-unsubs { color: #b45309; border-color: #f3d3a8; background: #fff8ec; }" in P.PERFORMANCE_CSS


def test_a_card_drawn_on_its_own_can_be_given_the_same_space_the_card_rows_have():
    assert P.spaced_html('<div class="pf-card">x</div>') == '<div class="pf-spaced"><div class="pf-card">x</div></div>' and ".pf-spaced { margin: 0 0 14px; }" in P.PERFORMANCE_CSS
    assert ".pf-grid { display: grid; gap: 14px; margin: 0 0 14px; }" in P.PERFORMANCE_CSS
