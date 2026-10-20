"""Pure logic for the Settings tab (Phase F). Persists sender selection
and daily limits into the campaign's EXISTING config override file
(config/campaigns/<name>.yaml) — the same file outreach.get_campaign
already deep-merges, not a new config surface. Only the 'sending' key's
relevant fields are ever touched; status, schedule, stages, variants, and
anything else already in that file are preserved exactly as they were.
"""
import os
import sys
from typing import Dict, List, Optional, Tuple

import yaml

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import outreach  # noqa: E402


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


def merge_live_override_into_cfg(campaign_cfg: Dict, live_override: Dict) -> Dict:
    """Overlays a LIVE override file on top of an already-merged
    campaign_cfg, returning a new dict (never mutating the input, which
    may be a cached object shared across reruns).

    campaign_cfg is built from the local checkout — defaults from
    settings.yaml merged with whatever override file happened to be on
    disk at deploy time. On Streamlit Cloud that disk copy is frozen
    until a redeploy, so the Settings and Schedule tabs displayed stale
    values indefinitely even though the save had committed to GitHub
    correctly. Overlaying the live override fixes the DISPLAY side the
    same way load_raw_override_live fixed the read-modify-write side.

    Only the keys an override file can legitimately contain are
    overlaid, and only when present, so nothing else in the merged
    config is disturbed. `sending` and `schedule` are merged key-by-key
    rather than replaced wholesale — an override that sets only
    daily_limit must not silently drop the other sending keys that came
    from defaults.
    """
    if not live_override:
        return campaign_cfg
    merged = dict(campaign_cfg)
    for section in ("sending", "schedule", "asana", "tracker_sync"):
        if section in live_override:
            base = dict(merged.get(section) or {})
            incoming = live_override[section]
            if isinstance(incoming, dict):
                base.update(incoming)
                merged[section] = base
            else:
                merged[section] = incoming
    for scalar in ("status", "previous_status"):
        if scalar in live_override:
            merged[scalar] = live_override[scalar]
    waits = live_override.get("stage_wait_days")
    if isinstance(waits, dict):
        # This campaign's own follow-up waits, as the engine will read them. A value the engine would refuse is not shown (the page keeps the shared
        # waits); the engine itself reports it when it next loads the campaign.
        try:
            valid = outreach.parse_stage_wait_overrides(merged.get("_campaign_name", ""), waits)
        except outreach.ConfigError:
            valid = {}
        merged["stage_wait_days"] = valid
        merged["stages"] = outreach.apply_stage_wait_overrides(merged.get("stages") or [], valid)
    return merged


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


def build_footer_settings_override(raw_override: Dict, business_name: str, address: str, opt_out: str) -> Dict:
    """The campaign's settings with its email footer (an HTML campaign's) set. Values are trimmed; a blank opt-out wording is left out so the default wording is used. Every other
    setting is carried over untouched."""
    updated = dict(raw_override or {})
    footer = {"business_name": (business_name or "").strip(), "address": (address or "").strip()}
    if (opt_out or "").strip():
        footer["opt_out"] = opt_out.strip()
    updated["footer"] = footer
    return updated


# ---------------------------------------------------------------- time between sends (the wait between sending rounds)

PACING_PRESETS = [("careful", "Careful", 5.0, 10.0), ("standard", "Standard", 3.0, 7.0), ("faster", "Faster", 1.0, 3.0)]
PACING_FALLBACK = (3.0, 7.0)         # the shared default if config/settings.yaml cannot be read
PACING_MIN, PACING_MAX, PACING_STEP = 0.5, 120.0, 0.5
SHORT_GAP_WARNING_MINUTES = 2.0       # gaps shorter than this at the longest look like bulk sending to mail providers
RUN_TIME_WARNING_MINUTES = 300.0      # a send run is stopped by GitHub at 360 minutes; a run stopped mid-way can repeat an email
RUN_TIME_LIMIT_MINUTES = 360.0


def clamp_pacing(minutes: float) -> float:
    """A wait as the card can SHOW it: pulled into the range its boxes offer (the engine also accepts 0 and values above 120, which the boxes cannot display)."""
    return min(PACING_MAX, max(PACING_MIN, float(minutes)))


def format_minutes(minutes: float) -> str:
    """'30 seconds', '1 minute', '3 minutes', '2.5 minutes': a gap, in plain words."""
    if minutes < 1:
        return f"{round(minutes * 60)} seconds"
    shown = int(minutes) if float(minutes).is_integer() else round(minutes, 1)
    return f"{shown} minute" + ("" if shown == 1 else "s")


def format_duration(minutes: float) -> str:
    """'45 min', '2 h', '2 h 5 min': how long a whole run takes."""
    total = int(round(minutes))
    hours, rest = divmod(total, 60)
    if hours == 0:
        return f"{rest} min"
    return f"{hours} h" + (f" {rest} min" if rest else "")


