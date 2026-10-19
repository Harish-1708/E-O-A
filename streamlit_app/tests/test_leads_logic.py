"""The Data tab's lead list and panel, worked out from a lead's own Master Sheet columns."""
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))        # the repo root, for outreach

import leads_logic as L  # noqa: E402
import outreach  # noqa: E402

TODAY = date(2026, 10, 13)


def lead(**kw):
    return {"LeadID": "7", "FirstName": "Maya", "LastName": "Okafor", "Email": "maya@example.com", "Company": "BrightLeaf", **kw}


# ================================================================ stage columns and labels

@pytest.mark.parametrize("index", range(11))
def test_the_sent_column_for_each_stage_is_the_one_the_engine_uses(index):
    assert L.sent_column(index) == outreach.stage_field_names(index)["sent_at"]


def test_every_stage_column_exists_in_the_master_sheet():
    assert all(L.sent_column(i) in outreach.MASTER_COLUMNS for i in range(L.MAX_STAGES))


@pytest.mark.parametrize("name, expected", [
    ("intro", "Intro"), ("Intro", "Intro"), ("followup1", "Follow-up 1"), ("followup10", "Follow-up 10"), ("FollowUp2", "Follow-up 2"), ("follow_up_3", "Follow-up 3"),
    ("follow-up 4", "Follow-up 4"), ("followup01", "Follow-up 1"), ("last_chance", "Last chance"), ("nudge", "Nudge"), ("", ""), (None, ""), ("  intro  ", "Intro"), ("followup", "Followup"),
])
def test_template_style_stage_names_are_made_readable(name, expected):
    assert L.humanize_stage(name) == expected


@pytest.mark.parametrize("names, count, expected", [
    (None, 3, ["Intro", "Follow-up 1", "Follow-up 2"]), ([], 2, ["Intro", "Follow-up 1"]), (["Hello", "Nudge", "Last"], 3, ["Hello", "Nudge", "Last"]),
    (["intro", "followup1", "followup2"], 3, ["Intro", "Follow-up 1", "Follow-up 2"]),
    (["Hello", "", "  "], 3, ["Hello", "Follow-up 1", "Follow-up 2"]), (["Only"], 3, ["Only", "Follow-up 1", "Follow-up 2"]), (None, 0, []),
])
def test_stage_labels_use_the_campaigns_names_and_fall_back_to_plain_ones(names, count, expected):
    assert L.stage_labels(names, count) == expected


def test_the_stage_count_is_the_campaigns_own_when_known_and_otherwise_what_has_actually_been_sent():
    assert L.stage_count(["a", "b", "c"], []) == 3 and L.stage_count(["a"] * 30, []) == L.MAX_STAGES
    leads = [{"IntroSentAt": "2026-10-01"}, {"IntroSentAt": "2026-10-01", "FollowUp2SentAt": "2026-10-09"}, {}]
    assert L.stage_count(None, leads) == 3 and L.stage_count([], leads) == 3
    assert L.stage_count(None, []) == 1 and L.stage_count(None, [{}]) == 1


# ================================================================ identity and names

def test_each_lead_gets_its_own_stable_key_even_when_ids_repeat_or_are_missing():
    leads = [{"LeadID": "5"}, {"LeadID": 5}, {"LeadID": "5"}, {"LeadID": "", "Email": "a@x.com"}, {"Email": "a@x.com"}, {}, {}]
    assert L.lead_keys(leads) == ["5", "5#2", "5#3", "a@x.com", "a@x.com#2", "lead", "lead#2"]
    assert len(set(L.lead_keys(leads))) == len(leads)


@pytest.mark.parametrize("lead_, expected", [
    ({"FirstName": "Maya", "LastName": "Okafor", "Email": "m@x.com"}, "Maya Okafor"), ({"FirstName": "Maya", "Email": "m@x.com"}, "Maya"),
    ({"LastName": "Okafor"}, "Okafor"), ({"FirstName": " ", "Email": "kari.b@x.com"}, "kari.b"), ({"Email": ""}, "(no name)"), ({}, "(no name)"),
])
def test_the_display_name(lead_, expected):
    assert L.display_name(lead_) == expected


