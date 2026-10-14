"""The Sequences tab through the real page: the stage list, the one-variant-at-a-time editor with its safeguards, the preview and its checks, adding and
deleting. A small fake GitHub remembers what is committed and deleted, so 'save, then read it back' behaves like the real thing."""
import contextlib
import os
import re
import sys
from unittest.mock import patch

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from github_client import GitHubActionsError
from test_pages_smoke import FakeWorksheet, FakeSpreadsheet, FIXTURE_CAMPAIGN, PAGES_DIR, _authed_session, _dashboard_secrets

STAGES = ["intro", "followup1", "followup2", "followup3", "followup4"]
LETTERS = "ABCD"


@pytest.fixture(autouse=True)
def _every_test_uses_the_fixture_repo(fixture_repo):
    yield


@pytest.fixture(autouse=True)
def _clear_streamlit_caches():
    st.cache_resource.clear()
    st.cache_data.clear()
    yield
    st.cache_resource.clear()
    st.cache_data.clear()


def _default(name):
    stage, variant = name.split("_")
    if stage == "intro":
        return "Paid collaboration with {{CompanyName}}", "Hi {{FirstName}},\n\nBody of intro %s.\n\nThanks,\nHarish" % variant
    return "", "Hi {{FirstName}}, follow-up %s %s." % (stage, variant)


class World:
    """A fake GitHub: the template files that exist, what is in them, and everything committed or deleted."""

    def __init__(self, stages=STAGES, letters=LETTERS):
        self.files = {f"{s}_{v}.txt" for s in stages for v in letters}
        self.content = {}
        self.commits, self.deleted = [], []
        self.calls, self.fail_after, self.reduced = 0, None, None        # to change GitHub's answer between the page being drawn and an action being processed

    def read(self, name):
        return self.content.get(name) or _default(name[:-4])

    def write(self, name, subject, body):
        self.content[name] = (subject, body)


def lead(i, **kw):
    base = {"LeadID": str(i), "FirstName": f"First{i}", "LastName": f"Last{i}", "Email": f"lead{i}@example.com", "Company": f"Co{i}", "Approval": "Yes", "SenderAccount": "sales1", "Status": ""}
    base.update(kw)
    return base


@contextlib.contextmanager
def seq_page(world=None, leads=None, send_log=None, responses=None, preset=None, commit_error=None, delete_error=None):
    world = world or World()
    leads = [lead(1), lead(2)] if leads is None else leads
    ws = {f"{FIXTURE_CAMPAIGN} Master Sheet": FakeWorksheet(leads), f"{FIXTURE_CAMPAIGN} Response Sheet": FakeWorksheet(responses or []),
          f"{FIXTURE_CAMPAIGN} Custom Log Sheet": FakeWorksheet(send_log or []), f"{FIXTURE_CAMPAIGN} Error Log": FakeWorksheet([])}

    def get_file_content(self, path, ref="main"):
        if path.rsplit("/", 1)[-1] not in world.files:
            raise GitHubActionsError(f"404: {path} does not exist")
        subject, body = world.read(path.rsplit("/", 1)[-1])
        return f"Subject: {subject}\n\n{body}\n".encode("utf-8")

    def commit(self, files, commit_message):
        if commit_error:
            raise commit_error
        world.commits.append({"files": files, "message": commit_message})
        for f in files:
            name = f["path"].rsplit("/", 1)[-1]
            text = f["content"].decode("utf-8")
            subject_line, _, body = text.partition("\n\n")
            world.files.add(name)
            world.write(name, subject_line[len("Subject:"):].strip(), body.strip())

    def lister(self, path, ref="main"):
        world.calls += 1
        if world.fail_after is not None and world.calls > world.fail_after:
            return sorted(world.reduced)                                   # what GitHub says by the time the action asks again
        return sorted(world.files)

    def delete(self, path, message=""):
        if delete_error:
            raise delete_error
        world.deleted.append(path)
        world.files.discard(path.rsplit("/", 1)[-1])

    with patch("gspread.authorize", return_value=type("C", (), {"open_by_key": lambda self, k: FakeSpreadsheet(ws)})()), \
         patch("google.oauth2.service_account.Credentials.from_service_account_info", return_value=object()), \
         patch("github_client.GitHubClient.list_directory_files", lister), \
         patch("github_client.GitHubClient.get_file_content", get_file_content), \
         patch("github_client.GitHubClient.commit_campaign_files_directly", commit), \
         patch("github_client.GitHubClient.delete_file", delete):
        at = AppTest.from_file(os.path.join(PAGES_DIR, "campaigns.py"))
        at.secrets.update(_dashboard_secrets())
        for key, value in _authed_session().items():
            at.session_state[key] = value
        at.session_state["selected_campaign"] = FIXTURE_CAMPAIGN
        for key, value in (preset or {}).items():
            at.session_state[key] = value
        at.run(timeout=30)
        yield at, world


def go(at):
    at.run(timeout=30)


def pick_stage(at, index):
    next(b for b in at.button if b.key == f"sq_pick_{index}").click()
    go(at)


def pick_variant(at, letter):
    next(r for r in at.radio if (r.key or "").startswith("sq_variant_")).set_value(letter)
    go(at)


def unlock(at, prefix, variant, value=True):
    next(c for c in at.checkbox if c.key == f"unlock_{prefix}_{variant}").set_value(value)
    go(at)


def type_subject(at, prefix, variant, text):
    next(t for t in at.text_input if t.key == f"subject_{prefix}_{variant}").set_value(text)
    go(at)


