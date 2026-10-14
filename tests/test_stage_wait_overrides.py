"""A campaign's OWN follow-up waits ('stage_wait_days' in its override file) against the shared table in settings.yaml.

The first group proves nothing changes for a campaign that does not use the feature; the rest cover the override itself and every way a value can be wrong.
"""
import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import outreach  # noqa: E402

SHARED_TABLE = {"intro": 0, "followup1": 3, "followup2": 4, "followup3": 5}


def _campaign(tmp_path, override_yaml=None, stages=("intro", "followup1", "followup2", "followup3"), name="camp"):
    """A settings.yaml (shared waits 0/3/4/5), a templates folder with the given stages (variant A), and an optional campaign override file."""
    settings = tmp_path / "settings.yaml"
    settings.write_text("""
shared_sheet_id: "sheet123"
email_accounts:
  default_account: "sales1"
default_campaign_settings:
  stage_wait_days:
    intro: 0
    followup1: 3
    followup2: 4
    followup3: 5
  sending:
    timezone: "Asia/Kolkata"
    window_start: "09:00"
    window_end: "17:00"
    delay_min_minutes: 1
    delay_max_minutes: 2
    daily_limit: 10
""")
    campaigns, templates = tmp_path / "campaigns", tmp_path / "templates"
    campaigns.mkdir(exist_ok=True)
    (templates / name).mkdir(parents=True, exist_ok=True)
    for stage in stages:
        (templates / name / f"{stage}_A.txt").write_text("Subject: Hi\n\nBody")
    if override_yaml is not None:
        (campaigns / f"{name}.yaml").write_text(override_yaml)
    return dict(settings_path=str(settings), campaigns_dir=str(campaigns), templates_root=str(templates))


def _waits(cfg):
    return {s["template_prefix"]: s["wait_days_after_previous"] for s in cfg["stages"]}


def _load(tmp_path, override_yaml=None, **kw):
    return outreach.get_campaign("camp", **_campaign(tmp_path, override_yaml, **kw))


def _load_in(tmp_path, folder, override_yaml=None, **kw):
    """Loads a campaign in its OWN folder, so two loads in one test can never see each other's override file."""
    (tmp_path / folder).mkdir()
    return _load(tmp_path / folder, override_yaml, **kw)


# ================================================================ nothing changes for a campaign that does not use it

def test_a_campaign_without_override_waits_gets_the_shared_table(tmp_path):
    assert _waits(_load(tmp_path)) == SHARED_TABLE


@pytest.mark.parametrize("override", ["sending:\n  daily_limit: 25\n", "status: paused\n", "stage_wait_days:\n", "stage_wait_days: {}\n", "schedule:\n  timezone: UTC\n  window_start: '08:00'\n  window_end: '12:00'\n  send_days: [mon]\n"])
def test_an_override_file_that_says_nothing_about_waits_leaves_them_alone(tmp_path, override):
    assert _waits(_load(tmp_path, override)) == SHARED_TABLE


def test_the_other_settings_in_the_override_file_are_still_applied_alongside_waits(tmp_path):
    cfg = _load(tmp_path, "sending:\n  daily_limit: 25\nstage_wait_days:\n  followup1: 6\n")
    assert cfg["sending"]["daily_limit"] == 25 and cfg["sending"]["window_start"] == "09:00" and _waits(cfg)["followup1"] == 6


# ================================================================ the override itself

def test_one_stage_can_be_changed_and_the_others_keep_the_shared_wait(tmp_path):
    assert _waits(_load(tmp_path, "stage_wait_days:\n  followup2: 7\n")) == {"intro": 0, "followup1": 3, "followup2": 7, "followup3": 5}


def test_every_stage_can_be_set_and_each_follow_up_is_independent(tmp_path):
    assert _waits(_load(tmp_path, "stage_wait_days:\n  followup1: 1\n  followup2: 10\n  followup3: 2\n")) == {"intro": 0, "followup1": 1, "followup2": 10, "followup3": 2}


