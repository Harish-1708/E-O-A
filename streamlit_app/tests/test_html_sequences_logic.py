"""The pair rule for HTML campaigns: an email is always BOTH its .txt and its .html, whether it is added or deleted."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import html_sequences_logic as HS  # noqa: E402


@pytest.mark.parametrize("cfg, expected", [({"format": "html"}, True), ({"format": "text"}, False), ({}, False), (None, False), ({"format": "HTML"}, False), ({"format": None}, False)])
def test_only_a_campaign_whose_format_is_exactly_html_is_an_html_campaign(cfg, expected):
    assert HS.is_html_campaign(cfg) is expected


def test_the_html_path_is_the_txt_path_with_the_other_ending():
    assert HS.html_path_for("templates/C/intro_A.txt") == "templates/C/intro_A.html"
    assert HS.html_path_for("templates/C/intro_A.html") == "templates/C/intro_A.html" and HS.html_path_for("config/campaigns/C.yaml") == "config/campaigns/C.yaml"


def test_deleting_removes_every_text_file_first_and_then_every_design():
    paths = ["templates/C/intro_A.txt", "templates/C/followup1_A.txt"]
    assert HS.with_html_deletions(paths) == ["templates/C/intro_A.txt", "templates/C/followup1_A.txt", "templates/C/intro_A.html", "templates/C/followup1_A.html"]
    assert paths == ["templates/C/intro_A.txt", "templates/C/followup1_A.txt"]               # the input is never changed


def test_deleting_does_not_repeat_a_path_and_ignores_non_text_paths():
    assert HS.with_html_deletions(["t/a.txt", "t/a.html"]) == ["t/a.txt", "t/a.html"] and HS.with_html_deletions(["config/c.yaml"]) == ["config/c.yaml"] and HS.with_html_deletions([]) == []


# ================================================================ what blocks a launch

GOOD_DESIGN = "<p>Hi {{ FirstName }}, there is plenty of readable text in this paragraph so that the email is not thin at all.</p>{{ footer }}"
GOOD_FOOTER = {"business_name": "K", "address": "A"}


def _email(label="Intro · Variant A", html=GOOD_DESIGN, subject="Hello", first=False, problem=None):
    return {"label": label, "html": html, "subject": subject, "first": first, "problem": problem}


def test_nothing_blocks_a_launch_when_every_email_passes_and_warnings_never_block():
    styled = "<style>p{color:red}</style>" + GOOD_DESIGN                                         # earns only a warning
    assert HS.launch_blockers([_email(first=True), _email("Follow-up 1 · Variant A", html=styled, subject="")], ["FirstName"], GOOD_FOOTER) == []
    assert HS.launch_blockers([], ["FirstName"], GOOD_FOOTER) == []


def test_every_error_is_listed_against_the_email_it_belongs_to():
    out = HS.launch_blockers([_email(html=GOOD_DESIGN + "<script>x</script>"), _email("Follow-up 1 · Variant B", html=GOOD_DESIGN + "{{ Nope }}")], ["FirstName"], GOOD_FOOTER)
    assert len(out) == 2 and out[0].startswith("Intro · Variant A: It contains <script>") and out[1].startswith("Follow-up 1 · Variant B: These placeholders match no Sheet column")


def test_the_first_email_must_have_a_subject_but_a_follow_up_may_leave_it_blank_to_continue_the_thread():
    assert HS.launch_blockers([_email(first=True, subject="")], ["FirstName"], GOOD_FOOTER) == ["Intro · Variant A: the subject is empty. The first email of a sequence needs one."]
    assert HS.launch_blockers([_email(first=True, subject="   ")], ["FirstName"], GOOD_FOOTER) == ["Intro · Variant A: the subject is empty. The first email of a sequence needs one."]
    assert HS.launch_blockers([_email(first=False, subject="")], ["FirstName"], GOOD_FOOTER) == []
    assert HS.launch_blockers([_email(first=True, subject=None)], ["FirstName"], GOOD_FOOTER)[0].endswith("needs one.")


def test_an_empty_subject_and_a_design_problem_on_the_same_email_are_both_listed_subject_first():
    out = HS.launch_blockers([_email(first=True, subject="", html=GOOD_DESIGN + "<form></form>")], ["FirstName"], GOOD_FOOTER)
    assert len(out) == 2 and "subject is empty" in out[0] and "must not have" in out[1]


def test_a_missing_design_and_an_unreadable_one_block_with_their_own_wording():
    out = HS.launch_blockers([_email(html=None), _email("B", problem="500 server error")], ["FirstName"], GOOD_FOOTER)
    assert out == ["Intro · Variant A: has no designed (.html) file yet.", "B: could not be read (500 server error)."]


def test_an_unreadable_email_is_reported_once_and_not_also_as_a_missing_subject():
    assert HS.launch_blockers([_email(first=True, subject="", html=None, problem="boom")], None, None) == ["Intro · Variant A: could not be read (boom)."]


def test_an_incomplete_footer_blocks_every_email_that_uses_it_and_none_that_do_not():
    own = "<p>Hello {{ FirstName }}, enough readable text in this paragraph to avoid the thin warning, and you can unsubscribe by replying.</p>"
    out = HS.launch_blockers([_email(), _email("Own footer", html=own)], ["FirstName"], {"business_name": "K"})
    assert len(out) == 1 and out[0].startswith("Intro · Variant A:") and "footer is incomplete" in out[0]


def test_unknown_columns_are_not_blockers_when_the_sheets_columns_are_unknown():
    assert HS.launch_blockers([_email(html=GOOD_DESIGN + "{{ AnyColumn }}")], None, GOOD_FOOTER) == []


def test_an_email_with_no_label_still_gets_a_sensible_one():
    assert HS.launch_blockers([{"html": None}], None, None) == ["An email: has no designed (.html) file yet."]