def type_body(at, prefix, variant, text):
    next(t for t in at.text_area if t.key == f"body_{prefix}_{variant}").set_value(text)
    go(at)


def edit(at, prefix, variant, subject=None, body=None):
    if not next(c for c in at.checkbox if c.key == f"unlock_{prefix}_{variant}").value:
        unlock(at, prefix, variant)
    if subject is not None:
        type_subject(at, prefix, variant, subject)
    if body is not None:
        type_body(at, prefix, variant, body)


def save_button(at):
    return next(b for b in at.button if b.key == "sq_save_button")


def markup(at):
    return " ".join(m.value for m in at.markdown)


def block(at, css_class):
    """The markdown block that IS a `css_class` element (not the stylesheet block, which mentions every class name)."""
    return next(m.value for m in at.markdown if m.value.startswith(f'<div class="{css_class}"'))


def subject_input(at):
    return next(t for t in at.text_input if (t.key or "").startswith("subject_"))


def body_input(at):
    return next(t for t in at.text_area if (t.key or "").startswith("body_"))


# ================================================================ the stage list and the editor header

def test_every_stage_is_a_card_with_its_readable_name_and_the_first_one_is_selected():
    with seq_page() as (at, _):
        assert list(at.exception) == [] and list(at.error) == []
        assert [b.key for b in at.button if (b.key or "").startswith("sq_pick_")] == [f"sq_pick_{i}" for i in range(5)]
        cards = re.findall(r'class="sq-stage( sq-stage-on)?"><div class="sq-stage-top"><span class="sq-stage-name">(.*?)</span>', markup(at))
        assert [name for _, name in cards] == ["Intro", "Follow-up 1", "Follow-up 2", "Follow-up 3", "Follow-up 4"]
        assert [bool(on) for on, _ in cards] == [True, False, False, False, False]
        assert markup(at).count("4 variants") >= 5 and "Day 0" in markup(at)


def test_choosing_a_stage_selects_its_card_and_shows_its_templates():
    with seq_page() as (at, _):
        pick_stage(at, 2)
        cards = re.findall(r'class="sq-stage( sq-stage-on)?">', markup(at))
        assert [bool(on) for on in cards] == [False, False, True, False, False]
        assert subject_input(at).key == "subject_followup2_A" and body_input(at).key == "body_followup2_A"
        assert "Follow-up 2" in re.findall(r'class="sq-editor-title"><span>(.*?)</span>', markup(at))


def test_the_editor_shows_one_variant_at_a_time_chosen_with_the_tabs():
    with seq_page() as (at, _):
        radio = next(r for r in at.radio if (r.key or "").startswith("sq_variant_"))
        assert radio.options == ["Variant A", "Variant B", "Variant C", "Variant D"] and radio.value == "A"
        pick_variant(at, "C")
        assert subject_input(at).key == "subject_intro_C" and "Body of intro C." in body_input(at).value


def test_a_variant_starts_locked_and_the_unlock_box_is_the_editing_badge():
    with seq_page() as (at, _):
        box = next(c for c in at.checkbox if c.key == "unlock_intro_A")
        assert box.label == "🔒 Locked" and box.value is False and subject_input(at).disabled and body_input(at).disabled
        unlock(at, "intro", "A")
        box = next(c for c in at.checkbox if c.key == "unlock_intro_A")
        assert box.label == "🔓 Unlocked" and box.value is True and not subject_input(at).disabled and not body_input(at).disabled


def test_the_first_stage_needs_a_subject_and_a_follow_up_may_continue_the_thread():
    with seq_page() as (at, _):
        assert "required" in subject_input(at).label.lower() and "continues" not in subject_input(at).label.lower()
        pick_stage(at, 1)
        assert "continues the previous thread" in subject_input(at).label


def test_the_live_results_of_the_variant_on_screen_are_shown():
    leads = [lead(1, IntroVariant="A", IntroSentAt="2026-09-01 10:00:00", Status="Stopped - Replied"), lead(2, IntroVariant="A", IntroSentAt="2026-09-01 10:00:00", Status="Intro Sent"),
             lead(3, IntroVariant="A", IntroSentAt="2026-09-01 10:00:00", Status="Intro Sent")]
    log = [{"Timestamp": "2026-09-01 10:00:00", "LeadID": str(i), "Stage": "intro", "Variant": "A", "Status": "sent", "MessageID": f"<m{i}@x>", "Error": "", "SenderAccount": "sales1"} for i in (1, 2, 3)]
    responses = [{"ResponseID": "R1", "LeadID": "1", "ReceivedAt": "2026-09-02 10:00:00", "Classification": "Genuine Reply", "Intent": "Interested", "InReplyTo": "<m1@x>"}]
    with seq_page(leads=leads, send_log=log, responses=responses) as (at, _):
        assert ">Sent 3<" in markup(at) and ">Replies 1<" in markup(at) and ">Reply rate 33.3%<" in markup(at)
        pick_variant(at, "B")
        assert ">Sent 0<" in markup(at) and ">Reply rate —<" in markup(at)                # nothing was sent for variant B


# ================================================================ unsaved edits are never lost

def test_an_unsaved_edit_survives_switching_to_another_variant_and_back():
    with seq_page() as (at, _):
        edit(at, "intro", "A", subject="My new intro subject")
        assert save_button(at).label == "💾 Save changes (1)"
        pick_variant(at, "B")
        assert save_button(at).label == "💾 Save changes (1)" and subject_input(at).key == "subject_intro_B"       # still counted while another variant is on screen
        pick_variant(at, "A")
        assert subject_input(at).value == "My new intro subject" and next(c for c in at.checkbox if c.key == "unlock_intro_A").value is True
        assert not subject_input(at).disabled and save_button(at).label == "💾 Save changes (1)"


