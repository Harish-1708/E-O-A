"""The Performance headline figures and the CSV export: one source for the cards and the file, and a file that is safe to open in a spreadsheet."""
import csv
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import performance_figures as F  # noqa: E402
import performance_view  # noqa: E402
from performance_logic import INTENT_ORDER, INTENT_NOT_CLASSIFIED, OUTCOME_ORDER  # noqa: E402


def perf(**kw):
    base = {
        "reply_hours": [24.0, 48.0], "contacted": 3, "total_leads": 5, "pending": 2, "sent": 4, "send_failures": 1, "reply_rate": 2 / 3, "replies": 2, "positive": 1, "positive_rate": 1 / 3,
        "negotiating": None, "rights_secured": 4, "median_reply_hours": 36.0, "bounce_rate_hard": 0.0, "bounced_hard": 0, "bounced_soft": 0, "bounce_rate_soft": 0.0,
        "sequence_completion": 0.0, "completed": 0,
        "intents": {**{label: 0 for label in INTENT_ORDER}, INTENT_NOT_CLASSIFIED: 0, "Interested": 1, "Not Interested": 1},
        "stages": [{"stage": "intro", "sent": 3, "replies": 2, "reply_rate": 2 / 3, "positive": 1, "positive_rate": 1 / 3},
                   {"stage": "followup1", "sent": 1, "replies": 0, "reply_rate": 0.0, "positive": 0, "positive_rate": 0.0}],
        "variants": [{"variant": "A", "sent": 2, "replies": 1, "reply_rate": 0.5, "positive": 1, "positive_rate": 0.5}],
        "sender_rows": [{"account": "sales1@x.com", "sent": 4, "leads": 3, "replies": 2, "reply_rate": 2 / 3}],
        "error_rows": [{"error_type": "SMTP", "count": 2}],
    }
    base.update(kw)
    return base


def rows_of(text):
    return list(csv.reader(io.StringIO(text)))


# ================================================================ the figures

def test_there_are_ten_headline_figures_in_the_order_the_page_shows_them():
    figures = F.headline_figures(perf())
    assert [f["label"] for f in figures] == ["Contacted", "Emails sent", "Reply rate", "Positive replies", "Negotiating", "Rights secured",
                                             "Median time to first reply", "Needs follow-up", "Hard bounce rate", "Sequence completion"]
    assert all(set(f) == {"label", "value", "caption", "help", "trend"} for f in figures)


def test_the_figures_are_formatted_for_people_and_a_missing_asana_figure_is_a_dash():
    values = {f["label"]: (f["value"], f["caption"]) for f in F.headline_figures(perf())}
    assert values["Reply rate"] == ("66.7%", "2 replied") and values["Negotiating"][0] == "—" and values["Rights secured"][0] == 4
    assert values["Median time to first reply"][0] == "36.0 hours" and values["Emails sent"] == (4, "1 failed") and values["Contacted"] == (3, "of 5 leads · 2 not yet contacted")
    assert F.headline_figures(perf(send_failures=0))[1]["caption"] == ""
    assert F.headline_figures(perf(reply_hours=[1.0]))[6]["caption"] == "from 1 reply"


def test_the_page_and_the_export_use_the_very_same_function_for_the_figures():
    assert performance_view.headline_figures is F.headline_figures


# ================================================================ the export

def test_the_file_has_every_section_in_order_with_its_header():
    rows = rows_of(F.build_performance_csv(perf(), "Camp"))
    names = [r[0] for i, r in enumerate(rows) if r and i > 0 and rows[i - 1] == []]
    assert rows[0] == ["Camp — Performance"]
    assert names == ["Headline figures", "Where the leads are now", "What the replies say", "Follow-up stage effectiveness", "Template variants", "By sender account", "Deliverability", "Errors (all time)"]


def test_the_headline_section_holds_all_ten_figures_exactly_as_the_page_shows_them():
    rows = rows_of(F.build_performance_csv(perf()))
    start = rows.index(["Figure", "Value", "Note"]) + 1
    got = {r[0]: (r[1], r[2]) for r in rows[start:start + 10]}
    assert got["Reply rate"] == ("66.7%", "2 replied") and got["Negotiating"] == ("—", "from the Asana sync") and got["Emails sent"] == ("4", "1 failed")
    assert len(got) == 10


