import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from schedule_logic import (
    validate_schedule, build_updated_schedule_override, get_current_schedule,
    timezone_display_name, COMMON_TIMEZONES, WEEKDAY_DEFAULTS,
)


# ---------- validate_schedule ----------

def test_validate_schedule_valid():
    assert validate_schedule("America/Los_Angeles", "09:00", "17:00", ["mon", "tue"]) == []


def test_validate_schedule_rejects_unknown_timezone():
    errors = validate_schedule("Not/A/Zone", "09:00", "17:00", ["mon"])
    assert len(errors) == 1
    assert "recognized timezone" in errors[0]


def test_validate_schedule_rejects_bad_time_format():
    errors = validate_schedule("UTC", "9am", "5pm", ["mon"])
    assert len(errors) == 2


def test_validate_schedule_rejects_no_days_selected():
    errors = validate_schedule("UTC", "09:00", "17:00", [])
    assert len(errors) == 1
    assert "at least one day" in errors[0]


def test_validate_schedule_reports_multiple_errors_at_once():
    errors = validate_schedule("Bad/Zone", "bad", "bad", [])
    assert len(errors) == 4


def test_validate_schedule_every_common_timezone_is_actually_valid():
    """Guards against a typo in the curated COMMON_TIMEZONES list itself —
    every IANA name offered in the dropdown must actually be real."""
    for _, iana_name in COMMON_TIMEZONES:
        assert validate_schedule(iana_name, "09:00", "17:00", ["mon"]) == []


# ---------- build_updated_schedule_override ----------

def test_build_updated_schedule_override_sets_schedule_key():
    updated = build_updated_schedule_override({}, "America/Los_Angeles", "09:00", "17:00", ["mon", "tue"])
    assert updated["schedule"] == {
        "timezone": "America/Los_Angeles", "window_start": "09:00", "window_end": "17:00",
        "send_days": ["mon", "tue"],
    }


def test_build_updated_schedule_override_preserves_other_keys():
    raw = {"status": "paused", "sending": {"daily_limit": 50}}
    updated = build_updated_schedule_override(raw, "UTC", "09:00", "17:00", ["mon"])
    assert updated["status"] == "paused"
    assert updated["sending"] == {"daily_limit": 50}


def test_build_updated_schedule_override_never_mutates_input():
    raw = {"status": "active"}
    build_updated_schedule_override(raw, "UTC", "09:00", "17:00", ["mon"])
    assert raw == {"status": "active"}


def test_build_updated_schedule_override_replaces_existing_schedule_wholesale():
    raw = {"schedule": {"timezone": "UTC", "window_start": "00:00", "window_end": "23:59", "send_days": ["sun"]}}
    updated = build_updated_schedule_override(raw, "America/New_York", "10:00", "18:00", ["mon", "fri"])
    assert updated["schedule"]["timezone"] == "America/New_York"
    assert updated["schedule"]["send_days"] == ["mon", "fri"]


# ---------- get_current_schedule ----------

def test_get_current_schedule_defaults_when_none_configured():
    schedule = get_current_schedule({})
    assert schedule["timezone"] == "America/Los_Angeles"
    assert schedule["window_start"] == "09:00"
    assert schedule["window_end"] == "17:00"
    assert schedule["send_days"] == WEEKDAY_DEFAULTS


def test_get_current_schedule_reads_existing_values():
    campaign_cfg = {"schedule": {"timezone": "UTC", "window_start": "08:00", "window_end": "16:00",
                                  "send_days": ["mon"]}}
    schedule = get_current_schedule(campaign_cfg)
    assert schedule == {"timezone": "UTC", "window_start": "08:00", "window_end": "16:00", "send_days": ["mon"]}


def test_get_current_schedule_partial_config_fills_in_defaults():
    campaign_cfg = {"schedule": {"timezone": "UTC"}}
    schedule = get_current_schedule(campaign_cfg)
    assert schedule["timezone"] == "UTC"
    assert schedule["window_start"] == "09:00"  # filled in
    assert schedule["send_days"] == WEEKDAY_DEFAULTS  # filled in


