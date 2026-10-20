"""The 'Designed (HTML) emails' editor through the real Sequences tab: what it shows, saving, unsafe content, layouts, a missing design, failures, and that a plain-text
campaign never shows it. Uses the Sequences page tests' fake GitHub (which now also holds .html files and records saves)."""
import contextlib
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import html_campaign_logic as hc  # noqa: E402
import outreach  # noqa: E402
from github_client import GitHubActionsError  # noqa: E402
from test_sequences_page import FIXTURE_CAMPAIGN, LETTERS, STAGES, World, _every_test_uses_the_fixture_repo, _clear_streamlit_caches, go, seq_page  # noqa: E402

_AUTOUSE_FIXTURES = (_every_test_uses_the_fixture_repo, _clear_streamlit_caches)
_REAL = outreach.get_campaign
FOOTER = {"business_name": "Kelson Agency", "address": "12 Example Street"}
DESIGN = "<p>Hi {{ FirstName }}, we have news for you and quite a lot more to say, so there is enough readable text here to be a real email.</p>{{ footer }}"


def _html_cfg(footer=True):
    def get(*a, **k):
        cfg = _REAL(*a, **k)
        cfg["format"] = "html"
        if footer:
            cfg["footer"] = dict(FOOTER)
        return cfg
    return get


def html_world(with_html=True, design=DESIGN):
    world = World()
    if with_html:
        world.files |= {f"{s}_{v}.html" for s in STAGES for v in LETTERS}
        world.html = {f"{s}_{v}.html": design for s in STAGES for v in LETTERS}
    return world


@contextlib.contextmanager
def editor(world=None, footer=True, **kwargs):
    """The campaign's Sequences tab switched to its Design (HTML) view."""
    with patch.object(outreach, "get_campaign", _html_cfg(footer)):
        with seq_page(world=world or html_world(), **kwargs) as (at, w):
            next(r for r in at.radio if r.key == f"seq_view_{FIXTURE_CAMPAIGN}").set_value("Design (HTML)")
            go(at)
            yield at, w


def area(at, prefix="intro", variant="A"):
    return next(t for t in at.text_area if t.key == f"html_text_{FIXTURE_CAMPAIGN}_{prefix}_{variant}")


def button(at, prefix_key):
    return next(b for b in at.button if (b.key or "").startswith(prefix_key))


def all_text(at):
    return " ".join(str(m.value) for m in at.markdown) + " " + " ".join(str(c.value) for c in at.caption)


# ================================================================ who sees it

def test_a_plain_text_campaign_never_shows_the_design_editor():
    with seq_page() as (at, _):
        assert not any((t.key or "").startswith("html_text_") for t in at.text_area) and "Designed (HTML) emails" not in all_text(at) and list(at.exception) == []


def test_an_html_campaign_opens_on_subject_and_plain_text_and_the_design_is_one_switch_away():
    with patch.object(outreach, "get_campaign", _html_cfg()):
        with seq_page(world=html_world()) as (at, _):
            switch = next(r for r in at.radio if r.key == f"seq_view_{FIXTURE_CAMPAIGN}")
            assert switch.options == ["Subject & plain text", "Design (HTML)"] and switch.value == "Subject & plain text"
            assert not any((t.key or "").startswith("html_text_") for t in at.text_area) and any(b.key == "sq_save_button" for b in at.button)       # the usual editor is what shows
            switch.set_value("Design (HTML)")
            go(at)
            assert area(at).value == DESIGN and not any(b.key == "sq_save_button" for b in at.button) and not any((b.key or "").startswith("sq_pick_") for b in at.button)
            next(r for r in at.radio if r.key == f"seq_view_{FIXTURE_CAMPAIGN}").set_value("Subject & plain text")
            go(at)
            assert any(b.key == "sq_save_button" for b in at.button) and list(at.exception) == []


def test_a_plain_text_campaign_has_no_such_switch():
    with seq_page() as (at, _):
        assert not any((r.key or "").startswith("seq_view_") for r in at.radio)


def test_an_html_campaign_shows_the_editor_with_the_saved_design_in_the_box_and_save_off():
    with editor() as (at, _):
        assert list(at.exception) == [] and "Designed (HTML) emails" in all_text(at)
        assert area(at).value == DESIGN and button(at, "html_save_").disabled is True


def test_the_email_picker_offers_every_stage_and_variant_and_switching_loads_that_design():
    world = html_world()
    world.html["followup1_B.html"] = "<p>the second design {{ footer }}</p>"
    with editor(world=world) as (at, _):
        picker = next(s for s in at.selectbox if s.key == f"html_pick_{FIXTURE_CAMPAIGN}")
        assert len(picker.options) == len(STAGES) * len(LETTERS) and picker.options[0] == "Intro · Variant A"
        picker.set_value("followup1|B")
        go(at)
        assert area(at, "followup1", "B").value == "<p>the second design {{ footer }}</p>"


# ================================================================ editing and saving

