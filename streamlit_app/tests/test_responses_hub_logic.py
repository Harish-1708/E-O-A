import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from responses_hub_logic import (
    tag_responses_with_campaign, response_key, filter_responses, count_unread,
    sort_responses_newest_first, get_campaign_names_present, build_reply_summary_label,
    find_response_by_key, STATUS_FILTER_ALL, INBOX_FILTER_ALL, INBOX_FILTER_UNREAD, search_responses,
    is_response_read, split_keys_by_campaign, build_mark_read_payload, matches_status_filter,
    group_responses_by_lead, sort_conversation_groups_newest_first, group_unread_count,
    all_response_keys_in_group, filter_groups_by_unread,
)


def _response(**overrides):
    r = {
        "ResponseID": "r1", "LeadID": "5", "From": "lead@abc.com", "Subject": "Re: Hi",
        "Classification": "Genuine Reply", "ReceivedAt": "2026-08-29 10:00:00", "_campaign": "Foo",
    }
    r.update(overrides)
    return r


# ---------- tag_responses_with_campaign ----------

def test_tag_responses_with_campaign_adds_campaign_key():
    tagged = tag_responses_with_campaign([{"ResponseID": "r1"}], "Foo")
    assert tagged[0]["_campaign"] == "Foo"


def test_tag_responses_with_campaign_never_mutates_input():
    original = [{"ResponseID": "r1"}]
    tag_responses_with_campaign(original, "Foo")
    assert "_campaign" not in original[0]


def test_tag_responses_with_campaign_empty_list():
    assert tag_responses_with_campaign([], "Foo") == []


def test_tag_responses_with_campaign_multiple_responses():
    tagged = tag_responses_with_campaign([{"ResponseID": "r1"}, {"ResponseID": "r2"}], "Foo")
    assert all(r["_campaign"] == "Foo" for r in tagged)


# ---------- response_key ----------

def test_response_key_combines_campaign_and_response_id():
    key = response_key(_response(_campaign="Foo", ResponseID="r1"))
    assert key == "Foo:r1"


def test_response_key_distinguishes_same_response_id_across_campaigns():
    key1 = response_key(_response(_campaign="Foo", ResponseID="r1"))
    key2 = response_key(_response(_campaign="Bar", ResponseID="r1"))
    assert key1 != key2


# ---------- filter_responses ----------

def test_filter_responses_status_all_returns_everything():
    responses = [_response(Classification="Genuine Reply"), _response(Classification="Bounce (Hard)")]
    result = filter_responses(responses, STATUS_FILTER_ALL, STATUS_FILTER_ALL, INBOX_FILTER_ALL, set())
    assert len(result) == 2


def test_filter_responses_by_status():
    responses = [_response(ResponseID="r1", Classification="Genuine Reply"),
                 _response(ResponseID="r2", Classification="Bounce (Hard)")]
    result = filter_responses(responses, "Genuine Reply", STATUS_FILTER_ALL, INBOX_FILTER_ALL, set())
    assert len(result) == 1
    assert result[0]["ResponseID"] == "r1"


def test_filter_responses_by_campaign():
    responses = [_response(ResponseID="r1", _campaign="Foo"), _response(ResponseID="r2", _campaign="Bar")]
    result = filter_responses(responses, STATUS_FILTER_ALL, "Foo", INBOX_FILTER_ALL, set())
    assert len(result) == 1
    assert result[0]["_campaign"] == "Foo"


def test_filter_responses_status_and_campaign_combined():
    responses = [
        _response(ResponseID="r1", _campaign="Foo", Classification="Genuine Reply"),
        _response(ResponseID="r2", _campaign="Foo", Classification="Bounce (Hard)"),
        _response(ResponseID="r3", _campaign="Bar", Classification="Genuine Reply"),
    ]
    result = filter_responses(responses, "Genuine Reply", "Foo", INBOX_FILTER_ALL, set())
    assert len(result) == 1
    assert result[0]["ResponseID"] == "r1"


def test_filter_responses_unread_only():
    responses = [_response(ResponseID="r1", _campaign="Foo"), _response(ResponseID="r2", _campaign="Foo")]
    read_keys = {"Foo:r1"}
    result = filter_responses(responses, STATUS_FILTER_ALL, STATUS_FILTER_ALL, INBOX_FILTER_UNREAD, read_keys)
    assert len(result) == 1
    assert result[0]["ResponseID"] == "r2"