def test_stage_names_are_readable_and_the_tables_hold_the_right_numbers():
    rows = rows_of(F.build_performance_csv(perf()))
    assert ["Intro", "3", "2", "66.7%", "1", "33.3%"] in rows and ["Follow-up 1", "1", "0", "0.0%", "0", "0.0%"] in rows and not any("followup1" in r for r in rows)
    assert ["A", "2", "1", "50.0%", "1", "50.0%"] in rows and ["sales1@x.com", "4", "3", "2", "66.7%"] in rows and ["SMTP", "2"] in rows
    assert ["Interested", "1"] in rows and ["Not Interested", "1"] in rows and [INTENT_NOT_CLASSIFIED, "0"] in rows


@pytest.mark.parametrize("dangerous", ["=HYPERLINK(\"http://evil.example\",\"x\")", "+1+1", "-2+3", "@SUM(A1)", "\t=1", "\r=1"])
def test_a_cell_that_a_spreadsheet_would_run_as_a_formula_is_made_plain_text(dangerous):
    text = F.build_performance_csv(perf(sender_rows=[{"account": dangerous, "sent": 1, "leads": 1, "replies": 0, "reply_rate": 0.0}], error_rows=[{"error_type": dangerous, "count": 1}]))
    cells = [c for r in rows_of(text) for c in r]
    assert ("'" + dangerous) in cells and dangerous not in cells                          # every occurrence carries the apostrophe


def test_a_title_that_looks_like_a_formula_is_neutralised_too():
    assert rows_of(F.build_performance_csv(perf(), "=cmd|' /C calc'!A0"))[0][0].startswith("'=cmd")


def test_text_with_commas_quotes_and_line_breaks_survives_the_file_unchanged():
    tricky = 'Acme, "Inc."\nSecond line'
    rows = rows_of(F.build_performance_csv(perf(sender_rows=[{"account": tricky, "sent": 1, "leads": 1, "replies": 0, "reply_rate": 0.0}])))
    assert [tricky, "1", "1", "0", "0.0%"] in rows


def test_a_campaign_with_nothing_yet_still_exports_every_section_without_error():
    empty = perf(stages=[], variants=[], sender_rows=[], error_rows=[], reply_hours=[], contacted=0, sent=0, replies=0, positive=0, send_failures=0,
                 reply_rate=None, positive_rate=None, median_reply_hours=None)
    rows = rows_of(F.build_performance_csv(empty))
    assert ["Stage", "Emails sent", "Replies", "Reply rate", "Positive", "Positive rate"] in rows and ["Error type", "Count"] in rows
    assert all(cell is not None and "None" not in cell for r in rows for cell in r)


def test_safe_cell_leaves_ordinary_text_and_numbers_alone():
    for value, expected in [("hello", "hello"), (5, "5"), (0, "0"), (None, ""), ("66.7%", "66.7%"), ("—", "—"), ("a=b", "a=b"), ("Follow-up 1", "Follow-up 1"), ("", "")]:
        assert F.safe_cell(value) == expected


@pytest.mark.parametrize("name, expected", [
    ("Kelson_Creators_Licensing_17SEP", "Kelson_Creators_Licensing_17SEP_performance.csv"), ("my campaign", "my_campaign_performance.csv"), ("a/b\\c:d", "a_b_c_d_performance.csv"),
    ('quote"and\'s', "quote_and_s_performance.csv"), ("../../etc/passwd", "etc_passwd_performance.csv"), ("", "performance_performance.csv"), (None, "performance_performance.csv"),
    ("###", "performance_performance.csv"), ("Café Ünïcode", "Caf_n_code_performance.csv"), ("performance", "performance_performance.csv"), ("x" * 5, "xxxxx_performance.csv"),
])
def test_the_download_file_name_is_safe_and_always_ends_in_performance_csv(name, expected):
    assert F.export_filename(name) == expected


