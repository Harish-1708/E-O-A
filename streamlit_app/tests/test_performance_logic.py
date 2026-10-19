"""Tests for the Performance page's logic (performance_logic.py)."""
import os
import random
import sys
from datetime import date, datetime, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import performance_logic as pl  # noqa: E402  (puts the repo root on sys.path, so `outreach` imports below)
import outreach  # noqa: E402

FMT = "%Y-%m-%d %H:%M:%S"
GENUINE = outreach.CLASSIFICATION_GENUINE


# ---------------------------------------------------------------- data helpers

def sent_field(i):
    return outreach.stage_field_names(i)["sent_at"]


def variant_field(i):
    return outreach.stage_field_names(i)["variant"]


def cfg(stages=3, name="Camp", status="active"):
    names = ["intro"] + [f"followup{i}" for i in range(1, stages)]
    return {"_campaign_name": name, "status": status,
            "stages": [{"name": n, "template_prefix": n, "wait_days_after_previous": 0 if i == 0 else 2}
                       for i, n in enumerate(names)]}


def lead(i, **fields):
    row = {"_row": i + 1, "LeadID": str(i), "Email": f"p{i}@x.com", "Status": "", "ReplyStatus": ""}
    row.update(fields)
    return row


def send(lead_id, stage, variant, ts, message_id="", status="sent", error=""):
    return {"BatchID": "B", "Timestamp": ts, "LeadID": str(lead_id), "Email": "", "Campaign": "Camp", "Stage": stage,
            "Variant": variant, "SenderAccount": "sales1", "Status": status, "MessageID": message_id, "Error": error}


def reply(lead_id, received_at, intent="", classification=GENUINE, in_reply_to="", rid=None):
    return {"ResponseID": rid or f"R{lead_id}-{received_at}", "LeadID": str(lead_id), "ReceivedAt": received_at,
            "Classification": classification, "Intent": intent, "InReplyTo": in_reply_to}


def perf(leads=(), responses=(), send_log=(), stages=3):
    return pl.build_campaign_performance(cfg(stages), list(leads), list(responses), list(send_log))


# ================================================================ helpers

def test_rate_is_none_not_zero_when_there_is_nothing_to_divide_by():
    assert pl.rate(0, 0) is None and pl.rate(5, 0) is None
    assert pl.rate(1, 4) == 0.25 and pl.rate(0, 4) == 0.0


@pytest.mark.parametrize("value, expected", [(None, "—"), (0.0, "0.0%"), (0.0333, "3.3%"), (1.0, "100.0%")])
def test_format_rate(value, expected):
    assert pl.format_rate(value) == expected


@pytest.mark.parametrize("hours, expected", [(None, "—"), (0.5, "30 min"), (5.4, "5.4 hours"), (47.9, "47.9 hours"), (72, "3.0 days")])
def test_format_duration_hours(hours, expected):
    assert pl.format_duration_hours(hours) == expected


# ================================================================ totals agree with the engine

@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_headline_totals_always_equal_the_engines_own_numbers(seed):
    """The Overview page and the dashboard tabs use outreach.compute_all_
    campaigns_row. This page must never show a different number for the same
    thing — checked on randomly generated, messy data."""
    rng = random.Random(seed)
    c = cfg(4)
    leads, responses, log = [], [], []
    for i in range(120):
        row = lead(i, Email=rng.choice(["", f"p{i}@x.com"]), ReplyStatus=rng.choice(["", "", "Replied"]))
        for k in range(rng.randint(0, 4)):
            row[sent_field(k)] = (datetime(2026, 9, 1) + timedelta(days=k, hours=i % 9)).strftime(FMT)
            log.append(send(i, c["stages"][k]["name"], rng.choice("ABCD"), row[sent_field(k)], f"<m{i}-{k}@x>",
                            status=rng.choice(["sent", "sent", "sent", "failed"])))
        leads.append(row)
        if rng.random() < 0.4:
            responses.append(reply(i, "2026-09-10 10:00:00", classification=rng.choice(
                [GENUINE, outreach.CLASSIFICATION_BOUNCE_HARD, outreach.CLASSIFICATION_BOUNCE_SOFT, outreach.CLASSIFICATION_OOO])))
    got = pl.build_campaign_performance(c, leads, responses, log)
    engine = outreach.compute_all_campaigns_row("Camp", leads, responses, log, c["stages"])
    assert (got["total_leads"], got["contacted"], got["sent"], got["delivered_est"], got["bounced_hard"],
            got["bounced_soft"], got["replies"]) == tuple(int(x) for x in engine[1:8])


# ================================================================ reply quality

def test_each_replying_creator_is_counted_once_by_their_latest_ai_reading():
    leads = [lead(1), lead(2)]
    responses = [reply(1, "2026-09-10 10:00:00", intent="Unclear"),
                 reply(1, "2026-09-12 10:00:00", intent="Interested"),       # changed their mind: the LATEST reading counts
                 reply(2, "2026-09-11 10:00:00", intent="Not Interested")]
    p = perf(leads, responses)
    assert p["intents"]["Interested"] == 1 and p["intents"]["Not Interested"] == 1 and p["intents"]["Unclear"] == 0
    assert p["responders"] == 2 and sum(p["intents"].values()) == 2


def test_a_reply_with_no_ai_reading_is_not_classified_never_silently_positive_or_negative():
    p = perf([lead(1), lead(2)], [reply(1, "2026-09-10 10:00:00", intent=""), reply(2, "2026-09-10 10:00:00", intent="Gibberish")])
    assert p["intents"][pl.INTENT_NOT_CLASSIFIED] == 2 and p["positive"] == 0


def test_bounces_out_of_office_and_auto_replies_are_not_replies_from_a_person():
    responses = [reply(1, "2026-09-10 10:00:00", intent="Interested", classification=c)
                 for c in (outreach.CLASSIFICATION_BOUNCE_HARD, outreach.CLASSIFICATION_BOUNCE_SOFT,
                           outreach.CLASSIFICATION_OOO, outreach.CLASSIFICATION_AUTOREPLY)]
    p = perf([lead(1)], responses)
    assert p["responders"] == 0 and sum(p["intents"].values()) == 0 and p["positive"] == 0


