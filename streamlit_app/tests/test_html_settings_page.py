"""The email footer card in an HTML campaign's Settings tab, through the real page. A plain-text campaign must not show it at all."""
import html
import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import html_campaign_logic as hc  # noqa: E402
from github_client import GitHubActionsError  # noqa: E402
from settings_logic import override_file_path  # noqa: E402
from test_pages_smoke import FIXTURE_CAMPAIGN  # noqa: E402
from test_settings_page import _clear_streamlit_caches, _every_test_uses_the_fixture_repo, _stub_the_live_template_listing, markup, settings_page  # noqa: E402

_AUTOUSE_FIXTURES = (_every_test_uses_the_fixture_repo, _clear_streamlit_caches, _stub_the_live_template_listing)        # imported so pytest applies them, as in the Settings page tests

HTML = {"format": "html"}
FOOTER = {"business_name": "Kelson Agency", "address": "12 Example Street", "opt_out": 'Reply "unsubscribe".'}
PATH = override_file_path(FIXTURE_CAMPAIGN)


def box(at, name):
    return next(t for t in at.text_input if t.key == f"footer_{name}_{FIXTURE_CAMPAIGN}")


def fill(at, business_name=None, address=None, opt_out=None):
    for name, value in (("business_name", business_name), ("address", address), ("opt_out", opt_out)):
        if value is not None:
            box(at, name).set_value(value)
    at.run(timeout=30)


def save_button(at):
    return next(b for b in at.button if b.key == "footer_save")


# ================================================================ who sees it

def test_a_plain_text_campaign_shows_no_footer_card_at_all():
    with settings_page() as (at, _):
        assert not any((t.key or "").startswith("footer_") for t in at.text_input) and not any(b.key == "footer_save" for b in at.button)
        assert "Email footer" not in markup(at) and "HTML email" not in markup(at) and list(at.exception) == []


def test_an_html_campaign_shows_the_footer_card_with_its_three_boxes_and_save_off():
    with settings_page(cfg=HTML) as (at, _):
        assert [box(at, n).value for n in ("business_name", "address", "opt_out")] == ["", "", ""] and save_button(at).disabled is True
        assert "Email footer" in markup(at) and "Needs details" in markup(at) and list(at.exception) == []


def test_a_saved_footer_fills_the_boxes_and_the_card_says_it_is_complete_and_shows_how_it_will_look():
    with settings_page(cfg={**HTML, "footer": FOOTER}) as (at, _):
        assert [box(at, n).value for n in ("business_name", "address", "opt_out")] == ["Kelson Agency", "12 Example Street", 'Reply "unsubscribe".']
        text = markup(at)
        assert "Complete" in text and "se-footer-preview" in text and "12 Example Street" in text and save_button(at).disabled is True


# ================================================================ typing

def test_typing_marks_the_change_as_unsaved_and_turns_save_on():
    with settings_page(cfg=HTML) as (at, _):
        fill(at, business_name="Kelson Agency")
        assert save_button(at).disabled is False and "Unsaved change" in markup(at)


def test_the_preview_appears_only_once_the_name_and_the_address_are_both_filled_in():
    with settings_page(cfg=HTML) as (at, _):
        fill(at, business_name="Kelson Agency")
        assert "se-footer-preview" not in markup(at).replace(".se-footer-preview", "")
        fill(at, address="12 Example Street")
        assert 'class="se-footer-preview"' in markup(at) and hc.DEFAULT_OPT_OUT.replace('"', "&quot;") in markup(at)


@pytest.mark.parametrize("hostile", ['<script>alert(1)</script>', '"><img src=x onerror=alert(1)>'])
def test_nothing_typed_into_the_footer_can_become_markup_in_the_preview(hostile):
    with settings_page(cfg=HTML) as (at, _):
        fill(at, business_name=hostile, address=hostile, opt_out=hostile)
        text = markup(at)
        assert "<script>alert" not in text and "<img src=x" not in text and html.escape(hostile) in text


# ================================================================ saving

def test_saving_writes_the_footer_to_the_campaigns_settings_file_and_keeps_every_other_setting():
    live = {PATH: yaml.safe_dump({"format": "html", "status": "draft", "sending": {"daily_limit": 40}}).encode()}
    with settings_page(cfg=HTML, live=live) as (at, captured):
        fill(at, business_name="  Kelson Agency ", address=" 12 Example Street ", opt_out="")
        save_button(at).click()
        at.run(timeout=30)
        assert list(at.exception) == [] and list(at.error) == [] and len(captured["commits"]) == 1
        commit = captured["commits"][0]
        assert commit["path"] == PATH and commit["message"].startswith(f"Update email footer for {FIXTURE_CAMPAIGN} (via Streamlit, by ")
        assert yaml.safe_load(commit["content"]) == {"format": "html", "status": "draft", "sending": {"daily_limit": 40}, "footer": {"business_name": "Kelson Agency", "address": "12 Example Street"}}
        assert any("Footer saved" in s.value for s in at.success)                 # the message survives the redraw that shows the card as saved


def test_after_saving_the_card_shows_as_saved_and_complete():
    with settings_page(cfg=HTML, live={PATH: b"format: html\n"}) as (at, _):
        fill(at, business_name="Kelson Agency", address="12 Example Street")
        save_button(at).click()
        at.run(timeout=30)
        assert save_button(at).disabled is True and "Unsaved change" not in markup(at) and "Complete" in markup(at)


def test_saving_with_the_address_missing_is_refused_and_writes_nothing():
    with settings_page(cfg=HTML, live={PATH: b"format: html\n"}) as (at, captured):
        fill(at, business_name="Kelson Agency")
        save_button(at).click()
        at.run(timeout=30)
        assert captured.get("commits") is None and any("Enter the business name and the postal address" in e.value for e in at.error)


def test_a_failed_save_says_so_and_does_not_show_the_footer_as_saved():
    with settings_page(cfg=HTML, live={PATH: b"format: html\n"}, commit_error=GitHubActionsError("boom")) as (at, _):
        fill(at, business_name="Kelson Agency", address="12 Example Street")
        save_button(at).click()
        at.run(timeout=30)
        assert any("Failed to save" in e.value for e in at.error) and not any("Footer saved" in s.value for s in at.success) and save_button(at).disabled is False


def test_saving_on_a_campaign_with_no_settings_file_yet_creates_one_with_the_footer():
    with settings_page(cfg=HTML, live={}) as (at, captured):
        fill(at, business_name="K", address="A")
        save_button(at).click()
        at.run(timeout=30)
        assert yaml.safe_load(captured["commits"][0]["content"]) == {"footer": {"business_name": "K", "address": "A"}}


# ================================================================ link tagging

def tagging_switch(at):
    return next(t for t in at.toggle if t.key == f"tracking_utm_{FIXTURE_CAMPAIGN}")


def tagging_save(at):
    return next(b for b in at.button if b.key == "tracking_save")


def test_a_plain_text_campaign_shows_no_link_tagging_card():
    with settings_page() as (at, _):
        assert not any((t.key or "").startswith("tracking_utm_") for t in at.toggle) and "Link tagging" not in markup(at)


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
