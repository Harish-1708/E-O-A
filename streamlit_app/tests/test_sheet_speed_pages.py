"""Page-level proof of the Google Sheets speed-ups.

Like test_github_read_cache_pages.py, deliberately its own module: only
the lowest-level network calls are faked, so the REAL pages, the REAL
connector and its REAL worksheet cache and parallel reads run exactly as
they do in production.
"""
import os
import threading
from unittest.mock import patch

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from test_pages_smoke import (
    FakeWorksheet, FakeSpreadsheet, FIXTURE_CAMPAIGN, PAGES_DIR, _dashboard_secrets, _authed_session,
)
from test_github_read_cache_pages import _FakeRepo


@pytest.fixture(autouse=True)
def _clear_streamlit_caches():
    st.cache_resource.clear()
    st.cache_data.clear()
    yield
    st.cache_resource.clear()
    st.cache_data.clear()


class _CountingSpreadsheet(FakeSpreadsheet):
    """In real gspread every worksheet() call is its own full Google request."""

    def __init__(self, worksheets):
        super().__init__(worksheets)
        self.lookups = []

    def worksheet(self, title):
        self.lookups.append(title)
        return super().worksheet(title)


class _MeetingWorksheet(FakeWorksheet):
    """The first `parties` reads across ALL such worksheets must overlap
    in time to finish: each waits for the others at a shared barrier.
    Read one after another, the first would wait out the timeout and
    fail. After those first reads, behaves like a plain fake."""

    def __init__(self, records, gate):
        super().__init__(records)
        self._gate = gate

    def get_all_records(self):
        if self._gate["remaining"] > 0:
            with self._gate["lock"]:
                self._gate["remaining"] -= 1
            self._gate["barrier"].wait()
        return super().get_all_records()


def _open_page(spreadsheet, selected=FIXTURE_CAMPAIGN):
    repo = _FakeRepo()
    patches = [
        patch("gspread.authorize", return_value=type("C", (), {"open_by_key": lambda self, k: spreadsheet})()),
        patch("google.oauth2.service_account.Credentials.from_service_account_info", return_value=object()),
        patch("github_client.requests.get", repo.get),
    ]
    at = AppTest.from_file(os.path.join(PAGES_DIR, "campaigns.py"))
    at.secrets.update(_dashboard_secrets())
    for k, v in _authed_session().items():
        at.session_state[k] = v
    if selected:
        at.session_state["selected_campaign"] = selected
    return at, patches


_LEAD = {"Email": "a@abc.com", "Approval": "Yes", "IntroSentAt": "", "IntroVariant": "",
         "SenderAccount": "", "Status": ""}


def test_campaign_page_reads_its_four_sheet_tabs_at_the_same_time(fixture_repo):
    """The actual reported slowness: the campaign page read leads,
    responses, send log and error log one after another — each its own
    slow Google round trip. All four must now overlap."""
    gate = {"barrier": threading.Barrier(4, timeout=8), "remaining": 4, "lock": threading.Lock()}
    ss = _CountingSpreadsheet({
        f"{FIXTURE_CAMPAIGN} Master Sheet": _MeetingWorksheet([_LEAD], gate),
        f"{FIXTURE_CAMPAIGN} Response Sheet": _MeetingWorksheet([], gate),
        f"{FIXTURE_CAMPAIGN} Custom Log Sheet": _MeetingWorksheet([], gate),
        f"{FIXTURE_CAMPAIGN} Error Log": _MeetingWorksheet([], gate),
    })
    at, patches = _open_page(ss)
    with patches[0], patches[1], patches[2]:
        at.run(timeout=30)
    assert list(at.exception) == []
    assert not gate["barrier"].broken           # all four really did meet
    assert "By stage" in [h.value for h in at.subheader]   # and the page rendered with their data


