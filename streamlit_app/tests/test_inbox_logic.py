"""Tests for the Inbox view's pure logic (inbox_logic.py)."""
import os
import re
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import inbox_logic as il  # noqa: E402  (puts the repo root on sys.path via its own imports)
from responses_hub_logic import group_responses_by_lead, tag_responses_with_campaign  # noqa: E402
from conversation_logic import filter_responses_for_lead  # noqa: E402

NOW = datetime(2026, 10, 12, 15, 0, 0)


# ================================================================ md_escape — hostile email text

HOSTILE = [
    "[click me](http://evil.example/steal)",
    "![tracking pixel](http://evil.example/p.png)",
    ":red[SCAM] and :green-background[more]",
    "Pay $100 or $$\\sqrt{x}$$ now",
    "**bold** __bold__ ~~strike~~ `code`",
    "<script>alert(1)</script> <img src=x onerror=alert(1)>",
    "# Heading\n> quote\n- list\n1. numbered",
    "Smile :smile: :+1:",
    "https://evil.example/autolink <https://evil.example/angle>",
]


def _unescaped_punctuation(text):
    """Punctuation that is NOT protected by a preceding backslash. A backslash
    plus the character it protects is one escape pair and is skipped whole."""
    found, i = [], 0
    while i < len(text):
        if text[i] == "\\":
            assert i + 1 < len(text) and re.match(r"[!-/:-@\[-`{-~]", text[i + 1]), f"dangling backslash in {text!r}"
            i += 2
            continue
        if re.match(r"[!-/:-@\[-`{-~]", text[i]):
            found.append((i, text[i]))
        i += 1
    return found


@pytest.mark.parametrize("hostile", HOSTILE)
def test_every_markdown_character_in_untrusted_text_is_escaped(hostile):
    escaped = il.md_escape(hostile)
    assert _unescaped_punctuation(escaped) == [], escaped
    assert "\n" not in escaped


@pytest.mark.parametrize("hostile", HOSTILE)
def test_escaped_text_renders_as_plain_text_with_no_link_image_or_markup(hostile):
    """The real outcome, through a real CommonMark renderer (Streamlit's
    Markdown follows CommonMark): no link, image, emphasis, heading, quote,
    list or HTML element can come out of untrusted text."""
    markdown_it = pytest.importorskip("markdown_it")
    html = markdown_it.MarkdownIt("commonmark").render(il.md_escape(hostile))
    for element in ("<a ", "<img", "<strong", "<em", "<h1", "<blockquote", "<ul", "<ol", "<code", "<script", "<del"):
        assert element not in html, f"{element!r} in {html!r}"
    visible = re.sub(r"<[^>]+>", "", html)
    assert visible.strip() != ""


def test_the_displayed_text_is_the_original_text():
    """Escaping must not change what a person READS."""
    markdown_it = pytest.importorskip("markdown_it")
    import html as html_lib
    for original in ("Re: Hello, world!", "Price is $5 (approx.) - ok?", "50% off [today] #1 @home"):
        rendered = markdown_it.MarkdownIt("commonmark").render(il.md_escape(original))
        assert html_lib.unescape(re.sub(r"<[^>]+>", "", rendered)).strip() == original


def test_escaping_does_not_change_how_ordinary_text_reads():
    assert il.md_escape("Kari B") == "Kari B"
    assert il.md_escape("  Re:   Hello,\n world!  ") == r"Re\: Hello\, world\!"
    assert il.md_escape(None) == "" and il.md_escape("") == ""
    assert il.md_escape(12345) == "12345"


# ================================================================ relative_time