@pytest.mark.parametrize("days", [0, 1, 2, 30, 89, 90])
def test_any_whole_number_of_days_from_zero_to_ninety_is_accepted(tmp_path, days):
    assert _waits(_load(tmp_path, f"stage_wait_days:\n  followup1: {days}\n"))["followup1"] == days


def test_a_wait_for_a_stage_the_campaign_does_not_have_is_accepted_and_unused(tmp_path):
    """Deleting the last follow-up leaves its entry behind; that must never stop the campaign sending."""
    cfg = _load(tmp_path, "stage_wait_days:\n  followup9: 5\n  followup10: 4\n", stages=("intro", "followup1"))
    assert _waits(cfg) == {"intro": 0, "followup1": 3} and cfg["stage_wait_days"] == {"followup9": 5, "followup10": 4}


def test_a_wait_set_on_the_intro_is_accepted_and_changes_nothing_that_matters(tmp_path):
    cfg = _load(tmp_path, "stage_wait_days:\n  intro: 5\n")
    lead = {"LeadID": "1", "Email": "a@x.com", "Status": "", "ReplyStatus": ""}
    assert [l["LeadID"] for l in outreach.get_eligible_leads([lead], cfg["stages"], 0)] == ["1"]            # the first email never waits


def test_the_stage_list_itself_is_unchanged_only_the_waits(tmp_path):
    plain, custom = _load_in(tmp_path, "plain"), _load_in(tmp_path, "custom", "stage_wait_days:\n  followup2: 7\n")
    strip = lambda cfg: [(s["name"], s["template_prefix"]) for s in cfg["stages"]]          # noqa: E731
    assert strip(plain) == strip(custom) and plain["variants"] == custom["variants"]


# ---------------------------------------------------------------- a campaign that spells its stages out itself

EXPLICIT = """
stages:
  - {name: intro, template_prefix: intro, wait_days_after_previous: 0}
  - {name: followup1, template_prefix: followup1, wait_days_after_previous: 9}
  - {name: followup2, template_prefix: followup2, wait_days_after_previous: 8}
variants: [A]
"""


def test_a_campaign_with_explicit_stages_keeps_its_own_waits_when_there_is_no_override(tmp_path):
    assert _waits(_load(tmp_path, EXPLICIT, stages=("intro", "followup1", "followup2"))) == {"intro": 0, "followup1": 9, "followup2": 8}


def test_the_override_waits_apply_to_explicit_stages_too_so_the_app_never_saves_something_the_engine_ignores(tmp_path):
    cfg = _load(tmp_path, EXPLICIT + "stage_wait_days:\n  followup2: 2\n", stages=("intro", "followup1", "followup2"))
    assert _waits(cfg) == {"intro": 0, "followup1": 9, "followup2": 2}


# ================================================================ the helpers on their own

def test_apply_returns_a_new_list_and_never_changes_the_stages_it_was_given():
    stages = [{"name": "intro", "template_prefix": "intro", "wait_days_after_previous": 0}, {"name": "followup1", "template_prefix": "followup1", "wait_days_after_previous": 2}]
    before = [dict(s) for s in stages]
    updated = outreach.apply_stage_wait_overrides(stages, {"followup1": 6})
    assert stages == before and updated[1]["wait_days_after_previous"] == 6 and updated[0] is stages[0] and updated is not stages


def test_apply_with_no_waits_returns_the_stages_themselves():
    stages = [{"name": "intro"}]
    assert outreach.apply_stage_wait_overrides(stages, {}) is stages and outreach.apply_stage_wait_overrides(stages, None) is stages


def test_apply_leaves_anything_that_is_not_a_stage_dict_alone_so_existing_validation_still_reports_it():
    assert outreach.apply_stage_wait_overrides(["oops", {"template_prefix": "f1", "wait_days_after_previous": 1}], {"f1": 4}) == ["oops", {"template_prefix": "f1", "wait_days_after_previous": 4}]


def test_apply_matches_on_the_template_prefix_and_falls_back_to_the_name():
    out = outreach.apply_stage_wait_overrides([{"name": "a", "template_prefix": "p"}, {"name": "b"}], {"p": 1, "b": 2})
    assert [s["wait_days_after_previous"] for s in out] == [1, 2]


