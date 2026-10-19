"""get_campaign built from a supplied list of template file names (what the app passes when its own checkout is behind GitHub), not the local templates folder."""
import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import outreach  # noqa: E402
from test_stage_wait_overrides import _campaign  # noqa: E402

ALL = ["intro_A.txt", "followup1_A.txt", "followup2_A.txt", "followup3_A.txt"]


def _kw(tmp_path, override_yaml=None, stages=("intro", "followup1", "followup2", "followup3")):
    return _campaign(tmp_path, override_yaml, stages=stages)


def test_the_listing_gives_the_same_campaign_as_the_local_folder(tmp_path):
    kw = _kw(tmp_path)
    local = outreach.get_campaign("camp", **kw)
    from_listing = outreach.get_campaign("camp", template_filenames=ALL, **kw)
    assert from_listing["stages"] == local["stages"] and from_listing["variants"] == local["variants"]
    for key in ("master_tab", "responses_tab", "send_log_tab", "error_log_tab", "sheet_id", "status", "_campaign_name", "sending"):
        assert from_listing[key] == local[key], key


def test_with_the_local_folder_gone_the_listing_still_builds_it_and_without_a_listing_it_still_fails_as_before(tmp_path):
    kw = _kw(tmp_path)
    shutil.rmtree(os.path.join(kw["templates_root"], "camp"))
    cfg = outreach.get_campaign("camp", template_filenames=ALL, **kw)
    assert [s["name"] for s in cfg["stages"]] == ["intro", "followup1", "followup2", "followup3"] and cfg["variants"] == ["A"]
    with pytest.raises(outreach.ConfigError, match="No templates found for campaign 'camp'"):
        outreach.get_campaign("camp", **kw)


def test_the_listing_wins_over_a_local_folder_that_disagrees_with_it(tmp_path):
    kw = _kw(tmp_path)                                                       # the local folder has four stages
    cfg = outreach.get_campaign("camp", template_filenames=["intro_A.txt", "intro_B.txt"], **kw)
    assert [s["name"] for s in cfg["stages"]] == ["intro"] and cfg["variants"] == ["A", "B"]


def test_an_empty_listing_says_there_are_no_template_files_and_says_where_it_looked(tmp_path):
    with pytest.raises(outreach.ConfigError, match=r"No template files found in templates/camp \(read from GitHub\)"):
        outreach.get_campaign("camp", template_filenames=[], **_kw(tmp_path))


def test_a_listing_with_other_files_in_it_only_counts_the_template_files(tmp_path):
    cfg = outreach.get_campaign("camp", template_filenames=["README.md", ".gitkeep", "intro_A.txt", "notes.txt"], **_kw(tmp_path))
    assert [s["name"] for s in cfg["stages"]] == ["intro"]


def test_the_same_rules_apply_to_a_listing_a_gap_ends_the_sequence_and_unequal_variants_are_refused(tmp_path):
    cfg = outreach.get_campaign("camp", template_filenames=["intro_A.txt", "followup2_A.txt"], **_kw(tmp_path))
    assert [s["name"] for s in cfg["stages"]] == ["intro"]                                            # followup1 is missing, so the sequence stops at the first gap
    with pytest.raises(outreach.ConfigError, match="Inconsistent variants"):
        outreach.get_campaign("camp", template_filenames=["intro_A.txt", "intro_B.txt", "followup1_A.txt"], **_kw(tmp_path))


def test_the_shared_and_the_campaigns_own_waits_still_apply(tmp_path):
    kw = _kw(tmp_path, override_yaml="stage_wait_days:\n  followup2: 9\n")
    waits = {s["name"]: s["wait_days_after_previous"] for s in outreach.get_campaign("camp", template_filenames=ALL, **kw)["stages"]}
    assert waits == {"intro": 0, "followup1": 3, "followup2": 9, "followup3": 5}


def test_the_local_override_file_is_still_read_with_a_listing(tmp_path):
    cfg = outreach.get_campaign("camp", template_filenames=ALL, **_kw(tmp_path, override_yaml="status: draft\nsending:\n  daily_limit: 77\n"))
    assert cfg["status"] == "draft" and cfg["sending"]["daily_limit"] == 77


def _explicit(tmp_path):
    return _kw(tmp_path, override_yaml='stages:\n  - {name: intro, template_prefix: intro, wait_days_after_previous: 0}\n  - {name: followup1, template_prefix: followup1, wait_days_after_previous: 3}\nvariants: ["A", "B"]\n')


def test_an_explicit_shape_is_checked_strictly_against_the_listing(tmp_path):
    kw = _explicit(tmp_path)
    complete = ["intro_A.txt", "intro_B.txt", "followup1_A.txt", "followup1_B.txt"]
    cfg = outreach.get_campaign("camp", template_filenames=complete, **kw)
    assert [s["name"] for s in cfg["stages"]] == ["intro", "followup1"] and cfg["variants"] == ["A", "B"]
    with pytest.raises(outreach.ConfigError) as caught:
        outreach.get_campaign("camp", template_filenames=["intro_A.txt", "intro_B.txt", "followup1_A.txt"], **kw)
    assert "missing 1 template file(s)" in str(caught.value) and "followup1_B.txt" in str(caught.value)


def test_the_listing_is_never_changed_by_the_call(tmp_path):
    listing = list(ALL)
    outreach.get_campaign("camp", template_filenames=listing, **_kw(tmp_path))
    assert listing == ALL
