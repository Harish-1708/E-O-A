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


def html_world(with_html=True, design=DESIGN, letters=LETTERS):
    world = World(letters=letters)
    if with_html:
        world.files |= {f"{s}_{v}.html" for s in STAGES for v in letters}
        world.html = {f"{s}_{v}.html": design for s in STAGES for v in letters}
    return world


@contextlib.contextmanager
def editor(world=None, footer=True, **kwargs):
    """An HTML campaign's Sequences tab: its workspace, opened on the first email (Intro, Variant A)."""
    with patch.object(outreach, "get_campaign", _html_cfg(footer)):
        with seq_page(world=world or html_world(), **kwargs) as pair:
            yield pair


def pick(at, prefix, variant):
    """Chooses an email the way a visitor does: its stage in the list on the left, its variant in the pills."""
    next(b for b in at.button if b.key == f"sq_pick_{STAGES.index(prefix)}").click()
    go(at)
    next(r for r in at.radio if r.key == f"html_variant_{FIXTURE_CAMPAIGN}").set_value(variant)
    go(at)


def subject_box(at, prefix="intro", variant="A"):
    return next(t for t in at.text_input if t.key == f"html_subject_{FIXTURE_CAMPAIGN}_{prefix}_{variant}")


def area(at, prefix="intro", variant="A"):
    return next(t for t in at.text_area if t.key == f"html_text_{FIXTURE_CAMPAIGN}_{prefix}_{variant}")


def button(at, prefix_key):
    return next(b for b in at.button if (b.key or "").startswith(prefix_key))


def all_text(at):
    return " ".join(str(m.value) for m in at.markdown) + " " + " ".join(str(c.value) for c in at.caption)


# ================================================================ who sees it

def test_a_plain_text_campaign_never_shows_the_design_editor():
    with seq_page() as (at, _):
        assert not any((t.key or "").startswith(("html_text_", "html_subject_")) for t in list(at.text_area) + list(at.text_input)) and not any(b.key == "footer_save" for b in at.button)
        assert any(b.key == "sq_save_button" for b in at.button) and list(at.exception) == []                  # the usual editor is what shows


def test_an_html_campaign_has_one_workspace_with_no_plain_text_editor_and_no_view_switch():
    with editor() as (at, _):
        assert list(at.exception) == [] and not any(b.key == "sq_save_button" for b in at.button) and not any((t.key or "").startswith("body_") for t in at.text_area)
        assert not any((r.key or "").startswith("seq_view_") for r in at.radio) and "Plain-text version" in " ".join(e.label for e in at.expander)


def test_a_plain_text_campaign_has_no_variant_pills_of_the_html_workspace():
    with seq_page() as (at, _):
        assert not any((r.key or "").startswith(("seq_view_", "html_variant_")) for r in at.radio)


def test_an_html_campaign_shows_the_saved_subject_and_design_in_their_boxes_and_save_off():
    with editor() as (at, _):
        assert subject_box(at).value == "Paid collaboration with {{CompanyName}}" and area(at).value == DESIGN and button(at, "html_save_").disabled is True


def test_the_stage_list_and_the_variant_pills_offer_every_email_and_choosing_one_loads_its_subject_and_design():
    world = html_world()
    world.html["followup1_B.html"] = "<p>the second design {{ footer }}</p>"
    with editor(world=world) as (at, _):
        pills = next(r for r in at.radio if r.key == f"html_variant_{FIXTURE_CAMPAIGN}")
        assert pills.options == [f"Variant {v}" for v in LETTERS] and len([b for b in at.button if (b.key or "").startswith("sq_pick_")]) == len(STAGES)
        pick(at, "followup1", "B")
        assert area(at, "followup1", "B").value == "<p>the second design {{ footer }}</p>" and subject_box(at, "followup1", "B").value == ""
        assert "Follow-up 1" in all_text(at)


# ================================================================ editing and saving

