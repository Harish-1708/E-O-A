"""response_status.py: lead statuses written into the Response Sheet's intent columns."""
import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import response_status as RS  # noqa: E402

NOW = "2026-10-13 09:00:00"
MANUAL, KEYWORDS = "Manual", "Keywords"


def row(n, lead="1", words="", intent="", confidence="", cls="Genuine Reply", snippet=""):
    return {"_row": n, "LeadID": lead, "Classification": cls, "Intent": intent, "IntentConfidence": confidence,
            "FullBody": words, "Snippet": snippet}


def fields(update):
    return update["intent_fields"]


# ================================================================ the payload

@pytest.mark.parametrize("payload, expected", [
    ({"campaign": "C", "lead_id": "7", "status": "interested"}, ("7", "interested")),
    ({"campaign": "C", "lead_id": 7, "status": "auto"}, ("7", "auto")),
    ({"campaign": "C", "lead_id": " 7 ", "status": "not_interested"}, ("7", "not_interested")),
])
def test_a_good_payload_gives_the_lead_and_status(payload, expected):
    assert RS.validate_payload(payload, "C") == expected


@pytest.mark.parametrize("payload", [
    None, [], "text", {}, {"campaign": "C", "status": "lead"}, {"campaign": "C", "lead_id": "", "status": "lead"},
    {"campaign": "C", "lead_id": "_no_lead_x", "status": "lead"}, {"campaign": "C", "lead_id": "1", "status": "maybe"},
    {"campaign": "C", "lead_id": "1", "status": ""}, {"campaign": "Other", "lead_id": "1", "status": "lead"},
    {"lead_id": "1", "status": "lead"},
])
def test_a_bad_payload_is_refused(payload):
    with pytest.raises(ValueError):
        RS.validate_payload(payload, "C")


# ================================================================ a status chosen by hand

def test_a_manual_status_goes_on_every_reply_row_of_that_lead_and_no_one_elses():
    rows = [row(2, "1"), row(3, "1", cls="Auto-Reply"), row(4, "2"), row(5, "1", intent="Interested", confidence="High")]
    updates = RS.plan_manual_status(rows, "1", "not_interested", NOW)
    assert [u["row"] for u in updates] == [2, 3, 5]                                  # the auto-reply row too; lead 2 untouched
    assert all(fields(u) == {"Intent": "Not Interested", "IntentConfidence": MANUAL, "IntentClassifiedAt": NOW} for u in updates)


@pytest.mark.parametrize("status, intent", [("lead", "Lead / Needs Follow-up"), ("interested", "Interested"),
                                            ("not_interested", "Not Interested"), ("unclear", "Unclear")])
def test_each_status_is_stored_as_the_intent_the_rest_of_the_system_understands(status, intent):
    (u,) = RS.plan_manual_status([row(2)], "1", status, NOW)
    assert fields(u)["Intent"] == intent


def test_a_lead_id_from_the_sheet_matches_whether_it_is_a_number_or_text():
    assert [u["row"] for u in RS.plan_manual_status([{**row(2), "LeadID": 1}, row(3, "1")], "1", "lead", NOW)] == [2, 3]


def test_automatic_takes_only_the_manual_statuses_off_and_leaves_the_ais_reading_alone():
    rows = [row(2, "1", intent="Interested", confidence=MANUAL), row(3, "1", intent="Not Interested", confidence="High"),
            row(4, "1", intent="Interested", confidence=KEYWORDS), row(5, "2", intent="Interested", confidence=MANUAL)]
    updates = RS.plan_manual_status(rows, "1", "auto", NOW)
    assert [u["row"] for u in updates] == [2] and fields(updates[0]) == {"Intent": "", "IntentConfidence": "", "IntentClassifiedAt": ""}


def test_a_lead_with_no_replies_changes_nothing():
    assert RS.plan_manual_status([row(2, "2")], "1", "lead", NOW) == []


def test_only_the_intent_columns_are_ever_written():
    (u,) = RS.plan_manual_status([row(2, words="private words")], "1", "lead", NOW)
    assert u["full_body"] is None and u["snippet"] is None and u["message_date"] is None and set(fields(u)) == {"Intent", "IntentConfidence", "IntentClassifiedAt"}


# ================================================================ filling in what the AI left unclear

def test_a_clear_yes_or_no_is_written_for_replies_with_no_definite_reading():
    rows = [row(2, words="No thank you, not interested"), row(3, words="Yes! I'd love to do this", intent="Unclear", confidence="Low"),
            row(4, words="Sounds great", intent="", confidence="")]
    updates = RS.plan_keyword_fill(rows, NOW)
    assert [(u["row"], fields(u)["Intent"]) for u in updates] == [(2, "Not Interested"), (3, "Interested"), (4, "Interested")]
    assert all(fields(u)["IntentConfidence"] == KEYWORDS and fields(u)["IntentClassifiedAt"] == NOW for u in updates)


def test_a_definite_ai_reading_is_never_replaced():
    assert RS.plan_keyword_fill([row(2, words="No thanks", intent="Interested", confidence="High")], NOW) == []


