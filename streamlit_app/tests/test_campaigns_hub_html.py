"""The Campaigns list's markup: everything from a person or a Sheet is escaped, the numbers and chart are what the rows say, and the stylesheet rules that make it look right."""
import os
import re
import sys
from datetime import datetime
from html.parser import HTMLParser

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import campaigns_hub_html as H  # noqa: E402

HOSTILE = ["<script>alert(1)</script>", '"><img src=x onerror=alert(1)>', "' onmouseover='alert(1)", "</div><div class=\"hb-row\">forged</div>", "<a href=\"javascript:alert(1)\">x</a>",
           "<svg onload=alert(1)>", "<style>body{display:none}</style>", "&lt;script&gt; &amp;amp; &#60;b&#62;", "\u202eRTL override\u202c and \x00 null", "line one\r\nline two"]
NOW = datetime(2026, 10, 15, 12, 0, 0)


class _Scan(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.tags, self.attrs, self.styles = set(), set(), []

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag)
        for name, value in attrs:
            self.attrs.add(name)
            if name == "style":
                self.styles.append(value)


def _safe(markup):
    scan = _Scan()
    scan.feed(markup)
    assert scan.tags <= {"div", "span", "b", "i"} and scan.attrs <= {"class", "style"}, (scan.tags, scan.attrs)
    assert all(re.fullmatch(r"height:\d{1,3}px", style) for style in scan.styles), scan.styles
    assert "<script" not in markup and "<img" not in markup and "<svg" not in markup and "<style" not in markup and "<a " not in markup
    assert "\u202e" not in markup and "\x00" not in markup


def _row(**kw):
    base = {"name": "Camp_A", "status": "running", "status_label": "🟢 Running", "problems": [], "total_leads": 77, "sent": 252, "replies": 51, "reply_rate": "20.2%",
            "last_activity": "2026-10-15 02:00:00", "series": {"bars": [0, 12, 40, 3], "unit": "day", "start": "2026-10-12", "end": "2026-10-15", "total": 55}}
    base.update(kw)
    return base


@pytest.mark.parametrize("ts, text", [("", "—"), (None, "—"), ("2026-10-15 11:59:30", "just now"), ("2026-10-15 11:48:00", "12 min ago"), ("2026-10-15 02:00:00", "10 hr ago"),
                                      ("2026-10-14 11:00:00", "1 day ago"), ("2026-10-13 12:00:00", "2 days ago"), ("2026-09-05 12:00:00", "40 days ago"),
                                      ("2026-10-16 12:00:00", "2026-10-16 12:00:00"), ("garbage", "garbage")])
def test_relative_times_read_naturally_and_a_bad_one_is_shown_as_it_is(ts, text):
    assert H.relative_time(ts, NOW) == text


def test_the_four_totals_say_what_they_are():
    markup = H.stats_html({"count": 5, "running": 1, "attention": 1, "leads": 126, "contacted": 126, "sent": 515, "replies": 74, "reply_rate": "58.7%"})
    assert markup.count('class="hb-stat"') == 4 and '<div class="hb-stat-v">126</div>' in markup and "58.7% of 126 leads replied" in markup and "of 5 campaigns" in markup and "14.4%" not in markup
    assert "of 1 campaign<" in H.stats_html({"count": 1, "running": 0, "attention": 0, "leads": 0, "sent": 0, "replies": 0, "reply_rate": "—"}) and "no reply rate yet" in H.stats_html({"count": 1, "running": 0, "attention": 0, "leads": 0, "sent": 0, "replies": 0, "reply_rate": "—"})


@pytest.mark.parametrize("status, css, text", [("running", "live", "● Running"), ("attention", "warn", "⚠ Attention needed"), ("paused", "paused", "⏸ Paused"), ("draft", "draft", "📝 Draft"), ("completed", "done", "✓ Completed")])
def test_each_status_has_its_own_pill(status, css, text):
    assert f'<span class="hb-chip hb-chip-{css}">{text}</span>' in H.campaign_card_html(_row(status=status), NOW)