def test_editing_the_design_turns_save_on_and_saving_writes_only_the_html_file_and_shows_it_as_saved():
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
        assert any("Saved the design" in s.value for s in at.success) and button(at, "html_save_").disabled is True and "Unsaved change" not in all_text(at)


def test_only_the_email_on_screen_is_written_never_another_one():
    with editor() as (at, world):
        pick(at, "followup2", "C")
        area(at, "followup2", "C").set_value(DESIGN + "x")
        go(at)
        button(at, "html_save_").click()
        go(at)
        assert [c["path"] for c in world.created] == [f"templates/{FIXTURE_CAMPAIGN}/followup2_C.html"]


def test_an_unsaved_design_and_an_unsaved_subject_both_survive_switching_to_another_email_and_back():
    with editor() as (at, _):
        area(at).set_value("<p>half-written</p>")
        subject_box(at).set_value("A half-written subject")
        go(at)
        pick(at, "intro", "B")
        assert area(at, "intro", "B").value == DESIGN and subject_box(at, "intro", "B").value == "Paid collaboration with {{CompanyName}}"
        pick(at, "followup1", "A")
        pick(at, "intro", "A")
        assert area(at).value == "<p>half-written</p>" and subject_box(at).value == "A half-written subject"


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
        assert at.get("iframe") and "Every check passed" in all_text(at) and not any("placeholders match no column" in w.value or "Couldn't read" in w.value for w in at.warning)


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
            with patch("github_client.GitHubClient.get_file_content", failing):
                pick(at, "followup3", "D")
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
        assert button(at, "html_test_").disabled is True and "Save first: the test sends what is saved." in all_text(at)


def test_the_test_button_is_off_while_the_design_contains_something_unsafe():
    with editor() as (at, _):
        _test_box(at).set_value("team@kelson.agency")
        area(at).set_value(DESIGN + "<script>x</script>")
        go(at)
        assert button(at, "html_test_").disabled is True


def test_sending_a_test_starts_the_workflow_for_exactly_the_email_on_screen():
    calls, patcher = _dispatches()
    with editor() as (at, world), patcher:
        pick(at, "followup1", "B")
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


def test_the_plain_text_version_is_shown_as_made_automatically_from_the_design_on_screen():
    with editor(world=html_world(design="<h1>Big news, {{ FirstName }}</h1><p>See <a href='https://x.com/a'>the offer</a>.</p>{{ footer }}")) as (at, _):
        shown = " ".join(c.value for c in at.code)
        assert "Big news, Ann" in shown and "See the offer (https://x.com/a)." in shown and "Kelson Agency" in shown and "<" not in shown
        assert "made automatically from the design" in " ".join(e.label for e in at.expander) and "You never need to write it" in all_text(at)
        area(at).set_value("<p>Changed words</p>{{ footer }}")
        go(at)
        assert "Changed words" in " ".join(c.value for c in at.code)


# ================================================================ the footer panel under the design

FOOT = {"business_name": "Kelson Agency", "address": "12 Example Street", "opt_out": 'Reply "unsubscribe".'}
YAML_PATH = f"config/campaigns/{FIXTURE_CAMPAIGN}.yaml"
NO_FOOTER_DESIGN = "<p>Hi {{ FirstName }}, here is a longer paragraph so that the email has plenty of readable text. You can unsubscribe by replying to this email.</p>"


def foot_box(at, name):
    return next(t for t in at.text_input if t.key == f"footer_{name}_{FIXTURE_CAMPAIGN}")


def fill_foot(at, business_name=None, address=None, opt_out=None):
    for name, value in (("business_name", business_name), ("address", address), ("opt_out", opt_out)):
        if value is not None:
            foot_box(at, name).set_value(value)
    go(at)


def foot_save(at):
    return next(b for b in at.button if b.key == "footer_save")