# ================================================================ the reply-rate change

TREND_UP = {"days": 7, "current_rate": 0.25, "previous_rate": 0.1, "delta_pts": 15.0, "sent_current": 8, "sent_previous": 10, "answered_current": 2, "answered_previous": 1}


@pytest.mark.parametrize("delta, text, kind", [(2.1, "▲ 2.1 pts vs previous 7 days", "up"), (15.0, "▲ 15.0 pts vs previous 7 days", "up"), (-0.8, "▼ 0.8 pts vs previous 7 days", "down"),
                                               (-40.0, "▼ 40.0 pts vs previous 7 days", "down"), (0.0, "▬ no change vs previous 7 days", "flat"), (0.1, "▲ 0.1 pts vs previous 7 days", "up")])
def test_the_change_is_worded_with_an_arrow_the_size_in_points_and_what_it_is_compared_with(delta, text, kind):
    assert F.format_trend({**TREND_UP, "delta_pts": delta}) == (text, kind)


def test_there_is_nothing_to_show_when_there_is_no_trend():
    assert F.format_trend(None) is None and F.trend_help(None) == ""


def test_the_tooltip_gives_both_periods_and_warns_that_the_latest_days_are_still_filling_in():
    tip = F.trend_help(TREND_UP)
    assert "Last 7 days: 25.0% of 8 emails" in tip and "The 7 days before: 10.0% of 10 emails" in tip and "Replies can take several days to arrive" in tip and "may rise" in tip


def test_only_the_reply_rate_card_carries_the_change_and_its_tooltip():
    figures = {f["label"]: f for f in F.headline_figures(perf(reply_trend=TREND_UP))}
    assert figures["Reply rate"]["trend"] == ("▲ 15.0 pts vs previous 7 days", "up") and "Replies can take several days" in figures["Reply rate"]["help"]
    assert all(f["trend"] is None for label, f in figures.items() if label != "Reply rate")


def test_with_no_trend_the_reply_rate_card_is_exactly_as_it_was():
    for given in (perf(), perf(reply_trend=None)):
        card = next(f for f in F.headline_figures(given) if f["label"] == "Reply rate")
        assert card["trend"] is None and card["help"] == "" and card["caption"] == "2 replied"


def test_the_export_notes_the_change_beside_the_reply_rate_and_says_nothing_extra_when_there_is_none():
    with_trend = {r[0]: r for r in rows_of(F.build_performance_csv(perf(reply_trend=TREND_UP))) if r}
    assert with_trend["Reply rate"] == ["Reply rate", "66.7%", "2 replied · ▲ 15.0 pts vs previous 7 days"]
    without = {r[0]: r for r in rows_of(F.build_performance_csv(perf())) if r}
    assert without["Reply rate"] == ["Reply rate", "66.7%", "2 replied"]


def test_the_where_section_lists_every_place_by_its_plain_name_with_its_count_and_its_share_of_all_the_leads():
    outcomes = {label: 0 for label in OUTCOME_ORDER}
    outcomes.update({"Not contacted yet": 2, "In progress": 3, "Completed the sequence": 1, "Replied": 2})
    rows = rows_of(F.build_performance_csv({**perf(), "is_html": True, "outcomes": outcomes, "total_leads": 8, "contacted": 6}, "Camp"))
    start = rows.index(["Where the leads are now"])
    assert rows[start + 1] == ["Where", "Leads", "Share of leads"]
    table = rows[start + 2: start + 2 + len(OUTCOME_ORDER)]
    assert [r[0] for r in table] == ["Not contacted yet", "In progress", "Completed", "Replied", "Bounced", "Unsubscribed", "Declined", "Stopped by hand", "Paused", "Removed", "No email address"]
    assert table[:4] == [["Not contacted yet", "2", "25.0%"], ["In progress", "3", "37.5%"], ["Completed", "1", "12.5%"], ["Replied", "2", "25.0%"]] and table[4] == ["Bounced", "0", "0.0%"]