def test_positive_rate_is_interested_over_contacted_and_none_with_nobody_contacted():
    leads = [lead(i, **{sent_field(0): "2026-09-01 10:00:00"}) for i in range(1, 5)]
    p = perf(leads, [reply(1, "2026-09-10 10:00:00", intent="Interested")])
    assert (p["contacted"], p["positive"], p["positive_rate"]) == (4, 1, 0.25)
    assert perf([lead(1)])["positive_rate"] is None


def test_a_response_without_a_timestamp_still_counts_as_a_reply_but_not_in_time_measures():
    p = perf([lead(1, **{sent_field(0): "2026-09-01 10:00:00"})], [reply(1, "", intent="Interested")])
    assert p["intents"]["Interested"] == 1 and p["reply_hours"] == [] and p["replies_by_day"] == {}


# ================================================================ time to first reply

def test_time_to_first_reply_uses_the_earliest_genuine_reply_and_the_median():
    leads = [lead(1, **{sent_field(0): "2026-09-01 10:00:00"}), lead(2, **{sent_field(0): "2026-09-01 10:00:00"}),
             lead(3, **{sent_field(0): "2026-09-01 10:00:00"})]
    responses = [reply(1, "2026-09-01 14:00:00"), reply(1, "2026-09-09 10:00:00"),    # first reply 4h later
                 reply(2, "2026-09-02 10:00:00"),                                      # 24h
                 reply(3, "2026-09-05 10:00:00")]                                      # 96h
    p = perf(leads, responses)
    assert sorted(p["reply_hours"]) == [4.0, 24.0, 96.0] and p["median_reply_hours"] == 24.0


def test_a_reply_logged_before_the_intro_or_without_one_is_left_out_of_the_timing():
    leads = [lead(1, **{sent_field(0): "2026-09-05 10:00:00"}), lead(2)]
    p = perf(leads, [reply(1, "2026-09-01 10:00:00"), reply(2, "2026-09-02 10:00:00")])
    assert p["reply_hours"] == [] and p["median_reply_hours"] is None


# ================================================================ which email earned the reply

def test_a_reply_is_credited_to_the_exact_email_it_answered_even_when_timing_says_otherwise():
    """The reply answers follow-up 1 (its In-Reply-To names that email), but it
    was only LOGGED after follow-up 2 had gone out. Timing alone would credit
    follow-up 2; the message id says follow-up 1 — and must win."""
    l1 = lead(1, **{sent_field(0): "2026-09-01 10:00:00", variant_field(0): "A",
                    sent_field(1): "2026-09-03 10:00:00", variant_field(1): "B",
                    sent_field(2): "2026-09-05 10:00:00", variant_field(2): "C"})
    log = [send(1, "intro", "A", "2026-09-01 10:00:00", "<m0@x>"), send(1, "followup1", "B", "2026-09-03 10:00:00", "<m1@x>"),
           send(1, "followup2", "C", "2026-09-05 10:00:00", "<m2@x>")]
    p = perf([l1], [reply(1, "2026-09-06 10:00:00", intent="Interested", in_reply_to="<M1@x>")], log)   # angle brackets + case ignored
    by_stage = {r["stage"]: r for r in p["stages"]}
    assert by_stage["followup1"]["replies"] == 1 and by_stage["followup2"]["replies"] == 0
    assert {r["variant"]: r["replies"] for r in p["variants"]} == {"A": 0, "B": 1, "C": 0}
    assert p["exact_matches"] == 1


def test_when_the_reply_cannot_be_matched_it_is_credited_to_the_latest_email_sent_before_it_was_logged():
    l1 = lead(1, **{sent_field(0): "2026-09-01 10:00:00", variant_field(0): "A",
                    sent_field(1): "2026-09-03 10:00:00", variant_field(1): "B"})
    log = [send(1, "intro", "A", "2026-09-01 10:00:00"), send(1, "followup1", "B", "2026-09-03 10:00:00")]
    for in_reply_to in ("", "<unknown@x>"):
        p = perf([l1], [reply(1, "2026-09-04 10:00:00", in_reply_to=in_reply_to)], log)
        assert {r["stage"]: r["replies"] for r in p["stages"]} == {"intro": 0, "followup1": 1}
        assert p["exact_matches"] == 0


def test_a_reply_logged_before_any_email_was_sent_is_credited_to_nothing():
    l1 = lead(1, **{sent_field(0): "2026-09-05 10:00:00", variant_field(0): "A"})
    p = perf([l1], [reply(1, "2026-09-01 10:00:00")], [send(1, "intro", "A", "2026-09-05 10:00:00")])
    assert all(r["replies"] == 0 for r in p["stages"]) and all(r["replies"] == 0 for r in p["variants"])


def test_only_the_first_reply_is_credited_so_a_chatty_creator_does_not_inflate_a_stage():
    l1 = lead(1, **{sent_field(0): "2026-09-01 10:00:00", variant_field(0): "A"})
    log = [send(1, "intro", "A", "2026-09-01 10:00:00", "<m0@x>")]
    p = perf([l1], [reply(1, f"2026-09-0{d} 10:00:00", in_reply_to="<m0@x>") for d in (2, 3, 4)], log)
    assert {r["stage"]: r["replies"] for r in p["stages"]}["intro"] == 1


def test_a_positive_reply_is_credited_as_positive_to_its_stage_and_variant():
    l1 = lead(1, **{sent_field(0): "2026-09-01 10:00:00", variant_field(0): "A"})
    log = [send(1, "intro", "A", "2026-09-01 10:00:00", "<m0@x>")]
    p = perf([l1], [reply(1, "2026-09-02 10:00:00", intent="Interested", in_reply_to="<m0@x>")], log)
    assert p["stages"][0]["positive"] == 1 and p["stages"][0]["positive_rate"] == 1.0
    assert p["variants"][0]["positive"] == 1


def test_stage_and_variant_rows_use_the_send_log_and_skip_stages_with_no_activity():
    log = [send(1, "intro", "A", "2026-09-01 10:00:00"), send(2, "intro", "B", "2026-09-01 10:00:00"),
           send(3, "followup1", "A", "2026-09-03 10:00:00"), send(4, "intro", "A", "2026-09-01 10:00:00", status="failed")]
    p = perf([], [], log, stages=4)
    assert [(r["stage"], r["sent"]) for r in p["stages"]] == [("intro", 3 - 1), ("followup1", 1)]   # followup2/3 omitted; the failed one not counted
    assert {r["variant"]: r["sent"] for r in p["variants"]} == {"A": 2, "B": 1}


