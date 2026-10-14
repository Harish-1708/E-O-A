"""The Sequences tab's markup and stylesheet: every piece of campaign, template or lead text is escaped, and the rules that give it the design's look."""
import os
import re
import sys
from html.parser import HTMLParser

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import sequences_html as H  # noqa: E402

HOSTILE = '<script>alert(1)</script> "><img src=x onerror=alert(1)> [a](http://evil.example) **b** \u202e \x00 x\n\ny'
SAFE_TAGS = {"div", "span", "b", "p", "br"}


class _Scan(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.tags, self.attrs = set(), set()

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag)
        self.attrs |= {name for name, _ in attrs}


def _scan(markup):
    scan = _Scan()
    scan.feed(markup)
    return scan


def _clean(markup):
    found = _scan(markup)
    assert found.tags <= SAFE_TAGS and found.attrs <= {"class"}, (found.tags, found.attrs)
    assert "<script" not in markup and "<img" not in markup and "\u202e" not in markup and "\x00" not in markup


# ================================================================ markup

def test_a_stage_card_shows_its_title_badge_and_variant_count_and_marks_the_selected_one():
    card = {"title": "Follow-up 1", "badge": "+2 days", "variants": "3 variants"}
    on, off = H.stage_card_html(card, True), H.stage_card_html(card, False)
    assert 'class="sq-stage sq-stage-on"' in on and 'class="sq-stage"' in off and "sq-stage-on" not in off
    for markup in (on, off):
        assert ">Follow-up 1<" in markup and '<span class="sq-badge">+2 days</span>' in markup and ">3 variants<" in markup and "\n" not in markup


def test_a_stage_with_no_badge_has_no_empty_badge_pill():
    assert "sq-badge" not in H.stage_card_html({"title": "Follow-up 9", "badge": "", "variants": "1 variant"}, False)


def test_hostile_text_in_a_stage_card_is_shown_as_text_never_as_markup():
    _clean(H.stage_card_html({"title": HOSTILE, "badge": HOSTILE, "variants": HOSTILE}, True))


def test_the_editor_title_is_just_the_title_because_the_unlock_box_is_the_badge():
    markup = H.editor_title_html("Intro")
    assert markup == '<div class="sq-editor-title"><span>Intro</span></div>'
    _clean(H.editor_title_html(HOSTILE))


def test_the_result_chips_show_sent_replies_and_reply_rate_in_the_designs_colours():
    markup = H.stats_chips_html({"sent": 306, "replies": 38, "rate": "12.4%"})
    assert ">Sent 306<" in markup and ">Replies 38<" in markup and ">Reply rate 12.4%<" in markup
    assert 'class="sq-chip sq-chip-blue">Sent' in markup and 'class="sq-chip sq-chip-green">Reply rate' in markup and 'class="sq-chip">Replies' in markup
    _clean(H.stats_chips_html({"sent": HOSTILE, "replies": HOSTILE, "rate": HOSTILE}))


def test_the_chips_say_which_stage_and_variant_they_describe():
    markup = H.stats_chips_html({"sent": 5, "replies": 1, "rate": "20.0%"}, "Follow-up 2 · Variant C")
    assert '<span class="sq-scope">Follow-up 2 · Variant C</span>' in markup and "sq-scope" not in H.stats_chips_html({"sent": 5, "replies": 1, "rate": "20.0%"})
    _clean(H.stats_chips_html({"sent": 1, "replies": 1, "rate": "1%"}, HOSTILE))


def test_the_placeholders_line_says_what_clicking_does():
    assert "click one to add it to the end of the body" in H.placeholders_html()