def test_the_footer_panel_is_under_the_design_of_every_email_and_a_plain_text_campaign_has_none():
    with editor() as (at, _):
        assert "Email footer" in all_text(at) and "shared by every email in this campaign" in " ".join(e.label for e in at.expander) and foot_save(at).disabled is True
        pick(at, "followup3", "D")
        assert "Email footer" in all_text(at) and any(b.key == "footer_save" for b in at.button)             # the same panel under every stage and variant
    with seq_page() as (at, _):
        assert not any(b.key == "footer_save" for b in at.button) and "Email footer" not in all_text(at)


def test_an_email_that_has_the_footer_says_so_and_offers_to_remove_it():
    with editor() as (at, _):
        assert "This email has the footer" in all_text(at) and any((b.key or "").startswith("html_footer_remove_") for b in at.button) and not any((b.key or "").startswith("html_footer_add_") for b in at.button)


def test_an_email_with_no_footer_says_it_is_optional_and_offers_to_add_it():
    with editor(world=html_world(design=NO_FOOTER_DESIGN)) as (at, _):
        assert "This email has no footer" in all_text(at) and "optional" in all_text(at) and any((b.key or "").startswith("html_footer_add_") for b in at.button)


def test_adding_the_footer_puts_the_placeholder_in_the_box_changes_nothing_on_github_and_turns_save_on():
    with editor(world=html_world(design=NO_FOOTER_DESIGN)) as (at, world):
        next(b for b in at.button if (b.key or "").startswith("html_footer_add_")).click()
        go(at)
        assert area(at).value == NO_FOOTER_DESIGN + "\n{{ footer }}\n" and world.created == [] and button(at, "html_save_").disabled is False
        assert "This email has the footer" in all_text(at) and any((b.key or "").startswith("html_footer_remove_") for b in at.button)


def test_removing_the_footer_takes_the_placeholder_out_of_the_box_without_saving():
    with editor() as (at, world):
        next(b for b in at.button if (b.key or "").startswith("html_footer_remove_")).click()
        go(at)
        assert "{{ footer }}" not in area(at).value and world.created == [] and button(at, "html_save_").disabled is False and "This email has no footer" in all_text(at)


def test_adding_the_footer_to_one_email_leaves_every_other_email_alone():
    with editor(world=html_world(design=NO_FOOTER_DESIGN)) as (at, world):
        next(b for b in at.button if (b.key or "").startswith("html_footer_add_")).click()
        go(at)
        button(at, "html_save_").click()
        go(at)
        assert [c["path"] for c in world.created] == [f"templates/{FIXTURE_CAMPAIGN}/intro_A.html"] and world.created[0]["content"].decode().endswith("{{ footer }}\n")


def test_the_footer_details_start_open_only_when_the_email_uses_the_footer_and_they_are_incomplete():
    with editor(footer=False) as (at, _):
        assert next(e for e in at.expander if e.label.startswith("Footer details")).proto.expanded is True
    with editor(footer=True) as (at, _):
        assert next(e for e in at.expander if e.label.startswith("Footer details")).proto.expanded is False
    with editor(footer=False, world=html_world(design=NO_FOOTER_DESIGN)) as (at, _):
        assert next(e for e in at.expander if e.label.startswith("Footer details")).proto.expanded is False


def test_typing_in_the_footer_details_shows_the_preview_only_once_the_name_and_address_are_both_there():
    with editor(footer=False) as (at, _):
        fill_foot(at, business_name="Kelson Agency")
        assert 'class="he-footer-preview"' not in all_text(at) and foot_save(at).disabled is False and "Unsaved change" in all_text(at)
        fill_foot(at, address="12 Example Street")
        assert 'class="he-footer-preview"' in all_text(at) and hc.DEFAULT_OPT_OUT.replace('"', "&quot;") in all_text(at)


