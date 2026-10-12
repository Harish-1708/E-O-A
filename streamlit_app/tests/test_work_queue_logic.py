"""Tests for the Today page's logic (work_queue_logic.py).

The property that matters most: the page must NEVER disagree with what
Auto Send will do. So the central test (test_page_numbers_always_match_
the_engines_own_eligibility_rule) throws hundreds of randomly generated,
deliberately messy leads at both and requires identical answers; the
rest pin the individual behaviours a person relies on when they read it.
"""
import os
import random
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import work_queue_logic as w  # noqa: E402  (puts the repo root on sys.path, so `outreach` imports below)
import outreach  # noqa: E402

FMT = "%Y-%m-%d %H:%M:%S"
NY = "America/New_York"
NY_SCHEDULE = {"timezone": NY, "window_start": "9:00", "window_end": "17:00",
               "send_days": ["mon", "tue", "wed", "thu", "fri"]}


# ---------------------------------------------------------------- clock helpers

@pytest.fixture
def utc_machine():
    """The engine reads the machine's own clock; on GitHub Actions and on
    Streamlit Cloud that is UTC. Pinned so the tests mean the same on any
    computer."""
    import time as _time
    old = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    _time.tzset()
    yield
    if old is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = old
    _time.tzset()


class _Clock(datetime):
    _utc = None

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return cls._utc
        return cls._utc.replace(tzinfo=timezone.utc).astimezone(tz)


@pytest.fixture
def at(monkeypatch, utc_machine):
    """at("2026-10-09 13:00") sets the engine's clock (naive UTC) and returns
    the same instant as an aware datetime to hand to the logic."""
    monkeypatch.setattr(outreach, "datetime", _Clock)

    def _set(utc_text):
        _Clock._utc = datetime.strptime(utc_text, "%Y-%m-%d %H:%M")
        return _Clock._utc.replace(tzinfo=timezone.utc)
    return _set


def utc(text):
    return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- data helpers

def sent_field(stage_index):
    return outreach.stage_field_names(stage_index)["sent_at"]


def make_cfg(status="active", schedule=NY_SCHEDULE, wait=2, daily_limit=200, stages=3):
    names = ["intro"] + [f"followup{i}" for i in range(1, stages)]
    return {"_campaign_name": "Camp", "status": status, "schedule": dict(schedule) if schedule else {},
            "sending": {"daily_limit": daily_limit},
            "stages": [{"name": n, "template_prefix": n, "wait_days_after_previous": 0 if i == 0 else wait}
                       for i, n in enumerate(names)]}


def make_lead(i=1, **fields):
    lead = {"_row": i + 1, "LeadID": str(i), "FirstName": "Pat", "LastName": f"L{i}", "Email": f"p{i}@x.com",
            "Status": "", "ReplyStatus": ""}
    lead.update(fields)
    return lead


# ================================================================ next_window_open

@pytest.mark.parametrize("now, expected_utc", [
    ("2026-10-09 15:00", None),                  # Fri 11:00 AM New York — open right now
    ("2026-10-09 22:00", "2026-10-12 13:00"),    # Fri 6:00 PM after close -> MONDAY 9:00 AM New York
    ("2026-10-10 15:00", "2026-10-12 13:00"),    # Saturday -> Monday
    ("2026-10-11 15:00", "2026-10-12 13:00"),    # Sunday -> Monday
    ("2026-10-12 11:00", "2026-10-12 13:00"),    # Monday 7:00 AM, before open -> TODAY at 9:00
    ("2026-10-12 21:30", "2026-10-13 13:00"),    # Monday after close -> Tuesday
    ("2026-10-31 15:00", "2026-11-02 14:00"),    # Saturday -> Monday 9:00 AM: DST has ended, so EST (14:00 UTC), not EDT
])
def test_next_window_open(now, expected_utc):
    result = w.next_window_open(NY_SCHEDULE, utc(now))
    assert (result.strftime("%Y-%m-%d %H:%M") if result else None) == expected_utc


