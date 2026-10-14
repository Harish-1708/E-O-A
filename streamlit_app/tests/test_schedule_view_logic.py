"""The Schedule tab's surroundings: the window strip, the next-run card, due counts from the engine's own rule, the week's bars and the follow-up waits."""
import copy
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import outreach  # noqa: E402
import schedule_view_logic as S  # noqa: E402

UTC = timezone.utc
NOW = datetime(2026, 10, 13, 14, 0, tzinfo=UTC)               # Tuesday: 09:00 in Chicago (CDT), 19:30 in Kolkata


# ================================================================ times

@pytest.mark.parametrize("text, minutes", [("09:00", 540), ("9:30", 570), ("00:00", 0), ("23:59", 1439), (" 17:00 ", 1020), ("24:00", None), ("12:60", None), ("9", None), ("09:0", None),
                                           ("", None), (None, None), ("ab:cd", None), ("-1:00", None), ("09:00:00", None), ("9am", None)])
def test_only_a_real_24_hour_time_is_understood(text, minutes):
    assert S.parse_hhmm(text) == minutes


@pytest.mark.parametrize("text, shown", [("17:00", "5:00 PM"), ("09:05", "9:05 AM"), ("00:15", "12:15 AM"), ("12:00", "12:00 PM"), ("23:59", "11:59 PM"), ("junk", "junk"), (None, ""), ("", "")])
def test_times_are_shown_the_12_hour_way_and_junk_is_returned_unchanged(text, shown):
    assert S.clock12(text) == shown


@pytest.mark.parametrize("seconds, words", [(0, "1 minute"), (30, "1 minute"), (60, "1 minute"), (120, "2 minutes"), (3300, "55 minutes"), (3599, "59 minutes"), (3600, "1 hour"),
                                            (7500, "2 hours 5 minutes"), (7200, "2 hours"), (20400, "5 hours 40 minutes"), (21600, "6 hours"), (22140, "6 hours"),
                                            (86400, "1 day"), (2 * 86400 + 19 * 3600 + 120, "2 days 19 hours"), (3 * 86400, "3 days"), (-5, "1 minute")])
def test_waits_are_in_words_and_the_largest_sensible_unit(seconds, words):
    assert S.wait_words(seconds) == words


@pytest.mark.parametrize("when, zone, abbr", [(datetime(2026, 10, 13, 14, 0, tzinfo=UTC), "America/Chicago", "CDT"), (datetime(2026, 1, 13, 14, 0, tzinfo=UTC), "America/Chicago", "CST"),
                                              (datetime(2026, 10, 13, 14, 0, tzinfo=UTC), "Asia/Kolkata", "IST"), (datetime(2026, 7, 1, 14, 0, tzinfo=UTC), "America/New_York", "EDT"),
                                              (datetime(2026, 10, 13, 14, 0, tzinfo=UTC), None, "UTC"), (datetime(2026, 10, 13, 14, 0, tzinfo=UTC), "Mars/Olympus", "UTC")])
def test_the_zone_abbreviation_follows_daylight_saving(when, zone, abbr):
    assert S.tz_abbr(when, zone) == abbr


# ================================================================ the next automatic run

def test_the_next_run_card_gives_the_day_the_time_the_wait_and_the_viewers_own_time():
    card = S.next_run_card(datetime(2026, 10, 13, 19, 43, tzinfo=UTC), NOW, "America/Chicago", "Asia/Kolkata")
    assert card == {"headline": "Today 2:43 PM CDT", "relative": "in 5 hours 43 minutes", "viewer": "Wed 1:13 AM IST"}          # 19:43 UTC is 01:13 the next day in Kolkata


def test_the_viewers_time_has_no_day_when_it_is_the_same_day():
    card = S.next_run_card(datetime(2026, 10, 13, 14, 43, tzinfo=UTC), NOW, "America/Chicago", "Asia/Kolkata")
    assert card == {"headline": "Today 9:43 AM CDT", "relative": "in 43 minutes", "viewer": "8:13 PM IST"}


