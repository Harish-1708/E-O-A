"""The 'Time between sends' card in a campaign's Settings tab, through the real page: what it shows, the presets, the live explanation, the warnings, saving and resetting,
and that saving it never disturbs the rest of the campaign's settings."""
import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from github_client import GitHubActionsError  # noqa: E402
from settings_logic import override_file_path  # noqa: E402
from test_pages_smoke import FIXTURE_CAMPAIGN  # noqa: E402
from test_settings_page import _clear_streamlit_caches, _every_test_uses_the_fixture_repo, _stub_the_live_template_listing, markup, settings_page  # noqa: E402

_AUTOUSE_FIXTURES = (_every_test_uses_the_fixture_repo, _clear_streamlit_caches, _stub_the_live_template_listing)
PATH = override_file_path(FIXTURE_CAMPAIGN)
ROOT = os.path.join(os.path.dirname(__file__), "..", "..")


def sending(low=None, high=None, **extra):
    base = {"timezone": "America/New_York", "window_start": "09:00", "window_end": "17:00", "daily_limit": 100}
    if low is not None:
        base["delay_min_minutes"], base["delay_max_minutes"] = low, high
    else:
        base["delay_min_minutes"], base["delay_max_minutes"] = 3, 7
    base.update(extra)
    return base


def box(at, which):
    return next(n for n in at.number_input if n.key == f"se_pace_{which}_{FIXTURE_CAMPAIGN}")


def preset(at):
    return next(r for r in at.radio if r.key == f"se_pace_preset_{FIXTURE_CAMPAIGN}")


def button(at, which):
    return next(b for b in at.button if b.key == f"se_pace_{which}_{FIXTURE_CAMPAIGN}")


def settle(at):
    at.run(timeout=30)


# ================================================================ what it shows

def test_the_card_is_in_settings_for_every_campaign_with_the_two_boxes_and_the_four_speeds():
    for cfg in (None, {"format": "html"}):
        with settings_page(cfg=cfg) as (at, _):
            assert list(at.exception) == [] and "Time between sends" in markup(at)
            assert preset(at).options == ["Careful · 5 to 10 minutes", "Standard · 3 to 7 minutes", "Faster · 1 to 3 minutes", "Custom"]
            assert box(at, "low").min == 0.5 and box(at, "high").max == 120.0 and box(at, "low").step == 0.5


def test_a_campaign_on_the_shared_default_says_so_and_has_nothing_to_reset():
    with settings_page(sending=sending(3, 7)) as (at, _):
        assert (box(at, "low").value, box(at, "high").value) == (3.0, 7.0) and preset(at).value == "standard"
        assert "Default for all campaigns (3 to 7 minutes)" in markup(at) and button(at, "save").disabled is True
        assert button(at, "reset").disabled is True


def test_a_campaign_with_its_own_wait_shows_it_and_says_it_is_custom():
    with settings_page(sending=sending(8, 15)) as (at, _):
        assert (box(at, "low").value, box(at, "high").value) == (8.0, 15.0) and preset(at).value == "custom"
        assert "Custom for this campaign" in markup(at) and button(at, "reset").disabled is False


@pytest.mark.parametrize("low, high, key", [(5, 10, "careful"), (3, 7, "standard"), (1, 3, "faster"), (2, 9, "custom")])
def test_the_speed_choice_always_matches_the_two_numbers(low, high, key):
    with settings_page(sending=sending(low, high)) as (at, _):
        assert preset(at).value == key


# ================================================================ the live explanation

def test_the_card_explains_in_plain_words_what_the_numbers_mean_for_this_campaigns_limits():
    with settings_page(sending=sending(3, 7, daily_limit=100)) as (at, _):
        text = markup(at)
        assert "Each mailbox sends about one email every 5 minutes (around 12 an hour)." in text and "Sending the whole daily limit of 100 across" in text


def test_a_setting_that_could_run_past_githubs_limit_is_warned_about_before_anything_is_saved():
    with settings_page(sending=sending(3, 7, daily_limit=100)) as (at, _):
        box(at, "high").set_value(60.0)
        box(at, "low").set_value(50.0)
        settle(at)
        text = markup(at)
        assert "can be cut off by GitHub" in text and "repeat an email" in text


def test_very_short_gaps_are_warned_about():
    with settings_page(sending=sending(3, 7)) as (at, _):
        box(at, "low").set_value(0.5)
        box(at, "high").set_value(1.0)
        settle(at)
        assert "look like bulk sending" in markup(at)


