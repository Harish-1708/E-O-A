import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from settings_logic import (
    load_raw_override_live, merge_live_override_into_cfg,
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


# ---------- live overlay on the DISPLAY path ----------

def test_merge_live_override_shows_newly_added_schedule_days():
    """The exact reported symptom: extra days added and saved, committed
    fine on GitHub, but the Schedule tab kept showing the old days —
    because the tab DISPLAYS from campaign_cfg, built from the local
    checkout, which is frozen until redeploy."""
    base = {"_campaign_name": "X", "schedule": {"timezone": "UTC", "send_days": ["mon"]}}
    merged = merge_live_override_into_cfg(base, {"schedule": {"send_days": ["mon", "tue", "wed"]}})
    assert merged["schedule"]["send_days"] == ["mon", "tue", "wed"]
    assert merged["schedule"]["timezone"] == "UTC"  # untouched default preserved


def test_merge_live_override_shows_newly_added_sender_accounts():
    base = {"_campaign_name": "X",
            "sending": {"daily_limit": 100, "sender_rotation": False, "rotation_accounts": ["a"]}}
    merged = merge_live_override_into_cfg(base, {"sending": {"rotation_accounts": ["a", "b", "c"]}})
    assert merged["sending"]["rotation_accounts"] == ["a", "b", "c"]
    # Keys the override didn't mention must survive — a partial override
    # must never silently drop the rest of the merged config.
    assert merged["sending"]["daily_limit"] == 100
    assert merged["sending"]["sender_rotation"] is False


def test_merge_live_override_never_mutates_the_cached_input():
    """campaign_cfg comes from an st.cache_data-backed call, so the same
    dict object can be shared across reruns — mutating it would corrupt
    every later render."""
    base = {"_campaign_name": "X", "sending": {"rotation_accounts": ["a"]}}
    merge_live_override_into_cfg(base, {"sending": {"rotation_accounts": ["a", "b"]}})
    assert base["sending"]["rotation_accounts"] == ["a"]


def test_merge_live_override_empty_is_a_noop():
    base = {"_campaign_name": "X", "sending": {"daily_limit": 5}}
    assert merge_live_override_into_cfg(base, {}) is base


def test_merge_live_override_carries_status_through():
    base = {"_campaign_name": "X", "status": "active"}
    assert merge_live_override_into_cfg(base, {"status": "paused"})["status"] == "paused"


def test_merge_live_override_reflects_paused_status_for_the_status_controls():
    """The actual reported bug: pausing a campaign committed correctly to
    GitHub, but the Status tab's controls read campaign_cfg with no live
    overlay applied at all — a THIRD consumer missed by the earlier fix,
    which only covered Settings and Schedule. Pause/Resume never
    reflected in the UI no matter how long you waited, because nothing
    about waiting re-reads the repository."""
    stale_cfg = {"_campaign_name": "X", "status": "active"}
    merged = merge_live_override_into_cfg(stale_cfg, {"status": "paused"})
    assert merged["status"] == "paused"


# ================================================================ a campaign's own follow-up waits, shown from the live override

import copy  # noqa: E402


def _cfg_with_stages(waits=(0, 2, 2, 2)):
    names = ["intro", "followup1", "followup2", "followup3"]
    return {"_campaign_name": "Camp", "sending": {"daily_limit": 10},
            "stages": [{"name": n, "template_prefix": n, "wait_days_after_previous": w} for n, w in zip(names, waits)]}


def _waits_of(cfg):
    return [s["wait_days_after_previous"] for s in cfg["stages"]]


def test_a_live_override_with_its_own_waits_changes_those_stages_only():
    merged = merge_live_override_into_cfg(_cfg_with_stages(), {"stage_wait_days": {"followup2": 6}})
    assert _waits_of(merged) == [0, 2, 6, 2] and merged["stage_wait_days"] == {"followup2": 6}


def test_every_follow_up_can_have_its_own_wait():
    assert _waits_of(merge_live_override_into_cfg(_cfg_with_stages(), {"stage_wait_days": {"followup1": 1, "followup2": 10, "followup3": 0}})) == [0, 1, 10, 0]


def test_a_live_override_without_waits_leaves_the_stages_exactly_as_they_were():
    cfg = _cfg_with_stages()
    assert merge_live_override_into_cfg(cfg, {"status": "paused"})["stages"] == cfg["stages"] and "stage_wait_days" not in merge_live_override_into_cfg(cfg, {"status": "paused"})


def test_merging_waits_never_changes_the_config_it_was_given():
    cfg = _cfg_with_stages()
    before = copy.deepcopy(cfg)
    merge_live_override_into_cfg(cfg, {"stage_wait_days": {"followup1": 9}, "sending": {"daily_limit": 5}})
    assert cfg == before


@pytest.mark.parametrize("bad", [{"followup1": -1}, {"followup1": 2.5}, {"followup1": "3"}, {"followup1": 500}, {"followup1": True}, {"followup1": 2, "followup2": None}])
def test_a_wait_the_engine_would_refuse_is_not_shown_and_nothing_is_half_applied(bad):
    merged = merge_live_override_into_cfg(_cfg_with_stages(), {"stage_wait_days": bad})
    assert _waits_of(merged) == [0, 2, 2, 2] and merged["stage_wait_days"] == {}


@pytest.mark.parametrize("junk", ["two", 5, ["a", "b"]])
def test_stage_wait_days_that_is_not_a_list_of_entries_is_ignored_by_the_display(junk):
    cfg = _cfg_with_stages()
    merged = merge_live_override_into_cfg(cfg, {"stage_wait_days": junk})
    assert _waits_of(merged) == [0, 2, 2, 2] and "stage_wait_days" not in merged and merged == cfg                  # the config is left exactly as it was


def test_a_wait_for_a_stage_the_campaign_does_not_have_is_harmless():
    assert _waits_of(merge_live_override_into_cfg(_cfg_with_stages(), {"stage_wait_days": {"followup9": 5}})) == [0, 2, 2, 2]


def test_waits_and_the_other_live_settings_merge_together():
    merged = merge_live_override_into_cfg(_cfg_with_stages(), {"stage_wait_days": {"followup1": 4}, "sending": {"daily_limit": 77}, "status": "paused"})
    assert _waits_of(merged)[1] == 4 and merged["sending"]["daily_limit"] == 77 and merged["status"] == "paused"


def test_a_config_with_no_stages_yet_does_not_break_the_merge():
    assert merge_live_override_into_cfg({"_campaign_name": "C"}, {"stage_wait_days": {"followup1": 3}})["stages"] == []


# ================================================================ the email footer (HTML campaigns)

def test_the_footer_settings_are_added_trimmed_and_everything_else_is_kept():
    import settings_logic as SL
    raw = {"status": "draft", "sending": {"daily_limit": 50}, "format": "html"}
    out = SL.build_footer_settings_override(raw, "  Kelson Agency ", " 12 Example Street ", ' Reply "unsubscribe". ')
    assert out == {"status": "draft", "sending": {"daily_limit": 50}, "format": "html", "footer": {"business_name": "Kelson Agency", "address": "12 Example Street", "opt_out": 'Reply "unsubscribe".'}}
    assert raw == {"status": "draft", "sending": {"daily_limit": 50}, "format": "html"}                 # the input is never changed


@pytest.mark.parametrize("opt_out", ["", "   ", None])
def test_a_blank_opt_out_wording_is_left_out_so_the_default_is_used(opt_out):
    import settings_logic as SL
    assert SL.build_footer_settings_override({}, "K", "A", opt_out) == {"footer": {"business_name": "K", "address": "A"}}


def test_the_footer_can_be_set_on_a_campaign_that_has_no_settings_file_yet_and_replaces_an_old_footer():
    import settings_logic as SL
    assert SL.build_footer_settings_override(None, "K", "A", "")["footer"] == {"business_name": "K", "address": "A"}
    assert SL.build_footer_settings_override({"footer": {"business_name": "Old", "address": "Old", "opt_out": "Old"}}, "New", "Addr", "")["footer"] == {"business_name": "New", "address": "Addr"}


# ================================================================ link tagging (HTML campaigns)

def test_link_tagging_is_switched_on_and_off_and_everything_else_is_kept():
    import settings_logic as SL
    raw = {"format": "html", "status": "draft", "footer": {"business_name": "K", "address": "A"}}
    on = SL.build_tracking_settings_override(raw, True)
    assert on == {"format": "html", "status": "draft", "footer": {"business_name": "K", "address": "A"}, "tracking": {"utm": True}}
    assert SL.build_tracking_settings_override(on, False)["tracking"] == {"utm": False} and raw == {"format": "html", "status": "draft", "footer": {"business_name": "K", "address": "A"}}


def test_a_custom_source_and_medium_survive_switching_link_tagging():
    import settings_logic as SL
    out = SL.build_tracking_settings_override({"tracking": {"utm": False, "source": "kelson", "medium": "newsletter"}}, True)
    assert out["tracking"] == {"utm": True, "source": "kelson", "medium": "newsletter"}


@pytest.mark.parametrize("raw", [None, {}, {"tracking": None}, {"tracking": "yes"}, {"tracking": ["utm"]}])
def test_link_tagging_can_be_set_on_a_campaign_with_no_or_a_broken_tracking_setting(raw):
    import settings_logic as SL
    assert SL.build_tracking_settings_override(raw, True)["tracking"] == {"utm": True}


# ================================================================ time between sends

def test_the_presets_are_careful_standard_and_faster_and_standard_is_the_shared_default():
    import settings_logic as SL
    assert [(k, lo, hi) for k, _l, lo, hi in SL.PACING_PRESETS] == [("careful", 5.0, 10.0), ("standard", 3.0, 7.0), ("faster", 1.0, 3.0)]
    assert SL.PACING_FALLBACK == (3.0, 7.0)


@pytest.mark.parametrize("minutes, words", [(0.5, "30 seconds"), (0.25, "15 seconds"), (1, "1 minute"), (1.0, "1 minute"), (3, "3 minutes"), (2.5, "2.5 minutes"), (120, "120 minutes")])
def test_a_gap_is_written_in_plain_words(minutes, words):
    import settings_logic as SL
    assert SL.format_minutes(minutes) == words


@pytest.mark.parametrize("minutes, words", [(0, "0 min"), (45, "45 min"), (59.6, "1 h"), (60, "1 h"), (125, "2 h 5 min"), (330, "5 h 30 min")])
def test_a_run_length_is_written_in_hours_and_minutes(minutes, words):
    import settings_logic as SL
    assert SL.format_duration(minutes) == words


def test_the_shared_default_is_read_from_the_settings_and_falls_back_safely():
    import settings_logic as SL
    assert SL.global_default_pacing({"default_campaign_settings": {"sending": {"delay_min_minutes": 2, "delay_max_minutes": 9}}}) == (2.0, 9.0)
    for broken in (None, {}, {"default_campaign_settings": {}}, {"default_campaign_settings": {"sending": {"delay_min_minutes": "x", "delay_max_minutes": 7}}},
                   {"default_campaign_settings": {"sending": {"delay_min_minutes": 9, "delay_max_minutes": 2}}}, {"default_campaign_settings": {"sending": {"delay_min_minutes": -1, "delay_max_minutes": 2}}}, "text"):
        assert SL.global_default_pacing(broken) == SL.PACING_FALLBACK


@pytest.mark.parametrize("low, high, expected", [(5, 10, "careful"), (3, 7, "standard"), (1, 3, "faster"), (3.0, 7.0, "standard"), (3, 8, "custom"), (4, 7, "custom"), (0.5, 1, "custom"), (3.5, 7, "custom"), (3, 7.5, "custom"), (5.5, 10, "custom"), (1, 2.5, "custom"), (3.0001, 7, "custom")])
def test_two_numbers_are_a_preset_only_when_they_match_it_exactly(low, high, expected):
    import settings_logic as SL
    assert SL.preset_for(low, high) == expected


@pytest.mark.parametrize("low, high", [(0.5, 0.5), (3, 7), (5, 5), (0.5, 120), (120, 120), (1.5, 2.5)])
def test_a_sensible_wait_is_accepted(low, high):
    import settings_logic as SL
    assert SL.validate_pacing(low, high) == []


@pytest.mark.parametrize("low, high, words", [(0.4, 5, "shortest wait must be between"), (0, 5, "shortest wait must be between"), (3, 121, "longest wait must be between"), (-1, 5, "shortest wait must be between"),
                                              (8, 7, "cannot be longer"), (5, 4.5, "cannot be longer"), (None, 5, "shortest wait must be a number"), (3, None, "longest wait must be a number"),
                                              (float("nan"), 5, "shortest wait must be a number")])
def test_a_wait_that_would_not_load_or_that_the_screen_does_not_offer_is_refused_with_the_reason(low, high, words):
    import settings_logic as SL
    errors = SL.validate_pacing(low, high)
    assert errors and words in errors[0]


def test_both_numbers_wrong_gives_both_reasons_and_no_misleading_order_message():
    import settings_logic as SL
    assert len(SL.validate_pacing(0, 500)) == 2 and not any("cannot be longer" in e for e in SL.validate_pacing(0, 500))


def test_saving_the_wait_sets_the_two_keys_and_keeps_every_other_setting():
    import settings_logic as SL
    raw = {"status": "draft", "format": "html", "sending": {"daily_limit": 40, "sender_rotation": True, "rotation_accounts": ["a", "b"]}, "stage_wait_days": {"followup1": 2}}
    out = SL.build_pacing_override(raw, 5.0, 12.5)
    assert out == {"status": "draft", "format": "html", "sending": {"daily_limit": 40, "sender_rotation": True, "rotation_accounts": ["a", "b"], "delay_min_minutes": 5, "delay_max_minutes": 12.5},
                   "stage_wait_days": {"followup1": 2}}
    assert raw["sending"] == {"daily_limit": 40, "sender_rotation": True, "rotation_accounts": ["a", "b"]}            # the input is never changed


def test_whole_numbers_are_saved_without_a_decimal_point():
    import settings_logic as SL
    sending = SL.build_pacing_override({}, 3.0, 7.0)["sending"]
    assert sending == {"delay_min_minutes": 3, "delay_max_minutes": 7} and type(sending["delay_min_minutes"]) is int


@pytest.mark.parametrize("raw", [None, {}, {"sending": None}, {"sending": "text"}, {"sending": []}])
def test_the_wait_can_be_saved_on_a_campaign_with_no_or_a_broken_sending_block(raw):
    import settings_logic as SL
    assert SL.build_pacing_override(raw, 2, 4) == {"sending": {"delay_min_minutes": 2, "delay_max_minutes": 4}}


def test_resetting_removes_only_the_two_wait_keys_so_the_shared_default_applies_again():
    import settings_logic as SL
    raw = {"sending": {"daily_limit": 40, "delay_min_minutes": 5, "delay_max_minutes": 9}, "status": "draft"}
    assert SL.build_pacing_override(raw, None, None) == {"sending": {"daily_limit": 40}, "status": "draft"}
    assert SL.build_pacing_override({"sending": {"delay_min_minutes": 5, "delay_max_minutes": 9}}, None, None) == {}
    assert "delay_min_minutes" not in SL.build_pacing_override({"sending": {"delay_min_minutes": 5, "delay_max_minutes": 9}}, 3, None).get("sending", {})


def test_the_estimate_says_how_often_each_mailbox_sends_and_how_long_the_whole_limit_takes():
    import settings_logic as SL
    notes = SL.pacing_notes(100, 4, 3, 7)
    assert notes[0] == ("info", "Each mailbox sends about one email every 5 minutes (around 12 an hour).")
    assert notes[1] == ("info", "Sending the whole daily limit of 100 across 4 mailboxes takes about 2 h (at most 2 h 48 min).") and len(notes) == 2


def test_a_single_mailbox_is_worded_in_the_singular_and_one_round_has_no_run_length():
    import settings_logic as SL
    assert "across 1 mailbox takes" in SL.pacing_notes(10, 1, 3, 7)[1][1]
    assert [n for n in SL.pacing_notes(3, 5, 3, 7) if "whole daily limit" in n[1]] == []


def test_a_run_that_could_pass_githubs_limit_is_warned_about_with_the_real_danger_named():
    import settings_logic as SL
    warnings = [t for k, t in SL.pacing_notes(100, 1, 3, 7) if k == "warn"]
    assert len(warnings) == 1 and "6-hour limit" in warnings[0] and "repeat an email" in warnings[0] and "11 h 33 min at most" in warnings[0]
    assert not [1 for k, _t in SL.pacing_notes(100, 4, 3, 7) if k == "warn"]


def test_the_warning_starts_exactly_above_five_hours():
    import settings_logic as SL
    assert not [1 for k, _t in SL.pacing_notes(61, 1, 5, 5) if k == "warn"]            # 60 waits of 5 min = 300 min
    assert [1 for k, _t in SL.pacing_notes(62, 1, 5, 5) if k == "warn"]               # 61 waits of 5 min = 305 min


def test_very_short_gaps_are_warned_about_and_a_fixed_gap_gets_a_gentle_suggestion():
    import settings_logic as SL
    assert any(k == "warn" and "look like bulk sending" in t for k, t in SL.pacing_notes(20, 2, 0.5, 1.5))
    assert not any("bulk sending" in t for k, t in SL.pacing_notes(20, 2, 3, 7))
    assert any(k == "info" and "exactly the same every time" in t for k, t in SL.pacing_notes(20, 2, 5, 5))
    assert not any("exactly the same" in t for k, t in SL.pacing_notes(20, 2, 3, 7))


def test_the_estimate_never_fails_on_odd_input():
    import settings_logic as SL
    assert SL.pacing_notes(0, 0, 0, 0) == [] and isinstance(SL.pacing_notes(1, 1, 0.5, 0.5), list) and isinstance(SL.pacing_notes(-5, -2, 3, 7), list)


def test_a_wait_is_pulled_into_the_range_the_boxes_can_show():
    import settings_logic as SL
    assert [SL.clamp_pacing(x) for x in (0, 0.2, 0.5, 3, 120, 121, 500, "7")] == [0.5, 0.5, 0.5, 3.0, 120.0, 120.0, 120.0, 7.0]