@pytest.mark.parametrize("viewer", [None, "", "America/Chicago", "Mars/Olympus", "America/Menominee"])
def test_no_second_time_is_shown_when_the_viewer_is_in_the_same_zone_or_unknown(viewer):
    assert S.next_run_card(datetime(2026, 10, 13, 19, 43, tzinfo=UTC), NOW, "America/Chicago", viewer)["viewer"] == ""


@pytest.mark.parametrize("next_utc, headline", [
    (datetime(2026, 10, 14, 14, 43, tzinfo=UTC), "Tomorrow 9:43 AM CDT"), (datetime(2026, 10, 16, 14, 43, tzinfo=UTC), "Fri 9:43 AM CDT"),
    (datetime(2026, 10, 21, 14, 43, tzinfo=UTC), "Oct 21 9:43 AM CDT"), (datetime(2026, 10, 14, 5, 5, tzinfo=UTC), "Tomorrow 12:05 AM CDT"),         # 00:05 on the 14th in Chicago: tomorrow, although it is 05:05 UTC
])
def test_the_headline_names_the_day_the_way_a_person_would(next_utc, headline):
    assert S.next_run_card(next_utc, NOW, "America/Chicago")["headline"] == headline


def test_with_no_next_run_there_is_no_card():
    assert S.next_run_card(None, NOW, "America/Chicago") is None


def test_a_campaign_with_no_time_zone_is_shown_in_utc():
    assert S.next_run_card(datetime(2026, 10, 13, 14, 43, tzinfo=UTC), NOW, None)["headline"] == "Today 2:43 PM UTC"


# ================================================================ the window across the day

def _active(strip):
    return [b["hour"] for b in strip if b["active"]]


def test_a_nine_to_five_window_covers_the_hours_nine_to_sixteen_and_the_strip_runs_six_to_twenty_one():
    strip = S.hour_strip("09:00", "17:00")
    assert [b["hour"] for b in strip] == list(range(6, 22)) and _active(strip) == list(range(9, 17))


@pytest.mark.parametrize("start, end, active", [("06:30", "08:15", [6, 7, 8]), ("09:30", "10:00", [9]), ("09:30", "10:01", [9, 10]), ("08:00", "09:00", [8]), ("21:00", "21:30", [21])])
def test_an_hour_is_active_when_the_window_covers_any_part_of_it(start, end, active):
    assert _active(S.hour_strip(start, end)) == active


@pytest.mark.parametrize("start, end, first, last", [("22:00", "23:30", 6, 23), ("05:00", "06:00", 5, 21), ("00:00", "23:59", 0, 23), ("04:00", "23:00", 4, 22)])
def test_the_strip_widens_when_the_window_reaches_outside_six_to_twenty_one(start, end, first, last):
    strip = S.hour_strip(start, end)
    assert strip[0]["hour"] == first and strip[-1]["hour"] == last and len(strip) == last - first + 1 and _active(strip)


@pytest.mark.parametrize("start, end", [("25:00", "17:00"), ("09:00", "abc"), ("", ""), (None, None), ("9", "17:00"), ("09:00", "24:00")])
def test_a_time_that_is_not_a_real_hh_mm_marks_nothing_and_does_not_break(start, end):
    strip = S.hour_strip(start, end)
    assert len(strip) == 16 and _active(strip) == []


@pytest.mark.parametrize("start, end, active", [("22:00", "06:00", [0, 1, 2, 3, 4, 5, 22, 23]), ("17:00", "09:00", list(range(0, 9)) + list(range(17, 24))), ("23:30", "00:30", [0, 23]), ("20:00", "02:00", [0, 1, 20, 21, 22, 23])])
def test_an_overnight_window_runs_from_the_start_to_midnight_and_on_to_the_end_as_the_engine_does(start, end, active):
    """The engine: 22:00 is open, 21:59 is not, and it stays open until 06:00. The strip shows the whole day so the overnight hours are visible."""
    strip = S.hour_strip(start, end)
    assert _active(strip) == active and [b["hour"] for b in strip] == list(range(0, 24))