def test_an_unsaved_edit_survives_switching_to_another_stage_and_back():
    with seq_page() as (at, _):
        edit(at, "intro", "A", body="Hi {{FirstName}}, a rewritten intro.")
        pick_stage(at, 3)
        assert save_button(at).label == "💾 Save changes (1)"
        pick_stage(at, 0)
        assert body_input(at).value == "Hi {{FirstName}}, a rewritten intro." and save_button(at).label == "💾 Save changes (1)"


def test_edits_in_several_variants_and_stages_are_saved_together_in_one_commit_with_the_right_content():
    with seq_page() as (at, world):
        edit(at, "intro", "A", subject="Intro A subject")
        pick_variant(at, "C")
        edit(at, "intro", "C", body="Intro C body, edited.")
        pick_stage(at, 2)
        edit(at, "followup2", "C", body="Follow-up 2 C body, edited.")
        assert save_button(at).label == "💾 Save changes (3)" and save_button(at).disabled is False
        save_button(at).click()
        go(at)
        assert list(at.exception) == [] and list(at.error) == [] and len(world.commits) == 1
        files = {f["path"].rsplit("/", 1)[-1]: f["content"].decode("utf-8") for f in world.commits[0]["files"]}
        assert sorted(files) == ["followup2_C.txt", "intro_A.txt", "intro_C.txt"]
        assert files["intro_A.txt"].startswith("Subject: Intro A subject\n\n") and "Body of intro A." in files["intro_A.txt"]
        assert files["intro_C.txt"].startswith("Subject: Paid collaboration with {{CompanyName}}\n\nIntro C body, edited.")
        assert files["followup2_C.txt"].startswith("Subject: \n\nFollow-up 2 C body, edited.")
        assert world.commits[0]["message"].startswith(f"Edit 3 template(s) in {FIXTURE_CAMPAIGN} (via Streamlit, by ")
        assert any("Saved 3 template(s)" in s.value for s in at.success)


def test_after_saving_nothing_is_pending_and_the_saved_text_is_what_is_shown():
    with seq_page() as (at, world):
        edit(at, "intro", "A", subject="Saved subject")
        save_button(at).click()
        go(at)
        go(at)
        assert save_button(at).label == "💾 Save changes (0)" and save_button(at).disabled is True
        assert world.read("intro_A.txt")[0] == "Saved subject" and at.session_state["seq_state"][FIXTURE_CAMPAIGN]["drafts"] == {}
        assert not any("Set aside" in w.value for w in at.warning)                         # a good save must not be followed by a warning about the edit it just saved


def test_discard_puts_every_edited_variant_back_to_the_saved_text_and_locks_it():
    with seq_page() as (at, world):
        edit(at, "intro", "A", subject="Will be discarded")
        pick_variant(at, "B")
        edit(at, "intro", "B", body="Also discarded")
        assert save_button(at).label == "💾 Save changes (2)"
        next(b for b in at.button if b.key == "sq_discard_button").click()
        go(at)
        assert save_button(at).label == "💾 Save changes (0)" and save_button(at).disabled is True and world.commits == []
        assert body_input(at).disabled and "Body of intro B." in body_input(at).value
        pick_variant(at, "A")
        assert subject_input(at).value == "Paid collaboration with {{CompanyName}}" and subject_input(at).disabled


def test_locking_a_variant_again_means_its_edit_no_longer_counts():
    with seq_page() as (at, world):
        edit(at, "intro", "A", subject="Typed, then locked again")
        assert save_button(at).label == "💾 Save changes (1)"
        unlock(at, "intro", "A", False)
        assert save_button(at).label == "💾 Save changes (0)" and save_button(at).disabled is True and world.commits == []


def test_changing_a_variant_back_to_what_is_saved_removes_it_from_the_pending_count():
    with seq_page() as (at, _):
        edit(at, "intro", "A", subject="Different")
        assert save_button(at).label == "💾 Save changes (1)"
        type_subject(at, "intro", "A", "Paid collaboration with {{CompanyName}}")
        assert save_button(at).label == "💾 Save changes (0)"


def test_text_saved_elsewhere_meanwhile_is_never_overwritten_by_an_older_edit_on_screen():
    """Someone else changes intro_A on GitHub while you have an unsaved edit of it open: your edit is set aside (and you are told), not saved over theirs."""
    with seq_page() as (at, world):
        edit(at, "intro", "A", subject="My edit, based on the old text")
        world.write("intro_A.txt", "Their newer subject", "Body of intro A.")
        go(at)
        assert any("Set aside your unsaved edit to intro_A" in w.value for w in at.warning)
        assert save_button(at).label == "💾 Save changes (0)" and save_button(at).disabled is True and world.commits == []
        assert subject_input(at).value == "Their newer subject" and subject_input(at).disabled


def test_text_saved_elsewhere_meanwhile_is_not_overwritten_for_an_edit_kept_from_another_variant_either():
    with seq_page() as (at, world):
        edit(at, "intro", "A", subject="My edit to A")
        pick_variant(at, "B")
        world.write("intro_A.txt", "Changed on GitHub", "Body of intro A.")
        go(at)
        assert any("Set aside your unsaved edit to intro_A" in w.value for w in at.warning)
        assert save_button(at).label == "💾 Save changes (0)" and world.commits == []