def test_next_window_open_is_none_when_there_is_nothing_to_wait_for():
    now = utc("2026-10-10 15:00")
    assert w.next_window_open({}, now) is None
    assert w.next_window_open(None, now) is None
    assert w.next_window_open({"window_start": "9:00", "window_end": "17:00"}, now) is None   # no timezone = always allowed


def test_next_window_open_never_raises_on_a_bad_schedule():
    now = utc("2026-10-10 15:00")
    assert w.next_window_open({**NY_SCHEDULE, "timezone": "Not/AZone"}, now) is None
    assert w.next_window_open({**NY_SCHEDULE, "window_start": "nine"}, now) is None


def test_next_window_open_with_no_send_day_restriction_is_simply_tomorrows_start():
    schedule = {"timezone": NY, "window_start": "9:00", "window_end": "17:00"}
    assert w.next_window_open(schedule, utc("2026-10-10 22:00")).strftime("%Y-%m-%d %H:%M") == "2026-10-11 13:00"


# ================================================================ describe_moment

def test_describe_moment_shows_the_campaign_time_and_the_teams_time():
    assert w.describe_moment(utc("2026-10-12 13:00"), NY) == "Mon 12 Oct, 9:00 AM EDT · 6:30 PM IST"


def test_describe_moment_repeats_the_date_when_it_differs_between_the_two():
    # 9 PM Friday in New York is already Saturday morning in India — a bare time there would mislead.
    assert w.describe_moment(utc("2026-10-10 01:00"), NY) == "Fri 09 Oct, 9:00 PM EDT · Sat 10 Oct, 6:30 AM IST"


def test_describe_moment_does_not_repeat_itself_when_both_are_the_same_timezone():
    assert w.describe_moment(utc("2026-10-12 13:00"), "Asia/Kolkata") == "Mon 12 Oct, 6:30 PM IST"


def test_describe_moment_falls_back_to_utc_for_an_unknown_or_missing_timezone():
    assert w.format_moment(utc("2026-10-12 13:00"), "Not/AZone").endswith("UTC")
    assert w.format_moment(utc("2026-10-12 13:00"), None).endswith("UTC")


# ================================================================ not_in_play_label

STAGES3 = make_cfg()["stages"]


@pytest.mark.parametrize("fields, expected", [
    ({"Email": ""}, w.LABEL_NO_EMAIL),
    ({"Email": "   "}, w.LABEL_NO_EMAIL),
    ({"Status": "Stopped - Replied"}, w.LABEL_REPLIED),
    ({"Status": "Stopped - Bounced"}, w.LABEL_BOUNCED),
    ({"Status": "Stopped - Rejected"}, w.LABEL_DECLINED),
    ({"Status": "Stopped - Manual"}, w.LABEL_STOPPED_MANUALLY),
    ({"Status": "Paused"}, w.LABEL_PAUSED),
    ({"Status": "Removed"}, w.LABEL_REMOVED),
    ({"Status": "Completed"}, w.LABEL_COMPLETED),
    ({"ReplyStatus": "Replied"}, w.LABEL_REPLIED),
    ({sent_field(2): "2026-10-01 10:00:00"}, w.LABEL_COMPLETED),     # sent the LAST stage
    ({sent_field(0): "2026-10-01 10:00:00"}, None),                  # mid-sequence: still in play
    ({}, None),
])
def test_not_in_play_label(fields, expected):
    assert w.not_in_play_label(make_lead(**fields), STAGES3) == expected


def test_not_in_play_checks_run_in_a_fixed_order_so_each_lead_gets_exactly_one_label():
    assert w.not_in_play_label(make_lead(Email="", Status="Stopped - Bounced"), STAGES3) == w.LABEL_NO_EMAIL
    assert w.not_in_play_label(make_lead(Status="Removed", ReplyStatus="Replied"), STAGES3) == w.LABEL_REMOVED


def test_every_status_the_engine_treats_as_terminal_has_a_label():
    """If a new terminal status is ever added to the engine, this fails
    until the page labels it — otherwise such leads would silently count as
    'in the sequence'."""
    unlabelled = outreach.TERMINAL_STATUSES - set(w._STATUS_LABELS)
    assert unlabelled == set()