def test_a_manual_status_is_never_replaced_even_if_it_says_unclear():
    rows = [row(2, words="No thanks", intent="Unclear", confidence=MANUAL), row(3, words="No thanks", intent="", confidence=MANUAL)]
    assert RS.plan_keyword_fill(rows, NOW) == []


def test_a_question_or_a_mention_of_money_is_only_a_hint_and_is_not_written():
    rows = [row(2, words="What are your rates?"), row(3, words="Would you do $400?"), row(4, words="Let me check with my manager")]
    assert RS.plan_keyword_fill(rows, NOW) == []


@pytest.mark.parametrize("cls", ["Auto-Reply", "Out of Office", "Bounce (Hard)", "Bounce (Soft)", ""])
def test_an_auto_reply_or_a_bounce_is_never_read_as_the_persons_answer(cls):
    assert RS.plan_keyword_fill([row(2, words="I am not interested in any emails", cls=cls)], NOW) == []


def test_only_what_the_person_wrote_is_read_not_the_history_quoted_below_it():
    rows = [row(2, words="Thanks!\n\n> Are you interested? Say no thank you if not")]
    assert RS.plan_keyword_fill(rows, NOW) == []


def test_the_short_preview_is_read_when_the_full_text_is_blank():
    (u,) = RS.plan_keyword_fill([row(2, words="  ", snippet="Not interested, thanks")], NOW)
    assert fields(u)["Intent"] == "Not Interested"


def test_running_it_twice_changes_nothing_the_second_time():
    rows = [row(2, words="Not interested"), row(3, words="Sounds good"), row(4, words="Thanks!")]
    first = RS.plan_keyword_fill(rows, NOW)
    for u in first:                                                          # apply what was written
        rows[u["row"] - 2].update(fields(u))
    assert len(first) == 2 and RS.plan_keyword_fill(rows, NOW) == []


def test_replies_with_nothing_to_read_are_left_alone():
    assert RS.plan_keyword_fill([row(2), row(3, words="Thanks!"), row(4, words=None)], NOW) == []


# ================================================================ writing

class Sheets:
    def __init__(self, rows=(), fail_on=None):
        self.rows, self.requests, self.fail_on = list(rows), [], fail_on

    def get_all_responses(self):
        return [dict(r) for r in self.rows]

    def update_response_rows(self, updates):
        if self.fail_on and len(self.requests) + 1 == self.fail_on:
            raise RuntimeError("quota")
        self.requests.append(list(updates))
        return len(updates)


def test_writes_go_out_in_requests_of_a_hundred_rows_with_a_pause_between_and_none_after_the_last():
    pauses, sheets = [], Sheets()
    assert RS.write_updates(sheets, [{"row": i} for i in range(250)], sleep=pauses.append) == 250
    assert [len(r) for r in sheets.requests] == [100, 100, 50] and pauses == [RS.PAUSE_SECONDS] * 2
    assert RS.write_updates(Sheets(), [], sleep=pauses.append) == 0


# ================================================================ the commands (with a stand-in engine)

@pytest.fixture
def engine(monkeypatch):
    fake = types.SimpleNamespace(sheets={}, names=["Camp_A", "Camp_B"])
    fake.get_campaign = lambda name: name
    fake._connect_sheets = lambda name: fake.sheets[name] if not isinstance(fake.sheets[name], Exception) else (_ for _ in ()).throw(fake.sheets[name])
    fake.discover_campaign_names = lambda: fake.names
    monkeypatch.setitem(sys.modules, "outreach", fake)
    monkeypatch.setattr(RS.time, "sleep", lambda s: None)
    return fake


def _payload(tmp_path, monkeypatch, body, name="x.json"):
    monkeypatch.chdir(tmp_path)
    folder = tmp_path / "status_changes" / "Camp_A"
    folder.mkdir(parents=True)
    (folder / name).write_text(body if isinstance(body, str) else json.dumps(body), encoding="utf-8")
    return f"status_changes/Camp_A/{name}"


def _args(**kw):
    return types.SimpleNamespace(**{"campaign": "Camp_A", "file": "", "all": False, "dry_run": False, **kw})


def test_set_applies_the_status_and_prints_only_the_lead_and_a_count(engine, tmp_path, monkeypatch, capsys):
    engine.sheets["Camp_A"] = Sheets([row(2, "7", words="private words"), row(3, "7"), row(4, "8")])
    path = _payload(tmp_path, monkeypatch, {"campaign": "Camp_A", "lead_id": "7", "status": "interested", "set_by": "someone@private.example"})
    assert RS.cmd_set(_args(file=path)) == 0
    assert [u["row"] for u in engine.sheets["Camp_A"].requests[0]] == [2, 3]
    out = capsys.readouterr().out
    assert out.strip() == "Set lead 7 to 'interested' on 2 reply row(s)."
    for private in ("private words", "someone@private.example"):
        assert private not in out


