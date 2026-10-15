"""The Email Accounts page's markup: what each card says, that nothing from a Sheet can become markup, and the stylesheet rules that make it look right."""
import os
import re
import sys
from datetime import datetime
from html.parser import HTMLParser

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import email_accounts_html as H  # noqa: E402

HOSTILE = ["<script>alert(1)</script>", '"><img src=x onerror=alert(1)>', "' onmouseover='alert(1)", "</div><div class=\"ea-row\">forged</div>", "<a href=\"javascript:alert(1)\">x</a>",
           "<svg onload=alert(1)>", "<style>body{display:none}</style>", "&lt;script&gt; &amp;amp; &#60;b&#62;", "\u202eRTL override\u202c and \x00 null"]
NOW = datetime(2026, 10, 15, 0, 0, 0)


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
    assert scan.tags <= {"div", "span", "b"} and scan.attrs <= {"class", "style"}, (scan.tags, scan.attrs)
    assert all(re.fullmatch(r"background:#[0-9a-fA-F]{6}", style) for style in scan.styles), scan.styles
    assert "<script" not in markup and "<img" not in markup and "<svg" not in markup and "<style" not in markup and "<a " not in markup
    assert "\u202e" not in markup and "\x00" not in markup


def _card(**kw):
    base = {"name": "sales1", "address": "harish@kelson.agency", "initial": "H", "kind": "ok", "color": "#2563eb", "status": "Connected", "detail": "", "checked_at": "2026-10-14 23:58:00",
            "sent_today": 41, "is_default": True, "managed": True, "slot": 1}
    base.update(kw)
    return base


def test_a_card_says_who_the_account_is_how_it_is_doing_and_how_much_it_sent():
    markup = H.account_card_html(_card(), NOW)
    assert 'class="ea-av" style="background:#2563eb">H<' in markup and 'ea-addr">harish@kelson.agency<' in markup and '<span class="ea-chip">sales1</span>' in markup
    assert '<span class="ea-chip ea-chip-info">⭐ Default</span>' in markup and '<span class="ea-slot">Slot 1</span>' in markup
    assert '<span class="ea-chip ea-chip-ok">● Connected</span>' in markup and "Checked 2 min ago" in markup and "<b>41</b><span>sent today</span>" in markup and "\n" not in markup


def test_an_account_that_is_not_the_default_has_no_star():
    assert "Default" not in H.account_card_html(_card(is_default=False), NOW)


@pytest.mark.parametrize("kind, css, text", [("ok", "ok", "● Connected"), ("bad", "bad", "● Disconnected"), ("unknown", "unknown", "○ Unknown"), ("weird", "unknown", "○ Unknown"), ('x" onclick="y', "unknown", "○ Unknown")])
def test_each_health_has_its_own_pill_and_an_unknown_one_cannot_inject_a_class(kind, css, text):
    markup = H.account_card_html(_card(kind=kind), NOW)
    assert f'<span class="ea-chip ea-chip-{css}">{text}</span>' in markup and "onclick" not in markup


def test_a_disconnected_account_shows_the_reason_and_a_connected_one_never_does():
    assert '<div class="ea-why">Login failed</div>' in H.account_card_html(_card(kind="bad", detail="Login failed"), NOW)
    assert "ea-why" not in H.account_card_html(_card(kind="ok", detail="stale"), NOW) and "ea-why" not in H.account_card_html(_card(kind="bad", detail=""), NOW)


def test_an_account_this_app_cannot_manage_is_marked_legacy_and_has_no_slot():
    markup = H.account_card_html(_card(managed=False, slot=None, is_default=False), NOW)
    assert '<span class="ea-chip">Legacy</span>' in markup and "ea-slot" not in markup


def test_an_account_never_checked_says_so():
    assert "Not checked yet" in H.account_card_html(_card(checked_at="", kind="unknown"), NOW)


@pytest.mark.parametrize("color, used", [("#2563eb", "#2563eb"), ("#ABCDEF", "#ABCDEF"), ("red", "#9ca3af"), ('#fff;background:url(x)', "#9ca3af"), (None, "#9ca3af"), ("#12345", "#9ca3af")])
def test_the_avatar_colour_can_only_ever_be_a_plain_hex_colour(color, used):
    assert f'style="background:{used}"' in H.account_card_html(_card(color=color), NOW)