def test_the_preview_shows_who_it_is_from_and_to_the_subject_and_the_body_as_paragraphs():
    markup = H.preview_html("You (sales1)", "Maya Okafor", "Paid collaboration", "Hi Maya,\n\nOur agency works on\nmarketing.\n\nThanks,\nHarish")
    assert "From: You (sales1) → To: Maya Okafor" in markup and '<div class="sq-preview-subject">Paid collaboration</div>' in markup
    assert "<p>Hi Maya,</p><p>Our agency works on<br>marketing.</p><p>Thanks,<br>Harish</p>" in markup and "\n" not in markup


@pytest.mark.parametrize("body, expected", [("", ""), ("   ", ""), ("one", "<p>one</p>"), ("a\n\n\n\nb", "<p>a</p><p>b</p>"), ("a\r\n\r\nb", "<p>a</p><p>b</p>"), ("a\nb", "<p>a<br>b</p>"), ("\n\nlead\n\n", "<p>lead</p>")])
def test_paragraphs_follow_blank_lines_and_single_breaks_stay_breaks(body, expected):
    assert H._paragraphs(body) == expected


def test_every_line_of_a_multi_line_body_is_kept_apart_and_each_is_escaped_on_its_own():
    assert H._paragraphs("Thanks,\n<b>Harish</b>\nKelson Agency") == "<p>Thanks,<br>&lt;b&gt;Harish&lt;/b&gt;<br>Kelson Agency</p>"


def test_hostile_text_in_the_preview_is_shown_as_text_never_as_markup():
    markup = H.preview_html(HOSTILE, HOSTILE, HOSTILE, HOSTILE)
    _clean(markup)
    assert "&lt;script&gt;" in markup


def test_the_checks_card_marks_each_item_with_a_tick_or_a_warning():
    markup = H.checks_html([{"ok": True, "text": "Every placeholder has a value for this lead"}, {"ok": False, "text": "Subject is 80 characters (over 70)"}])
    assert "Check before saving" in markup
    assert '<div class="sq-check sq-ok">✓ Every placeholder has a value for this lead</div>' in markup
    assert '<div class="sq-check sq-warn">⚠ Subject is 80 characters (over 70)</div>' in markup and "\n" not in markup
    _clean(H.checks_html([{"ok": True, "text": HOSTILE}, {"ok": False, "text": HOSTILE}]))


def test_the_section_label_is_escaped():
    assert H.section_label_html("Stages") == '<div class="sq-label">Stages</div>'
    _clean(H.section_label_html(HOSTILE))


# ================================================================ stylesheet

CSS = H.SEQUENCES_CSS


def _rule(selector_start):
    i = CSS.index(selector_start)
    return CSS[i:CSS.index("}", i)]


def test_the_stylesheet_is_well_formed_and_loads_nothing_from_outside():
    assert CSS.count("{") == CSS.count("}") and "@import" not in CSS and "http" not in CSS and "expression(" not in CSS.lower()


def test_a_stage_card_is_a_white_rounded_card_and_the_selected_one_is_outlined_in_blue():
    card = _rule(".sq-stage {")
    assert "background: #fff" in card and "border: 1px solid #e5e7eb" in card and "border-radius: 12px" in card and "min-height: 68px" in card
    on = _rule(".sq-stage-on {")
    assert "border-color: #2563eb" in on and "background: #f3f7ff" in on


def test_a_stage_cards_container_is_as_tall_as_the_card_and_the_whole_card_is_the_click_target():
    """The same Streamlit quirk that made table rows overlap: the Markdown wrapper's negative margin shortens the container, so the container carries the card's
    own height and the wrapper's margin is removed."""
    container = _rule('[class*="st-key-sq_stage_"] {')
    assert "min-height: 68px" in container and "position: relative" in container and "gap: 0 !important" in container
    assert "margin-bottom: 0 !important" in _rule('[class*="st-key-sq_stage_"] [data-testid="stMarkdownContainer"] {')
    layer = _rule('[class*="st-key-sq_stage_"] [class*="st-key-sq_pick_"] {')                 # REGRESSION: without these !important sizes the layer was 16px wide and the cards ignored clicks
    assert "position: absolute !important" in layer and "inset: 0" in layer and "width: 100% !important" in layer and "height: 100% !important" in layer and "z-index: 3" in layer
    assert "width: 100% !important" in _rule('[class*="st-key-sq_stage_"] [class*="st-key-sq_pick_"] > div {') and "height: 100% !important" in _rule('[class*="st-key-sq_stage_"] [class*="st-key-sq_pick_"] > div {')
    button = _rule('[class*="st-key-sq_stage_"] [class*="st-key-sq_pick_"] button {')
    assert "width: 100%" in button and "height: 100%" in button and "opacity: 0" in button


