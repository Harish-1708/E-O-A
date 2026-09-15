"""Tests for the LIVE (GitHub-backed) readers.

These all exist because of one systemic bug class: this app WRITES every
config change to GitHub via the API, but historically READ the same data
from the local checkout. On Streamlit Cloud that checkout is frozen until
a redeploy, so a change could be visibly committed on GitHub and never
appear in the UI — surviving a refresh, cache expiry, and even a full
logout/login, because none of those re-read the repository.

Each reader here must satisfy the same three-part contract:
  1. live GitHub content wins over a stale local copy
  2. a definitive "not there" (404 / empty) is reported as such, never
     silently backfilled from the stale local copy
  3. a genuine API failure degrades to the local copy rather than
     erroring out or showing a misleadingly empty result
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from email_account_slots_logic import read_slot_mapping_live  # noqa: E402
from preview_logic import list_campaigns_live  # noqa: E402


class _Client:
    def __init__(self, dirs=None, content=None, error=None):
        self._dirs, self._content, self._error = dirs, content, error

    def list_subdirectories(self, path, ref="main"):
        if self._error:
            raise Exception(self._error)
        return list(self._dirs or [])

    def get_file_content(self, path, ref="main"):
        if self._error:
            raise Exception(self._error)
        return self._content


# ---------- account slot mapping (the reported "removed accounts still show") ----------

def test_slot_mapping_live_reflects_removal_on_github(tmp_path):
    """The actual reported bug: 4 accounts removed, committed fine on
    GitHub, but the Email Accounts page kept showing them as connected."""
    stale = tmp_path / "slots.yaml"
    stale.write_text("sales1:\n  slot: 1\nsales2:\n  slot: 2\n")
    client = _Client(content=b"sales1:\n  slot: 1\n")
    assert list(read_slot_mapping_live(client, str(stale)).keys()) == ["sales1"]


def test_slot_mapping_live_all_accounts_removed_is_empty_not_stale(tmp_path):
    """Every account removed -> the file is gone (404). Falling back to
    disk here would show all of them as still connected."""
    stale = tmp_path / "slots.yaml"
    stale.write_text("sales1:\n  slot: 1\n")
    assert read_slot_mapping_live(_Client(error="404 Not Found"), str(stale)) == {}


def test_slot_mapping_live_api_failure_falls_back_to_disk(tmp_path):
    stale = tmp_path / "slots.yaml"
    stale.write_text("sales1:\n  slot: 1\n")
    assert list(read_slot_mapping_live(_Client(error="500 server"), str(stale)).keys()) == ["sales1"]


def test_slot_mapping_live_no_client_falls_back_to_disk(tmp_path):
    stale = tmp_path / "slots.yaml"
    stale.write_text("sales1:\n  slot: 1\n")
    assert list(read_slot_mapping_live(None, str(stale)).keys()) == ["sales1"]


# ---------- campaign list ----------

def test_list_campaigns_live_returns_github_state_sorted():
    """A campaign exists because its templates/<name>/ folder exists, so
    create / duplicate / delete all change GitHub immediately while the
    local checkout stays frozen."""
    assert list_campaigns_live(_Client(dirs=["Zeta", "Alpha"])) == ["Alpha", "Zeta"]


def test_list_campaigns_live_reflects_a_deleted_campaign():
    assert list_campaigns_live(_Client(dirs=["Alpha"])) == ["Alpha"]


def test_list_campaigns_live_api_failure_falls_back_without_raising():
    result = list_campaigns_live(_Client(error="500 server"))
    assert isinstance(result, list)  # local fallback, never an exception


def test_list_campaigns_live_no_client_falls_back_without_raising():
    assert isinstance(list_campaigns_live(None), list)
