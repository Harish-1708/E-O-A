"""The campaign header's chips and sub-line: what the settings and logs say, never anything invented."""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))        # the repo root, for the engine

import campaign_header_logic as H  # noqa: E402


@pytest.mark.parametrize("days, expected", [
    (["mon", "tue", "wed", "thu", "fri"], "Mon–Fri"), (["fri"], "Fri"), (["mon", "wed", "fri"], "Mon, Wed, Fri"), (["mon", "tue"], "Mon, Tue"),
    (["mon", "tue", "wed", "fri"], "Mon–Wed, Fri"), (["sat", "sun"], "Sat, Sun"), (["fri", "sat", "sun"], "Fri–Sun"),
    (["mon", "tue", "wed", "thu", "fri", "sat", "sun"], "Every day"), ([], "No days"), (None, "No days"), (["xyz"], "No days"),
    (["FRI", "Mon"], "Mon, Fri"), (["Friday", "monday"], "Mon, Fri"), (["fri", "fri", "fri"], "Fri"), (["sun", "mon", "tue"], "Mon, Tue, Sun"),
])
def test_days_are_summarised_the_way_a_person_would_say_them(days, expected):
    assert H.days_summary(days) == expected


@pytest.mark.parametrize("zone, expected", [("America/Chicago", "Chicago"), ("America/Los_Angeles", "Los Angeles"), ("Asia/Kolkata", "Kolkata"), ("UTC", "UTC"), ("", ""), (None, ""), ("  ", "")])
def test_the_city_comes_from_the_time_zone_name(zone, expected):
    assert H.city_of(zone) == expected


def test_a_campaign_with_no_schedule_says_it_sends_anytime_and_does_not_invent_hours():
    assert H.schedule_summary({}) == "Sends anytime" and H.schedule_summary({"schedule": None}) == "Sends anytime" and H.schedule_summary({"schedule": {}}) == "Sends anytime"


def test_a_schedule_is_shown_as_days_hours_and_city():
    cfg = {"schedule": {"timezone": "America/Chicago", "window_start": "09:00", "window_end": "17:00", "send_days": ["fri"]}}
    assert H.schedule_summary(cfg) == "Fri · 09:00–17:00 · Chicago"
    assert H.schedule_summary({"schedule": {"timezone": "America/Los_Angeles", "window_start": "08:30", "window_end": "18:00", "send_days": ["mon", "tue", "wed", "thu", "fri"]}}) == "Mon–Fri · 08:30–18:00 · Los Angeles"


def test_a_partly_filled_schedule_shows_only_what_is_there_and_defaults_the_days_like_the_schedule_tab_does():
    assert H.schedule_summary({"schedule": {"timezone": "America/Chicago"}}) == "Mon–Fri · Chicago"
    assert H.schedule_summary({"schedule": {"window_start": "09:00", "window_end": "17:00"}}) == "Mon–Fri · 09:00–17:00"
    assert H.schedule_summary({"schedule": {"window_start": "09:00", "timezone": "UTC", "send_days": ["sat"]}}) == "Sat · UTC"


@pytest.mark.parametrize("leads, expected", [
    ([], "No sender yet"), ([{}], "No sender yet"), ([{"SenderAccount": ""}, {"SenderAccount": "  "}], "No sender yet"), ([{"SenderAccount": "sales1"}], "1 sender account"),
    ([{"SenderAccount": "sales1"}, {"SenderAccount": "sales1 "}, {"SenderAccount": ""}], "1 sender account"), ([{"SenderAccount": "a"}, {"SenderAccount": "b"}], "2 sender accounts"),
    ([{"SenderAccount": "a"}, {"SenderAccount": "b"}, {"SenderAccount": "c"}, {"SenderAccount": "a"}], "3 sender accounts"),
])
def test_the_sender_chip_counts_the_accounts_the_leads_really_use(leads, expected):
    assert H.sender_summary(leads) == expected