def test_an_unknown_status_falls_back_to_a_neutral_pill_that_cannot_inject_a_class():
    markup = H.campaign_card_html(_row(status='x" onclick="y', status_label="Mystery"), NOW)
    assert 'hb-chip-done">Mystery<' in markup and "onclick" not in markup.replace("&quot;", "")


def test_a_card_shows_the_four_numbers_the_activity_and_the_chart():
    markup = H.campaign_card_html(_row(), NOW)
    for number in ("77", "252", "51", "20.2%"):
        assert f"<b>{number}</b>" in markup
    assert "Last activity 10 hr ago" in markup and '<div class="hb-spark">' in markup and "Sends per day · Oct 12 – Oct 15" in markup and "\n" not in markup


def test_a_draft_says_it_has_not_launched_and_a_campaign_with_no_sends_says_no_activity():
    assert "Not launched yet" in H.campaign_card_html(_row(status="draft", last_activity="", sent=0, series={"bars": []}), NOW)
    assert "No activity yet" in H.campaign_card_html(_row(status="attention", last_activity="", sent=0, series={"bars": []}), NOW)


def test_the_numbers_of_a_campaign_that_has_sent_nothing_are_faded_but_a_running_one_never_is():
    assert 'class="hb-metrics hb-faded"' in H.campaign_card_html(_row(status="draft", sent=0), NOW) and 'class="hb-metrics hb-faded"' in H.campaign_card_html(_row(status="attention", sent=0), NOW)
    assert 'class="hb-metrics"' in H.campaign_card_html(_row(status="running", sent=0), NOW) and 'class="hb-metrics"' in H.campaign_card_html(_row(status="completed", sent=0), NOW)


def test_what_is_wrong_is_spelled_out_as_chips():
    markup = H.campaign_card_html(_row(status="attention", problems=["No leads with an email address", "No sender account configured"]), NOW)
    assert markup.count('<span class="hb-problem">') == 2 and "No sender account configured" in markup
    assert "hb-problems" not in H.campaign_card_html(_row(problems=[]), NOW)


def test_the_tallest_bar_is_full_height_and_a_quiet_day_is_still_a_visible_sliver():
    markup = H.chart_html({"bars": [0, 10, 5, 1], "unit": "day", "start": "2026-10-01", "end": "2026-10-04"})
    assert re.findall(r'<i style="height:(\d+)px">', markup) == ["3", "36", "18", "4"]


@pytest.mark.parametrize("series", [None, {}, {"bars": []}, {"bars": None}])
def test_a_missing_or_empty_series_is_just_the_no_sends_caption(series):
    assert H.chart_html(series) == '<div class="hb-chart-none">No sends yet</div>'


def test_junk_in_the_bars_never_breaks_the_chart():
    markup = H.chart_html({"bars": [-5, 3, 0], "unit": "day", "start": "2026-10-01", "end": "2026-10-03"})
    assert re.findall(r'height:(\d+)px', markup) == ["3", "36", "3"]


@pytest.mark.parametrize("hostile", HOSTILE)
def test_hostile_text_from_a_sheet_can_never_become_markup(hostile):
    _safe(H.campaign_card_html(_row(name=hostile, problems=[hostile], status_label=hostile, status="weird", reply_rate=hostile, total_leads=hostile), NOW))
    _safe(H.empty_state_html(hostile, hostile, hostile))
    _safe(H.name_feedback_html("error", hostile))
    _safe(H.copy_list_html([(True, hostile), (False, hostile)]))
    _safe(H.source_chip_html(hostile))
    _safe(H.dialog_intro_html(hostile))
    _safe(H.page_head_html(hostile, hostile))
    _safe(H.stat_card_html(hostile, hostile, hostile))


def test_the_load_error_banner_counts_correctly():
    assert "1 campaign couldn’t be loaded</b> and is left out" in H.load_error_html(1) and "3 campaigns couldn’t be loaded</b> and are left out" in H.load_error_html(3)