# ================================================================ status

@pytest.mark.parametrize("fields, label, kind", [
    ({"Status": "Removed"}, "Removed", "bad"), ({"Status": "Stopped - Replied"}, "Replied", "good"), ({"Status": "Stopped - Bounced"}, "Bounced", "bad"),
    ({"Status": "Stopped - Manual"}, "Stopped · Manual", "neutral"), ({"Status": "Stopped - Rejected"}, "Stopped · Rejected", "neutral"),
    ({"Status": "Stopped"}, "Stopped", "neutral"), ({"Status": "Paused", "IntroSentAt": "2026-10-01"}, "Paused", "waiting"),
    ({"Status": "Completed", "IntroSentAt": "2026-10-01"}, "Completed", "neutral"),
    ({"Status": "Intro Sent", "IntroSentAt": "2026-10-01"}, "In sequence", "active"), ({"IntroSentAt": "2026-10-01"}, "In sequence", "active"),
    ({"ReplyStatus": "Replied", "IntroSentAt": "2026-10-01"}, "Replied", "good"), ({"ReplyStatus": "Replied"}, "Replied", "good"),
    ({}, "Not contacted", "waiting"), ({"Status": ""}, "Not contacted", "waiting"), ({"Status": "Something new"}, "Not contacted", "waiting"),
    ({"Status": "Something new", "IntroSentAt": "2026-10-01"}, "In sequence", "active"),
])
def test_the_status_pill(fields, label, kind):
    assert L.status_summary(fields) == (label, kind)


def test_removed_and_stopped_beat_a_reply_or_a_send_date():
    assert L.status_summary({"Status": "Removed", "ReplyStatus": "Replied", "IntroSentAt": "2026-10-01"})[0] == "Removed"
    assert L.status_summary({"Status": "Stopped - Manual", "ReplyStatus": "Replied"})[0] == "Stopped · Manual"


# ================================================================ sequence progress

def test_progress_marks_each_stage_sent_or_pending():
    assert L.sequence_progress({"IntroSentAt": "2026-10-01", "FollowUp1SentAt": "2026-10-03"}, 4) == ["sent", "sent", "pending", "pending"]
    assert L.sequence_progress({}, 3) == ["pending"] * 3


def test_a_replied_lead_has_its_last_sent_stage_marked_replied():
    assert L.sequence_progress({"Status": "Stopped - Replied", "IntroSentAt": "2026-10-01", "FollowUp1SentAt": "2026-10-03"}, 4) == ["sent", "replied", "pending", "pending"]
    assert L.sequence_progress({"ReplyStatus": "Replied", "IntroSentAt": "2026-10-01"}, 3) == ["replied", "pending", "pending"]


def test_a_reply_before_anything_was_sent_marks_nothing_replied():
    assert L.sequence_progress({"ReplyStatus": "Replied"}, 3) == ["pending"] * 3


def test_a_stage_sent_out_of_order_still_shows_as_sent_where_it_is():
    assert L.sequence_progress({"FollowUp2SentAt": "2026-10-03"}, 3) == ["pending", "pending", "sent"]


def test_blank_or_whitespace_dates_do_not_count_as_sent():
    assert L.sequence_progress({"IntroSentAt": "   ", "FollowUp1SentAt": None}, 2) == ["pending", "pending"]


# ================================================================ dates

@pytest.mark.parametrize("value, expected", [
    ("2026-10-12 14:20:00", "Oct 12"), ("2026-10-12", "Oct 12"), ("2026-10-12T14:20:00", "Oct 12"), ("2026-10-12 14:20", "Oct 12"),
    ("2025-12-01 09:00:00", "Dec 1, 2025"), ("", ""), (None, ""), ("not a date", "not a date"), ("  2026-01-05  ", "Jan 5"),
])
def test_short_dates(value, expected):
    assert L.short_date(value, TODAY) == expected


def test_the_last_sent_date_is_the_latest_stage_date_not_the_last_column_filled():
    assert L.last_sent({"IntroSentAt": "2026-10-09 10:00:00", "FollowUp1SentAt": "2026-10-12 09:00:00", "FollowUp2SentAt": "2026-10-10 09:00:00"}, 3) == "2026-10-12 09:00:00"
    assert L.last_sent({}, 3) == "" and L.last_sent({"IntroSentAt": "garbled"}, 3) == "garbled"


