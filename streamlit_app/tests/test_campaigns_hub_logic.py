import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from campaigns_hub_logic import (
    compute_last_activity_timestamp, build_draft_campaign_row, build_campaign_hub_row,
    build_campaigns_hub, filter_campaigns_by_search, build_deleted_campaign_row,
)

STAGES = [{"name": "intro", "template_prefix": "intro", "wait_days_after_previous": 0}]


def _cfg(name="Foo", status="active"):
    return {"_campaign_name": name, "stages": STAGES, "status": status,
            "_global_default_account": "sales1", "sending": {}}


def _lead(**overrides):
    lead = {"Approval": "Yes", "Email": "a@abc.com", "Status": "", "IntroSentAt": ""}
    lead.update(overrides)
    return lead


# ---------- compute_last_activity_timestamp ----------

def test_last_activity_picks_max_timestamp():
    send_log = [{"Timestamp": "2026-08-20 10:00:00"}, {"Timestamp": "2026-08-25 09:00:00"}]
    assert compute_last_activity_timestamp(send_log) == "2026-08-25 09:00:00"


def test_last_activity_empty_send_log():
    assert compute_last_activity_timestamp([]) == ""


def test_last_activity_ignores_blank_timestamps():
    send_log = [{"Timestamp": ""}, {"Timestamp": "2026-08-20 10:00:00"}]
    assert compute_last_activity_timestamp(send_log) == "2026-08-20 10:00:00"


# ---------- build_draft_campaign_row ----------

def test_draft_row_has_zero_metrics_and_draft_status():
    row = build_draft_campaign_row(_cfg("NewCampaign", status="draft"))
    assert row["name"] == "NewCampaign"
    assert row["status"] == "draft"
    assert row["total_leads"] == 0
    assert row["sent"] == 0


# ---------- build_campaign_hub_row ----------

def test_hub_row_computes_status_and_metrics_together():
    two_stage_cfg = _cfg()
    two_stage_cfg["stages"] = [
        {"name": "intro", "template_prefix": "intro", "wait_days_after_previous": 0},
        {"name": "followup1", "template_prefix": "followup1", "wait_days_after_previous": 3},
    ]
    leads = [_lead(IntroSentAt="2026-08-01 09:00:00")]  # intro sent, followup1 not — still running
    send_log = [{"Status": "sent", "Timestamp": "2026-08-01 09:00:00"}]
    row = build_campaign_hub_row(two_stage_cfg, leads, [], send_log)
    assert row["name"] == "Foo"
    assert row["status"] == "running"
    assert row["total_leads"] == 1
    assert row["sent"] == 1
    assert row["last_activity"] == "2026-08-01 09:00:00"


def test_hub_row_reflects_attention_status():
    cfg = _cfg()
    cfg["_global_default_account"] = ""
    row = build_campaign_hub_row(cfg, [_lead()], [], [])
    assert row["status"] == "attention"
    assert len(row["problems"]) > 0


# ---------- build_campaigns_hub ----------

def test_build_campaigns_hub_skips_sheet_fetch_for_draft_campaigns():
    fetch_calls = []

    def get_cfg(name):
        return _cfg(name, status="draft" if name == "NewOne" else "active")

    def fetch(campaign_cfg):
        fetch_calls.append(campaign_cfg["_campaign_name"])
        return [_lead()], [], [{"Status": "sent", "Timestamp": "2026-08-01 09:00:00"}]

    rows, deleted_rows, errors = build_campaigns_hub(["NewOne", "Established"], get_cfg, fetch)

    assert len(rows) == 2
    assert fetch_calls == ["Established"]  # never fetched Sheet data for the draft one
    by_name = {r["name"]: r for r in rows}
    assert by_name["NewOne"]["status"] == "draft"
    assert by_name["Established"]["status"] == "running"


def test_build_campaigns_hub_isolates_per_campaign_config_errors():
    def get_cfg(name):
        if name == "Broken":
            raise Exception("No templates found")
        return _cfg(name)

    def fetch(campaign_cfg):
        return [_lead()], [], []

    rows, deleted_rows, errors = build_campaigns_hub(["Broken", "Fine"], get_cfg, fetch)
    assert len(rows) == 1
    assert rows[0]["name"] == "Fine"
    assert errors == [("Broken", "No templates found")]


