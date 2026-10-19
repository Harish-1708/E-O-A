"""The Inbox's markup builders: what each piece of the Unibox-style layout looks like, and — above all — that text
from an email can never become markup."""
import os
import re
import sys
from datetime import datetime
from html.parser import HTMLParser

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import inbox_html as H  # noqa: E402

NOW = datetime(2026, 10, 12, 15, 0, 0)

HOSTILE = [
    "<script>alert(1)</script>",
    '"><img src=x onerror=alert(1)>',
    "' onmouseover='alert(1)",
    "</div><div class=\"ux-card\">forged card</div>",
    "<a href=\"javascript:alert(1)\">click</a>",
    "<svg onload=alert(1)>",
    "<style>body{display:none}</style>",
    "<iframe src=//evil.example></iframe>",
    "&lt;script&gt; &amp;amp; &#60;b&#62;",
    "[click](http://evil.example) ![pixel](http://evil.example/p.png) **bold** :red[x] $$x$$ :smile:",
    "    four spaces\n\n\n# heading\n- list\n> quote",
    "line one\r\nline two\rline three\nline four",
    "\u202eRTL override\u202c and \x00 null",
]

ALLOWED_TAGS = {"div", "span", "b"}
ALLOWED_ATTRS = {"class", "style", "title"}
SAFE_STYLE = re.compile(r"^(?:[a-z-]+:#[0-9a-fA-F]{3,8}(?:;[a-z-]+:#[0-9a-fA-F]{3,8})*;?|margin-left:\d{1,3}px)$")


class _Inspector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.tags, self.attrs, self.styles, self.raw_text = [], [], [], []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        for name, value in attrs:
            self.attrs.append(name)
            if name == "style":
                self.styles.append(value or "")

    def handle_data(self, data):
        self.raw_text.append(data)


def _inspect(markup):
    inspector = _Inspector()
    inspector.feed(markup)
    inspector.close()
    return inspector


def _everything_hostile(payload):
    """Every builder, with `payload` in every email-derived field."""
    return [
        H.row_html(payload, payload, payload, payload, "interested", True, True, payload, 3, 2, payload),
        H.thread_header_html(payload, payload, "lead", [payload], payload),
        H.card_html(payload, payload, payload, "2026-10-12 14:17:00", payload, payload),
        H.card_html(payload, payload, payload, "2026-10-12 14:17:00", "> " + payload + "\n>> " + payload + "\nafter\n> " + payload, payload),   # inside quoted history too
        H.card_html(payload, payload, payload, payload, "", payload),
        H.rail_item_html(payload, 3, payload, False),
        H.rail_title_html(payload),
        H.list_header_html(payload, 1, 2),
        H.empty_thread_html(payload),
    ]


# ================================================================ nothing from an email can become markup

@pytest.mark.parametrize("payload", HOSTILE)
def test_only_harmless_tags_and_attributes_can_ever_come_out(payload):
    for markup in _everything_hostile(payload):
        found = _inspect(markup)
        assert set(found.tags) <= ALLOWED_TAGS, (set(found.tags) - ALLOWED_TAGS, markup[:200])
        assert set(found.attrs) <= ALLOWED_ATTRS, (set(found.attrs) - ALLOWED_ATTRS, markup[:200])
        for style in found.styles:
            assert SAFE_STYLE.match(style), style


@pytest.mark.parametrize("payload", HOSTILE)
def test_no_hostile_text_survives_unescaped(payload):
    """A raw '<' or '>' from the email must never appear: the only angle brackets are our own tags."""
    for markup in _everything_hostile(payload):
        text_only = re.sub(r"</?(?:div|span|b)(?: [^<>]*)?>", "", markup)
        assert "<" not in text_only and ">" not in text_only, text_only[:200]
        for attribute_value in re.findall(r'(?:title|style|class)="([^"]*)"', markup):
            assert '"' not in attribute_value and "<" not in attribute_value


def test_a_quote_or_angle_bracket_cannot_break_out_of_an_attribute():
    markup = H.row_html("n", "s", "p", "d", "lead", False, False)
    assert 'title="Lead"' in markup
    hostile_status = H.status_label("x\" onmouseover=\"alert(1)")
    assert hostile_status == "Unclear"                               # an unknown key never echoes back what it was given