def test_an_edit_for_a_variant_that_has_since_been_deleted_is_set_aside_not_saved():
    with seq_page() as (at, world):
        pick_variant(at, "D")
        edit(at, "intro", "D", subject="Edit to a variant that goes away")
        pick_variant(at, "A")
        world.files -= {f"{s}_D.txt" for s in STAGES}
        go(at)
        assert any("Set aside your unsaved edit to intro_D" in w.value for w in at.warning)
        assert save_button(at).label == "💾 Save changes (0)" and world.commits == []


def test_a_fresh_visit_to_a_campaign_starts_with_no_old_drafts_and_a_different_campaigns_state_is_left_alone():
    stale = {"seq_state": {FIXTURE_CAMPAIGN: {"stage": 3, "mode": "add_stage", "drafts": {"intro|A": {"prefix": "intro", "variant": "A", "base": ("x", "y"), "subject": "old", "body": "old"}}, "flash": None},
                           "Another_Campaign": {"stage": 1, "mode": "edit", "drafts": {"intro|B": {"keep": "me"}}, "flash": None}}}
    with seq_page(preset=stale) as (at, _):
        state = at.session_state["seq_state"]
        assert state[FIXTURE_CAMPAIGN]["drafts"] == {} and state[FIXTURE_CAMPAIGN]["mode"] == "edit" and state[FIXTURE_CAMPAIGN]["stage"] == 0
        assert state["Another_Campaign"]["drafts"] == {"intro|B": {"keep": "me"}}


# ================================================================ validation

def test_a_blank_subject_on_the_first_stage_is_refused_with_the_same_message_and_save_stays_off():
    with seq_page() as (at, world):
        edit(at, "intro", "A", subject="")
        assert any(e.value.startswith("intro_A:") for e in at.error) and save_button(at).disabled is True and world.commits == []


def test_a_blank_subject_on_a_follow_up_is_allowed_because_it_continues_the_thread():
    with seq_page() as (at, world):
        pick_stage(at, 1)
        edit(at, "followup1", "A", subject="", body="A changed follow-up body.")
        assert list(at.error) == [] and save_button(at).disabled is False
        save_button(at).click()
        go(at)
        assert len(world.commits) == 1


def test_an_invalid_edit_elsewhere_blocks_saving_everything_and_names_it():
    with seq_page() as (at, world):
        pick_variant(at, "B")
        edit(at, "intro", "B", subject="")
        pick_stage(at, 1)
        pick_variant(at, "A")                                           # (the editor keeps the variant you were on, B, when you change stage)
        edit(at, "followup1", "A", body="Fine edit")
        assert any(e.value.startswith("intro_B:") for e in at.error) and save_button(at).disabled is True and world.commits == []


# ================================================================ placeholders

def test_the_placeholders_offered_are_the_real_ones_plus_the_leads_custom_columns_and_never_an_internal_one():
    leads = [lead(1, Client="DubeRobe", Product="SheRobe", _row=2)]
    with seq_page(leads=leads) as (at, _):
        group = next(g for g in at.get("button_group") if g.key == "sq_insert_intro_A")
        assert group.options == ["{{FirstName}}", "{{LastName}}", "{{CompanyName}}", "{{Email}}", "{{Client}}", "{{Product}}"]


def test_clicking_a_placeholder_adds_it_to_the_end_of_the_body_and_the_choice_clears():
    with seq_page() as (at, _):
        unlock(at, "intro", "A")
        next(g for g in at.get("button_group") if g.key == "sq_insert_intro_A").set_value("{{Email}}")
        go(at)
        assert body_input(at).value.endswith("Harish\n{{Email}}") and next(g for g in at.get("button_group") if g.key == "sq_insert_intro_A").value is None
        assert save_button(at).label == "💾 Save changes (1)"                                        # and it counts as an edit


def test_the_placeholders_cannot_be_clicked_while_the_variant_is_locked():
    with seq_page() as (at, _):
        assert next(g for g in at.get("button_group") if g.key == "sq_insert_intro_A").proto.disabled is True
        unlock(at, "intro", "A")
        assert next(g for g in at.get("button_group") if g.key == "sq_insert_intro_A").proto.disabled is False


# ================================================================ the preview and the checks

def test_the_preview_shows_the_email_as_the_first_lead_will_see_it():
    with seq_page(leads=[lead(1, FirstName="Maya", LastName="Okafor", Company="BrightLeaf")]) as (at, _):
        text = markup(at)
        assert "Preview — using your first lead" in text and "From: You (sales1) → To: Maya Okafor" in text
        assert '<div class="sq-preview-subject">Paid collaboration with BrightLeaf</div>' in text
        assert "<p>Hi Maya,</p><p>Body of intro A.</p><p>Thanks,<br>Harish</p>" in text


def test_the_preview_follows_the_lead_you_pick():
    with seq_page(leads=[lead(1, FirstName="Maya", Company="BrightLeaf"), lead(2, FirstName="Devon", Company="DubeRobe")]) as (at, _):
        picker = next(s for s in at.selectbox if (s.key or "").startswith("sq_preview_lead_"))
        assert picker.options[0].startswith("Maya ") and picker.options[1].startswith("Devon ")
        picker.set_value(picker.options[1])
        go(at)
        text = markup(at)
        assert "Preview — using the lead you picked" in text and "To: Devon" in text and "Paid collaboration with DubeRobe" in text and "Hi Devon," in text


