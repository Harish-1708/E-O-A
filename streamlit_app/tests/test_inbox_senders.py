"""The accounts a reply can be sent from: secrets and the live slot file merged, with health that can never break the Inbox."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from inbox_senders import build_sender_options  # noqa: E402


def _slots(tmp_path, text="sales2:\n  slot: 1\n  address: new-sales2@kelson.agency\nsales3:\n  slot: 2\n  address: sales3@kelson.agency\n"):
    path = tmp_path / "email_account_slots.yaml"
    path.write_text(text)
    return str(path)


def test_the_directory_merges_the_secrets_with_the_slot_file_and_the_slot_file_wins(tmp_path):
    options = build_sender_options({"sales1": "sales1@kelson.agency", "sales2": "old-sales2@kelson.agency"}, None, _slots(tmp_path), lambda: {})
    assert options["directory"] == {"sales1": "sales1@kelson.agency", "sales2": "new-sales2@kelson.agency", "sales3": "sales3@kelson.agency"}


def test_health_is_passed_through_as_the_lookup_it_is(tmp_path):
    health = {"sales1": {"status": "Connected", "detail": "", "checked_at": "x"}}
    assert build_sender_options({"sales1": "a@x"}, None, _slots(tmp_path), lambda: health)["health"] == health


def test_a_failing_health_lookup_gives_no_health_and_never_an_error(tmp_path):
    def boom():
        raise RuntimeError("sheet unreachable")
    options = build_sender_options({"sales1": "a@x"}, None, _slots(tmp_path), boom)
    assert options == {"directory": {"sales1": "a@x", "sales2": "new-sales2@kelson.agency", "sales3": "sales3@kelson.agency"}, "health": {}}


def test_a_health_lookup_that_returns_nothing_gives_an_empty_health(tmp_path):
    assert build_sender_options({}, None, _slots(tmp_path), lambda: None)["health"] == {}


def test_with_no_secrets_and_no_slot_file_there_are_simply_no_accounts(tmp_path):
    assert build_sender_options(None, None, str(tmp_path / "missing.yaml"), lambda: {}) == {"directory": {}, "health": {}}


def test_the_callers_directory_is_never_changed(tmp_path):
    secrets = {"sales1": "a@x"}
    build_sender_options(secrets, None, _slots(tmp_path), lambda: {})
    assert secrets == {"sales1": "a@x"}