def test_nothing_typed_into_the_footer_can_become_markup_in_its_preview():
    import html
    for hostile in ('<script>alert(1)</script>', '"><img src=x onerror=alert(1)>'):
        with editor(footer=False) as (at, _):
            fill_foot(at, business_name=hostile, address=hostile, opt_out=hostile)
            text = all_text(at)
            assert "<script>alert" not in text and "<img src=x" not in text and html.escape(hostile) in text


def test_saving_the_footer_writes_it_once_for_the_campaign_and_keeps_every_other_setting():
    import yaml
    live_yaml = yaml.safe_dump({"format": "html", "status": "draft", "sending": {"daily_limit": 40}}).encode()
    with editor(footer=False) as (at, world):
        fill_foot(at, business_name="  Kelson Agency ", address=" 12 Example Street ", opt_out="")
        with patch("html_editor_view.load_raw_override_live", lambda name, client, d, branch="main": yaml.safe_load(live_yaml)):
            foot_save(at).click()
            go(at)
        assert list(at.exception) == [] and list(at.error) == [] and len(world.created) == 1
        written = world.created[0]
        assert written["path"] == YAML_PATH and written["message"].startswith(f"Update email footer for {FIXTURE_CAMPAIGN} (via Streamlit, by ")
        assert yaml.safe_load(written["content"]) == {"format": "html", "status": "draft", "sending": {"daily_limit": 40}, "footer": {"business_name": "Kelson Agency", "address": "12 Example Street"}}
        assert any("Footer saved for every email in this campaign" in s.value for s in at.success)


def test_a_footer_saved_a_moment_ago_counts_at_once_in_the_preview_and_the_checks():
    with editor(footer=False) as (at, _):
        assert "footer is incomplete" in all_text(at)
        fill_foot(at, business_name="Kelson Agency", address="12 Example Street")
        foot_save(at).click()
        go(at)
        text = all_text(at)
        assert "footer is incomplete" not in text and foot_save(at).disabled is True and "Unsaved change" not in text and "Kelson Agency" in text


def _preview_page(at):
    frames = at.get("iframe")
    return getattr(frames[0].proto, "srcdoc", "") if frames else ""


def test_the_preview_frame_itself_shows_the_footer_in_force_a_marker_before_it_is_complete_and_the_real_one_after_saving():
    with editor(footer=False) as (at, _):
        before = _preview_page(at)
        assert "Your footer will appear here" in before and "Kelson Agency" not in before
        fill_foot(at, business_name="Kelson Agency", address="12 Example Street")
        foot_save(at).click()
        go(at)
        after = _preview_page(at)
        assert "Your footer will appear here" not in after and "Kelson Agency" in after and "12 Example Street" in after


def test_saving_the_footer_without_an_address_is_refused_and_writes_nothing():
    with editor(footer=False) as (at, world):
        fill_foot(at, business_name="Kelson Agency")
        foot_save(at).click()
        go(at)
        assert world.created == [] and any("Enter the business name and the postal address" in e.value for e in at.error)


def test_a_failed_footer_save_says_so_and_does_not_count_the_footer_as_saved():
    with editor(footer=False, commit_error=GitHubActionsError("boom")) as (at, _):
        fill_foot(at, business_name="Kelson Agency", address="12 Example Street")
        foot_save(at).click()
        go(at)
        assert any("Failed to save" in e.value for e in at.error) and not any("Footer saved" in s.value for s in at.success) and "footer is incomplete" in all_text(at)


# ================================================================ the subject, saved together with the design

INTRO_SUBJECT = "Paid collaboration with {{CompanyName}}"
INTRO_BODY = "Hi {{FirstName}},\n\nBody of intro A.\n\nThanks,\nHarish\n"          # the fake GitHub plays a file back with one trailing newline