@pytest.mark.parametrize("value, expected", [
    ("2026-10-12 14:59:30", "just now"),
    ("2026-10-12 15:00:30", "just now"),               # slightly in the future: clock skew, never negative
    ("2026-10-12 14:55:00", "5m ago"),
    ("2026-10-12 14:01:00", "59m ago"),
    ("2026-10-12 13:00:00", "2h ago"),
    ("2026-10-11 16:00:00", "23h ago"),
    ("2026-10-11 10:00:00", "Yesterday"),
    ("2026-10-10 09:00:00", "2d ago"),
    ("2026-10-06 15:00:00", "6d ago"),
    ("2026-10-05 15:00:00", "05 Oct"),                  # a week or more: a plain date
    ("2025-12-25 09:00:00", "25 Dec 2025"),             # another year: with the year
    ("", ""),
    ("not a date", ""),
    (None, ""),
])
def test_relative_time(value, expected):
    assert il.relative_time(value, NOW) == expected


# ================================================================ preview_text / sender_display

def test_preview_collapses_whitespace_and_truncates_with_an_ellipsis():
    assert il.preview_text("  Hi\n\n  there   friend ") == "Hi there friend"
    long = "word " * 40
    out = il.preview_text(long)
    assert len(out) <= il.PREVIEW_LENGTH and out.endswith("…")
    assert il.preview_text("x" * il.PREVIEW_LENGTH) == "x" * il.PREVIEW_LENGTH     # exactly at the limit: untouched
    assert il.preview_text("") == "(no preview)" and il.preview_text(None) == "(no preview)"


@pytest.mark.parametrize("raw, name, email", [
    ('"Kari B" <kari@x.com>', "Kari B", "kari@x.com"),
    ("Kari <kari@x.com>", "Kari", "kari@x.com"),
    ("kari.b@x.com", "kari.b", "kari.b@x.com"),                   # bare address: the part before the @
    ("", "(unknown sender)", ""),
    (None, "(unknown sender)", ""),
])
def test_sender_display(raw, name, email):
    assert il.sender_display(raw) == {"name": name, "email": email}


# ================================================================ the list

def _resp(rid, lead, campaign, received, **extra):
    base = {"ResponseID": rid, "LeadID": lead, "From": f'"Person {lead}" <p{lead}@x.com>', "Subject": f"Re: Hi {lead}",
            "Snippet": "Sounds interesting, tell me more", "Classification": "Genuine Reply", "ReceivedAt": received,
            "ActionTaken": "Stopped Sequence"}
    base.update(extra)
    return tag_responses_with_campaign([base], campaign)[0]


def _groups(responses):
    return il.sort_conversation_groups_newest_first(group_responses_by_lead(responses)) \
        if hasattr(il, "sort_conversation_groups_newest_first") else sorted(
            group_responses_by_lead(responses), key=lambda g: g["latest"]["ReceivedAt"], reverse=True)


def test_each_conversation_becomes_one_row_with_everything_the_list_shows():
    responses = [_resp("a1", "1", "C", "2026-10-12 14:55:00", Intent="Interested", IntentConfidence="High"),
                 _resp("a2", "1", "C", "2026-10-10 09:00:00"),
                 _resp("b1", "2", "C", "2026-10-11 10:00:00", Snippet="")]
    leads = {"C": [{"LeadID": "1", "Status": "Stopped - Replied"}, {"LeadID": "2", "Status": "Intro Sent"}]}
    rows = il.build_conversation_rows(_groups(responses), set(), lambda c: leads[c], NOW)
    first, second = rows
    assert first["key"] == "C:1" and first["name"] == "Person 1" and first["email"] == "p1@x.com"
    assert first["subject"] == "Re: Hi 1" and first["when"] == "5m ago" and first["message_count"] == 2
    assert (first["intent"], first["intent_confidence"]) == ("Interested", "High")
    assert first["stopped_status"] == "Stopped - Replied" and first["action"] == "Stopped Sequence"
    assert second["key"] == "C:2" and second["preview"] == "(no preview)" and second["stopped_status"] == ""
    assert second["when"] == "Yesterday"


