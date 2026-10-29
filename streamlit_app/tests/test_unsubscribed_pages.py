"""Page-level tests: what the Data tab and the Performance page show for a lead who unsubscribed, for an HTML campaign (the only kind that has an unsubscribe page) and not for a plain-text one."""
import contextlib
import html
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import outreach  # noqa: E402
from test_html_editor_page import FOOTER  # noqa: E402
from test_leads_page import _click, _markup as _data_markup, _open_keys, _panel, _rows, data_tab, lead  # noqa: E402
from test_leads_page import _clear_streamlit_caches, _every_test_uses_the_fixture_repo, _stub_the_live_template_reads_other_tabs_make  # noqa: E402,F401  (autouse fixtures)
from test_performance_page import _bars, _kpis, _markup as _perf_markup, _run_page, _sheet, _tables  # noqa: E402

_AUTOUSE_FIXTURES = (_clear_streamlit_caches, _every_test_uses_the_fixture_repo, _stub_the_live_template_reads_other_tabs_make)      # imported so pytest applies them to this file too
_REAL = outreach.get_campaign
SENT = "2026-10-01 10:00:00"


def _html_get(*args, **kwargs):
    cfg = _REAL(*args, **kwargs)
    cfg["format"] = "html"
    cfg["footer"] = dict(FOOTER)
    return cfg


@contextlib.contextmanager
def html_campaign():
    with patch.object(outreach, "get_campaign", _html_get):
        yield


LEADS = [lead(1, Status="Stopped - Unsubscribed", IntroSentAt=SENT, LastActionAt="2026-10-05 09:30:00"), lead(2, Status="Stopped - Unsubscribed", IntroSentAt=SENT),
         lead(3, Status="Stopped - Replied", IntroSentAt=SENT), lead(4, IntroSentAt=SENT, Status="Intro Sent"), lead(5)]


# ================================================================ the Data tab

def test_an_html_campaign_offers_an_unsubscribed_chip_with_its_count_just_before_removed():
    with html_campaign(), data_tab(LEADS) as (at, _):
        assert at.radio(key="data_filter").options == ["All · 5", "In Progress · 1", "Completed · 0", "Replied · 1", "Bounced · 0", "Unsubscribed · 2", "Removed · 0"]


def test_choosing_the_unsubscribed_chip_shows_only_the_people_who_unsubscribed():
    with html_campaign(), data_tab(LEADS) as (at, _):
        at.radio(key="data_filter").set_value("Unsubscribed")
        at.run(timeout=30)
        rows = _rows(at)
        assert sorted(r["email"] for r in rows) == ["lead1@example.com", "lead2@example.com"] and {r["status"] for r in rows} == {"Unsubscribed"}
        assert "Showing 2 of 5 lead(s)" in " ".join(c.value for c in at.caption)


def test_an_unsubscribed_lead_has_an_unsubscribed_pill_in_the_all_view_and_the_others_keep_theirs():
    with html_campaign(), data_tab(LEADS) as (at, _):
        statuses = {r["email"]: r["status"] for r in _rows(at)}
        assert statuses["lead1@example.com"] == "Unsubscribed" and statuses["lead3@example.com"] == "Replied" and statuses["lead4@example.com"] == "In sequence" and statuses["lead5@example.com"] == "Not contacted"
        assert 'ld-pill ld-pill-waiting">Unsubscribed<' in _data_markup(at)


def test_an_unsubscribed_lead_is_not_counted_as_in_progress_or_completed():
    with html_campaign(), data_tab(LEADS) as (at, _):
        options = at.radio(key="data_filter").options
        assert "In Progress · 1" in options and "Completed · 0" in options


def test_opening_an_unsubscribed_lead_shows_when_they_unsubscribed_in_its_timeline():
    with html_campaign(), data_tab(LEADS) as (at, _):
        key = next(k for k in _open_keys(at) if "1" in k.split("_")[-1] or k.endswith("1"))
        _click(at, f"data_open_{key}")
        panel = _panel(at)
        assert "Unsubscribed — sending stopped" in panel and "Unsubscribed" in panel and "Next follow-up due" not in panel


def test_a_plain_text_campaign_has_exactly_the_chips_it_always_had():
    with data_tab(LEADS) as (at, _):
        assert at.radio(key="data_filter").options == ["All · 5", "In Progress · 1", "Completed · 0", "Replied · 1", "Bounced · 0", "Removed · 0"]


def test_a_chip_chosen_on_an_html_campaign_never_breaks_a_campaign_that_does_not_have_it():
    starter = patch.object(outreach, "get_campaign", _html_get)
    starter.start()
    try:
        with data_tab(LEADS) as (at, _):
            at.radio(key="data_filter").set_value("Unsubscribed")
            at.run(timeout=30)
            assert at.radio(key="data_filter").value == "Unsubscribed"
            starter.stop()
            starter = None
            at.run(timeout=30)
            assert not at.exception and at.radio(key="data_filter").value == "All" and "Unsubscribed · 2" not in " ".join(at.radio(key="data_filter").options)
    finally:
        if starter is not None:
            starter.stop()