@pytest.mark.parametrize("kind, css, mark", [("hint", "hint", ""), ("error", "error", "✗ "), ("ok", "ok", "✓ "), ("weird", "hint", ""), ('x" onclick="y', "hint", "")])
def test_name_feedback_is_grey_red_or_green_and_an_unknown_kind_is_grey(kind, css, mark):
    markup = H.name_feedback_html(kind, "msg")
    assert markup == f'<div class="hb-feedback hb-feedback-{css}">{mark}msg</div>'


def test_the_duplicate_dialog_lists_what_is_and_is_not_copied():
    markup = H.copy_list_html([(True, "Every template"), (False, "Leads, sends and replies")])
    assert '<div class="hb-copy-yes">✓ Every template</div>' in markup and '<div class="hb-copy-no">✗ Leads, sends and replies</div>' in markup


def _rule(selector_start):
    start = H.HUB_CSS.index(selector_start)
    return H.HUB_CSS[start:H.HUB_CSS.index("}", start)]


def test_each_campaign_card_has_a_status_colour_on_its_left_edge():
    assert "border-left: 4px solid #d1d5db" in _rule('[class*="st-key-hub_card_"] {')
    assert "#22c55e" in _rule('[class*="st-key-hub_card_running"] {') and "#f59e0b" in _rule('[class*="st-key-hub_card_attention"] {') and "dashed #93c5fd" in _rule('[class*="st-key-hub_card_draft"] {')


def test_the_card_is_a_three_part_grid_and_its_buttons_never_wrap():
    assert "grid-template-columns: minmax(230px, 1.2fr) 1.3fr 170px" in _rule(".hb-row {")
    assert "white-space: nowrap" in _rule('[class*="st-key-hub_card_"] .stButton button {')


def test_the_totals_are_four_equal_tiles_and_the_chart_bars_never_get_fat_or_vanish():
    assert "repeat(4, 1fr)" in _rule(".hb-stats {")
    bar = _rule(".hb-spark i {")
    assert "max-width: 9px" in bar and "min-width: 2px" in bar and "flex: 1 1 0" in bar


def test_the_hub_rules_are_all_scoped_to_the_hub_or_its_dialogs():
    css = re.sub(r"/\*.*?\*/", "", H.HUB_CSS, flags=re.S)
    for selector, _body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        for part in selector.split(","):
            assert re.search(r"hb-|hub_", part), f"an unscoped hub rule: {part.strip()}"


def test_the_cards_have_room_to_breathe_at_the_bottom_as_well_as_the_top():
    assert "padding: 22px 24px;" in _rule('[class*="st-key-hub_card_"] {')                                # equal top and bottom (see the card-spacing tests below)


# ================================================================ the card's spacing: the content sits in the middle of its box

def _hub_rule(start):
    i = H.HUB_CSS.index(start)
    return H.HUB_CSS[i:H.HUB_CSS.index("}", i)]


def test_a_card_has_equal_space_above_and_below_its_content_and_centres_it():
    """REGRESSION (reported, then measured): the padding was 16px on top and 20px below, plus a stray 4px margin, so the content looked pushed down onto the bottom edge."""
    rule = _hub_rule('[class*="st-key-hub_card_"] {')
    assert "padding: 22px 24px;" in rule and "margin-bottom: 12px" in rule and "min-height: 96px" in rule and "justify-content: center" in rule
    assert "padding: 22px 24px 18px" not in rule and "16px 18px 20px" not in rule


def test_streamlits_negative_margin_under_markdown_does_not_drag_the_cards_content_down():
    rule = _hub_rule('[class*="st-key-hub_card_"] [data-testid="stMarkdownContainer"], [class*="st-key-hub_card_"] [data-testid="stElementContainer"] {')
    assert "margin-bottom: 0 !important" in rule


def test_the_tiles_and_the_list_follow_one_spacing_rhythm():
    stats = _hub_rule(".hb-stats {")
    assert "gap: 16px" in stats and "margin: 20px 0 16px" in stats and "padding: 16px 20px" in _hub_rule(".hb-stat {")