def test_google_worksheet_lookups_are_made_once_not_before_every_read(fixture_repo):
    """In the gspread version this app runs, each worksheet lookup is a
    full extra Google request. Reloading the page's data a second time
    (cache cleared, as happens every 30 seconds) must not repeat them."""
    ss = _CountingSpreadsheet({
        f"{FIXTURE_CAMPAIGN} Master Sheet": FakeWorksheet([_LEAD]),
        f"{FIXTURE_CAMPAIGN} Response Sheet": FakeWorksheet([]),
        f"{FIXTURE_CAMPAIGN} Custom Log Sheet": FakeWorksheet([]),
        f"{FIXTURE_CAMPAIGN} Error Log": FakeWorksheet([]),
    })
    at, patches = _open_page(ss)
    with patches[0], patches[1], patches[2]:
        at.run(timeout=30)
        lookups_after_first_load = len(ss.lookups)
        st.cache_data.clear()          # the 30s data cache expiring
        at.run(timeout=30)
        lookups_after_reload = len(ss.lookups)
    assert list(at.exception) == []
    assert lookups_after_first_load <= 4        # one per distinct tab, never one per read
    assert lookups_after_reload == lookups_after_first_load   # a reload adds NONE


def test_campaigns_list_page_loads_and_does_one_lookup_per_tab(fixture_repo):
    ss = _CountingSpreadsheet({
        f"{FIXTURE_CAMPAIGN} Master Sheet": FakeWorksheet([_LEAD]),
        f"{FIXTURE_CAMPAIGN} Response Sheet": FakeWorksheet([]),
        f"{FIXTURE_CAMPAIGN} Custom Log Sheet": FakeWorksheet([]),
        f"{FIXTURE_CAMPAIGN} Error Log": FakeWorksheet([]),
    })
    at, patches = _open_page(ss, selected=None)   # the hub list, not a detail page
    with patches[0], patches[1], patches[2]:
        at.run(timeout=30)
        first = len(ss.lookups)
        st.cache_data.clear()
        at.run(timeout=30)
    assert list(at.exception) == []
    assert " ".join(str(m.value) for m in at.markdown).count(FIXTURE_CAMPAIGN) >= 1   # the campaign is listed
    assert first <= 3                       # master + responses + send log, once each
    assert len(ss.lookups) == first         # and the reload added none


def test_campaigns_list_page_asks_for_concurrent_fetching(fixture_repo):
    """A single-campaign fixture can't show concurrency on its own, so
    pin the wiring itself: the list page must hand the builder a
    max_workers above 1, or the parallel fetching never happens at all."""
    import campaigns_hub_logic
    seen = {}
    real = campaigns_hub_logic.build_campaigns_hub

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real(*args, **kwargs)

    ss = _CountingSpreadsheet({
        f"{FIXTURE_CAMPAIGN} Master Sheet": FakeWorksheet([_LEAD]),
        f"{FIXTURE_CAMPAIGN} Response Sheet": FakeWorksheet([]),
        f"{FIXTURE_CAMPAIGN} Custom Log Sheet": FakeWorksheet([]),
        f"{FIXTURE_CAMPAIGN} Error Log": FakeWorksheet([]),
    })
    at, patches = _open_page(ss, selected=None)
    with patches[0], patches[1], patches[2], patch("campaigns_hub_logic.build_campaigns_hub", spy):
        at.run(timeout=30)
    assert list(at.exception) == []
    assert seen.get("max_workers", 1) > 1


def test_overview_page_asks_for_concurrent_fetching(fixture_repo):
    import overview_logic
    seen = {}
    real = overview_logic.build_all_campaigns_overview

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real(*args, **kwargs)

    ss = _CountingSpreadsheet({
        f"{FIXTURE_CAMPAIGN} Master Sheet": FakeWorksheet([_LEAD]),
        f"{FIXTURE_CAMPAIGN} Response Sheet": FakeWorksheet([]),
        f"{FIXTURE_CAMPAIGN} Custom Log Sheet": FakeWorksheet([]),
        f"{FIXTURE_CAMPAIGN} Error Log": FakeWorksheet([]),
    })
    with patch("gspread.authorize", return_value=type("C", (), {"open_by_key": lambda self, k: ss})()), \
         patch("google.oauth2.service_account.Credentials.from_service_account_info", return_value=object()), \
         patch("overview_logic.build_all_campaigns_overview", spy):
        at = AppTest.from_file(os.path.join(PAGES_DIR, "overview.py"))
        at.secrets.update(_dashboard_secrets())
        for k, v in _authed_session().items():
            at.session_state[k] = v
        at.run(timeout=30)
    assert list(at.exception) == []
    assert seen.get("max_workers", 1) > 1