# ================================================================ the Performance page

PERF_LEADS = [{"LeadID": "1", "Email": "a@x.com", "IntroSentAt": SENT, "ReplyStatus": "", "Status": "Stopped - Unsubscribed"},
              {"LeadID": "2", "Email": "b@x.com", "IntroSentAt": SENT, "ReplyStatus": "", "Status": "Stopped - Unsubscribed"},
              {"LeadID": "3", "Email": "c@x.com", "IntroSentAt": SENT, "ReplyStatus": "", "Status": "Intro Sent"},
              {"LeadID": "4", "Email": "d@x.com", "IntroSentAt": "", "ReplyStatus": "", "Status": ""}]


def test_an_html_campaigns_performance_has_an_unsubscribed_card_with_the_count_and_the_share():
    with html_campaign():
        at = _run_page(_sheet(PERF_LEADS, [], []))
    assert not at.exception
    kpis = dict(_kpis(at))
    assert kpis["Unsubscribed"] == "2" and "66.7% of contacted" in html.unescape(_perf_markup(at)) and 'class="pf-grid pf-g5"' in _perf_markup(at) and 'class="pf-grid pf-g4"' not in _perf_markup(at)


def test_the_where_your_leads_are_now_card_lists_unsubscribed_for_an_html_campaign():
    with html_campaign():
        at = _run_page(_sheet(PERF_LEADS, [], []))
    bars = _bars(at)
    assert bars["Unsubscribed"][1] == 2 and sum(count for _, count in bars.values()) == 4


def test_a_plain_text_campaigns_performance_has_no_unsubscribed_card_or_row():
    at = _run_page(_sheet(PERF_LEADS, [], []))
    assert not at.exception and "Unsubscribed" not in dict(_kpis(at)) and "Unsubscribed" not in _bars(at) and 'class="pf-grid pf-g5"' not in _perf_markup(at) and 'class="pf-grid pf-g4"' in _perf_markup(at)


def test_an_html_campaign_with_nobody_unsubscribed_still_shows_the_card_with_a_zero():
    leads = [dict(PERF_LEADS[2]), dict(PERF_LEADS[3])]
    with html_campaign():
        at = _run_page(_sheet(leads, [], []))
    assert dict(_kpis(at))["Unsubscribed"] == "0" and "Unsubscribed" not in _bars(at)


def test_the_clear_filters_button_appears_only_while_a_chip_other_than_all_is_chosen_and_puts_all_back():
    with html_campaign(), data_tab(LEADS) as (at, _):
        assert "data_filters_clear" not in [b.key for b in at.button]
        at.radio(key="data_filter").set_value("Unsubscribed")
        at.run(timeout=30)
        assert "data_filters_clear" in [b.key for b in at.button]
        at.button(key="data_filters_clear").click()
        at.run(timeout=30)
        assert at.radio(key="data_filter").value == "All" and "data_filters_clear" not in [b.key for b in at.button]



# ================================================================ Performance: unsubscribes in the Activity chart and the three tables