@pytest.mark.parametrize("cfg, expected", [({"sending": {"daily_limit": 100}}, 100), ({"sending": {"daily_limit": "40"}}, 40), ({"sending": {"daily_limit": 0}}, None), ({"sending": {"daily_limit": -5}}, None),
                                           ({"sending": {"daily_limit": "lots"}}, None), ({"sending": {}}, None), ({}, None), ({"sending": None}, None), ({"sending": {"daily_limit": None}}, None)])
def test_the_daily_limit_is_only_shown_when_it_is_a_real_number(cfg, expected):
    assert H.daily_limit(cfg) == expected


NOW = datetime(2026, 10, 13, 14, 0, tzinfo=timezone.utc)          # 09:00 in Chicago (CDT), 19:30 in Kolkata


def row(stamp, status="sent"):
    return {"Timestamp": stamp, "Status": status}


def test_only_todays_sent_emails_are_counted_in_the_campaigns_own_day():
    log = [row("2026-10-13 05:00:00"), row("2026-10-13 13:59:59"), row("2026-10-12 23:00:00"), row("2026-10-13 06:00:00", "failed"), row("2026-10-13 07:00:00", "SENT")]
    assert H.sent_today(log, "America/Chicago", NOW) == 3                                                   # 00:00 Chicago is 05:00 UTC; the 23:00 UTC row is yesterday there
    assert H.sent_today(log, None, NOW) == 3 and H.sent_today(log, "", NOW) == 3


def test_the_day_changes_with_the_time_zone():
    log = [row("2026-10-13 03:30:00"), row("2026-10-13 12:00:00")]
    assert H.sent_today(log, "America/Chicago", NOW) == 1                       # 03:30 UTC is still yesterday evening in Chicago
    assert H.sent_today(log, "Asia/Kolkata", NOW) == 2                          # 09:00 IST and 17:30 IST are both today
    assert H.sent_today(log, "UTC", NOW) == 2


def test_rows_with_a_bad_or_missing_time_are_not_counted_and_nothing_breaks():
    assert H.sent_today([row("garbled"), row(""), row(None), {"Status": "sent"}, row("2026-10-13T09:00:00")], "UTC", NOW) == 1


def test_an_unknown_time_zone_falls_back_to_utc_instead_of_failing():
    assert H.sent_today([row("2026-10-13 09:00:00")], "Mars/Olympus", NOW) == 1


def test_a_time_without_a_zone_is_treated_as_utc():
    assert H.sent_today([row("2026-10-13 09:00:00")], "UTC", datetime(2026, 10, 13, 14, 0)) == 1


def test_an_empty_log_counts_zero():
    assert H.sent_today([], "UTC", NOW) == 0


def test_the_sub_line_shows_the_limit_only_when_there_is_one():
    cfg = {"sending": {"daily_limit": 100}, "schedule": {"timezone": "UTC"}}
    log = [row("2026-10-13 09:00:00"), row("2026-10-13 10:00:00")]
    assert H.subline(cfg, log, NOW) == "2 sent today · daily limit 100"
    assert H.subline({"schedule": {"timezone": "UTC"}}, log, NOW) == "2 sent today"
    assert H.subline({}, [], NOW) == "0 sent today"


# ================================================================ the Sheet's clock is UTC whatever time zone the server itself runs in

@pytest.fixture
def server_in_auckland(monkeypatch):
    """Runs the test as if the server's own clock were in New Zealand (UTC+13 in October), so a mistake that reads a Sheet time as 'local'
    instead of UTC shows up (the sandbox is itself in UTC, which would hide it)."""
    import time
    monkeypatch.setenv("TZ", "Pacific/Auckland")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def test_a_sheet_time_is_read_as_utc_even_when_the_server_runs_in_another_zone(server_in_auckland):
    # 05:00 UTC on the 13th is 18:00 on the 13th in Auckland: today either way — but 05:00 read as AUCKLAND time is 16:00 UTC on the 12th.
    assert H.sent_today([row("2026-10-13 05:00:00")], "UTC", NOW) == 1
    assert H.sent_today([row("2026-10-12 23:00:00")], "UTC", NOW) == 0


