"""An HTML campaign's Settings tab, through the real page: the link tagging card (HTML campaigns only), and that the email footer is NOT here any more (it lives under the design in
the Sequences tab). A plain-text campaign must show none of it."""
import os
import sys

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from github_client import GitHubActionsError  # noqa: E402
from settings_logic import override_file_path  # noqa: E402
from test_pages_smoke import FIXTURE_CAMPAIGN  # noqa: E402
from test_settings_page import _clear_streamlit_caches, _every_test_uses_the_fixture_repo, _stub_the_live_template_listing, markup, settings_page  # noqa: E402

_AUTOUSE_FIXTURES = (_every_test_uses_the_fixture_repo, _clear_streamlit_caches, _stub_the_live_template_listing)        # imported so pytest applies them, as in the Settings page tests

HTML = {"format": "html"}
FOOTER = {"business_name": "Kelson Agency", "address": "12 Example Street", "opt_out": 'Reply "unsubscribe".'}
PATH = override_file_path(FIXTURE_CAMPAIGN)


def test_the_email_footer_is_not_in_settings_for_any_campaign_it_is_edited_under_the_design():
    for cfg in (None, HTML, {**HTML, "footer": FOOTER}):
        with settings_page(cfg=cfg) as (at, _):
            assert not any((t.key or "").startswith("footer_") for t in at.text_input) and not any(b.key == "footer_save" for b in at.button)
            assert "Email footer" not in markup(at) and list(at.exception) == []


# ================================================================ link tagging

def tagging_switch(at):
    return next(t for t in at.toggle if t.key == f"tracking_utm_{FIXTURE_CAMPAIGN}")


def tagging_save(at):
    return next(b for b in at.button if b.key == "tracking_save")


def test_a_plain_text_campaign_shows_no_link_tagging_card():
    with settings_page() as (at, _):
        assert not any((t.key or "").startswith("tracking_utm_") for t in at.toggle) and "Link tagging" not in markup(at)


def test_the_link_tagging_card_sits_under_its_own_html_email_heading_which_a_plain_text_campaign_never_shows():
    with settings_page(cfg=HTML) as (at, _):
        text = markup(at)
        assert "HTML email" in text and text.index("HTML email") < text.index("Link tagging")
    with settings_page() as (at, _):
        assert "HTML email" not in markup(at)


def test_an_html_campaign_shows_link_tagging_off_by_default_with_save_off():
    with settings_page(cfg=HTML) as (at, _):
        assert tagging_switch(at).value is False and tagging_save(at).disabled is True and "Link tagging" in markup(at) and "Turn this on and save" in markup(at)


def test_a_campaign_that_has_it_on_shows_it_on_with_an_example_naming_the_campaign_and_the_limits():
    with settings_page(cfg={**HTML, "tracking": {"utm": True}}) as (at, _):
        text = markup(at)
        assert tagging_switch(at).value is True and f"utm_campaign={FIXTURE_CAMPAIGN}" in text and "utm_content=intro_A" in text and "does not record clicks inside this tool" in text


def test_switching_it_on_turns_save_on_and_saving_writes_it_and_keeps_the_other_settings():
    live = {PATH: yaml.safe_dump({"format": "html", "status": "draft", "footer": FOOTER}).encode()}
    with settings_page(cfg=HTML, live=live) as (at, captured):
        tagging_switch(at).set_value(True)
        at.run(timeout=30)
        assert tagging_save(at).disabled is False and "Unsaved change" in markup(at)
        tagging_save(at).click()
        at.run(timeout=30)
        assert list(at.exception) == [] and list(at.error) == [] and len(captured["commits"]) == 1
        commit = captured["commits"][0]
        assert commit["path"] == PATH and commit["message"].startswith(f"Update link tagging for {FIXTURE_CAMPAIGN} (via Streamlit, by ")
        assert yaml.safe_load(commit["content"]) == {"format": "html", "status": "draft", "footer": FOOTER, "tracking": {"utm": True}}
        assert any("Link tagging saved" in s.value for s in at.success) and tagging_save(at).disabled is True and "Unsaved change" not in markup(at)


def test_switching_it_off_again_is_saved_as_off_not_removed():
    live = {PATH: yaml.safe_dump({"format": "html", "tracking": {"utm": True, "source": "kelson"}}).encode()}
    with settings_page(cfg={**HTML, "tracking": {"utm": True}}, live=live) as (at, captured):
        tagging_switch(at).set_value(False)
        at.run(timeout=30)
        tagging_save(at).click()
        at.run(timeout=30)
        assert yaml.safe_load(captured["commits"][0]["content"])["tracking"] == {"utm": False, "source": "kelson"}


def test_a_failed_save_of_link_tagging_says_so_and_keeps_the_change_unsaved():
    with settings_page(cfg=HTML, live={PATH: b"format: html\n"}, commit_error=GitHubActionsError("boom")) as (at, _):
        tagging_switch(at).set_value(True)
        at.run(timeout=30)
        tagging_save(at).click()
        at.run(timeout=30)
        assert any("Failed to save" in e.value for e in at.error) and not any("Link tagging saved" in s.value for s in at.success) and tagging_save(at).disabled is False