def test_filter_responses_all_three_filters_combined():
    responses = [
        _response(ResponseID="r1", _campaign="Foo", Classification="Genuine Reply"),
        _response(ResponseID="r2", _campaign="Foo", Classification="Genuine Reply"),
        _response(ResponseID="r3", _campaign="Bar", Classification="Genuine Reply"),
    ]
    read_keys = {"Foo:r1"}
    result = filter_responses(responses, "Genuine Reply", "Foo", INBOX_FILTER_UNREAD, read_keys)
    assert len(result) == 1
    assert result[0]["ResponseID"] == "r2"


def test_filter_responses_empty_list():
    assert filter_responses([], STATUS_FILTER_ALL, STATUS_FILTER_ALL, INBOX_FILTER_ALL, set()) == []


# ---------- count_unread ----------

def test_count_unread_all_unread():
    responses = [_response(ResponseID="r1"), _response(ResponseID="r2")]
    assert count_unread(responses, set()) == 2


def test_count_unread_some_read():
    responses = [_response(ResponseID="r1", _campaign="Foo"), _response(ResponseID="r2", _campaign="Foo")]
    assert count_unread(responses, {"Foo:r1"}) == 1


def test_count_unread_all_read():
    responses = [_response(ResponseID="r1", _campaign="Foo")]
    assert count_unread(responses, {"Foo:r1"}) == 0


def test_count_unread_empty_list():
    assert count_unread([], set()) == 0


# ---------- sort_responses_newest_first ----------

def test_sort_responses_newest_first():
    responses = [
        _response(ResponseID="old", ReceivedAt="2026-08-01 09:00:00"),
        _response(ResponseID="new", ReceivedAt="2026-08-29 09:00:00"),
    ]
    sorted_responses = sort_responses_newest_first(responses)
    assert sorted_responses[0]["ResponseID"] == "new"
    assert sorted_responses[1]["ResponseID"] == "old"


def test_sort_responses_newest_first_empty_list():
    assert sort_responses_newest_first([]) == []


# ---------- get_campaign_names_present ----------

def test_get_campaign_names_present_deduplicates_and_sorts():
    responses = [_response(_campaign="Zeta"), _response(_campaign="Alpha"), _response(_campaign="Zeta")]
    assert get_campaign_names_present(responses) == ["Alpha", "Zeta"]


def test_get_campaign_names_present_empty_list():
    assert get_campaign_names_present([]) == []


def test_get_campaign_names_present_skips_blank_campaign():
    responses = [_response(_campaign=""), _response(_campaign="Foo")]
    assert get_campaign_names_present(responses) == ["Foo"]


# ---------- build_reply_summary_label ----------

def test_build_reply_summary_label_includes_sender_subject_campaign():
    label = build_reply_summary_label(_response(From="lead@abc.com", Subject="Re: Hi", _campaign="Foo"))
    assert "lead@abc.com" in label
    assert "Re: Hi" in label
    assert "Foo" in label


def test_build_reply_summary_label_handles_missing_fields():
    label = build_reply_summary_label({})
    assert "unknown sender" in label
    assert "no subject" in label


# ---------- find_response_by_key ----------

def test_find_response_by_key_found():
    responses = [_response(ResponseID="r1", _campaign="Foo"), _response(ResponseID="r2", _campaign="Foo")]
    found = find_response_by_key(responses, "Foo:r2")
    assert found["ResponseID"] == "r2"


def test_find_response_by_key_not_found_returns_none():
    responses = [_response(ResponseID="r1", _campaign="Foo")]
    assert find_response_by_key(responses, "Foo:nonexistent") is None


def test_find_response_by_key_empty_list():
    assert find_response_by_key([], "Foo:r1") is None


# ---------- search_responses ----------

def test_search_responses_matches_sender():
    responses = [_response(From="rocky@abc.com"), _response(From="jane@xyz.com")]
    result = search_responses(responses, "rocky")
    assert len(result) == 1
    assert result[0]["From"] == "rocky@abc.com"