def test_build_campaigns_hub_isolates_per_campaign_sheet_errors():
    def get_cfg(name):
        return _cfg(name)

    def fetch(campaign_cfg):
        if campaign_cfg["_campaign_name"] == "NoTabsYet":
            raise Exception("Tab doesn't exist yet")
        return [_lead()], [], []

    rows, deleted_rows, errors = build_campaigns_hub(["NoTabsYet", "Fine"], get_cfg, fetch)
    assert len(rows) == 1
    assert rows[0]["name"] == "Fine"
    assert errors == [("NoTabsYet", "Tab doesn't exist yet")]


def test_build_campaigns_hub_empty_list():
    rows, deleted_rows, errors = build_campaigns_hub([], lambda n: _cfg(n), lambda c: ([], [], []))
    assert rows == []
    assert errors == []


def test_build_campaigns_hub_excludes_deleted_campaigns_from_normal_rows():
    def get_cfg(name):
        status = "deleted" if name == "Removed" else "active"
        return _cfg(name, status=status)

    def fetch(campaign_cfg):
        return [_lead()], [], []

    rows, deleted_rows, errors = build_campaigns_hub(["Removed", "Fine"], get_cfg, fetch)
    assert [r["name"] for r in rows] == ["Fine"]  # deleted one never appears here
    assert [r["name"] for r in deleted_rows] == ["Removed"]
    assert errors == []


def test_build_campaigns_hub_deleted_campaign_never_needs_a_sheet_fetch():
    fetch_calls = []

    def get_cfg(name):
        return _cfg(name, status="deleted")

    def fetch(campaign_cfg):
        fetch_calls.append(campaign_cfg["_campaign_name"])
        return [], [], []

    build_campaigns_hub(["Removed"], get_cfg, fetch)
    assert fetch_calls == []  # same principle as Draft — status is knowable from config alone


def test_build_deleted_campaign_row_shape():
    row = build_deleted_campaign_row(_cfg("Removed", status="deleted"))
    assert row["name"] == "Removed"
    assert row["status"] == "deleted"
    assert "Deleted" in row["status_label"]


# ---------- filter_campaigns_by_search ----------

def test_filter_by_search_case_insensitive_substring():
    rows = [{"name": "DudeRobe"}, {"name": "SheRobe"}, {"name": "TestCampaign"}]
    result = filter_campaigns_by_search(rows, "robe")
    assert {r["name"] for r in result} == {"DudeRobe", "SheRobe"}


def test_filter_by_search_empty_query_returns_all():
    rows = [{"name": "A"}, {"name": "B"}]
    assert filter_campaigns_by_search(rows, "") == rows
    assert filter_campaigns_by_search(rows, "   ") == rows


def test_filter_by_search_no_match_returns_empty():
    rows = [{"name": "A"}]
    assert filter_campaigns_by_search(rows, "zzz") == []


# ---------- max_workers: concurrent Sheet fetches without changing any output ----------

import threading


def _hub(names, cfgs, fetch, max_workers=1):
    return build_campaigns_hub(names, lambda n: cfgs[n], fetch, max_workers=max_workers)


def test_parallel_hub_keeps_rows_in_campaign_order_even_when_a_later_fetch_finishes_first():
    """Rows must come out in campaign_names order, never completion
    order — the Campaigns list's visible ordering depends on it."""
    import time
    names = ["First", "Second", "Third"]
    cfgs = {n: _cfg(n) for n in names}
    def fetch(cfg):
        if cfg["_campaign_name"] == "First":
            time.sleep(0.2)   # the first campaign is the SLOWEST
        return [_lead()], [], []
    rows, deleted, errors = _hub(names, cfgs, fetch, max_workers=3)
    assert [r["name"] for r in rows] == names
    assert errors == []


def test_parallel_hub_matches_the_sequential_result_exactly():
    names = ["A", "B", "C", "D"]
    cfgs = {"A": _cfg("A"), "B": _cfg("B", "draft"), "C": _cfg("C"), "D": _cfg("D", "deleted")}
    fetch = lambda cfg: ([_lead(), _lead(Email="b@abc.com")], [], [])  # noqa: E731
    sequential = _hub(names, cfgs, fetch, max_workers=1)
    parallel = _hub(names, cfgs, fetch, max_workers=6)
    assert parallel == sequential