def test_the_preview_shows_what_is_typed_before_it_is_saved():
    with seq_page(leads=[lead(1, FirstName="Maya")]) as (at, _):
        edit(at, "intro", "A", subject="A subject typed just now", body="Dear {{FirstName}}, typed just now.")
        text = markup(at)
        assert '<div class="sq-preview-subject">A subject typed just now</div>' in text and "<p>Dear Maya, typed just now.</p>" in text


def test_a_blank_follow_up_subject_is_previewed_as_continuing_the_thread():
    with seq_page() as (at, _):
        pick_stage(at, 1)
        assert "(blank — continues the previous thread)" in markup(at)


def test_text_from_a_lead_or_a_template_can_never_inject_markup_into_the_preview():
    hostile_lead = lead(1, FirstName="<img src=x onerror=alert(1)>", LastName="", Company='"><script>alert(2)</script>')
    world = World()
    world.write("intro_A.txt", "<b>Bold</b> {{CompanyName}}", "Hi {{FirstName}}\n\n<script>alert(3)</script>")
    with seq_page(world=world, leads=[hostile_lead]) as (at, _):
        preview = block(at, "sq-preview")
        assert "<script" not in preview and "<img" not in preview and "<b>Bold" not in preview and "&lt;script&gt;" in preview


def test_the_check_card_ticks_a_clean_template():
    with seq_page() as (at, _):
        card = block(at, "sq-checks")
        assert card.count("sq-ok") == 3 and "sq-warn" not in card and "Every placeholder has a value for this lead" in card


def test_the_check_card_warns_about_a_misspelt_placeholder_a_long_subject_and_a_link_shortener():
    with seq_page() as (at, _):
        edit(at, "intro", "A", subject="x" * 80, body="Dear {{Frist}}, see bit.ly/abc")
        card = block(at, "sq-checks")
        assert "No column is named {{Frist}}" in card and "Subject is 80 characters (over 70)" in card and "Link shortener found: bit.ly" in card and card.count("sq-warn") == 3


def test_the_checks_only_advise_and_never_block_saving():
    with seq_page() as (at, world):
        edit(at, "intro", "A", subject="x" * 80, body="Dear {{Frist}}, see bit.ly/abc")
        assert list(at.error) == [] and save_button(at).disabled is False
        save_button(at).click()
        go(at)
        assert len(world.commits) == 1


def test_a_campaign_with_no_leads_still_works_and_says_the_preview_is_the_template_as_written():
    with seq_page(leads=[]) as (at, _):
        assert list(at.exception) == [] and any("No leads yet, so the preview shows your template as written." in c.value for c in at.caption)
        card = block(at, "sq-checks")
        assert "No lead to check the placeholders against" in card
        group = next(g for g in at.get("button_group") if g.key == "sq_insert_intro_A")
        assert group.options == ["{{FirstName}}", "{{LastName}}", "{{CompanyName}}", "{{Email}}"]


# ================================================================ adding a variant

def _letters_world(letters):
    return World(letters=letters)


def test_add_variant_opens_a_form_with_every_stage_and_cancel_goes_back_without_changing_anything():
    with seq_page(world=_letters_world("AB")) as (at, world):
        next(b for b in at.button if b.key == "sq_add_variant_button").click()
        go(at)
        inputs = [t.key for t in at.text_input if (t.key or "").startswith("newvariant_subject_")]
        assert inputs == [f"newvariant_subject_{FIXTURE_CAMPAIGN}_{s}_C" for s in STAGES]
        assert any(b.label == "Add Variant C" for b in at.button) and not any((b.key or "").startswith("sq_pick_") is False and b.key == "sq_save_button" for b in at.button)
        next(b for b in at.button if b.key == "sq_cancel_variant_button").click()
        go(at)
        assert any(b.key == "sq_save_button" for b in at.button) and world.commits == []


def test_a_new_variant_needs_content_for_every_stage_before_anything_is_committed():
    with seq_page(world=_letters_world("AB")) as (at, world):
        next(b for b in at.button if b.key == "sq_add_variant_button").click()
        go(at)
        next(b for b in at.button if b.label == "Add Variant C").click()
        go(at)
        assert at.error and world.commits == []


def test_adding_a_variant_commits_one_file_per_stage_shows_a_confirmation_and_selects_the_new_variant():
    with seq_page(world=_letters_world("AB")) as (at, world):
        next(b for b in at.button if b.key == "sq_add_variant_button").click()
        go(at)
        for stage in STAGES:
            next(t for t in at.text_input if t.key == f"newvariant_subject_{FIXTURE_CAMPAIGN}_{stage}_C").set_value("Intro C subject" if stage == "intro" else "")
            next(t for t in at.text_area if t.key == f"newvariant_body_{FIXTURE_CAMPAIGN}_{stage}_C").set_value(f"Body C for {stage}")
        go(at)
        next(b for b in at.button if b.label == "Add Variant C").click()
        go(at)
        assert list(at.exception) == [] and len(world.commits) == 1
        paths = sorted(f["path"].rsplit("/", 1)[-1] for f in world.commits[0]["files"])
        assert paths == sorted(f"{s}_C.txt" for s in STAGES) and world.commits[0]["message"].startswith(f"Add variant C to {FIXTURE_CAMPAIGN} (via Streamlit, by ")
        assert any("Variant C added to all 5 stage(s)" in s.value for s in at.success)                      # the confirmation is shown on the editor that follows
        assert next(r for r in at.radio if (r.key or "").startswith("sq_variant_")).value == "C" and subject_input(at).key == "subject_intro_C"


