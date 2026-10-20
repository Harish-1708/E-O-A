"""The pure rules for HTML campaigns: what an email must never contain, the checks, the footer, the plain-text helper and the starter layouts."""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import html_campaign_logic as H  # noqa: E402

FOOTER = {"business_name": "Kelson Agency", "address": "12 Example Street, New York", "opt_out": 'Reply "unsubscribe" to stop.'}
KNOWN = ["FirstName", "CompanyName", "Email"]
TEXT = "Subject: Hello\n\nHi {{ FirstName }},\n\nMessage.\n\n{{ footer }}\n"
GOOD = '<html><body><p>Hi {{ FirstName }}, we have news for {{ CompanyName }} and a lot more to say about it so there is plenty of readable text here.</p>{{ footer }}</body></html>'


# ================================================================ what must never be sent

@pytest.mark.parametrize("markup", [
    "<script>alert(1)</script>", "<SCRIPT SRC=x></SCRIPT>", "<script >x</script >", "<p>hi</p><script\n>x</script>", "<iframe src='https://x.com'></iframe>", "<form action='https://evil.example/steal'></form>", "<form action='x'><input name=a></form>", "<input type=text name=a>",
    "<object data='x'></object>", "<embed src='x'>", "<button>Go</button>", "<textarea>x</textarea>", "<select><option>1</option></select>", "<base href='https://x.com'>",
])
def test_dangerous_tags_are_found_in_any_spelling(markup):
    problems = H.unsafe_problems(markup)
    assert problems and "must not have" in problems[0]


@pytest.mark.parametrize("markup", ['<p onclick="x()">a</p>', '<img src="https://x/y.png" alt="a" onerror="x()">', '<a href="https://x" ONMOUSEOVER="x()">a</a>', "<body onload=x()>"])
def test_event_attributes_are_found_whatever_the_case_or_the_tag(markup):
    assert any("event attributes" in p for p in H.unsafe_problems(markup))


@pytest.mark.parametrize("href", ["javascript:alert(1)", "  JavaScript:alert(1)", "vbscript:x", "JAVASCRIPT : x"])
def test_javascript_links_are_found_even_with_spaces_and_capitals(href):
    assert H.unsafe_problems(f'<a href="{href}">x</a>') == ["It contains a javascript: link, which is not allowed."]


@pytest.mark.parametrize("meta", ["<meta http-equiv='refresh' content='0;url=https://x.com'>", "<META HTTP-EQUIV=\"Refresh\" content=\"5\">", "<meta http-equiv=' refresh '>"])
def test_an_automatic_redirect_is_found_in_any_spelling(meta):
    assert H.unsafe_problems(meta) == ["It contains a meta refresh (an automatic redirect), which is not allowed."]


@pytest.mark.parametrize("meta", ['<meta charset="utf-8">', '<meta name="viewport" content="width=device-width, initial-scale=1">', '<meta http-equiv="Content-Type" content="text/html; charset=UTF-8">'])
def test_the_ordinary_meta_lines_every_email_has_are_fine(meta):
    assert H.unsafe_problems(meta) == []


def test_an_outside_stylesheet_is_found_but_a_style_block_is_not_unsafe():
    assert any("outside stylesheet" in p for p in H.unsafe_problems('<link rel="stylesheet" href="https://x.com/a.css">'))
    assert H.unsafe_problems("<style>p{color:red}</style><p>hi</p>") == []


@pytest.mark.parametrize("markup", ["", "<p>Hello <b>there</b></p>", '<a href="https://example.com">link</a>', '<img src="https://x/y.png" alt="a">', "<!-- <script>not real</script> --><p>hi</p>", "<p>a &lt;script&gt; in words</p>"])
def test_ordinary_email_has_no_unsafe_problems(markup):
    assert H.unsafe_problems(markup) == []


def test_several_kinds_at_once_give_one_sentence_each():
    assert len(H.unsafe_problems('<script>x</script><p onclick="y()">a</p><a href="javascript:z">b</a><link rel="stylesheet" href="x">')) == 4


# ================================================================ reading the email

def test_visible_text_ignores_tags_style_and_script_content_and_collapses_white_space():
    assert H.visible_text("<style>p{color:red}</style><p>Hello   <b>big</b>\n world</p><script>x()</script>") == "Hello big world"


