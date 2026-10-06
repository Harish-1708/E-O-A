"""Guards the scheduled-workflow design: WHEN things run, and that they can
never step on each other.

Why this exists: a schedule is easy to get subtly wrong and nothing fails
loudly when it is. GitHub cron is UTC, the intended window is IST 5 PM - 5 AM
(UTC+5:30, so the offset is a half hour), a typo shifts a run outside the
window or off the 30-minute grid without any error, and a wrong concurrency
setting can let two sends overlap (duplicate emails) or kill one mid-send.
These tests expand the REAL cron expressions from the REAL workflow files
into concrete run times, convert them to IST, and assert on those — they do
not just compare strings, so a change that looks different but behaves the
same still passes, and one that looks harmless but isn't fails.

What they cannot prove: how GitHub itself behaves at runtime (delays, queue
replacement, the 60-day inactivity rule). That is documented in the README
and can only be observed on the real Actions tab.
"""
import os

import pytest
import yaml

WORKFLOWS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows"))

# The five workflows that were asked to run every 30 min, IST 5 PM - 5 AM.
SCHEDULED = ["check_replies.yml", "auto_send.yml", "sync_asana.yml", "dashboard.yml", "ugc_tracker_sync.yml"]
# Of those, the ones that write to the campaign Google Sheets and therefore
# share ONE lock so they take turns (UGC has its own sheet and its own lock).
SHARED_LOCK = ["check_replies.yml", "auto_send.yml", "sync_asana.yml", "dashboard.yml"]
SHARED_GROUP = "google-sheets-api"
# Intended order inside each half hour: replies are recorded BEFORE the next
# send decides who is due, and Asana/dashboard reflect both afterwards.
CYCLE_ORDER = ["check_replies.yml", "auto_send.yml", "sync_asana.yml", "dashboard.yml"]

IST_OFFSET_MIN = 5 * 60 + 30
WINDOW_START_MIN = 17 * 60   # 5:00 PM IST
WINDOW_END_MIN = 5 * 60      # 5:00 AM IST (next day)


# ---------------------------------------------------------------- helpers

def _load(name):
    with open(os.path.join(WORKFLOWS_DIR, name)) as handle:
        return yaml.safe_load(handle)


def _triggers(workflow):
    # PyYAML (YAML 1.1) reads the bare key `on` as the boolean True.
    return workflow.get("on", workflow.get(True))


def _expand_field(field, lo, hi):
    values = set()
    for part in field.split(","):
        step = 1
        has_step = "/" in part
        if has_step:
            part, step_text = part.split("/")
            step = int(step_text)
        if part == "*":
            start, end = lo, hi
        elif "-" in part:
            start, end = (int(x) for x in part.split("-"))
        else:
            start = int(part)
            end = hi if has_step else start
        values |= set(range(start, end + 1, step))
    assert all(lo <= v <= hi for v in values), f"cron field '{field}' out of range {lo}-{hi}"
    return values


def cron_utc_minutes_of_day(expression):
    """Every (hour*60+minute) UTC a 5-field cron fires at, per day. Only
    handles day-of-month/month/day-of-week = '*' — anything else raises so
    this can never silently mis-evaluate a restricted schedule."""
    fields = expression.split()
    assert len(fields) == 5, f"not a 5-field cron: {expression!r}"
    minute, hour, dom, month, dow = fields
    if (dom, month, dow) != ("*", "*", "*"):
        raise ValueError(f"restricted day/month/weekday cron not supported by this test: {expression!r}")
    return {h * 60 + m for h in _expand_field(hour, 0, 23) for m in _expand_field(minute, 0, 59)}


def to_ist(utc_minute_of_day):
    return (utc_minute_of_day + IST_OFFSET_MIN) % (24 * 60)


def in_ist_window(ist_minute):
    return ist_minute >= WINDOW_START_MIN or ist_minute <= WINDOW_END_MIN


def schedule_ist_minutes(workflow):
    """All run times (minute of day, IST) from every cron line, with a check
    that no two lines list the same time (an accidental duplicate)."""
    crons = [entry["cron"] for entry in (_triggers(workflow).get("schedule") or [])]
    assert crons, "workflow has no schedule"
    seen = set()
    for expression in crons:
        times = cron_utc_minutes_of_day(expression)
        assert not (times & seen), f"cron lines overlap each other: {crons}"
        seen |= times
    return sorted(to_ist(t) for t in seen)


def window_order(ist_minutes):
    """Sorted as they occur through the night, starting at 5:00 PM IST."""
    return sorted(ist_minutes, key=lambda m: (m - WINDOW_START_MIN) % (24 * 60))


def offset_in_half_hour(workflow):
    minutes = schedule_ist_minutes(workflow)
    offsets = {m % 30 for m in minutes}
    assert len(offsets) == 1, f"runs are not on a single 30-minute grid: {sorted(offsets)}"
    return offsets.pop()


# ------------------------------------------------ the checker checks itself

def test_cron_expander_handles_the_forms_used_here():
    assert len(cron_utc_minutes_of_day("7,37 12-22 * * *")) == 22
    assert len(cron_utc_minutes_of_day("*/30 * * * *")) == 48
    assert cron_utc_minutes_of_day("37 11 * * *") == {11 * 60 + 37}