def test_the_add_variant_button_is_off_with_a_reason_when_all_four_variants_exist():
    with seq_page() as (at, _):
        button = next(b for b in at.button if b.key == "sq_add_variant_button")
        assert button.disabled is True and any("maximum" in c.value for c in at.caption)


# ================================================================ adding a stage

def test_add_stage_opens_a_form_for_the_next_stage_with_every_variant_and_cancel_goes_back():
    with seq_page() as (at, world):
        next(b for b in at.button if b.key == "sq_add_stage_button").click()
        go(at)
        assert any("followup5" in m.value and "Required variants" in m.value for m in at.markdown)
        assert [t.key for t in at.text_input if (t.key or "").startswith("followup_subject_")] == [f"followup_subject_{FIXTURE_CAMPAIGN}_followup5_{v}" for v in LETTERS]
        next(b for b in at.button if b.key == "sq_cancel_stage_button").click()
        go(at)
        assert any(b.key == "sq_save_button" for b in at.button) and world.commits == []


def test_adding_a_stage_commits_one_file_per_variant_shows_a_confirmation_and_opens_the_new_stage():
    with seq_page() as (at, world):
        next(b for b in at.button if b.key == "sq_add_stage_button").click()
        go(at)
        for v in LETTERS:
            next(t for t in at.text_area if t.key == f"followup_body_{FIXTURE_CAMPAIGN}_followup5_{v}").set_value(f"Body of followup5 {v}")
        go(at)
        next(b for b in at.button if b.label == "Add followup5").click()
        go(at)
        assert list(at.exception) == [] and len(world.commits) == 1
        assert sorted(f["path"].rsplit("/", 1)[-1] for f in world.commits[0]["files"]) == [f"followup5_{v}.txt" for v in LETTERS]
        assert world.commits[0]["message"].startswith(f"Add followup5 to {FIXTURE_CAMPAIGN} (via Streamlit, by ")
        assert any("'followup5' added" in s.value for s in at.success)
        assert [b.key for b in at.button if (b.key or "").startswith("sq_pick_")][-1] == "sq_pick_5" and subject_input(at).key == "subject_followup5_A"


# ================================================================ deleting a variant

def test_deleting_a_variant_needs_its_own_tick_and_then_removes_it_from_every_stage():
    with seq_page() as (at, world):
        pick_variant(at, "C")
        confirm = next(c for c in at.checkbox if c.key == "confirm_delete_variant_C")
        assert confirm.value is False and next(b for b in at.button if b.key == "delete_variant_button").disabled is True
        confirm.set_value(True)
        go(at)
        next(b for b in at.button if b.key == "delete_variant_button").click()
        go(at)
        assert list(at.exception) == [] and list(at.error) == []
        assert sorted(p.rsplit("/", 1)[-1] for p in world.deleted) == sorted(f"{s}_C.txt" for s in STAGES)
        assert any("Variant C deleted from all 5 stage(s)" in s.value for s in at.success)


def test_a_tick_given_for_one_variant_can_never_confirm_deleting_another():
    """The safeguard this layout needs: with one shared tick box, ticking it for A and switching to B would leave B ready to delete."""
    with seq_page() as (at, world):
        next(c for c in at.checkbox if c.key == "confirm_delete_variant_A").set_value(True)
        go(at)
        pick_variant(at, "B")
        assert [c.key for c in at.checkbox if (c.key or "").startswith("confirm_delete_variant_")] == ["confirm_delete_variant_B"]
        assert next(c for c in at.checkbox if c.key == "confirm_delete_variant_B").value is False
        assert next(b for b in at.button if b.key == "delete_variant_button").disabled is True and world.deleted == []


def test_the_last_remaining_variant_cannot_be_deleted():
    with seq_page(world=_letters_world("A")) as (at, world):
        assert not any((c.key or "").startswith("confirm_delete_variant_") for c in at.checkbox)
        assert any("last variant" in i.value.lower() or "only variant" in i.value.lower() or "at least one" in i.value.lower() for i in at.info) and world.deleted == []


# ================================================================ deleting the last stage

def test_deleting_the_last_stage_needs_its_own_tick_and_removes_all_its_variant_files():
    with seq_page() as (at, world):
        confirm = next(c for c in at.checkbox if c.key == "confirm_delete_stage_followup4")
        assert confirm.value is False and next(b for b in at.button if b.key == "delete_stage_button").disabled is True
        confirm.set_value(True)
        go(at)
        next(b for b in at.button if b.key == "delete_stage_button").click()
        go(at)
        assert list(at.exception) == [] and list(at.error) == []
        assert sorted(p.rsplit("/", 1)[-1] for p in world.deleted) == [f"followup4_{v}.txt" for v in LETTERS]


def test_a_tick_given_for_one_stage_can_never_confirm_deleting_the_next_last_stage():
    """After the last stage goes, the one before it becomes the last: the earlier tick must not carry over and delete it with one click."""
    with seq_page() as (at, world):
        next(c for c in at.checkbox if c.key == "confirm_delete_stage_followup4").set_value(True)
        go(at)
        next(b for b in at.button if b.key == "delete_stage_button").click()
        go(at)
        go(at)
        assert [c.key for c in at.checkbox if (c.key or "").startswith("confirm_delete_stage_")] == ["confirm_delete_stage_followup3"]
        assert next(c for c in at.checkbox if c.key == "confirm_delete_stage_followup3").value is False
        assert next(b for b in at.button if b.key == "delete_stage_button").disabled is True
        assert all("followup3" not in p for p in world.deleted)


