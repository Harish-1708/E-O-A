import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from conversation_logic import (
    build_outgoing_messages_for_lead, build_incoming_messages_for_responses,
    filter_responses_for_lead, build_conversation_thread,
)

from conftest import FIXTURE_CAMPAIGN

_CAMPAIGN_CFG = {"_campaign_name": FIXTURE_CAMPAIGN}


def _lead(**overrides):
    lead = {"LeadID": "1", "FirstName": "Sam", "LastName": "Smith", "Email": "sam@abc.com",
            "CompanyName": "Acme"}
    lead.update(overrides)
    return lead


# ---------- build_outgoing_messages_for_lead ----------

def test_build_outgoing_messages_includes_sent_stage(fixture_repo):
    lead = _lead(IntroSentAt="2026-08-01 09:00:00", IntroVariant="A")
    messages = build_outgoing_messages_for_lead(_CAMPAIGN_CFG, lead)
    assert len(messages) == 1
    assert messages[0]["direction"] == "outgoing"
    assert messages[0]["timestamp"] == "2026-08-01 09:00:00"
    assert "Sam" in messages[0]["body"]  # template variable actually rendered


def test_build_outgoing_messages_includes_multiple_sent_stages(fixture_repo):
    # Followup templates have a blank Subject (continue the thread), so a
    # lead whose intro has already gone out carries the ThreadSubject that
    # intro established — same as a real lead in the Master Sheet.
    lead = _lead(IntroSentAt="2026-08-01 09:00:00", IntroVariant="A",
                 FollowUp1SentAt="2026-08-05 09:00:00", FollowUp1Variant="A",
                 ThreadSubject="Sample intro subject A")
    messages = build_outgoing_messages_for_lead(_CAMPAIGN_CFG, lead)
    assert len(messages) == 2
    assert messages[0]["timestamp"] == "2026-08-01 09:00:00"
    assert messages[1]["timestamp"] == "2026-08-05 09:00:00"


def test_build_outgoing_messages_skips_unsent_stages(fixture_repo):
    lead = _lead(IntroSentAt="2026-08-01 09:00:00", IntroVariant="A")  # no FollowUp1SentAt
    messages = build_outgoing_messages_for_lead(_CAMPAIGN_CFG, lead)
    assert len(messages) == 1


def test_build_outgoing_messages_no_stages_sent_returns_empty(fixture_repo):
    lead = _lead()
    assert build_outgoing_messages_for_lead(_CAMPAIGN_CFG, lead) == []


def test_build_outgoing_messages_skips_stage_with_sent_at_but_no_variant(fixture_repo):
    """Defensive — a data inconsistency (SentAt set, Variant blank)
    shouldn't crash the whole conversation view, just skip that stage."""
    lead = _lead(IntroSentAt="2026-08-01 09:00:00", IntroVariant="")
    assert build_outgoing_messages_for_lead(_CAMPAIGN_CFG, lead) == []


def test_build_outgoing_messages_renders_actual_template_variables(fixture_repo):
    lead = _lead(IntroSentAt="2026-08-01 09:00:00", IntroVariant="A", FirstName="Rocky")
    messages = build_outgoing_messages_for_lead(_CAMPAIGN_CFG, lead)
    assert "Rocky" in messages[0]["body"]
    assert "{{FirstName}}" not in messages[0]["body"]


def test_build_outgoing_messages_skips_stage_that_fails_to_render(fixture_repo):
    """A nonexistent campaign (no template files at all) must not crash —
    every stage simply fails to render and gets skipped."""
    lead = _lead(IntroSentAt="2026-08-01 09:00:00", IntroVariant="A")
    fake_campaign_cfg = {"_campaign_name": "NonexistentCampaignXYZ"}
    assert build_outgoing_messages_for_lead(fake_campaign_cfg, lead) == []


# ---------- build_incoming_messages_for_responses ----------

def test_build_incoming_messages_uses_full_body_when_available(fixture_repo):
    responses = [{"ReceivedAt": "2026-08-02 10:00:00", "Subject": "Re: Hi", "From": "sam@abc.com",
                  "Snippet": "short preview", "FullBody": "the complete original message, much longer"}]
    messages = build_incoming_messages_for_responses(responses)
    assert messages[0]["body"] == "the complete original message, much longer"


def test_build_incoming_messages_falls_back_to_snippet_when_full_body_missing(fixture_repo):
    """Responses logged before FullBody existed as a column — must still
    show something, just the shorter historical Snippet."""
    responses = [{"ReceivedAt": "2026-08-02 10:00:00", "Subject": "Re: Hi", "From": "sam@abc.com",
                  "Snippet": "short preview", "FullBody": ""}]
    messages = build_incoming_messages_for_responses(responses)
    assert messages[0]["body"] == "short preview"