# ================================================================ build_campaign_queue

def _intro_sent(day_time):
    return {sent_field(0): day_time}


def test_due_today_on_the_morning_of_the_due_date_before_the_time_of_day_it_was_sent(at):
    """The scenario that confused everyone: sent Wed 10:00 AM NY with a 2-day
    wait is due all of Friday — so at 9:00 AM Friday the page says due."""
    now = at("2026-10-09 13:00")                       # Fri 9:00 AM New York
    queue = w.build_campaign_queue(make_cfg(), [make_lead(**_intro_sent("2026-10-07 14:00:00"))], now_utc=now)
    row = queue["stages"][1]
    assert (row["stage"], row["due_now"], row["waiting"]) == ("followup1", 1, 0)
    assert queue["due_total"] == 1 and queue["window"]["open"] is True
    assert w.summarize_queue(queue).startswith("1 lead is due and sending is open")


def test_the_day_before_it_is_waiting_with_the_date_it_falls_due(at):
    now = at("2026-10-08 15:00")                       # Thu
    queue = w.build_campaign_queue(make_cfg(), [make_lead(**_intro_sent("2026-10-07 14:00:00"))], now_utc=now)
    row = queue["stages"][1]
    assert (row["due_now"], row["waiting"], row["next_due_text"]) == (0, 1, "Fri 09 Oct")
    assert queue["next_due_text"] == "Fri 09 Oct"
    assert w.summarize_queue(queue) == "Nothing is due right now. The next lead falls due on Fri 09 Oct."


def test_the_next_due_date_is_counted_in_the_campaign_timezone(at):
    """Sent Tue 6 Oct 10:00 PM New York (already Wed 7 Oct in UTC), wait 2:
    due Thu 8 Oct New York — not Fri 9 Oct as the UTC calendar would say."""
    now = at("2026-10-07 15:00")
    queue = w.build_campaign_queue(make_cfg(), [make_lead(**_intro_sent("2026-10-07 02:00:00"))], now_utc=now)
    assert queue["stages"][1]["next_due_text"] == "Thu 08 Oct"


def test_next_due_is_the_earliest_of_several_waiting_leads(at):
    now = at("2026-10-08 15:00")
    leads = [make_lead(1, **_intro_sent("2026-10-08 14:00:00")),     # due Sat 10
             make_lead(2, **_intro_sent("2026-10-07 14:00:00"))]     # due Fri 9  <- earliest
    queue = w.build_campaign_queue(make_cfg(), leads, now_utc=now)
    assert queue["stages"][1]["waiting"] == 2 and queue["stages"][1]["next_due_text"] == "Fri 09 Oct"


def test_a_follow_up_due_on_a_weekend_shows_the_window_closed_and_when_it_reopens(at):
    now = at("2026-10-10 15:00")                       # Saturday 11:00 AM New York
    queue = w.build_campaign_queue(make_cfg(), [make_lead(**_intro_sent("2026-10-08 14:00:00"))], now_utc=now)
    assert queue["due_total"] == 1                      # due Saturday — a due date never expires
    assert queue["window"]["open"] is False
    assert "send_days" in queue["window"]["reason"]
    assert queue["window"]["next_open"] == "Mon 12 Oct, 9:00 AM EDT · 6:30 PM IST"
    assert w.summarize_queue(queue) == ("1 lead is due. They will go out when sending next opens: "
                                         "Mon 12 Oct, 9:00 AM EDT · 6:30 PM IST.")


def test_the_sending_window_is_judged_at_the_moment_given_not_at_the_machine_clock(at):
    """The machine clock says Friday 11 AM (window open); the moment handed
    in is Saturday. The window must follow the moment handed in — the other
    tests set both to the same instant, which could not tell the two apart."""
    at("2026-10-09 15:00")                                   # the machine clock: Friday, open
    saturday = utc("2026-10-10 15:00")
    queue = w.build_campaign_queue(make_cfg(), [make_lead(1)], now_utc=saturday)
    assert queue["window"]["open"] is False
    friday = utc("2026-10-09 15:00")
    assert w.build_campaign_queue(make_cfg(), [make_lead(1)], now_utc=friday)["window"]["open"] is True