def test_placeholders_are_listed_once_each_in_order_of_first_use():
    assert H.placeholders_in("{{ a }} {{b}} {{ a }} {{ c_d }} {x}") == ["a", "b", "c_d"] and H.placeholders_in("") == [] and H.placeholders_in(None) == []


@pytest.mark.parametrize("markup, expected", [("{{ footer }}", True), ("<p>You can unsubscribe at any time</p>", True), ("<p>OPT OUT here</p>", True), ("<p>opt-out</p>", True),
                                              ("<p>Nothing here</p>", False), ("<!-- unsubscribe -->", False), ("<style>.unsubscribe{}</style><p>hi</p>", False)])
def test_an_opt_out_is_a_footer_placeholder_or_the_words_a_reader_can_see(markup, expected):
    assert H.has_opt_out(markup) is expected


# ================================================================ the checks

def test_a_good_email_has_no_errors_and_no_warnings():
    assert H.html_checks(GOOD, TEXT, KNOWN, FOOTER) == {"errors": [], "warnings": []}


def test_an_empty_email_is_one_error():
    assert H.html_checks("  \n", TEXT, KNOWN, FOOTER) == {"errors": ["The HTML is empty."], "warnings": []}


def test_unsafe_constructs_are_errors():
    assert any("must not have" in e for e in H.html_checks(GOOD + "<script>x</script>", TEXT, KNOWN, FOOTER)["errors"])


@pytest.mark.parametrize("img, error_words", [('<img src="https://x/y.png">', "no alt text"), ('<img src="http://x/y.png" alt="a">', "https://"), ('<img src="C:\\pics\\a.png" alt="a">', "https://"),
                                              ('<img alt="a">', "https://"), ('<img src="data:image/png;base64,AAAA" alt="a">', "https://")])
def test_images_need_alt_text_and_a_secure_web_address(img, error_words):
    assert any(error_words in e for e in H.html_checks(GOOD + img, TEXT, KNOWN, FOOTER)["errors"])


def test_an_image_with_alt_text_and_an_https_address_is_fine_and_an_empty_alt_is_allowed():
    assert H.html_checks(GOOD + '<img src="https://x.com/a.png" alt="A product"><img src="https://x.com/b.png" alt="">', TEXT, KNOWN, FOOTER)["errors"] == []


def test_a_placeholder_that_matches_no_column_is_an_error_that_names_it():
    errors = H.html_checks(GOOD + "{{ frist_name }}", TEXT, KNOWN, FOOTER)["errors"]
    assert errors == ["These placeholders match no Sheet column: {{ frist_name }}."]


def test_the_footer_placeholder_is_always_known():
    assert H.html_checks(GOOD, TEXT, [], FOOTER)["errors"] == ["These placeholders match no Sheet column: {{ FirstName }}, {{ CompanyName }}."]


def test_an_empty_plain_text_copy_is_an_error():
    assert any("plain-text copy is empty" in e for e in H.html_checks(GOOD, "  ", KNOWN, FOOTER)["errors"])


def test_no_way_to_opt_out_is_an_error_but_the_designers_own_unsubscribe_line_is_enough():
    bare = "<p>Hello {{ FirstName }}, this is a longer message with enough readable text to avoid the thin-email warning for sure.</p>"
    assert any("no way to opt out" in e for e in H.html_checks(bare, TEXT, KNOWN, FOOTER)["errors"])
    with_own = bare + "<p>Unsubscribe by replying to this email.</p>"
    assert H.html_checks(with_own, TEXT, KNOWN, None)["errors"] == []


@pytest.mark.parametrize("footer, words", [(None, "business name"), ({}, "business name"), ({"business_name": "K"}, "postal address"), ({"business_name": " ", "address": "x"}, "business name"), ({"address": "x"}, "business name")])
def test_using_the_footer_placeholder_needs_a_complete_footer(footer, words):
    errors = H.html_checks(GOOD, TEXT, KNOWN, footer)["errors"]
    assert errors and words in errors[0] and "footer is incomplete" in errors[0]


def test_the_footer_settings_are_not_needed_when_the_email_has_its_own_opt_out_and_does_not_use_the_placeholder():
    own = "<p>Hello {{ FirstName }}, plenty of readable text goes in this paragraph so it is not thin at all.</p><p>Unsubscribe: reply to this.</p>"
    assert H.html_checks(own, "Subject: a\n\nHello. Unsubscribe by replying.", KNOWN, None)["errors"] == []