def test_changing_only_the_subject_writes_only_the_subject_file_and_keeps_the_text_that_was_in_it():
    with editor() as (at, world):
        subject_box(at).set_value("  A  brand new   subject for {{ FirstName }} ")
        go(at)
        assert button(at, "html_save_").disabled is False and "Unsaved change" in all_text(at)
        button(at, "html_save_").click()
        go(at)
        assert list(at.exception) == [] and list(at.error) == [] and len(world.created) == 1
        saved = world.created[0]
        assert saved["path"] == f"templates/{FIXTURE_CAMPAIGN}/intro_A.txt" and saved["content"].decode() == f"Subject: A brand new subject for {{{{ FirstName }}}}\n\n{INTRO_BODY}"
        assert saved["message"].startswith(f"Update subject of intro_A in {FIXTURE_CAMPAIGN} (via Streamlit, by ") and any("Saved the subject." in s.value for s in at.success)
        assert button(at, "html_save_").disabled is True and subject_box(at).value == "A brand new subject for {{ FirstName }}"


def test_changing_both_writes_the_subject_then_the_design_in_that_order_and_says_so():
    with editor() as (at, world):
        subject_box(at).set_value("New subject")
        area(at).set_value(DESIGN + "<p>edit</p>")
        go(at)
        button(at, "html_save_").click()
        go(at)
        assert [c["path"].rsplit("/", 1)[-1] for c in world.created] == ["intro_A.txt", "intro_A.html"] and any("Saved the subject and design." in s.value for s in at.success)


def test_the_subject_box_holds_at_most_two_hundred_characters():
    with editor() as (at, _):
        assert subject_box(at).max_chars == 200


def test_the_first_email_cannot_be_saved_or_tested_without_a_subject_and_says_why():
    with editor() as (at, world):
        subject_box(at).set_value("   ")
        area(at).set_value(DESIGN + "x")
        _test_box(at).set_value("team@kelson.agency")
        go(at)
        assert button(at, "html_save_").disabled is True and button(at, "html_test_").disabled is True and world.created == []
        text = all_text(at)
        assert "Not saved: the first email needs a subject." in text and "The subject is empty. The first email of a sequence needs one." in text
        assert "The first email of a sequence needs a subject." in text


def test_a_follow_up_may_have_a_blank_subject_to_continue_the_thread_and_the_page_explains_it():
    with editor() as (at, world):
        pick(at, "followup1", "A")
        assert subject_box(at, "followup1", "A").value == "" and "Leave the subject blank to send this as a reply in the same email thread" in all_text(at)
        area(at, "followup1", "A").set_value(DESIGN + "x")
        go(at)
        assert button(at, "html_save_").disabled is False and "needs a subject" not in all_text(at)
        button(at, "html_save_").click()
        go(at)
        assert [c["path"].rsplit("/", 1)[-1] for c in world.created] == ["followup1_A.html"]


def test_clearing_a_follow_ups_subject_is_a_real_change_that_saves_a_blank_subject_line():
    world = html_world()
    world.write("followup1_A.txt", "Re-use this thread", "old body")
    with editor(world=world) as (at, w):
        pick(at, "followup1", "A")
        subject_box(at, "followup1", "A").set_value("")
        go(at)
        button(at, "html_save_").click()
        go(at)
        assert w.created[0]["content"].decode() == "Subject: \n\nold body\n" and any("Saved the subject." in s.value for s in at.success)


def test_if_the_design_fails_after_the_subject_saved_the_error_says_so_and_a_retry_writes_only_the_design():
    writes = []

    def flaky(self, path, content, message="", **kw):
        writes.append(path.rsplit("/", 1)[-1])
        if path.endswith(".html") and writes.count("intro_A.html") == 1:
            raise GitHubActionsError("html write refused")

    with editor() as (at, world):
        subject_box(at).set_value("Saved subject")
        area(at).set_value(DESIGN + "<p>edit</p>")
        go(at)
        with patch("github_client.GitHubClient.create_file", flaky):
            button(at, "html_save_").click()
            go(at)
            assert writes == ["intro_A.txt", "intro_A.html"] and any("Failed to save: html write refused (the subject did save)" in e.value for e in at.error)
            assert button(at, "html_save_").disabled is False and area(at).value == DESIGN + "<p>edit</p>" and subject_box(at).value == "Saved subject"
            button(at, "html_save_").click()
            go(at)
            assert writes == ["intro_A.txt", "intro_A.html", "intro_A.html"] and any("Saved the design." in s.value for s in at.success)       # the subject is not written a second time