def test_intro_stage_is_due_for_unsent_leads_and_never_waiting(at):
    now = at("2026-10-09 15:00")
    queue = w.build_campaign_queue(make_cfg(), [make_lead(1), make_lead(2)], now_utc=now)
    intro = queue["stages"][0]
    assert (intro["due_now"], intro["waiting"]) == (2, 0)
    assert [r["Email"] for r in intro["due_leads"]] == ["p1@x.com", "p2@x.com"]
    assert intro["due_leads"][0]["Name"] == "Pat L1"


def test_a_lead_with_an_unreadable_previous_timestamp_is_counted_waiting_without_crashing(at):
    now = at("2026-10-09 15:00")
    queue = w.build_campaign_queue(make_cfg(), [make_lead(**_intro_sent("garbage"))], now_utc=now)
    row = queue["stages"][1]
    assert (row["due_now"], row["waiting"], row["next_due_text"]) == (0, 1, "")


def test_the_daily_budget_counts_todays_sends_and_never_goes_negative(at):
    now = at("2026-10-09 15:00")
    today = "2026-10-09 12:00:00"
    leads = [make_lead(i, **{sent_field(0): today}) for i in range(1, 4)]
    queue = w.build_campaign_queue(make_cfg(daily_limit=5), leads, now_utc=now)
    assert (queue["sent_today"], queue["daily_limit"], queue["remaining_today"]) == (3, 5, 2)
    assert w.build_campaign_queue(make_cfg(daily_limit=2), leads, now_utc=now)["remaining_today"] == 0   # over the limit
    no_limit_cfg = make_cfg()
    no_limit_cfg["sending"] = {}
    assert w.build_campaign_queue(no_limit_cfg, leads, now_utc=now)["remaining_today"] is None


def test_a_lead_who_replied_is_out_of_play_and_never_due(at):
    now = at("2026-10-09 13:00")
    leads = [make_lead(1, ReplyStatus="Replied", **_intro_sent("2026-10-07 14:00:00")),
             make_lead(2, Status="Stopped - Bounced", **_intro_sent("2026-10-07 14:00:00")),
             make_lead(3, Email=""),
             make_lead(4, **_intro_sent("2026-10-07 14:00:00"))]
    queue = w.build_campaign_queue(make_cfg(), leads, now_utc=now)
    assert queue["due_total"] == 1
    assert queue["out_of_play"][w.LABEL_REPLIED] == 1
    assert queue["out_of_play"][w.LABEL_BOUNCED] == 1
    assert queue["out_of_play"][w.LABEL_NO_EMAIL] == 1
    assert queue["in_sequence"] == 1


def test_a_long_due_list_is_capped_and_says_how_many_are_not_shown(at, monkeypatch):
    monkeypatch.setattr(w, "MAX_LEADS_LISTED", 3)
    now = at("2026-10-09 15:00")
    queue = w.build_campaign_queue(make_cfg(), [make_lead(i) for i in range(1, 8)], now_utc=now)
    intro = queue["stages"][0]
    assert intro["due_now"] == 7 and len(intro["due_leads"]) == 3 and intro["due_leads_hidden"] == 4


def test_a_bad_schedule_does_not_hide_the_rest_of_the_page(at):
    now = at("2026-10-09 15:00")
    cfg = make_cfg(schedule={"timezone": "Not/AZone", "window_start": "9:00", "window_end": "17:00"})
    queue = w.build_campaign_queue(cfg, [make_lead(1)], now_utc=now)
    assert queue["window"]["open"] is None and "could not be read" in queue["window"]["reason"]
    assert queue["due_total"] == 1                                    # the counts are still there
    assert "could not be read" in w.summarize_queue(queue)