def test_after_the_last_stage_is_deleted_the_stage_list_and_the_selection_stay_valid():
    with seq_page() as (at, world):
        pick_stage(at, 4)
        next(c for c in at.checkbox if c.key == "confirm_delete_stage_followup4").set_value(True)
        go(at)
        next(b for b in at.button if b.key == "delete_stage_button").click()
        go(at)
        go(at)
        assert list(at.exception) == [] and [b.key for b in at.button if (b.key or "").startswith("sq_pick_")] == [f"sq_pick_{i}" for i in range(4)]
        assert subject_input(at).key == "subject_followup3_A"


# ================================================================ nothing else changed

def test_the_tab_reads_templates_live_and_never_writes_just_by_being_looked_at():
    with seq_page() as (at, world):
        pick_stage(at, 1)
        pick_variant(at, "B")
        pick_stage(at, 0)
        assert list(at.exception) == [] and world.commits == [] and world.deleted == []


def test_when_the_templates_cannot_be_read_the_tab_says_so_and_does_not_crash():
    with seq_page() as (at, world):
        with patch("github_client.GitHubClient.list_directory_files", side_effect=RuntimeError("GitHub is down")):
            st.cache_data.clear()
            at.run(timeout=30)
        assert list(at.exception) == [] and any("Couldn't read templates for" in e.value and "GitHub is down" in e.value for e in at.error)


# ================================================================ moving around after things change

def test_after_the_variant_on_screen_is_deleted_the_editor_falls_back_to_the_first_variant_without_an_error():
    with seq_page() as (at, world):
        pick_variant(at, "D")
        next(c for c in at.checkbox if c.key == "confirm_delete_variant_D").set_value(True)
        go(at)
        next(b for b in at.button if b.key == "delete_variant_button").click()
        go(at)
        go(at)
        radio = next(r for r in at.radio if (r.key or "").startswith("sq_variant_"))
        assert list(at.exception) == [] and radio.options == ["Variant A", "Variant B", "Variant C"] and radio.value == "A" and subject_input(at).key == "subject_intro_A"


def test_choosing_a_stage_while_a_form_is_open_closes_the_form_and_shows_that_stage():
    with seq_page(world=_letters_world("AB")) as (at, _):
        next(b for b in at.button if b.key == "sq_add_variant_button").click()
        go(at)
        assert any(b.label == "Add Variant C" for b in at.button)
        pick_stage(at, 3)
        assert not any(b.label == "Add Variant C" for b in at.button) and subject_input(at).key == "subject_followup3_A"
        next(b for b in at.button if b.key == "sq_add_stage_button").click()
        go(at)
        assert any("followup5" in m.value and "Required variants" in m.value for m in at.markdown)
        pick_stage(at, 1)
        assert not any(b.label == "Add followup5" for b in at.button) and subject_input(at).key == "subject_followup1_A"


def test_a_new_stage_needs_a_body_for_every_variant_before_anything_is_committed():
    with seq_page() as (at, world):
        next(b for b in at.button if b.key == "sq_add_stage_button").click()
        go(at)
        next(b for b in at.button if b.label == "Add followup5").click()                  # everything still blank
        go(at)
        assert any("Variant A" in e.value for e in at.error) and world.commits == []
        for v in "AB":                                                                      # filling in only some variants is still refused
            next(t for t in at.text_area if t.key == f"followup_body_{FIXTURE_CAMPAIGN}_followup5_{v}").set_value("Some body")
        go(at)
        next(b for b in at.button if b.label == "Add followup5").click()
        go(at)
        assert any("Variant C" in e.value for e in at.error) and world.commits == []


# ================================================================ when GitHub says no, the message appears and nothing typed is lost

def test_a_failed_save_says_why_and_keeps_the_edit_so_it_can_be_tried_again():
    with seq_page(commit_error=GitHubActionsError("boom")) as (at, world):
        edit(at, "intro", "A", subject="Important edit")
        save_button(at).click()
        go(at)
        assert list(at.exception) == [] and any("Save failed: boom" in e.value for e in at.error) and not at.success
        assert save_button(at).label == "💾 Save changes (1)" and save_button(at).disabled is False and subject_input(at).value == "Important edit" and world.commits == []


def test_a_failed_add_variant_says_why_and_keeps_the_form_and_what_was_typed():
    with seq_page(world=_letters_world("AB"), commit_error=GitHubActionsError("boom")) as (at, world):
        next(b for b in at.button if b.key == "sq_add_variant_button").click()
        go(at)
        for stage in STAGES:
            next(t for t in at.text_area if t.key == f"newvariant_body_{FIXTURE_CAMPAIGN}_{stage}_C").set_value(f"Body C {stage}")
            next(t for t in at.text_input if t.key == f"newvariant_subject_{FIXTURE_CAMPAIGN}_{stage}_C").set_value("S" if stage == "intro" else "")
        go(at)
        next(b for b in at.button if b.label == "Add Variant C").click()
        go(at)
        assert any("Failed to add variant: boom" in e.value for e in at.error) and any(b.label == "Add Variant C" for b in at.button)
        assert next(t for t in at.text_area if t.key == f"newvariant_body_{FIXTURE_CAMPAIGN}_intro_C").value == "Body C intro"