def test_size_is_a_warning_near_the_gmail_limit_and_an_error_over_it():
    near = GOOD + "<!--" + "x" * (95 * 1024) + "-->"
    over = GOOD + "<!--" + "x" * (110 * 1024) + "-->"
    assert any("close to the point" in w for w in H.html_checks(near, TEXT, KNOWN, FOOTER)["warnings"]) and H.html_checks(near, TEXT, KNOWN, FOOTER)["errors"] == []
    assert any("cuts messages off" in e for e in H.html_checks(over, TEXT, KNOWN, FOOTER)["errors"])


def test_many_links_a_thin_email_and_a_style_block_are_warnings_only():
    many = GOOD + "".join(f'<a href="https://x.com/{i}">l</a>' for i in range(16))
    assert any("16 links" in w for w in H.html_checks(many, TEXT, KNOWN, FOOTER)["warnings"]) and H.html_checks(many, TEXT, KNOWN, FOOTER)["errors"] == []
    thin = "<p>Hi</p>{{ footer }}"
    assert any("very little text" in w for w in H.html_checks(thin, TEXT, KNOWN, FOOTER)["warnings"])
    styled = "<style>p{color:red}</style>" + GOOD
    assert any("<style> block" in w for w in H.html_checks(styled, TEXT, KNOWN, FOOTER)["warnings"])


def test_a_plain_text_copy_without_an_opt_out_gets_a_warning_not_an_error():
    result = H.html_checks(GOOD, "Subject: Hi\n\nJust text.", KNOWN, FOOTER)
    assert any("no opt-out line" in w for w in result["warnings"]) and result["errors"] == []


# ================================================================ the footer

def test_the_footer_has_the_name_the_address_and_the_opt_out_and_the_opt_out_has_a_default():
    markup = H.build_footer_html(FOOTER)
    assert "Kelson Agency" in markup and "12 Example Street, New York" in markup and "Reply &quot;unsubscribe&quot; to stop." in markup
    assert H.DEFAULT_OPT_OUT.replace('"', "&quot;") in H.build_footer_html({"business_name": "K", "address": "A"})
    text = H.build_footer_text({"business_name": "K", "address": "A"})
    assert text == f"--\nK · A\n{H.DEFAULT_OPT_OUT}"


@pytest.mark.parametrize("hostile", ['<script>alert(1)</script>', '"><img src=x onerror=alert(1)>', "</div><a href='javascript:x'>", "&lt;b&gt;"])
def test_nothing_typed_in_the_footer_settings_can_become_markup(hostile):
    markup = H.build_footer_html({"business_name": hostile, "address": hostile, "opt_out": hostile})
    assert H.unsafe_problems(markup) == [] and "<script" not in markup and "<img" not in markup and markup.count("<div") == 1


def test_footer_problems_lists_only_what_is_really_missing():
    assert H.footer_problems(FOOTER) == [] and H.footer_problems({"business_name": "K", "address": "A"}) == [] and len(H.footer_problems({})) == 2 and len(H.footer_problems(None)) == 2


# ================================================================ the plain-text copy

def test_html_to_text_keeps_the_words_the_links_and_the_paragraphs_and_drops_the_rest():
    text = H.html_to_text('<style>p{}</style><h1>Big news</h1><p>Hi {{ FirstName }},</p><p>Read <a href="https://x.com/a">the details</a> now.</p><script>x()</script><br><b>Thanks</b>')
    assert text == "Big news\n\nHi {{ FirstName }},\n\nRead the details (https://x.com/a) now.\n\nThanks"
    assert H.html_to_text("") == "" and H.html_to_text(None) == ""


def test_html_to_text_unescapes_entities():
    assert H.html_to_text("<p>Fish &amp; chips &middot; 5 &lt; 6</p>") == "Fish & chips · 5 < 6"


# ================================================================ the starter layouts