def test_the_title_row_is_tall_enough_that_the_variant_tabs_never_collide_with_the_badge():
    assert "min-height: 40px" in _rule(".sq-editor-title {") and "margin-top: 4px" in _rule('[class*="st-key-sq_variantbar"] {')


def test_the_editor_is_a_white_card_the_unlock_box_floats_at_its_corner_as_the_badge():
    editor = _rule('[class*="st-key-sq_editor"] {')
    assert "position: relative" in editor and "border-radius: 14px" in editor and "background: #fff" in editor
    unlock = _rule('[class*="st-key-sq_unlock"] {')
    assert "position: absolute" in unlock and "top: 20px" in unlock and "right: 22px" in unlock
    assert "display: none" in _rule('[class*="st-key-sq_unlock"] label > div:first-of-type {')              # the tick square is hidden: the label is the badge
    assert "border-radius: 999px" in _rule('[class*="st-key-sq_unlock"] label {')
    assert "background: #f0fbf4" in _rule('[class*="st-key-sq_unlock"] label:has(input:checked) {')         # green when editing
    assert "margin-bottom: -16px !important" in _rule('div:has(> [class*="st-key-sq_unlock"]) {')           # and it leaves no gap above the title


def test_the_variant_tabs_are_one_connected_segmented_control_with_a_blue_selected_tab():
    group = _rule('[class*="st-key-sq_variant_"] [role="radiogroup"] {')
    assert "display: inline-flex" in group and "overflow: hidden" in group and "border: 1px solid #d1d5db" in group
    assert "background: #dde9fb" in _rule('[class*="st-key-sq_variant_"] label[data-testid="stRadioOption"]:has(input:checked) {')


def test_save_is_blue_and_delete_variant_is_red_and_every_button_rule_is_scoped_to_this_tabs_own_containers():
    assert "background: #2563eb !important" in _rule('[class*="st-key-sq_save"] button {')
    assert "color: #b91c1c !important" in _rule('[class*="st-key-sq_delete_variant"] button { border-color')
    styled = 0
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)):                     # (comments are not selectors)
        if re.search(r"\b(background|border|color|padding)\b", body):
            for part in selector.split(","):
                if "button" in part.lower():
                    styled += 1
                    assert "st-key-" in part, f"an unscoped button rule: {part.strip()}"
    assert styled >= 6


def test_the_actions_row_puts_save_and_discard_left_and_delete_variant_right():
    assert "flex-direction: row !important" in _rule('[class*="st-key-sq_actions"] {')
    assert "margin-left: auto" in _rule('[class*="st-key-sq_actions"] > div:last-child {')


def test_the_preview_and_the_checks_card_have_the_designs_card_look():
    assert "border-radius: 14px" in _rule(".sq-preview {") and "background: #fff" in _rule(".sq-preview {")
    checks = _rule(".sq-checks {")
    assert "linear-gradient(180deg, #f3f7ff, #fff)" in checks and "border: 1px solid #c7d7fb" in checks
    assert "color: #b45309" in _rule(".sq-warn {")


def test_the_stylesheet_targets_streamlits_stable_attributes_not_its_generated_class_names():
    assert not re.search(r"st-emotion|\.e[0-9a-z]{6,}\b|css-[0-9a-z]+", CSS)
