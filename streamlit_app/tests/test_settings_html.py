"""The Settings tab's markup and stylesheet: all campaign, account and Sheet text is escaped, and the rules give the cards, bars and buttons the designed look."""
import os
import re
import sys
from html.parser import HTMLParser

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import settings_html as H  # noqa: E402

HOSTILE = '<script>alert(1)</script> "><img src=x onerror=alert(1)> [a](http://evil.example) **b** \u202e \x00 x\n\ny'
SAFE_TAGS = {"div", "span", "i"}


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

def test_a_card_heading_has_a_title_and_an_optional_subtitle():
    assert H.card_heading_html("Sender accounts", "Emails rotate across the accounts you switch on.") == (
        '<div class="se-title">Sender accounts</div><div class="se-sub">Emails rotate across the accounts you switch on.</div>')
    assert "se-sub" not in H.card_heading_html("Readiness")


def test_the_danger_zone_title_is_marked_so_it_can_be_red():
    assert 'class="se-title se-danger-title"' in H.card_heading_html("Danger zone", "These can't be undone from here.", danger=True)
    assert "se-danger-title" not in H.card_heading_html("Danger zone")


def test_hostile_text_in_a_heading_is_escaped():
    _clean(H.card_heading_html(HOSTILE, HOSTILE, danger=True))


@pytest.mark.parametrize("kind, css", [("ok", "se-note-ok"), ("bad", "se-note-bad"), ("unknown", "se-note-unknown"), ("weird", "se-note-unknown"), ("", "se-note-unknown"), ('x" onclick="y', "se-note-unknown")])
def test_an_accounts_note_is_coloured_by_its_kind_and_an_unknown_kind_can_never_inject_a_class(kind, css):
    markup = H.account_info_html("sales1@kelson.agency", "Healthy", kind)
    assert f'<div class="se-acct-note {css}">Healthy</div>' in markup and '<div class="se-acct-name">sales1@kelson.agency</div>' in markup and "onclick" not in markup
    _clean(markup)


def test_hostile_text_in_an_account_is_escaped():
    _clean(H.account_info_html(HOSTILE, HOSTILE, "ok"))


def test_usage_shows_a_bar_against_the_per_account_limit_with_the_words_under_it():
    markup = H.usage_html(17, 40, 0.425)
    assert 'style="width:42%"' in markup or 'style="width:43%"' in markup
    assert '<div class="se-usage-text">17 sent today · limit 40</div>' in markup
    _clean(markup)


def test_with_no_limit_usage_is_just_the_count_and_has_no_bar():
    markup = H.usage_html(17, None, 0.0)
    assert "se-track" not in markup and '<div class="se-usage-text">17 sent today</div>' in markup


@pytest.mark.parametrize("fraction, width", [(0, 0), (0.5, 50), (1, 100), (1.7, 100), (-0.3, 0), (float("nan"), 0), (None, 0), ("lots", 0)])
def test_the_bar_is_clamped_and_a_bad_number_draws_an_empty_one(fraction, width):
    assert f'style="width:{width}%"' in H.usage_html(5, 40, fraction)


def test_hostile_text_in_usage_is_escaped():
    _clean(H.usage_html(HOSTILE, HOSTILE, 0.5))


@pytest.mark.parametrize("kind, css", [("ok", "se-line-ok"), ("warn", "se-line-warn"), ("info", "se-line-info"), ("other", "se-line-info"), ("", "se-line-info")])
def test_a_line_of_explanation_is_coloured_by_its_kind(kind, css):
    assert H.note_html("Per-account limits add up", kind) == f'<div class="se-line {css}">Per-account limits add up</div>'


def test_hostile_text_in_a_line_is_escaped():
    _clean(H.note_html(HOSTILE, "warn"))


@pytest.mark.parametrize("status, label, family", [("running", "🟢 Running", "good"), ("paused", "⏸️ Paused", "waiting"), ("attention", "⚠️ Needs attention", "bad"),
                                                   ("draft", "📝 Draft", "neutral"), ("completed", "✅ Completed", "neutral"), ("mystery", "Mystery", "neutral")])
def test_the_status_pill_uses_the_headers_colour_families_and_drops_the_emoji_for_a_dot(status, label, family):
    markup = H.status_pill_html(status, label)
    assert markup.startswith(f'<span class="se-pill se-pill-{family}">● ') and "🟢" not in markup and "⏸" not in markup and "⚠" not in markup


