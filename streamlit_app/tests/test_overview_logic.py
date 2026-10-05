import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from conftest import FIXTURE_CAMPAIGN

from preview_logic import get_campaign_cfg
from overview_logic import build_campaign_overview_row, build_all_campaigns_overview, OVERVIEW_COLUMNS


def _leads(n_with_email=3, n_contacted=1):
    leads = []
    for i in range(n_with_email):
        lead = {"Email": f"lead{i}@abc.com", "Approval": "Yes"}
        if i < n_contacted:
            lead["IntroSentAt"] = "2026-08-01 09:00:00"
        leads.append(lead)
    return leads


def test_overview_columns_has_pending_inserted_after_total_leads():
    assert OVERVIEW_COLUMNS[0] == "Campaign"
    assert OVERVIEW_COLUMNS[1] == "Total Leads"
    assert OVERVIEW_COLUMNS[2] == "Pending (Not Yet Contacted)"


def test_build_campaign_overview_row_computes_pending_correctly(fixture_repo):
    cfg = get_campaign_cfg(FIXTURE_CAMPAIGN)
    leads = _leads(n_with_email=5, n_contacted=2)
    row = build_campaign_overview_row(cfg, leads, responses=[], send_log=[])

    row_dict = dict(zip(OVERVIEW_COLUMNS, row))
    assert row_dict["Campaign"] == FIXTURE_CAMPAIGN
    assert row_dict["Total Leads"] == "5"
    assert row_dict["Unique Contacted"] == "2"
    assert row_dict["Pending (Not Yet Contacted)"] == "3"


def test_build_campaign_overview_row_pending_never_negative(fixture_repo):
    cfg = get_campaign_cfg(FIXTURE_CAMPAIGN)
    # Pathological case: more "contacted" markers than total leads with email
    # shouldn't be possible in real data, but pending must still floor at 0.
    leads = _leads(n_with_email=2, n_contacted=2)
    row = build_campaign_overview_row(cfg, leads, responses=[], send_log=[])
    row_dict = dict(zip(OVERVIEW_COLUMNS, row))
    assert int(row_dict["Pending (Not Yet Contacted)"]) >= 0


def test_build_all_campaigns_overview_skips_unreadable_campaigns(fixture_repo):
    def fetch(name):
        if name == "Broken":
            raise RuntimeError("Tab doesn't exist yet")
        cfg = get_campaign_cfg(FIXTURE_CAMPAIGN)
        return cfg, _leads(), [], []

    rows, errors = build_all_campaigns_overview([FIXTURE_CAMPAIGN, "Broken"], fetch)
    assert len(rows) == 1
    assert len(errors) == 1
    assert errors[0][0] == "Broken"
    assert "Tab doesn't exist yet" in errors[0][1]


def test_build_all_campaigns_overview_empty_list_returns_empty():
    rows, errors = build_all_campaigns_overview([], lambda name: (None, [], [], []))
    assert rows == []
    assert errors == []


# ---------- max_workers: concurrent Sheet reads without changing any output ----------

import threading


def _overview_fetch(fixture_cfg, fail_for=()):
    def fetch(name):
        if name in fail_for:
            raise RuntimeError(f"{name}: tab doesn't exist yet")
        return fixture_cfg, _leads(), [], []
    return fetch


def test_parallel_overview_matches_sequential_exactly_and_keeps_order(fixture_repo):
    cfg = get_campaign_cfg(FIXTURE_CAMPAIGN)
    names = ["One", "Two", "Three"]
    sequential = build_all_campaigns_overview(names, _overview_fetch(cfg), max_workers=1)
    parallel = build_all_campaigns_overview(names, _overview_fetch(cfg), max_workers=6)
    assert parallel == sequential
    assert len(parallel[0]) == 3


def test_parallel_overview_one_failing_campaign_is_isolated(fixture_repo):
    cfg = get_campaign_cfg(FIXTURE_CAMPAIGN)
    rows, errors = build_all_campaigns_overview(
        ["Good1", "Bad", "Good2"], _overview_fetch(cfg, fail_for={"Bad"}), max_workers=3)
    assert len(rows) == 2
    assert errors == [("Bad", "Bad: tab doesn't exist yet")]


def test_parallel_overview_campaigns_are_really_read_at_the_same_time(fixture_repo):
    cfg = get_campaign_cfg(FIXTURE_CAMPAIGN)
    barrier = threading.Barrier(3, timeout=5)
    def fetch(name):
        barrier.wait()
        return cfg, _leads(), [], []
    rows, errors = build_all_campaigns_overview(["A", "B", "C"], fetch, max_workers=6)
    assert errors == [] and len(rows) == 3


def test_default_overview_behavior_is_still_strictly_sequential(fixture_repo):
    cfg = get_campaign_cfg(FIXTURE_CAMPAIGN)
    barrier = threading.Barrier(2, timeout=0.3)
    def fetch(name):
        barrier.wait()
        return cfg, _leads(), [], []
    _rows, errors = build_all_campaigns_overview(["A", "B"], fetch)  # max_workers omitted
    assert len(errors) == 2