# ---------- timezone_display_name ----------

def test_timezone_display_name_known():
    assert timezone_display_name("America/Los_Angeles") == "Pacific Time (US & Canada)"


def test_timezone_display_name_unknown_returns_none():
    assert timezone_display_name("Some/Other/Zone") is None


# ================================================================ a campaign's own follow-up waits

import copy  # noqa: E402

import pytest  # noqa: E402

import outreach  # noqa: E402
from schedule_logic import validate_stage_waits, build_updated_stage_waits_override  # noqa: E402


@pytest.mark.parametrize("waits", [{}, {"followup1": 0}, {"followup1": 2, "followup2": 90}, {"followup1": 1, "followup2": 30, "followup3": 7}])
def test_whole_days_from_zero_to_ninety_are_accepted(waits):
    assert validate_stage_waits(waits) == []


@pytest.mark.parametrize("days", [-1, 91, 1000, 2.5, 2.0, "3", "", None, True, False, [2], {"a": 1}])
def test_anything_else_is_refused_with_a_message_that_names_the_follow_up(days):
    errors = validate_stage_waits({"followup2": days}, {"followup2": "Follow-up 2"})
    assert errors == ["Follow-up 2: the wait must be a whole number of days from 0 to 90."]


def test_a_stage_with_no_label_is_named_by_its_own_name_and_every_bad_wait_gets_its_own_message():
    assert validate_stage_waits({"followup1": 2, "followup2": -3, "followup3": 99}) == ["followup2: the wait must be a whole number of days from 0 to 90.",
                                                                                         "followup3: the wait must be a whole number of days from 0 to 90."]


def test_the_apps_check_and_the_engines_check_never_disagree():
    """Held together on purpose: the app must refuse exactly what the engine would stop a campaign for, and accept exactly what it takes."""
    for days in [-1, 0, 1, 2, 89, 90, 91, 2.5, 2.0, "3", "", None, True, False, [2], {"a": 1}, 10 ** 9]:
        try:
            outreach.parse_stage_wait_overrides("c", {"followup1": days})
            engine_accepts = True
        except outreach.ConfigError:
            engine_accepts = False
        assert (validate_stage_waits({"followup1": days}) == []) is engine_accepts, days


def test_building_the_file_changes_only_the_waits_and_keeps_everything_else():
    raw = {"status": "active", "sending": {"daily_limit": 50}, "schedule": {"timezone": "UTC", "window_start": "09:00", "window_end": "17:00", "send_days": ["mon"]}}
    updated = build_updated_stage_waits_override(raw, {"followup1": 3, "followup2": 5})
    assert updated["stage_wait_days"] == {"followup1": 3, "followup2": 5}
    assert {k: v for k, v in updated.items() if k != "stage_wait_days"} == raw


def test_building_never_changes_the_dict_it_was_given():
    raw = {"stage_wait_days": {"followup1": 2}, "status": "active"}
    before = copy.deepcopy(raw)
    build_updated_stage_waits_override(raw, {"followup1": 9})
    assert raw == before


def test_entries_already_in_the_file_for_other_stages_are_kept_and_the_given_ones_win():
    updated = build_updated_stage_waits_override({"stage_wait_days": {"followup1": 2, "followup9": 4}}, {"followup1": 7, "followup2": 3})
    assert updated["stage_wait_days"] == {"followup1": 7, "followup9": 4, "followup2": 3}


@pytest.mark.parametrize("junk", ["two", 5, ["a"], None])
def test_a_hand_edited_stage_wait_days_that_is_not_a_list_of_entries_is_replaced_not_crashed_on(junk):
    assert build_updated_stage_waits_override({"stage_wait_days": junk}, {"followup1": 4})["stage_wait_days"] == {"followup1": 4}


def test_a_file_with_nothing_in_it_gets_just_the_waits():
    assert build_updated_stage_waits_override({}, {"followup1": 4}) == {"stage_wait_days": {"followup1": 4}}


