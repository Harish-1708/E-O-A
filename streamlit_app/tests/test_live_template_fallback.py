"""A campaign created or duplicated a moment ago exists on GitHub before this app's own checkout has its templates: the app must build its settings from the live listing."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import config  # noqa: E402
import outreach  # noqa: E402
import preview_logic as P  # noqa: E402
import shell_data as SD  # noqa: E402
from test_pages_smoke import FIXTURE_CAMPAIGN  # noqa: E402

LISTING = ["intro_A.txt", "intro_B.txt", "followup1_A.txt", "followup1_B.txt", "followup2_A.txt", "followup2_B.txt"]


@pytest.fixture(autouse=True)
def _restore_the_hook(fixture_repo):
    saved = P._live_template_listing
    yield
    P._live_template_listing = saved


@pytest.fixture
def own_templates_root(monkeypatch, tmp_path):
    """A templates folder of the test's own: the shared fixture repo is permanent and must never be written to."""
    root = tmp_path / "templates"
    root.mkdir()
    monkeypatch.setattr(config, "TEMPLATES_ROOT", str(root))
    return root


class Provider:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def __call__(self, name):
        self.calls.append(name)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def test_a_campaign_whose_templates_are_in_this_apps_own_copy_never_asks_github():
    provider = Provider(LISTING)
    P.register_live_template_listing(provider)
    cfg = P.get_campaign_cfg(FIXTURE_CAMPAIGN)
    assert cfg["_campaign_name"] == FIXTURE_CAMPAIGN and provider.calls == []


def test_a_campaign_missing_from_this_apps_copy_is_built_from_the_live_listing():
    provider = Provider(LISTING)
    P.register_live_template_listing(provider)
    cfg = P.get_campaign_cfg("Duplicated_Campaign")
    assert provider.calls == ["Duplicated_Campaign"] and [s["name"] for s in cfg["stages"]] == ["intro", "followup1", "followup2"] and cfg["variants"] == ["A", "B"]
    assert cfg["master_tab"] == "Duplicated_Campaign Master Sheet"


def test_a_folder_that_exists_but_has_no_template_files_also_falls_back(own_templates_root):
    """This is what the real screenshot showed: the folder was there ('/mount/src/.../templates/<name>') but without any template file in it."""
    (own_templates_root / "Half_Synced").mkdir()
    (own_templates_root / "Half_Synced" / "README.md").write_text("not a template")
    P.register_live_template_listing(Provider(LISTING))
    assert [s["name"] for s in P.get_campaign_cfg("Half_Synced")["stages"]] == ["intro", "followup1", "followup2"]


@pytest.mark.parametrize("answer", [None, [], RuntimeError("github is down")])
def test_if_the_live_listing_is_not_available_the_original_error_is_reported_unchanged(answer):
    P.register_live_template_listing(Provider(answer))
    with pytest.raises(outreach.ConfigError, match="No templates found for campaign 'Nowhere'"):
        P.get_campaign_cfg("Nowhere")


def test_with_no_hook_registered_nothing_changes():
    P.register_live_template_listing(None)
    with pytest.raises(outreach.ConfigError, match="No templates found for campaign 'Nowhere'"):
        P.get_campaign_cfg("Nowhere")


def test_the_check_for_a_local_copy_says_yes_for_a_real_campaign_and_no_for_one_that_does_not_exist():
    assert P.local_templates_present(FIXTURE_CAMPAIGN) is True and P.local_templates_present("Never_Existed") is False


def test_the_check_for_a_local_copy_needs_a_real_template_file_in_the_folder(own_templates_root):
    (own_templates_root / "Only_Readme").mkdir()
    (own_templates_root / "Only_Readme" / "README.md").write_text("x")
    (own_templates_root / "Has_Template").mkdir()
    (own_templates_root / "Has_Template" / "intro_A.txt").write_text("hi")
    assert P.local_templates_present("Only_Readme") is False and P.local_templates_present("Has_Template") is True


class FakeClient:
    def __init__(self, files, override=None, boom=None):
        self.files, self.override, self.boom, self.listed = files, override, boom, []

    def list_directory_files(self, path, ref="main"):
        self.listed.append(path)
        if self.boom:
            raise self.boom
        return list(self.files)

    def get_file_content(self, path, ref="main"):
        return self.override if path.startswith("config/campaigns/") else None


def test_the_apps_provider_lists_the_campaigns_templates_folder_on_github_and_keeps_only_template_files(monkeypatch):
    client = FakeClient(["intro_A.txt", "README.md", ".gitkeep", "followup1_A.txt"])
    monkeypatch.setattr(SD, "safe_github_client", lambda: client)
    assert SD._live_template_listing("Dup") == ["intro_A.txt", "followup1_A.txt"] and client.listed == ["templates/Dup"]


@pytest.mark.parametrize("client", [None, FakeClient([], boom=RuntimeError("api down"))])
def test_the_apps_provider_gives_nothing_rather_than_an_error_when_github_cannot_be_read(monkeypatch, client):
    monkeypatch.setattr(SD, "safe_github_client", lambda: client)
    assert SD._live_template_listing("Dup") is None


def test_importing_the_shared_data_module_registers_the_provider_for_every_page():
    P.register_live_template_listing(None)
    import importlib
    importlib.reload(SD)
    assert P._live_template_listing is SD._live_template_listing


def test_a_duplicated_campaign_loads_fully_with_its_live_settings_even_though_this_apps_copy_has_nothing(monkeypatch):
    """The reported bug end to end: the campaign is on GitHub (templates and settings, created as a Draft); this app's checkout has neither."""
    client = FakeClient(LISTING, override=b"status: draft\nsending:\n  daily_limit: 42\n")
    monkeypatch.setattr(SD, "safe_github_client", lambda: client)
    P.register_live_template_listing(SD._live_template_listing)
    cfg = SD.get_campaign_cfg_live("Duplicated_Campaign")
    assert cfg["status"] == "draft" and cfg["sending"]["daily_limit"] == 42
    assert [s["name"] for s in cfg["stages"]] == ["intro", "followup1", "followup2"] and cfg["variants"] == ["A", "B"] and cfg["_campaign_name"] == "Duplicated_Campaign"
