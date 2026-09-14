import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from settings_logic import (
    load_raw_override_live,
    load_raw_override, validate_settings, build_updated_override,
    override_to_yaml_bytes, override_file_path, build_asana_settings_override,
    build_tracker_sync_settings_override,
)


# ---------- load_raw_override ----------

def test_load_raw_override_returns_empty_dict_when_file_missing(tmp_path):
    assert load_raw_override("NoSuchCampaign", str(tmp_path)) == {}


def test_load_raw_override_reads_existing_file(tmp_path):
    (tmp_path / "Foo.yaml").write_text("status: paused\nsending:\n  daily_limit: 50\n")
    result = load_raw_override("Foo", str(tmp_path))
    assert result == {"status": "paused", "sending": {"daily_limit": 50}}


def test_load_raw_override_empty_file_returns_empty_dict(tmp_path):
    (tmp_path / "Foo.yaml").write_text("")
    assert load_raw_override("Foo", str(tmp_path)) == {}


# ---------- validate_settings ----------

def test_validate_settings_valid():
    assert validate_settings(100, 20) == []


def test_validate_settings_valid_with_no_per_account_limit():
    assert validate_settings(100, None) == []


def test_validate_settings_rejects_non_positive_daily_limit():
    assert len(validate_settings(0, None)) == 1
    assert len(validate_settings(-5, None)) == 1


def test_validate_settings_rejects_non_positive_per_account_limit():
    assert len(validate_settings(100, 0)) == 1
    assert len(validate_settings(100, -1)) == 1


def test_validate_settings_reports_both_errors_at_once():
    errors = validate_settings(0, -1)
    assert len(errors) == 2


# ---------- build_updated_override — the "preserve everything else" guarantee ----------

def test_build_updated_override_preserves_status_and_other_top_level_keys():
    raw = {"status": "paused", "schedule": {"timezone": "America/Los_Angeles"}, "sending": {"daily_limit": 50}}
    updated = build_updated_override(raw, daily_limit=200, per_account_daily_limit=None,
                                      sender_rotation=False, rotation_accounts=[])
    assert updated["status"] == "paused"
    assert updated["schedule"] == {"timezone": "America/Los_Angeles"}
    assert updated["sending"]["daily_limit"] == 200


def test_build_updated_override_never_mutates_input():
    raw = {"status": "active", "sending": {"daily_limit": 50}}
    build_updated_override(raw, daily_limit=200, per_account_daily_limit=None,
                            sender_rotation=False, rotation_accounts=[])
    assert raw == {"status": "active", "sending": {"daily_limit": 50}}  # untouched


def test_build_updated_override_preserves_stages_and_variants_if_explicit():
    raw = {"stages": [{"name": "intro"}], "variants": ["A"], "sending": {}}
    updated = build_updated_override(raw, daily_limit=100, per_account_daily_limit=None,
                                      sender_rotation=False, rotation_accounts=[])
    assert updated["stages"] == [{"name": "intro"}]
    assert updated["variants"] == ["A"]


def test_build_updated_override_sets_per_account_limit_when_given():
    updated = build_updated_override({}, daily_limit=100, per_account_daily_limit=20,
                                      sender_rotation=True, rotation_accounts=["sales1"])
    assert updated["sending"]["per_account_daily_limit"] == 20


def test_build_updated_override_removes_per_account_limit_when_none_and_previously_set():
    raw = {"sending": {"per_account_daily_limit": 20}}
    updated = build_updated_override(raw, daily_limit=100, per_account_daily_limit=None,
                                      sender_rotation=False, rotation_accounts=[])
    assert "per_account_daily_limit" not in updated["sending"]


def test_build_updated_override_sets_rotation_accounts_when_given():
    updated = build_updated_override({}, daily_limit=100, per_account_daily_limit=None,
                                      sender_rotation=True, rotation_accounts=["sales1", "sales2"])
    assert updated["sending"]["rotation_accounts"] == ["sales1", "sales2"]


def test_build_updated_override_removes_rotation_accounts_when_empty_and_previously_set():
    raw = {"sending": {"rotation_accounts": ["sales1"]}}
    updated = build_updated_override(raw, daily_limit=100, per_account_daily_limit=None,
                                      sender_rotation=False, rotation_accounts=[])
    assert "rotation_accounts" not in updated["sending"]


def test_build_updated_override_from_completely_empty_raw():
    updated = build_updated_override({}, daily_limit=100, per_account_daily_limit=None,
                                      sender_rotation=False, rotation_accounts=[])
    assert updated == {"sending": {"daily_limit": 100, "sender_rotation": False}}


# ---------- override_to_yaml_bytes / override_file_path ----------

def test_override_to_yaml_bytes_round_trips():
    import yaml
    override = {"status": "active", "sending": {"daily_limit": 100}}
    raw = override_to_yaml_bytes(override)
    assert yaml.safe_load(raw.decode("utf-8")) == override


def test_override_file_path_format():
    assert override_file_path("DudeRobe") == "config/campaigns/DudeRobe.yaml"


