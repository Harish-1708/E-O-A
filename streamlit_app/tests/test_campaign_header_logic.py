"""The campaign header's chips and sub-line: what the settings and logs say, never anything invented."""
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

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
