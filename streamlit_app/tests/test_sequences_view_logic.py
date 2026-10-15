"""The Sequences tab's surroundings: stage cards, real placeholders, the pre-save checks, a variant's live results, the preview's lead picker, and
the store that keeps unsaved edits safe."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import sequences_view_logic as S  # noqa: E402
from sequences_logic import has_content_changed  # noqa: E402

LEAD = {"LeadID": "7", "FirstName": "Maya", "LastName": "Okafor", "Email": "maya@example.com", "Company": "BrightLeaf", "Client": "DubeRobe", "Product": ""}


# ================================================================ the stage cards

STAGES = [{"template_prefix": "intro"}, {"template_prefix": "followup1"}, {"template_prefix": "followup2"}, {"template_prefix": "followup3"}]
CFG_STAGES = [{"template_prefix": "intro", "wait_days_after_previous": 0}, {"template_prefix": "followup1", "wait_days_after_previous": 2},
              {"template_prefix": "followup2", "wait_days_after_previous": 1}, {"template_prefix": "followup3", "wait_days_after_previous": 0}]


def test_each_stage_gets_a_readable_title_and_the_wait_after_the_previous_stage():
    cards = S.stage_cards(STAGES, CFG_STAGES, 3)
    assert [c["title"] for c in cards] == ["Intro", "Follow-up 1", "Follow-up 2", "Follow-up 3"]
    assert [c["badge"] for c in cards] == ["Day 0", "+2 days", "+1 day", "+0 days"]
    assert [c["prefix"] for c in cards] == ["intro", "followup1", "followup2", "followup3"]


def test_the_first_stage_is_day_zero_whatever_the_settings_say():
    assert S.stage_cards([{"template_prefix": "intro"}], [{"template_prefix": "intro", "wait_days_after_previous": 5}], 1)[0]["badge"] == "Day 0"


@pytest.mark.parametrize("cfg", [None, [], [{"template_prefix": "other", "wait_days_after_previous": 3}], [{"template_prefix": "followup1"}], [{"template_prefix": "followup1", "wait_days_after_previous": None}],
                                 [{"template_prefix": "followup1", "wait_days_after_previous": "2"}], [{"template_prefix": "followup1", "wait_days_after_previous": -1}], [{"template_prefix": "followup1", "wait_days_after_previous": True}]])
def test_a_stage_the_settings_do_not_know_gets_no_badge_instead_of_an_invented_one(cfg):
    assert S.stage_cards(STAGES[:2], cfg, 2)[1]["badge"] == ""


@pytest.mark.parametrize("count, text", [(1, "1 variant"), (2, "2 variants"), (4, "4 variants"), (0, "0 variants")])
def test_the_variant_count_is_worded_for_a_person(count, text):
    assert S.stage_cards(STAGES[:1], None, count)[0]["variants"] == text


def test_no_stages_gives_no_cards():
    assert S.stage_cards([], CFG_STAGES, 3) == []


# ================================================================ placeholders

SYSTEM = ["LeadID", "FirstName", "LastName", "Email", "Company", "Status", "SenderAccount", "IntroSentAt"]


def test_the_standard_placeholders_come_first_then_one_per_custom_column_and_never_a_system_column():
    leads = [{"LeadID": "1", "FirstName": "A", "Email": "a@x.com", "Status": "", "Client": "x", "Product": "y"}, {"LeadID": "2", "Brand": "z", "IntroSentAt": "", "Client": ""}]
    assert S.placeholder_tokens(leads, SYSTEM) == ["{{FirstName}}", "{{LastName}}", "{{CompanyName}}", "{{Email}}", "{{Brand}}", "{{Client}}", "{{Product}}"]


def test_a_campaign_with_no_leads_still_offers_the_standard_placeholders():
    assert S.placeholder_tokens([], SYSTEM) == ["{{FirstName}}", "{{LastName}}", "{{CompanyName}}", "{{Email}}"]


def test_a_column_name_that_cannot_be_a_placeholder_is_not_offered():
    tokens = S.placeholder_tokens([{"Client Name": "x", "ok_1": "y", "bad-name": "z", "": "w"}], SYSTEM)
    assert tokens[-1] == "{{ok_1}}" and "{{Client Name}}" not in tokens and "{{bad-name}}" not in tokens and len(tokens) == 5


def test_company_is_offered_as_companyname_not_twice():
    tokens = S.placeholder_tokens([{"Company": "Acme"}], SYSTEM)
    assert "{{CompanyName}}" in tokens and "{{Company}}" not in tokens


def test_unknown_and_blank_placeholders_are_told_apart():
    unknown, blank = S.placeholder_problems("Hi {{FirstName}} {{Nope}} {{Product}} {{Client}} {{LastName}}", LEAD)
    assert unknown == ["Nope"] and blank == ["Product"]                     # Product is a real column but empty for Maya; LastName and Client have values


def test_a_standard_placeholder_with_a_default_is_not_flagged_when_the_lead_has_no_value():
    unknown, blank = S.placeholder_problems("Hi {{FirstName}} at {{CompanyName}}", {"FirstName": "", "Company": ""})
    assert unknown == [] and blank == []                                    # "there" and "your team" are used


def test_a_standard_placeholder_with_no_default_is_flagged_when_empty():
    assert S.placeholder_problems("{{LastName}}", {"LastName": " "}) == ([], ["LastName"])
    assert S.placeholder_problems("{{Email}}", {}) == ([], ["Email"])


def test_each_problem_is_listed_once_however_often_it_is_used():
    assert S.placeholder_problems("{{Nope}} {{Nope}} {{Product}} {{Product}}", LEAD) == (["Nope"], ["Product"])


def test_spaces_inside_the_braces_are_understood():
    assert S.placeholder_problems("{{ Nope }}", LEAD) == (["Nope"], [])


def test_text_without_placeholders_has_no_problems():
    assert S.placeholder_problems("Plain text, {single braces} and {{}}", LEAD) == ([], [])


# ================================================================ link shorteners

@pytest.mark.parametrize("text, found", [
    ("see bit.ly/abc", ["bit.ly"]), ("https://bit.ly/abc and https://t.co/x", ["bit.ly", "t.co"]), ("HTTP://TinyURL.com/x", ["tinyurl.com"]), ("www.goo.gl/x", ["goo.gl"]),
    ("a bit.ly b bit.ly", ["bit.ly"]), ("rb.gy/z.", ["rb.gy"]), ("visit example.com/bit.lyx", []), ("orbit.ly is not it", []), ("bit.lyx", []), ("bit.ly.com/x", []),
    ("email me at me@bit.ly", []), ("about.co and net.co", []), ("t.com", []), ("", []), (None, []), ("lnkd.in/a", ["lnkd.in"]), ("https://example.com/a-bit.ly", []),
])
def test_only_real_link_shortener_domains_are_found_never_a_piece_of_a_longer_word(text, found):
    assert S.shortener_hits(text) == found


# ================================================================ the checks

def _ok(items):
    return [i["ok"] for i in items]


def test_a_clean_template_passes_all_three_checks_with_the_designs_wording():
    items = S.check_items("Paid collaboration with {{CompanyName}}", "Hi {{FirstName}}, we love your video.", LEAD, True)
    assert items == [{"ok": True, "text": "Every placeholder has a value for this lead"}, {"ok": True, "text": "Subject under 70 characters"}, {"ok": True, "text": "No link-shortener domains"}]


def test_a_misspelt_placeholder_fails_the_first_check_and_names_it():
    items = S.check_items("Hi", "Dear {{Frist}}", LEAD, True)
    assert items[0] == {"ok": False, "text": "No column is named {{Frist}}"}


def test_a_placeholder_empty_for_this_lead_fails_the_first_check_and_names_it():
    items = S.check_items("Hi", "About {{Product}}", LEAD, True)
    assert items[0] == {"ok": False, "text": "No value for this lead: {{Product}}"}


def test_an_unknown_placeholder_is_reported_before_a_blank_one():
    assert "No column is named {{Nope}}" == S.check_items("", "{{Nope}} {{Product}}", LEAD, False)[0]["text"]


def test_the_subject_limit_is_70_characters_of_the_rendered_subject():
    assert S.check_items("x" * 70, "b", LEAD, True)[1]["ok"] is True
    long = S.check_items("x" * 71, "b", LEAD, True)[1]
    assert long == {"ok": False, "text": "Subject is 71 characters (over 70)"}
    assert S.check_items("{{FirstName}}" + "x" * 60, "b", LEAD, True)[1]["ok"] is True          # "{{FirstName}}" shows as "Maya": 64 characters, not 73


def test_a_blank_subject_is_an_error_for_the_first_stage_but_normal_for_a_follow_up():
    assert S.check_items("", "b", LEAD, True)[1] == {"ok": False, "text": "Subject is required for the first stage"}
    assert S.check_items("  ", "b", LEAD, False)[1] == {"ok": True, "text": "Subject is blank, so it continues the previous thread"}


def test_a_link_shortener_in_the_subject_or_the_body_fails_the_third_check():
    assert S.check_items("Look bit.ly/x", "b", LEAD, True)[2] == {"ok": False, "text": "Link shortener found: bit.ly"}
    assert S.check_items("s", "go to https://tinyurl.com/q and t.co/z", LEAD, True)[2] == {"ok": False, "text": "Link shortener found: tinyurl.com, t.co"}


def test_with_no_lead_the_placeholder_check_says_so_instead_of_guessing():
    assert S.check_items("s", "Hi {{FirstName}}", None, True)[0] == {"ok": True, "text": "No lead to check the placeholders against"}
    assert S.check_items("s", "Hi {{FirstName}}", {}, True)[0]["ok"] is True


def test_there_are_always_exactly_three_checks_in_the_same_order():
    for subject, body, lead, first in [("", "", None, True), ("s", "b", LEAD, False), ("x" * 99, "bit.ly {{Nope}}", LEAD, True)]:
        assert len(S.check_items(subject, body, lead, first)) == 3


# ================================================================ a variant's live results

ROWS = [{"stage": "intro", "variant": "A", "sent": 306, "replies": 38, "reply_rate": 38 / 306}, {"stage": "intro", "variant": "B", "sent": 10, "replies": 0, "reply_rate": 0.0},
        {"stage": "followup1", "variant": "A", "sent": 50, "replies": 5, "reply_rate": 0.1}]


def test_a_variants_results_come_from_its_own_stage_and_letter():
    assert S.variant_stats(ROWS, "intro", "A") == {"sent": 306, "replies": 38, "rate": "12.4%"}
    assert S.variant_stats(ROWS, "followup1", "A") == {"sent": 50, "replies": 5, "rate": "10.0%"}
    assert S.variant_stats(ROWS, "intro", "B") == {"sent": 10, "replies": 0, "rate": "0.0%"}


@pytest.mark.parametrize("rows, prefix, variant", [([], "intro", "A"), (None, "intro", "A"), (ROWS, "intro", "C"), (ROWS, "followup9", "A")])
def test_a_variant_nothing_has_been_sent_for_shows_zero_and_a_dash(rows, prefix, variant):
    assert S.variant_stats(rows, prefix, variant) == {"sent": 0, "replies": 0, "rate": "—"}


# ================================================================ the preview's lead picker

def test_a_lead_is_labelled_by_name_and_address():
    assert S.lead_label(LEAD) == "Maya Okafor <maya@example.com>"
    assert S.lead_label({"FirstName": "Maya", "Email": "m@x.com"}) == "Maya <m@x.com>"
    assert S.lead_label({"Email": "m@x.com"}) == "m@x.com" and S.lead_label({"FirstName": "Maya"}) == "Maya" and S.lead_label({"LeadID": "9"}) == "Lead 9"


def test_two_people_with_the_same_name_stay_distinguishable():
    same = {"FirstName": "Sam", "Email": "s@x.com"}
    assert S.lead_options([same, same, {"FirstName": "Al", "Email": "a@x.com"}, same]) == ["Sam <s@x.com>", "Sam <s@x.com> (2)", "Al <a@x.com>", "Sam <s@x.com> (3)"]


def test_the_picker_offers_at_most_the_first_200_leads_in_sheet_order():
    leads = [{"FirstName": f"P{i}", "Email": f"p{i}@x.com"} for i in range(500)]
    options = S.lead_options(leads)
    assert len(options) == 200 and options[0].startswith("P0 ") and options[-1].startswith("P199 ") and S.lead_options([]) == [] and len(S.lead_options(leads, 5)) == 5


# ================================================================ unsaved edits

ORIG = {"subject": "Hello", "body": "Body"}


def _drafts_with(prefix="intro", variant="A", subject="Hello!", body="Body"):
    drafts = {}
    S.record_draft(drafts, prefix, variant, ORIG, subject, body, has_content_changed(ORIG, subject, body))
    return drafts


def test_an_edit_is_kept_while_it_differs_from_the_saved_text_and_forgotten_when_it_matches_again():
    drafts = _drafts_with()
    assert drafts["intro|A"] == {"prefix": "intro", "variant": "A", "base": ("Hello", "Body"), "subject": "Hello!", "body": "Body"}
    S.record_draft(drafts, "intro", "A", ORIG, "Hello", "Body", has_content_changed(ORIG, "Hello", "Body"))
    assert drafts == {}


def test_an_edit_in_progress_survives_moving_to_another_stage_and_variant_and_back():
    drafts = _drafts_with("intro", "A", "Changed", "Body")
    S.record_draft(drafts, "followup1", "B", ORIG, "", "New body", True)               # …then edit another one
    originals = {("intro", "A"): ORIG, ("followup1", "B"): ORIG, ("intro", "B"): ORIG}
    pending, dropped = S.settle_drafts(drafts, originals, has_content_changed)
    assert pending == [("followup1", "B", "", "New body"), ("intro", "A", "Changed", "Body")] and dropped == []


def test_the_pending_edits_are_in_a_stable_order():
    drafts = {}
    for p, v in (("followup2", "B"), ("intro", "A"), ("followup1", "A")):
        S.record_draft(drafts, p, v, ORIG, "x", "y", True)
    originals = {(p, v): ORIG for p, v in (("followup2", "B"), ("intro", "A"), ("followup1", "A"))}
    assert [(p, v) for p, v, _, _ in S.settle_drafts(drafts, originals, has_content_changed)[0]] == [("followup1", "A"), ("followup2", "B"), ("intro", "A")]


def test_a_draft_is_dropped_and_reported_when_the_saved_text_changed_since_it_was_started():
    """Saving it would overwrite someone else's newer text with a copy of the old one."""
    drafts = _drafts_with()
    newer = {("intro", "A"): {"subject": "Hello", "body": "Body, edited on GitHub meanwhile"}}
    pending, dropped = S.settle_drafts(drafts, newer, has_content_changed)
    assert pending == [] and dropped == ["intro|A"] and drafts == {}