def test_editing_turns_save_on_and_saving_writes_that_exact_file_and_shows_it_as_saved():
    with editor() as (at, world):
        area(at).set_value(DESIGN + "<p>an edit</p>")
        go(at)
        assert button(at, "html_save_").disabled is False and "Unsaved change" in all_text(at)
        button(at, "html_save_").click()
        go(at)
        assert list(at.exception) == [] and list(at.error) == [] and len(world.created) == 1
        saved = world.created[0]
        assert saved["path"] == f"templates/{FIXTURE_CAMPAIGN}/intro_A.html" and saved["content"] == (DESIGN + "<p>an edit</p>").encode("utf-8")
        assert saved["message"].startswith(f"Update design intro_A in {FIXTURE_CAMPAIGN} (via Streamlit, by ")
        assert any("Design saved" in s.value for s in at.success) and button(at, "html_save_").disabled is True and "Unsaved change" not in all_text(at)


def test_only_the_email_on_screen_is_written_never_another_one():
    with editor() as (at, world):
        next(s for s in at.selectbox if s.key == f"html_pick_{FIXTURE_CAMPAIGN}").set_value("followup2|C")
        go(at)
        area(at, "followup2", "C").set_value(DESIGN + "x")
        go(at)
        button(at, "html_save_").click()
        go(at)
        assert [c["path"] for c in world.created] == [f"templates/{FIXTURE_CAMPAIGN}/followup2_C.html"]


def test_an_unsaved_edit_survives_switching_to_another_email_and_back():
    with editor() as (at, _):
        area(at).set_value("<p>half-written</p>")
        go(at)
        picker = next(s for s in at.selectbox if s.key == f"html_pick_{FIXTURE_CAMPAIGN}")
        picker.set_value("intro|B")
        go(at)
        next(s for s in at.selectbox if s.key == f"html_pick_{FIXTURE_CAMPAIGN}").set_value("intro|A")
        go(at)
        assert area(at).value == "<p>half-written</p>"


def test_reload_puts_back_what_is_saved_and_discards_the_edit():
    with editor() as (at, world):
        area(at).set_value("<p>oops</p>")
        go(at)
        button(at, "html_reload_").click()
        go(at)
        assert area(at).value == DESIGN and world.created == []


def test_an_empty_box_cannot_be_saved():
    with editor() as (at, world):
        area(at).set_value("   ")
        go(at)
        assert button(at, "html_save_").disabled is True and world.created == []


def test_a_failed_save_says_so_and_keeps_the_edit_unsaved():
    with editor(commit_error=GitHubActionsError("boom")) as (at, _):
        area(at).set_value(DESIGN + "x")
        go(at)
        button(at, "html_save_").click()
        go(at)
        assert any("Failed to save" in e.value for e in at.error) and not any("Design saved" in s.value for s in at.success) and area(at).value == DESIGN + "x"


# ================================================================ unsafe content

def test_a_design_with_a_script_cannot_be_saved_is_not_previewed_and_the_checks_say_why():
    with editor() as (at, world):
        area(at).set_value(DESIGN + "<script>alert(1)</script>")
        go(at)
        assert button(at, "html_save_").disabled is True and world.created == []
        text = all_text(at)
        assert "must not have" in text and "preview is not shown" in " ".join(i.value for i in at.info) and "Not saved: remove what the checks list in red first" in text
        assert not at.get("iframe")


# ================================================================ the preview and the checks

def test_a_good_design_is_previewed_in_a_frame_and_every_check_passes():
    with editor() as (at, _):
        assert at.get("iframe") and 'class="he-ck"><i class="he-er"' not in all_text(at) and "No problems that stop sending" in all_text(at) and not any("placeholders match no column" in w.value or "Couldn't read" in w.value for w in at.warning)


def test_the_checks_name_the_problems_of_the_design_on_screen():
    with editor() as (at, _):
        area(at).set_value("<p>Hi {{ Nope }}</p><img src='http://x/y.png'>")
        go(at)
        text = all_text(at)
        assert "match no Sheet column" in text and "no alt text" in text and "https://" in text and "no way to opt out" in text


def test_an_incomplete_footer_is_reported_and_the_layout_is_still_shown():
    with editor(footer=False) as (at, _):
        text = all_text(at)
        assert "footer is incomplete" in text and at.get("iframe")


def test_placeholders_with_no_column_are_listed_under_the_preview():
    with editor() as (at, _):
        area(at).set_value(DESIGN + "{{ Frist }}")
        go(at)
        assert any("{{ Frist }}" in w.value for w in at.warning)


def test_the_preview_can_be_shown_for_a_real_lead_and_at_mobile_width():
    with editor() as (at, _):
        lead_picker = next(s for s in at.selectbox if (s.key or "").startswith("html_lead_"))
        assert lead_picker.options[0].startswith("Sample lead") and len(lead_picker.options) == 3
        lead_picker.set_value("lead_0")
        next(r for r in at.radio if (r.key or "").startswith("html_width_")).set_value("Mobile")
        go(at)
        assert list(at.exception) == [] and at.get("iframe")


# ================================================================ starter layouts

def test_use_this_layout_puts_the_layout_in_the_box_without_saving_anything():
    with editor() as (at, world):
        next(s for s in at.selectbox if (s.key or "").startswith("html_layout_")).set_value("announcement")
        go(at)
        button(at, "html_use_").click()
        go(at)
        assert area(at).value == hc.starter_html("announcement") and world.created == [] and button(at, "html_save_").disabled is False


