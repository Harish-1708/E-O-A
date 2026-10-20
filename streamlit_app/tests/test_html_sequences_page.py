"""An HTML campaign's Sequences tab, through the real page: adding a variant or a follow-up stage is one click and writes the subject file AND the design of every email in one
commit; deleting removes the pair; and a plain-text campaign beside it is untouched. Uses the Sequences page tests' fake GitHub (which also holds .html files)."""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import html_campaign_logic as hc  # noqa: E402
from campaign_builder import build_template_file_content  # noqa: E402
from github_client import GitHubActionsError, GitHubClient  # noqa: E402
from test_html_editor_page import DESIGN, area, editor, html_world, pick, subject_box, all_text  # noqa: E402
from test_sequences_page import FIXTURE_CAMPAIGN, LETTERS, STAGES, _every_test_uses_the_fixture_repo, _clear_streamlit_caches, go, seq_page  # noqa: E402

_AUTOUSE_FIXTURES = (_every_test_uses_the_fixture_repo, _clear_streamlit_caches)


def names(files):
    return sorted(f["path"].rsplit("/", 1)[-1] for f in files)


def add_variant_button(at):
    return next(b for b in at.button if b.key == f"html_add_variant_{FIXTURE_CAMPAIGN}")


def open_add_variant(at):
    add_variant_button(at).click()
    go(at)


def press(at, key):
    next(b for b in at.button if b.key == key).click()
    go(at)


# ================================================================ adding a variant

def test_add_variant_opens_a_one_click_panel_that_explains_the_copy_and_cancel_changes_nothing():
    with editor(world=html_world(letters="AB")) as (at, world):
        open_add_variant(at)
        text = all_text(at)
        assert "Add variant C" in text and "every stage" in text and "as a copy of variant A" in text and "shared out between the variants" in text
        assert not any(t.key and t.key.startswith("html_subject_") for t in at.text_input)                    # the workspace is replaced by the panel while it is open
        press(at, "html_add_variant_cancel")
        assert world.commits == [] and world.created == [] and any(t.key and t.key.startswith("html_subject_") for t in at.text_input)


def test_adding_a_variant_copies_variant_a_of_every_stage_subject_and_design_in_one_commit():
    world = html_world(letters="AB")
    world.html["intro_A.html"] = "<p>the intro design {{ footer }}</p>"
    world.write("followup1_A.txt", "Re: carry on", "body one")
    with editor(world=world) as (at, w):
        open_add_variant(at)
        press(at, "html_add_variant_go")
        assert list(at.exception) == [] and list(at.error) == [] and len(w.commits) == 1
        files = {f["path"].rsplit("/", 1)[-1]: f["content"].decode() for f in w.commits[0]["files"]}
        assert sorted(files) == sorted([f"{s}_C.txt" for s in STAGES] + [f"{s}_C.html" for s in STAGES])
        assert files["intro_C.html"] == "<p>the intro design {{ footer }}</p>" and files["followup1_C.html"] == DESIGN
        assert files["intro_C.txt"].startswith("Subject: Paid collaboration with {{CompanyName}}") and files["followup1_C.txt"].startswith("Subject: Re: carry on\n\nbody one")
        assert w.commits[0]["message"].startswith(f"Add variant C to {FIXTURE_CAMPAIGN} as a copy of variant A (via Streamlit, by ")


def test_after_adding_a_variant_the_page_returns_to_the_workspace_says_so_and_offers_the_new_variant():
    with editor(world=html_world(letters="AB")) as (at, _):
        open_add_variant(at)
        press(at, "html_add_variant_go")
        assert any("Variant C added to all 5 stage(s) as a copy of variant A" in s.value for s in at.success)
        pills = next(r for r in at.radio if r.key == f"html_variant_{FIXTURE_CAMPAIGN}")
        assert pills.options == ["Variant A", "Variant B", "Variant C"] and any(t.key and t.key.startswith("html_subject_") for t in at.text_input)
        pick(at, "intro", "C")
        assert area(at, "intro", "C").value == DESIGN and subject_box(at, "intro", "C").value == "Paid collaboration with {{CompanyName}}"