def test_a_campaign_with_no_schedule_is_always_open(at):
    now = at("2026-10-10 03:00")                                      # 3 AM Saturday
    queue = w.build_campaign_queue(make_cfg(schedule=None), [make_lead(1)], now_utc=now)
    assert queue["window"]["open"] is True


def test_when_the_daily_limit_is_used_up_the_sentence_says_so(at):
    now = at("2026-10-09 15:00")
    leads = [make_lead(1, **{sent_field(0): "2026-10-09 12:00:00"}), make_lead(2)]
    queue = w.build_campaign_queue(make_cfg(daily_limit=1), leads, now_utc=now)
    assert "daily limit is already used up" in w.summarize_queue(queue)


@pytest.mark.parametrize("now_text", ["2026-10-06 03:00", "2026-10-08 13:30", "2026-10-09 04:00", "2026-10-10 15:00",
                                      "2026-10-12 13:00", "2026-10-14 22:45"])
def test_page_numbers_always_match_the_engines_own_eligibility_rule(at, now_text):
    """THE property. 400 random, deliberately messy leads: duplicate emails
    in different casing, blank emails, every status, replies, holes in the
    sequence, unparseable timestamps. For every stage the page's 'due now'
    must equal the engine's eligible list exactly, and 'due + waiting' must
    equal the engine's list with the wait ignored."""
    rng = random.Random(20261009)
    cfg = make_cfg(stages=4, wait=rng.choice([1, 2, 3]))
    stages = cfg["stages"]
    leads = []
    for i in range(400):
        lead = make_lead(i, Email=rng.choice(["", f"p{i}@x.com", f"dup{i % 9}@x.com", f"DUP{i % 9}@X.com"]),
                         Status=rng.choice(["", "", "", "Intro Sent", "Stopped - Bounced", "Paused", "Removed", "Completed",
                                            "Stopped - Manual", "Stopped - Replied"]),
                         ReplyStatus=rng.choice(["", "", "", "Replied"]))
        base = datetime(2026, 10, 1, 12, 0) + timedelta(minutes=rng.randint(0, 60 * 24 * 8))
        for k in range(rng.randint(0, 3)):
            lead[sent_field(k)] = (base + timedelta(days=k * rng.randint(1, 3), minutes=rng.randint(0, 900))).strftime(FMT)
        if rng.random() < 0.06:
            lead[sent_field(0)] = "garbage"
        if rng.random() < 0.06:
            lead[sent_field(1)] = ""
        leads.append(lead)

    now = at(now_text)
    queue = w.build_campaign_queue(cfg, leads, now_utc=now)
    for index, row in enumerate(queue["stages"]):
        eligible = outreach.get_eligible_leads(leads, stages, index, timezone_name=NY)
        no_wait = outreach.get_eligible_leads(leads, stages, index, ignore_wait_days=True, timezone_name=NY)
        assert row["due_now"] == len(eligible), (now_text, row["stage"])
        assert row["due_now"] + row["waiting"] == len(no_wait), (now_text, row["stage"])
        assert [d["Email"] for d in row["due_leads"]] == [(l["Email"] or "").strip() for l in eligible][:w.MAX_LEADS_LISTED]
    assert sum(queue["out_of_play"].values()) + queue["in_sequence"] == len(leads)     # every lead counted exactly once
    assert queue["total_leads"] == len(leads)


@pytest.mark.parametrize("due_row_first", [True, False])
def test_one_email_address_is_one_person_even_when_two_rows_are_in_different_states(at, due_row_first):
    """Found by the random parity test: the same address on two rows, one
    due and one still inside its wait, used to be counted twice (due AND
    waiting). The engine de-duplicates by address, so the page must too —
    whichever row happens to come first."""
    now = at("2026-10-09 13:00")
    due_row = make_lead(1, Email="same@x.com", **_intro_sent("2026-10-07 14:00:00"))      # due today
    waiting_row = make_lead(2, Email="Same@X.com", **_intro_sent("2026-10-09 12:00:00"))   # sent this morning
    leads = [due_row, waiting_row] if due_row_first else [waiting_row, due_row]
    queue = w.build_campaign_queue(make_cfg(), leads, now_utc=now)
    assert queue["due_total"] == 1 and queue["waiting_total"] == 0