def test_the_status_pill_keeps_the_words_of_a_label_that_has_no_emoji():
    assert H.status_pill_html("running", "Running") == '<span class="se-pill se-pill-good">● Running</span>'


def test_hostile_text_in_the_status_pill_is_escaped():
    markup = H.status_pill_html("running", "Running " + HOSTILE)
    assert "<script" not in markup and "<img" not in markup and "&lt;script&gt;" in markup and "&lt;img" in markup


def test_the_readiness_list_has_an_icon_and_a_line_for_each_check():
    markup = H.readiness_html([{"kind": "ok", "text": "Leads imported (840)"}, {"kind": "bad", "text": "No sender account configured"}, {"kind": "info", "text": "No schedule is set"},
                               {"kind": "warn", "text": "sales2: disconnected"}])
    assert re.findall(r'se-check-icon">(.*?)</span><span>(.*?)</span>', markup) == [("✅", "Leads imported (840)"), ("❌", "No sender account configured"), ("ℹ️", "No schedule is set"), ("⚠️", "sales2: disconnected")]
    _clean(markup)


def test_an_unknown_kind_gets_the_info_icon_and_no_checks_draws_an_empty_list():
    assert "ℹ️" in H.readiness_html([{"kind": "wat", "text": "x"}]) and 'class="se-checks"></div>' in H.readiness_html([])


def test_hostile_text_in_the_readiness_list_is_escaped():
    _clean(H.readiness_html([{"kind": "ok", "text": HOSTILE}]))


# ================================================================ stylesheet

CSS = H.SETTINGS_CSS


def _rule(selector_start):
    i = CSS.index(selector_start)
    return CSS[i:CSS.index("}", i)]


def test_the_stylesheet_is_well_formed_and_loads_nothing_from_outside():
    assert CSS.count("{") == CSS.count("}") and "@import" not in CSS and "http" not in CSS and "expression(" not in CSS.lower()


def test_the_cards_are_white_rounded_and_bordered_and_the_danger_card_has_a_red_border():
    card = _rule('[class*="st-key-se_card_"] {')
    assert "background: #fff" in card and "border: 1px solid #e5e7eb" in card and "border-radius: 14px" in card
    assert "border-color: #f3c4c4" in _rule('[class*="st-key-se_card_danger"] {')


def test_the_usage_bar_is_a_thin_rounded_blue_bar_on_a_grey_track():
    assert "height: 8px" in _rule(".se-track {") and "background: #eef0f4" in _rule(".se-track {") and "background: #2563eb" in _rule(".se-track i {")


def test_the_account_note_is_red_only_when_the_account_is_disconnected():
    assert "color: #b91c1c" in _rule(".se-note-bad {") and "color: #6b7280" in _rule(".se-note-ok, .se-note-unknown {")


def test_each_account_row_is_separated_by_a_fine_rule():
    assert "border-top: 1px solid #eef0f3" in _rule('[class*="st-key-se_acctrow_"] {')


def test_the_pill_colours_match_the_headers():
    assert "color: #15803d" in _rule(".se-pill-good {") and "background: #f0fbf4" in _rule(".se-pill-good {")
    assert "color: #b45309" in _rule(".se-pill-waiting {") and "color: #b91c1c" in _rule(".se-pill-bad {")


def test_save_settings_is_the_blue_primary_button():
    assert "background: #2563eb !important" in _rule('[class*="st-key-se_save"] button {')


def test_the_danger_buttons_are_outlined_red_not_filled():
    rule = _rule('[class*="st-key-se_archive"] button, [class*="st-key-se_delete_open"] button {')
    assert "background: #fff !important" in rule and "border: 1px solid #f3c4c4 !important" in rule and "color: #b91c1c !important" in rule


def test_select_all_looks_like_a_quiet_text_link_not_a_button():
    rule = _rule('[class*="st-key-se_selectall_"] button {')
    assert "border: 0 !important" in rule and "background: transparent !important" in rule and "color: #6b7280 !important" in rule


def test_the_readiness_and_line_colours():
    assert "color: #15803d" in _rule(".se-line-ok {") and "color: #b45309" in _rule(".se-line-warn {") and "color: #4b5563" in _rule(".se-line-info {")


def test_button_rules_are_scoped_to_this_tabs_own_containers():
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)):
        for part in selector.split(","):
            if "button" in part.lower():
                assert "st-key-" in part, f"an unscoped button rule: {part.strip()}"


def test_the_stylesheet_targets_streamlits_stable_attributes_not_its_generated_class_names():
    assert not re.search(r"st-emotion|\.e[0-9a-z]{6,}\b|css-[0-9a-z]+", CSS)


