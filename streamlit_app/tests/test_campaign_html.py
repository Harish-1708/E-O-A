"""The frame of the campaign page: header chips, sub-line, and the stylesheet that makes the tab bar, buttons, inputs, filter chips,
the range control and the expanders look like the approved design."""
import os
import re
import sys
from html.parser import HTMLParser

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import campaign_html as C  # noqa: E402

HOSTILE = '<script>alert(1)</script> "><img src=x onerror=alert(1)> [a](http://evil.example) **b** \u202e \x00 x\n\ny'


class _Scan(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.tags, self.attrs, self.classes = set(), set(), set()

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag)
        self.attrs |= {n for n, _ in attrs}
        for n, v in attrs:
            if n == "class":
                self.classes |= set((v or "").split())


def _scan(markup):
    scan = _Scan()
    scan.feed(markup)
    return scan


# ================================================================ the chips and the sub-line

@pytest.mark.parametrize("status, kind", [("running", "good"), ("paused", "waiting"), ("attention", "bad"), ("deleted", "bad"), ("draft", "neutral"), ("completed", "neutral"), ("surprise", "neutral"), ("", "neutral")])
def test_the_chips_carry_the_status_colour_family(status, kind):
    assert f'class="cp-chips cp-s-{kind}"' in C.chips_html(status, ["a"])


def test_each_chip_is_shown_and_blank_ones_are_left_out():
    markup = C.chips_html("running", ["Fri · 09:00–17:00 · Chicago", "", None, "  ", "1 sender account"])
    assert markup.count('<span class="cp-chip">') == 2 and "Fri · 09:00–17:00 · Chicago" in markup and "1 sender account" in markup


def test_hostile_text_in_a_schedule_or_sender_is_shown_as_text_never_as_markup():
    for markup in (C.chips_html("running", [HOSTILE, HOSTILE]), C.chips_html(HOSTILE, ["x"]), C.subline_html(HOSTILE)):
        found = _scan(markup)
        assert found.tags <= {"div", "span"} and found.attrs <= {"class"}, (found.tags, found.attrs)
        assert "<script" not in markup and "<img" not in markup and "\n" not in markup and "\u202e" not in markup
    assert "cp-s-neutral" in C.chips_html(HOSTILE, ["x"])                       # an unknown status can never inject a class


def test_the_sub_line_is_one_escaped_line():
    assert C.subline_html("41 sent today · daily limit 100") == '<div class="cp-subline">41 sent today · daily limit 100</div>'


# ================================================================ the stylesheet

CSS = C.CAMPAIGN_SKIN_CSS


def _rule(selector_start):
    i = CSS.index(selector_start)
    return CSS[i:CSS.index("}", i)]


def test_the_stylesheet_is_well_formed_and_loads_nothing_from_outside():
    assert CSS.count("{") == CSS.count("}") and "@import" not in CSS and "http" not in CSS and "expression(" not in CSS.lower()


def test_the_page_uses_the_whole_width_with_room_under_streamlits_own_toolbar():
    rule = _rule('[data-testid="stMainBlockContainer"]:has([class*="st-key-cp_titlebar"]) {')
    assert "max-width: 100% !important" in rule and "padding: 3.25rem 2.2rem 4rem !important" in rule          # 52px, the same top as every other page


def test_the_title_bar_is_one_row_with_the_name_status_chips_and_the_action_button_pushed_right():
    assert "flex-direction: row !important; flex-wrap: wrap; align-items: center" in _rule('[class*="st-key-cp_titlebar"] {')
    assert "font-size: 26px !important" in _rule('[class*="st-key-cp_titlebar"] h1 {')
    assert '[class*="st-key-cp_titlebar"] > div:last-child { margin-left: auto; }' in CSS


@pytest.mark.parametrize("family, colour", [("good", "#15803d"), ("waiting", "#b45309"), ("bad", "#b91c1c")])
def test_the_status_pill_is_coloured_by_the_status_family(family, colour):
    assert f'[class*="st-key-cp_titlebar"]:has(.cp-s-{family}) h3 {{ color: {colour};' in CSS


def test_the_tab_bar_has_a_thin_rule_bold_labels_and_a_blue_underline_on_the_selected_tab():
    assert "border-bottom: 1px solid #e5e7eb" in _rule('[role="tablist"] {')
    assert "box-shadow: inset 0 -3px 0 #2563eb" in _rule('[role="tab"][aria-selected="true"] {')
    assert "font-weight: 600 !important" in _rule('[role="tab"] p, [role="tab"] span {')


def test_inputs_and_dropdowns_are_white_with_a_thin_border_not_streamlits_grey_fill():
    rule = _rule('[data-testid="stTextInputRootElement"], [data-testid="stSelectbox"] [role="group"]')
    assert "background: #fff !important" in rule and "border: 1px solid #d1d5db !important" in rule and "border-radius: 9px !important" in rule


def test_expanders_are_slim_white_cards():
    rule = _rule('[data-testid="stExpander"] {')
    assert "background: #fff" in rule and "border: 1px solid #e5e7eb !important" in rule and "border-radius: 12px !important" in rule


def test_button_styling_is_limited_to_this_pages_own_buttons_so_the_inboxs_blue_reply_button_is_not_overridden():
    """The Responses tab (the Inbox) lives on this same page and styles its own buttons; a colour or border rule on every button would turn its
    Reply white. (A rule that only sets the FONT of every button is harmless, and is allowed.)"""
    styled = 0
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", CSS):
        changes_look = re.search(r"\b(background|border|color|box-shadow|padding)\b", body)
        for part in selector.split(","):
            if "button" in part.lower() and changes_look:
                styled += 1
                assert "st-key-" in part, f"an unscoped button rule that changes colours or borders: {part.strip()}"
    assert styled >= 8                                                       # the rules were really found, not skipped


