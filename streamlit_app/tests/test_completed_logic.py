"""'Completed': a lead sent the LAST email of the sequence, with nothing else having decided its outcome. One shared rule feeds the Data tab (pill, chips, timeline) and
Performance (the 'Where your leads are now' card), and it must agree with Home's counts. A completed lead that later replies is no longer completed."""
import itertools
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import leads_logic as L  # noqa: E402
import outreach  # noqa: E402
import performance_logic as PL  # noqa: E402
import work_queue_logic as WQ  # noqa: E402
from data_import_logic import FILTER_OPTIONS, filter_leads  # noqa: E402

SENT = "2026-10-05 10:00:00"


def lead(sent=1, **kw):
    """A contacted lead that has been sent the first `sent` emails."""
    base = {"Email": "ann@x.com", "FirstName": "Ann", "IntroSentAt": "2026-10-01 09:00:00"}
    for index in range(1, sent):
        base[L.sent_column(index)] = f"2026-10-0{min(index + 1, 9)} 09:00:00"
    base.update(kw)
    return base


def stages(count):
    return [{"name": f"stage{i}"} for i in range(count)]


# ================================================================ the rule

def test_a_lead_sent_its_last_email_is_completed_and_one_not_yet_sent_it_is_not():
    assert L.sequence_completed(lead(sent=3), 3) is True
    assert L.sequence_completed(lead(sent=2), 3) is False and L.sequence_completed(lead(sent=1), 3) is False


def test_the_last_stage_is_judged_by_the_campaigns_own_stage_count_so_an_eleven_stage_campaign_needs_all_eleven():
    full = lead(sent=11)
    assert full.get("FollowUp10SentAt") and L.sequence_completed(full, 11) is True
    almost = lead(sent=10)
    assert "FollowUp10SentAt" not in almost and L.sequence_completed(almost, 11) is False and L.sequence_completed(almost, 10) is True


@pytest.mark.parametrize("status", ["Removed", "Paused", "Stopped - Replied", "Stopped - Bounced", "Stopped - Rejected", "Stopped - Manual", "Stopped - Unsubscribed", "Stopped - Something New"])
def test_a_decided_outcome_always_wins_over_having_finished_the_sequence(status):
    assert L.sequence_completed(lead(sent=3, Status=status), 3) is False


def test_a_recorded_reply_wins_even_when_the_status_has_not_caught_up_yet():
    assert L.sequence_completed(lead(sent=3, ReplyStatus="Replied"), 3) is False
    assert L.sequence_completed(lead(sent=3, ReplyStatus="Replied", Status="Stopped - Replied"), 3) is False


def test_a_lead_explicitly_marked_completed_is_completed_even_if_its_last_date_is_blank():
    assert L.sequence_completed(lead(sent=1, Status="Completed"), 3) is True
    assert L.sequence_completed(lead(sent=3, Status="Completed", ReplyStatus="Replied"), 3) is False


@pytest.mark.parametrize("changes, count", [({"Email": ""}, 3), ({"Email": "   "}, 3), ({}, 0), ({}, -1), ({"FollowUp2SentAt": "  "}, 3)])
def test_no_email_no_stages_or_a_blank_last_date_is_never_completed(changes, count):
    assert L.sequence_completed({**lead(sent=3), **changes}, count) is False


def test_the_statuses_this_pure_module_lists_stay_in_step_with_the_engines_terminal_statuses():
    for status in outreach.TERMINAL_STATUSES:
        expected = status == outreach.STATUS_COMPLETED
        assert L.sequence_completed(lead(sent=3, Status=status), 3) is expected, status


@pytest.mark.parametrize("status, reply, last_sent, email", list(itertools.product(sorted(outreach.TERMINAL_STATUSES) + [""], ["", "Replied"], [True, False], ["ann@x.com", ""])))
def test_it_agrees_with_homes_own_decision_for_every_combination(status, reply, last_sent, email):
    candidate = lead(sent=3 if last_sent else 2, Status=status, ReplyStatus=reply, Email=email)
    assert L.sequence_completed(candidate, 3) == (WQ.not_in_play_label(candidate, stages(3)) == WQ.LABEL_COMPLETED)


# ================================================================ a completed lead that later replies