def test_a_now_without_a_zone_is_utc_even_when_the_server_runs_in_another_zone(server_in_auckland):
    naive_now = datetime(2026, 10, 13, 5, 0)                                    # read as Auckland time this would be the 12th, 16:00 UTC
    assert H.sent_today([row("2026-10-13 04:00:00")], "UTC", naive_now) == 1
    assert H.sent_today([row("2026-10-12 20:00:00")], "UTC", naive_now) == 0


# ================================================================ the next automatic send

import outreach  # noqa: E402  (the engine's own window rule is the one used for real)

CHICAGO_FRIDAYS = {"timezone": "America/Chicago", "window_start": "09:00", "window_end": "17:00", "send_days": ["fri"]}


@pytest.mark.parametrize("text, expected", [
    ('    - cron: "43 * * * *"   # :43 past every hour', 43), ("cron: '5 * * * *'", 5), ('cron: "0 * * * *"', 0), ('cron: "59 * * * *"', 59),
    ('cron: "60 * * * *"', None), ('cron: "*/15 * * * *"', None), ('cron: "43 9 * * 1-5"', None), ("no schedule here", None), ("", None), (None, None),
])
def test_the_run_minute_is_read_from_the_workflow_file_only_when_it_is_a_plain_hourly_schedule(text, expected):
    assert H.read_cron_minute(text) == expected


def test_the_real_auto_send_workflow_file_is_understood():
    path = os.path.join(os.path.dirname(__file__), "..", "..", ".github", "workflows", "auto_send.yml")
    with open(path, encoding="utf-8") as handle:
        assert H.read_cron_minute(handle.read()) == 43


@pytest.mark.parametrize("seconds, expected", [(0, "1 min"), (30, "1 min"), (60, "1 min"), (3300, "55 min"), (3599, "59 min"), (3600, "1 h"), (10800, "3 h"), (86399, "23 h"), (86400, "1 d"),
                                               (2 * 86400 + 19 * 3600 + 120, "2 d 19 h"), (-5, "1 min")])
def test_waits_are_shown_in_the_largest_sensible_unit(seconds, expected):
    assert H.humanize_wait(seconds) == expected


def always(schedule, when):
    return True, ""


def test_with_no_schedule_the_next_send_is_simply_the_next_run():
    now = datetime(2026, 10, 13, 14, 10, tzinfo=timezone.utc)
    assert H.next_automatic_send({}, now, 43, outreach.is_within_sending_window) == datetime(2026, 10, 13, 14, 43, tzinfo=timezone.utc)
    assert H.next_automatic_send({}, datetime(2026, 10, 13, 14, 43, tzinfo=timezone.utc), 43, always) == datetime(2026, 10, 13, 15, 43, tzinfo=timezone.utc)    # strictly after now


def test_with_a_schedule_the_next_send_is_the_first_run_inside_the_window_by_the_engines_own_rule():
    tuesday = datetime(2026, 10, 13, 14, 0, tzinfo=timezone.utc)              # a Tuesday: Fridays only, so the next send is Friday morning in Chicago
    when = H.next_automatic_send(CHICAGO_FRIDAYS, tuesday, 43, outreach.is_within_sending_window)
    assert when == datetime(2026, 10, 16, 14, 43, tzinfo=timezone.utc)        # Fri 09:43 CDT
    assert outreach.is_within_sending_window(CHICAGO_FRIDAYS, when)[0] and not outreach.is_within_sending_window(CHICAGO_FRIDAYS, when - timedelta(hours=1))[0]