@pytest.mark.parametrize("payload", HOSTILE)
def test_every_builder_returns_one_line_so_markdown_cannot_split_it(payload):
    """A blank line would end Streamlit's HTML block and let the rest be read as Markdown."""
    for markup in _everything_hostile(payload):
        assert "\n" not in markup and "\r" not in markup
    assert "\n" not in H.card_html("s", "f", "t", "2026-10-12 14:17:00", "a\n\n\nb\r\n\r\nc")


def test_the_visible_text_is_exactly_what_was_written():
    """Escaping must change the markup, never what a person reads."""
    original = "Tom & Jerry <3 \"quotes\" and 'apostrophes' > less"
    class _Text(HTMLParser):                                         # decodes entities, as a browser does
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.parts = []

        def handle_data(self, data):
            self.parts.append(data)

    reader = _Text()
    reader.feed(H.card_html(original, original, original, "", original))
    assert "".join(reader.parts).count(original) == 4                # subject, From, To and body — each exactly as written


# ================================================================ small helpers

@pytest.mark.parametrize("value, expected", [
    ("<b>", "&lt;b&gt;"), ('"', "&quot;"), ("'", "&#x27;"), ("&", "&amp;"), (None, ""), (12, "12"), ("", ""), ("plain", "plain"),
])
def test_esc(value, expected):
    assert H.esc(value) == expected


@pytest.mark.parametrize("name, expected", [
    ("Gertrud Liebhart", "GL"), ("maya okafor", "MO"), ("Madonna", "M"), ("  Jean   Luc  Picard ", "JL"),
    ("maya.okafor@example.com", "MO"), ("", "?"), (None, "?"), ("123 456", "?"), ("(no sender)", "NS"), ("Élise Du", "ÉD"),
])
def test_initials(name, expected):
    assert H.initials(name) == expected


def test_a_person_always_gets_the_same_colour_whatever_the_capitalisation():
    assert H.avatar_color("Maya Okafor") == H.avatar_color("  maya okafor ") == H.avatar_color("MAYA OKAFOR")
    assert all(H.avatar_color(n) in H.AVATAR_COLORS for n in ("a", "b", "Maya", "", None, "x" * 500))
    assert len({H.avatar_color(n) for n in ("Ann", "Bob", "Cy", "Di", "Ed", "Flo", "Gus", "Hal", "Ivy")}) > 3     # not all one colour


@pytest.mark.parametrize("value, short, long", [
    ("2026-10-12 14:17:00", "Oct 12, 2026", "Oct 12, 2026, 2:17 PM"),
    ("2026-10-05 09:05:00", "Oct 5, 2026", "Oct 5, 2026, 9:05 AM"),            # no zero-padded day or hour
    ("2026-01-01 00:05:00", "Jan 1, 2026", "Jan 1, 2026, 12:05 AM"),            # midnight is 12, not 0
    ("2026-01-01 12:00:00", "Jan 1, 2026", "Jan 1, 2026, 12:00 PM"),            # noon
    ("2026-12-31 23:59:59", "Dec 31, 2026", "Dec 31, 2026, 11:59 PM"),
    ("2026-10-12 14:17", "Oct 12, 2026", "Oct 12, 2026, 2:17 PM"),
    ("2026-10-12", "Oct 12, 2026", "Oct 12, 2026, 12:00 AM"),
    ("", "", ""), (None, "", ""), ("not a date", "", ""), ("2026-13-45 99:99:99", "", ""),
])
def test_dates_read_like_a_mail_app(value, short, long):
    assert H.list_date(value) == short and H.card_date(value) == long


@pytest.mark.parametrize("unread, received, expected", [
    (True, "2026-10-09 18:40:00", "Received 2 days ago. Reply?"),
    (True, "2026-10-11 14:00:00", "Received 1 day ago. Reply?"),              # singular
    (True, "2026-10-11 15:00:00", "Received 1 day ago. Reply?"),              # exactly 24 hours
    (True, "2026-10-11 15:00:01", ""),                                        # a second short of a day
    (True, "2026-10-12 14:00:00", ""),
    (False, "2026-10-01 10:00:00", ""),                                       # already read: nobody is waiting on you
    (True, "2026-10-20 10:00:00", ""),                                        # in the future (clock skew)
    (True, "garbage", ""), (True, "", ""), (True, None, ""),
])
def test_the_waiting_nudge(unread, received, expected):
    assert H.waiting_nudge(unread, received, NOW) == expected


