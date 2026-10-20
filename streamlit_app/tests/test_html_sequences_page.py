"""The Sequences tab for an HTML campaign, through the real page: every add and delete keeps each email's two files (the .txt and the .html) together, and a plain-text
campaign beside it is untouched. Uses the same fake GitHub as the Sequences page tests."""
import contextlib
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import outreach  # noqa: E402
import html_campaign_logic as hc  # noqa: E402
from test_sequences_page import FIXTURE_CAMPAIGN, LETTERS, STAGES, _letters_world, _every_test_uses_the_fixture_repo, _clear_streamlit_caches, go, seq_page  # noqa: E402

_AUTOUSE_FIXTURES = (_every_test_uses_the_fixture_repo, _clear_streamlit_caches)        # imported so pytest applies them to every test here, exactly as in the Sequences page tests

_REAL_GET_CAMPAIGN = outreach.get_campaign


def _as_html(*args, **kwargs):
    cfg = _REAL_GET_CAMPAIGN(*args, **kwargs)
    cfg["format"] = "html"
    return cfg


@contextlib.contextmanager
def html_page(**kwargs):
    with patch.object(outreach, "get_campaign", _as_html):
        with seq_page(**kwargs) as pair:
            yield pair


def _names(files):
    return sorted(f["path"].rsplit("/", 1)[-1] for f in files)


def _add_variant_c(at):
    next(b for b in at.button if b.key == "sq_add_variant_button").click()
    go(at)
    for stage in STAGES:
        next(t for t in at.text_input if t.key == f"newvariant_subject_{FIXTURE_CAMPAIGN}_{stage}_C").set_value("Intro C subject" if stage == "intro" else "")
        next(t for t in at.text_area if t.key == f"newvariant_body_{FIXTURE_CAMPAIGN}_{stage}_C").set_value(f"Body C for {stage}")
    go(at)
    next(b for b in at.button if b.label == "Add Variant C").click()
    go(at)


def _add_stage_5(at):
    next(b for b in at.button if b.key == "sq_add_stage_button").click()
    go(at)
    for v in LETTERS:
        next(t for t in at.text_area if t.key == f"followup_body_{FIXTURE_CAMPAIGN}_followup5_{v}").set_value(f"Body of followup5 {v}")
    go(at)
    next(b for b in at.button if b.label == "Add Follow-up 5").click()
    go(at)


# ================================================================ adding

def test_adding_a_variant_to_an_html_campaign_commits_a_txt_and_a_starter_html_for_every_stage_in_one_save():
    with html_page(world=_letters_world("AB")) as (at, world):
        _add_variant_c(at)
        assert list(at.exception) == [] and list(at.error) == [] and len(world.commits) == 1
        files = world.commits[0]["files"]
        assert _names(files) == sorted([f"{s}_C.txt" for s in STAGES] + [f"{s}_C.html" for s in STAGES])
        assert all(f["content"].decode() == hc.starter_html(hc.DEFAULT_LAYOUT) for f in files if f["path"].endswith(".html"))
        assert any("Variant C added to all 5 stage(s)" in s.value for s in at.success)


def test_adding_a_variant_to_a_text_campaign_still_commits_only_the_txt_files():
    with seq_page(world=_letters_world("AB")) as (at, world):
        _add_variant_c(at)
        assert len(world.commits) == 1 and _names(world.commits[0]["files"]) == sorted(f"{s}_C.txt" for s in STAGES)


def test_adding_a_stage_to_an_html_campaign_commits_a_txt_and_a_starter_html_for_every_variant_in_one_save():
    with html_page() as (at, world):
        _add_stage_5(at)
        assert list(at.exception) == [] and list(at.error) == [] and len(world.commits) == 1
        assert _names(world.commits[0]["files"]) == sorted([f"followup5_{v}.txt" for v in LETTERS] + [f"followup5_{v}.html" for v in LETTERS])
        assert any("'followup5' added" in s.value for s in at.success)


def test_adding_a_stage_to_a_text_campaign_still_commits_only_the_txt_files():
    with seq_page() as (at, world):
        _add_stage_5(at)
        assert _names(world.commits[0]["files"]) == [f"followup5_{v}.txt" for v in LETTERS]


def test_the_add_forms_of_an_html_campaign_explain_where_the_design_comes_from_and_a_text_campaigns_do_not():
    with html_page(world=_letters_world("AB")) as (at, _):
        next(b for b in at.button if b.key == "sq_add_variant_button").click()
        go(at)
        assert any("This is an HTML campaign" in c.value for c in at.caption)
    with html_page() as (at, _):
        next(b for b in at.button if b.key == "sq_add_stage_button").click()
        go(at)
        assert any("This is an HTML campaign" in c.value for c in at.caption)
    with seq_page(world=_letters_world("AB")) as (at, _):
        next(b for b in at.button if b.key == "sq_add_variant_button").click()
        go(at)
        assert not any("HTML campaign" in c.value for c in at.caption)


# ================================================================ deleting

def test_deleting_a_variant_from_an_html_campaign_removes_every_text_file_then_every_design_and_counts_stages_not_files():
    with html_page() as (at, world):
        next(r for r in at.radio if (r.key or "").startswith("sq_variant_")).set_value("C")
        go(at)
        next(c for c in at.checkbox if c.key == "confirm_delete_variant_C").set_value(True)
        go(at)
        next(b for b in at.button if b.key == "delete_variant_button").click()
        go(at)
        assert list(at.exception) == [] and list(at.error) == []
        names = [p.rsplit("/", 1)[-1] for p in world.deleted]
        assert names == [f"{s}_C.txt" for s in STAGES] + [f"{s}_C.html" for s in STAGES]
        assert any("Variant C deleted from all 5 stage(s)" in s.value for s in at.success)


def test_deleting_a_variant_from_a_text_campaign_removes_only_the_txt_files():
    with seq_page() as (at, world):
        next(r for r in at.radio if (r.key or "").startswith("sq_variant_")).set_value("C")
        go(at)
        next(c for c in at.checkbox if c.key == "confirm_delete_variant_C").set_value(True)
        go(at)
        next(b for b in at.button if b.key == "delete_variant_button").click()
        go(at)
        assert [p.rsplit("/", 1)[-1] for p in world.deleted] == [f"{s}_C.txt" for s in STAGES]


def test_deleting_the_last_stage_of_an_html_campaign_removes_every_text_file_then_every_design():
    with html_page() as (at, world):
        next(c for c in at.checkbox if c.key == "confirm_delete_stage_followup4").set_value(True)
        go(at)
        next(b for b in at.button if b.key == "delete_stage_button").click()
        go(at)
        assert list(at.exception) == [] and list(at.error) == []
        assert [p.rsplit("/", 1)[-1] for p in world.deleted] == [f"followup4_{v}.txt" for v in LETTERS] + [f"followup4_{v}.html" for v in LETTERS]


def test_deleting_the_last_stage_of_a_text_campaign_removes_only_the_txt_files():
    with seq_page() as (at, world):
        next(c for c in at.checkbox if c.key == "confirm_delete_stage_followup4").set_value(True)
        go(at)
        next(b for b in at.button if b.key == "delete_stage_button").click()
        go(at)
        assert [p.rsplit("/", 1)[-1] for p in world.deleted] == [f"followup4_{v}.txt" for v in LETTERS]