@pytest.mark.parametrize("start, expected_hour", [("09:00", 9), ("00:00", 0), ("23:59", 23), ("13:45", 13)])
def test_the_same_time_twice_is_a_one_minute_window_so_only_that_hour_is_marked(start, expected_hour):
    assert _active(S.hour_strip(start, start)) == [expected_hour]


def test_a_window_that_ends_exactly_on_an_hour_does_not_mark_the_next_hour_and_one_minute_past_does():
    assert _active(S.hour_strip("09:00", "10:00")) == [9] and _active(S.hour_strip("09:00", "10:01")) == [9, 10]


# ================================================================ days and the window right now

@pytest.mark.parametrize("days, line", [
    (["mon", "tue", "wed", "thu", "fri"], "Weekend sends: none (Sat, Sun off)"), (["sat"], "Weekend sends: Sat (Sun off)"), (["sun"], "Weekend sends: Sun (Sat off)"),
    (["sat", "sun"], "Weekend sends: Sat, Sun"), ([], "Weekend sends: none (Sat, Sun off)"), (None, "Weekend sends: none (Sat, Sun off)"), (["SAT", "Sunday"], "Weekend sends: Sat, Sun"),
])
def test_the_weekend_line_says_which_weekend_days_send(days, line):
    assert S.weekend_line(days) == line


CHICAGO = {"timezone": "America/Chicago", "window_start": "09:00", "window_end": "17:00", "send_days": ["mon", "tue", "wed", "thu", "fri"]}


def test_the_window_status_follows_the_engines_own_rule():
    assert S.window_status(CHICAGO, NOW) == {"open": True, "text": "Window is open until 5:00 PM"}
    assert S.window_status(CHICAGO, datetime(2026, 10, 13, 23, 0, tzinfo=UTC)) == {"open": False, "text": "Window is closed right now"}                 # 18:00 in Chicago
    assert S.window_status(CHICAGO, datetime(2026, 10, 17, 17, 0, tzinfo=UTC))["open"] is False                                                          # a Saturday
    assert S.window_status(CHICAGO, datetime(2026, 10, 13, 22, 0, tzinfo=UTC))["open"] is True                                                           # 17:00 sharp is still inside, as the engine says


def test_with_no_schedule_the_campaign_sends_anytime():
    assert S.window_status({}, NOW) == {"open": True, "text": "No schedule: this campaign sends anytime"} and S.window_status(None, NOW)["open"] is True


def test_a_broken_schedule_never_breaks_the_tab():
    def explode(schedule, when):
        raise RuntimeError("bad timezone")
    assert S.window_status(CHICAGO, NOW, explode) == {"open": False, "text": "Window can't be worked out — check the time zone and times"}


# ================================================================ how many leads are due

def _stages(*waits):
    names = ["intro"] + [f"followup{i}" for i in range(1, len(waits))]
    return [{"name": n, "template_prefix": n, "wait_days_after_previous": w} for n, w in zip(names, waits)]


def _ago(days):
    return (datetime.now() - timedelta(days=days)).strftime(outreach.DATETIME_FMT)


def _lead(i, **fields):
    row = {"LeadID": str(i), "Email": f"p{i}@x.com", "Status": "", "ReplyStatus": ""}
    row.update(fields)
    return row


