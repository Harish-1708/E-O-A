"""The Campaigns list through the real page: header and totals, status filters, search, sort, the empty states, Open / Duplicate / Restore and both dialogs' live feedback."""
import html
import os
import re
import sys
from unittest.mock import patch

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from test_pages_smoke import FIXTURE_CAMPAIGN, PAGES_DIR, _authed_session, _dashboard_secrets, _mock_github_writes  # noqa: E402
from campaign_status_logic import status_label  # noqa: E402


@pytest.fixture(autouse=True)
def _every_test_uses_the_fixture_repo(fixture_repo):
    yield


@pytest.fixture(autouse=True)
def _clear_streamlit_caches():
    st.cache_resource.clear()
    st.cache_data.clear()
    yield
    st.cache_resource.clear()
    st.cache_data.clear()


def row(name, status, leads=0, sent=0, replies=0, rate="—", last="", problems=None, bars=None):
    series = {"bars": bars, "unit": "day", "start": "2026-10-01", "end": "2026-10-05", "total": sum(bars)} if bars else {"bars": [], "unit": "day", "start": "", "end": "", "total": 0}
    return {"name": name, "status": status, "status_label": status_label(status), "problems": problems or [], "total_leads": leads, "sent": sent, "replies": replies,
            "reply_rate": rate, "last_activity": last, "series": series}


ROWS = [row("Old_Done", "completed", 10, 28, 3, "10.7%", "2026-09-01 10:00:00", bars=[3, 2, 1]), row("Live_17SEP", "running", 77, 252, 51, "20.2%", "2026-10-14 09:00:00", bars=[0, 12, 40, 3]),
        row("Soon_Draft", "draft"), row("Mid_Done", "completed", 39, 235, 20, "8.5%", "2026-10-12 09:00:00", bars=[60, 70]),
        row("Stuck_Q4", "attention", problems=["No leads with an email address"]), row("On_Hold", "paused", 5, 3, 0, "0.0%", "2026-10-13 09:00:00")]


def hub(rows=None, deleted=None, errors=None, boom=None, prime=None):
    """Open the Campaigns page with a made-up list; returns a context manager yielding (at, captured)."""
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        captured, fake_create_file = _mock_github_writes()
        listing = list(ROWS if rows is None else rows)

        def fake_build(names, get_cfg, fetch, max_workers=1):
            if boom:
                raise boom
            return listing, list(deleted or []), list(errors or [])
        with patch("gspread.authorize", return_value=type("C", (), {"open_by_key": lambda self, k: None})()), \
             patch("google.oauth2.service_account.Credentials.from_service_account_info", return_value=object()), \
             patch("github_client.GitHubClient.create_file", fake_create_file), \
             patch("campaigns_hub_logic.build_campaigns_hub", fake_build):
            at = AppTest.from_file(os.path.join(PAGES_DIR, "campaigns.py"))
            at.secrets.update(_dashboard_secrets())
            for key, value in _authed_session().items():
                at.session_state[key] = value
            for key, value in (prime or {}).items():
                at.session_state[key] = value
            at.run(timeout=30)
            yield at, captured
    return _ctx()


def markup(at):
    return " ".join(str(m.value) for m in at.markdown)


def names_in_order(at):
    return re.findall(r'hb-name">(.*?)</div>', markup(at))


def pills(at):
    return next(g for g in at.get("button_group") if g.key == "hub_filter")


def test_the_header_says_what_is_in_the_list_and_the_four_totals_add_up():
    with hub() as (at, _):
        assert list(at.exception) == [] and list(at.error) == []
        text = markup(at)
        assert 'hb-title">Campaigns<' in text and "6 campaigns · 1 running · 1 needs attention" in text
        values = re.findall(r'hb-stat-v">(.*?)</div>', text)
        assert values == ["1", "131", "518", "74"] and "14.3% reply rate" in text


def test_the_list_starts_active_first_running_then_attention_paused_draft_and_completed():
    with hub() as (at, _):
        assert names_in_order(at) == ["Live_17SEP", "Stuck_Q4", "On_Hold", "Soon_Draft", "Mid_Done", "Old_Done"]


def test_every_card_has_open_and_duplicate_and_a_chart_when_it_has_sent_something():
    with hub() as (at, _):
        for name in [r["name"] for r in ROWS]:
            assert any(b.key == f"open_{name}" for b in at.button) and any(b.key == f"duplicate_{name}" for b in at.button)
        assert markup(at).count('<div class="hb-spark">') == 3 and markup(at).count("No sends yet") == 3


def test_the_status_filters_show_their_counts_and_choosing_one_narrows_the_list():
    with hub() as (at, _):
        group = pills(at)
        assert [o.content for o in group.proto.options] == ["All 6", "Running 1", "Attention 1", "Paused 1", "Draft 1", "Completed 2"]
        group.set_value("completed")
        at.run(timeout=30)
        assert names_in_order(at) == ["Mid_Done", "Old_Done"]
        pills(at).set_value("all")
        at.run(timeout=30)
        assert len(names_in_order(at)) == 6


