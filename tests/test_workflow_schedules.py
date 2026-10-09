"""Guards the scheduled-workflow design: WHEN things run, and that they can
never step on each other.

Why this exists: a schedule is easy to get subtly wrong and nothing fails
loudly when it is. A typo can turn "once an hour" into once a minute or once
a day without any error, and a wrong concurrency setting can let two sends
overlap (duplicate emails) or kill one mid-send. These tests expand the REAL
cron expressions from the REAL workflow files into concrete run times and
assert on those — they do not just compare strings, so a change that looks
different but behaves the same still passes, and one that looks harmless but
isn't fails.

What they cannot prove: how GitHub itself behaves at runtime (delays, queue
replacement, runs it never starts, the 60-day inactivity rule). That is
documented in the README and can only be observed on the real Actions tab.
"""
import os

import pytest
import yaml

WORKFLOWS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows"))

# The five workflows that run once an hour, around the clock.
SCHEDULED = ["check_replies.yml", "auto_send.yml", "sync_asana.yml", "dashboard.yml", "ugc_tracker_sync.yml"]
# Of those, the ones that write to the campaign Google Sheets and therefore
# share ONE lock so they take turns (UGC has its own sheet and its own lock).
SHARED_LOCK = ["check_replies.yml", "auto_send.yml", "sync_asana.yml", "dashboard.yml"]
SHARED_GROUP = "google-sheets-api"
# Intended order inside each hour: replies are recorded BEFORE the next
# send decides who is due, and Asana/dashboard reflect both afterwards.
CYCLE_ORDER = ["check_replies.yml", "auto_send.yml", "sync_asana.yml", "dashboard.yml"]

MINUTES_PER_DAY = 24 * 60


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


def schedule_utc_minutes(workflow):
    """All run times (minute of day, UTC) from every cron line, with a check
    that no two lines list the same time (an accidental duplicate)."""
    crons = [entry["cron"] for entry in (_triggers(workflow).get("schedule") or [])]
    assert crons, "workflow has no schedule"
    seen = set()
    for expression in crons:
        times = cron_utc_minutes_of_day(expression)
        assert not (times & seen), f"cron lines overlap each other: {crons}"
        seen |= times
    return sorted(seen)