def test_a_completed_lead_that_later_replies_stops_being_completed_everywhere_at_once():
    finished = lead(sent=3)
    assert L.status_summary(finished, 3) == ("Completed", L.KIND_NEUTRAL) and PL.lead_outcomes([finished], stages(3))[PL.OUTCOME_COMPLETED] == 1
    finished.update({"ReplyStatus": "Replied", "Status": "Stopped - Replied"})                       # what the reply check writes
    assert L.status_summary(finished, 3)[0] == "Replied" and L.sequence_completed(finished, 3) is False
    outcomes = PL.lead_outcomes([finished], stages(3))
    assert outcomes[PL.OUTCOME_COMPLETED] == 0 and outcomes[WQ.LABEL_REPLIED] == 1
    assert filter_leads([finished], "Completed", 3) == [] and filter_leads([finished], "Replied", 3) == [finished]


def test_the_same_goes_for_a_bounce_a_stop_and_a_removal_after_completing():
    for status, label in (("Stopped - Bounced", "Bounced"), ("Stopped - Manual", "Stopped · Manual"), ("Removed", "Removed"), ("Paused", "Paused")):
        finished = lead(sent=3)
        assert L.status_summary(finished, 3)[0] == "Completed"
        finished["Status"] = status
        assert L.status_summary(finished, 3)[0] == label


# ================================================================ the status pill

def test_the_pill_says_completed_only_when_it_knows_the_stage_count_and_otherwise_is_unchanged():
    finished = lead(sent=3)
    assert L.status_summary(finished, 3) == ("Completed", L.KIND_NEUTRAL)
    assert L.status_summary(finished) == ("In sequence", L.KIND_ACTIVE)                                # no count given: exactly as before
    assert L.status_summary(lead(sent=2), 3) == ("In sequence", L.KIND_ACTIVE) and L.status_summary({"Email": "a@x.com"}, 3) == ("Not contacted", L.KIND_WAITING)


def test_every_other_pill_is_unchanged_by_the_stage_count():
    for changes in ({"Status": "Removed"}, {"Status": "Stopped - Replied"}, {"Status": "Stopped - Bounced"}, {"Status": "Paused"}, {"Status": "Stopped - Unsubscribed"}, {"ReplyStatus": "Replied"}, {}):
        for sent in (1, 2, 3):
            candidate = lead(sent=sent, **changes)
            if not (sent == 3 and not changes):
                assert L.status_summary(candidate, 3) == L.status_summary(candidate), (changes, sent)


def test_a_row_carries_the_completed_pill_and_the_progress_strip_still_shows_every_stage_sent():
    row = L.lead_row(lead(sent=3), 3)
    assert row["status"] == "Completed" and row["status_kind"] == L.KIND_NEUTRAL and row["progress"] == ["sent", "sent", "sent"]
    replied = L.lead_row(lead(sent=3, ReplyStatus="Replied", Status="Stopped - Replied"), 3)
    assert replied["status"] == "Replied" and replied["progress"] == ["sent", "sent", "replied"]


# ================================================================ the timeline

def test_a_completed_leads_timeline_ends_with_sequence_completed_dated_at_its_last_email_and_has_no_next_follow_up():
    finished = lead(sent=3, NextEligibleAt="2026-10-20 09:00:00")                                      # a stale due date must not show
    events = L.timeline(finished, ["Intro", "Follow-up 1", "Follow-up 2"])
    assert [e["text"] for e in events][-1] == "Sequence completed" and "Next follow-up due" not in [e["text"] for e in events]
    assert events[-1]["icon"] == "✅" and events[-1]["when"] == L.short_date(finished["FollowUp2SentAt"])


def test_a_lead_still_in_the_sequence_keeps_its_next_follow_up_and_has_no_completed_event():
    texts = [e["text"] for e in L.timeline(lead(sent=2, NextEligibleAt="2026-10-20 09:00:00"), ["Intro", "Follow-up 1", "Follow-up 2"])]
    assert "Next follow-up due" in texts and "Sequence completed" not in texts


def test_a_completed_lead_that_replied_shows_the_reply_and_not_a_completed_event():
    texts = [e["text"] for e in L.timeline(lead(sent=3, ReplyStatus="Replied", Status="Stopped - Replied", ReplyAt="2026-10-09 09:00:00"), ["Intro", "Follow-up 1", "Follow-up 2"])]
    assert "Replied" in texts and "Sequence completed" not in texts


def test_an_explicitly_completed_lead_still_gets_exactly_one_completed_event():
    texts = [e["text"] for e in L.timeline(lead(sent=3, Status="Completed", LastActionAt="2026-10-06 09:00:00"), ["Intro", "Follow-up 1", "Follow-up 2"])]
    assert texts.count("Sequence completed") == 1