def ago(days, hour=10):
    return (datetime.now(timezone.utc) - timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


def _sent_row(lead_id, stage, variant, ts, account="sales1"):
    return {"BatchID": "B", "Timestamp": ts, "LeadID": str(lead_id), "Email": "", "Campaign": "x", "Stage": stage, "Variant": variant, "SenderAccount": account, "Status": "sent", "MessageID": f"<m{lead_id}{stage}>", "Error": ""}


ACT_LEADS = [{"LeadID": "1", "Email": "a@x.com", "SenderAccount": "sales1", "IntroSentAt": ago(8), "IntroVariant": "A", "FollowUp1SentAt": ago(5), "FollowUp1Variant": "A", "ReplyStatus": "",
              "Status": "Stopped - Unsubscribed", "LastActionAt": ago(4)},
             {"LeadID": "2", "Email": "b@x.com", "SenderAccount": "sales2", "IntroSentAt": ago(7), "IntroVariant": "B", "ReplyStatus": "", "Status": "Stopped - Unsubscribed", "LastActionAt": ago(2)},
             {"LeadID": "3", "Email": "c@x.com", "SenderAccount": "sales1", "IntroSentAt": ago(6), "IntroVariant": "A", "ReplyStatus": "", "Status": "Intro Sent"}]
ACT_LOG = [_sent_row(1, "intro", "A", ago(8)), _sent_row(1, "followup1", "A", ago(5)), _sent_row(2, "intro", "B", ago(7), "sales2"), _sent_row(3, "intro", "A", ago(6))]


def _perf_page(is_html):
    if is_html:
        with html_campaign():
            return _run_page(_sheet(ACT_LEADS, [], ACT_LOG))
    return _run_page(_sheet(ACT_LEADS, [], ACT_LOG))


def _table_with(at, first_header):
    return next(rows for rows in _tables(at) if rows and rows[0] and rows[0][0] == first_header)


def test_an_html_campaigns_activity_card_has_an_unsubscribes_chip_and_an_amber_line():
    at = _perf_page(True)
    markup = _perf_markup(at)
    assert not at.exception and 'class="pf-chip pf-chip-unsubs">Unsubscribes<' in markup and markup.count('stroke="#d97706"') == 1 and "unsubscribes per day" in markup
    assert "Unsubscribes are counted on the day they were recorded in the Sheet." in html.unescape(markup)


def test_a_plain_text_campaigns_activity_card_has_neither():
    at = _perf_page(False)
    markup = _perf_markup(at)
    assert not at.exception and "pf-chip-unsubs\">" not in markup and 'stroke="#d97706"' not in markup and "Unsubscribes are counted" not in html.unescape(markup)


def test_the_stage_variant_and_sender_tables_of_an_html_campaign_have_the_unsubscribed_columns_with_the_right_numbers():
    at = _perf_page(True)
    stage, variant, sender = _table_with(at, "Stage"), _table_with(at, "Variant"), _table_with(at, "Account")
    assert stage[0][-2:] == ["Unsubscribed", "Unsubscribe rate"] and variant[0][-2:] == ["Unsubscribed", "Unsubscribe rate"] and sender[0][-2:] == ["Unsubscribed", "Unsubscribe rate"]
    by_stage = {row[0]: row for row in stage[1:]}
    assert by_stage["Intro"][-2:] == ["1", "33.3%"] and by_stage["Follow-up 1"][-2:] == ["1", "100.0%"]               # lead 2 unsubscribed after its intro; lead 1 after its follow-up
    by_variant = {row[0].split("  ")[0]: row for row in variant[1:]}
    assert by_variant["A"][-2:] == ["1", "33.3%"] and by_variant["B"][-2:] == ["1", "100.0%"]
    by_account = {row[0]: row for row in sender[1:]}
    assert by_account["sales1"][-2:] == ["1", "50.0%"] and by_account["sales2"][-2:] == ["1", "100.0%"]


def test_the_same_tables_of_a_plain_text_campaign_have_exactly_the_columns_they_always_had():
    at = _perf_page(False)
    assert _table_with(at, "Stage")[0] == ["Stage", "Emails sent", "Replies", "Reply rate", "Positive", "Positive rate"] and _table_with(at, "Variant")[0][-1] == "Positive rate"
    assert _table_with(at, "Account")[0] == ["Account", "Emails sent", "Leads contacted", "Replies", "Reply rate"]


def test_the_stage_and_variant_hints_of_an_html_campaign_explain_who_an_unsubscribe_is_credited_to():
    assert "Unsubscribed is credited to the latest email sent to that person before they unsubscribed." in html.unescape(_perf_markup(_perf_page(True)))


def test_the_stage_and_variant_hints_of_a_plain_text_campaign_say_nothing_about_unsubscribing():
    assert "Unsubscribed is credited" not in html.unescape(_perf_markup(_perf_page(False)))          # one page per test: the page caches its data, so a second run in one test would reuse the first


def test_the_where_your_leads_are_now_card_is_followed_by_space_before_the_range_tabs():
    markup = _perf_markup(_perf_page(True))
    assert '<div class="pf-spaced"><div class="pf-card"><div class="pf-card-head"><h3 class="pf-card-title">Where your leads are now</h3>' in markup



def _variant_stage_frame(at):
    return next(d.value for d in at.dataframe if {"Stage", "Variant", "Emails sent"} <= set(d.value.columns))


def test_the_each_variant_stage_by_stage_table_of_an_html_campaign_has_the_unsubscribed_columns():
    frame = _variant_stage_frame(_perf_page(True))
    assert list(frame.columns)[-2:] == ["Unsubscribed", "Unsubscribe rate"]
    rows = {(r["Stage"], r["Variant"]): (r["Unsubscribed"], r["Unsubscribe rate"]) for _, r in frame.iterrows()}
    assert rows[("Follow-up 1", "A")] == (1, "100.0%") and rows[("Intro", "B")] == (1, "100.0%") and rows[("Intro", "A")] == (0, "0.0%")


def test_the_each_variant_stage_by_stage_table_of_a_plain_text_campaign_has_exactly_its_old_columns():
    assert list(_variant_stage_frame(_perf_page(False)).columns) == ["Stage", "Variant", "Emails sent", "Replies", "Reply rate", "Positive"]



def test_an_html_campaigns_paired_table_cards_use_the_grid_that_stacks_sooner():
    markup = _perf_markup(_perf_page(True))
    assert markup.count('class="pf-grid pf-g2w"') == 2 and 'class="pf-grid pf-g2e"' not in markup


def test_a_plain_text_campaigns_paired_cards_keep_the_grid_they_always_had():
    markup = _perf_markup(_perf_page(False))
    assert 'class="pf-grid pf-g2w"' not in markup and markup.count('class="pf-grid pf-g2e"') >= 2
