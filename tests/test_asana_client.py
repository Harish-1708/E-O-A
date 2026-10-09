"""Tests for the UGC sync's minimal Asana client (asana_client.py)."""
import os
import sys

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asana_client


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code}", response=self)


@pytest.fixture
def no_sleep(monkeypatch):
    sleeps = []
    monkeypatch.setattr(asana_client.time, "sleep", lambda s: sleeps.append(s))
    return sleeps


# ---------- what the task list asks Asana for ----------

def test_the_task_list_requests_the_assignee_email_and_completed_flag(monkeypatch):
    """The assignee swap can only work if these come back. Pinned so
    trimming the field list for tidiness cannot silently turn the swap
    into a permanent no-op."""
    seen = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        seen["params"] = params
        return FakeResponse(200, {"data": [{"gid": "1"}], "next_page": None})

    monkeypatch.setattr(asana_client.requests, "get", fake_get)
    assert asana_client.get_all_project_tasks("P1", "tok") == [{"gid": "1"}]
    fields = seen["params"]["opt_fields"].split(",")
    assert "assignee.email" in fields and "completed" in fields
    # the fields the Sheet sync already relied on must still be there
    for needed in ("memberships.section.name", "custom_fields.name", "custom_fields.display_value", "permalink_url"):
        assert needed in fields


# ---------- get_user ----------

def test_get_user_returns_the_user_record_and_asks_for_it_by_email(monkeypatch):
    seen = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        seen["url"] = url
        return FakeResponse(200, {"data": {"gid": "42", "name": "A Person", "email": "a@x.com"}})

    monkeypatch.setattr(asana_client.requests, "get", fake_get)
    user = asana_client.get_user("a@x.com", "tok")
    assert user["gid"] == "42"
    assert seen["url"].endswith("/users/a@x.com")  # the @ must survive URL-encoding


def test_get_user_failure_is_a_clear_error_that_does_not_print_the_address(monkeypatch):
    monkeypatch.setattr(asana_client.requests, "get", lambda *a, **k: FakeResponse(404, text="not found"))
    with pytest.raises(RuntimeError) as err:
        asana_client.get_user("secret.person@example.com", "tok", label="the user to assign")
    message = str(err.value)
    assert "the user to assign" in message and "404" in message
    assert "secret.person" not in message and "@" not in message  # Actions logs are public


def test_get_user_with_an_empty_reply_is_an_error_not_a_silent_none(monkeypatch):
    monkeypatch.setattr(asana_client.requests, "get", lambda *a, **k: FakeResponse(200, {"data": {}}))
    with pytest.raises(RuntimeError):
        asana_client.get_user("a@x.com", "tok", label="the user to replace")


# ---------- update_task ----------

def test_update_task_sends_one_put_with_the_patch_in_a_data_envelope(monkeypatch, no_sleep):
    calls = []

    def fake_put(url, headers=None, json=None, timeout=None):
        calls.append((url, headers, json))
        return FakeResponse(200, {"data": {}})

    monkeypatch.setattr(asana_client.requests, "put", fake_put)
    asana_client.update_task("T9", {"assignee": "G7"}, "tok")
    assert len(calls) == 1
    url, headers, body = calls[0]
    assert url.endswith("/tasks/T9")
    assert body == {"data": {"assignee": "G7"}}
    assert headers["Authorization"] == "Bearer tok"
    assert no_sleep == []


def test_update_task_retries_a_server_error_then_succeeds(monkeypatch, no_sleep):
    results = iter([FakeResponse(503), FakeResponse(500), FakeResponse(200)])
    monkeypatch.setattr(asana_client.requests, "put", lambda *a, **k: next(results))
    asana_client.update_task("T1", {"assignee": "G"}, "tok")
    assert no_sleep == [2, 5]  # the normal back-off between attempts


def test_update_task_honours_retry_after_on_a_429_but_caps_it(monkeypatch, no_sleep):
    results = iter([FakeResponse(429, headers={"Retry-After": "7"}), FakeResponse(429, headers={"Retry-After": "9999"}),
                    FakeResponse(200)])
    monkeypatch.setattr(asana_client.requests, "put", lambda *a, **k: next(results))
    asana_client.update_task("T1", {"assignee": "G"}, "tok")
    assert 7.0 in no_sleep                       # waited what Asana asked for
    assert max(no_sleep) <= 60                   # but never an absurd amount
    assert 9999 not in no_sleep


def test_update_task_does_not_retry_a_rejection_that_retrying_cannot_fix(monkeypatch, no_sleep):
    n = {"calls": 0}

    def fake_put(*a, **k):
        n["calls"] += 1
        return FakeResponse(403, text="Not allowed")

    monkeypatch.setattr(asana_client.requests, "put", fake_put)
    with pytest.raises(asana_client.AsanaWriteError, match="403"):
        asana_client.update_task("T1", {"assignee": "G"}, "tok")
    assert n["calls"] == 1 and no_sleep == []


def test_update_task_retries_timeouts_and_connection_errors(monkeypatch, no_sleep):
    results = iter([requests.exceptions.Timeout("slow"), requests.exceptions.ConnectionError("reset"), FakeResponse(200)])

    def fake_put(*a, **k):
        r = next(results)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(asana_client.requests, "put", fake_put)
    asana_client.update_task("T1", {"assignee": "G"}, "tok")


def test_update_task_gives_up_with_a_specific_error_when_it_never_recovers(monkeypatch, no_sleep):
    monkeypatch.setattr(asana_client.requests, "put", lambda *a, **k: FakeResponse(502))
    with pytest.raises(asana_client.AsanaWriteError, match="still failing after retries"):
        asana_client.update_task("T1", {"assignee": "G"}, "tok")
    assert len(no_sleep) == len(asana_client._RETRY_DELAYS_SECONDS)