def test_parallel_hub_never_fetches_sheets_for_draft_or_deleted_campaigns():
    names = ["Active", "Draft", "Deleted"]
    cfgs = {"Active": _cfg("Active"), "Draft": _cfg("Draft", "draft"), "Deleted": _cfg("Deleted", "deleted")}
    fetched = []
    def fetch(cfg):
        fetched.append(cfg["_campaign_name"])
        return [], [], []
    _hub(names, cfgs, fetch, max_workers=6)
    assert fetched == ["Active"]


def test_parallel_hub_one_failing_campaign_is_isolated_and_the_rest_still_load():
    names = ["Good1", "Broken", "Good2"]
    cfgs = {n: _cfg(n) for n in names}
    def fetch(cfg):
        if cfg["_campaign_name"] == "Broken":
            raise RuntimeError("Tab doesn't exist yet")
        return [_lead()], [], []
    rows, _deleted, errors = _hub(names, cfgs, fetch, max_workers=3)
    assert [r["name"] for r in rows] == ["Good1", "Good2"]
    assert errors == [("Broken", "Tab doesn't exist yet")]


def test_parallel_hub_a_campaign_whose_config_cannot_load_is_reported_not_fetched():
    def get_cfg(name):
        if name == "NoConfig":
            raise FileNotFoundError("no templates folder")
        return _cfg(name)
    fetched = []
    rows, _d, errors = build_campaigns_hub(
        ["Ok", "NoConfig"], get_cfg, lambda cfg: (fetched.append(cfg["_campaign_name"]) or ([_lead()], [], [])),
        max_workers=4)
    assert [r["name"] for r in rows] == ["Ok"]
    assert errors == [("NoConfig", "no templates folder")] and fetched == ["Ok"]


def test_parallel_hub_campaigns_are_really_fetched_at_the_same_time():
    """The actual reported slowness: N campaigns used to cost N full
    Sheet loads in a row. Three fetches that each wait for the other
    two can only all complete if they genuinely overlap."""
    barrier = threading.Barrier(3, timeout=5)
    names = ["A", "B", "C"]
    cfgs = {n: _cfg(n) for n in names}
    def fetch(cfg):
        barrier.wait()
        return [_lead()], [], []
    rows, _d, errors = _hub(names, cfgs, fetch, max_workers=6)
    assert errors == [] and len(rows) == 3


def test_default_hub_behavior_is_still_strictly_sequential():
    barrier = threading.Barrier(2, timeout=0.3)
    names = ["A", "B"]
    cfgs = {n: _cfg(n) for n in names}
    def fetch(cfg):
        barrier.wait()
        return [_lead()], [], []
    _rows, _d, errors = build_campaigns_hub(names, lambda n: cfgs[n], fetch)  # max_workers omitted
    assert len(errors) == 2  # one-at-a-time, so neither could ever meet the other


# ================================================================ the whole-campaign send series, totals, filters, sort and name feedback

from datetime import date  # noqa: E402

import pytest  # noqa: E402

import campaigns_hub_logic as HL  # noqa: E402


def _sent(day, n=1, status="sent", hour="10:00:00"):
    return [{"Timestamp": f"{day} {hour}", "Status": status} for _ in range(n)]


def test_a_campaign_with_no_sends_has_the_empty_series():
    assert HL.send_series([], date(2026, 10, 15)) == HL.EMPTY_SERIES and HL.send_series(None) == HL.EMPTY_SERIES
    assert HL.send_series(_sent("2026-10-01", 3, status="failed"), date(2026, 10, 15))["bars"] == []          # only emails that went out are counted


def test_the_series_covers_the_whole_campaign_one_bar_per_day_from_the_first_send_to_the_last():
    log = _sent("2026-10-01", 3) + _sent("2026-10-03", 5) + _sent("2026-10-06", 2)
    series = HL.send_series(log, date(2026, 10, 20))
    assert series == {"bars": [3, 0, 5, 0, 0, 2], "unit": "day", "start": "2026-10-01", "end": "2026-10-06", "total": 10}