# ================================================================ summarize_queue

def _queue(due=0, waiting=0, next_due_text="", open_=True, next_open="", remaining=10):
    return {"due_total": due, "waiting_total": waiting, "next_due_text": next_due_text, "remaining_today": remaining,
            "window": {"open": open_, "reason": "", "next_open": next_open}}


@pytest.mark.parametrize("queue, expected", [
    (_queue(), "Nothing is due, and nobody is waiting on a follow-up."),
    (_queue(waiting=2), "Nothing is due right now."),
    (_queue(waiting=2, next_due_text="Fri 09 Oct"), "Nothing is due right now. The next lead falls due on Fri 09 Oct."),
    (_queue(due=1), "1 lead is due and sending is open — the next Auto Send run will email them (up to 10 more today)."),
    (_queue(due=5, remaining=None), "5 leads are due and sending is open — the next Auto Send run will email them."),
    (_queue(due=3, open_=False, next_open="Mon 12 Oct, 9:00 AM EDT"), "3 leads are due. They will go out when sending next opens: Mon 12 Oct, 9:00 AM EDT."),
    (_queue(due=3, open_=False), "3 leads are due. They will go out when sending next opens."),
    (_queue(due=2, remaining=0), "2 leads are due, but today's daily limit is already used up — they go out on the next sending day."),
])
def test_summarize_queue(queue, expected):
    assert w.summarize_queue(queue) == expected


# ================================================================ build_all_queues

def _cfg_for(name, status="active"):
    cfg = make_cfg(status=status)
    cfg["_campaign_name"] = name
    return cfg


def test_only_running_campaigns_are_read_and_the_rest_are_listed_not_read(at):
    now = at("2026-10-09 15:00")
    statuses = {"A": "active", "B": "paused", "C": "draft", "D": "deleted", "E": None}
    read = []

    def get_cfg(name):
        cfg = _cfg_for(name, statuses[name] or "active")
        if statuses[name] is None:
            cfg.pop("status")                               # no status at all = active
        return cfg

    def get_leads(cfg):
        read.append(cfg["_campaign_name"])
        return [make_lead(1)]

    queues, not_running, errors = w.build_all_queues(list("ABCDE"), get_cfg, get_leads, now_utc=now)
    assert [q["campaign"] for q in queues] == ["A", "E"]
    assert not_running == [("B", "paused"), ("C", "draft"), ("D", "deleted")]
    assert sorted(read) == ["A", "E"] and errors == []     # a paused campaign's Sheet is never touched


def test_one_campaign_failing_does_not_block_the_others_and_order_is_kept(at):
    now = at("2026-10-09 15:00")

    def get_cfg(name):
        if name == "BadCfg":
            raise RuntimeError("config broke")
        return _cfg_for(name)

    def get_leads(cfg):
        if cfg["_campaign_name"] == "BadLeads":
            raise RuntimeError("sheet unreachable")
        return [make_lead(1)]

    queues, not_running, errors = w.build_all_queues(["First", "BadCfg", "BadLeads", "Last"], get_cfg, get_leads, now_utc=now)
    assert [q["campaign"] for q in queues] == ["First", "Last"]
    assert sorted(errors) == [("BadCfg", "config broke"), ("BadLeads", "sheet unreachable")]


def test_reading_campaigns_at_the_same_time_gives_the_same_answer_as_one_by_one(at):
    now = at("2026-10-09 15:00")
    names = [f"C{i}" for i in range(6)]
    leads_for = lambda cfg: [make_lead(i) for i in range(1, 4)]
    sequential = w.build_all_queues(names, _cfg_for, leads_for, max_workers=1, now_utc=now)
    parallel = w.build_all_queues(names, _cfg_for, leads_for, max_workers=4, now_utc=now)
    assert sequential == parallel