def test_due_now_and_due_tomorrow_come_from_the_engines_own_rule():
    stages = _stages(0, 2, 2)
    terminal = sorted(outreach.TERMINAL_STATUSES)[0]
    leads = [
        _lead("A"),                                                                          # never contacted: its intro is due now
        _lead("B", IntroSentAt=_ago(3)),                                                     # intro 3 days ago, wait 2: follow-up 1 is due now
        _lead("C", IntroSentAt=_ago(1)),                                                     # intro yesterday: follow-up 1 is due TOMORROW
        _lead("D", IntroSentAt=_ago(0)),                                                     # intro today: not due for two days
        _lead("E", ReplyStatus="Replied", IntroSentAt=_ago(5)),                              # replied: never due
        _lead("F", Status=terminal, IntroSentAt=_ago(5)),                                    # finished: never due
        _lead("G", Email="", IntroSentAt=_ago(5)),                                           # no email address: never due
        _lead("H", Email="h@x.com"),                                                          # a different person, due now like A
        _lead("I", IntroSentAt=_ago(5), FollowUp1SentAt=_ago(0)),                            # follow-up 1 just sent: follow-up 2 not due yet
        _lead("J", IntroSentAt=_ago(6), FollowUp1SentAt=_ago(1)),                            # follow-up 1 yesterday: follow-up 2 due TOMORROW
    ]
    assert S.due_counts(leads, stages) == (3, 2)                                             # now: A, B, H · tomorrow: C, J


def test_two_rows_for_the_same_email_count_once_as_the_engine_sends_once():
    leads = [_lead("A", Email="same@x.com"), _lead("B", Email="same@x.com")]
    assert S.due_counts(leads, _stages(0, 2)) == (1, 0)


def test_a_lead_due_now_is_not_counted_again_as_due_tomorrow():
    assert S.due_counts([_lead("B", IntroSentAt=_ago(4))], _stages(0, 2)) == (1, 0)


def test_counting_never_changes_the_leads():
    leads = [_lead("B", IntroSentAt=_ago(3)), _lead("C", IntroSentAt=_ago(1))]
    before = copy.deepcopy(leads)
    S.due_counts(leads, _stages(0, 2, 2))
    assert leads == before


@pytest.mark.parametrize("leads, stages", [([], _stages(0, 2)), ([_lead("A")], []), ([], []), (None, _stages(0))])
def test_nothing_to_count_gives_zero_zero(leads, stages):
    assert S.due_counts(leads, stages) == (0, 0)


def test_the_campaigns_time_zone_is_passed_to_the_engine():
    assert S.due_counts([_lead("A"), _lead("B", IntroSentAt=_ago(10))], _stages(0, 2), "America/Chicago") == (2, 0)


# ================================================================ today and this week

@pytest.mark.parametrize("sent, limit, fraction", [(41, 100, 0.41), (0, 100, 0.0), (100, 100, 1.0), (150, 100, 1.0), (5, 0, 0.0), (5, None, 0.0), (-3, 10, 0.0), (1, 3, 1 / 3)])
def test_the_progress_bar_is_the_share_of_the_daily_limit_used_and_never_more_than_all_of_it(sent, limit, fraction):
    assert S.progress_fraction(sent, limit) == fraction


def _sent(stamp, status="sent"):
    return {"Timestamp": stamp, "Status": status}


WEEK_LOG = [_sent("2026-10-12 15:00:00"), _sent("2026-10-12 16:00:00"), _sent("2026-10-12 17:00:00"), _sent("2026-10-13 14:00:00"), _sent("2026-10-13 15:00:00"),
            _sent("2026-10-13 15:30:00", "failed"), _sent("not a date"), {"Status": "sent"}, _sent("2026-10-11 15:00:00")]


def test_the_week_lists_each_send_day_from_monday_with_a_dash_for_days_that_have_not_happened():
    rows = S.week_rows(WEEK_LOG, "America/Chicago", NOW, ["mon", "tue", "wed", "thu", "fri"], 4)
    assert [(r["day"], r["count"]) for r in rows] == [("Mon", 3), ("Tue", 2), ("Wed", None), ("Thu", None), ("Fri", None)]
    assert [r["fraction"] for r in rows] == [0.75, 0.5, 0.0, 0.0, 0.0]                           # against a limit of 4; failed sends, bad dates and last Sunday are not counted