def test_if_nothing_can_be_written_nothing_is_marked_saved_and_both_edits_stay():
    with editor(commit_error=GitHubActionsError("boom")) as (at, world):
        subject_box(at).set_value("Wanted subject")
        area(at).set_value(DESIGN + "x")
        go(at)
        button(at, "html_save_").click()
        go(at)
        assert any("Failed to save: boom" in e.value and "did save" not in e.value for e in at.error) and not any("Saved the" in s.value for s in at.success)
        assert subject_box(at).value == "Wanted subject" and area(at).value == DESIGN + "x" and button(at, "html_save_").disabled is False


def test_reload_puts_back_both_the_saved_subject_and_the_saved_design():
    with editor() as (at, world):
        subject_box(at).set_value("Changed")
        area(at).set_value("<p>changed</p>")
        go(at)
        button(at, "html_reload_").click()
        go(at)
        assert subject_box(at).value == INTRO_SUBJECT and area(at).value == DESIGN and world.created == []


def test_a_subject_that_cannot_be_read_is_reported_while_the_design_can_still_be_edited():
    from github_client import GitHubClient
    with editor() as (at, _):
        original = GitHubClient.get_file_content                    # the fake GitHub's reader, which is in place now

        def selective(self, path, ref="main"):
            if path.endswith("followup2_B.txt"):
                raise GitHubActionsError("Failed to read: 500 server error")
            return original(self, path, ref)

        with patch("github_client.GitHubClient.get_file_content", selective):
            pick(at, "followup2", "B")
            assert any("Couldn't read this email's subject" in w.value and "500" in w.value for w in at.warning) and area(at, "followup2", "B").value == DESIGN


def test_the_test_button_waits_for_a_saved_subject_too_and_comes_back_once_it_is_saved():
    with editor() as (at, _):
        _test_box(at).set_value("team@kelson.agency")
        subject_box(at).set_value("Changed subject")
        go(at)
        assert button(at, "html_test_").disabled is True and "Save first: the test sends what is saved." in all_text(at)
        button(at, "html_save_").click()
        go(at)
        assert button(at, "html_test_").disabled is False


# ================================================================ variants

def test_the_variant_pills_say_variant_a_to_d_and_choosing_one_shows_its_own_delete_option():
    with editor() as (at, _):
        pills = next(r for r in at.radio if r.key == f"html_variant_{FIXTURE_CAMPAIGN}")
        assert pills.options == ["Variant A", "Variant B", "Variant C", "Variant D"] and "Delete variant A" in " ".join(e.label for e in at.expander)
        pick(at, "intro", "C")
        assert "Delete variant C" in " ".join(e.label for e in at.expander) and "Delete variant A" not in " ".join(e.label for e in at.expander)


def test_add_variant_is_off_when_every_variant_already_exists_and_on_otherwise():
    with editor() as (at, _):
        assert next(b for b in at.button if b.key == f"html_add_variant_{FIXTURE_CAMPAIGN}").disabled is True
    with editor(world=html_world(letters="AB")) as (at, _):
        assert next(b for b in at.button if b.key == f"html_add_variant_{FIXTURE_CAMPAIGN}").disabled is False


def test_the_workspace_follows_the_stage_chosen_in_the_list_and_names_it():
    with editor() as (at, _):
        assert "Intro" in all_text(at)
        pick(at, "followup4", "A")
        assert "Follow-up 4" in all_text(at) and area(at, "followup4", "A").value == DESIGN