def test_the_four_figures_above_the_list_say_what_they_are():
    summary = {"total": 4, "connected": 2, "bad": 1, "unknown": 1, "sent_today": 53, "last_checked": "2026-10-14 23:58:00"}
    markup = H.stats_html(summary, NOW)
    assert len(re.findall(r'<div class="ea-stat(?: ea-stat-bad)?">', markup)) == 4                                    # four tiles
    assert '2 <span class="ea-of">of 4</span>' in markup and 'ea-stat-v">1<' in markup and 'ea-stat-v">53<' in markup and "2 min ago" in markup and "automatic checks every 2 hours" in markup
    assert "ea-stat ea-stat-bad" in markup and "ea-stat ea-stat-bad" not in H.stats_html({**summary, "bad": 0}, NOW)
    assert "Not yet" in H.stats_html({**summary, "last_checked": ""}, NOW)


@pytest.mark.parametrize("hostile", HOSTILE)
def test_hostile_text_from_a_sheet_can_never_become_markup(hostile):
    _safe(H.account_card_html(_card(name=hostile, address=hostile, initial=hostile, detail=hostile, kind="bad", slot=hostile, checked_at=hostile, sent_today=3), NOW))
    _safe(H.empty_state_html(hostile, hostile))
    _safe(H.dialog_note_html(hostile))
    _safe(H.warn_box_html(hostile))
    _safe(H.page_head_html(hostile, hostile))


def test_the_security_banner_keeps_the_meaning_of_the_old_paragraph():
    banner = H.security_banner_html()
    assert "Passwords are never stored, logged or shown." in banner and "encrypt it and send it to GitHub" in banner


def _rule(selector_start):
    start = H.EA_CSS.index(selector_start)
    return H.EA_CSS[start:H.EA_CSS.index("}", start)]


def test_a_disconnected_card_is_tinted_red():
    assert "border-color: #f3c4c4" in _rule('[class*="st-key-ea_card_bad"] {') and "background: #fffafa" in _rule('[class*="st-key-ea_card_bad"] {')


def test_the_card_is_a_three_part_grid_and_its_buttons_never_wrap():
    assert "display: grid" in _rule(".ea-row {") and "minmax(250px, 1.5fr) 220px 100px" in _rule(".ea-row {") and "white-space: nowrap" in _rule('[class*="st-key-ea_card_"] .stButton button {')


def test_update_password_is_blue_and_remove_is_red():
    assert "color: #1d4ed8" in _rule('[class*="st-key-ea_card_"] [class*="st-key-ea_fix_"] button {') and "color: #b91c1c" in _rule('[class*="st-key-ea_card_"] [class*="st-key-ea_remove_"] button {')


def test_the_four_figures_are_equal_tiles_and_a_problem_count_is_red():
    assert "repeat(4, 1fr)" in _rule(".ea-stats {") and "color: #b91c1c" in _rule(".ea-stat-bad .ea-stat-v {")


def test_the_avatar_is_a_round_42_pixel_badge():
    rule = _rule(".ea-av {")
    assert "width: 42px" in rule and "height: 42px" in rule and "border-radius: 50%" in rule


def test_all_rules_are_scoped_to_this_page_or_its_dialogs():
    css = re.sub(r"/\*.*?\*/", "", H.EA_CSS, flags=re.S)
    for selector, _body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        for part in selector.split(","):
            assert re.search(r"ea-|ea_|manage_", part), f"an unscoped rule: {part.strip()}"


def test_the_remove_button_inside_its_dialog_is_red_when_it_can_be_pressed():
    rule = _rule('[class*="st-key-manage_remove_"] button:not(:disabled) {')
    assert "background: #dc2626" in rule and "color: #fff" in rule


def test_the_cards_have_room_to_breathe_at_the_bottom_as_well_as_the_top():
    assert "padding: 16px 18px 20px" in _rule('[class*="st-key-ea_card_"] {')