@pytest.mark.parametrize("raw, expected", [(None, {}), ({}, {}), ({"followup1": 2}, {"followup1": 2}), ({"a": 0, "b": 90}, {"a": 0, "b": 90})])
def test_parse_accepts_nothing_or_whole_days(raw, expected):
    assert outreach.parse_stage_wait_overrides("c", raw) == expected


# ================================================================ every way a value can be wrong stops the campaign, naming the stage

@pytest.mark.parametrize("value", ["-1", "91", "2.5", "'3'", "true", "false", "null", "[2]", "{a: 1}", "1000000", "-0.5", "'two'"])
def test_a_wait_that_is_not_a_whole_number_of_days_in_range_stops_the_campaign_and_names_the_stage(tmp_path, value):
    with pytest.raises(outreach.ConfigError) as error:
        _load(tmp_path, f"stage_wait_days:\n  followup2: {value}\n")
    message = str(error.value)
    assert "camp" in message and "followup2" in message and "0 to 90" in message


@pytest.mark.parametrize("raw", ["5", "[1, 2]", "'3 days'", "true"])
def test_stage_wait_days_that_is_not_a_list_of_entries_stops_the_campaign(tmp_path, raw):
    with pytest.raises(outreach.ConfigError) as error:
        _load(tmp_path, f"stage_wait_days: {raw}\n")
    assert "camp" in str(error.value) and "stage_wait_days" in str(error.value)


def test_an_entry_whose_stage_name_is_not_text_stops_the_campaign(tmp_path):
    with pytest.raises(outreach.ConfigError) as error:
        _load(tmp_path, "stage_wait_days:\n  1: 2\n")
    assert "not text" in str(error.value)


def test_one_bad_value_among_good_ones_still_stops_the_campaign_rather_than_half_applying(tmp_path):
    with pytest.raises(outreach.ConfigError):
        _load(tmp_path, "stage_wait_days:\n  followup1: 2\n  followup2: 500\n")


def test_the_limit_is_ninety_days():
    assert outreach.MAX_STAGE_WAIT_DAYS == 90


# ================================================================ what it means for who is due, and when

def _lead(i, sent_days_ago):
    return {"LeadID": str(i), "Email": f"p{i}@x.com", "Status": "", "ReplyStatus": "",
            "IntroSentAt": (datetime.now() - timedelta(days=sent_days_ago)).strftime(outreach.DATETIME_FMT)}


@pytest.mark.parametrize("days_ago, wait, due", [(1, 1, True), (1, 2, False), (2, 2, True), (3, 3, True), (3, 4, False), (0, 0, True), (0, 1, False), (30, 90, False), (90, 90, True)])
def test_a_lead_is_due_for_a_follow_up_exactly_when_the_campaigns_own_wait_has_passed(tmp_path, days_ago, wait, due):
    cfg = _load(tmp_path, f"stage_wait_days:\n  followup1: {wait}\n")
    assert bool(outreach.get_eligible_leads([_lead(1, days_ago)], cfg["stages"], 1)) is due


def test_two_campaigns_with_different_waits_judge_the_same_lead_differently(tmp_path):
    lead = _lead(1, 2)
    short = _load_in(tmp_path, "short", "stage_wait_days:\n  followup1: 1\n")
    long = _load_in(tmp_path, "long", "stage_wait_days:\n  followup1: 5\n")
    assert outreach.get_eligible_leads([lead], short["stages"], 1) and not outreach.get_eligible_leads([lead], long["stages"], 1)


def test_the_next_eligible_date_recorded_at_send_time_follows_the_campaigns_own_wait(tmp_path):
    custom = _load_in(tmp_path, "custom", "stage_wait_days:\n  followup1: 6\n")
    plain = _load_in(tmp_path, "plain")
    sent = datetime(2026, 3, 2, 10, 0)
    assert outreach._compute_next_eligible_at(custom["stages"], 0, sent) == "2026-03-08"
    assert outreach._compute_next_eligible_at(plain["stages"], 0, sent) == "2026-03-05"              # the shared table's 3 days