def test_the_written_values_are_plain_whole_numbers_the_engine_can_read_back():
    import yaml
    from settings_logic import override_to_yaml_bytes
    written = build_updated_stage_waits_override({"status": "active"}, {"followup1": 4, "followup2": 0})
    reloaded = yaml.safe_load(override_to_yaml_bytes(written).decode("utf-8"))
    assert reloaded["stage_wait_days"] == {"followup1": 4, "followup2": 0} and outreach.parse_stage_wait_overrides("c", reloaded["stage_wait_days"]) == {"followup1": 4, "followup2": 0}


# ================================================================ the file the app writes is the file the engine reads

def test_the_waits_the_app_saves_are_exactly_the_waits_the_engine_then_uses(tmp_path):
    """End to end: build the settings file the way the Schedule tab does, write it where the engine looks, and load the campaign with the engine itself."""
    from settings_logic import override_to_yaml_bytes
    (tmp_path / "campaigns").mkdir()
    (tmp_path / "templates" / "camp").mkdir(parents=True)
    for stage in ("intro", "followup1", "followup2", "followup3"):
        (tmp_path / "templates" / "camp" / f"{stage}_A.txt").write_text("Subject: Hi\n\nBody")
    (tmp_path / "settings.yaml").write_text('shared_sheet_id: "s"\nemail_accounts:\n  default_account: "sales1"\ndefault_campaign_settings:\n  stage_wait_days: {intro: 0, followup1: 2, followup2: 2, followup3: 2}\n'
                                            '  sending: {timezone: "Asia/Kolkata", window_start: "09:00", window_end: "17:00", delay_min_minutes: 1, delay_max_minutes: 2, daily_limit: 10}\n')
    existing = {"status": "active", "sending": {"daily_limit": 40}}
    written = build_updated_stage_waits_override(existing, {"followup1": 5, "followup2": 0, "followup3": 14})
    (tmp_path / "campaigns" / "camp.yaml").write_bytes(override_to_yaml_bytes(written))
    cfg = outreach.get_campaign("camp", settings_path=str(tmp_path / "settings.yaml"), campaigns_dir=str(tmp_path / "campaigns"), templates_root=str(tmp_path / "templates"))
    assert {s["template_prefix"]: s["wait_days_after_previous"] for s in cfg["stages"]} == {"intro": 0, "followup1": 5, "followup2": 0, "followup3": 14}
    assert cfg["sending"]["daily_limit"] == 40 and cfg["status"] == "active"                  # the other settings in the same file still apply


def test_a_schedule_and_waits_saved_one_after_the_other_both_reach_the_engine(tmp_path):
    from settings_logic import override_to_yaml_bytes
    (tmp_path / "campaigns").mkdir()
    (tmp_path / "templates" / "camp").mkdir(parents=True)
    for stage in ("intro", "followup1"):
        (tmp_path / "templates" / "camp" / f"{stage}_A.txt").write_text("Subject: Hi\n\nBody")
    (tmp_path / "settings.yaml").write_text('shared_sheet_id: "s"\nemail_accounts:\n  default_account: "sales1"\ndefault_campaign_settings:\n  stage_wait_days: {intro: 0, followup1: 2}\n'
                                            '  sending: {timezone: "Asia/Kolkata", window_start: "09:00", window_end: "17:00", delay_min_minutes: 1, delay_max_minutes: 2, daily_limit: 10}\n')
    first = build_updated_schedule_override({}, "America/Chicago", "08:00", "12:00", ["mon", "wed"])
    second = build_updated_stage_waits_override(first, {"followup1": 7})                      # saved after the schedule, from the file the schedule left behind
    (tmp_path / "campaigns" / "camp.yaml").write_bytes(override_to_yaml_bytes(second))
    cfg = outreach.get_campaign("camp", settings_path=str(tmp_path / "settings.yaml"), campaigns_dir=str(tmp_path / "campaigns"), templates_root=str(tmp_path / "templates"))
    assert cfg["stages"][1]["wait_days_after_previous"] == 7 and cfg["schedule"] == {"timezone": "America/Chicago", "window_start": "08:00", "window_end": "12:00", "send_days": ["mon", "wed"]}