def test_a_running_campaign_is_drawn_up_to_today_so_quiet_days_show():
    log = _sent("2026-10-01", 3) + _sent("2026-10-03", 5)
    series = HL.send_series(log, date(2026, 10, 6), through_today=True)
    assert series["bars"] == [3, 0, 5, 0, 0, 0] and series["end"] == "2026-10-06"
    assert HL.send_series(log, date(2026, 10, 6), through_today=False)["bars"] == [3, 0, 5]                     # a finished one stops at its last send


def test_a_today_before_the_last_send_never_shortens_the_series():
    log = _sent("2026-10-01", 1) + _sent("2026-10-09", 1)
    assert HL.send_series(log, date(2026, 10, 5), through_today=True)["end"] == "2026-10-09"


def test_a_campaign_of_exactly_thirty_days_is_still_drawn_day_by_day_and_a_longer_one_by_week():
    thirty = HL.send_series(_sent("2026-09-01") + _sent("2026-09-30"), None)
    assert thirty["unit"] == "day" and len(thirty["bars"]) == 30
    long = HL.send_series(_sent("2026-09-01", 2) + _sent("2026-10-01", 4), None)                                # 31 days
    assert long["unit"] == "week" and long["bars"] == [2, 0, 0, 0, 4] and long["total"] == 6


def test_a_very_long_campaign_keeps_only_its_last_half_year_by_week_and_every_email_is_still_in_the_total():
    log = _sent("2025-01-01", 7) + _sent("2026-10-01", 9)
    series = HL.send_series(log, None)
    assert series["unit"] == "week" and len(series["bars"]) == HL.MAX_WEEKLY_BARS and series["bars"][-1] == 9 and series["total"] == 16
    assert date.fromisoformat(series["start"]).weekday() == date(2025, 1, 1).weekday()                        # the window starts on a week boundary of the campaign


@pytest.mark.parametrize("bad", ["", "not a date", None, "2026-13-45 10:00:00", "10/01/2026"])
def test_a_row_with_an_unreadable_time_is_skipped_not_fatal(bad):
    log = [{"Timestamp": bad, "Status": "sent"}] + _sent("2026-10-02", 2)
    assert HL.send_series(log, None)["bars"] == [2]


def test_the_series_counts_like_the_sent_number_does():
    log = _sent("2026-10-01", 4) + _sent("2026-10-02", 2, status="failed") + _sent("2026-10-02", 1, status="bounced")
    assert HL.send_series(log, None)["total"] == 4 == sum(1 for r in log if r["Status"] == "sent")


def test_a_hub_row_carries_the_series_and_a_draft_or_archived_one_has_the_empty_series(tmp_path):
    cfg = {"_campaign_name": "C", "stages": [{"name": "intro"}], "status": "draft"}
    assert HL.build_draft_campaign_row(cfg)["series"] == HL.EMPTY_SERIES and HL.build_deleted_campaign_row({**cfg, "status": "deleted"})["series"] == HL.EMPTY_SERIES


@pytest.mark.parametrize("series, text", [
    ({"bars": [1], "unit": "day", "start": "2026-09-03", "end": "2026-09-17"}, "Sends per day · Sep 3 – Sep 17"),
    ({"bars": [1], "unit": "week", "start": "2026-01-05", "end": "2026-10-01"}, "Sends per week · Jan 5 – Oct 1"),
    ({"bars": [], "unit": "day", "start": "", "end": ""}, "No sends yet"), (None, "No sends yet"), ({}, "No sends yet"),
])
def test_the_chart_caption_says_what_the_bars_are_and_over_what_dates(series, text):
    assert HL.series_caption(series) == text


def _row(name, status, leads=0, sent=0, replies=0, last="", contacted=None):
    contacted = (leads if sent else 0) if contacted is None else contacted
    return {"name": name, "status": status, "total_leads": leads, "contacted": contacted, "sent": sent, "replies": replies, "last_activity": last, "reply_rate": HL.reply_rate_text(replies, contacted), "problems": [], "series": HL.EMPTY_SERIES}