def test_inside_the_window_the_very_next_run_is_the_answer():
    friday_noon = datetime(2026, 10, 16, 17, 20, tzinfo=timezone.utc)         # Fri 12:20 CDT
    assert H.next_automatic_send(CHICAGO_FRIDAYS, friday_noon, 43, outreach.is_within_sending_window) == datetime(2026, 10, 16, 17, 43, tzinfo=timezone.utc)


def test_after_the_window_closes_it_waits_for_the_next_allowed_day():
    friday_evening = datetime(2026, 10, 16, 23, 0, tzinfo=timezone.utc)       # Fri 18:00 CDT, window closed
    assert H.next_automatic_send(CHICAGO_FRIDAYS, friday_evening, 43, outreach.is_within_sending_window) == datetime(2026, 10, 23, 14, 43, tzinfo=timezone.utc)


def test_a_schedule_that_can_never_send_gives_none_and_a_broken_one_cannot_break_the_page():
    never = {"timezone": "America/Chicago", "window_start": "09:00", "window_end": "17:00", "send_days": ["xyz"]}          # no real day: the engine never allows it
    assert H.next_automatic_send(never, NOW, 43, outreach.is_within_sending_window) is None
    assert H.next_automatic_send(CHICAGO_FRIDAYS, NOW, 43, lambda s, w: (False, "closed")) is None
    def explode(schedule, when):
        raise RuntimeError("bad timezone")
    assert H.next_automatic_send(CHICAGO_FRIDAYS, NOW, 43, explode) is None


@pytest.mark.parametrize("when, tz, expected", [
    (datetime(2026, 10, 13, 19, 43, tzinfo=timezone.utc), "America/Chicago", "today 2:43 PM"), (datetime(2026, 10, 14, 14, 43, tzinfo=timezone.utc), "America/Chicago", "tomorrow 9:43 AM"),
    (datetime(2026, 10, 16, 14, 43, tzinfo=timezone.utc), "America/Chicago", "Fri 9:43 AM"), (datetime(2026, 10, 23, 14, 43, tzinfo=timezone.utc), "America/Chicago", "Oct 23 9:43 AM"),
    (datetime(2026, 10, 13, 0, 5, tzinfo=timezone.utc), "UTC", "today 12:05 AM"), (datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc), None, "today 12:00 PM"),
    (datetime(2026, 10, 14, 2, 0, tzinfo=timezone.utc), "America/Chicago", "today 9:00 PM"), (datetime(2026, 10, 13, 19, 43, tzinfo=timezone.utc), "Mars/Olympus", "today 7:43 PM"),
])
def test_a_moment_is_described_in_the_campaigns_own_time_zone_the_way_a_person_says_it(when, tz, expected):
    assert H.describe_moment(when, NOW, tz) == expected


def test_the_whole_line():
    line = H.next_send_line({"schedule": CHICAGO_FRIDAYS}, datetime(2026, 10, 13, 14, 0, tzinfo=timezone.utc), 43, outreach.is_within_sending_window)
    assert line == "Next automatic send: Fri 9:43 AM (in 3 d)"
    assert H.next_send_line({}, datetime(2026, 10, 13, 14, 0), 43, always) == "Next automatic send: today 2:43 PM (in 43 min)"


def test_no_line_when_the_run_minute_is_unknown_or_nothing_is_due():
    assert H.next_send_line({}, NOW, None, always) is None
    assert H.next_send_line({"schedule": CHICAGO_FRIDAYS}, NOW, 43, lambda s, w: (False, "")) is None


def test_the_sub_line_puts_the_next_send_first_when_there_is_one():
    cfg = {"sending": {"daily_limit": 100}, "schedule": {"timezone": "UTC"}}
    assert H.subline(cfg, [], NOW, "Next automatic send: today 2:43 PM (in 43 min)") == "Next automatic send: today 2:43 PM (in 43 min) · 0 sent today · daily limit 100"
    assert H.subline(cfg, [], NOW) == "0 sent today · daily limit 100"