def test_deselecting_the_filter_shows_everything_again():
    with hub() as (at, _):
        pills(at).set_value("running")
        at.run(timeout=30)
        assert names_in_order(at) == ["Live_17SEP"]
        pills(at).set_value(None)
        at.run(timeout=30)
        assert len(names_in_order(at)) == 6


def test_search_and_the_status_filter_work_together():
    with hub() as (at, _):
        at.text_input(key="hub_search").set_value("done")
        at.run(timeout=30)
        assert sorted(names_in_order(at)) == ["Mid_Done", "Old_Done"]
        pills(at).set_value("running")
        at.run(timeout=30)
        assert names_in_order(at) == [] and "No campaigns match “done”" in html.unescape(markup(at))


def test_nothing_matching_says_so_and_clearing_brings_everything_back():
    with hub() as (at, _):
        at.text_input(key="hub_search").set_value("zzz")
        at.run(timeout=30)
        assert "No campaigns match “zzz”" in html.unescape(markup(at)) and names_in_order(at) == []
        next(b for b in at.button if b.key == "hub_clear_filters").click()
        at.run(timeout=30)
        assert len(names_in_order(at)) == 6 and at.text_input(key="hub_search").value == "" and pills(at).value in ("all", None)


@pytest.mark.parametrize("option, first, last", [("Name", "Live_17SEP", "Stuck_Q4"), ("Most replies", "Live_17SEP", "On_Hold"), ("Last activity", "Live_17SEP", "Stuck_Q4"), ("Active first", "Live_17SEP", "Old_Done")])
def test_the_sort_menu_reorders_the_list(option, first, last):
    with hub() as (at, _):
        at.selectbox(key="hub_sort").set_value(option)
        at.run(timeout=30)
        order = names_in_order(at)
        assert order[0] == first and order[-1] == last and len(order) == 6
        assert at.selectbox(key="hub_sort").options[0] == "Sort: Active first"


def test_open_goes_to_that_campaign_and_duplicate_opens_the_dialog_for_it():
    with hub() as (at, _):
        next(b for b in at.button if b.key == "duplicate_Live_17SEP").click()
        at.run(timeout=30)
        assert at.session_state["duplicating_campaign"] == "Live_17SEP" and "Copying from" in markup(at) and "Live_17SEP" in markup(at)
    with hub() as (at, _):
        next(b for b in at.button if b.key == "open_Mid_Done").click()
        at.run(timeout=30)
        assert at.session_state["selected_campaign"] == "Mid_Done"


def test_with_no_campaigns_there_is_a_welcome_and_a_working_new_campaign_button():
    with hub(rows=[]) as (at, _):
        assert "Create your first campaign" in markup(at) and "0 campaigns" in markup(at)
        next(b for b in at.button if b.key == "hub_empty_new").click()
        at.run(timeout=30)
        assert at.session_state["show_new_campaign_dialog"] is True and any(t.key == "new_campaign_name" for t in at.text_input)


def test_a_campaign_that_could_not_be_loaded_is_reported_with_its_reason():
    with hub(errors=[("Spring_Test", "Master Sheet tab not found")]) as (at, _):
        assert "1 campaign couldn’t be loaded" in markup(at) and any("Show details" == e.label for e in at.expander)
        assert any("Spring_Test" in m.value and "Master Sheet tab not found" in m.value for m in at.markdown)


def test_archived_campaigns_are_tucked_away_with_a_restore_button_each():
    with hub(deleted=[row("Gone_One", "deleted"), row("Gone_Two", "deleted")]) as (at, _):
        assert any(e.label == "🗑 Archived campaigns (2)" for e in at.expander)
        assert any(b.key == "restore_Gone_One" for b in at.button) and any(b.key == "restore_Gone_Two" for b in at.button) and "Gone_Two" in markup(at)
    st.cache_data.clear()                                                                         # the list is cached for a few seconds: start the next page load fresh
    with hub() as (at, _):
        assert not any("Archived campaigns" in e.label for e in at.expander)


def test_if_the_list_cannot_be_loaded_the_header_and_buttons_still_work_and_the_page_says_why():
    with hub(boom=RuntimeError("sheet unreachable")) as (at, _):
        assert any("Couldn't load campaigns: sheet unreachable" in e.value for e in at.error) and any(b.key == "refresh_hub_button" for b in at.button) and any(b.key == "hub_new_campaign" for b in at.button)
        assert list(at.exception) == [] and '<div class="hb-stat-v">' not in markup(at)