# ================================================================ a row

def test_a_row_summarises_the_lead():
    row = L.lead_row(lead(Status="Stopped - Replied", IntroSentAt="2026-10-09 10:00:00", FollowUp1SentAt="2026-10-12 09:00:00", SenderAccount="sales1@x.com"), 4, TODAY)
    assert row == {"name": "Maya Okafor", "company": "BrightLeaf", "email": "maya@example.com", "status": "Replied", "status_kind": "good",
                   "progress": ["sent", "replied", "pending", "pending"], "last_sent": "Oct 12", "sender": "sales1@x.com"}


def test_a_lead_with_almost_nothing_still_gives_a_row():
    row = L.lead_row({}, 2, TODAY)
    assert row["name"] == "(no name)" and row["status"] == "Not contacted" and row["last_sent"] == "" and row["progress"] == ["pending", "pending"]


# ================================================================ timeline

def _events(**fields):
    return [(e["when"], e["icon"], e["text"]) for e in L.timeline(lead(**fields), ["Intro", "Follow-up 1", "Follow-up 2"], TODAY)]


def test_the_timeline_lists_each_send_then_the_reply_in_time_order():
    assert _events(Status="Stopped - Replied", IntroSentAt="2026-10-09 10:00:00", FollowUp1SentAt="2026-10-11 10:00:00", ReplyStatus="Replied", ReplyAt="2026-10-13 08:00:00") == [
        ("Oct 9", "📨", "Intro sent"), ("Oct 11", "📨", "Follow-up 1 sent"), ("Oct 13", "💬", "Replied"), ("", "⏹️", "Follow-ups stopped automatically")]


def test_the_timeline_is_ordered_by_time_not_by_the_order_columns_happen_to_be_in():
    events = _events(IntroSentAt="2026-10-09 10:00:00", FollowUp1SentAt="2026-10-02 10:00:00")
    assert [e[0] for e in events] == ["Oct 2", "Oct 9"]


def test_an_active_lead_shows_when_its_next_follow_up_is_due():
    assert _events(Status="Intro Sent", IntroSentAt="2026-10-09 10:00:00", NextEligibleAt="2026-10-14 09:00:00") == [("Oct 9", "📨", "Intro sent"), ("Oct 14", "⏳", "Next follow-up due")]


@pytest.mark.parametrize("status", ["Stopped - Replied", "Stopped - Manual", "Stopped - Bounced", "Removed", "Completed", "Paused"])
def test_a_lead_that_is_not_active_shows_no_next_follow_up(status):
    assert not any(e[1] == "⏳" for e in _events(Status=status, IntroSentAt="2026-10-09 10:00:00", NextEligibleAt="2026-10-14 09:00:00"))


def test_each_way_sending_can_end_is_described():
    base = {"IntroSentAt": "2026-10-09 10:00:00", "LastActionAt": "2026-10-10 10:00:00"}
    assert ("Oct 10", "⚠️", "Bounced — sending stopped") in _events(Status="Stopped - Bounced", **base)
    assert ("Oct 10", "🗑️", "Removed from the campaign") in _events(Status="Removed", **base)
    assert ("Oct 10", "⏹️", "Stopped - Manual — sending stopped") in _events(Status="Stopped - Manual", **base)
    assert ("Oct 10", "✅", "Sequence completed") in _events(Status="Completed", **base)
    assert ("Oct 10", "⏸️", "Paused") in _events(Status="Paused", **base)


def test_a_reply_with_no_known_time_is_still_listed_and_goes_last_before_nothing_is_lost():
    events = _events(ReplyStatus="Replied", IntroSentAt="2026-10-09 10:00:00")
    assert events == [("Oct 9", "📨", "Intro sent"), ("", "💬", "Replied")]


def test_the_reply_time_falls_back_to_the_last_inbound_time():
    assert ("Oct 12", "💬", "Replied") in _events(ReplyStatus="Replied", LastInboundAt="2026-10-12 08:00:00", IntroSentAt="2026-10-09 10:00:00")


