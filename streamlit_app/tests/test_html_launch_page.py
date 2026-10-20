"""Launching an HTML campaign through the real campaign page: Confirm Launch is blocked while any email fails the checks, and nothing changes for a plain-text campaign."""
import contextlib
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import outreach  # noqa: E402
from github_client import GitHubActionsError  # noqa: E402
from test_html_editor_page import DESIGN, FOOTER, html_world  # noqa: E402
from test_sequences_page import FIXTURE_CAMPAIGN, _every_test_uses_the_fixture_repo, _clear_streamlit_caches, go, seq_page  # noqa: E402

_AUTOUSE_FIXTURES = (_every_test_uses_the_fixture_repo, _clear_streamlit_caches)
_REAL = outreach.get_campaign


def _draft_cfg(fmt="html", footer=True):
    def get(*a, **k):
        cfg = _REAL(*a, **k)
        cfg["status"] = "draft"
        cfg["format"] = fmt
        if footer:
            cfg["footer"] = dict(FOOTER)
        return cfg
    return get


@contextlib.contextmanager
def launching(world=None, fmt="html", footer=True, **kwargs):
    with patch.object(outreach, "get_campaign", _draft_cfg(fmt, footer)):
        with seq_page(world=world or html_world(), **kwargs) as (at, w):
            next(b for b in at.button if b.label == "🚀 Launch").click()
            go(at)
            yield at, w


def confirm(at):
    return next(b for b in at.button if b.label == "Confirm Launch")


def blocked_message(at):
    return " ".join(e.value for e in at.error if "cannot launch yet" in e.value)


def test_a_valid_html_campaign_can_be_launched():
    with launching() as (at, _):
        assert list(at.exception) == [] and confirm(at).disabled is False and blocked_message(at) == ""


def test_a_script_in_one_design_blocks_the_launch_and_names_the_email():
    world = html_world()
    world.html["followup2_C.html"] = DESIGN + "<script>alert(1)</script>"
    with launching(world=world) as (at, _):
        assert confirm(at).disabled is True
        message = blocked_message(at)
        assert "Follow-up 2 · Variant C" in message and "<script>" in message and message.count("\n- ") == 1


def test_an_email_with_no_design_file_blocks_the_launch():
    world = html_world()
    world.files.discard("followup1_B.html")
    with launching(world=world) as (at, _):
        assert confirm(at).disabled is True and "Follow-up 1 · Variant B: has no designed (.html) file yet." in blocked_message(at)


def test_an_incomplete_footer_blocks_the_launch_of_every_email_that_uses_it():
    with launching(footer=False) as (at, _):
        message = blocked_message(at)
        assert confirm(at).disabled is True and "footer is incomplete" in message and message.count("\n- ") == 20          # 5 stages x 4 variants


def test_a_design_that_cannot_be_read_blocks_the_launch_rather_than_being_waved_through():
    def failing(self, path, ref="main"):
        if path.endswith("intro_A.html"):
            raise GitHubActionsError("Failed to read: 500 server error")
        return b"Subject: S\n\nBody {{ footer }}"
    with patch.object(outreach, "get_campaign", _draft_cfg()):
        with seq_page(world=html_world()) as (at, _):
            with patch("github_client.GitHubClient.get_file_content", failing):
                next(b for b in at.button if b.label == "🚀 Launch").click()
                go(at)
                assert confirm(at).disabled is True and "Intro · Variant A: could not be read" in blocked_message(at)


def test_a_plain_text_campaign_launches_exactly_as_before_with_no_html_checks():
    with launching(fmt="text") as (at, _):
        assert confirm(at).disabled is False and blocked_message(at) == ""


def test_cancel_closes_the_confirmation_even_when_launching_is_blocked():
    world = html_world()
    world.html["intro_A.html"] = "<script>x</script>"
    with launching(world=world) as (at, _):
        next(b for b in at.button if b.key == "cancel_launch_button").click()
        go(at)
        assert not any(b.label == "Confirm Launch" for b in at.button)


def test_a_plain_text_campaign_with_no_html_files_at_all_is_never_held_up_by_html_checks():
    with launching(world=html_world(with_html=False), fmt="text", footer=False) as (at, _):
        assert confirm(at).disabled is False and blocked_message(at) == "" and list(at.exception) == []


def test_a_footer_saved_a_moment_ago_counts_at_once_at_launch_before_the_app_catches_up():
    saved = {"business_name": "Kelson Agency", "address": "12 Example Street", "opt_out": ""}
    with launching(footer=False, preset={f"footer_saved_{FIXTURE_CAMPAIGN}": saved}) as (at, _):
        assert confirm(at).disabled is False and blocked_message(at) == ""
    with launching(footer=False) as (at, _):                                      # without it the same campaign is still held
        assert confirm(at).disabled is True and "footer is incomplete" in blocked_message(at)


def test_an_empty_subject_on_the_first_email_of_any_variant_blocks_the_launch_and_names_it():
    world = html_world()
    world.write("intro_B.txt", "", "body")
    with launching(world=world) as (at, _):
        message = blocked_message(at)
        assert confirm(at).disabled is True and "Intro · Variant B: the subject is empty. The first email of a sequence needs one." in message and message.count("\n- ") == 1


def test_follow_ups_with_a_blank_subject_never_block_the_launch_because_a_blank_subject_means_continue_the_thread():
    with launching() as (at, _):                                              # the fake campaign's follow-ups all have blank subjects
        assert confirm(at).disabled is False and blocked_message(at) == ""


def test_a_subject_file_that_cannot_be_read_blocks_the_launch_with_its_own_reason():
    from github_client import GitHubClient

    def selective(self, path, ref="main"):
        if path.endswith("followup1_C.txt"):
            raise GitHubActionsError("Failed to read: 500 server error")
        return original(self, path, ref)

    with patch.object(outreach, "get_campaign", _draft_cfg()):
        with seq_page(world=html_world()) as (at, _):
            original = GitHubClient.get_file_content
            with patch("github_client.GitHubClient.get_file_content", selective):
                next(b for b in at.button if b.label == "🚀 Launch").click()
                go(at)
                assert confirm(at).disabled is True and "Follow-up 1 · Variant C: could not be read (its subject file could not be read" in blocked_message(at)


def test_the_launch_check_no_longer_cares_what_an_old_hand_written_text_body_says():
    world = html_world()
    world.write("intro_A.txt", "A subject", "")                               # no text body at all: fine, the text half is made from the design
    with launching(world=world) as (at, _):
        assert confirm(at).disabled is False and blocked_message(at) == ""