def test_a_stage_whose_source_has_no_design_gets_the_starter_design_for_the_new_variant():
    world = html_world(letters="AB")
    world.files.discard("followup2_A.html")
    with editor(world=world) as (at, w):
        open_add_variant(at)
        press(at, "html_add_variant_go")
        files = {f["path"].rsplit("/", 1)[-1]: f["content"].decode() for f in w.commits[0]["files"]}
        assert files["followup2_C.html"] == hc.starter_html(hc.DEFAULT_LAYOUT) and files["followup1_C.html"] == DESIGN


def test_if_the_source_cannot_be_read_nothing_is_added_and_the_error_says_so():
    with editor(world=html_world(letters="AB")) as (at, world):
        original = GitHubClient.get_file_content

        def selective(self, path, ref="main"):
            if path.endswith("followup3_A.txt"):
                raise GitHubActionsError("Failed to read: 500 server error")
            return original(self, path, ref)

        open_add_variant(at)
        with patch("github_client.GitHubClient.get_file_content", selective):
            press(at, "html_add_variant_go")
        assert world.commits == [] and any("Failed to add the variant" in e.value and "500" in e.value for e in at.error) and not any("added to all" in s.value for s in at.success)


def test_if_the_commit_fails_nothing_is_reported_as_added_and_the_panel_stays_open():
    with editor(world=html_world(letters="AB"), commit_error=GitHubActionsError("boom")) as (at, world):
        open_add_variant(at)
        press(at, "html_add_variant_go")
        assert world.commits == [] and any("Failed to add the variant: boom" in e.value for e in at.error) and any(b.key == "html_add_variant_go" for b in at.button)


def test_the_new_variant_letter_is_the_first_free_one():
    with editor(world=html_world(letters="AC")) as (at, _):
        open_add_variant(at)
        assert "Add variant B" in all_text(at)


# ================================================================ adding a follow-up stage

def test_add_stage_opens_a_one_click_panel_that_explains_the_blank_subject_and_the_stage_list_marks_it_new():
    with editor() as (at, world):
        press(at, "sq_add_stage_button")
        text = all_text(at)
        assert "Add Follow-up 5" in text and "one email for each variant (A, B, C, D)" in text and "blank subject" in text and "continues the same email thread" in text
        assert "New · 4 variants" in text and "ready" not in text.split("New · 4 variants")[1][:30]
        press(at, "html_add_stage_cancel")
        assert world.commits == [] and any(t.key and t.key.startswith("html_subject_") for t in at.text_input)


def test_adding_a_follow_up_stage_creates_a_blank_subject_and_the_starter_design_for_every_variant_in_one_commit():
    with editor() as (at, world):
        press(at, "sq_add_stage_button")
        press(at, "html_add_stage_go")
        assert list(at.exception) == [] and list(at.error) == [] and len(world.commits) == 1
        files = {f["path"].rsplit("/", 1)[-1]: f["content"] for f in world.commits[0]["files"]}
        assert sorted(files) == sorted([f"followup5_{v}.txt" for v in LETTERS] + [f"followup5_{v}.html" for v in LETTERS])
        assert all(files[f"followup5_{v}.txt"] == build_template_file_content("", "") for v in LETTERS)
        assert all(files[f"followup5_{v}.html"].decode() == hc.starter_html(hc.DEFAULT_LAYOUT) for v in LETTERS)
        assert world.commits[0]["message"].startswith(f"Add stage followup5 to {FIXTURE_CAMPAIGN} (via Streamlit, by ")


def test_after_adding_a_stage_the_page_returns_to_the_workspace_and_the_new_stage_can_be_opened():
    with editor() as (at, _):
        press(at, "sq_add_stage_button")
        press(at, "html_add_stage_go")
        assert any("Follow-up 5 added with 4 variant(s)" in s.value for s in at.success)
        assert any(b.key == f"sq_pick_{len(STAGES)}" for b in at.button)
        press(at, f"sq_pick_{len(STAGES)}")
        assert "Follow-up 5" in all_text(at) and subject_box(at, "followup5", "A").value == "" and area(at, "followup5", "A").value == hc.starter_html(hc.DEFAULT_LAYOUT)