def test_build_incoming_messages_empty_list(fixture_repo):
    assert build_incoming_messages_for_responses([]) == []


def test_build_incoming_messages_preserves_from_and_subject(fixture_repo):
    responses = [{"ReceivedAt": "2026-08-02 10:00:00", "Subject": "Re: Pricing", "From": "sam@abc.com",
                  "Snippet": "x", "FullBody": "x"}]
    messages = build_incoming_messages_for_responses(responses)
    assert messages[0]["from"] == "sam@abc.com"
    assert messages[0]["subject"] == "Re: Pricing"


# ---------- filter_responses_for_lead ----------

def test_filter_responses_for_lead_matches_by_lead_id(fixture_repo):
    responses = [{"LeadID": "1"}, {"LeadID": "2"}, {"LeadID": "1"}]
    filtered = filter_responses_for_lead(responses, "1")
    assert len(filtered) == 2


def test_filter_responses_for_lead_handles_str_int_mismatch(fixture_repo):
    responses = [{"LeadID": 1}, {"LeadID": 2}]  # int, not str
    filtered = filter_responses_for_lead(responses, "1")
    assert len(filtered) == 1


def test_filter_responses_for_lead_no_match_returns_empty(fixture_repo):
    responses = [{"LeadID": "1"}]
    assert filter_responses_for_lead(responses, "999") == []


def test_filter_responses_for_lead_empty_list(fixture_repo):
    assert filter_responses_for_lead([], "1") == []


# ---------- build_conversation_thread ----------

def test_build_conversation_thread_merges_outgoing_and_incoming_chronologically(fixture_repo):
    lead = _lead(IntroSentAt="2026-08-01 09:00:00", IntroVariant="A",
                 FollowUp1SentAt="2026-08-10 09:00:00", FollowUp1Variant="A",
                 ThreadSubject="Sample intro subject A")  # followup continues the intro thread
    responses = [{"LeadID": "1", "ReceivedAt": "2026-08-05 10:00:00", "Subject": "Re: Hi",
                  "From": "sam@abc.com", "Snippet": "interested", "FullBody": "I'm interested, tell me more"}]
    thread = build_conversation_thread(_CAMPAIGN_CFG, lead, responses)

    assert len(thread) == 3
    assert thread[0]["direction"] == "outgoing"  # Aug 1
    assert thread[1]["direction"] == "incoming"  # Aug 5 — reply came between the two outgoing stages
    assert thread[2]["direction"] == "outgoing"  # Aug 10


def test_build_conversation_thread_no_activity_returns_empty(fixture_repo):
    lead = _lead()
    assert build_conversation_thread(_CAMPAIGN_CFG, lead, []) == []


def test_build_conversation_thread_only_outgoing_no_replies_yet(fixture_repo):
    lead = _lead(IntroSentAt="2026-08-01 09:00:00", IntroVariant="A")
    thread = build_conversation_thread(_CAMPAIGN_CFG, lead, [])
    assert len(thread) == 1
    assert thread[0]["direction"] == "outgoing"


def test_build_conversation_thread_only_incoming_no_outgoing_recorded(fixture_repo):
    """Edge case — a reply logged for a lead whose own SentAt/Variant
    somehow isn't populated (e.g. imported mid-sequence) still shows the
    reply, just without a matching outgoing entry."""
    lead = _lead()
    responses = [{"LeadID": "1", "ReceivedAt": "2026-08-05 10:00:00", "Subject": "Re: Hi",
                  "From": "sam@abc.com", "Snippet": "x", "FullBody": "x"}]
    thread = build_conversation_thread(_CAMPAIGN_CFG, lead, responses)
    assert len(thread) == 1
    assert thread[0]["direction"] == "incoming"


def test_a_body_that_is_only_a_line_break_does_not_hide_the_snippet():
    """A stray newline is 'present' but shows nothing; the snippet beside it must win."""
    from conversation_logic import build_incoming_messages_for_responses
    (message,) = build_incoming_messages_for_responses(
        [{"FullBody": "\n", "Snippet": "the short version", "ReceivedAt": "2026-10-01 10:00:00", "From": "a@x.com"}])
    assert message["body"] == "the short version"


def test_a_real_body_still_wins_over_the_snippet_and_two_blank_ones_give_an_empty_body():
    from conversation_logic import build_incoming_messages_for_responses
    full, none = build_incoming_messages_for_responses([
        {"FullBody": "the whole message", "Snippet": "short", "ReceivedAt": "1", "From": ""},
        {"FullBody": "  \n", "Snippet": " ", "ReceivedAt": "2", "From": ""}])
    assert full["body"] == "the whole message" and none["body"] == ""


