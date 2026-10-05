"""Page-level proof of the GitHub read cache.

Deliberately NOT in test_pages_smoke.py: that module has an autouse
fixture which replaces GitHubClient.get_file_content and
list_directory_files for every test in it, which would bypass the very
cache layer (and the HTTP behind it) this file exists to exercise. Here
only requests.get is faked, so the REAL GitHubClient and its REAL cache
run exactly as they do in production.
"""
import base64
import os
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import github_client
from test_pages_smoke import (
    FakeWorksheet, FakeSpreadsheet, FIXTURE_CAMPAIGN, PAGES_DIR, _dashboard_secrets, _authed_session,
)


class _FakeRepo:
    """A fake GitHub shaped like a real, mature campaign — 6 stages x 4
    variants = 24 template files — whose head commit can be advanced
    mid-test, with every request counted."""

    def __init__(self):
        self.sha = "c" * 40
        self.stages = ["intro"] + [f"followup{i}" for i in range(1, 6)]
        self.counts = {"head": 0, "contents": 0}

    def _files(self):
        return {f"templates/{FIXTURE_CAMPAIGN}/{s}_{v}.txt": f"Subject: {s} {v}\n\nBody {s} {v}".encode()
                for s in self.stages for v in "ABCD"}

    def push_new_stage(self, stage, new_sha):
        self.stages.append(stage)
        self.sha = new_sha

    def reset_counts(self):
        self.counts.update(head=0, contents=0)

    def get(self, url, headers=None, params=None, timeout=None):
        class _R:
            def __init__(s, status, text="", payload=None):
                s.status_code, s.text, s._payload = status, text, payload

            def json(s):
                return s._payload

        if url.endswith("/commits/main"):
            self.counts["head"] += 1
            return _R(200, text=self.sha)
        self.counts["contents"] += 1
        path = url.split("/contents/", 1)[1]
        files = self._files()
        if path in files:
            return _R(200, payload={"content": base64.b64encode(files[path]).decode()})
        if path == f"templates/{FIXTURE_CAMPAIGN}":
            return _R(200, payload=[{"name": n.rsplit("/", 1)[1], "type": "file"} for n in files])
        if path.startswith("config/campaigns/"):
            return _R(200, payload={"content": base64.b64encode(b"status: active\n").decode()})
        if "slots" in path:
            return _R(200, payload={"content": base64.b64encode(b"{}\n").decode()})
        if path == "templates":
            return _R(200, payload=[{"name": FIXTURE_CAMPAIGN, "type": "dir"}])
        return _R(404)


def _open_campaign_page(repo, clock):
    fake_ws = {
        f"{FIXTURE_CAMPAIGN} Master Sheet": FakeWorksheet([{
            "Email": "a@abc.com", "Approval": "Yes", "IntroSentAt": "", "IntroVariant": "",
            "SenderAccount": "", "Status": ""}]),
        f"{FIXTURE_CAMPAIGN} Response Sheet": FakeWorksheet([]),
        f"{FIXTURE_CAMPAIGN} Custom Log Sheet": FakeWorksheet([]),
        f"{FIXTURE_CAMPAIGN} Error Log": FakeWorksheet([]),
    }
    fake_spreadsheet = FakeSpreadsheet(fake_ws)
    patches = [
        patch("gspread.authorize", return_value=type("C", (), {"open_by_key": lambda self, k: fake_spreadsheet})()),
        patch("google.oauth2.service_account.Credentials.from_service_account_info", return_value=object()),
        patch("github_client.requests.get", repo.get),
        patch("github_client.time.monotonic", lambda: clock["t"]),
    ]
    at = AppTest.from_file(os.path.join(PAGES_DIR, "campaigns.py"))
    at.secrets.update(_dashboard_secrets())
    for k, v in _authed_session().items():
        at.session_state[k] = v
    at.session_state["selected_campaign"] = FIXTURE_CAMPAIGN
    return at, patches


def _next_stage_shown(at):
    for m in at.markdown:
        if "Next stage:" in m.value:
            return m.value
    return ""


def test_campaign_page_reruns_make_no_github_content_calls_after_the_first_load(fixture_repo):
    """The actual reported slowness: every rerun of a campaign page (any
    click, any widget change) re-read every template file, the campaign
    config and the stage listing from GitHub, one sequential request at
    a time — dozens per rerun for a mature campaign. After the first
    load, an unchanged repo must cost no content reads at all."""
    repo, clock = _FakeRepo(), {"t": 1000.0}
    at, patches = _open_campaign_page(repo, clock)
    with patches[0], patches[1], patches[2], patches[3]:
        at.run(timeout=30)
        first_load = dict(repo.counts)
        repo.reset_counts()
        at.run(timeout=30)
        rerun = dict(repo.counts)
        print(f"\n[profile] first load: {first_load}   later rerun (unchanged repo): {rerun}")

    assert list(at.exception) == []
    assert first_load["contents"] >= 24      # the real, unavoidable first read of all 24 templates
    assert rerun["contents"] == 0            # ...and never again while nothing has changed
    assert rerun["head"] <= 1                # at most one tiny head-commit lookup


def test_a_new_commit_shows_up_on_the_page_without_waiting_for_any_long_cache_expiry(fixture_repo):
    """The other half — speed must not bring the stale-page bugs back.
    After someone commits a new follow-up stage, the very next rerun
    past the short head-lookup window must show it (the "Next stage"
    moves on from followup6 to followup7), then go back to costing
    nothing while that new state stays unchanged."""
    repo, clock = _FakeRepo(), {"t": 1000.0}
    at, patches = _open_campaign_page(repo, clock)
    with patches[0], patches[1], patches[2], patches[3]:
        at.run(timeout=30)
        assert "followup6" in _next_stage_shown(at)

        repo.push_new_stage("followup6", "d" * 40)
        clock["t"] += github_client._HEAD_SHA_TTL_SECONDS + 0.5
        repo.reset_counts()
        at.run(timeout=30)
        assert "followup7" in _next_stage_shown(at)   # the new commit is visible immediately
        assert repo.counts["contents"] > 0            # it genuinely re-read, rather than guessing

        repo.reset_counts()
        at.run(timeout=30)
        assert repo.counts["contents"] == 0           # and is cached again once it's the current state
    assert list(at.exception) == []