# ================================================================ the Data tab's chips

def campaign_leads():
    return [lead(sent=3), lead(sent=3, Email="b@x.com"), lead(sent=2, Email="c@x.com"), lead(sent=1, Email="d@x.com"),
            lead(sent=3, Email="e@x.com", Status="Stopped - Replied", ReplyStatus="Replied"), lead(sent=3, Email="f@x.com", Status="Stopped - Bounced"),
            lead(sent=3, Email="g@x.com", Status="Removed"), {"Email": "h@x.com"}]


def test_the_completed_chip_lists_exactly_the_finished_leads_and_in_progress_no_longer_includes_them():
    leads = campaign_leads()
    assert [l["Email"] for l in filter_leads(leads, "Completed", 3)] == ["ann@x.com", "b@x.com"]
    assert [l["Email"] for l in filter_leads(leads, "In Progress", 3)] == ["c@x.com", "d@x.com"]


def test_the_chips_give_each_lead_one_place_so_they_add_up_without_double_counting():
    leads = campaign_leads()
    counts = L.filter_counts(leads, FILTER_OPTIONS, lambda subset, option: filter_leads(subset, option, 3))
    assert counts == {"All": 8, "In Progress": 2, "Completed": 2, "Replied": 1, "Bounced": 1, "Removed": 1}
    assert counts["In Progress"] + counts["Completed"] + counts["Replied"] + counts["Bounced"] + counts["Removed"] + 1 == counts["All"]       # +1: never contacted


def test_without_a_stage_count_the_chips_are_exactly_what_they_always_were():
    leads = campaign_leads()
    assert [l["Email"] for l in filter_leads(leads, "In Progress")] == ["ann@x.com", "b@x.com", "c@x.com", "d@x.com"] and filter_leads(leads, "Completed") == []
    assert filter_leads(leads + [lead(sent=1, Status="Completed", Email="z@x.com")], "Completed")[0]["Email"] == "z@x.com"


def test_an_unknown_chip_still_falls_back_to_everything_and_the_completed_chip_is_in_the_options():
    assert "Completed" in FILTER_OPTIONS and FILTER_OPTIONS.index("Completed") == FILTER_OPTIONS.index("In Progress") + 1
    assert filter_leads(campaign_leads(), "Something Old", 3) == campaign_leads()


# ================================================================ Performance: where every lead is now

def test_every_lead_is_in_exactly_one_place_so_the_places_add_up_to_the_leads():
    leads = campaign_leads()
    outcomes = PL.lead_outcomes(leads, stages(3))
    assert sum(outcomes.values()) == len(leads) and list(outcomes) == PL.OUTCOME_ORDER
    assert outcomes == {"Not contacted yet": 1, "In progress": 2, "Completed the sequence": 2, "Replied": 1, "Bounced": 1, "Unsubscribed": 0, "Declined": 0, "Stopped by hand": 0,
                        "Paused": 0, "Removed": 1, "No email address": 0}


def test_the_outcomes_agree_with_the_data_tabs_chips_for_the_same_leads():
    leads = campaign_leads()
    outcomes = PL.lead_outcomes(leads, stages(3))
    assert outcomes[PL.OUTCOME_COMPLETED] == len(filter_leads(leads, "Completed", 3)) and outcomes[PL.OUTCOME_IN_PROGRESS] == len(filter_leads(leads, "In Progress", 3))


def test_a_lead_with_no_email_and_each_stop_reason_has_its_own_place():
    leads = [{"Status": "Stopped - Unsubscribed", "Email": "a@x.com"}, {"Status": "Stopped - Rejected", "Email": "b@x.com"}, {"Status": "Stopped - Manual", "Email": "c@x.com"},
             {"Status": "Paused", "Email": "d@x.com"}, {"Email": ""}]
    outcomes = PL.lead_outcomes(leads, stages(3))
    assert (outcomes["Unsubscribed"], outcomes["Declined"], outcomes["Stopped by hand"], outcomes["Paused"], outcomes["No email address"]) == (1, 1, 1, 1, 1)


def test_no_leads_and_no_stages_never_fail():
    assert sum(PL.lead_outcomes([], stages(3)).values()) == 0 and sum(PL.lead_outcomes([lead(sent=3)], []).values()) == 1