def test_a_start_after_the_end_is_explained_and_cannot_be_saved():
    with settings_page(sending=sending(3, 7)) as (at, _):
        box(at, "low").set_value(9.0)
        settle(at)
        assert "cannot be longer than the longest wait" in markup(at) and button(at, "save").disabled is True


# ================================================================ choosing a speed and typing

def test_choosing_a_speed_puts_its_numbers_in_the_boxes_and_marks_the_change_unsaved():
    with settings_page(sending=sending(3, 7)) as (at, _):
        preset(at).set_value("careful")
        settle(at)
        assert (box(at, "low").value, box(at, "high").value) == (5.0, 10.0) and preset(at).value == "careful"
        assert button(at, "save").disabled is False and "Unsaved change" in markup(at)


def test_typing_numbers_that_are_not_a_preset_switches_the_speed_to_custom():
    with settings_page(sending=sending(3, 7)) as (at, _):
        box(at, "high").set_value(9.0)
        settle(at)
        assert preset(at).value == "custom" and (box(at, "low").value, box(at, "high").value) == (3.0, 9.0)


def test_choosing_custom_leaves_the_numbers_as_they_are():
    with settings_page(sending=sending(3, 7)) as (at, _):
        preset(at).set_value("custom")
        settle(at)
        assert (box(at, "low").value, box(at, "high").value) == (3.0, 7.0)


# ================================================================ saving

def test_saving_writes_the_two_numbers_to_the_campaigns_settings_file_and_keeps_every_other_setting():
    live = {PATH: yaml.safe_dump({"status": "draft", "format": "html", "sending": {"daily_limit": 40, "sender_rotation": True}, "stage_wait_days": {"followup1": 2}}).encode()}
    with settings_page(sending=sending(3, 7), live=live) as (at, captured):
        box(at, "low").set_value(5.0)
        box(at, "high").set_value(12.5)
        settle(at)
        button(at, "save").click()
        settle(at)
        assert list(at.exception) == [] and list(at.error) == [] and len(captured["commits"]) == 1
        commit = captured["commits"][0]
        assert commit["path"] == PATH and commit["message"].startswith(f"Update time between sends for {FIXTURE_CAMPAIGN} (via Streamlit, by ")
        assert yaml.safe_load(commit["content"]) == {"status": "draft", "format": "html", "sending": {"daily_limit": 40, "sender_rotation": True, "delay_min_minutes": 5, "delay_max_minutes": 12.5},
                                                     "stage_wait_days": {"followup1": 2}}
        assert any("each mailbox now waits between 5 minutes and 12.5 minutes" in s.value for s in at.success)


def test_after_saving_the_card_shows_it_as_saved_custom_and_resettable_without_waiting_for_a_redeploy():
    with settings_page(sending=sending(3, 7), live={PATH: b"status: draft\n"}) as (at, _):
        preset(at).set_value("faster")
        settle(at)
        button(at, "save").click()
        settle(at)
        assert (box(at, "low").value, box(at, "high").value) == (1.0, 3.0) and button(at, "save").disabled is True and "Unsaved change" not in markup(at)
        assert "Custom for this campaign" in markup(at) and button(at, "reset").disabled is False


def test_a_fixed_wait_the_same_every_time_can_be_saved_and_gets_a_gentle_suggestion():
    with settings_page(sending=sending(3, 7), live={PATH: b"status: draft\n"}) as (at, captured):
        box(at, "low").set_value(5.0)
        box(at, "high").set_value(5.0)
        settle(at)
        assert "exactly the same every time" in markup(at)
        button(at, "save").click()
        settle(at)
        assert yaml.safe_load(captured["commits"][0]["content"])["sending"] == {"delay_min_minutes": 5, "delay_max_minutes": 5}


def test_saving_on_a_campaign_with_no_settings_file_yet_creates_one_with_only_the_wait():
    with settings_page(sending=sending(3, 7), live={}) as (at, captured):
        preset(at).set_value("careful")
        settle(at)
        button(at, "save").click()
        settle(at)
        assert yaml.safe_load(captured["commits"][0]["content"]) == {"sending": {"delay_min_minutes": 5, "delay_max_minutes": 10}}


def test_a_failed_save_says_so_and_keeps_the_numbers_and_the_change_unsaved():
    with settings_page(sending=sending(3, 7), live={PATH: b"status: draft\n"}, commit_error=GitHubActionsError("boom")) as (at, _):
        preset(at).set_value("careful")
        settle(at)
        button(at, "save").click()
        settle(at)
        assert any("Save failed: boom" in e.value for e in at.error) and not any("each mailbox now waits" in s.value for s in at.success)
        assert (box(at, "low").value, box(at, "high").value) == (5.0, 10.0) and button(at, "save").disabled is False


