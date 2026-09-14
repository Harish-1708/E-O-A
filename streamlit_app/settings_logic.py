"""Pure logic for the Settings tab (Phase F). Persists sender selection
and daily limits into the campaign's EXISTING config override file
(config/campaigns/<name>.yaml) — the same file outreach.get_campaign
already deep-merges, not a new config surface. Only the 'sending' key's
relevant fields are ever touched; status, schedule, stages, variants, and
anything else already in that file are preserved exactly as they were.
"""
import os
import sys
from typing import Dict, List, Optional

import yaml

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def load_raw_override(campaign_name: str, campaigns_dir: str) -> Dict:
    """The override file's raw content, exactly as it is on disk — NOT
    merged with defaults (unlike campaign_cfg, which is always fully
    merged). Returns {} if the file doesn't exist yet — every campaign is
    valid without one (auto-discovery covers that case)."""
    path = os.path.join(campaigns_dir, f"{campaign_name}.yaml")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_raw_override_live(campaign_name: str, github_client, campaigns_dir: str,
                            branch: str = "main") -> Dict:
    """The override file's raw content read LIVE from GitHub, falling
    back to the local checkout only if GitHub can't be reached.

    Why this exists — the actual reported bug: every settings write in
    this app commits to GitHub via the API, but reads came from the
    LOCAL checkout on disk. On Streamlit Cloud that checkout only
    changes when the app redeploys, so a saved change could be visibly
    committed on GitHub and still never appear in the UI — surviving a
    refresh, a cache expiry, and even a full logout/login, because
    nothing about those re-reads the repository. The user sees "saved
    successfully", GitHub shows the commit, and the app keeps showing
    the old value indefinitely.

    Reading live closes that gap the same way the Sequences tab already
    does for template files, which hit this identical problem earlier.

    The local-disk fallback keeps this safe rather than fragile: if the
    GitHub token is missing or the API call fails, behaviour degrades to
    exactly what it was before instead of erroring out. A file that
    genuinely doesn't exist yet returns {} — every campaign is valid
    without an override file.
    """
    if github_client is not None:
        try:
            content = github_client.get_file_content(
                f"config/campaigns/{campaign_name}.yaml", ref=branch)
            if content is None:
                return {}
            return yaml.safe_load(content.decode("utf-8")) or {}
        except Exception as exc:  # noqa: BLE001
            # A 404 is a definitive answer, not a failure: the override
            # file genuinely does not exist on the branch, which is a
            # normal, valid state. Returning {} here matters — falling
            # back to disk instead could resurrect a stale local copy of
            # a file that was deliberately deleted.
            if "404" in str(exc):
                return {}
            # Anything else (network, auth, rate limit) is a real failure
            # to READ, so degrade to the local checkout rather than
            # erroring out or showing a misleadingly empty config.
            pass
    return load_raw_override(campaign_name, campaigns_dir)


def validate_settings(daily_limit: int, per_account_daily_limit: Optional[int]) -> List[str]:
    """Mirrors outreach.apply_sending_overrides' own validation rules, so
    Settings can never persist a value the core system would itself
    reject when it later loads this same file."""
    errors = []
    if daily_limit <= 0:
        errors.append("Daily limit must be a positive number.")
    if per_account_daily_limit is not None and per_account_daily_limit <= 0:
        errors.append("Per-account daily limit must be a positive number, or left blank for no limit.")
    return errors


def build_updated_override(raw_override: Dict, daily_limit: int, per_account_daily_limit: Optional[int],
                            sender_rotation: bool, rotation_accounts: List[str]) -> Dict:
    """Returns a NEW dict — never mutates raw_override. Only 'sending' is
    touched; every other top-level key (status, schedule, stages,
    variants, reply_monitor, ...) passes through untouched."""
    updated = dict(raw_override)
    sending = dict(updated.get("sending", {}))

    sending["daily_limit"] = daily_limit
    if per_account_daily_limit is not None:
        sending["per_account_daily_limit"] = per_account_daily_limit
    elif "per_account_daily_limit" in sending:
        del sending["per_account_daily_limit"]

    sending["sender_rotation"] = sender_rotation
    if rotation_accounts:
        sending["rotation_accounts"] = rotation_accounts
    elif "rotation_accounts" in sending:
        del sending["rotation_accounts"]

    updated["sending"] = sending
    return updated


def build_asana_settings_override(raw_override: Dict, enabled: bool, project_name: str) -> Dict:
    """Returns a NEW dict — never mutates raw_override. Only the 'asana'
    key is touched; everything else passes through untouched, same
    guarantee build_updated_override makes for 'sending'."""
    updated = dict(raw_override)
    updated["asana"] = {"enabled": enabled, "project_name": project_name}
    return updated


def build_tracker_sync_settings_override(raw_override: Dict, enabled: bool) -> Dict:
    """Returns a NEW dict — never mutates raw_override. Only the
    'tracker_sync' key is touched. No project-name-style field here —
    the Creator Tracker sheet's own spreadsheet ID and worksheet name
    are shared secrets, not per-campaign settings; only whether THIS
    campaign's leads should sync to it at all is a per-campaign
    choice."""
    updated = dict(raw_override)
    updated["tracker_sync"] = {"enabled": enabled}
    return updated


def override_to_yaml_bytes(override: Dict) -> bytes:
    return yaml.safe_dump(override, sort_keys=False, default_flow_style=False).encode("utf-8")


def override_file_path(campaign_name: str) -> str:
    return f"config/campaigns/{campaign_name}.yaml"