# ================================================================ a design that does not exist yet

def test_an_email_with_no_design_file_says_so_and_can_be_given_one_from_the_starter_layout():
    with editor(world=html_world(with_html=False)) as (at, world):
        assert "has no designed (.html) file yet" in " ".join(w.value for w in at.warning) and not any((t.key or "").startswith("html_text_") for t in at.text_area)
        button(at, "html_create_").click()
        go(at)
        assert len(world.created) == 1 and world.created[0]["path"] == f"templates/{FIXTURE_CAMPAIGN}/intro_A.html"
        assert world.created[0]["content"] == hc.starter_html(hc.DEFAULT_LAYOUT).encode("utf-8")
        assert area(at).value == hc.starter_html(hc.DEFAULT_LAYOUT) and list(at.exception) == []


def test_a_failed_attempt_to_create_the_missing_design_says_so():
    with editor(world=html_world(with_html=False), commit_error=GitHubActionsError("nope")) as (at, _):
        button(at, "html_create_").click()
        go(at)
        assert any("Failed to create the design" in e.value for e in at.error)


def test_a_design_that_cannot_be_read_for_another_reason_offers_to_try_again_and_shows_the_reason():
    def failing(self, path, ref="main"):
        raise GitHubActionsError("Failed to read: 500 server error")
    with patch.object(outreach, "get_campaign", _html_cfg()):
        with seq_page(world=html_world()) as (at, _):
            next(r for r in at.radio if r.key == f"seq_view_{FIXTURE_CAMPAIGN}").set_value("Design (HTML)")
            go(at)
            with patch("github_client.GitHubClient.get_file_content", failing):
                next(s for s in at.selectbox if s.key == f"html_pick_{FIXTURE_CAMPAIGN}").set_value("followup3|D")
                go(at)
                assert any("Couldn't read this email's design" in e.value and "500" in e.value for e in at.error) and any(b.label == "Try again" for b in at.button)


# ================================================================ send me a test

def _test_box(at):
    return next(t for t in at.text_input if (t.key or "").startswith("html_test_to_"))


def _dispatches():
    calls = []

    def fake(self, workflow, inputs=None, *a, **k):
        calls.append((workflow, inputs))
    return calls, patch("github_client.GitHubClient.dispatch_workflow", fake)


def test_the_test_button_needs_a_valid_address_and_a_saved_design():
    with editor() as (at, _):
        assert button(at, "html_test_").disabled is True                         # no address yet
        _test_box(at).set_value("not an address")
        go(at)
        assert button(at, "html_test_").disabled is True and "not a valid email address" in all_text(at)
        _test_box(at).set_value("team@kelson.agency")
        go(at)
        assert button(at, "html_test_").disabled is False
        area(at).set_value(DESIGN + "x")
        go(at)
        assert button(at, "html_test_").disabled is True and "Save the design first" in all_text(at)


def test_the_test_button_is_off_while_the_design_contains_something_unsafe():
    with editor() as (at, _):
        _test_box(at).set_value("team@kelson.agency")
        area(at).set_value(DESIGN + "<script>x</script>")
        go(at)
        assert button(at, "html_test_").disabled is True


def test_sending_a_test_starts_the_workflow_for_exactly_the_email_on_screen():
    calls, patcher = _dispatches()
    with editor() as (at, world), patcher:
        next(s for s in at.selectbox if s.key == f"html_pick_{FIXTURE_CAMPAIGN}").set_value("followup1|B")
        go(at)
        _test_box(at).set_value("  team@kelson.agency ")
        go(at)
        button(at, "html_test_").click()
        go(at)
        assert list(at.exception) == [] and list(at.error) == [] and world.created == []
        assert calls == [("send_test_email.yml", {"campaign": FIXTURE_CAMPAIGN, "stage": "followup1", "variant": "B", "to": "team@kelson.agency"})]
        assert any("Test requested for team@kelson.agency" in s.value for s in at.success)


def test_a_test_that_cannot_be_started_says_so():
    def boom(self, workflow, inputs=None, *a, **k):
        raise GitHubActionsError("workflow not found")
    with editor() as (at, _), patch("github_client.GitHubClient.dispatch_workflow", boom):
        _test_box(at).set_value("team@kelson.agency")
        go(at)
        button(at, "html_test_").click()
        go(at)
        assert any("Couldn't start the test" in e.value for e in at.error) and not any("Test requested" in s.value for s in at.success)


def test_the_checks_read_the_plain_text_copy_of_the_email_on_screen():
    with editor() as (at, _):
        assert "plain-text copy has no opt-out line" in all_text(at)               # the stand-in copy in this fake GitHub has none
    world = html_world()
    world.write("intro_A.txt", "Hello", "Hi {{ FirstName }}\n\n{{ footer }}")
    with editor(world=world) as (at, _):
        assert "plain-text copy has no opt-out line" not in all_text(at)
        assert "Subject: Hello" in all_text(at)                                       # and its subject is shown beside it