def test_unread_counts_come_from_the_read_keys_and_are_never_shown_when_read_state_is_not_tracked():
    responses = [_resp("a1", "1", "C", "2026-10-12 14:00:00"), _resp("a2", "1", "C", "2026-10-12 14:30:00")]
    leads = lambda c: []
    none_read = il.build_conversation_rows(_groups(responses), set(), leads, NOW)[0]
    assert none_read["unread"] is True and none_read["unread_count"] == 2
    one_read = il.build_conversation_rows(_groups(responses), {"C:a1"}, leads, NOW)[0]
    assert one_read["unread_count"] == 1
    all_read = il.build_conversation_rows(_groups(responses), {"C:a1", "C:a2"}, leads, NOW)[0]
    assert all_read["unread"] is False and all_read["unread_count"] == 0
    untracked = il.build_conversation_rows(_groups(responses), None, leads, NOW)[0]
    assert untracked["unread"] is False and untracked["unread_count"] == 0       # a campaign's own tab has no read state


def test_two_campaigns_with_the_same_lead_id_are_two_different_rows():
    responses = [_resp("a1", "5", "A", "2026-10-12 14:00:00"), _resp("b1", "5", "B", "2026-10-12 14:30:00")]
    rows = il.build_conversation_rows(_groups(responses), set(), lambda c: [], NOW)
    assert [r["key"] for r in rows] == ["B:5", "A:5"]


def test_a_response_with_no_matching_lead_still_gets_a_row():
    rows = il.build_conversation_rows(_groups([_resp("a1", "99", "A", "2026-10-12 14:00:00")]), set(), lambda c: [], NOW)
    assert len(rows) == 1 and rows[0]["stopped_status"] == ""


@pytest.mark.parametrize("keys, selected, expected", [
    (["A:1", "B:2"], "B:2", "B:2"),
    (["A:1", "B:2"], "C:9", "A:1"),               # the picked one was filtered out: fall back to the first
    (["A:1", "B:2"], None, "A:1"),
    ([], "A:1", None),
])
def test_resolve_selection(keys, selected, expected):
    assert il.resolve_selection(keys, selected) == expected


def test_our_messages_sit_on_the_right_and_theirs_on_the_left():
    assert il.bubble_side({"direction": "outgoing"}) == "right"
    assert il.bubble_side({"direction": "incoming"}) == "left"
    assert il.bubble_side({}) == "left"


# ================================================================ the cross-campaign bug

def test_a_conversation_never_includes_another_campaigns_replies_that_share_a_lead_id():
    """REGRESSION. Lead IDs restart in every campaign. The all-campaigns page
    used to build Campaign A / lead 5's thread from every campaign's replies for
    'lead 5', showing a different person's message from Campaign B."""
    a = tag_responses_with_campaign([{"ResponseID": "a1", "LeadID": "5", "From": "kari@a.com"}], "Campaign_A")
    b = tag_responses_with_campaign([{"ResponseID": "b1", "LeadID": "5", "From": "someone-else@b.com"}], "Campaign_B")
    both = a + b
    assert [r["From"] for r in filter_responses_for_lead(both, "5", "Campaign_A")] == ["kari@a.com"]
    assert [r["From"] for r in filter_responses_for_lead(both, "5", "Campaign_B")] == ["someone-else@b.com"]


def test_without_a_campaign_the_lookup_behaves_exactly_as_before():
    both = tag_responses_with_campaign([{"ResponseID": "a1", "LeadID": "5"}], "A") + \
           tag_responses_with_campaign([{"ResponseID": "b1", "LeadID": "5"}], "B")
    assert len(filter_responses_for_lead(both, "5")) == 2


def test_a_campaigns_own_untagged_responses_always_pass_the_campaign_check():
    """The per-campaign tab passes untagged responses; naming the campaign must not hide them."""
    untagged = [{"ResponseID": "a1", "LeadID": "5"}, {"ResponseID": "a2", "LeadID": "6"}]
    assert [r["ResponseID"] for r in filter_responses_for_lead(untagged, "5", "AnyCampaign")] == ["a1"]