# ================================================================ statuses

@pytest.mark.parametrize("intent, classification, expected", [
    ("Interested", "Genuine Reply", "interested"),
    ("Not Interested", "Genuine Reply", "not_interested"),
    ("not interested", "genuine reply", "not_interested"),                    # 'not interested' must not be read as 'interested'
    ("Lead / Needs Follow-up", "Genuine Reply", "lead"),
    ("Unclear", "Genuine Reply", "unclear"),
    ("", "Genuine Reply", "unclear"),
    ("", "", "unclear"), (None, None, "unclear"),
    ("Interested", "Auto-Reply", "auto"),                                     # an auto-reply is an auto-reply whatever it says
    ("Interested", "Out of Office", "auto"),
    ("Interested", "Bounce (Hard)", "bounced"),
    ("", "Bounce (Soft)", "bounced"),
    ("Not Interested", "Bounce (Hard)", "bounced"),                           # a bounce outranks everything
    ("Interested", "Bounce (Hard) Auto-Reply", "bounced"),
])
def test_status_of(intent, classification, expected):
    assert H.status_of(intent, classification) == expected


def test_every_status_has_a_label_icon_and_colour_and_an_unknown_one_falls_back():
    keys = [k for k, *_ in H.STATUSES]
    assert len(keys) == len(set(keys)) == 6
    for key in keys:
        assert H.status_label(key) and H.status_icon(key) and re.fullmatch(r"#[0-9a-f]{6}", H.status_color(key))
    assert (H.status_label("nope"), H.status_icon("nope")) == (H.status_label("unclear"), H.status_icon("unclear"))


def test_the_rail_lists_the_statuses_in_a_fixed_order():
    assert [k for k, *_ in H.STATUSES] == ["lead", "interested", "not_interested", "unclear", "auto", "bounced"]


# ================================================================ the pieces

def test_a_list_row_shows_everything_the_reference_does():
    markup = H.row_html("Maya Okafor", "Re: Deal", "Sounds good", "Oct 12, 2026", "interested", False, False)
    for piece in ("Maya Okafor", "Re: Deal", "Sounds good", "Oct 12, 2026", "👍", 'class="ux-row"'):
        assert piece in markup
    assert "ux-selected" not in markup and "ux-unread" not in markup and "ux-dot" not in markup and "ux-nudge" not in markup


def test_a_selected_unread_row_is_marked_and_has_a_dot():
    markup = H.row_html("n", "s", "p", "d", "lead", True, True)
    assert 'class="ux-row ux-selected ux-unread"' in markup and 'class="ux-dot"' in markup


def test_the_nudge_the_count_of_messages_and_new_ones_appear_only_when_they_apply():
    base = H.row_html("n", "s", "p", "d", "lead", False, False)
    assert "ux-meta" not in base
    one_message = H.row_html("n", "s", "p", "d", "lead", False, False, message_count=1, unread_count=0)
    assert "ux-meta" not in one_message                                       # '1 messages' is never shown
    full = H.row_html("n", "s", "p", "d", "lead", False, True, "Received 2 days ago. Reply?", 3, 2, "Camp A")
    assert "Received 2 days ago. Reply?" in full and "Camp A · 3 messages · 2 new" in full
    assert H.row_html("n", "s", "p", "d", "lead", False, False, message_count=2).count("2 messages") == 1


def test_the_nudge_is_above_the_rest_of_the_row():
    markup = H.row_html("n", "s", "p", "d", "lead", False, True, "Received 2 days ago. Reply?")
    assert markup.index("ux-nudge") < markup.index("ux-top")


def test_the_status_icon_has_a_tooltip_naming_the_status():
    assert 'title="Not interested"' in H.row_html("n", "s", "p", "d", "not_interested", False, False)


def test_the_thread_header_has_an_avatar_name_email_and_a_coloured_status_pill():
    markup = H.thread_header_html("Gertrud Liebhart", "g@byodo.example", "interested", ["Stopped - Replied"])
    assert ">GL<" in markup and "Gertrud Liebhart" in markup and "g@byodo.example" in markup
    assert f"background:{H.avatar_color('Gertrud Liebhart')}" in markup
    assert "👍 Interested" in markup and f"color:{H.status_color('interested')}" in markup and "Stopped - Replied" in markup
    assert "ux-chip-muted" not in H.thread_header_html("n", "e", "lead", ["", None])


