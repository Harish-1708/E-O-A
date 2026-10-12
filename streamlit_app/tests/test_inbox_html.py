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