def test_without_a_limit_the_bars_are_against_the_busiest_day_and_a_zero_day_in_the_past_shows_zero_not_a_dash():
    rows = S.week_rows(WEEK_LOG[:5], "America/Chicago", datetime(2026, 10, 15, 14, 0, tzinfo=UTC), ["mon", "tue", "wed", "thu", "fri"], None)         # now: Thursday
    assert [(r["day"], r["count"]) for r in rows] == [("Mon", 3), ("Tue", 2), ("Wed", 0), ("Thu", 0), ("Fri", None)]
    assert [round(r["fraction"], 3) for r in rows] == [1.0, 0.667, 0.0, 0.0, 0.0]


@pytest.mark.parametrize("stamp, counted_days", [("2026-10-13 04:30:00", ["Mon"]), ("2026-10-13 05:00:00", ["Tue"]), ("2026-10-12 04:59:59", [])])
def test_a_send_belongs_to_the_day_it_happened_in_the_campaigns_own_zone(stamp, counted_days):
    """04:30 UTC on the 13th is still Monday evening in Chicago; 05:00 UTC is midnight, so Tuesday; 04:59 UTC on the 12th is Sunday night — last week, not shown."""
    rows = S.week_rows([_sent(stamp)], "America/Chicago", NOW, ["mon", "tue", "wed", "thu", "fri"], 10)
    assert [r["day"] for r in rows if r["count"]] == counted_days


def test_a_day_that_is_not_a_send_day_appears_only_if_it_actually_had_sends():
    log = [_sent("2026-10-17 15:00:00")]                                                                                                    # a Saturday
    saturday_now = datetime(2026, 10, 17, 20, 0, tzinfo=UTC)
    assert "Sat" in [r["day"] for r in S.week_rows(log, "America/Chicago", saturday_now, ["mon", "tue", "wed", "thu", "fri"], 10)]
    assert "Sat" not in [r["day"] for r in S.week_rows([], "America/Chicago", saturday_now, ["mon", "tue", "wed", "thu", "fri"], 10)]


def test_an_empty_log_gives_zero_for_past_days_and_dashes_for_the_future():
    rows = S.week_rows([], None, NOW, ["mon", "tue", "wed"], 10)
    assert [(r["day"], r["count"], r["fraction"]) for r in rows] == [("Mon", 0, 0.0), ("Tue", 0, 0.0), ("Wed", None, 0.0)]


# ================================================================ follow-up waits, as they are edited

def _stage_list(*waits):
    names = ["intro"] + [f"followup{i}" for i in range(1, len(waits))]
    return [{"name": n, "template_prefix": n, "wait_days_after_previous": w} for n, w in zip(names, waits)]


@pytest.mark.parametrize("wait, after, text", [(0, "the intro", "Same day as the intro"), (1, "the intro", "1 day after the intro"), (2, "follow-up 1", "2 days after follow-up 1"), (90, "follow-up 9", "90 days after follow-up 9"),
                                               (0, "follow-up 2", "Same day as follow-up 2")])
def test_a_wait_is_put_in_words_with_what_it_counts_from(wait, after, text):
    assert S.sends_on_text(wait, after) == text


def test_each_follow_up_is_a_row_with_its_saved_wait_and_what_it_counts_from_and_the_intro_is_not_one():
    assert S.wait_edit_rows(_stage_list(0, 2, 3, 4)) == [
        {"prefix": "followup1", "label": "Follow-up 1", "saved": 2, "after": "the intro"},
        {"prefix": "followup2", "label": "Follow-up 2", "saved": 3, "after": "follow-up 1"},
        {"prefix": "followup3", "label": "Follow-up 3", "saved": 4, "after": "follow-up 2"}]