def test_the_status_pill_can_carry_a_short_note_and_has_none_by_default():
    assert "👍 Interested · High" in H.thread_header_html("n", "e", "interested", [], "High")
    assert " · " not in H.thread_header_html("n", "e", "interested", [])


def test_a_card_has_a_large_subject_from_and_to_lines_the_date_and_a_divider():
    markup = H.card_html("Re: Can I send our catalogue?", "Gertrud <g@byodo.example>", "You", "2026-10-12 14:17:00", "Hello\nWorld")
    for piece in ('class="ux-title"', "Re: Can I send our catalogue?", "<b>From:</b> Gertrud &lt;g@byodo.example&gt;", "<b>To:</b> You",
                  "Oct 12, 2026, 2:17 PM UTC", 'class="ux-rule"'):
        assert piece in markup, piece
    assert markup.index("ux-title") < markup.index("ux-fromrow") < markup.index("ux-rule") < markup.index("ux-body")


def test_a_card_without_a_date_or_subject_still_reads_properly():
    markup = H.card_html("", "a", "b", "", "text")
    assert "(no subject)" in markup and "ux-card-date" not in markup


def test_every_line_of_a_body_is_its_own_line_and_blank_lines_are_kept():
    markup = H.card_html("s", "f", "t", "", "one\n\nthree")
    assert markup.count('class="ux-line"') == 3 and "&nbsp;" in markup


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_every_kind_of_line_break_makes_a_new_line(newline):
    assert H.card_html("s", "f", "t", "", f"a{newline}b{newline}c").count('class="ux-line"') == 3


def test_quoted_history_loses_its_markers_and_is_indented_one_step_per_level():
    markup = H.card_html("s", "f", "t", "", "Reply\n> level one\n>> level two\n> > level two again\n>>> level three")
    assert markup.count("ux-qline") == 4 and ">" not in re.sub(r"</?[a-z]+(?: [^<>]*)?>", "", markup)    # no '>' left in the TEXT
    margins = [int(m) for m in re.findall(r"margin-left:(\d+)px", markup)]
    assert margins == [0, 10, 10, 20]


def test_a_line_that_is_only_quote_markers_is_a_blank_quoted_line_not_lost():
    markup = H.card_html("s", "f", "t", "", "> first\n>\n> second")
    assert markup.count("ux-qline") == 3 and "&nbsp;" in markup


def test_an_email_with_no_text_shows_the_note_instead_of_an_empty_card_body():
    for blank in ("", "   ", "\n\n", None):
        markup = H.card_html("s", "f", "t", "", blank, "This email has no readable text.")
        assert "This email has no readable text." in markup and "ux-body" not in markup and "ux-line" not in markup


def test_the_rail_item_shows_its_icon_label_and_count_and_marks_the_selected_one():
    plain = H.rail_item_html("Interested", 4, "👍", False)
    selected = H.rail_item_html("Interested", 4, "👍", True)
    assert ">4<" in plain and "Interested" in plain and "ux-selected" not in plain and "ux-selected" in selected
    assert "ux-count" not in H.rail_item_html("All", None, "📥", False)
    assert ">0<" in H.rail_item_html("Bounced", 0, "⚠️", False)               # a zero is shown, not hidden


def test_the_list_header_says_how_many_are_shown_of_how_many():
    assert H.list_header_html("Inbox", 5, 5).count("5") == 1 and "5 of 9" in H.list_header_html("Inbox", 5, 9)


# ================================================================ the stylesheet

def test_the_stylesheet_is_balanced_and_has_the_rules_the_layout_depends_on():
    css = H.INBOX_CSS
    assert css.count("{") == css.count("}") and css.count("(") == css.count(")")
    for needle in (
        "margin-bottom: 0 !important",                  # undoes Streamlit's -16px wrapper margin that clipped each block
        "position: absolute !important",                # the invisible button laid over each row
        "opacity: 0",
        "[class*=\"_open_\"] > div",                  # the button's wrapper fills the row: only its top 40px used to respond to clicks
        "@media (max-width: 899px)",                    # small screens
        ".ux-selected", ".ux-nudge", ".ux-qline", "st-key-ux_bar_",
    ):
        assert needle in css, needle