# ================================================================ emails sent by hand / from the control panel

def _sent(mid, at, lead="1", subject="Re: Hi", body="we can do $200", source="mailbox"):
    return {"MessageID": mid, "LeadID": lead, "SentAt": at, "Subject": subject, "Body": body, "Source": source}


def test_sent_mail_rows_become_outgoing_messages_with_their_real_text():
    from conversation_logic import build_outgoing_messages_from_sent_mail
    (message,) = build_outgoing_messages_from_sent_mail([_sent("<m@x>", "2026-09-04 16:52:30", subject="Re: Deal", body="We can do $200.")])
    assert message == {"direction": "outgoing", "timestamp": "2026-09-04 16:52:30", "subject": "Re: Deal",
                       "body": "We can do $200.", "source": "mailbox"}


def test_only_the_leads_own_sent_mail_is_used():
    from conversation_logic import filter_sent_mail_for_lead
    rows = [_sent("<a@x>", "t", lead="1"), _sent("<b@x>", "t", lead="2"), _sent("<c@x>", "t", lead=1)]
    assert [r["MessageID"] for r in filter_sent_mail_for_lead(rows, "1")] == ["<a@x>", "<c@x>"]      # a number from the Sheet matches too


def test_a_reply_is_placed_by_when_it_was_really_sent_not_when_it_was_logged():
    """REGRESSION in waiting: ReceivedAt is when the system LOGGED a reply, up to an hour or more after
    she sent it. Once YOUR answers appear in the thread, ordering by that would put your answer BEFORE her question."""
    from conversation_logic import build_conversation_thread
    inbound = [{"ReceivedAt": "2026-09-04 17:27:14", "MessageDate": "2026-09-04 17:25:10", "FullBody": "Sounds great", "From": "a@x.com", "Subject": "Re: x"}]
    mine = [_sent("<m@x>", "2026-09-04 17:26:00", body="I have sent the request")]
    thread = build_conversation_thread({"_campaign_name": "Camp"}, {"LeadID": "1"}, inbound, mine)
    assert [m["body"] for m in thread] == ["Sounds great", "I have sent the request"]               # she wrote at 17:25, you answered at 17:26


def test_without_a_real_sent_time_a_reply_falls_back_to_when_it_was_logged():
    from conversation_logic import build_incoming_messages_for_responses
    (a, b) = build_incoming_messages_for_responses([
        {"ReceivedAt": "2026-09-04 17:00:00", "MessageDate": "", "FullBody": "old row", "From": ""},
        {"ReceivedAt": "2026-09-04 18:00:00", "MessageDate": "  ", "FullBody": "blank date", "From": ""}])
    assert (a["timestamp"], b["timestamp"]) == ("2026-09-04 17:00:00", "2026-09-04 18:00:00")


def test_the_whole_conversation_is_in_true_order_across_all_three_sources():
    from conversation_logic import build_conversation_thread
    lead = {"LeadID": "1", "IntroSentAt": "2026-09-04 14:36:00", "IntroVariant": "A", "FirstName": "Ann"}
    inbound = [{"ReceivedAt": "2026-09-04 16:52:02", "MessageDate": "2026-09-04 16:48:00", "FullBody": "her first", "From": "", "Subject": ""},
               {"ReceivedAt": "2026-09-04 17:03:32", "MessageDate": "2026-09-04 17:00:00", "FullBody": "her second", "From": "", "Subject": ""}]
    mine = [_sent("<1@x>", "2026-09-04 16:52:00", body="my offer"), _sent("<2@x>", "2026-09-04 17:16:00", body="my agreement")]
    bodies = [m["body"] for m in build_conversation_thread({"_campaign_name": "NoSuchTemplates"}, lead, inbound, mine)]
    assert bodies == ["her first", "my offer", "her second", "my agreement"]       # the unrendered intro is skipped, as before


def test_a_thread_built_the_old_way_without_sent_mail_is_unchanged():
    from conversation_logic import build_conversation_thread
    inbound = [{"ReceivedAt": "2026-09-04 17:00:00", "FullBody": "hi", "From": "", "Subject": ""}]
    assert [m["body"] for m in build_conversation_thread({"_campaign_name": "C"}, {"LeadID": "1"}, inbound)] == ["hi"]
    assert [m["body"] for m in build_conversation_thread({"_campaign_name": "C"}, {"LeadID": "1"}, inbound, None)] == ["hi"]
    assert [m["body"] for m in build_conversation_thread({"_campaign_name": "C"}, {"LeadID": "1"}, inbound, [])] == ["hi"]