def test_search_responses_matches_subject():
    responses = [_response(Subject="Re: pricing question"), _response(Subject="Re: unrelated")]
    result = search_responses(responses, "pricing")
    assert len(result) == 1


def test_search_responses_matches_snippet():
    responses = [_response(ResponseID="r1", Snippet="Let's discuss DudeRobe partnership"),
                 _response(ResponseID="r2", Snippet="Not interested")]
    result = search_responses(responses, "duderobe")
    assert len(result) == 1
    assert result[0]["ResponseID"] == "r1"


def test_search_responses_matches_campaign():
    responses = [_response(ResponseID="r1", _campaign="DudeRobe_Q3"), _response(ResponseID="r2", _campaign="Other")]
    result = search_responses(responses, "duderobe")
    assert len(result) == 1
    assert result[0]["ResponseID"] == "r1"


def test_search_responses_case_insensitive():
    responses = [_response(From="Rocky@ABC.com")]
    assert len(search_responses(responses, "rocky")) == 1
    assert len(search_responses(responses, "ROCKY")) == 1


def test_search_responses_blank_query_returns_everything():
    responses = [_response(ResponseID="r1"), _response(ResponseID="r2")]
    assert search_responses(responses, "") == responses
    assert search_responses(responses, "   ") == responses


def test_search_responses_no_match_returns_empty():
    responses = [_response(From="rocky@abc.com")]
    assert search_responses(responses, "nonexistent_term") == []


def test_search_responses_empty_list():
    assert search_responses([], "anything") == []


def test_search_responses_handles_missing_fields_gracefully():
    assert search_responses([{}], "anything") == []


# ---------- is_response_read ----------

def test_is_response_read_true_when_sheet_says_yes():
    response = _response(IsRead="Yes")
    assert is_response_read(response, set()) is True


def test_is_response_read_case_insensitive_and_alternate_truthy_values():
    assert is_response_read(_response(IsRead="yes"), set()) is True
    assert is_response_read(_response(IsRead="TRUE"), set()) is True
    assert is_response_read(_response(IsRead="1"), set()) is True


def test_is_response_read_false_when_sheet_blank_and_not_in_session():
    response = _response(IsRead="")
    assert is_response_read(response, set()) is False


def test_is_response_read_true_via_session_overlay_even_if_sheet_blank():
    """The optimistic local overlay — marked read this session, sync to
    the Sheet hasn't landed yet, but the UI shouldn't look stuck."""
    response = _response(_campaign="Foo", ResponseID="r1", IsRead="")
    assert is_response_read(response, {"Foo:r1"}) is True


def test_is_response_read_sheet_value_takes_precedence_regardless():
    response = _response(_campaign="Foo", ResponseID="r1", IsRead="Yes")
    assert is_response_read(response, set()) is True  # true even with an empty session overlay


# ---------- split_keys_by_campaign ----------

def test_split_keys_by_campaign_groups_correctly():
    keys = {"Foo:r1", "Foo:r2", "Bar:r3"}
    grouped = split_keys_by_campaign(keys)
    assert set(grouped["Foo"]) == {"r1", "r2"}
    assert grouped["Bar"] == ["r3"]


def test_split_keys_by_campaign_empty_set():
    assert split_keys_by_campaign(set()) == {}


def test_split_keys_by_campaign_skips_malformed_keys():
    keys = {"", "NoColonHere", "Foo:r1"}
    grouped = split_keys_by_campaign(keys)
    assert grouped == {"Foo": ["r1"]}


# ---------- build_mark_read_payload ----------

def test_build_mark_read_payload_shape():
    payload = build_mark_read_payload(["r1", "r2"])
    assert payload == {"response_ids": ["r1", "r2"]}


def test_build_mark_read_payload_is_json_serializable():
    import json
    json.dumps(build_mark_read_payload(["r1"]))


# ---------- matches_status_filter ----------

def test_matches_status_filter_all_matches_everything():
    assert matches_status_filter(_response(), STATUS_FILTER_ALL) is True


def test_matches_status_filter_intent_value_checks_intent_field():
    response = _response(Classification="Genuine Reply", Intent="Interested")
    assert matches_status_filter(response, "Interested") is True
    assert matches_status_filter(response, "Not Interested") is False