def test_every_key_pattern_in_the_stylesheet_ends_in_an_underscore_so_it_cannot_catch_a_neighbour():
    """A pattern like [class*="st-key-ux_rail"] once also matched the invisible rail BUTTONS and wrecked the layout.
    Containers are named ux_<thing>_<page>, so each pattern must stop at the underscore after <thing>."""
    patterns = re.findall(r'\[class\*="(st-key-ux_[a-z]+_?)"\]', H.INBOX_CSS)
    assert patterns and all(p.endswith("_") for p in patterns), [p for p in patterns if not p.endswith("_")]
    assert {"st-key-ux_shell_", "st-key-ux_railpane_", "st-key-ux_listpane_", "st-key-ux_list_", "st-key-ux_threadbody_",
            "st-key-ux_bar_", "st-key-ux_row_", "st-key-ux_ritem_", "st-key-ux_reply_"} <= set(patterns)


def test_the_stylesheet_never_loads_anything_from_outside():
    assert "@import" not in H.INBOX_CSS and "url(" not in H.INBOX_CSS and "http" not in H.INBOX_CSS


# ================================================================ what esc removes

@pytest.mark.parametrize("raw, expected", [
    ("a\nb", "a b"), ("a\r\nb", "a b"), ("a\rb", "a b"), ("a\n\n\nb", "a   b"),
    ("nul\x00byte", "nulbyte"), ("bell\x07here", "bellhere"), ("esc\x1b[31m", "esc[31m"),
    ("tab\tstays", "tab\tstays"),                                    # an ordinary tab is harmless and kept
    ("\u202eevil\u202c", "evil"), ("\u2066x\u2069", "x"),           # text-direction overrides that disguise a subject
    ("plain é 日本 😀", "plain é 日本 😀"),                             # ordinary international text is untouched
])
def test_esc_removes_only_what_could_change_how_the_page_is_read(raw, expected):
    assert H.esc(raw) == expected


def test_a_subject_made_to_read_backwards_is_shown_the_right_way_round():
    spoof = "invoice\u202e fdp.exe"
    assert "\u202e" not in H.card_html(spoof, "f", "t", "", "b") and "\u202e" not in H.row_html(spoof, spoof, spoof, "d", "lead", False, False)


# ================================================================ text size and fitting the screen

def _font_sizes(css):
    return [float(x) for x in re.findall(r"font-size: ([\d.]+)px", css)]


def test_the_default_stylesheet_is_the_normal_size():
    assert H.INBOX_CSS == H.inbox_css(1.0)


@pytest.mark.parametrize("name, scale", [("Normal", 1.0), ("Large", 1.15), ("Extra large", 1.3)])
def test_every_font_size_is_scaled_and_nothing_else_changes(name, scale):
    assert H.SIZES[name] == scale
    base, scaled = H.inbox_css(1.0), H.inbox_css(scale)
    assert len(_font_sizes(base)) == len(_font_sizes(scaled)) > 20
    for before, after in zip(_font_sizes(base), _font_sizes(scaled)):
        assert after == pytest.approx(before * scale, abs=0.06)
    neutral = lambda css: re.sub(r"(?:flex: 0 0 |width: |min-width: )\d+px !important", "", re.sub(r"font-size: [\d.]+px", "", css))   # (the rail's own width grows too)
    assert neutral(base) == neutral(scaled)                                                              # spacing, colours and panes untouched
    assert scaled.count("{") == scaled.count("}")


def test_the_sizes_are_ordered_and_the_default_is_one_of_them_and_larger_than_before():
    assert list(H.SIZES.values()) == sorted(H.SIZES.values()) and H.DEFAULT_SIZE in H.SIZES and H.SIZES[H.DEFAULT_SIZE] > 1.0


def test_the_frame_follows_the_height_of_the_window_but_never_gets_shorter_than_700px_and_a_phone_keeps_the_fixed_height():
    css = H.INBOX_CSS
    assert "--ux-h: max(700px, calc(100vh - 230px))" in css and "min-height: var(--ux-h)" in css and "--ux-h: 700px" in css
    assert css.index('[class*="st-key-ux_shell_tab_"] { --ux-h: max(700px, calc(100vh - 373px)); }') < css.index("@media (max-width: 899px)")     # the phone rule still has the last word