def test_cron_expander_refuses_schedules_it_cannot_evaluate_honestly():
    with pytest.raises(ValueError):
        cron_utc_minutes_of_day("7 12 * * 1-5")   # weekday-restricted


def test_utc_to_ist_conversion_matches_the_known_window_edges():
    assert to_ist(11 * 60 + 30) == 17 * 60          # 11:30 UTC is 5:00 PM IST
    assert to_ist(23 * 60 + 30) == 5 * 60           # 23:30 UTC is 5:00 AM IST
    assert to_ist(23 * 60 + 45) == 5 * 60 + 15      # ...and just past the window


def test_window_check_rejects_the_old_around_the_clock_schedules():
    """Negative control: if this passed 24/7 schedules, every test below
    would be meaningless."""
    for old in ("5,35 * * * *", "*/10 * * * *", "*/30 * * * *", "25 */6 * * *"):
        assert not all(in_ist_window(to_ist(t)) for t in cron_utc_minutes_of_day(old)), old


# ---------------------------------------------------- the real schedules

@pytest.mark.parametrize("name", SCHEDULED)
def test_runs_every_30_minutes_and_only_between_5pm_and_5am_ist(name):
    minutes = schedule_ist_minutes(_load(name))
    assert len(minutes) == 24, f"expected 24 runs a day, got {len(minutes)}"
    assert all(in_ist_window(m) for m in minutes), \
        f"a run falls outside 5 PM - 5 AM IST: {[f'{m // 60:02d}:{m % 60:02d}' for m in minutes if not in_ist_window(m)]}"
    ordered = window_order(minutes)
    gaps = {(b - a) % (24 * 60) for a, b in zip(ordered, ordered[1:])}
    assert gaps == {30}, f"runs are not exactly 30 minutes apart: {sorted(gaps)}"


@pytest.mark.parametrize("name", SCHEDULED)
def test_the_whole_window_is_covered_with_no_gap_at_either_edge(name):
    ordered = window_order(schedule_ist_minutes(_load(name)))
    first, last = (ordered[0] - WINDOW_START_MIN) % (24 * 60), (ordered[-1] - WINDOW_START_MIN) % (24 * 60)
    assert 0 <= first < 30, "first run should start within the first half hour of 5 PM IST"
    assert 11 * 60 + 30 <= last <= 12 * 60, "last run should land between 4:30 AM and 5:00 AM IST"


@pytest.mark.parametrize("name", SCHEDULED)
def test_runs_avoid_the_top_of_the_hour_and_half_hour(name):
    """GitHub delays (and can drop) scheduled runs under load, worst at the
    start of the hour — so these are deliberately kept off :00 and :30."""
    assert offset_in_half_hour(_load(name)) != 0


def test_each_half_hour_runs_in_the_intended_order():
    offsets = [offset_in_half_hour(_load(n)) for n in CYCLE_ORDER]
    assert offsets == sorted(offsets) and len(set(offsets)) == len(offsets), \
        f"expected strictly increasing offsets in order {CYCLE_ORDER}, got {offsets}"


def test_no_two_workflows_that_share_the_lock_start_in_the_same_minute():
    offsets = [offset_in_half_hour(_load(n)) for n in SHARED_LOCK]
    assert len(set(offsets)) == len(offsets), \
        "two lock-sharing workflows start in the same minute, so one would always have to wait"


# ------------------------------------------------- locks and run lengths

@pytest.mark.parametrize("name", SHARED_LOCK)
def test_sheet_writing_workflows_share_one_lock_and_never_cancel_a_running_job(name):
    """They write the same Sheet columns (sending and reply-checking both
    write Status), so they must take turns. Cancelling a RUNNING send would
    stop it mid-round — an email could go out without being recorded."""
    concurrency = _load(name)["concurrency"]
    assert concurrency["group"] == SHARED_GROUP
    assert concurrency["cancel-in-progress"] is False


def test_ugc_tracker_keeps_its_own_separate_lock():
    concurrency = _load("ugc_tracker_sync.yml")["concurrency"]
    assert concurrency["group"] != SHARED_GROUP
    assert concurrency["cancel-in-progress"] is False


def _job_timeout(name):
    jobs = _load(name)["jobs"]
    assert len(jobs) == 1
    return next(iter(jobs.values())).get("timeout-minutes")


@pytest.mark.parametrize("name", ["check_replies.yml", "sync_asana.yml", "dashboard.yml"])
def test_short_jobs_that_hold_the_shared_lock_have_a_timeout(name):
    """They hold the lock that auto-send also needs; one hung network call
    must not block sending for hours."""
    timeout = _job_timeout(name)
    assert timeout is not None and 0 < timeout <= 30


def test_auto_send_is_deliberately_not_given_a_short_timeout():
    """Sends are paced with 3-7 minute pauses between rounds, so a real run
    can last an hour or more — and force-stopping one mid-round risks a
    duplicate email on the next run."""
    timeout = _job_timeout("auto_send.yml")
    assert timeout is not None and 240 <= timeout <= 360


@pytest.mark.parametrize("name", SCHEDULED)
def test_manual_run_buttons_keep_working(name):
    """The app's 'Check Replies Now' / 'Sync Now' buttons and the Actions
    tab's 'Run workflow' use workflow_dispatch; the window must not remove it."""
    assert "workflow_dispatch" in _triggers(_load(name))
