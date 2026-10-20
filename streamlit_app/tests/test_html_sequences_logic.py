"""The pair rule for HTML campaigns: an email is always BOTH its .txt and its .html, whether it is added or deleted."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import html_campaign_logic as hc  # noqa: E402
import html_sequences_logic as HS  # noqa: E402


@pytest.mark.parametrize("cfg, expected", [({"format": "html"}, True), ({"format": "text"}, False), ({}, False), (None, False), ({"format": "HTML"}, False), ({"format": None}, False)])
def test_only_a_campaign_whose_format_is_exactly_html_is_an_html_campaign(cfg, expected):
    assert HS.is_html_campaign(cfg) is expected


def test_the_html_path_is_the_txt_path_with_the_other_ending():
    assert HS.html_path_for("templates/C/intro_A.txt") == "templates/C/intro_A.html"
    assert HS.html_path_for("templates/C/intro_A.html") == "templates/C/intro_A.html" and HS.html_path_for("config/campaigns/C.yaml") == "config/campaigns/C.yaml"


def test_every_new_txt_gets_a_starter_html_beside_it_in_the_same_save():
    files = [{"path": "templates/C/intro_B.txt", "content": b"t"}, {"path": "templates/C/followup1_B.txt", "content": b"t2"}]
    out = HS.with_html_companions(files)
    assert [f["path"] for f in out] == ["templates/C/intro_B.txt", "templates/C/followup1_B.txt", "templates/C/intro_B.html", "templates/C/followup1_B.html"]
    assert out[:2] == files and out[2]["content"] == hc.starter_html(hc.DEFAULT_LAYOUT).encode("utf-8")


def test_a_design_already_in_the_list_is_kept_and_not_duplicated_and_other_files_are_untouched():
    files = [{"path": "t/intro_A.txt", "content": b"t"}, {"path": "t/intro_A.html", "content": b"mine"}, {"path": "config/c.yaml", "content": b"y"}]
    out = HS.with_html_companions(files)
    assert out == files


def test_the_layout_can_be_chosen_and_an_empty_list_stays_empty():
    assert HS.with_html_companions([{"path": "t/a.txt", "content": b"x"}], layout="announcement")[1]["content"] == hc.starter_html("announcement").encode("utf-8")
    assert HS.with_html_companions([]) == []


def test_deleting_removes_every_text_file_first_and_then_every_design():
    paths = ["templates/C/intro_A.txt", "templates/C/followup1_A.txt"]
    assert HS.with_html_deletions(paths) == ["templates/C/intro_A.txt", "templates/C/followup1_A.txt", "templates/C/intro_A.html", "templates/C/followup1_A.html"]
    assert paths == ["templates/C/intro_A.txt", "templates/C/followup1_A.txt"]               # the input is never changed


def test_deleting_does_not_repeat_a_path_and_ignores_non_text_paths():
    assert HS.with_html_deletions(["t/a.txt", "t/a.html"]) == ["t/a.txt", "t/a.html"] and HS.with_html_deletions(["config/c.yaml"]) == ["config/c.yaml"] and HS.with_html_deletions([]) == []


# ================================================================ what blocks a launch

GOOD_DESIGN = "<p>Hi {{ FirstName }}, there is plenty of readable text in this paragraph so that the email is not thin at all.</p>{{ footer }}"
GOOD_FOOTER = {"business_name": "K", "address": "A"}


def _email(label="Intro · Variant A", html=GOOD_DESIGN, body="Hello {{ footer }}", problem=None):
    return {"label": label, "html": html, "body": body, "problem": problem}


def test_nothing_blocks_a_launch_when_every_email_passes_and_warnings_never_block():
    assert HS.launch_blockers([_email(), _email("Follow-up 1 · Variant A", body="Just some plain words here")], ["FirstName"], GOOD_FOOTER) == []         # the second only earns a warning
    assert HS.launch_blockers([], ["FirstName"], GOOD_FOOTER) == []


def test_the_plain_text_sample_used_for_the_warning_case_really_does_produce_a_warning():
    import html_campaign_logic as hc2
    assert any("no opt-out line" in w for w in hc2.html_checks(GOOD_DESIGN, "Just some plain words here", ["FirstName"], GOOD_FOOTER)["warnings"])


def test_every_error_is_listed_against_the_email_it_belongs_to():
    out = HS.launch_blockers([_email(html=GOOD_DESIGN + "<script>x</script>"), _email("Follow-up 1 · Variant B", html=GOOD_DESIGN + "{{ Nope }}")], ["FirstName"], GOOD_FOOTER)
    assert len(out) == 2 and out[0].startswith("Intro · Variant A: It contains <script>") and out[1].startswith("Follow-up 1 · Variant B: These placeholders match no Sheet column")


def test_a_missing_design_and_an_unreadable_one_block_with_their_own_wording():
    out = HS.launch_blockers([_email(html=None), _email("B", problem="500 server error")], ["FirstName"], GOOD_FOOTER)
    assert out == ["Intro · Variant A: has no designed (.html) file yet.", "B: could not be read (500 server error)."]


def test_an_incomplete_footer_blocks_every_email_that_uses_it_and_none_that_do_not():
    own = "<p>Hello {{ FirstName }}, enough readable text in this paragraph to avoid the thin warning, and you can unsubscribe by replying.</p>"
    out = HS.launch_blockers([_email(), _email("Own footer", html=own)], ["FirstName"], {"business_name": "K"})
    assert len(out) == 1 and out[0].startswith("Intro · Variant A:") and "footer is incomplete" in out[0]


def test_unknown_columns_are_not_blockers_when_the_sheets_columns_are_unknown():
    assert HS.launch_blockers([_email(html=GOOD_DESIGN + "{{ AnyColumn }}")], None, GOOD_FOOTER) == []


def test_an_email_with_no_label_still_gets_a_sensible_one():
    assert HS.launch_blockers([{"html": None}], None, None) == ["An email: has no designed (.html) file yet."]