def test_what_is_inside_the_frame_fills_it_through_the_outer_elements_not_through_fixed_heights():
    """REGRESSION (seen in a real browser, in the installed app): the list and the conversation stayed 656px and 586px tall in every window, with the empty rest of the frame under them.
    Streamlit wraps each pane and each scrolling area in an OUTER element that carries the code's fixed height, and the inner element only fills it, so the rules must flex the OUTER one."""
    css = H.INBOX_CSS
    assert 'div:has(> [class*="st-key-ux_listpane_"]), div:has(> [class*="st-key-ux_threadpane_"]) { flex: 1 1 auto !important; min-height: 0 !important; }' in css
    assert 'div:has(> [class*="st-key-ux_list_"]), div:has(> [class*="st-key-ux_threadbody_"]) { flex: 1 1 0 !important; min-height: 0 !important; height: auto !important; }' in css
    assert not re.search(r'^\[class\*="st-key-ux_list_"\] \{ height:', css, re.M) and not re.search(r'^\[class\*="st-key-ux_threadbody_"\] \{ height:', css, re.M)     # the rules that did nothing are gone
    assert "calc(var(--ux-h) - 44px)" not in css and "calc(var(--ux-h) - 114px)" not in css                                                                  # and so are the guessed overheads


def test_the_inbox_uses_the_whole_width_of_the_page_instead_of_a_narrow_column():
    css = H.INBOX_CSS
    assert '[data-testid="stMainBlockContainer"]:has([class*="st-key-ux_shell_"]) { max-width: 100% !important;' in css


def test_a_nonsense_scale_still_gives_a_valid_stylesheet():
    for scale in (0, 0.5, 3, -1):
        css = H.inbox_css(scale)
        assert css.count("{") == css.count("}") and "font-size:" in css


def test_the_rail_keeps_a_width_that_grows_with_the_text_size_and_only_on_wide_screens():
    widths = {name: re.search(r"flex: 0 0 (\d+)px !important", H.inbox_css(scale)).group(1) for name, scale in H.SIZES.items()}
    assert [int(w) for w in widths.values()] == sorted(int(w) for w in widths.values()) and int(widths["Normal"]) >= 190
    assert int(widths["Extra large"]) > int(widths["Normal"])
    css = H.INBOX_CSS
    assert "__RAIL_W__" not in H.inbox_css(1.0) and "__RAIL_W__" not in H.inbox_css(1.3)               # the placeholder is always filled in
    assert css.index("@media (min-width: 900px)") < css.index('[data-testid="stColumn"]:first-child') < css.index("@media (max-width: 899px)")


def test_the_three_panes_stay_in_one_row_on_wide_screens_and_stack_only_on_small_ones():
    """Pinning the rail's width once pushed the conversation pane onto a second row; the row must never wrap on a wide screen."""
    css = H.INBOX_CSS
    wide = css[css.index("@media (min-width: 900px)"):css.index("@media (max-width: 899px)")]
    assert "flex-wrap: nowrap !important" in wide and ":nth-child(2) { flex: 1 1 0" in wide and ":nth-child(3) { flex: 2 1 0" in wide
    assert "nowrap !important" not in css[css.index("@media (max-width: 899px)"):]                     # on a phone they stack as before


# ================================================================ Read more / Read less

@pytest.mark.parametrize("body, expected", [
    ("", False), (None, False), ("short", False), ("\n".join(["line"] * 12), False), ("\n".join(["line"] * 13), True),
    ("x" * 1100, False), ("x" * 1101, True), ("a\n\n\n\n\n\n\n\n\n\n\nb", False), ("a\n\n\n\n\n\n\n\n\n\n\n\nb", True),
])
def test_how_long_an_email_must_be_before_it_gets_a_read_more(body, expected):
    assert H.needs_clamp(body) is expected


def test_a_long_email_is_cropped_only_when_asked_and_a_short_one_never_is():
    long, short = "\n".join(f"line {i}" for i in range(40)), "Thanks!"
    assert "ux-clamped" in H.card_html("s", "f", "t", "", long, clamp=True)
    assert "ux-clamped" not in H.card_html("s", "f", "t", "", long) and "ux-clamped" not in H.card_html("s", "f", "t", "", long, clamp=False)
    assert "ux-clamped" not in H.card_html("s", "f", "t", "", short, clamp=True)


def test_cropping_hides_nothing_from_the_page_it_only_fades_it():
    long = "\n".join(f"line {i}" for i in range(40))
    assert H.card_html("s", "f", "t", "", long, clamp=True).count('class="ux-line"') == 40                # every line is still there