def test_a_lead_nothing_has_happened_to_has_an_empty_timeline():
    assert _events() == []


def test_unreadable_dates_do_not_break_the_timeline():
    assert ("garbled", "📨", "Intro sent") in _events(IntroSentAt="garbled")


# ================================================================ pages

@pytest.mark.parametrize("total, page, size, expected", [
    (0, 0, 25, (0, 0, 0, 1)), (1, 0, 25, (0, 0, 1, 1)), (25, 0, 25, (0, 0, 25, 1)), (26, 0, 25, (0, 0, 25, 2)), (26, 1, 25, (1, 25, 26, 2)),
    (840, 33, 25, (33, 825, 840, 34)), (840, 99, 25, (33, 825, 840, 34)), (50, -4, 25, (0, 0, 25, 2)), (10, 0, 0, (0, 0, 1, 10)), (-5, 3, 25, (0, 0, 0, 1)),
])
def test_pagination(total, page, size, expected):
    assert L.paginate(total, page, size) == expected


def test_the_default_page_size_is_25():
    assert L.PAGE_SIZE == 25 and L.paginate(100, 3)[1:3] == (75, 100)


def test_filter_counts_ask_the_real_filter_for_each_option():
    from data_import_logic import FILTER_OPTIONS, filter_leads
    leads = [lead(Status="Removed"), lead(Status="Stopped - Replied"), lead(Status="Stopped - Bounced"), lead(IntroSentAt="2026-10-01", Approval="Yes"), lead(Approval="Yes")]
    counts = L.filter_counts(leads, FILTER_OPTIONS, filter_leads)
    assert counts == {"All": 5, "In Progress": 1, "Replied": 1, "Bounced": 1, "Removed": 1}


# ================================================================ the extra filters

def _filter_leads():
    return [
        lead(LeadID="1", SenderAccount="sales1", IntroSentAt="2026-10-13 09:00:00"),                                                           # today, at Intro
        lead(LeadID="2", SenderAccount="sales2", IntroSentAt="2026-10-01 09:00:00", FollowUp1SentAt="2026-10-10 09:00:00"),                 # 3 days ago, at Follow-up 1
        lead(LeadID="3", SenderAccount="sales1", IntroSentAt="2026-08-01 09:00:00"),                                                           # long ago, at Intro
        lead(LeadID="4", SenderAccount="", IntroSentAt="2026-10-07 09:00:00", FollowUp1SentAt="2026-10-07 09:00:00", FollowUp2SentAt="2026-10-08 09:00:00"),   # 5 days ago, at Follow-up 2
        lead(LeadID="5", SenderAccount="sales2"),                                                                                               # never contacted
    ]


def _ids(result):
    return [x["LeadID"] for x in result]


LABELS = ["Intro", "Follow-up 1", "Follow-up 2"]


def test_the_highest_stage_is_the_last_one_sent_or_minus_one():
    assert [L.highest_stage(x, 3) for x in _filter_leads()] == [0, 1, 0, 2, -1]
    assert L.highest_stage({"FollowUp2SentAt": "2026-10-01"}, 3) == 2 and L.highest_stage({"IntroSentAt": "  "}, 3) == -1


def test_the_sender_options_list_every_account_once_and_offer_no_sender_only_when_some_lead_has_none():
    assert L.sender_options(_filter_leads()) == ["All senders", "sales1", "sales2", "(no sender yet)"]
    assert L.sender_options([lead(SenderAccount="b"), lead(SenderAccount="a"), lead(SenderAccount="a ")]) == ["All senders", "a", "b"]
    assert L.sender_options([]) == ["All senders"]


def test_the_stage_options_follow_the_campaigns_stages():
    assert L.stage_options(LABELS) == ["Any stage", "Not contacted yet", "At Intro", "At Follow-up 1", "At Follow-up 2"]


@pytest.mark.parametrize("sender, expected", [("All senders", ["1", "2", "3", "4", "5"]), ("sales1", ["1", "3"]), ("sales2", ["2", "5"]), ("(no sender yet)", ["4"]), ("nobody", [])])
def test_filtering_by_sender(sender, expected):
    assert _ids(L.apply_extra_filters(_filter_leads(), 3, LABELS, sender=sender, today=TODAY)) == expected