def test_the_where_section_shows_dashes_not_a_division_error_for_a_campaign_with_no_leads():
    rows = rows_of(F.build_performance_csv({**perf(), "is_html": True, "outcomes": {label: 0 for label in OUTCOME_ORDER}, "total_leads": 0}, "Camp"))
    start = rows.index(["Where the leads are now"])
    assert all(r[2] == "—" for r in rows[start + 2: start + 2 + len(OUTCOME_ORDER)])


def test_an_older_figures_dict_with_no_outcomes_still_exports_without_failing():
    rows = rows_of(F.build_performance_csv({**{k: v for k, v in perf().items() if k != "outcomes"}, "total_leads": 3}, "Camp"))
    assert ["Where the leads are now"] in rows



# ================================================================ the Unsubscribed figure (HTML campaigns only)

def html(**kw):
    return perf(is_html=True, unsubscribed=1, unsubscribe_rate=1 / 3, **kw)


def test_a_plain_text_campaign_still_has_exactly_ten_figures_and_none_is_about_unsubscribing():
    labels = [f["label"] for f in F.headline_figures(perf())]
    assert len(labels) == 10 and "Unsubscribed" not in labels and len(F.headline_figures(perf(unsubscribed=5, unsubscribe_rate=1.0))) == 10


def test_an_html_campaign_has_an_eleventh_figure_right_after_the_hard_bounce_rate():
    labels = [f["label"] for f in F.headline_figures(html())]
    assert len(labels) == 11 and labels[8:] == ["Hard bounce rate", "Unsubscribed", "Sequence completion"] and labels[:8] == [f["label"] for f in F.headline_figures(perf())][:8]


def test_the_unsubscribed_figure_is_the_count_with_its_share_of_contacted_and_says_what_it_means():
    figure = next(f for f in F.headline_figures(html()) if f["label"] == "Unsubscribed")
    assert figure["value"] == 1 and figure["caption"] == "33.3% of contacted" and "Unsubscribe link" in figure["help"] and "same brand" in figure["help"] and figure["trend"] is None


def test_a_missing_share_is_a_dash_and_a_missing_count_is_zero():
    figure = next(f for f in F.headline_figures(perf(is_html=True)) if f["label"] == "Unsubscribed")
    assert figure["value"] == 0 and figure["caption"] == "— of contacted"
    assert next(f for f in F.headline_figures(perf(is_html=True, unsubscribed=2, unsubscribe_rate=None)) if f["label"] == "Unsubscribed")["caption"] == "— of contacted"


def test_the_export_of_an_html_campaign_has_the_unsubscribed_figure_row_and_place():
    outcomes = {label: 0 for label in OUTCOME_ORDER}
    outcomes.update({"Unsubscribed": 2, "Replied": 1})
    rows = rows_of(F.build_performance_csv({**html(), "outcomes": outcomes, "total_leads": 4}, "Camp"))
    by_name = {r[0]: r for r in rows if r}
    assert by_name["Unsubscribed"] in (["Unsubscribed", "1", "33.3% of contacted"], ["Unsubscribed", "2", "50.0%"], ["Unsubscribed", "1"]) and sum(1 for r in rows if r and r[0] == "Unsubscribed") == 3
    assert ["Unsubscribed", "1", "33.3% of contacted"] in rows and ["Unsubscribed", "2", "50.0%"] in rows and ["Unsubscribed", "1"] in rows


def test_the_export_of_a_plain_text_campaign_has_no_unsubscribed_row_anywhere():
    outcomes = {label: 0 for label in OUTCOME_ORDER}
    rows = rows_of(F.build_performance_csv({**perf(), "outcomes": outcomes, "total_leads": 4}, "Camp"))
    assert not any("Unsubscribed" in cell for row in rows for cell in row)
    start = rows.index(["Where the leads are now"])
    assert len([r for r in rows[start + 2:] if r and r[0] in [*[name for name in ("Not contacted yet", "In progress", "Completed", "Replied", "Bounced", "Declined", "Stopped by hand", "Paused", "Removed", "No email address")]]]) == 10
