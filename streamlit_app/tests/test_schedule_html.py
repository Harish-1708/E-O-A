"""The Schedule tab's markup and stylesheet: all campaign and Sheet text is escaped, and the rules give the cards, day buttons and strip the designed look."""
import os
import re
import sys
from html.parser import HTMLParser

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import schedule_html as H  # noqa: E402

HOSTILE = '<script>alert(1)</script> "><img src=x onerror=alert(1)> [a](http://evil.example) **b** \u202e \x00 x\n\ny'
SAFE_TAGS = {"div", "span", "b", "table", "thead", "tbody", "tr", "th", "td", "i"}


class _Scan(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.tags, self.attrs = set(), set()

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag)
        self.attrs |= {name for name, _ in attrs}


def _clean(markup):
    scan = _Scan()
    scan.feed(markup)
    assert scan.tags <= SAFE_TAGS and scan.attrs <= {"class", "style"}, (scan.tags, scan.attrs)
    assert "<script" not in markup and "<img" not in markup and "\u202e" not in markup and "\x00" not in markup and "\n" not in markup


# ================================================================ markup

def test_the_form_heading_says_what_the_window_is_for():
    markup = H.when_heading_html()
    assert ">When emails go out<" in markup and "Auto Send only sends inside this window, in the campaign's own time zone." in markup and "\n" not in markup


def test_the_strip_has_one_block_per_hour_blue_where_sending_is_allowed_and_the_hour_number_under_each():
    blocks = [{"hour": h, "active": 9 <= h < 17} for h in range(6, 22)]
    markup = H.strip_html(blocks)
    assert markup.count('class="sc-block"') == 8 and markup.count('class="sc-block sc-on"') == 8 and "repeat(16, 1fr)" in markup
    assert [int(n) for n in re.findall(r'class="sc-hour">(\d+)<', markup)] == list(range(6, 22)) and "Sending window across the day" in markup
    _clean(markup)


def test_the_strip_follows_however_many_hours_it_is_given():
    assert "repeat(24, 1fr)" in H.strip_html([{"hour": h, "active": True} for h in range(24)]) and "repeat(0, 1fr)" in H.strip_html([])


def test_the_waits_card_lists_each_follow_up_with_its_wait_as_a_pill():
    rows = [{"stage": "Follow-up 1", "wait": "2 days", "sends_on": "2 days after the intro"}, {"stage": "Follow-up 2", "wait": "3 days", "sends_on": "3 days after follow-up 1"}]
    markup = H.waits_card_html(rows)
    assert "Follow-up waits" in markup and "Days after the previous email. Counted in calendar days in this campaign's time zone." in markup
    assert markup.count('<span class="sc-pill">') == 2 and '<td class="sc-stage">Follow-up 2</td>' in markup and "3 days after follow-up 1" in markup
    assert [h for h in re.findall(r"<th>(.*?)</th>", markup)] == ["Stage", "Wait", "Sends on"]
    _clean(markup)


def test_with_no_follow_ups_the_card_says_so_instead_of_showing_an_empty_table():
    markup = H.waits_card_html([])
    assert "<table" not in markup and "only its intro" in markup


def test_hostile_text_in_the_waits_is_shown_as_text_never_as_markup():
    _clean(H.waits_card_html([{"stage": HOSTILE, "wait": HOSTILE, "sends_on": HOSTILE}]))


def test_the_next_run_card_shows_the_time_the_wait_and_the_viewers_own_time():
    markup = H.next_run_html({"headline": "Today 2:15 PM EDT", "relative": "in 55 minutes", "viewer": "11:45 PM IST"})
    assert ">Next automatic run<" in markup and '<div class="sc-next-time">Today 2:15 PM EDT</div>' in markup and "in 55 minutes · <b>11:45 PM IST</b>" in markup
    assert "· <b>" not in H.next_run_html({"headline": "Today 2:15 PM EDT", "relative": "in 55 minutes", "viewer": ""})
    _clean(markup)


def test_with_no_next_run_the_card_says_why():
    assert "Paused: nothing is scheduled" in H.next_run_html(None, "Paused: nothing is scheduled") and "Nothing is scheduled" in H.next_run_html(None)


def test_hostile_text_in_the_next_run_card_is_escaped():
    _clean(H.next_run_html({"headline": HOSTILE, "relative": HOSTILE, "viewer": HOSTILE}))
    _clean(H.next_run_html(None, HOSTILE))


def test_the_today_card_shows_progress_the_window_the_due_counts_and_the_weekend_rule():
    markup = H.today_html(41, 100, 0.41, {"open": True, "text": "Window is open until 5:00 PM"}, 12, 37, "Weekend sends: none (Sat, Sun off)")
    assert ">Today<" in markup and '<div class="sc-count">41 / 100</div>' in markup and 'style="width:41%"' in markup
    assert 'Window is <b class="sc-open">open</b> until 5:00 PM' in markup and "Due now: <b>12</b> leads · Due tomorrow: <b>37</b> leads" in markup and "Weekend sends: none (Sat, Sun off)" in markup
    _clean(markup)


def test_a_closed_window_is_not_drawn_green_and_no_limit_shows_just_the_count():
    markup = H.today_html(7, None, 0.0, {"open": False, "text": "Window is closed right now"}, 0, 0, "x")
    assert 'class="sc-line sc-closed">Window is closed right now' in markup and 'sc-open">open' not in markup and '<div class="sc-count">7 sent</div>' in markup