@pytest.mark.parametrize("key", list(H.STARTER_LAYOUTS))
def test_every_starter_layout_passes_every_check_with_a_complete_footer(key):
    layout = H.STARTER_LAYOUTS[key]
    assert layout["label"] and layout["description"]
    result = H.html_checks(layout["html"], H.starter_plain_text(), ["FirstName"], FOOTER)
    assert result == {"errors": [], "warnings": []}, (key, result)
    assert "{{ footer }}" in layout["html"] and "{{ FirstName }}" in layout["html"] and H.unsafe_problems(layout["html"]) == []


def test_there_are_at_least_three_layouts_and_an_unknown_key_gives_the_default():
    assert len(H.STARTER_LAYOUTS) >= 3 and H.DEFAULT_LAYOUT in H.STARTER_LAYOUTS
    assert H.starter_html("nonsense") == H.starter_html(H.DEFAULT_LAYOUT) == H.STARTER_LAYOUTS[H.DEFAULT_LAYOUT]["html"]


def test_the_starter_plain_text_has_a_subject_line_a_blank_line_and_the_footer_placeholder():
    text = H.starter_plain_text("Spring news")
    assert text.startswith("Subject: Spring news\n\n") and text.rstrip().endswith("{{ footer }}") and "{{ FirstName }}" in text
    assert H.starter_plain_text("   ").startswith("Subject: Your subject here\n\n")


def test_the_starter_layouts_are_small_enough_for_gmail_and_use_the_safe_table_style():
    for layout in H.STARTER_LAYOUTS.values():
        assert len(layout["html"].encode("utf-8")) < 8 * 1024 and "<table" in layout["html"] and "max-width:600px" in layout["html"]


def test_the_starter_plain_text_is_the_subject_line_then_the_starter_body():
    assert H.starter_plain_text("Hi") == "Subject: Hi\n\n" + H.starter_plain_body() + "\n" and H.starter_plain_body().rstrip().endswith("{{ footer }}") and "Subject:" not in H.starter_plain_body()


def test_when_the_sheets_columns_are_not_known_placeholders_are_not_checked_instead_of_wrongly_reported():
    markup = GOOD + "{{ AnyCustomColumn }}"
    assert H.html_checks(markup, TEXT, None, FOOTER) == {"errors": [], "warnings": []}
    assert any("match no Sheet column" in e for e in H.html_checks(markup, TEXT, KNOWN, FOOTER)["errors"])
    assert any("match no Sheet column" in e for e in H.html_checks(markup, TEXT, [], FOOTER)["errors"])         # an empty list is "no columns", not "unknown"


# ================================================================ link tagging (UTM)

TAGS = {"utm_source": "email", "utm_medium": "outreach", "utm_campaign": "Spring", "utm_content": "intro_A"}
TAG_QUERY = "utm_source=email&amp;utm_medium=outreach&amp;utm_campaign=Spring&amp;utm_content=intro_A"


def test_tagging_is_off_unless_the_campaign_turns_it_on():
    for tracking in (None, {}, {"utm": False}, {"utm": None}, "yes", [], 5):
        assert H.utm_params("Spring", tracking, "intro_A") is None


def test_the_labels_say_where_a_click_came_from_with_sensible_defaults_and_overrides():
    assert H.utm_params("Spring", {"utm": True}, "intro_A") == TAGS
    assert H.utm_params("Spring", {"utm": True, "source": "kelson", "medium": "newsletter"}, "followup1_B") == {"utm_source": "kelson", "utm_medium": "newsletter", "utm_campaign": "Spring", "utm_content": "followup1_B"}
    assert H.utm_params("Spring", {"utm": True, "source": "  ", "medium": ""}, "") == {"utm_source": "email", "utm_medium": "outreach", "utm_campaign": "Spring"}
    assert "utm_campaign" not in H.utm_params("", {"utm": True}, "x")


def test_a_web_link_gets_every_label():
    assert H.tag_links('<a href="https://x.com/page">go</a>', TAGS) == f'<a href="https://x.com/page?{TAG_QUERY}">go</a>'


def test_an_existing_query_and_a_fragment_are_kept_and_the_labels_go_between_them():
    assert H.tag_links('<a href="https://x.com/p?a=1&amp;b=2#top">go</a>', TAGS) == f'<a href="https://x.com/p?a=1&amp;b=2&amp;{TAG_QUERY}#top">go</a>'


def test_a_bare_domain_gets_a_slash_so_the_address_stays_valid():
    assert H.tag_links('<a href="http://y.org">y</a>', TAGS) == f'<a href="http://y.org/?{TAG_QUERY}">y</a>'