def test_a_failed_add_stage_says_why_and_keeps_the_form_and_what_was_typed():
    with seq_page(commit_error=GitHubActionsError("boom")) as (at, world):
        next(b for b in at.button if b.key == "sq_add_stage_button").click()
        go(at)
        for v in LETTERS:
            next(t for t in at.text_area if t.key == f"followup_body_{FIXTURE_CAMPAIGN}_followup5_{v}").set_value(f"Body {v}")
        go(at)
        next(b for b in at.button if b.label == "Add followup5").click()
        go(at)
        assert any("Failed to add stage: boom" in e.value for e in at.error) and any(b.label == "Add followup5" for b in at.button)
        assert next(t for t in at.text_area if t.key == f"followup_body_{FIXTURE_CAMPAIGN}_followup5_B").value == "Body B"


def test_a_failed_delete_of_a_variant_or_a_stage_says_why():
    with seq_page(delete_error=GitHubActionsError("boom")) as (at, world):
        next(c for c in at.checkbox if c.key == "confirm_delete_variant_A").set_value(True)
        go(at)
        next(b for b in at.button if b.key == "delete_variant_button").click()
        go(at)
        assert any("Failed to delete variant: boom" in e.value for e in at.error)
        next(c for c in at.checkbox if c.key == "confirm_delete_stage_followup4").set_value(True)
        go(at)
        next(b for b in at.button if b.key == "delete_stage_button").click()
        go(at)
        assert any("Failed to delete stage: boom" in e.value for e in at.error) and world.deleted == []


def test_a_failed_save_keeps_every_edit_including_those_kept_for_variants_not_on_screen():
    with seq_page(commit_error=GitHubActionsError("boom")) as (at, world):
        edit(at, "intro", "A", subject="Edit to A")
        pick_variant(at, "B")
        edit(at, "intro", "B", subject="Edit to B")
        save_button(at).click()
        go(at)
        assert any("Save failed: boom" in e.value for e in at.error)
        go(at)
        assert save_button(at).label == "💾 Save changes (2)" and world.commits == []
        pick_variant(at, "A")
        assert subject_input(at).value == "Edit to A" and save_button(at).label == "💾 Save changes (2)"


# ================================================================ the last check before anything is deleted: GitHub is asked again

def _arm_live_change(at, world, without):
    """Make GitHub's answer change AFTER this page has been drawn but before the next click is processed: the click's own redraw reads the structure
    exactly as often as the redraw just before it did, so the first read beyond that is the delete action's own fresh check."""
    world.calls = 0
    go(at)
    world.reduced = {name for name in world.files if not without(name)}
    world.fail_after, world.calls = world.calls, 0


def test_deleting_a_stage_is_refused_if_github_changed_since_the_page_was_drawn():
    with seq_page() as (at, world):
        next(c for c in at.checkbox if c.key == "confirm_delete_stage_followup4").set_value(True)
        _arm_live_change(at, world, without=lambda name: name.startswith("followup4_"))             # someone else already removed followup4
        next(b for b in at.button if b.key == "delete_stage_button").click()
        go(at)
        assert world.deleted == [] and any("out of date" in e.value and "followup3" in e.value for e in at.error)


def test_deleting_a_variant_is_refused_if_github_changed_since_the_page_was_drawn():
    with seq_page() as (at, world):
        pick_variant(at, "C")
        next(c for c in at.checkbox if c.key == "confirm_delete_variant_C").set_value(True)
        _arm_live_change(at, world, without=lambda name: name.endswith("_C.txt"))                    # someone else already removed variant C
        next(b for b in at.button if b.key == "delete_variant_button").click()
        go(at)
        assert world.deleted == [] and any("no longer has variant 'C'" in e.value for e in at.error)


def test_every_stage_and_variant_shows_its_own_results_and_says_which_they_are():
    """Hand-derived: intro A 4 sent / 2 replied, intro B 3 sent / 0, followup1 A 2 sent / 1 replied, followup1 B 5 sent / 1 replied — every combination must show ONLY its own numbers."""
    plan = [("intro", "A", 4, 2), ("intro", "B", 3, 0), ("followup1", "A", 2, 1), ("followup1", "B", 5, 1)]
    leads, log, responses, n = [], [], [], 0
    for stage, variant, sent, replied in plan:
        for k in range(sent):
            n += 1
            leads.append(lead(n, Status="Stopped - Replied" if k < replied else "Intro Sent"))
            log.append({"Timestamp": "2026-09-01 10:00:00", "LeadID": str(n), "Stage": stage, "Variant": variant, "Status": "sent", "MessageID": f"<m{n}@x>", "Error": "", "SenderAccount": "sales1"})
            if k < replied:
                responses.append({"ResponseID": f"R{n}", "LeadID": str(n), "ReceivedAt": "2026-09-02 10:00:00", "Classification": "Genuine Reply", "Intent": "Interested", "InReplyTo": f"<m{n}@x>"})
    with seq_page(leads=leads, send_log=log, responses=responses) as (at, _):
        seen = {}
        for stage_index, prefix in ((0, "intro"), (1, "followup1")):
            pick_stage(at, stage_index)
            for letter in "AB":
                pick_variant(at, letter)
                text = markup(at)
                seen[(prefix, letter)] = tuple(re.search(pattern, text).group(1) for pattern in (r">Sent (\d+)<", r">Replies (\d+)<", r">Reply rate ([^<]+)<"))
                title = "Intro" if prefix == "intro" else "Follow-up 1"
                assert f'<span class="sq-scope">{title} · Variant {letter}</span>' in text
        assert seen == {("intro", "A"): ("4", "2", "50.0%"), ("intro", "B"): ("3", "0", "0.0%"), ("followup1", "A"): ("2", "1", "50.0%"), ("followup1", "B"): ("5", "1", "20.0%")}