def test_a_draft_for_a_variant_or_stage_that_no_longer_exists_is_dropped_and_reported():
    drafts = _drafts_with("followup3", "C")
    pending, dropped = S.settle_drafts(drafts, {("intro", "A"): ORIG}, has_content_changed)
    assert pending == [] and dropped == ["followup3|C"] and drafts == {}


def test_a_draft_that_has_come_to_match_the_saved_text_disappears_quietly():
    drafts = {"intro|A": {"prefix": "intro", "variant": "A", "base": ("Hello", "Body"), "subject": "Hello", "body": "Body"}}
    assert S.settle_drafts(drafts, {("intro", "A"): ORIG}, has_content_changed) == ([], []) and drafts == {}


def test_settling_with_no_drafts_is_fine():
    assert S.settle_drafts({}, {}, has_content_changed) == ([], [])


@pytest.mark.parametrize("subject, body, is_an_edit", [("Hello", "Body\n", False), ("Hello ", "Body", False), (" Hello", "Body", False), ("Hello", "Body\n\n\n", False), ("hello", "Body", True), ("Hello", "Bo dy", True)])
def test_trailing_or_leading_spaces_and_blank_lines_are_not_an_edit_but_a_changed_letter_or_word_is(subject, body, is_an_edit):
    """The rule the Save button has always used (the saved text is trimmed), checked here against fixed expectations rather than against itself."""
    drafts = {}
    S.record_draft(drafts, "intro", "A", ORIG, subject, body, has_content_changed(ORIG, subject, body))
    pending, _ = S.settle_drafts(drafts, {("intro", "A"): ORIG}, has_content_changed)
    assert bool(pending) is is_an_edit