def test_a_usage_with_no_bar_is_right_aligned_so_it_sits_at_the_edge_like_the_bars_do():
    assert "text-align: right" in _rule(".se-usage-plain .se-usage-text {") and "margin-left: auto" in _rule(".se-usage {") and "width: 176px" in _rule(".se-usage {")


def test_select_all_and_the_status_button_are_pushed_to_the_right_edge_of_their_rows():
    for selector in ('[class*="st-key-se_selectall_"] {', '[class*="st-key-se_statusaction"] {'):
        rule = _rule(selector)
        assert "flex-direction: column" in rule and "align-items: flex-end" in rule, selector


def test_only_the_five_cards_have_a_name_that_the_card_rules_would_also_match():
    """REGRESSION guard: an element whose name merely CONTAINS a card's name gets the card's border and padding too (it happened twice, as a stray box). Every key the Settings
    view gives an element must be matched by the card rule only if it IS one of the five cards."""
    source = open(os.path.join(os.path.dirname(__file__), "..", "settings_view.py"), encoding="utf-8").read()
    keys = re.findall(r'key=f?"([^"]+)"', source)
    assert sorted(k for k in keys if "se_card_" in k) == ["se_card_accounts", "se_card_danger", "se_card_limits", "se_card_readiness", "se_card_status"]


def test_no_element_name_is_matched_by_two_different_style_groups_by_accident():
    source = open(os.path.join(os.path.dirname(__file__), "..", "settings_view.py"), encoding="utf-8").read()
    keys = re.findall(r'key=f?"([^"]+)"', source)
    groups = ["se_save", "se_selectall_", "se_rotation_", "se_limit_", "se_acctrow_", "se_archive", "se_delete_open", "se_statusaction"]
    for key in keys:
        matched = [g for g in groups if g in key]
        assert len(matched) <= 1, (key, matched)



# ================================================================ the integration and maintenance cards

def test_a_section_label_is_small_grey_capitals_text():
    assert H.section_label_html("Integrations") == '<div class="se-section">Integrations</div>'
    _clean(H.section_label_html(HOSTILE))


@pytest.mark.parametrize("kind, css", [("on", "se-chip-on"), ("unsaved", "se-chip-unsaved"), ("off", "se-chip-off"), ("weird", "se-chip-off"), ("", "se-chip-off"), ('x" onclick="y', "se-chip-off")])
def test_a_state_pill_is_green_amber_or_grey_and_an_unknown_kind_can_never_inject_a_class(kind, css):
    markup = H.chip_html(kind, "● On")
    assert markup == f'<span class="se-chip {css}">● On</span>' and "onclick" not in markup


def test_the_head_of_an_integration_card_has_an_icon_the_title_a_state_pill_and_a_purpose():
    markup = H.tool_heading_html("🔗", "Asana sync", "One Asana task per lead — never a duplicate.", "on", "● On")
    assert '<div class="se-ico">🔗</div>' in markup and '<span class="se-title">Asana sync</span><span class="se-chip se-chip-on">● On</span>' in markup
    assert "One Asana task per lead — never a duplicate." in markup and "\n" not in markup
    _clean(markup)


def test_hostile_text_in_an_integration_card_is_escaped():
    _clean(H.tool_heading_html(HOSTILE, HOSTILE, HOSTILE, "on", HOSTILE))
    _clean(H.tool_note_html(HOSTILE))


def test_the_note_inside_a_card_is_plain_escaped_text():
    assert H.tool_note_html("Turn this on & save.") == '<div class="se-tool-note">Turn this on &amp; save.</div>'


def test_the_state_pills_use_the_headers_green_and_the_amber_of_a_warning():
    assert "color: #15803d" in _rule(".se-chip-on {") and "background: #f0fbf4" in _rule(".se-chip-on {")
    assert "color: #b45309" in _rule(".se-chip-unsaved {") and "background: #fffaf0" in _rule(".se-chip-unsaved {")
    assert "border-radius: 99px" in _rule(".se-chip {")


def test_the_icon_is_a_small_rounded_tile_and_the_section_label_small_grey_capitals():
    assert "width: 34px" in _rule(".se-ico {") and "border-radius: 10px" in _rule(".se-ico {") and "background: #eef2ff" in _rule(".se-ico {")
    assert "text-transform: uppercase" in _rule(".se-section {") and "color: #6b7280" in _rule(".se-section {")