def test_a_stage_name_not_in_the_campaign_config_is_still_reported_after_the_known_ones():
    p = perf([], [], [send(1, "intro", "A", "2026-09-01 10:00:00"), send(2, "legacy_stage", "A", "2026-09-01 10:00:00")])
    assert [r["stage"] for r in p["stages"]] == ["intro", "legacy_stage"]


# ================================================================ sending problems, pipeline, series

def test_failed_sends_are_counted_with_their_most_common_errors():
    log = [send(i, "intro", "A", "2026-09-01 10:00:00", status="failed", error="SMTP 550 mailbox full") for i in range(3)]
    log += [send(9, "intro", "A", "2026-09-01 10:00:00", status="failed", error="timeout"),
            send(10, "intro", "A", "2026-09-01 10:00:00", status="failed", error=""),
            send(11, "intro", "A", "2026-09-01 10:00:00")]
    p = perf([], [], log)
    assert p["send_failures"] == 5 and p["sent"] == 1
    assert p["top_send_errors"][0] == ("SMTP 550 mailbox full", 3)
    assert ("(no detail)", 1) in p["top_send_errors"]


def test_negotiating_and_rights_secured_come_from_the_asana_sync_and_are_none_until_it_has_run():
    leads = [lead(1, LastSyncedAsanaStage="Negotiating"), lead(2, LastSyncedAsanaStage="Negotiating"),
             lead(3, LastSyncedAsanaStage="Rights Secured"), lead(4, LastSyncedAsanaStage="Sourced"), lead(5)]
    p = perf(leads)
    assert (p["negotiating"], p["rights_secured"]) == (2, 1)
    untracked = perf([lead(1), lead(2)])
    assert untracked["negotiating"] is None and untracked["rights_secured"] is None     # "—", not a false 0


def test_daily_series_fills_quiet_days_and_respects_the_window():
    p = perf([lead(1, **{sent_field(0): "2026-09-01 10:00:00"})], [reply(1, "2026-09-04 09:00:00")],
             [send(1, "intro", "A", "2026-09-01 10:00:00"), send(2, "intro", "A", "2026-09-01 15:00:00"),
              send(3, "intro", "A", "2026-09-03 10:00:00")])
    series = pl.daily_series(p, None, today=date(2026, 9, 5))
    assert [(d["date"], d["Sent"], d["Replies"]) for d in series] == [
        ("2026-09-01", 2, 0), ("2026-09-02", 0, 0), ("2026-09-03", 1, 0), ("2026-09-04", 0, 1), ("2026-09-05", 0, 0)]
    last3 = pl.daily_series(p, 3, today=date(2026, 9, 5))
    assert [d["date"] for d in last3] == ["2026-09-03", "2026-09-04", "2026-09-05"]
    assert pl.daily_series(perf(), 30, today=date(2026, 9, 5)) == []


def test_an_empty_campaign_does_not_crash_and_shows_no_data_not_zero_rates():
    p = perf()
    assert p["reply_rate"] is None and p["positive_rate"] is None and p["bounce_rate_hard"] is None
    assert p["median_reply_hours"] is None and p["stages"] == [] and p["variants"] == []


# ================================================================ combining campaigns

def _two_campaigns():
    big = perf([lead(i, **{sent_field(0): "2026-09-01 10:00:00"}) for i in range(1, 1001)],
               [reply(1, "2026-09-02 10:00:00", intent="Interested")] + [reply(i, "2026-09-02 10:00:00") for i in range(2, 11)],
               [send(i, "intro", "A", "2026-09-01 10:00:00") for i in range(1, 1001)])
    big["campaign"] = "Big"
    small = perf([lead(i, ReplyStatus="Replied", **{sent_field(0): "2026-09-01 10:00:00"}) for i in range(1, 11)],
                 [reply(i, "2026-09-01 12:00:00", intent="Not Interested") for i in range(1, 6)],
                 [send(i, "intro", "B", "2026-09-01 10:00:00") for i in range(1, 11)] +
                 [send(1, "followup1", "B", "2026-09-03 10:00:00")])
    small["campaign"] = "Small"
    return big, small


def test_combined_rates_are_recomputed_from_the_added_counts_never_averaged():
    """A 10-lead campaign must not count as much as a 1,000-lead one."""
    big, small = _two_campaigns()
    combined = pl.combine_performance([big, small])
    assert combined["contacted"] == 1010 and combined["sent"] == 1011
    assert combined["replies"] == big["replies"] + small["replies"]
    assert combined["reply_rate"] == combined["replies"] / 1010
    assert combined["reply_rate"] != (big["reply_rate"] + small["reply_rate"]) / 2
    assert combined["positive"] == 1 and combined["intents"]["Not Interested"] == 5


def test_combined_stage_and_variant_rows_add_up_in_canonical_stage_order():
    big, small = _two_campaigns()
    combined = pl.combine_performance([small, big])
    stages = {r["stage"]: r["sent"] for r in combined["stages"]}
    assert stages == {"intro": 1010, "followup1": 1} and [r["stage"] for r in combined["stages"]] == ["intro", "followup1"]
    assert {r["variant"]: r["sent"] for r in combined["variants"]} == {"A": 1000, "B": 11}


def test_combined_timing_pools_every_reply_before_taking_the_median():
    big, small = _two_campaigns()
    combined = pl.combine_performance([big, small])
    assert len(combined["reply_hours"]) == len(big["reply_hours"]) + len(small["reply_hours"])
    assert combined["median_reply_hours"] is not None


def test_combined_pipeline_is_none_only_if_no_campaign_has_tracked_stages():
    a = perf([lead(1, LastSyncedAsanaStage="Negotiating")])
    b = perf([lead(1)])
    assert pl.combine_performance([a, b])["negotiating"] == 1
    assert pl.combine_performance([b, b])["negotiating"] is None


def test_combining_nothing_is_safe():
    combined = pl.combine_performance([])
    assert combined["contacted"] == 0 and combined["reply_rate"] is None and combined["stages"] == []


# ================================================================ what worked best

def _row(label, sent, replies, positive, key="variant"):
    return {key: label, "sent": sent, "replies": replies, "positive": positive,
            "reply_rate": pl.rate(replies, sent), "positive_rate": pl.rate(positive, sent)}


def _combined(variants=(), stages=()):
    return {"variants": list(variants), "stages": list(stages)}


def _campaign(name, contacted, replies, positive):
    return {"campaign": name, "contacted": contacted, "reply_rate": pl.rate(replies, contacted),
            "positive_rate": pl.rate(positive, contacted)}