def test_single_quotes_capitals_and_other_attributes_are_all_handled():
    out = H.tag_links("<A STYLE='x:y' HREF='https://x.com' TARGET=_blank>go</A>", TAGS)
    assert out == f"<A STYLE='x:y' HREF='https://x.com/?{TAG_QUERY}' TARGET=_blank>go</A>"


@pytest.mark.parametrize("href", ["mailto:a@b.com", "tel:+15551234", "#top", "/relative/page", "page.html", "javascript:void(0)", "ftp://x.com/f", "https:///nohost", "", "{{ footer }}"])
def test_links_that_are_not_web_links_are_never_touched(href):
    markup = f'<a href="{href}">x</a>'
    assert H.tag_links(markup, TAGS) == markup


def test_a_label_the_link_already_has_is_never_overwritten_or_repeated():
    marked = '<a href="https://x.com/?utm_source=mine&amp;utm_campaign=theirs">go</a>'
    out = H.tag_links(marked, TAGS)
    assert "utm_source=mine" in out and "utm_campaign=theirs" in out and out.count("utm_source") == 1 and out.count("utm_campaign") == 1 and "utm_medium=outreach" in out and "utm_content=intro_A" in out
    assert H.tag_links('<a href="https://x.com/?UTM_SOURCE=mine">go</a>', {"utm_source": "email"}) == '<a href="https://x.com/?UTM_SOURCE=mine">go</a>'


def test_tagging_twice_is_the_same_as_tagging_once():
    once = H.tag_links('<p><a href="https://x.com/a?b=1">1</a> <a href="https://y.com">2</a></p>', TAGS)
    assert H.tag_links(once, TAGS) == once


def test_every_link_in_the_email_is_tagged_and_nothing_else_changes():
    markup = '<p>See https://plain.example.com in text</p><a href="https://a.com">a</a><img src="https://img.com/p.png" alt="p"><a href="mailto:x@y.z">m</a><a href="https://b.com/?q=1">b</a>'
    out = H.tag_links(markup, TAGS)
    assert out.count("utm_source=email") == 2 and "https://plain.example.com in text" in out and 'src="https://img.com/p.png"' in out and 'href="mailto:x@y.z"' in out


def test_without_labels_or_without_markup_nothing_changes():
    assert H.tag_links('<a href="https://x.com">x</a>', None) == '<a href="https://x.com">x</a>' and H.tag_links('<a href="https://x.com">x</a>', {}) == '<a href="https://x.com">x</a>'
    assert H.tag_links("", TAGS) == "" and H.tag_links(None, TAGS) is None


def test_values_are_url_encoded_and_the_result_is_safe_inside_an_attribute():
    out = H.tag_links('<a href="https://x.com">x</a>', {"utm_content": 'a b&c"d<e>'})
    value = re.search(r'href="([^"]*)"', out).group(1)
    assert "utm_content=a+b%26c%22d%3Ce%3E" in value and "<" not in value and '"' not in value and out.count('"') == 2 and H.unsafe_problems(out) == []


@pytest.mark.parametrize("href", ['https://x.com/"><script>alert(1)</script>', "https://[broken", "https://x.com/\n\tpath", "http://a b.com", 'https://x.com/?a="b"', "https://x.com/?a=&lt;b&gt;"])
def test_a_hostile_or_broken_address_never_crashes_and_tagging_never_adds_markup(href):
    markup = f'<a href="{href}">x</a>'
    out = H.tag_links(markup, TAGS)
    assert isinstance(out, str) and out.count("<") == markup.count("<") and out.count("<script") == markup.count("<script")


def test_the_starter_layouts_keep_passing_every_check_when_their_links_are_tagged():
    for layout in H.STARTER_LAYOUTS.values():
        tagged = H.tag_links(layout["html"], TAGS)
        assert H.html_checks(tagged, H.starter_plain_text(), ["FirstName"], FOOTER) == {"errors": [], "warnings": []}


# ================================================================ adding and removing the footer in a design

def test_a_design_is_said_to_have_the_footer_only_when_it_has_the_placeholder():
    assert H.footer_in_design("<p>x</p>{{ footer }}") and H.footer_in_design("{{footer}}") and not H.footer_in_design("<p>x</p>") and not H.footer_in_design("") and not H.footer_in_design(None)
    assert not H.footer_in_design("<!-- footer -->") and not H.footer_in_design("{{ FooterNote }}")