def test_the_expanders_inside_the_cards_have_no_box_of_their_own_and_how_it_works_is_a_blue_link_line():
    rule = _rule('[class*="st-key-se_card_maint"] details, [class*="st-key-se_card_asana"] details, [class*="st-key-se_card_tracker"] details,')
    assert "border: 0 !important" in rule and "background: transparent !important" in rule and rule.count("stExpander") == 3          # the wrapper AND the inner element, in all three cards
    assert "padding-left: 0 !important" in _rule('[class*="st-key-se_card_maint"] summary, [class*="st-key-se_card_asana"] summary, [class*="st-key-se_card_tracker"] summary {')
    assert "color: #1d4ed8" in _rule('\n[class*="st-key-se_card_asana"] summary, [class*="st-key-se_card_tracker"] summary {')       # (anchored on the line start: the three-card rule above ends with the same text)


# ================================================================ the Asana card's lock, label, rules and spacing

def _css_rule(start):
    css = H.SETTINGS_CSS if hasattr(H, "SETTINGS_CSS") else H.SE_CSS
    i = css.index(start)
    return css[i:css.index("}", i)]


def test_the_lock_help_says_whether_the_box_is_locked_and_what_the_padlock_does():
    locked, unlocked = H.lock_help_html(True), H.lock_help_html(False)
    assert "Locked, so it cannot be changed by accident. Press the lock to edit." in locked and "se-lock-open" not in locked
    assert "Unlocked. Press the lock again, or Save, to lock it." in unlocked and "se-lock-open" in unlocked
    _clean(locked)
    _clean(unlocked)


def test_the_field_label_has_a_bold_name_and_an_optional_quiet_hint():
    assert H.field_label_html("Asana project name", "exact match") == '<div class="se-field-label">Asana project name <span>· exact match</span></div>'
    assert "<span>" not in H.field_label_html("Asana project name")


def test_hostile_text_in_the_field_label_is_escaped():
    _clean(H.field_label_html(HOSTILE, HOSTILE))


def test_the_rule_is_a_plain_line_with_nothing_in_it():
    assert H.rule_html() == '<div class="se-rule"></div>'


def test_the_cards_have_room_to_breathe_and_the_rule_runs_edge_to_edge():
    card = _css_rule('[class*="st-key-se_card_"] {')
    assert "padding: 22px 24px 18px" in card and "margin-bottom: 18px" in card
    rule = _css_rule(".se-rule {")
    assert "height: 1px" in rule and "margin: 16px -24px" in rule                        # -24px cancels the card's 24px side padding so the line reaches both edges


def test_the_project_box_and_its_padlock_read_as_one_control():
    box = _css_rule('[class*="st-key-se_asana_field"] [data-testid="stTextInputRootElement"] {')
    lock = _css_rule('[class*="st-key-asana_name_lock"] button {')
    assert "border-top-right-radius: 0 !important" in box and "border-bottom-right-radius: 0 !important" in box and "min-height: 42px" in box
    assert "height: 42px" in lock and "border-radius: 0 10px 10px 0 !important" in lock and "border-left: 0" in lock
    assert "gap: 0 !important" in _css_rule('[class*="st-key-se_asana_field"] [data-testid="stHorizontalBlock"] {')


def test_a_locked_box_is_grey_and_an_unlocked_one_turns_the_padlock_blue():
    assert "background: #f3f4f6 !important" in _css_rule('[class*="st-key-se_asana_field"] [data-testid="stTextInputRootElement"]:has(input:disabled) {')
    open_state = _css_rule('[class*="st-key-se_card_asana"]:has(input:not(:disabled)[aria-label="Asana project name (exact match)"]) [class*="st-key-asana_name_lock"] button {')
    assert "background: #eaf1ff" in open_state and "color: #1d4ed8" in open_state


def test_the_explanation_and_the_button_row_are_spaced_apart_in_both_cards():
    """REGRESSION (seen in a real browser): with no gap of its own the card put the label against the line and the note against the buttons."""
    assert "margin: 14px 0 0" in _css_rule('[class*="st-key-se_card_asana"] .se-tool-note, [class*="st-key-se_card_tracker"] .se-tool-note {')
    row = _css_rule('[class*="st-key-se_card_asana"] [data-testid="stHorizontalBlock"]:has([class*="st-key-asana_sync_save"]),')
    assert "margin-top: 16px" in row and 'st-key-tracker_sync_save' in row
    assert "margin: 0 0 6px" in _css_rule(".se-field-label {") and "margin: 6px 0 0" in _css_rule(".se-lock-help {")