def test_nothing_is_ranked_when_no_group_has_enough_volume():
    best = pl.build_best_performers([_campaign("Tiny", 5, 3, 3)], _combined([_row("A", 10, 9, 9)]))
    assert best["campaign"] is None and best["variant"] is None and best["stage"] is None


def test_the_leader_is_the_highest_positive_rate_among_groups_with_enough_volume_only():
    variants = [_row("A", 100, 20, 10), _row("B", 100, 30, 5), _row("C", 10, 10, 10)]    # C looks perfect but has 10 sends
    best = pl.build_best_performers([_campaign("X", 100, 10, 4), _campaign("Y", 100, 30, 2)], _combined(variants))
    assert best["variant"]["label"] == "A" and best["variant"]["compared"] == 2
    assert best["campaign"]["label"] == "X"                                              # more POSITIVE, though Y got more replies


def test_a_tie_on_positive_rate_is_broken_by_reply_rate():
    variants = [_row("A", 100, 10, 5), _row("B", 100, 40, 5)]
    assert pl.build_best_performers([], _combined(variants))["variant"]["label"] == "B"


# ================================================================ loading every campaign

def _fetch_factory(fail=(), deleted=(), paused=()):
    def fetch(name):
        if name in fail:
            raise RuntimeError(f"cannot read {name}")
        c = cfg(name=name, status="deleted" if name in deleted else "paused" if name in paused else "active")
        return c, [lead(1, **{sent_field(0): "2026-09-01 10:00:00"})], [], [send(1, "intro", "A", "2026-09-01 10:00:00")]
    return fetch


def test_one_unreadable_campaign_does_not_block_the_others_and_order_is_kept():
    perfs, errors = pl.build_all_performance(["A", "Broken", "C"], _fetch_factory(fail={"Broken"}))
    assert [p["campaign"] for p in perfs] == ["A", "C"] and errors == [("Broken", "cannot read Broken")]


def test_deleted_campaigns_are_left_out_and_each_one_carries_the_status_the_campaigns_page_shows(monkeypatch):
    """The status comes from the SAME function the Campaigns page uses (it
    needs a fully configured campaign to say Running, so it is stubbed here
    to prove the delegation, not to re-test it)."""
    seen = []
    monkeypatch.setattr(pl, "compute_campaign_status",
                        lambda c, leads: (seen.append(c["_campaign_name"]) or f"status-of-{c['_campaign_name']}", ["a problem"]))
    perfs, _ = pl.build_all_performance(["A", "Gone", "P"], _fetch_factory(deleted={"Gone"}, paused={"P"}))
    assert [(p["campaign"], p["status"], p["status_problems"]) for p in perfs] == [
        ("A", "status-of-A", ["a problem"]), ("P", "status-of-P", ["a problem"])]
    assert seen == ["A", "P"]


def test_reading_campaigns_at_the_same_time_gives_the_same_answer_as_one_by_one():
    names = [f"C{i}" for i in range(6)]
    assert pl.build_all_performance(names, _fetch_factory(), max_workers=1) == pl.build_all_performance(names, _fetch_factory(), max_workers=4)


# ================================================================ what the old Analytics / Overview / Dashboard pages showed

def _random_campaign(seed, n=120, stages=4, with_accounts=True):
    rng = random.Random(seed)
    c = cfg(stages)
    leads, responses, log = [], [], []
    for i in range(n):
        row = lead(i, Email=rng.choice(["", f"p{i}@x.com"]),
                   Status=rng.choice(["", "Intro Sent", "Stopped - Replied", "Stopped - Bounced"]),
                   ReplyStatus=rng.choice(["", "", "Replied"]),
                   SenderAccount=rng.choice(["sales1", "sales2", ""]) if with_accounts else "")
        for k in range(rng.randint(0, stages)):
            row[sent_field(k)] = (datetime(2026, 9, 1) + timedelta(days=k, hours=i % 9)).strftime(FMT)
            row[variant_field(k)] = rng.choice("ABCD")
            log.append(send(i, c["stages"][k]["name"], row[variant_field(k)], row[sent_field(k)], f"<m{i}-{k}@x>",
                            status=rng.choice(["sent", "sent", "failed"])))
            log[-1]["SenderAccount"] = row["SenderAccount"]
        leads.append(row)
        if rng.random() < 0.4:
            # a mix, INCLUDING bounces — otherwise "delivered" would always equal "sent" and a test could not tell the columns apart
            responses.append(reply(i, "2026-09-10 10:00:00", classification=rng.choice(
                [GENUINE, GENUINE, outreach.CLASSIFICATION_BOUNCE_HARD, outreach.CLASSIFICATION_BOUNCE_SOFT])))
    return c, leads, responses, log


@pytest.mark.parametrize("seed", [11, 12, 13, 14])
def test_pending_and_sequence_completion_equal_the_engines_own_figures(seed):
    """The Overview page showed Pending and Sequence Completion. Both must
    agree with the engine's number for the same data."""
    c, leads, responses, log = _random_campaign(seed)
    got = pl.build_campaign_performance(c, leads, responses, log)
    engine = outreach.compute_all_campaigns_row("Camp", leads, responses, log, c["stages"])
    assert got["pending"] == max(int(engine[1]) - int(engine[2]), 0)
    assert pl.format_rate(got["sequence_completion"]) == (engine[9] if int(engine[2]) else "—")
    assert pl.format_rate(got["reply_rate"]) == (engine[8] if int(engine[2]) else "—")


@pytest.mark.parametrize("seed", [21, 22, 23])
def test_the_by_sender_table_equals_the_engines_own_sender_rows(seed):
    """The old Analytics tab's By sender account table — kept exactly."""
    from campaign_analytics_logic import build_sender_table
    c, leads, responses, log = _random_campaign(seed)
    got = pl.build_campaign_performance(c, leads, responses, log, [])
    engine = build_sender_table(outreach.compute_campaign_dashboard(c, leads, responses, log, []))
    assert [r["account"] for r in got["sender_rows"]] == [e["account"] for e in engine]
    for mine, theirs in zip(got["sender_rows"], engine):
        assert (mine["sent"], mine["replies"]) == (int(theirs["sent"]), int(theirs["replies"]))
        assert pl.format_rate(mine["reply_rate"]) == theirs["reply_rate"]