def test_adding_the_footer_puts_it_just_before_the_end_of_the_page():
    assert H.add_footer_placeholder("<html><body><p>x</p></body></html>") == "<html><body><p>x</p>{{ footer }}\n</body></html>"
    assert H.add_footer_placeholder("<HTML><BODY><p>x</p></BODY></HTML>") == "<HTML><BODY><p>x</p>{{ footer }}\n</BODY></HTML>"


def test_a_fragment_with_no_body_tag_gets_the_footer_at_the_end():
    assert H.add_footer_placeholder("<p>x</p>") == "<p>x</p>\n{{ footer }}\n" and H.add_footer_placeholder("<p>x</p>\n\n") == "<p>x</p>\n{{ footer }}\n" and H.add_footer_placeholder("") == "\n{{ footer }}\n"


def test_adding_the_footer_twice_changes_nothing_the_second_time():
    once = H.add_footer_placeholder("<body><p>x</p></body>")
    assert H.add_footer_placeholder(once) == once and once.count("{{ footer }}") == 1
    already = "<p>x</p><div>{{ footer }}</div>"
    assert H.add_footer_placeholder(already) == already


def test_a_design_with_the_footer_added_has_it_and_passes_the_opt_out_check():
    bare = "<html><body><p>Hello {{ FirstName }}, plenty of readable text goes in this paragraph so the email is not thin.</p></body></html>"
    assert not H.has_opt_out(bare) and H.has_opt_out(H.add_footer_placeholder(bare)) and H.footer_in_design(H.add_footer_placeholder(bare))


def test_removing_the_footer_takes_a_row_that_holds_only_the_placeholder_with_it():
    layout = H.starter_html("announcement")
    out = H.remove_footer_placeholder(layout)
    assert "{{ footer }}" not in out and "<tr><td></td></tr>" not in out and out.count("<tr>") == layout.count("<tr>") - 1 and out.endswith("</table>\n</td></tr></table>\n</body>\n</html>\n")


def test_removing_the_footer_never_touches_a_spacer_row_the_designer_made_on_purpose():
    design = "<table><tr><td height='20'></td></tr><tr><td>text</td></tr><tr><td>{{ footer }}</td></tr><tr><td></td></tr></table>"
    out = H.remove_footer_placeholder(design)
    assert out == "<table><tr><td height='20'></td></tr><tr><td>text</td></tr><tr><td></td></tr></table>"


def test_a_placeholder_alone_on_its_own_line_takes_the_line_with_it():
    assert H.remove_footer_placeholder("<p>a</p>\n  {{ footer }}  \n</body>") == "<p>a</p>\n</body>" and H.add_footer_placeholder("<p>a</p>\n</body>") == "<p>a</p>\n{{ footer }}\n</body>"


def test_a_placeholder_that_is_not_alone_in_a_row_is_removed_but_its_neighbours_stay():
    assert H.remove_footer_placeholder("<div>Bye {{ footer }} now</div>") == "<div>Bye  now</div>" and H.remove_footer_placeholder("<p>a</p>{{footer}}{{ footer }}") == "<p>a</p>"


def test_removing_a_footer_that_is_not_there_changes_nothing_and_adding_then_removing_returns_the_start():
    assert H.remove_footer_placeholder("<p>x</p>") == "<p>x</p>" and H.remove_footer_placeholder("") == "" and H.remove_footer_placeholder(None) == ""
    for layout in H.STARTER_LAYOUTS.values():
        stripped = H.remove_footer_placeholder(layout["html"])
        assert H.remove_footer_placeholder(H.add_footer_placeholder(stripped)) == stripped


def test_a_footer_row_written_in_capitals_is_removed_as_a_row_too():
    assert H.remove_footer_placeholder("<TABLE><TR><TD>{{ footer }}</TD></TR><TR><TD>x</TD></TR></TABLE>") == "<TABLE><TR><TD>x</TD></TR></TABLE>"
    assert H.remove_footer_placeholder("<tr class='f'>\n  <td style='x'>\n {{footer}}\n </td>\n</tr>\n<tr><td>y</td></tr>") == "<tr><td>y</td></tr>"