def test_a_failed_stage_commit_reports_the_failure_and_adds_nothing():
    with editor(commit_error=GitHubActionsError("boom")) as (at, world):
        press(at, "sq_add_stage_button")
        press(at, "html_add_stage_go")
        assert world.commits == [] and any("Failed to add the stage: boom" in e.value for e in at.error) and not any("added with" in s.value for s in at.success)


def test_a_two_variant_campaign_gets_two_emails_in_its_new_stage():
    with editor(world=html_world(letters="AB")) as (at, world):
        press(at, "sq_add_stage_button")
        assert "one email for each variant (A, B)" in all_text(at)
        press(at, "html_add_stage_go")
        assert names(world.commits[0]["files"]) == ["followup5_A.html", "followup5_A.txt", "followup5_B.html", "followup5_B.txt"]


# ================================================================ deleting

def test_deleting_a_variant_from_an_html_campaign_removes_every_text_file_then_every_design_and_counts_stages_not_files():
    with editor() as (at, world):
        pick(at, "intro", "C")
        next(c for c in at.checkbox if c.key == "confirm_delete_variant_C").set_value(True)
        go(at)
        press(at, "delete_variant_button")
        assert list(at.exception) == [] and list(at.error) == []
        assert [p.rsplit("/", 1)[-1] for p in world.deleted] == [f"{s}_C.txt" for s in STAGES] + [f"{s}_C.html" for s in STAGES]
        assert any("Variant C deleted from all 5 stage(s)" in s.value for s in at.success)


def test_deleting_a_variant_from_a_text_campaign_removes_only_the_txt_files():
    with seq_page() as (at, world):
        next(r for r in at.radio if (r.key or "").startswith("sq_variant_")).set_value("C")
        go(at)
        next(c for c in at.checkbox if c.key == "confirm_delete_variant_C").set_value(True)
        go(at)
        press(at, "delete_variant_button")
        assert [p.rsplit("/", 1)[-1] for p in world.deleted] == [f"{s}_C.txt" for s in STAGES]


def test_deleting_the_last_stage_of_an_html_campaign_removes_every_text_file_then_every_design():
    with editor() as (at, world):
        next(c for c in at.checkbox if c.key == "confirm_delete_stage_followup4").set_value(True)
        go(at)
        press(at, "delete_stage_button")
        assert list(at.exception) == [] and list(at.error) == []
        assert [p.rsplit("/", 1)[-1] for p in world.deleted] == [f"followup4_{v}.txt" for v in LETTERS] + [f"followup4_{v}.html" for v in LETTERS]


def test_deleting_the_last_stage_of_a_text_campaign_removes_only_the_txt_files():
    with seq_page() as (at, world):
        next(c for c in at.checkbox if c.key == "confirm_delete_stage_followup4").set_value(True)
        go(at)
        press(at, "delete_stage_button")
        assert [p.rsplit("/", 1)[-1] for p in world.deleted] == [f"followup4_{v}.txt" for v in LETTERS]


# ================================================================ the plain-text flows are not used for an HTML campaign

def test_an_html_campaign_never_shows_the_plain_text_add_forms_and_a_text_campaign_still_does():
    with editor(world=html_world(letters="AB")) as (at, _):
        open_add_variant(at)
        assert not any((t.key or "").startswith(("newvariant_subject_", "newvariant_body_")) for t in list(at.text_input) + list(at.text_area))
    with seq_page(world=html_world(letters="AB", with_html=False)) as (at, _):
        press(at, "sq_add_variant_button")
        assert any((t.key or "").startswith("newvariant_subject_") for t in at.text_input) and not any(b.key == "html_add_variant_go" for b in at.button)


def test_if_the_source_variant_has_no_subject_file_for_a_stage_nothing_is_added_and_the_stage_is_named():
    with editor(world=html_world(letters="AB")) as (at, world):
        original = GitHubClient.get_file_content

        def missing(self, path, ref="main"):
            if path.endswith("followup3_A.txt"):
                raise GitHubActionsError("Failed to read 'templates/x/followup3_A.txt': 404 not found")
            return original(self, path, ref)

        open_add_variant(at)
        with patch("github_client.GitHubClient.get_file_content", missing):
            press(at, "html_add_variant_go")
        assert world.commits == [] and any("Variant A has no subject file for Follow-up 3 to copy from." in e.value for e in at.error)