def test_all_time_errors_come_from_the_error_log_and_an_unreadable_log_is_flagged_not_fatal():
    error_log = [{"ErrorType": "SMTP", "Timestamp": "2026-09-01 10:00:00", "Detail": "x"}] * 3 + \
                [{"ErrorType": "IMAP", "Timestamp": "2026-09-01 10:00:00", "Detail": "y"}]
    read = perf_with_errors(error_log)
    assert read["error_log_read"] is True
    empty = perf_with_errors([])
    assert empty["error_log_read"] is True and empty["error_rows"] == []
    unreadable = perf_with_errors(None)
    assert unreadable["error_log_read"] is False and unreadable["error_rows"] == []          # flagged, report still built


def perf_with_errors(error_log):
    return pl.build_campaign_performance(cfg(), [lead(1)], [], [], error_log)


def test_error_counts_match_what_the_old_errors_table_showed():
    from campaign_analytics_logic import build_error_summary
    error_log = [{"ErrorType": "SMTP", "Detail": ""}] * 3 + [{"ErrorType": "IMAP", "Detail": ""}]
    c = cfg()
    engine = {r["error_type"]: int(r["count"]) for r in build_error_summary(
        outreach.compute_campaign_dashboard(c, [lead(1)], [], [], error_log))}
    mine = {r["error_type"]: r["count"] for r in pl.build_campaign_performance(c, [lead(1)], [], [], error_log)["error_rows"]}
    assert mine == engine and sum(mine.values()) == 4


def test_the_raw_dashboard_rows_are_kept_for_the_full_details_view_and_match_the_engine(monkeypatch):
    c, leads, responses, log = _random_campaign(31)
    got = pl.build_campaign_performance(c, leads, responses, log, [])
    engine = outreach.compute_campaign_dashboard(c, leads, responses, log, [])
    strip_time = lambda rows: [r for r in rows if r[1] != "Last Updated"]              # the only value that differs between two calls
    assert strip_time(got["dashboard_rows"]) == strip_time(engine)


def test_each_variant_is_reported_stage_by_stage_with_exact_credit():
    l1 = lead(1, **{sent_field(0): "2026-09-01 10:00:00", variant_field(0): "A", sent_field(1): "2026-09-03 10:00:00", variant_field(1): "A"})
    log = [send(1, "intro", "A", "2026-09-01 10:00:00", "<m0@x>"), send(1, "followup1", "A", "2026-09-03 10:00:00", "<m1@x>"),
           send(2, "intro", "B", "2026-09-01 10:00:00", "<m2@x>")]
    p = perf([l1, lead(2)], [reply(1, "2026-09-04 10:00:00", intent="Interested", in_reply_to="<m1@x>")], log)
    rows = {(r["stage"], r["variant"]): r for r in p["variant_stage_rows"]}
    assert set(rows) == {("intro", "A"), ("followup1", "A"), ("intro", "B")}
    assert rows[("followup1", "A")]["replies"] == 1 and rows[("followup1", "A")]["positive"] == 1
    assert rows[("intro", "A")]["replies"] == 0
    assert [(r["stage"], r["variant"]) for r in p["variant_stage_rows"]] == [("intro", "A"), ("intro", "B"), ("followup1", "A")]


def test_combined_new_figures_add_up_and_rates_are_recomputed():
    b_leads = [lead(i, SenderAccount="sales1", Status="Stopped - Replied", **{sent_field(0): "2026-09-01 10:00:00"}) for i in range(1, 3)]
    b = pl.build_campaign_performance(cfg(name="B"), b_leads, [], [send(i, "intro", "A", "2026-09-01 10:00:00") for i in range(1, 3)],
                                      [{"ErrorType": "SMTP"}])
    a = pl.build_campaign_performance(cfg(name="A"), [lead(i, SenderAccount="sales1", **{sent_field(0): "2026-09-01 10:00:00"}) for i in range(1, 11)],
                                      [], [send(i, "intro", "A", "2026-09-01 10:00:00") for i in range(1, 11)], [{"ErrorType": "SMTP"}, {"ErrorType": "IMAP"}])
    combined = pl.combine_performance([a, b])
    assert combined["pending"] == a["pending"] + b["pending"] and combined["completed"] == a["completed"] + b["completed"]
    sender = combined["sender_rows"][0]
    assert (sender["account"], sender["leads"], sender["replies"], sender["sent"]) == ("sales1", 12, 2, 12)
    assert sender["reply_rate"] == 2 / 12 and sender["reply_rate"] != (0 + 1.0) / 2          # never an average of rates
    assert {r["error_type"]: r["count"] for r in combined["error_rows"]} == {"SMTP": 2, "IMAP": 1}
    assert combined["dashboard_rows"] is None
    assert combined["error_log_read"] is True


def test_the_combined_error_log_is_flagged_unread_if_any_campaigns_log_was_unreadable():
    ok = pl.build_campaign_performance(cfg(name="A"), [lead(1)], [], [], [])
    broken = pl.build_campaign_performance(cfg(name="B"), [lead(1)], [], [], None)
    assert pl.combine_performance([ok, broken])["error_log_read"] is False


@pytest.mark.parametrize("seed", [41, 42])
def test_the_at_a_glance_table_carries_every_figure_the_old_overview_page_showed(seed):
    c, leads, responses, log = _random_campaign(seed)
    p = pl.build_campaign_performance(c, leads, responses, log, [])
    p["status"] = "running"
    (row,) = pl.at_a_glance_rows([p], status_labels=lambda s: s.title())
    engine = outreach.compute_all_campaigns_row("Camp", leads, responses, log, c["stages"])
    pending = max(int(engine[1]) - int(engine[2]), 0)
    assert pl.AT_A_GLANCE_COLUMNS == ["Campaign", "Status", "Total leads", "Contacted", "Pending", "Emails sent",
                                      "Delivered (est.)", "Hard bounces", "Soft bounces", "Replies", "Reply rate", "Sequence completion"]
    assert row[0] == "Camp" and row[1] == "Running"
    assert engine[3] != engine[4] and engine[5] != "0"                          # the data really has bounces: sent != delivered
    assert row[2:6] == [engine[1], engine[2], str(pending), engine[3]]            # leads, contacted, pending, sent
    assert row[6:10] == [engine[4], engine[5], engine[6], engine[7]]              # delivered est., hard, soft, replies
    assert row[10] == (engine[8] if int(engine[2]) else "—") and row[11] == (engine[9] if int(engine[2]) else "—")