def test_matches_status_filter_classification_value_checks_classification_field():
    response = _response(Classification="Auto-Reply", Intent="")
    assert matches_status_filter(response, "Auto-Reply") is True
    assert matches_status_filter(response, "Genuine Reply") is False


def test_matches_status_filter_genuine_reply_with_blank_intent_still_matches_genuine_reply():
    """A reply not yet intent-classified (blank Intent) should still be
    findable via the 'Genuine Reply' Classification filter — this is how
    you'd find "replies that haven't been intent-classified yet"."""
    response = _response(Classification="Genuine Reply", Intent="")
    assert matches_status_filter(response, "Genuine Reply") is True


def test_matches_status_filter_intent_value_does_not_match_a_bounce():
    response = _response(Classification="Bounce (Hard)", Intent="")
    assert matches_status_filter(response, "Interested") is False


def test_filter_responses_by_intent():
    responses = [
        _response(ResponseID="r1", Classification="Genuine Reply", Intent="Interested"),
        _response(ResponseID="r2", Classification="Genuine Reply", Intent="Not Interested"),
    ]
    result = filter_responses(responses, "Interested", STATUS_FILTER_ALL, INBOX_FILTER_ALL, set())
    assert len(result) == 1
    assert result[0]["ResponseID"] == "r1"


def test_filter_responses_lead_followup_never_matches_a_bounce():
    responses = [_response(Classification="Bounce (Hard)", Intent="")]
    result = filter_responses(responses, "Lead / Needs Follow-up", STATUS_FILTER_ALL, INBOX_FILTER_ALL, set())
    assert result == []


# ---------- group_responses_by_lead (the actual reported bug) ----------

def test_group_responses_by_lead_collapses_one_creators_messages_into_one_conversation():
    """The actual reported production bug, shown directly in a
    screenshot: the same creator (Kari Blackburn) sending several
    messages over time showed as a SEPARATE card for every single
    message, instead of one conversation that simply grows. Must
    collapse to exactly one group for one lead, regardless of how many
    responses they've sent."""
    responses = [
        _response(ResponseID="r1", LeadID="5", ReceivedAt="2026-09-30 11:57:00"),
        _response(ResponseID="r2", LeadID="5", ReceivedAt="2026-09-30 19:15:37"),
        _response(ResponseID="r3", LeadID="5", ReceivedAt="2026-09-30 19:15:38"),
        _response(ResponseID="r4", LeadID="5", ReceivedAt="2026-10-01 15:38:00"),
    ]
    groups = group_responses_by_lead(responses)
    assert len(groups) == 1
    assert len(groups[0]["messages"]) == 4


def test_group_responses_by_lead_keeps_different_leads_separate():
    responses = [
        _response(ResponseID="r1", LeadID="5", From="kari@abc.com"),
        _response(ResponseID="r2", LeadID="9", From="other@abc.com"),
    ]
    groups = group_responses_by_lead(responses)
    assert len(groups) == 2
    assert {g["lead_id"] for g in groups} == {"5", "9"}


def test_group_responses_by_lead_keeps_same_lead_id_separate_across_campaigns():
    """LeadID alone is only unique WITHIN a campaign — the same numeric
    ID in two different campaigns' sheets refers to two entirely
    different people, and must never be merged into one conversation."""
    responses = [
        _response(ResponseID="r1", LeadID="5", _campaign="CampaignA"),
        _response(ResponseID="r2", LeadID="5", _campaign="CampaignB"),
    ]
    groups = group_responses_by_lead(responses)
    assert len(groups) == 2
    assert {g["campaign"] for g in groups} == {"CampaignA", "CampaignB"}


def test_group_responses_by_lead_latest_is_the_most_recent_message():
    responses = [
        _response(ResponseID="r1", LeadID="5", Subject="First one", ReceivedAt="2026-09-30 11:57:00"),
        _response(ResponseID="r2", LeadID="5", Subject="Most recent", ReceivedAt="2026-10-01 15:38:00"),
        _response(ResponseID="r3", LeadID="5", Subject="Middle one", ReceivedAt="2026-09-30 19:15:38"),
    ]
    groups = group_responses_by_lead(responses)
    assert groups[0]["latest"]["Subject"] == "Most recent"
    # Newest first throughout, not just the head.
    assert [m["Subject"] for m in groups[0]["messages"]] == ["Most recent", "Middle one", "First one"]