def test_the_back_link_looks_like_a_link_and_the_primary_action_is_blue():
    assert "color: #2563eb !important" in _rule('[class*="st-key-cp_back"] button {') and "background: transparent !important" in _rule('[class*="st-key-cp_back"] button {')
    assert "background: #2563eb !important" in _rule('[class*="st-key-cp_actions"] button[kind="primary"]')


def test_the_data_toolbar_is_one_white_card():
    rule = _rule('[class*="st-key-ld_toolbar"] {')
    assert "background: #fff" in rule and "border: 1px solid #e5e7eb" in rule and "border-radius: 14px" in rule


def test_the_status_filter_is_pills_with_the_radio_circle_hidden_and_a_blue_selected_one():
    assert "border-radius: 999px" in _rule('[class*="st-key-data_filter"] label[data-testid="stRadioOption"] {')
    assert "display: none" in _rule('[class*="st-key-data_filter"] label[data-testid="stRadioOption"] > div > div:first-child')
    selected = _rule('[class*="st-key-data_filter"] label[data-testid="stRadioOption"]:has(input:checked) {')
    assert "background: #dde9fb" in selected


def test_the_chart_range_is_one_connected_segmented_control():
    group = _rule('[class*="st-key-"][class*="_range"] [role="radiogroup"] {')
    assert "display: inline-flex" in group and "border: 1px solid #d1d5db" in group and "overflow: hidden" in group
    assert "background: #dde9fb" in _rule('[class*="st-key-"][class*="_range"] label[data-testid="stRadioOption"]:has(input:checked) {')


def test_the_stylesheet_targets_streamlits_stable_attributes_not_its_generated_class_names():
    assert not re.search(r"st-emotion|\.e[0-9a-z]{6,}\b|css-[0-9a-z]+", CSS)


# ================================================================ the second round: breadcrumb, actions, font, toolbar, add leads

def test_the_breadcrumb_name_and_the_build_stamp_are_quiet_grey_text():
    assert "color: #6b7280" in _rule(".cp-crumb-name {") and "color: #9ca3af" in _rule(".cp-build {")


def test_crumb_and_build_markup_are_escaped_one_liners():
    for markup in (C.crumb_html(HOSTILE), C.build_html(HOSTILE)):
        found = _scan(markup)
        assert found.tags <= {"div", "span"} and found.attrs <= {"class"} and "<script" not in markup and "\n" not in markup
    assert C.crumb_html("Kelson") == '<span class="cp-crumb-name">/ Kelson</span>' and C.build_html("x-1") == '<div class="cp-build">UI build x-1</div>'


def test_the_action_buttons_sit_in_one_row_and_send_now_is_the_blue_primary():
    assert "flex-direction: row !important" in _rule('[class*="st-key-cp_actions"] {')
    assert "background: #2563eb !important" in _rule('[class*="st-key-cp_actions"] [class*="st-key-cp_send"] button {')           # more specific than the general outline-button rule, which would otherwise win
    assert "justify-content: flex-start" in _rule('[class*="st-key-cp_crumb"] {')                                        # the campaign name sits right after the "Campaigns" link


def test_the_page_font_is_the_designs_system_stack_applied_to_text_only_never_to_icon_fonts():
    assert "--cp-font: Inter, -apple-system, \"Segoe UI\", Roboto" in CSS
    rule = CSS[CSS.index(".stApp [data-testid=\"stMarkdownContainer\"]"):]
    rule = rule[:rule.index("}")]
    assert "font-family: var(--cp-font) !important" in rule and "stIconMaterial" not in rule and not re.search(r"\.stApp\s*\*|\bspan\b", rule.split("{")[0])


def test_the_performance_toolbar_puts_the_range_left_and_the_buttons_right():
    assert "justify-content: space-between" in _rule('[class*="st-key-pf_toolbar_"] {')
    assert "flex-direction: row !important" in _rule('[class*="st-key-pf_tools_"] {')
    assert "border-radius: 9px !important" in _rule('[class*="st-key-pf_tools_"] button, [class*="st-key-pf_tools_"] a {')


def test_add_leads_is_the_toolbars_one_blue_button_and_closing_the_panel_is_an_outline_button():
    assert "background: #2563eb !important" in _rule('[class*="st-key-ld_add_leads"] button {')
    assert "background: #fff !important" in _rule('[class*="st-key-ld_import_close"] button {')


def test_the_build_name_is_a_real_label():
    assert re.fullmatch(r"[a-z]+-[a-z]+-\d+", C.UI_BUILD)


# ================================================================ the header's ... menu

def test_the_menu_group_labels_are_small_grey_capitals_and_the_menu_is_wide_enough_for_its_explanations():
    label = _rule(".cp-menu-label {")
    assert "text-transform: uppercase" in label and "color: #6b7280" in label and "font-size: 11.5px" in label
    assert "min-width: 340px" in _rule('[data-testid="stPopoverBody"]:has([class*="st-key-cp_menu_"]) {')


def test_the_menu_items_fill_the_menu_and_the_dividers_are_tight():
    assert "justify-content: flex-start" in _rule('[class*="st-key-cp_menu_"] button {') and "width: 100%" in _rule('[class*="st-key-cp_menu_"] button > div {')
    assert "margin: 4px 0 !important" in _rule('[data-testid="stPopoverBody"]:has([class*="st-key-cp_menu_"]) hr {')