def test_full_round_trip_load_edit_save_reload(tmp_path):
    """The real end-to-end contract: write a file, load it, edit it,
    serialize it, write it back, load it again — confirms nothing is
    lost or corrupted across the whole cycle."""
    original_path = tmp_path / "Foo.yaml"
    original_path.write_text("status: paused\nsending:\n  daily_limit: 50\n  sender_rotation: true\n")

    raw = load_raw_override("Foo", str(tmp_path))
    updated = build_updated_override(raw, daily_limit=999, per_account_daily_limit=30,
                                      sender_rotation=True, rotation_accounts=["sales1"])
    yaml_bytes = override_to_yaml_bytes(updated)
    original_path.write_bytes(yaml_bytes)  # simulate the commit landing back at the same path

    reloaded = load_raw_override("Foo", str(tmp_path))
    assert reloaded["status"] == "paused"  # preserved
    assert reloaded["sending"]["daily_limit"] == 999
    assert reloaded["sending"]["per_account_daily_limit"] == 30
    assert reloaded["sending"]["rotation_accounts"] == ["sales1"]


# ---------- build_asana_settings_override ----------

def test_build_asana_settings_override_sets_enabled_and_project_name():
    updated = build_asana_settings_override({}, enabled=True, project_name="Creator Outreach")
    assert updated["asana"] == {"enabled": True, "project_name": "Creator Outreach"}


def test_build_asana_settings_override_preserves_other_keys():
    raw = {"sending": {"daily_limit": 100}, "status": "active"}
    updated = build_asana_settings_override(raw, enabled=True, project_name="Creator Outreach")
    assert updated["sending"] == {"daily_limit": 100}
    assert updated["status"] == "active"


def test_build_asana_settings_override_never_mutates_input():
    raw = {"status": "active"}
    build_asana_settings_override(raw, enabled=True, project_name="Creator Outreach")
    assert raw == {"status": "active"}


def test_build_asana_settings_override_disabled():
    updated = build_asana_settings_override({}, enabled=False, project_name="")
    assert updated["asana"]["enabled"] is False


# ---------- build_tracker_sync_settings_override ----------

def test_build_tracker_sync_settings_override_sets_enabled():
    updated = build_tracker_sync_settings_override({}, enabled=True)
    assert updated["tracker_sync"] == {"enabled": True}


def test_build_tracker_sync_settings_override_preserves_other_keys():
    raw = {"asana": {"enabled": True, "project_name": "X"}, "status": "active"}
    updated = build_tracker_sync_settings_override(raw, enabled=True)
    assert updated["asana"] == {"enabled": True, "project_name": "X"}
    assert updated["status"] == "active"


def test_build_tracker_sync_settings_override_never_mutates_input():
    raw = {"status": "active"}
    build_tracker_sync_settings_override(raw, enabled=True)
    assert raw == {"status": "active"}


def test_build_tracker_sync_settings_override_disabled():
    updated = build_tracker_sync_settings_override({}, enabled=False)
    assert updated["tracker_sync"]["enabled"] is False


# ---------- live override reads (settings not reflecting after save) ----------

class _FakeGitHubClient:
    def __init__(self, content=None, error=None):
        self._content = content
        self._error = error
        self.calls = []

    def get_file_content(self, path, ref="main"):
        self.calls.append((path, ref))
        if self._error:
            raise Exception(self._error)
        return self._content


def test_load_raw_override_live_prefers_github_over_stale_disk(tmp_path):
    """The actual reported bug: writes commit to GitHub but reads came
    from the LOCAL checkout, which on Streamlit Cloud only changes on
    redeploy. A saved change was visibly committed on GitHub yet never
    appeared in the UI — surviving refresh, cache expiry and even a full
    logout/login, because none of those re-read the repository."""
    (tmp_path / "X.yaml").write_text("status: active\n")   # stale local copy
    client = _FakeGitHubClient(content=b"status: paused\n")  # live truth

    result = load_raw_override_live("X", client, str(tmp_path))

    assert result == {"status": "paused"}
    assert client.calls == [("config/campaigns/X.yaml", "main")]


def test_load_raw_override_live_404_returns_empty_not_stale_disk(tmp_path):
    """A 404 is a definitive answer, not a failure — the file genuinely
    isn't on the branch. Falling back to disk here would resurrect a
    local copy of a deliberately deleted override."""
    (tmp_path / "X.yaml").write_text("status: active\n")
    client = _FakeGitHubClient(error="Failed to read: 404 Not Found")
    assert load_raw_override_live("X", client, str(tmp_path)) == {}


def test_load_raw_override_live_falls_back_to_disk_on_api_failure(tmp_path):
    """A real read failure (network, auth, rate limit) must degrade to
    the previous behaviour rather than erroring or showing empty config."""
    (tmp_path / "X.yaml").write_text("status: active\n")
    client = _FakeGitHubClient(error="500 server error")
    assert load_raw_override_live("X", client, str(tmp_path)) == {"status": "active"}


def test_load_raw_override_live_falls_back_when_no_client(tmp_path):
    (tmp_path / "X.yaml").write_text("status: active\n")
    assert load_raw_override_live("X", None, str(tmp_path)) == {"status": "active"}


def test_load_raw_override_live_missing_everywhere_is_empty(tmp_path):
    client = _FakeGitHubClient(error="404")
    assert load_raw_override_live("NoSuch", client, str(tmp_path)) == {}