def global_default_pacing(settings: Optional[Dict]) -> Tuple[float, float]:
    """The wait every campaign starts with (config/settings.yaml), or the standard 3 to 7 minutes if it cannot be read."""
    try:
        sending = settings["default_campaign_settings"]["sending"]
        low, high = float(sending["delay_min_minutes"]), float(sending["delay_max_minutes"])
        return (low, high) if 0 <= low <= high else PACING_FALLBACK
    except (KeyError, TypeError, ValueError):
        return PACING_FALLBACK


def preset_for(low: float, high: float) -> str:
    """The key of the preset these two numbers are, or 'custom'."""
    for key, _label, preset_low, preset_high in PACING_PRESETS:
        if abs(low - preset_low) < 1e-9 and abs(high - preset_high) < 1e-9:
            return key
    return "custom"


def validate_pacing(low: float, high: float) -> List[str]:
    """The same rules the engine applies when it loads the campaign, plus the limits the screen offers, so Settings can never save what would not load."""
    errors = []
    for label, value in (("The shortest wait", low), ("The longest wait", high)):
        if value is None or value != value or isinstance(value, bool):
            errors.append(f"{label} must be a number of minutes.")
        elif value < PACING_MIN or value > PACING_MAX:
            errors.append(f"{label} must be between {format_minutes(PACING_MIN)} and {int(PACING_MAX)} minutes.")
    if not errors and low > high:
        errors.append("The shortest wait cannot be longer than the longest wait.")
    return errors


def build_pacing_override(raw_override: Dict, low: Optional[float], high: Optional[float]) -> Dict:
    """The campaign's settings with its own wait between sends set. Passing None, None removes the campaign's own wait, so it goes back to the shared default. Only the two
    wait keys under `sending` are touched; every other setting (daily limit, accounts, schedule, ...) is carried over untouched."""
    updated = dict(raw_override or {})
    sending = dict(updated.get("sending") or {}) if isinstance(updated.get("sending"), dict) else {}
    if low is None or high is None:
        sending.pop("delay_min_minutes", None)
        sending.pop("delay_max_minutes", None)
    else:
        sending["delay_min_minutes"] = _plain_number(low)
        sending["delay_max_minutes"] = _plain_number(high)
    if sending:
        updated["sending"] = sending
    else:
        updated.pop("sending", None)
    return updated


def _plain_number(value: float):
    """3.0 is saved as 3 and 2.5 as 2.5, so the settings file stays tidy."""
    return int(value) if float(value).is_integer() else float(value)


def pacing_notes(daily_limit: int, mailboxes: int, low: float, high: float) -> List[Tuple[str, str]]:
    """[(kind, sentence)] telling what these two numbers mean in practice: how often each mailbox sends, how long the whole daily limit takes, and any danger. Kinds are the
    note styles 'info', 'ok' and 'warn'. A run waits between rounds (one email per mailbox per round), so n emails over m mailboxes take (rounds - 1) waits."""
    mailboxes = max(1, int(mailboxes))
    average = (low + high) / 2
    notes: List[Tuple[str, str]] = []
    if average > 0:
        per_hour = 60 / average
        notes.append(("info", f"Each mailbox sends about one email every {format_minutes(average)} (around {per_hour:.0f} an hour)."))
    rounds = -(-max(1, int(daily_limit)) // mailboxes)
    typical, longest = (rounds - 1) * average, (rounds - 1) * high
    if rounds > 1:
        notes.append(("info", f"Sending the whole daily limit of {int(daily_limit)} across {mailboxes} mailbox{'es' if mailboxes != 1 else ''} takes about {format_duration(typical)}"
                              f" (at most {format_duration(longest)})."))
    if longest > RUN_TIME_WARNING_MINUTES:
        notes.append(("warn", f"A run this long ({format_duration(longest)} at most) can be cut off by GitHub's {int(RUN_TIME_LIMIT_MINUTES // 60)}-hour limit, and a run cut off part-way can repeat "
                              "an email. Use more mailboxes, a lower daily limit or shorter waits."))
    if 0 < high < SHORT_GAP_WARNING_MINUTES:
        notes.append(("warn", "Gaps this short look like bulk sending to mail providers and can hurt delivery. A few minutes is safer."))
    if high == low and high > 0:
        notes.append(("info", "The wait is exactly the same every time. A range (for example 3 to 7) looks more natural to mail providers."))
    return notes


def build_tracking_settings_override(raw_override: Dict, enabled: bool) -> Dict:
    """The campaign's settings with link tagging switched on or off. Anything else already under `tracking` (a custom source or medium) is kept, and every other setting is
    carried over untouched."""
    updated = dict(raw_override or {})
    tracking = dict(updated.get("tracking") or {}) if isinstance(updated.get("tracking"), dict) else {}
    tracking["utm"] = bool(enabled)
    updated["tracking"] = tracking
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