def runs_per_clock_hour(minutes):
    """{hour: how many runs fall in it}, for all 24 hours."""
    counts = {hour: 0 for hour in range(24)}
    for minute in minutes:
        counts[minute // 60] += 1
    return counts


def minute_in_hour(workflow):
    offsets = {m % 60 for m in schedule_utc_minutes(workflow)}
    assert len(offsets) == 1, f"runs are not all at the same minute of the hour: {sorted(offsets)}"
    return offsets.pop()


def _is_exactly_hourly_around_the_clock(minutes):
    return len(minutes) == 24 and set(runs_per_clock_hour(minutes).values()) == {1}


# ------------------------------------------------ the checker checks itself

def test_cron_expander_handles_the_forms_used_here():
    assert len(cron_utc_minutes_of_day("37 * * * *")) == 24
    assert len(cron_utc_minutes_of_day("7,37 12-22 * * *")) == 22
    assert len(cron_utc_minutes_of_day("*/30 * * * *")) == 48
    assert cron_utc_minutes_of_day("37 11 * * *") == {11 * 60 + 37}


def test_cron_expander_refuses_schedules_it_cannot_evaluate_honestly():
    with pytest.raises(ValueError):
        cron_utc_minutes_of_day("7 12 * * 1-5")   # weekday-restricted


def test_hourly_check_rejects_every_other_kind_of_schedule():
    """Negative control: if this accepted the wrong shapes, every test below
    would be meaningless."""
    assert _is_exactly_hourly_around_the_clock(sorted(cron_utc_minutes_of_day("37 * * * *")))
    for wrong in ("5,35 * * * *",       # twice an hour
                  "*/10 * * * *",       # six times an hour
                  "37 11-22 * * *",     # hourly, but only half the day
                  "37 */2 * * *",       # every two hours
                  "37 9 * * *"):        # once a day
        assert not _is_exactly_hourly_around_the_clock(sorted(cron_utc_minutes_of_day(wrong))), wrong


# ---------------------------------------------------- the real schedules

@pytest.mark.parametrize("name", SCHEDULED)
def test_runs_once_every_hour_around_the_clock(name):
    minutes = schedule_utc_minutes(_load(name))
    assert len(minutes) == 24, f"expected 24 runs a day, got {len(minutes)}"
    assert runs_per_clock_hour(minutes) == {hour: 1 for hour in range(24)}, \
        "every clock hour must have exactly one run — none missed, none doubled"
    following = minutes[1:] + minutes[:1]
    gaps = {((nxt - cur) % MINUTES_PER_DAY) for cur, nxt in zip(minutes, following)}
    assert gaps == {60}, f"runs are not exactly one hour apart: {sorted(gaps)}"


@pytest.mark.parametrize("name", SCHEDULED)
def test_runs_avoid_the_top_of_the_hour_and_half_hour(name):
    """GitHub delays (and can drop) scheduled runs under load, worst at the
    start of the hour — so these are deliberately kept off :00 and :30."""
    assert minute_in_hour(_load(name)) % 30 != 0


def test_each_hour_runs_in_the_intended_order():
    offsets = [minute_in_hour(_load(n)) for n in CYCLE_ORDER]
    assert offsets == sorted(offsets) and len(set(offsets)) == len(offsets), \
        f"expected strictly increasing minutes in order {CYCLE_ORDER}, got {offsets}"


def test_no_two_workflows_that_share_the_lock_start_in_the_same_minute():
    offsets = [minute_in_hour(_load(n)) for n in SHARED_LOCK]
    assert len(set(offsets)) == len(offsets), \
        "two lock-sharing workflows start in the same minute, so one would always have to wait"


def test_every_scheduled_workflow_starts_at_its_own_minute():
    """Not just the lock-sharing four: no two scheduled workflows fire at the
    same instant, so GitHub never has to start two jobs for this repo at once."""
    offsets = [minute_in_hour(_load(n)) for n in SCHEDULED]
    assert len(set(offsets)) == len(offsets)


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
    tab's 'Run workflow' use workflow_dispatch; the schedule must not remove it."""
    assert "workflow_dispatch" in _triggers(_load(name))


# ------------------------------------------- the schedule vs. reply lookback

def _longest_gap_minutes(workflow):
    """Longest stretch between two consecutive runs, wrapping around the clock."""
    minutes = schedule_utc_minutes(workflow)
    following = minutes[1:] + minutes[:1]
    return max(((nxt - cur) % MINUTES_PER_DAY) or MINUTES_PER_DAY for cur, nxt in zip(minutes, following))


def _reply_lookback_hours_everywhere():
    """(label, hours) for the global default and for every campaign that
    overrides it."""
    root = os.path.abspath(os.path.join(WORKFLOWS_DIR, "..", ".."))
    with open(os.path.join(root, "config", "settings.yaml")) as handle:
        settings = yaml.safe_load(handle)
    found = [("settings.yaml default", settings["default_campaign_settings"]["reply_monitor"]["lookback_hours"])]
    campaigns_dir = os.path.join(root, "config", "campaigns")
    for filename in sorted(os.listdir(campaigns_dir)):
        if filename.endswith(".yaml"):
            with open(os.path.join(campaigns_dir, filename)) as handle:
                override = yaml.safe_load(handle) or {}
            hours = (override.get("reply_monitor") or {}).get("lookback_hours")
            if hours is not None:
                found.append((f"campaigns/{filename}", hours))
    return found


def test_the_longest_gap_between_reply_checks_is_one_hour():
    """Pins the number the next test depends on."""
    assert _longest_gap_minutes(_load("check_replies.yml")) == 60


def test_reply_lookback_always_reaches_back_further_than_the_scheduled_gap():
    """Check Replies only looks back `lookback_hours`; a lookback shorter than
    the gap between runs would silently miss every reply in it. One hour of
    margin."""
    gap_hours = _longest_gap_minutes(_load("check_replies.yml")) / 60
    for label, hours in _reply_lookback_hours_everywhere():
        assert hours >= gap_hours + 1, f"{label}: lookback_hours={hours} is shorter than the {gap_hours:g}h gap"


def test_reply_lookback_covers_a_whole_day_because_github_does_not_run_every_scheduled_hour():
    """The schedule asks for a run every hour, but GitHub is known to start
    only a handful of scheduled runs a day on this repository (see the
    README). The lookback is what stops a reply from being missed when that
    happens, so it must be generous — a full day."""
    for label, hours in _reply_lookback_hours_everywhere():
        assert hours >= 24, f"{label}: lookback_hours={hours} — keep it at 24 or more"