def test_the_new_campaign_button_opens_the_dialog_and_leaving_the_page_closes_it():
    with hub() as (at, _):
        next(b for b in at.button if b.key == "hub_new_campaign").click()
        at.run(timeout=30)
        assert any(t.key == "new_campaign_name" for t in at.text_input)
        at.run(timeout=30)
        assert any(t.key == "new_campaign_name" for t in at.text_input)             # still open across an ordinary rerun (a widget inside it was touched)
    with hub(prime={"show_new_campaign_dialog": True}) as (at, _):
        assert at.session_state["show_new_campaign_dialog"] is False and not any(t.key == "new_campaign_name" for t in at.text_input)      # arriving afresh never reopens it


def _open_new(at):
    next(b for b in at.button if b.key == "hub_new_campaign").click()
    at.run(timeout=30)


@pytest.mark.parametrize("typed, kind, said", [("", "hint", "Letters, numbers and underscores only"), ("bad name", "error", "Use only letters, numbers, and underscores"), (FIXTURE_CAMPAIGN, "error", "already exists"), ("Fresh_Name_1", "ok", "Looks good")])
def test_the_new_campaign_name_box_gives_live_feedback(typed, kind, said):
    with hub() as (at, _):
        _open_new(at)
        at.text_input(key="new_campaign_name").set_value(typed)
        at.run(timeout=30)
        assert f'hb-feedback hb-feedback-{kind}">' in markup(at) and said in html.unescape(markup(at))


def test_create_stays_off_until_confirmed_and_a_bad_name_is_still_refused_at_the_button():
    with hub() as (at, captured):
        _open_new(at)
        go = lambda: next(b for b in at.button if b.key == "new_campaign_go")      # noqa: E731
        assert go().disabled is True and go().label == "Create campaign"
        at.text_input(key="new_campaign_name").set_value("bad name")
        at.checkbox(key="new_campaign_confirm").set_value(True)
        at.run(timeout=30)
        assert go().disabled is False
        go().click()
        at.run(timeout=30)
        assert any("Use only letters" in e.value for e in at.error) and captured.get("commits") is None and captured.get("files") is None


def test_cancel_closes_the_new_campaign_dialog():
    with hub() as (at, _):
        _open_new(at)
        next(b for b in at.button if b.key == "new_campaign_cancel").click()
        at.run(timeout=30)
        assert at.session_state["show_new_campaign_dialog"] is False and not any(t.key == "new_campaign_name" for t in at.text_input)


def test_the_duplicate_dialog_says_what_is_copied_and_gives_the_same_live_feedback():
    with hub() as (at, _):
        next(b for b in at.button if b.key == "duplicate_Live_17SEP").click()
        at.run(timeout=30)
        text = html.unescape(markup(at))
        assert text.count('class="hb-copy-yes"') == 2 and text.count('class="hb-copy-no"') == 1 and "Leads, sends and replies are never copied" in text
        at.text_input(key="duplicate_campaign_new_name").set_value(FIXTURE_CAMPAIGN)                 # a name that really exists as a campaign folder
        at.run(timeout=30)
        assert "already exists" in html.unescape(markup(at))
        go = next(b for b in at.button if b.key == "duplicate_campaign_go")
        assert go.disabled is True and go.label == "Duplicate campaign"


def test_cancel_closes_the_duplicate_dialog():
    with hub() as (at, _):
        next(b for b in at.button if b.key == "duplicate_Live_17SEP").click()
        at.run(timeout=30)
        next(b for b in at.button if b.key == "cancel_duplicate_campaign").click()
        at.run(timeout=30)
        assert at.session_state["duplicating_campaign"] is None and not any(t.key == "duplicate_campaign_new_name" for t in at.text_input)


def test_the_header_gives_refresh_and_new_campaign_the_same_width():
    """REGRESSION guard (read from the page's own source): both buttons sit in columns of the same ratio."""
    source = open(os.path.join(os.path.dirname(__file__), "..", "pages", "campaigns.py"), encoding="utf-8").read()
    ratios = re.search(r"head_column, refresh_column, new_column = st\.columns\(\[([\d., ]+)\]", source).group(1).replace(" ", "").split(",")
    assert len(ratios) == 3 and ratios[1] == ratios[2]


def test_the_totals_and_subtitle_always_describe_every_campaign_even_while_a_filter_or_search_narrows_the_list():
    with hub() as (at, _):
        before = re.findall(r'hb-stat-v">(.*?)</div>', markup(at))
        pills(at).set_value("draft")
        at.text_input(key="hub_search").set_value("Soon")
        at.run(timeout=30)
        assert names_in_order(at) == ["Soon_Draft"]
        assert re.findall(r'hb-stat-v">(.*?)</div>', markup(at)) == before == ["1", "131", "518", "74"] and "6 campaigns · 1 running · 1 needs attention" in markup(at)


def test_arriving_from_home_with_new_campaign_opens_the_dialog_and_uses_up_the_note():
    with hub(prime={"hub_open_new_on_arrival": True}) as (at, _):
        assert at.session_state["show_new_campaign_dialog"] is True and any(t.key == "new_campaign_name" for t in at.text_input)
        assert "hub_open_new_on_arrival" not in at.session_state                                  # used once: it can never reopen the dialog later