@pytest.mark.parametrize("wait", [None, "2", -1, True, 2.5, 91, [2]])
def test_a_wait_that_is_not_a_usable_whole_number_of_days_is_offered_as_not_saved_rather_than_guessed(wait):
    stages = [{"name": "intro", "template_prefix": "intro"}, {"name": "followup1", "template_prefix": "followup1", "wait_days_after_previous": wait}]
    assert S.wait_edit_rows(stages)[0]["saved"] is None


@pytest.mark.parametrize("wait", [0, 1, 45, 90])
def test_zero_to_ninety_are_all_usable_saved_waits(wait):
    assert S.wait_edit_rows(_stage_list(0, wait))[0]["saved"] == wait


def test_a_campaign_with_only_an_intro_has_no_rows():
    assert S.wait_edit_rows(_stage_list(0)) == [] and S.wait_edit_rows([]) == [] and S.wait_edit_rows(None) == []


def test_the_timeline_adds_the_waits_up_so_each_follow_up_has_its_day_counted_from_the_intro():
    rows = S.wait_edit_rows(_stage_list(0, 2, 5, 1))
    described = S.describe_waits(rows, {"followup1": 2, "followup2": 5, "followup3": 1})
    assert [(d["label"], d["wait"], d["day"]) for d in described] == [("Follow-up 1", 2, 2), ("Follow-up 2", 5, 7), ("Follow-up 3", 1, 8)]
    assert [d["sends_on"] for d in described] == ["2 days after the intro", "5 days after follow-up 1", "1 day after follow-up 2"]


def test_the_timeline_follows_what_is_being_typed_not_what_was_saved():
    rows = S.wait_edit_rows(_stage_list(0, 2, 2))
    assert [d["day"] for d in S.describe_waits(rows, {"followup1": 10, "followup2": 0})] == [10, 10]


def test_a_follow_up_with_no_edited_value_uses_its_saved_wait_and_a_never_saved_one_counts_as_zero():
    rows = S.wait_edit_rows([{"template_prefix": "intro"}, {"template_prefix": "followup1", "wait_days_after_previous": 3}, {"template_prefix": "followup2"}])
    assert [d["wait"] for d in S.describe_waits(rows, {})] == [3, 0]


def test_only_the_waits_that_differ_from_the_saved_ones_are_changes():
    rows = S.wait_edit_rows(_stage_list(0, 2, 3, 4))
    assert S.changed_waits(rows, {"followup1": 2, "followup2": 3, "followup3": 4}) == []
    assert S.changed_waits(rows, {"followup1": 2, "followup2": 9, "followup3": 1}) == [{"label": "Follow-up 2", "was": 3, "now": 9}, {"label": "Follow-up 3", "was": 4, "now": 1}]


def test_a_row_with_nothing_saved_is_only_a_change_once_it_differs_from_zero_the_engines_own_default():
    rows = S.wait_edit_rows([{"template_prefix": "intro"}, {"template_prefix": "followup1"}])
    assert S.changed_waits(rows, {"followup1": 0}) == [] and S.changed_waits(rows, {"followup1": 3}) == [{"label": "Follow-up 1", "was": None, "now": 3}]


def test_a_follow_up_missing_from_the_edited_values_is_not_a_change():
    assert S.changed_waits(S.wait_edit_rows(_stage_list(0, 2, 3)), {"followup1": 5}) == [{"label": "Follow-up 1", "was": 2, "now": 5}]


def test_only_a_shorter_wait_counts_as_shortened_and_a_wait_that_had_nothing_saved_never_does():
    changes = [{"label": "a", "was": 5, "now": 2}, {"label": "b", "was": 2, "now": 5}, {"label": "c", "was": 3, "now": 3}, {"label": "d", "was": None, "now": 0}, {"label": "e", "was": 1, "now": 0}]
    assert [c["label"] for c in S.shortened(changes)] == ["a", "e"]