def test_the_loader_passes_the_error_log_through_and_still_accepts_the_old_four_part_shape():
    def five(name):
        return cfg(name=name), [lead(1)], [], [], [{"ErrorType": "SMTP"}]
    perfs, _ = pl.build_all_performance(["A"], five)
    assert perfs[0]["error_log_read"] is True and perfs[0]["error_rows"] == [{"error_type": "SMTP", "count": 1}]
    perfs, _ = pl.build_all_performance(["A"], _fetch_factory())          # four parts: error log unknown, flagged
    assert perfs[0]["error_log_read"] is False


def test_a_brand_new_campaign_with_no_stages_yet_does_not_crash():
    """The campaign page now builds this for EVERY campaign it opens,
    including a draft that has no stages, leads or sends."""
    empty_cfg = {"_campaign_name": "NewOne", "status": "draft", "stages": []}
    p = pl.build_campaign_performance(empty_cfg, [], [], [], [])
    assert p["contacted"] == 0 and p["pending"] == 0 and p["sequence_completion"] is None
    assert p["stages"] == [] and p["variants"] == [] and p["sender_rows"] == [] and p["error_rows"] == []
    assert pl.combine_performance([p])["contacted"] == 0


def test_every_at_a_glance_column_shows_its_own_figure_each_number_distinct_so_no_swap_can_hide():
    """Deliberately handcrafted so that no two figures are equal: total leads
    7, contacted 5, pending 2, sent 11, delivered 8, hard bounces 3, soft 1,
    replies 4, reply rate 80%, sequence completion 40%."""
    c = cfg(2)                                                                    # intro + followup1
    leads = [lead(i, **{sent_field(0): "2026-09-01 10:00:00"}) for i in range(1, 6)]          # 5 contacted
    leads += [lead(6), lead(7)]                                                               # 2 not yet contacted
    for l in leads[:2]:
        l[sent_field(1)] = "2026-09-03 10:00:00"                                              # 2 reached the final stage
    for l in leads[:4]:
        l["ReplyStatus"] = "Replied"                                                          # 4 replies
    log = [send(1 + (k % 5), "intro", "A", "2026-09-01 10:00:00") for k in range(11)] + \
          [send(1, "intro", "A", "2026-09-01 10:00:00", status="failed"), send(2, "intro", "A", "2026-09-01 10:00:00", status="failed")]
    responses = [reply(i, "2026-09-02 10:00:00", classification=outreach.CLASSIFICATION_BOUNCE_HARD) for i in (1, 2, 3)] + \
                [reply(4, "2026-09-02 10:00:00", classification=outreach.CLASSIFICATION_BOUNCE_SOFT)]
    p = pl.build_campaign_performance(c, leads, responses, log, [])
    p["status"] = "running"
    (row,) = pl.at_a_glance_rows([p], status_labels=lambda s: s)
    assert dict(zip(pl.AT_A_GLANCE_COLUMNS, row)) == {
        "Campaign": "Camp", "Status": "running", "Total leads": "7", "Contacted": "5", "Pending": "2", "Emails sent": "11",
        "Delivered (est.)": "8", "Hard bounces": "3", "Soft bounces": "1", "Replies": "4", "Reply rate": "80.0%",
        "Sequence completion": "40.0%"}


# ================================================================ the 7-day reply-rate trend

TODAY = date(2026, 3, 18)            # last 7 days: Mar 12 to Mar 18 · the 7 before: Mar 5 to Mar 11


def _rows(count, day_text, first_lead, stage="intro"):
    return [send(first_lead + i, stage, "A", f"{day_text} 10:00:00") for i in range(count)]


def test_the_trend_compares_the_share_of_emails_answered_in_the_last_7_days_with_the_7_before():
    rows = _rows(8, "2026-03-16", 1) + _rows(10, "2026-03-09", 11)
    answered = {("1", "intro"), ("2", "intro"), ("11", "intro")}                 # 2 of the 8 recent emails, 1 of the 10 earlier ones
    trend = pl.reply_rate_trend(rows, answered, TODAY)
    assert trend == {"days": 7, "current_rate": 0.25, "previous_rate": 0.1, "delta_pts": 15.0, "sent_current": 8, "sent_previous": 10, "answered_current": 2, "answered_previous": 1}


@pytest.mark.parametrize("stamp, period", [
    ("2026-03-18 23:59:59", "current"), ("2026-03-12 00:00:00", "current"), ("2026-03-11 23:59:59", "previous"), ("2026-03-05 00:00:00", "previous"),
    ("2026-03-04 23:59:59", None), ("2026-03-19 00:00:00", None),
])
def test_each_day_belongs_to_exactly_one_period_or_none(stamp, period):
    """One email in the period under test, five anchors in each period to clear the minimum: it moves the counts only if it is in that period."""
    anchors = _rows(5, "2026-03-14", 100) + _rows(5, "2026-03-07", 200)
    trend = pl.reply_rate_trend(anchors + [send(1, "intro", "A", stamp)], set(), TODAY)
    assert (trend["sent_current"], trend["sent_previous"]) == {"current": (6, 5), "previous": (5, 6), None: (5, 5)}[period]


@pytest.mark.parametrize("recent, earlier, shown", [(5, 5, True), (4, 5, False), (5, 4, False), (0, 9, False), (9, 0, False), (0, 0, False), (50, 50, True)])
def test_the_trend_needs_at_least_five_emails_in_both_weeks_and_is_hidden_otherwise(recent, earlier, shown):
    rows = _rows(recent, "2026-03-16", 1) + _rows(earlier, "2026-03-09", 100)
    assert (pl.reply_rate_trend(rows, set(), TODAY) is not None) is shown


def test_the_minimum_can_be_changed_and_a_log_with_no_sends_gives_no_trend():
    rows = _rows(2, "2026-03-16", 1) + _rows(2, "2026-03-09", 100)
    assert pl.reply_rate_trend(rows, set(), TODAY) is None and pl.reply_rate_trend(rows, set(), TODAY, min_emails=2) is not None
    assert pl.reply_rate_trend([], set(), TODAY) is None


def test_an_email_counts_as_answered_only_when_the_reply_was_credited_to_that_very_email():
    rows = _rows(5, "2026-03-16", 1) + _rows(5, "2026-03-09", 100)
    credited_elsewhere = {("1", "followup1"), ("2", "intro" + "x"), ("999", "intro")}          # another stage, another name, another lead
    assert pl.reply_rate_trend(rows, credited_elsewhere, TODAY)["answered_current"] == 0