# ================================================================ regression: internal columns are never offered

def test_the_row_number_column_the_sheet_connector_adds_is_never_offered_as_a_placeholder():
    """REGRESSION: the real connector puts `_row` on every lead, and `{{_row}}` was being offered next to the real placeholders."""
    tokens = S.placeholder_tokens([{"_row": 5, "_internal": "x", "Client": "DubeRobe", "_": "y"}], SYSTEM)
    assert tokens == ["{{FirstName}}", "{{LastName}}", "{{CompanyName}}", "{{Email}}", "{{Client}}"]


# ================================================================ the add forms: tab labels, progress, the wait



@pytest.mark.parametrize("name, ready, label", [("Variant A", True, "Variant A ✓"), ("Variant B", False, "Variant B ○"), ("Follow-up 3", True, "Follow-up 3 ✓"), ("", False, " ○")])
def test_a_tab_shows_a_tick_when_ready_and_an_open_circle_when_not(name, ready, label):
    assert S.tab_label(name, ready) == label


@pytest.mark.parametrize("names, text", [([], ""), (["B"], "B"), (["B", "C"], "B and C"), (["B", "C", "D"], "B, C and D"), (["Intro", "Follow-up 1", "Follow-up 2"], "Intro, Follow-up 1 and Follow-up 2")])
def test_names_are_joined_the_way_a_person_would_write_them(names, text):
    assert S.join_names(names) == text