# ================================================================ using the default again

def test_use_the_default_removes_the_campaigns_own_wait_and_keeps_everything_else():
    live = {PATH: yaml.safe_dump({"status": "draft", "sending": {"daily_limit": 40, "delay_min_minutes": 8, "delay_max_minutes": 15}}).encode()}
    with settings_page(sending=sending(8, 15), live=live) as (at, captured):
        button(at, "reset").click()
        settle(at)
        assert list(at.exception) == [] and len(captured["commits"]) == 1
        assert yaml.safe_load(captured["commits"][0]["content"]) == {"status": "draft", "sending": {"daily_limit": 40}}
        assert (box(at, "low").value, box(at, "high").value) == (3.0, 7.0) and preset(at).value == "standard" and "Default for all campaigns" in markup(at)
        assert button(at, "reset").disabled is True


def test_use_the_default_on_a_file_that_holds_only_the_wait_leaves_an_empty_file_not_a_broken_one():
    with settings_page(sending=sending(8, 15), live={PATH: yaml.safe_dump({"sending": {"delay_min_minutes": 8, "delay_max_minutes": 15}}).encode()}) as (at, captured):
        button(at, "reset").click()
        settle(at)
        assert yaml.safe_load(captured["commits"][0]["content"]) in ({}, None)


# ================================================================ the rest of Settings is undisturbed

def test_the_sending_limits_save_still_keeps_the_campaigns_own_wait():
    live = {PATH: yaml.safe_dump({"sending": {"daily_limit": 40, "delay_min_minutes": 8, "delay_max_minutes": 15}}).encode()}
    with settings_page(sending=sending(8, 15), live=live) as (at, captured):
        next(n for n in at.number_input if (n.key or "").startswith("se_limit_daily_input_")).set_value(55)
        settle(at)
        next(b for b in at.button if b.key == "se_save_button").click()
        settle(at)
        written = yaml.safe_load(captured["commits"][0]["content"])
        assert written["sending"]["daily_limit"] == 55 and written["sending"]["delay_min_minutes"] == 8 and written["sending"]["delay_max_minutes"] == 15


def test_the_explanation_follows_the_daily_limit_typed_in_the_card_above():
    with settings_page(sending=sending(3, 7, daily_limit=100)) as (at, _):
        next(n for n in at.number_input if (n.key or "").startswith("se_limit_daily_input_")).set_value(20)
        settle(at)
        assert "whole daily limit of 20 across" in markup(at)


# ================================================================ values the engine accepts but the boxes cannot show

@pytest.mark.parametrize("low, high, shown_low, shown_high", [(0, 0, 0.5, 0.5), (0, 10, 0.5, 10.0), (3, 200, 3.0, 120.0), (300, 500, 120.0, 120.0)])
def test_a_wait_the_engine_accepts_but_the_boxes_cannot_show_never_breaks_the_settings_tab(low, high, shown_low, shown_high):
    with settings_page(sending=sending(low, high)) as (at, _):
        assert list(at.exception) == [] and (box(at, "low").value, box(at, "high").value) == (shown_low, shown_high)
        assert "Time between sends" in markup(at) and button(at, "save").disabled is True            # untouched, so nothing to save


def test_use_the_default_works_even_when_the_shared_default_is_one_the_boxes_cannot_show():
    from unittest.mock import patch
    live = {PATH: yaml.safe_dump({"sending": {"daily_limit": 40, "delay_min_minutes": 8, "delay_max_minutes": 15}}).encode()}
    with patch("settings_logic.global_default_pacing", lambda settings: (0.0, 0.0)):
        with settings_page(sending=sending(8, 15), live=live) as (at, captured):
            assert list(at.exception) == []
            button(at, "reset").click()
            settle(at)
            assert list(at.exception) == [] and list(at.error) == [] and len(captured["commits"]) == 1
            assert yaml.safe_load(captured["commits"][0]["content"]) == {"sending": {"daily_limit": 40}}
            assert (box(at, "low").value, box(at, "high").value) == (0.5, 0.5)


def test_a_campaign_that_is_on_an_unusual_shared_default_is_still_shown_as_on_the_default():
    from unittest.mock import patch
    with patch("settings_logic.global_default_pacing", lambda settings: (0.0, 0.0)):
        with settings_page(sending=sending(0, 0)) as (at, _):
            assert list(at.exception) == [] and "Default for all campaigns" in markup(at) and "Custom for this campaign" not in markup(at) and button(at, "reset").disabled is True