def test_rounding_direction_and_no_change():
    up = pl.reply_rate_trend(_rows(3, "2026-03-16", 1) + _rows(4, "2026-03-16", 50) + _rows(4, "2026-03-09", 100), {("1", "intro"), ("50", "intro")}, TODAY, min_emails=3)
    assert up["current_rate"] == 2 / 7 and up["previous_rate"] == 0.0
    rows = _rows(3, "2026-03-16", 1) + _rows(4, "2026-03-09", 100)
    third_vs_quarter = pl.reply_rate_trend(rows, {("1", "intro"), ("100", "intro")}, TODAY, min_emails=3)
    assert third_vs_quarter["delta_pts"] == 8.3                                              # 33.33… − 25.00 = 8.33…, shown to one decimal
    down = pl.reply_rate_trend(_rows(5, "2026-03-16", 1) + _rows(5, "2026-03-09", 100), {("1", "intro"), ("100", "intro"), ("101", "intro"), ("102", "intro")}, TODAY)
    assert down["delta_pts"] == -40.0 and down["current_rate"] == 0.2 and down["previous_rate"] == 0.6          # 1 of 5 now against 3 of 5 before: 20% − 60%
    flat = pl.reply_rate_trend(_rows(5, "2026-03-16", 1) + _rows(5, "2026-03-09", 100), {("1", "intro"), ("100", "intro")}, TODAY)
    assert flat["delta_pts"] == 0.0


def test_failed_sends_and_rows_with_bad_dates_are_not_emails_sent():
    good = _rows(5, "2026-03-16", 1) + _rows(5, "2026-03-09", 100)
    log = good + [send(1, "intro", "A", "2026-03-16 10:00:00", status="failed"), send(2, "intro", "A", "not a date"), send(3, "intro", "A", "")]
    p = pl.build_campaign_performance(cfg(), [lead(i) for i in range(1, 4)], [], log, [], today=TODAY)
    assert p["reply_trend"]["sent_current"] == 5 and p["reply_trend"]["sent_previous"] == 5


def test_a_campaign_report_carries_the_trend_and_it_follows_the_same_credit_rules_as_the_rest():
    """Recent: 6 emails, leads 1-2 answered (lead 1 by exact message id, lead 2 by timing). Earlier: 6 emails, lead 11 answered. 33.3% vs 16.7%."""
    log = [send(i, "intro", "A", "2026-03-16 10:00:00", f"<m{i}@x>") for i in range(1, 7)] + [send(i, "intro", "B", "2026-03-09 10:00:00", f"<m{i}@x>") for i in range(11, 17)]
    leads = [lead(i, **{sent_field(0): "2026-03-16 10:00:00" if i < 10 else "2026-03-09 10:00:00"}) for i in list(range(1, 7)) + list(range(11, 17))]
    responses = [reply(1, "2026-03-17 09:00:00", in_reply_to="<m1@x>"), reply(2, "2026-03-18 09:00:00"), reply(11, "2026-03-10 09:00:00", in_reply_to="<m11@x>")]
    trend = pl.build_campaign_performance(cfg(), leads, responses, log, [], today=TODAY)["reply_trend"]
    assert (trend["sent_current"], trend["answered_current"], trend["sent_previous"], trend["answered_previous"]) == (6, 2, 6, 1) and trend["delta_pts"] == 16.7


def test_the_trend_never_appears_in_the_all_campaigns_total():
    log = _rows(6, "2026-03-16", 1) + _rows(6, "2026-03-09", 100)
    one = pl.build_campaign_performance(cfg(), [lead(1)], [], log, [], today=TODAY)
    assert one["reply_trend"] is not None and pl.combine_performance([one, one])["reply_trend"] is None


# ================================================================ the "All campaigns at a glance" table: order, best rate, totals, CSV

def _glance_perf(name, contacted, sent, replies, leads=None, hard=0, soft=0, completed=0, status="completed"):
    leads = contacted if leads is None else leads
    return {"campaign": name, "status": status, "total_leads": leads, "contacted": contacted, "pending": max(leads - contacted, 0), "sent": sent, "delivered_est": max(sent - hard, 0),
            "bounced_hard": hard, "bounced_soft": soft, "replies": replies, "reply_rate": pl.rate(replies, contacted), "completed": completed,
            "sequence_completion": pl.rate(completed, contacted)}


GLANCE = [_glance_perf("Kelson_17SEP", 77, 262, 51, completed=8, status="running"), _glance_perf("Kelson_03SEP", 39, 235, 20, soft=2, completed=15), _glance_perf("Kelson_Old", 10, 28, 3, completed=7)]


def test_the_table_is_ordered_best_reply_rate_first_by_default_and_by_the_chosen_measure_otherwise():
    names = lambda sort: [r["name"] for r in pl.glance_rows(GLANCE, sort=sort)]                    # noqa: E731
    assert names("Reply rate") == ["Kelson_17SEP", "Kelson_03SEP", "Kelson_Old"]                    # 66.2%, 51.3%, 30.0%
    assert names("Replies") == ["Kelson_17SEP", "Kelson_03SEP", "Kelson_Old"] and names("Emails sent") == ["Kelson_17SEP", "Kelson_03SEP", "Kelson_Old"]
    assert names("Name") == ["Kelson_03SEP", "Kelson_17SEP", "Kelson_Old"]
    assert names("nonsense") == names("Reply rate")                                                  # an unknown choice falls back to the reply rate


def test_each_sort_really_uses_its_own_measure():
    perfs = [_glance_perf("A", 10, 500, 1), _glance_perf("B", 10, 20, 9), _glance_perf("C", 100, 100, 30)]
    order = lambda sort: [p["campaign"] for p in pl.sort_perfs(perfs, sort)]                       # noqa: E731
    assert order("Reply rate") == ["B", "C", "A"] and order("Replies") == ["C", "B", "A"] and order("Emails sent") == ["A", "C", "B"] and order("Name") == ["A", "B", "C"]


def test_a_campaign_with_no_reply_rate_yet_goes_last_and_ties_are_broken_by_name():
    perfs = [_glance_perf("Zed", 5, 5, 1), {**_glance_perf("Empty", 0, 0, 0), "reply_rate": None}, _glance_perf("Amy", 5, 5, 1)]
    assert [p["campaign"] for p in pl.sort_perfs(perfs, "Reply rate")] == ["Amy", "Zed", "Empty"]