@pytest.mark.parametrize("ready, names, plural, adding, text", [
    ([True, False, False, False], list("ABCD"), "variants", "this stage", "1 of 4 variants ready · fill B, C and D to add this stage"),
    ([False] * 4, list("ABCD"), "variants", "this stage", "0 of 4 variants ready · fill A, B, C and D to add this stage"),
    ([True, True, True, False], list("ABCD"), "variants", "this stage", "3 of 4 variants ready · fill D to add this stage"),
    ([True] * 4, list("ABCD"), "variants", "this stage", "All 4 variants ready"),
    ([True, False], ["Intro", "Follow-up 1"], "stages", "Variant C", "1 of 2 stages ready · fill Follow-up 1 to add Variant C"),
    ([True] * 5, list("abcde"), "stages", "Variant C", "All 5 stages ready"),
])
def test_the_progress_line_says_how_far_along_it_is_and_what_is_missing(ready, names, plural, adding, text):
    assert S.progress_text(ready, names, plural, adding) == text


@pytest.mark.parametrize("wait, text", [(2, "sends 2 days after Follow-up 9"), (1, "sends 1 day after Follow-up 9"), (0, "sends the same day as Follow-up 9"), (90, "sends 90 days after Follow-up 9"),
                                        (None, ""), (-1, ""), (True, ""), (2.5, ""), ("2", "")])
def test_the_wait_sentence_is_only_ever_said_when_the_wait_is_known(wait, text):
    assert S.wait_sentence("Follow-up 9", wait) == text


@pytest.mark.parametrize("wait, badge", [(2, "+2 days"), (1, "+1 day"), (0, "Same day"), (None, ""), (-3, ""), (True, ""), ("x", "")])
def test_the_new_stage_card_badge_uses_the_same_wording_as_the_other_cards(wait, badge):
    assert S.new_stage_badge(wait) == badge