@pytest.mark.parametrize("fraction, width", [(0, 0), (0.5, 50), (1, 100), (1.7, 100), (-0.3, 0), (float("nan"), 0), (None, 0), ("lots", 0)])
def test_a_bar_is_clamped_and_a_bad_number_draws_an_empty_one(fraction, width):
    assert f'style="width:{width}%"' in H.today_html(1, 5, fraction, {"open": True, "text": "x"}, 0, 0, "w")
    assert f'style="width:{width}%"' in H.week_html([{"day": "Mon", "count": 1, "fraction": fraction}])


def test_hostile_text_in_the_today_card_is_escaped_even_when_the_window_text_mentions_open():
    markup = H.today_html(1, 2, 0.5, {"open": True, "text": "<script>open()</script>"}, 1, 1, HOSTILE)
    _clean(markup)
    assert "&lt;script&gt;" in markup


def test_the_week_card_shows_a_bar_and_count_per_day_and_a_dash_for_days_not_yet_happened():
    markup = H.week_html([{"day": "Mon", "count": 100, "fraction": 1.0}, {"day": "Tue", "count": 41, "fraction": 0.41}, {"day": "Wed", "count": None, "fraction": 0.0}])
    assert ">This week<" in markup and markup.count('class="sc-week-row"') == 3
    assert re.findall(r'sc-week-count">(.*?)<', markup) == ["100", "41", "—"] and 'style="width:41%"' in markup
    _clean(markup)


def test_an_empty_week_says_so():
    assert "No send days are set." in H.week_html([])


def test_hostile_text_in_the_week_card_is_escaped():
    _clean(H.week_html([{"day": HOSTILE, "count": HOSTILE, "fraction": 0.5}]))


# ================================================================ stylesheet

CSS = H.SCHEDULE_CSS


def _rule(selector_start):
    i = CSS.index(selector_start)
    return CSS[i:CSS.index("}", i)]


def test_the_stylesheet_is_well_formed_and_loads_nothing_from_outside():
    assert CSS.count("{") == CSS.count("}") and "@import" not in CSS and "http" not in CSS and "expression(" not in CSS.lower()


def test_the_cards_are_white_rounded_and_bordered():
    for selector in (".sc-card {", '[class*="st-key-sc_when"] {'):
        rule = _rule(selector)
        assert "background: #fff" in rule and "border: 1px solid #e5e7eb" in rule and "border-radius: 14px" in rule


def test_the_send_day_checkboxes_become_equal_wide_buttons_blue_when_ticked():
    assert "display: none" in _rule('[class*="st-key-schedule_day_"] label > div:first-of-type {')                                # the tick square is hidden
    button = _rule('[class*="st-key-schedule_day_"] label {')
    assert "width: 100%" in button and "border-radius: 10px" in button and "border: 1px solid #d1d5db" in button and "justify-content: center" in button
    assert "border-color: #2563eb" in _rule('[class*="st-key-schedule_day_"] label:has(input:checked) {') and "color: #1d4ed8" in _rule('[class*="st-key-schedule_day_"] label:has(input:checked) p {')
    assert "color: #9ca3af" in _rule('[class*="st-key-schedule_day_"] label p {')                                                     # an unticked day looks switched off


def test_the_strip_is_a_grid_of_rounded_blocks_blue_when_on():
    assert "display: grid" in _rule(".sc-strip, .sc-hours {") and "height: 30px" in _rule(".sc-block {") and "background: #2563eb" in _rule(".sc-block.sc-on {")


def test_save_schedule_is_the_blue_primary_button():
    assert "background: #2563eb !important" in _rule('[class*="st-key-sc_save"] button {')


def test_button_rules_are_scoped_to_this_tabs_own_containers():
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)):
        for part in selector.split(","):
            if "button" in part.lower():
                assert "st-key-" in part, f"an unscoped button rule: {part.strip()}"


def test_the_next_run_card_has_the_soft_blue_gradient_and_the_headline_is_large():
    assert "linear-gradient(180deg, #f3f7ff, #fff)" in _rule(".sc-next {") and "font-size: 28px" in _rule(".sc-next-time {")


def test_the_open_window_is_green_and_the_closed_one_amber():
    assert "color: #15803d" in _rule(".sc-line b.sc-open {") and "color: #b45309" in _rule(".sc-closed {")


def test_the_waits_table_has_small_grey_capital_headings_and_a_pill_for_each_wait():
    assert "text-transform: uppercase" in _rule(".sc-table th {") and "border-radius: 8px" in _rule(".sc-pill {")


def test_the_stylesheet_targets_streamlits_stable_attributes_not_its_generated_class_names():
    assert not re.search(r"st-emotion|\.e[0-9a-z]{6,}\b|css-[0-9a-z]+", CSS)


def test_the_waits_table_has_only_horizontal_rules_like_the_design_not_streamlits_full_grid():
    assert "border: 0 !important" in _rule(".sc-table {") and all(side in _rule(".sc-table th, .sc-table td {") for side in ("border-left: 0 !important", "border-right: 0 !important", "border-top: 0 !important"))
    assert "border-bottom: 1px solid #e5e7eb !important" in _rule(".sc-table th { text-align") and "border-bottom: 1px solid #f0f2f5 !important" in _rule(".sc-table td { padding")


def test_the_save_row_has_room_above_it_so_it_never_touches_the_hour_strip():
    assert "margin-top: 20px" in _rule('[class*="st-key-sc_saverow"] {') and "padding-top: 9px" in _rule(".sc-save-note {")