ROWS = [_row("Old", "completed", 10, 28, 3, "2026-09-01 10:00:00"), _row("Live", "running", 77, 252, 51, "2026-10-14 09:00:00"), _row("Soon", "draft"), _row("Mid", "completed", 39, 235, 20, "2026-10-12 09:00:00"),
        _row("Stuck", "attention"), _row("Hold", "paused", 5, 3, 0, "2026-10-13 09:00:00", contacted=3)]


def test_the_totals_are_added_up_from_the_rows_and_the_rate_is_overall():
    summary = HL.summarize_campaigns(ROWS)
    assert summary == {"count": 6, "running": 1, "attention": 1, "leads": 131, "contacted": 129, "sent": 518, "replies": 74, "reply_rate": "57.4%"}      # 74 replied of 129 contacted (not of 518 emails)


@pytest.mark.parametrize("replies, contacted, text", [(74, 126, "58.7%"), (51, 77, "66.2%"), (0, 10, "0.0%"), (5, 0, "—"), (0, 0, "—"), (1, 3, "33.3%")])
def test_the_reply_rate_is_leads_who_replied_over_leads_contacted_with_one_decimal_and_a_dash_when_nobody_was_contacted(replies, contacted, text):
    assert HL.reply_rate_text(replies, contacted) == text


def test_the_overall_rate_never_divides_by_emails_sent():
    """REGRESSION (reported twice): 74 replied of 126 leads contacted is 58.7%; dividing by the 515 EMAILS sent gave a misleading 14.4%."""
    rows = [_row("A", "completed", 10, 28, 3), _row("B", "running", 77, 252, 51), _row("C", "completed", 39, 235, 20)]
    summary = HL.summarize_campaigns(rows)
    assert (summary["replies"], summary["contacted"], summary["sent"], summary["reply_rate"]) == (74, 126, 515, "58.7%")


@pytest.mark.parametrize("leads, sent, replies, contacted", [(77, 252, 51, 77), (10, 28, 3, 10), (39, 235, 20, 39), (5, 40, 1, 4), (8, 100, 2, 2)])
def test_one_campaigns_total_always_equals_its_own_reply_rate(leads, sent, replies, contacted):
    """The law that was broken: the figure above the list must agree with the figure on the card, whatever the numbers are."""
    row = _row("Only", "running", leads, sent, replies, contacted=contacted)
    assert HL.summarize_campaigns([row])["reply_rate"] == row["reply_rate"]


def test_the_totals_of_no_campaigns_are_zero_and_a_dash():
    assert HL.summarize_campaigns([]) == {"count": 0, "running": 0, "attention": 0, "leads": 0, "contacted": 0, "sent": 0, "replies": 0, "reply_rate": "—"}


@pytest.mark.parametrize("summary, text", [
    ({"count": 5, "running": 1, "attention": 1}, "5 campaigns · 1 running · 1 needs attention"), ({"count": 1, "running": 0, "attention": 0}, "1 campaign"),
    ({"count": 4, "running": 2, "attention": 2}, "4 campaigns · 2 running · 2 need attention"), ({"count": 0, "running": 0, "attention": 0}, "0 campaigns"),
])
def test_the_subtitle_says_only_what_is_not_zero(summary, text):
    assert HL.hub_subtitle(summary) == text


def test_every_status_filter_is_offered_with_its_count_in_a_fixed_order():
    assert HL.status_filter_options(ROWS) == [("all", "All 6"), ("running", "Running 1"), ("attention", "Attention 1"), ("paused", "Paused 1"), ("draft", "Draft 1"), ("completed", "Completed 2")]
    assert HL.status_filter_options([])[0] == ("all", "All 0") and HL.status_filter_options([])[3] == ("paused", "Paused 0")


@pytest.mark.parametrize("status, names", [("all", ["Old", "Live", "Soon", "Mid", "Stuck", "Hold"]), ("", ["Old", "Live", "Soon", "Mid", "Stuck", "Hold"]), ("completed", ["Old", "Mid"]), ("running", ["Live"]), ("deleted", [])])
def test_filtering_by_status_keeps_the_order_and_all_means_everything(status, names):
    assert [r["name"] for r in HL.filter_campaigns_by_status(ROWS, status)] == names