def test_a_cropped_card_is_still_one_safe_line_and_the_crop_is_in_the_stylesheet():
    markup = H.card_html("s", "f", "t", "", "<script>x</script>\n" * 30, clamp=True)
    assert "\n" not in markup and "<script" not in markup
    assert ".ux-body.ux-clamped { position: relative; max-height: 20em; overflow: hidden; }" in H.INBOX_CSS and ".ux-body.ux-clamped::after" in H.INBOX_CSS


def test_the_read_more_and_show_earlier_buttons_are_styled_by_their_own_key_prefixes():
    """Checked by the declarations themselves: the hover rules repeat the selector text, so the text alone proves nothing."""
    assert '[class*="st-key-ux_more_"] button { background: transparent; border: 0; color: #2563eb;' in H.INBOX_CSS
    assert '[class*="st-key-ux_expand_"] button { width: 100%; background: #f3f6fb;' in H.INBOX_CSS


# ================================================================ "Mark all read" in the list header

def _inbox_rule(selector_start):
    css = H.inbox_css(1.0)
    i = css.index(selector_start)
    return css[i:css.index("}", i)]


def test_the_mark_all_read_button_is_laid_over_the_list_header_just_after_the_title_and_out_of_the_flow():
    """Out of the flow, so the fixed-height panes below it do not move; after the title, because the conversation count at the other end varies in width."""
    rule = _inbox_rule('[class*="st-key-ux_markall_"] {')
    assert "position: absolute" in rule and "top: 8px" in rule and "left: 96px" in rule and "z-index: 4" in rule
    assert "position: relative" in _inbox_rule('[class*="st-key-ux_listpane_"] { border-right')                  # so that "absolute" is measured from the list pane


def test_the_mark_all_read_button_is_a_small_outlined_button_that_dims_when_off():
    button = _inbox_rule('[class*="st-key-ux_markall_"] button {')
    assert "min-height: 28px" in button and "border: 1px solid #d1d5db" in button and "border-radius: 8px" in button
    assert "opacity: .5" in _inbox_rule('[class*="st-key-ux_markall_"] button:disabled {') and "color: #1d4ed8" in _inbox_rule('[class*="st-key-ux_markall_"] button:hover:not(:disabled) {')


# ================================================================ the composer: markup and styling

class _ComposerScan(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.tags, self.attrs = set(), set()

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag)
        self.attrs |= {name for name, _ in attrs}


def _composer_safe(markup):
    scan = _ComposerScan()
    scan.feed(markup)
    assert scan.tags <= {"div", "span"} and scan.attrs <= {"class", "style"}, (scan.tags, scan.attrs)
    assert "<script" not in markup and "<img" not in markup and "\u202e" not in markup and "\x00" not in markup


def test_the_composer_pieces_are_small_escaped_blocks_with_their_own_classes():
    assert H.composer_label_html("From") == '<div class="ux-flabel">From</div>'
    assert H.sender_hint_html("the account this thread started from") == '<div class="ux-fhint">the account this thread started from</div>'
    assert H.sender_note_html("You are replying from sales2.") == '<div class="ux-fnote">⚠ You are replying from sales2.</div>'


@pytest.mark.parametrize("hostile", HOSTILE)
def test_hostile_text_in_any_composer_piece_is_escaped(hostile):
    for markup in (H.composer_label_html(hostile), H.sender_hint_html(hostile), H.sender_note_html(hostile), H.attachment_chips_html([(hostile, 1234)], 10 * 1024 * 1024)):
        _composer_safe(markup)


def test_attachments_show_a_chip_for_each_file_with_its_size_and_a_total_against_the_limit():
    markup = H.attachment_chips_html([("contract.pdf", int(1.2 * 1024 * 1024)), ("logo.png", 340 * 1024)], 10 * 1024 * 1024)
    assert markup.count('<span class="ux-chipf">') == 2 and "📄 contract.pdf" in markup and '<span class="ux-fhint">1.2 MB</span>' in markup and '<span class="ux-fhint">340 KB</span>' in markup
    assert '<span class="ux-total">1.5 MB of 10 MB</span>' in markup and "\n" not in markup


