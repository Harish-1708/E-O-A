"""The standalone Responses page's header markup, Replies menu text and styles."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import responses_page_html as H  # noqa: E402

HOSTILE = ['<script>alert(1)</script>', '"><img src=x onerror=alert(1)>', "</div><div class=\"rp-group\">forged</div>", "\u202eRTL\u202c and \x00 null", "&lt;b&gt;"]


def test_the_title_block_has_the_icon_the_title_and_the_line_under_it():
    markup = H.title_html()
    assert '<h1>Responses</h1>' in markup and "Every reply across every campaign, in one place." in markup and "💬" in markup


@pytest.mark.parametrize("hostile", HOSTILE)
def test_hostile_text_can_never_become_markup(hostile):
    for markup in (H.title_html(hostile, hostile), H.group_label_html(hostile)):
        assert "<script" not in markup and "<img" not in markup and "\u202e" not in markup and "\x00" not in markup and 'class="rp-group">forged' not in markup
        assert markup.count("<div") == markup.count("</div>")


def test_the_group_label_is_a_small_capital_heading():
    assert H.group_label_html("Replies") == '<div class="rp-group">Replies</div>'


def test_the_menu_explains_both_actions_in_plain_words():
    assert "Asks every campaign's mailbox for new replies" in H.CHECK_REPLIES_HELP and "within a minute or two" in H.CHECK_REPLIES_HELP
    assert "Saves what you have read to the Sheet" in H.SYNC_HELP and "up to date" in H.NOTHING_TO_SYNC


def _rule(start):
    i = H.RESPONSES_PAGE_CSS.index(start)
    return H.RESPONSES_PAGE_CSS[i:H.RESPONSES_PAGE_CSS.index("}", i)]


def test_the_all_unread_choice_is_drawn_as_one_bordered_switch_with_the_chosen_side_blue_and_no_round_buttons():
    group = _rule('[class*="st-key-responses_inbox_chip"] [role="radiogroup"] {')
    assert "display: inline-flex" in group and "border: 1px solid #d1d5db" in group and "overflow: hidden" in group
    chosen = _rule('[class*="st-key-responses_inbox_chip"] label[data-testid="stRadioOption"]:has(input:checked) {')
    assert "background: #eaf1ff" in chosen and "color: #1d4ed8" in chosen
    assert "display: none" in _rule('[class*="st-key-responses_inbox_chip"] label[data-testid="stRadioOption"] > div > div:first-child {')   # the round dot sits one level inside the label


def test_the_header_and_the_filter_row_centre_their_contents():
    rule = _rule('[class*="st-key-rp_head"] [data-testid="stHorizontalBlock"], [class*="st-key-rp_toolbar"] [data-testid="stHorizontalBlock"] {')
    assert "align-items: center" in rule


def test_every_rule_is_scoped_to_this_page():
    import re
    for part in H.RESPONSES_PAGE_CSS.split("}"):
        selector = part.split("{")[0].strip()
        if selector:
            assert re.search(r"rp-|rp_|responses_inbox_chip", selector), f"an unscoped rule: {selector}"