@pytest.mark.parametrize("option, names", [
    ("Active first", ["Live", "Stuck", "Hold", "Soon", "Mid", "Old"]),
    ("Last activity", ["Live", "Hold", "Mid", "Old", "Soon", "Stuck"]),
    ("Name", ["Hold", "Live", "Mid", "Old", "Soon", "Stuck"]),
    ("Most replies", ["Live", "Mid", "Old", "Soon", "Stuck", "Hold"]),
    ("anything else", ["Live", "Stuck", "Hold", "Soon", "Mid", "Old"]),
])
def test_the_sort_options(option, names):
    assert [r["name"] for r in HL.sort_campaigns(ROWS, option)] == names


def test_sorting_never_changes_the_list_it_is_given():
    before = [r["name"] for r in ROWS]
    HL.sort_campaigns(ROWS, "Name")
    assert [r["name"] for r in ROWS] == before


@pytest.mark.parametrize("name, existing, kind", [("", [], "hint"), ("   ", [], "hint"), (None, [], "hint"), ("bad name", [], "error"), ("bad-name", [], "error"), ("taken", ["taken"], "error"), ("fine_1", ["taken"], "ok")])
def test_the_name_box_gives_live_feedback(name, existing, kind):
    assert HL.name_feedback(name, existing)[0] == kind


def test_the_feedback_uses_the_same_reasons_the_button_will_give():
    from campaign_builder import validate_campaign_name
    for name, existing in (("bad name", []), ("taken", ["taken"])):
        assert HL.name_feedback(name, existing)[1] == validate_campaign_name(name, existing)


from unittest.mock import patch  # noqa: E402


def _built_row(status, send_log, today=date(2026, 10, 10)):
    cfg = {"_campaign_name": "C", "stages": [{"name": "intro"}]}
    base = ["C", 5, 4, 4, 0, 0, 0, 1, "25.0%"]                      # 5 leads, 4 contacted, 4 emails sent, 1 replied: 25% of the contacted
    with patch.object(HL, "compute_campaign_status", lambda c, leads: (status, [])), patch.object(HL.outreach, "compute_all_campaigns_row", lambda *a, **k: base):
        return HL.build_campaign_hub_row(cfg, [], [], send_log, today=today)


def test_a_running_campaigns_chart_reaches_today_but_a_finished_ones_stops_at_its_last_send():
    log = _sent("2026-10-01", 2) + _sent("2026-10-03", 2)
    running = _built_row("running", log)
    assert running["series"]["end"] == "2026-10-10" and len(running["series"]["bars"]) == 10 and running["series"]["bars"][-1] == 0
    for status in ("completed", "paused", "attention"):
        assert _built_row(status, log)["series"]["end"] == "2026-10-03"


def test_a_built_row_carries_the_numbers_the_card_shows_and_the_series_total_matches_the_sent_log():
    row = _built_row("running", _sent("2026-10-01", 4))
    assert (row["total_leads"], row["contacted"], row["sent"], row["replies"], row["reply_rate"], row["status"]) == (5, 4, 4, 1, "25.0%", "running") and row["series"]["total"] == 4
    assert row["last_activity"] == "2026-10-01 10:00:00"


def test_with_the_real_engine_calculation_the_row_and_the_total_agree_and_neither_divides_by_emails():
    """No stand-in numbers: 5 leads, 4 contacted (one of them replied), and 9 emails sent in all (follow-ups included)."""
    cfg = {"_campaign_name": "C", "stages": [{"name": "intro"}, {"name": "followup1"}]}
    leads = [{"Email": f"l{i}@x.com", "IntroSentAt": "2026-10-01 10:00:00" if i < 4 else "", "ReplyStatus": "Replied" if i == 0 else ""} for i in range(5)]
    log = [{"Status": "sent", "Timestamp": "2026-10-01 10:00:00"}] * 9
    with patch.object(HL, "compute_campaign_status", lambda c, ls: ("running", [])):
        row = HL.build_campaign_hub_row(cfg, leads, [], log, today=date(2026, 10, 10))
    assert (row["total_leads"], row["contacted"], row["sent"], row["replies"], row["reply_rate"]) == (5, 4, 9, 1, "25.0%")
    summary = HL.summarize_campaigns([row])
    assert summary["reply_rate"] == "25.0%" and summary["contacted"] == 4                      # not 1 of 9 emails (11.1%)