def test_the_engines_sequence_completion_figure_is_unchanged_and_counts_a_lead_that_replied_after_the_last_email():
    """The 'Sequence completion' headline equals the engine's and the Sheet's Dashboard, which counts everyone sent the last email. The new 'Completed' place excludes
    those who replied, so the two figures are different on purpose and the page says what each one means."""
    leads = campaign_leads()
    last_field = outreach.stage_field_names(2)["sent_at"]
    reached = sum(1 for l in leads if (l.get(last_field) or "").strip())
    assert reached == 5 and PL.lead_outcomes(leads, stages(3))[PL.OUTCOME_COMPLETED] == 2



def test_homes_queue_also_lets_a_reply_win_over_an_explicit_completed_status():
    assert WQ.not_in_play_label(lead(sent=3, Status="Completed", ReplyStatus="Replied"), stages(3)) == WQ.LABEL_REPLIED
    assert WQ.not_in_play_label(lead(sent=3, Status="Completed"), stages(3)) == WQ.LABEL_COMPLETED
    assert WQ.not_in_play_label(lead(sent=3, Status="Stopped - Bounced", ReplyStatus="Replied"), stages(3)) == WQ.LABEL_BOUNCED          # every other status behaves exactly as before


def campaign_perf(leads, stage_count=3):
    cfg = {"_campaign_name": "C", "stages": [{"name": "intro" if i == 0 else f"followup{i}"} for i in range(stage_count)], "variants": ["A"]}
    return PL.build_campaign_performance(cfg, leads, [], [])


def test_a_campaigns_figures_carry_where_each_of_its_leads_is_and_they_add_up_to_its_leads():
    leads = campaign_leads()
    perf = campaign_perf(leads)
    assert perf["outcomes"] == PL.lead_outcomes(leads, stages(3)) and sum(perf["outcomes"].values()) == perf["total_leads"] == len(leads)
    assert perf["outcomes"][PL.OUTCOME_COMPLETED] == 2 and perf["outcomes"][WQ.LABEL_REPLIED] == 1


def test_the_engines_funnel_figure_and_the_new_completed_place_differ_on_purpose():
    perf = campaign_perf(campaign_leads())
    assert perf["completed"] == 5 and perf["outcomes"][PL.OUTCOME_COMPLETED] == 2                       # 5 were sent the last email; 2 of them are still just completed
    assert perf["sequence_completion"] == PL.rate(5, perf["contacted"])


def test_combining_campaigns_adds_up_each_place_with_the_real_function():
    first = campaign_perf([lead(sent=3), lead(sent=3, Email="b@x.com"), lead(sent=1, Email="c@x.com")])
    second = campaign_perf([lead(sent=3, Email="d@x.com", Status="Stopped - Replied", ReplyStatus="Replied"), lead(sent=2, Email="e@x.com"), {"Email": "f@x.com"}])
    combined = PL.combine_performance([first, second])
    for label in PL.OUTCOME_ORDER:
        assert combined["outcomes"][label] == first["outcomes"][label] + second["outcomes"][label], label
    assert combined["outcomes"][PL.OUTCOME_COMPLETED] == 2 and combined["outcomes"][WQ.LABEL_REPLIED] == 1 and combined["outcomes"][PL.OUTCOME_IN_PROGRESS] == 2
    assert sum(combined["outcomes"].values()) == combined["total_leads"] == 6


def test_combining_with_a_campaign_that_has_no_outcomes_never_fails():
    combined = PL.combine_performance([campaign_perf([lead(sent=3)]), {**campaign_perf([lead(sent=3)]), "outcomes": None}])
    assert combined["outcomes"][PL.OUTCOME_COMPLETED] == 1 and list(combined["outcomes"]) == PL.OUTCOME_ORDER


def test_the_longest_sequence_of_eleven_dots_fits_the_sequence_column_in_a_normal_window():
    """The progress strip needs 11 dots and the gaps between them; the column is 1.5 of 9.15 shares of the table's width less its padding and gaps. This holds at 900px."""
    import re
    import leads_html as LH
    dot = int(re.search(r"\.ld-dot \{ width: (\d+)px", LH.LEADS_CSS).group(1))
    gap = int(re.search(r"\.ld-dots \{[^}]*?gap: (\d+)px", LH.LEADS_CSS).group(1))
    needed = 11 * dot + 10 * gap
    column = (900 - 32 - 5 * 14) / 9.15 * 1.5                              # a 900px-wide table: padding 2x16, five 14px gaps, 1.5 of 9.15 shares
    assert needed <= column, (needed, column)