def test_group_responses_by_lead_a_response_with_no_lead_id_gets_its_own_group():
    """Must never silently merge two UNRELATED responses just because
    neither has a LeadID, and must never drop one entirely."""
    responses = [
        _response(ResponseID="r1", LeadID="", From="a@abc.com"),
        _response(ResponseID="r2", LeadID="", From="b@abc.com"),
    ]
    groups = group_responses_by_lead(responses)
    assert len(groups) == 2


def test_group_responses_by_lead_preserves_first_seen_order_for_ties():
    responses = [_response(ResponseID="r1", LeadID="5"), _response(ResponseID="r2", LeadID="9")]
    groups = group_responses_by_lead(responses)
    assert [g["lead_id"] for g in groups] == ["5", "9"]


# ---------- sort_conversation_groups_newest_first ----------

def test_sort_conversation_groups_by_their_latest_message():
    """The exact reported requirement: if Kari sends another message,
    HER conversation moves to the top — sorting is by each
    conversation's own most recent activity, not by when the
    conversation first started."""
    groups = group_responses_by_lead([
        _response(ResponseID="r1", LeadID="5", ReceivedAt="2026-09-01 10:00:00"),  # old conversation
        _response(ResponseID="r2", LeadID="9", ReceivedAt="2026-09-15 10:00:00"),
        _response(ResponseID="r3", LeadID="9", ReceivedAt="2026-10-01 10:00:00"),  # lead 9 just replied again
    ])
    sorted_groups = sort_conversation_groups_newest_first(groups)
    assert sorted_groups[0]["lead_id"] == "9"
    assert sorted_groups[1]["lead_id"] == "5"


# ---------- group_unread_count / all_response_keys_in_group ----------

def test_group_unread_count_counts_unread_messages_within_the_group():
    responses = [
        _response(ResponseID="r1", LeadID="5", IsRead="Yes"),
        _response(ResponseID="r2", LeadID="5", IsRead=""),
        _response(ResponseID="r3", LeadID="5", IsRead=""),
    ]
    group = group_responses_by_lead(responses)[0]
    assert group_unread_count(group, set()) == 2


def test_all_response_keys_in_group_covers_every_message_for_mark_as_read():
    """Marking a conversation read must mark EVERY message in it, not
    just the one most recently shown — otherwise earlier messages in
    the same thread stay stuck as unread forever."""
    responses = [_response(ResponseID="r1", LeadID="5"), _response(ResponseID="r2", LeadID="5")]
    group = group_responses_by_lead(responses)[0]
    keys = all_response_keys_in_group(group)
    assert len(keys) == 2
    assert all(k.endswith("r1") or k.endswith("r2") for k in keys)


# ---------- filter_groups_by_unread ----------

def test_filter_groups_by_unread_keeps_a_conversation_with_any_unread_message():
    """Read/unread is a property of the WHOLE conversation from the
    person's own point of view — a thread with one unread message
    (even an older one, not the latest) must still surface, exactly
    like a real email client's unread filter."""
    responses = [
        _response(ResponseID="r1", LeadID="5", ReceivedAt="2026-09-01 10:00:00", IsRead=""),
        _response(ResponseID="r2", LeadID="5", ReceivedAt="2026-10-01 10:00:00", IsRead="Yes"),
    ]
    groups = group_responses_by_lead(responses)
    result = filter_groups_by_unread(groups, INBOX_FILTER_UNREAD, set())
    assert len(result) == 1


def test_filter_groups_by_unread_drops_a_fully_read_conversation():
    responses = [_response(ResponseID="r1", LeadID="5", IsRead="Yes")]
    groups = group_responses_by_lead(responses)
    result = filter_groups_by_unread(groups, INBOX_FILTER_UNREAD, set())
    assert result == []


def test_filter_groups_by_unread_all_filter_is_a_no_op():
    responses = [_response(ResponseID="r1", LeadID="5", IsRead="Yes")]
    groups = group_responses_by_lead(responses)
    result = filter_groups_by_unread(groups, INBOX_FILTER_ALL, set())
    assert len(result) == 1