def test_the_best_reply_rate_is_marked_only_when_one_campaign_is_clearly_ahead():
    assert [r["best"] for r in pl.glance_rows(GLANCE)] == [True, False, False]
    assert not any(r["best"] for r in pl.glance_rows([GLANCE[0]]))                                  # a star on the only campaign would mean nothing
    tie = [_glance_perf("A", 10, 10, 5), _glance_perf("B", 20, 20, 10)]
    assert not any(r["best"] for r in pl.glance_rows(tie))                                         # 50% each: no winner
    assert [r["best"] for r in pl.glance_rows([GLANCE[0], {**_glance_perf("New", 0, 0, 0), "reply_rate": None}])] == [False, False]   # only one campaign has a rate


def test_a_row_carries_every_figure_the_table_shows_and_chips_use_the_campaign_lists_words():
    row = pl.glance_rows(GLANCE)[0]
    assert (row["leads"], row["contacted"], row["pending"], row["sent"], row["delivered"], row["hard"], row["soft"], row["replies"], row["reply_rate_text"], row["completion_text"], row["completed"]) == \
        (77, 77, 0, 262, 262, 0, 0, 51, "66.2%", "10.4%", 8)
    assert [r["status_label"] for r in pl.glance_rows(GLANCE)][:2] == ["● Running", "✓ Completed"]
    assert pl.glance_rows([{**GLANCE[0], "status": "mystery"}], status_labels=lambda s: s.upper())[0]["status_label"] == "MYSTERY"


def _real_campaign(name, contacted, replied, uncontacted=0, sends_each=1):
    leads = [lead(i, ReplyStatus="Replied" if i <= replied else "", **{sent_field(0): "2026-09-01 10:00:00"}) for i in range(1, contacted + 1)]
    leads += [lead(contacted + j, **{}) for j in range(1, uncontacted + 1)]
    sends = [send(i, "intro", "A", "2026-09-01 10:00:00") for i in range(1, contacted + 1) for _ in range(sends_each)]
    campaign = perf(leads, [], sends)
    campaign["campaign"] = name
    return campaign


def test_the_total_row_agrees_with_the_rows_the_same_way_the_campaigns_total_does():
    """Real campaigns, not stand-in numbers: the total adds up the rows with the same rules, so replied-of-contacted can never disagree with the cards."""
    perfs = [_real_campaign("A", 10, 6, sends_each=3), _real_campaign("B", 20, 4), _real_campaign("C", 3, 0, uncontacted=2)]
    totals = pl.glance_totals(pl.combine_performance(perfs), len(perfs))
    rows = pl.glance_rows(perfs)
    assert totals["campaigns"] == 3 and totals["leads"] == sum(r["leads"] for r in rows) == 35 and totals["contacted"] == sum(r["contacted"] for r in rows) == 33
    assert totals["sent"] == sum(r["sent"] for r in rows) == 53 and totals["replies"] == sum(r["replies"] for r in rows) == 10 and totals["pending"] == 2
    assert totals["reply_rate_text"] == "30.3%"                                                      # 10 replied of 33 contacted, NOT 10 of 53 emails (18.9%)
    assert pl.glance_totals(pl.combine_performance([perfs[0]]), 1)["reply_rate_text"] == pl.glance_rows([perfs[0]])[0]["reply_rate_text"] == "60.0%"       # one campaign: total == its own



def test_the_csv_keeps_every_column_of_the_old_table_in_the_order_shown():
    import csv
    import io
    rows = list(csv.reader(io.StringIO(pl.glance_csv(GLANCE, lambda s: s.title(), "Name").decode("utf-8"))))
    assert rows[0] == pl.AT_A_GLANCE_COLUMNS and [r[0] for r in rows[1:]] == ["Kelson_03SEP", "Kelson_17SEP", "Kelson_Old"]          # sorted like the screen
    assert dict(zip(rows[0], rows[2]))["Replies"] == "51" and dict(zip(rows[0], rows[2]))["Reply rate"] == "66.2%" and dict(zip(rows[0], rows[2]))["Status"] == "Running"


def test_no_campaigns_gives_no_rows_and_a_csv_with_only_the_headings():
    import csv
    import io
    assert pl.glance_rows([]) == [] and list(csv.reader(io.StringIO(pl.glance_csv([]).decode("utf-8")))) == [pl.AT_A_GLANCE_COLUMNS]


def test_a_campaign_with_a_real_zero_percent_rate_comes_before_one_with_no_rate_at_all():
    """0% is a result; 'no rate yet' is not. They must not tie (the second one would otherwise sort ahead by its name)."""
    perfs = [{**_glance_perf("A_Nothing_yet", 0, 0, 0), "reply_rate": None}, _glance_perf("Z_Zero_percent", 5, 5, 0)]
    assert [p["campaign"] for p in pl.sort_perfs(perfs, "Reply rate")] == ["Z_Zero_percent", "A_Nothing_yet"]
    assert [r["name"] for r in pl.glance_rows(perfs)] == ["Z_Zero_percent", "A_Nothing_yet"]


def test_delivered_is_emails_sent_less_hard_bounces_and_is_not_the_same_figure_as_sent():
    row = pl.glance_rows([_glance_perf("Bouncy", 10, 20, 2, hard=3, soft=1)])[0]
    assert (row["sent"], row["delivered"], row["hard"], row["soft"]) == (20, 17, 3, 1)


def test_a_row_carries_the_positive_replies_and_the_reasons_a_campaign_needs_attention():
    row = pl.glance_rows([{**GLANCE[0], "positive": 17, "status_problems": ["No leads with an email address", 5]}])[0]
    assert row["positive"] == 17 and row["problems"] == ["No leads with an email address", "5"]
    plain = pl.glance_rows([GLANCE[0]])[0]
    assert plain["positive"] == 0 and plain["problems"] == []
    assert pl.glance_rows([{**GLANCE[0], "positive": None, "status_problems": None}])[0]["positive"] == 0 and pl.glance_rows([{**GLANCE[0], "status_problems": None}])[0]["problems"] == []


def test_the_total_row_carries_the_positive_replies_too_and_has_no_problems_list():
    totals = pl.glance_totals({"positive": 5, "replies": 9}, 2)
    assert totals["positive"] == 5 and "problems" not in totals