def test_set_says_so_when_the_lead_has_no_replies(engine, tmp_path, monkeypatch, capsys):
    engine.sheets["Camp_A"] = Sheets([row(2, "8")])
    assert RS.cmd_set(_args(file=_payload(tmp_path, monkeypatch, {"campaign": "Camp_A", "lead_id": "7", "status": "lead"}))) == 0
    assert "nothing to change" in capsys.readouterr().out and engine.sheets["Camp_A"].requests == []


@pytest.mark.parametrize("path", ["config/settings.yaml", "../secret.json", "status_changes/../config/settings.yaml", "/etc/passwd", "replies/x.json", ""])
def test_set_refuses_to_read_a_file_outside_the_status_folder(engine, tmp_path, monkeypatch, capsys, path):
    monkeypatch.chdir(tmp_path)
    assert RS.cmd_set(_args(file=path)) == 1 and "Refusing" in capsys.readouterr().out


@pytest.mark.parametrize("body, message", [
    ("not json", "not valid"), ('{"campaign": "Camp_A", "lead_id": "7", "status": "maybe"}', "unknown status"),
    ('{"campaign": "Other", "lead_id": "7", "status": "lead"}', "different campaign"), ('[1, 2]', "not a JSON object"),
])
def test_set_refuses_a_bad_payload_and_never_touches_the_sheet(engine, tmp_path, monkeypatch, capsys, body, message):
    engine.sheets["Camp_A"] = Sheets([row(2, "7")])
    assert RS.cmd_set(_args(file=_payload(tmp_path, monkeypatch, body))) == 1
    assert message in capsys.readouterr().out and engine.sheets["Camp_A"].requests == []


def test_set_refuses_a_missing_file(engine, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert RS.cmd_set(_args(file="status_changes/Camp_A/nope.json")) == 1 and "missing or too large" in capsys.readouterr().out


def test_set_refuses_an_oversized_file_even_if_it_would_otherwise_be_valid(engine, tmp_path, monkeypatch, capsys):
    engine.sheets["Camp_A"] = Sheets([row(2, "7")])
    padded = json.dumps({"campaign": "Camp_A", "lead_id": "7", "status": "lead", "padding": "x" * RS.MAX_PAYLOAD_BYTES})   # valid JSON, valid fields, too big
    path = _payload(tmp_path, monkeypatch, padded, name="big.json")
    assert RS.cmd_set(_args(file=path)) == 1 and "missing or too large" in capsys.readouterr().out
    assert engine.sheets["Camp_A"].requests == []


def test_fill_writes_for_every_campaign_with_all_and_prints_counts_only(engine, capsys):
    engine.sheets["Camp_A"] = Sheets([row(2, words="Not interested, thanks"), row(3, words="Thanks!")])
    engine.sheets["Camp_B"] = Sheets([row(2, words="Sounds great")])
    assert RS.cmd_fill(_args(all=True)) == 0
    out = capsys.readouterr().out
    assert "Camp_A: labelled 1 reply" in out and "Camp_B: labelled 1 reply" in out
    assert "Not interested" not in out and "Sounds great" not in out and "Thanks" not in out


def test_a_dry_run_writes_nothing_and_says_so(engine, capsys):
    engine.sheets["Camp_A"] = Sheets([row(2, words="No thanks")])
    assert RS.cmd_fill(_args(campaign="Camp_A", dry_run=True)) == 0
    assert engine.sheets["Camp_A"].requests == [] and "would label 1 reply" in capsys.readouterr().out


def test_one_campaign_failing_does_not_stop_the_others_but_the_run_is_marked_failed(engine, capsys):
    engine.sheets["Camp_A"] = RuntimeError("boom")
    engine.sheets["Camp_B"] = Sheets([row(2, words="No thanks")])
    assert RS.cmd_fill(_args(all=True)) == 1
    out = capsys.readouterr().out
    assert "Camp_A: could not be processed (RuntimeError)" in out and "Camp_B: labelled 1 reply" in out and len(engine.sheets["Camp_B"].requests) == 1


def test_a_write_failure_inside_a_campaign_is_reported_not_raised(engine, capsys):
    engine.sheets["Camp_A"] = Sheets([row(2, words="No thanks")], fail_on=1)
    assert RS.cmd_fill(_args(campaign="Camp_A")) == 1 and "could not be processed" in capsys.readouterr().out


# ================================================================ the command line

def test_the_command_line_needs_its_arguments_and_offers_the_right_commands(engine, tmp_path, monkeypatch, capsys):
    for bad in ([], ["set"], ["set", "--campaign", "C"], ["fill"], ["fill", "--all", "--campaign", "C"], ["nonsense"]):
        with pytest.raises(SystemExit):
            RS.main(bad)
    engine.sheets["Camp_A"] = Sheets([row(2, words="No thanks")])
    assert RS.main(["fill", "--campaign", "Camp_A", "--dry-run"]) == 0


def test_the_status_choices_the_inbox_offers_are_exactly_the_ones_this_script_accepts():
    import inbox_keywords as K
    assert {key for key, _ in K.STATUS_CHOICES} == RS.VALID_STATUSES
    assert RS.VALID_STATUSES - {"auto"} == set(K.STATUS_TO_INTENT) and set(K.INTENT_TO_STATUS) == set(K.STATUS_TO_INTENT.values())