@pytest.mark.parametrize("stage, expected", [("Any stage", ["1", "2", "3", "4", "5"]), ("Not contacted yet", ["5"]), ("At Intro", ["1", "3"]), ("At Follow-up 1", ["2"]), ("At Follow-up 2", ["4"])])
def test_filtering_by_the_stage_most_recently_sent(stage, expected):
    assert _ids(L.apply_extra_filters(_filter_leads(), 3, LABELS, stage=stage, today=TODAY)) == expected


@pytest.mark.parametrize("recency, expected", [("Any time", ["1", "2", "3", "4", "5"]), ("Today", ["1"]), ("Last 7 days", ["1", "2", "4"]),
                                               ("Last 30 days", ["1", "2", "4"]), ("Older than 30 days", ["3"])])
def test_filtering_by_how_recently_an_email_went_out(recency, expected):
    assert _ids(L.apply_extra_filters(_filter_leads(), 3, LABELS, recency=recency, today=TODAY)) == expected


def test_a_lead_never_emailed_is_neither_recent_nor_older():
    never = [lead(LeadID="9")]
    for recency in ("Today", "Last 7 days", "Last 30 days", "Older than 30 days"):
        assert L.apply_extra_filters(never, 3, LABELS, recency=recency, today=TODAY) == []


def test_the_recency_boundaries_are_exact():
    edge = [lead(LeadID="a", IntroSentAt="2026-10-07 00:00:00"), lead(LeadID="b", IntroSentAt="2026-10-06 00:00:00"),
            lead(LeadID="c", IntroSentAt="2026-09-14 00:00:00"), lead(LeadID="d", IntroSentAt="2026-09-13 00:00:00")]
    assert _ids(L.apply_extra_filters(edge, 3, LABELS, recency="Last 7 days", today=TODAY)) == ["a"]               # 6 days ago is in, 7 is out
    assert _ids(L.apply_extra_filters(edge, 3, LABELS, recency="Last 30 days", today=TODAY)) == ["a", "b", "c"]    # 29 days ago is in, 30 is out
    assert _ids(L.apply_extra_filters(edge, 3, LABELS, recency="Older than 30 days", today=TODAY)) == ["d"]


def test_the_three_filters_combine_with_and():
    assert _ids(L.apply_extra_filters(_filter_leads(), 3, LABELS, sender="sales1", stage="At Intro", recency="Last 7 days", today=TODAY)) == ["1"]
    assert L.apply_extra_filters(_filter_leads(), 3, LABELS, sender="sales2", stage="At Intro", today=TODAY) == []


def test_an_option_that_is_not_recognised_filters_nothing_so_a_stale_choice_cannot_hide_every_lead():
    assert _ids(L.apply_extra_filters(_filter_leads(), 3, LABELS, stage="At Follow-up 9", recency="Sometime", today=TODAY)) == ["1", "2", "3", "4", "5"]


def test_the_filters_keep_the_same_dicts_in_the_same_order():
    leads = _filter_leads()
    result = L.apply_extra_filters(leads, 3, LABELS, sender="sales1", today=TODAY)
    assert result[0] is leads[0] and result[1] is leads[2]


def test_any_extra_filter_says_whether_one_is_active():
    assert not L.any_extra_filter("All senders", "Any stage", "Any time")
    assert L.any_extra_filter("sales1", "Any stage", "Any time") and L.any_extra_filter("All senders", "At Intro", "Any time") and L.any_extra_filter("All senders", "Any stage", "Today")


def test_today_means_today_and_not_yesterday():
    two = [lead(LeadID="a", IntroSentAt="2026-10-13 00:01:00"), lead(LeadID="b", IntroSentAt="2026-10-12 23:59:00")]
    assert _ids(L.apply_extra_filters(two, 3, LABELS, recency="Today", today=TODAY)) == ["a"]
    assert _ids(L.apply_extra_filters(two, 3, LABELS, recency="Last 7 days", today=TODAY)) == ["a", "b"]