@pytest.mark.parametrize("sizes, over", [([10 * 1024 * 1024], False), ([10 * 1024 * 1024 + 1], True), ([6 * 1024 * 1024, 5 * 1024 * 1024], True), ([1024], False)])
def test_the_total_turns_red_exactly_when_it_passes_the_limit(sizes, over):
    markup = H.attachment_chips_html([(f"f{i}.bin", size) for i, size in enumerate(sizes)], 10 * 1024 * 1024)
    assert ('class="ux-total ux-over"' in markup) is over


@pytest.mark.parametrize("files", [None, [], ()])
def test_with_no_files_there_is_nothing_to_show(files):
    assert H.attachment_chips_html(files, 100) == ""


@pytest.mark.parametrize("size, text", [(0, "0 KB"), (1, "1 KB"), (1024, "1 KB"), (1536, "2 KB"), (340 * 1024, "340 KB"), (1024 * 1024, "1.0 MB"), (int(2.5 * 1024 * 1024), "2.5 MB"), (None, "0 KB"), ("x", "0 KB"), (-5, "0 KB")])
def test_file_sizes_read_naturally_and_a_bad_size_is_zero(size, text):
    assert H._size_text(size) == text


def test_the_composer_is_docked_with_a_top_edge_and_the_field_rows_are_a_fixed_34_pixels():
    assert "border-top: 1px solid #e5e7eb" in _inbox_rule('[class*="st-key-ux_composer_"] {')
    row = _inbox_rule('[class*="st-key-ux_frm_"] {')
    assert "height: 34px !important" in row and "flex: 0 0 34px !important" in row and "overflow: hidden !important" in row


def test_each_row_is_a_grid_of_label_field_and_hint_so_the_field_gets_the_room():
    """REGRESSION: with Streamlit's own column widths the first column was a fixed 218px and the From address was cut off."""
    grid = _inbox_rule('[class*="st-key-ux_frm_"] [data-testid="stHorizontalBlock"] { grid-template-columns')
    assert "72px minmax(0, 1fr) auto" in grid
    assert "flex: unset !important" in _inbox_rule('[class*="st-key-ux_frm_"] [data-testid="stColumn"], [class*="st-key-ux_foot_"] [data-testid="stColumn"] {')


def test_the_fields_have_no_boxes_of_their_own_because_the_row_is_the_field():
    for selector in ('[class*="st-key-ux_frm_"] [data-testid="stTextInputRootElement"] {', '[class*="st-key-ux_composer_"] [data-testid="stTextAreaRootElement"] {',
                     '[class*="st-key-ux_frm_"] [data-testid="stSelectbox"] > div > div {'):
        rule = _inbox_rule(selector)
        assert "border: 0 !important" in rule and "background: transparent !important" in rule, selector


def test_the_footer_gives_the_note_its_full_width_so_send_never_covers_it():
    foot = _inbox_rule('[class*="st-key-ux_foot_"] [data-testid="stHorizontalBlock"] { grid-template-columns')
    assert "112px minmax(0, 1fr) max-content 118px" in foot and "column-gap: 14px" in foot
    assert "white-space: nowrap" in _inbox_rule('[class*="st-key-ux_foot_"] .ux-fhint {')


def test_the_amber_note_and_the_red_total_use_the_apps_warning_colours():
    assert "background: #fffaf0" in _inbox_rule(".ux-fnote {") and "color: #92400e" in _inbox_rule(".ux-fnote {")
    assert "color: #b91c1c" in _inbox_rule(".ux-over {")


def test_the_composer_rules_are_scoped_to_the_composer_not_the_whole_page():
    css = H.inbox_css(1.0)
    start = css.index("/* ---- the composer")
    block = css[start:css.index("/* ---- small screens")]
    for selector, _body in re.findall(r"([^{}]+)\{([^{}]*)\}", re.sub(r"/\*.*?\*/", "", block, flags=re.S)):
        for part in selector.split(","):
            assert "ux" in part, f"an unscoped composer rule: {part.strip()}"


def test_opening_the_composer_only_highlights_the_reply_button_and_sets_no_height_because_the_conversation_makes_room_by_itself():
    import inspect
    css = H.composer_open_css("tab_Camp")
    assert css == '[class*="st-key-ux_reply_"] button { background: #eaf1ff !important; color: #1d4ed8 !important; border-color: #2563eb !important; }'
    assert "height" not in css and list(inspect.signature(H.composer_open_css).parameters) == ["namespace"]
