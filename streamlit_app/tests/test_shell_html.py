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
    assert scan.tags <= {"div", "span", "b", "i", "svg", "path", "rect", "circle"}, scan.tags
    assert scan.attrs <= {"class", "style", "width", "height", "viewbox", "fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "d", "x", "y", "rx"}, scan.attrs
    assert all(re.fullmatch(r"width:\d{1,3}%|flex:none|width:\d{2}%", style) for style in scan.styles), scan.styles
    assert "<script" not in markup and "<img" not in markup and "<style" not in markup and "<a " not in markup and "\u202e" not in markup and "\x00" not in markup


def _summary(**kw):
    base = {"campaigns": 5, "running": 1, "sent_today": 41, "limit_total": 100, "unread_total": 49, "accounts": {"total": 3, "connected": 2, "bad": 1, "last_checked": "2026-10-15 11:58:00", "bad_accounts": []}}
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


def test_the_four_figures_say_what_they_are():
    markup = H.stats_html(_summary(), NOW)
    assert len(re.findall(r'<div class="sh-stat">', markup)) == 4 and 'sh-stat-v">1<' in markup and 'sh-stat-v">41<' in markup and 'sh-stat-v">49<' in markup
    assert "of 5 campaigns" in markup and "of 100 daily limit" in markup and "unread conversations" in markup and '2 <span class="sh-of">of 3</span>' in markup and "checked 2 min ago" in markup


@pytest.mark.parametrize("summary, text", [({"limit_total": None}, "across all campaigns"), ({"campaigns": 1}, "of 1 campaign<"), ({"accounts": {"total": 0, "connected": 0, "bad": 0, "last_checked": "", "bad_accounts": []}}, "not checked yet")])
def test_the_figures_adapt_their_wording_to_what_is_known(summary, text):
    assert text in H.stats_html(_summary(**summary), NOW)


def test_a_running_campaign_shows_today_against_its_limit_as_a_bar():
    markup = H.running_card_html({"name": "Live", "sent_today": 41, "limit": 100, "percent": 41, "next_send": "Fri 9:43 AM (in 2 d)"})
    assert "<b>41</b> of 100 sent today" in markup and 'style="width:41%"' in markup and "Fri 9:43 AM (in 2 d)" in markup and "● Running" in markup


@pytest.mark.parametrize("percent, width", [(0, 0), (100, 100), (250, 100), (-5, 0), (None, 0)])
def test_the_bar_never_goes_past_full_or_below_empty(percent, width):
    assert f'style="width:{width}%"' in H.running_card_html({"name": "L", "sent_today": 1, "limit": 100, "percent": percent, "next_send": ""})


def test_a_campaign_with_no_limit_says_so_and_has_no_bar_and_an_unknown_next_send_is_said_plainly():
    markup = H.running_card_html({"name": "L", "sent_today": 7, "limit": None, "percent": 0, "next_send": ""})
    assert "<b>7</b> sent today · no daily limit" in markup and "sh-bar" not in markup.replace(".sh-bar", "") and "not scheduled in the next 8 days" in markup


@pytest.mark.parametrize("hostile", HOSTILE)
def test_hostile_text_can_never_become_markup(hostile):
    _safe(H.user_card_html(hostile))
    _safe(H.greeting_html(hostile, hostile))
    _safe(H.attention_card_html({"kind": hostile, "title": hostile, "detail": hostile}))
    _safe(H.running_card_html({"name": hostile, "sent_today": 1, "limit": 10, "percent": 10, "next_send": hostile}))
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
    assert "#2563eb" in _rule('[class*="st-key-home_att_unread"] {') and "#dc2626" in _rule('[class*="st-key-home_att_account"] {') and "#22c55e" in _rule('[class*="st-key-home_run_"] {')
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
    prefixes = ("home_att_", "home_run_", "home_guide_")
    button_keys = re.findall(r'st\.button\([^\n]*?key=f?"([^"{]+)', source)
    assert button_keys, "expected to find the buttons' keys"
    assert not [key for key in button_keys if key.startswith(prefixes)], button_keys
    card_keys = re.findall(r'st\.container\(key=f?"(home_[a-z_]+)', source)
    assert sorted(set(card_keys)) == ["home_att_", "home_guide_", "home_qa_responses", "home_qa_row", "home_qa_today", "home_run_"]
