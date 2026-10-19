"""The sidebar's and Home's markup: nothing from a Sheet or a person can become markup, the figures are what the summary says, and the style rules that make it look right."""
import os
import re
import sys
from datetime import datetime
from html.parser import HTMLParser

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import shell_html as H  # noqa: E402

SHELL_CSS_TEXT = H.SHELL_CSS

HOSTILE = ["<script>alert(1)</script>", '"><img src=x onerror=alert(1)>', "' onmouseover='alert(1)", "</div><div class=\"sh-run\">forged</div>", "<a href=\"javascript:alert(1)\">x</a>",
           "<svg onload=alert(1)>", "<style>body{display:none}</style>", "&lt;script&gt; &amp;amp; &#60;b&#62;", "\u202eRTL override\u202c and \x00 null"]
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
    assert scan.tags <= {"div", "span", "b", "i", "svg", "path", "rect", "circle", "table", "tr", "th", "td"}, scan.tags          # the stage table is fixed markup of ours
    assert scan.attrs <= {"class", "style", "width", "height", "viewbox", "fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "d", "x", "y", "rx"}, scan.attrs
    assert all(re.fullmatch(r"width:\d{1,3}%|flex:none|width:\d{2}%", style) for style in scan.styles), scan.styles
    assert "<script" not in markup and "<img" not in markup and "<style" not in markup and "<a " not in markup and "\u202e" not in markup and "\x00" not in markup


def _tcard(**kw):
    base = {"name": "Kelson_17SEP", "window_kind": "shut", "window_pill": "○ Closed · opens Fri 16 Oct, 9:00 AM EDT (6:30 PM IST)", "window_state": "Closed",
            "window_note": "Current time 08:47 America/New_York is outside the sending window (9:00-18:00)", "due": 0, "waiting": 10, "next_due": "Fri 16 Oct", "sent_today": 0, "limit": 200,
            "percent": 0, "summary": "Nothing is due right now. The next lead falls due on Fri 16 Oct.", "in_sequence": 10, "total_leads": 77, "out_of_play": [("Replied", 51), ("Completed the sequence", 8)],
            "stages": []}
    base.update(kw)
    return base


def test_the_brand_and_the_user_card_say_who_and_what():
    assert "Email Outreach" in H.brand_html() and "Control panel" in H.brand_html() and "\n" not in H.brand_html()
    card = H.user_card_html("Harish")
    assert 'sh-user-av">H<' in card and "<b>Harish</b>" in card and "Signed in" in card


@pytest.mark.parametrize("name, letter", [("harish", "H"), ("", "?"), (None, "?"), ("  x", "X"), ("émile", "É")])
def test_the_avatar_shows_the_first_letter_of_the_name_in_capitals(name, letter):
    assert f'sh-user-av">{letter}<' in H.user_card_html(name)


def test_the_greeting_block_has_the_greeting_and_the_date_line():
    markup = H.greeting_html("Good evening, Harish", "Thursday, October 15")
    assert 'sh-greet">Good evening, Harish<' in markup and "Thursday, October 15 · here is where things stand." in markup


@pytest.mark.parametrize("kind, color", [("unread", "#2563eb"), ("account", "#dc2626"), ("campaign", "#d97706"), ("weird", "#d97706")])
def test_each_attention_card_has_its_own_icon_colour_and_an_unknown_kind_gets_the_warning_one(kind, color):
    assert f'stroke="{color}"' in H.attention_card_html({"kind": kind, "title": "T", "detail": "D"})


def test_an_attention_card_shows_its_title_and_detail():
    markup = H.attention_card_html({"kind": "unread", "title": "49 unread replies", "detail": "Across 2 campaigns."})
    assert "<span>49 unread replies</span>" in markup and 'sh-att-d">Across 2 campaigns.<' in markup and "\n" not in markup


def test_all_clear_says_what_is_clear():
    assert "All clear" in H.all_clear_html() and "every account is connected" in H.all_clear_html()


@pytest.mark.parametrize("hostile", HOSTILE)
def test_hostile_text_can_never_become_markup(hostile):
    _safe(H.user_card_html(hostile))
    _safe(H.greeting_html(hostile, hostile))
    _safe(H.attention_card_html({"kind": hostile, "title": hostile, "detail": hostile}))
    _safe(H.today_card_html(_tcard(name=hostile, window_pill=hostile, window_note=hostile, summary=hostile, next_due=hostile, out_of_play=[(hostile, 2)])))
    _safe(H.stage_table_html([{"label": hostile, "stage": hostile, "due_now": 1, "waiting": 2, "next_due_text": hostile}]))
    _safe(H.guide_card_html(hostile, hostile, hostile))
    _safe(H.stat_card_html(hostile, H.esc(hostile), hostile))
    _safe(H.load_error_html(hostile))
    _safe(H.section_html(hostile))


def test_the_guide_card_has_an_icon_a_title_and_a_line_and_an_unknown_icon_draws_nothing():
    markup = H.guide_card_html("chat", "Responses", "Every reply in one inbox.")
    assert "<svg" in markup and 'sh-gt">Responses<' in markup and "Every reply in one inbox." in markup and "<path" not in H.guide_card_html("nope", "T", "D")


def test_while_loading_there_is_a_shimmer_and_when_it_fails_the_message_says_the_rest_still_works():
    assert H.skeleton_html().count('class="sh-skel"') == 3 and "The rest of this page still works." in H.load_error_html("boom") and "boom" in H.load_error_html("boom")


def _rule(selector_start):
    start = H.SHELL_CSS.index(selector_start)
    return H.SHELL_CSS[start:H.SHELL_CSS.index("}", start)]


def test_the_sidebar_is_white_with_a_right_edge():
    rule = _rule('[data-testid="stSidebar"] {')
    assert "background: #fff" in rule and "border-right: 1px solid #e5e7eb" in rule


def test_the_user_card_is_pushed_to_the_bottom_by_its_wrapper_not_by_every_block():
    """REGRESSION (seen in a real browser): the first version gave EVERY nested block in the sidebar a full-screen height, so only 'Home' showed."""
    css = H.SHELL_CSS
    top = _rule('[data-testid="stSidebarUserContent"] > div > [data-testid="stVerticalBlock"] {')
    assert "min-height: calc(100vh - 7rem)" in top and "gap: 0 !important" in top
    assert "margin-top: auto" in _rule('[data-testid="stSidebarUserContent"] > div > [data-testid="stVerticalBlock"] > [data-testid="stLayoutWrapper"]:has([class*="st-key-nav_user"]) {')
    assert not re.search(r'stSidebarUserContent"\] \[data-testid="stVerticalBlock"\] \{[^}]*min-height', css)


def test_the_page_you_are_on_is_highlighted_blue_with_a_bar_on_its_left_edge():
    """The highlight hangs on the container the app itself marks '__active': Streamlit leaves no stable mark on the active link (a real browser showed aria-current is absent)."""
    link = '[class*="st-key-nav_"][class*="__active"] a[data-testid="stPageLink-NavLink"]'
    assert "background: #eaf1ff !important" in _rule(link + " {") and "color: #1d4ed8 !important" in _rule(link + " {")
    bar = _rule(link + "::before {")
    assert "width: 4px" in bar and "background: #2563eb" in bar
    assert "aria-current" not in H.SHELL_CSS


def test_the_cards_on_home_have_a_coloured_left_edge_and_equal_minimum_heights():
    assert "#2563eb" in _rule('[class*="st-key-home_att_unread"] {') and "#dc2626" in _rule('[class*="st-key-home_att_account"] {') and "#22c55e" in _rule('[class*="st-key-home_today_"] {') and "#9ca3af" in _rule('[class*="st-key-home_today_shut"] {')
    assert "min-height: 128px" in _rule('[class*="st-key-home_att_"] {') and "min-height: 196px" in _rule('[class*="st-key-home_guide_"] {')


def test_the_links_in_the_cards_are_blue_and_their_stray_icons_are_hidden():
    assert "color: #1d4ed8 !important" in _rule('[class*="st-key-home_att_"] a p, [class*="st-key-home_guide_"] a p')
    assert "display: none" in _rule('[class*="st-key-home_att_"] a [data-testid="stIconEmoji"]')


def test_a_button_used_as_a_link_inside_a_card_has_no_box_of_its_own():
    rule = _rule('[class*="st-key-home_att_"] .stButton button {')
    assert "border: 0 !important" in rule and "background: transparent !important" in rule


def test_the_quick_action_links_look_like_buttons_and_the_row_is_centred():
    rule = _rule('[class*="st-key-home_qa_"] a[data-testid="stPageLink-NavLink"] {')
    assert "border: 1px solid #d1d5db" in rule and "min-height: 40px" in rule and "align-items: center" in _rule('[class*="st-key-home_qa_row"] [data-testid="stHorizontalBlock"] {')


def test_the_four_figures_are_equal_tiles():
    assert "repeat(4, 1fr)" in _rule(".sh-stats {")


def test_no_button_or_link_is_named_like_a_card():
    """REGRESSION: a button named home_att_open_campaign picked up the CARD style because its name contained the card prefix. Only the card containers may use those prefixes."""
    source = open(os.path.join(os.path.dirname(__file__), "..", "shell_view.py"), encoding="utf-8").read()
    prefixes = ("home_att_", "home_today_", "home_guide_")
    button_keys = re.findall(r'st\.button\([^\n]*?key=f?"([^"{]+)', source)
    assert button_keys, "expected to find the buttons' keys"
    assert not [key for key in button_keys if key.startswith(prefixes)], button_keys
    card_keys = re.findall(r'st\.container\(key=f?"(home_[a-z_]+)', source)
    assert sorted(set(card_keys)) == ["home_att_", "home_guide_", "home_qa_responses", "home_qa_row", "home_today_"]


# ================================================================ the page frame: every page starts right under Streamlit's toolbar

def test_every_page_starts_52px_down_instead_of_96():
    """Streamlit adds 96px of padding above a page (its 60px toolbar is inside that), so a title used to start 128px down."""
    rule = _rule('[data-testid="stMainBlockContainer"] {')
    assert "padding-top: 3.25rem !important" in rule                                              # 3.25rem = 52px


def test_the_toolbar_stays_but_stops_painting_a_band_over_the_page():
    assert "background: transparent !important" in _rule('[data-testid="stHeader"] {')


def test_the_sidebars_collapse_arrows_sit_beside_the_brand_instead_of_in_a_band_above_it():
    rule = _rule('[data-testid="stSidebarHeader"] {')
    assert "position: absolute" in rule and "height: auto" in rule and "right: 6px" in rule


def test_a_block_holding_only_a_stylesheet_is_taken_out_of_the_layout_but_one_with_content_is_not():
    """REGRESSION (measured in a real browser): the pages inject their styles as invisible blocks, and each still took a 16px gap, pushing every title 32px down."""
    css = SHELL_CSS_TEXT
    assert ':has(> [data-testid="stMarkdown"] [data-testid="stMarkdownContainer"] > style:only-child) { display: none !important; }' in css
    assert "style:only-child" in css and ":has(style)" not in css                                    # only-child: a block with a stylesheet AND visible content stays visible


def test_the_campaign_page_uses_the_same_top_padding_as_every_other_page():
    import campaign_html
    assert "padding: 3.25rem 2.2rem 4rem !important" in campaign_html.CAMPAIGN_SKIN_CSS and "3.4rem" not in campaign_html.CAMPAIGN_SKIN_CSS



# ================================================================ Today, as a part of Home

_TILES = {"running": 2, "due": 14, "waiting": 27, "sent_today": 61, "limit_total": 300, "open": 1}


def test_the_four_totals_say_what_they_are_and_due_now_turns_amber_only_when_something_is_due():
    markup = H.today_tiles_html(_TILES)
    assert markup.count('class="sh-stat"') == 4 and 'sh-stat-v sh-due">14<' in markup and "Waiting on a follow-up date" in markup and "of 300 daily limit" in markup
    assert '1 <span class="sh-of">of 2</span>' in markup and "Sending open right now" in markup and "\n" not in markup
    assert "sh-due" not in H.today_tiles_html({**_TILES, "due": 0})


def test_with_no_limit_anywhere_the_sent_tile_says_across_running_campaigns():
    assert "of 300 daily limit" not in H.today_tiles_html({**_TILES, "limit_total": None}) and H.today_tiles_html({**_TILES, "limit_total": None}).count("across running campaigns") == 3


def test_a_card_shows_its_window_what_is_due_and_waiting_and_a_summary_with_the_sequence_line():
    markup = H.today_card_html(_tcard())
    assert 'sh-tc-name">Kelson_17SEP<' in markup and "● Running" in markup and 'sh-win sh-win-shut">○ Closed · opens Fri 16 Oct, 9:00 AM EDT (6:30 PM IST)<' in markup
    assert 'sh-tc-l">Due now</div><div class="sh-tc-v">0<' in markup and 'sh-tc-l">Waiting</div><div class="sh-tc-v">10<' in markup and "next lead due Fri 16 Oct" in markup
    assert "outside the sending window (9:00-18:00)" in markup and "Nothing is due right now. The next lead falls due on Fri 16 Oct." in markup
    assert "In the sequence: 10 · Out of play: Replied 51, Completed the sequence 8" not in markup and "In the sequence: 10 · Out of play: Replied 51 · Completed the sequence 8 · Total leads: 77" in markup
    assert "\n" not in markup


def test_today_is_shown_against_the_daily_limit_as_a_bar_or_as_a_plain_count_with_none():
    limited = H.today_card_html(_tcard(sent_today=61, limit=100, percent=61))
    assert '61 <span class="sh-of">/ 100</span>' in limited and 'style="width:61%"' in limited
    assert 'sh-tc-v">7<' in H.today_card_html(_tcard(sent_today=7, limit=None)) and "no daily limit" in H.today_card_html(_tcard(sent_today=7, limit=None)) and "sh-bar" not in H.today_card_html(_tcard(sent_today=7, limit=None)).replace(".sh-bar", "")


@pytest.mark.parametrize("percent, width", [(0, 0), (100, 100), (250, 100), (-5, 0), (None, 0)])
def test_the_bar_never_goes_past_full_or_below_empty(percent, width):
    assert f'style="width:{width}%"' in H.today_card_html(_tcard(limit=100, percent=percent))


@pytest.mark.parametrize("kind", ["open", "shut", "unknown", "mystery", None])
def test_the_window_pill_has_a_colour_family_and_an_unknown_kind_cannot_inject_a_class(kind):
    markup = H.today_card_html(_tcard(window_kind=kind))
    assert re.search(r'sh-win sh-win-(open|shut|unknown)"', markup) and "mystery" not in markup


def test_a_card_with_something_due_shows_it_in_amber_and_a_note_only_when_there_is_one():
    assert 'sh-tc-v sh-due">14<' in H.today_card_html(_tcard(due=14)) and "sh-due" not in H.today_card_html(_tcard(due=0))
    assert "next lead due" not in H.today_card_html(_tcard(next_due="")) and "outside the sending window" not in H.today_card_html(_tcard(window_note=""))


def test_the_sequence_line_leaves_out_who_is_out_of_play_when_nobody_is():
    assert "Out of play" not in H.today_card_html(_tcard(out_of_play=[]))


def test_the_stage_table_lists_each_stage_with_its_figures_and_a_dash_for_no_date():
    markup = H.stage_table_html([{"label": "Follow-up 4", "stage": "followup4", "due_now": 0, "waiting": 4, "next_due_text": "Fri 16 Oct"}, {"stage": "intro", "due_now": 1, "waiting": 0, "next_due_text": ""}])
    assert "<th>Stage</th><th>Due now</th><th>Waiting</th><th>Next lead due</th>" in markup and "<td>Follow-up 4</td><td>0</td><td>4</td><td>Fri 16 Oct</td>" in markup
    assert "<td>intro</td><td>1</td><td>0</td><td>—</td>" in markup


def test_a_closed_card_has_a_grey_edge_and_an_open_one_green_and_due_is_amber():
    assert "border-left: 4px solid #22c55e" in _rule('[class*="st-key-home_today_"] {') and "border-left-color: #9ca3af" in _rule('[class*="st-key-home_today_shut"] {')
    assert "color: #b45309 !important" in _rule(".sh-due {") and "grid-template-columns: repeat(4, 1fr)" in _rule(".sh-tc-grid {")


def test_the_stage_table_has_no_vertical_lines_and_streamlits_gap_under_markdown_does_not_squeeze_the_card():
    assert "border-left: 0 !important" in _rule(".sh-stages th, .sh-stages td {")
    assert "margin-bottom: 0 !important" in _rule('[class*="st-key-home_today_"] [data-testid="stMarkdownContainer"], [class*="st-key-home_today_"] [data-testid="stElementContainer"] {')


def test_the_old_figures_and_the_running_now_card_are_gone():
    assert not hasattr(H, "stats_html") and not hasattr(H, "running_card_html") and "sh-run" not in H.SHELL_CSS and "home_run_" not in H.SHELL_CSS


def test_a_today_card_has_room_between_its_parts_and_around_its_dropdowns():
    """REGRESSION (reported, then measured in a real browser): the summary, the 'Stage breakdown' dropdown and the 'Who is due now' dropdown had 0px between them."""
    card = _rule('[class*="st-key-home_today_"] {')
    assert "gap: 12px !important" in card and "padding: 22px 24px" in card and "margin-bottom: 14px" in card and "gap: 0 !important" not in card
    box = _rule('[class*="st-key-home_today_"] [data-testid="stExpander"] {')
    assert "border: 1px solid #e5e7eb !important" in box and "border-radius: 10px !important" in box and "background: #fafbfc" in box
    assert "padding: 10px 14px" in _rule('[class*="st-key-home_today_"] [data-testid="stExpander"] summary {')


def test_the_figures_and_the_summary_line_inside_a_card_are_spaced_apart():
    assert "margin-top: 18px" in _rule(".sh-tc-grid {") and "margin-top: 18px; padding-top: 16px" in _rule(".sh-tc-foot {") and "margin-top: 6px" in _rule(".sh-tc-seq {")
