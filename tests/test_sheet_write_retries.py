"""A quota error (429) on a write must be waited out and retried — never turned into a different, permanent failure.

REPORTED: the repair hit Google's write quota, and the retry then failed with
"Unable to parse range: 'Tab'!'Tab'!R112". gspread's batch_update REWRITES the ranges inside the list it is
handed, so re-sending that same list put the tab's name in twice."""
import os
import sys

import gspread
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import outreach  # noqa: E402

TITLE = "Kelson_Creators_Licensing_17SEP Response Sheet"


def _error(status):
    class _Response:
        status_code = status

        def json(self):
            return {"error": {"code": status, "message": "test error"}}
    return gspread.exceptions.APIError(_Response())


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    monkeypatch.setattr(outreach.time, "sleep", lambda seconds: None)


# ---------------------------------------------------------------- the REAL gspread worksheet

class FakeGoogle(gspread.http_client.HTTPClient):
    """Stands in for Google: refuses the first N requests with the given status, records what it is sent.
    (A real HTTPClient subclass, because gspread's Worksheet refuses any other kind of client.)"""
    def __init__(self, fail_first=1, status=429):          # deliberately not calling the real __init__: no login
        self.fail_first, self.status, self.bodies = fail_first, status, []

    def values_batch_update(self, spreadsheet_id, body=None):
        self.bodies.append(body)
        if len(self.bodies) <= self.fail_first:
            raise _error(self.status)
        # what the real API does with a doubled name:
        for entry in body["data"]:
            assert entry["range"].count("!") == 1, f"Unable to parse range: {entry['range']}"
        return {"totalUpdatedCells": len(body["data"])}


def _real_worksheet(google):
    return gspread.Worksheet(spreadsheet=None, properties={"title": TITLE, "sheetId": 1, "index": 0}, spreadsheet_id="sid", client=google)


@pytest.mark.parametrize("status", [429, 503])
def test_a_refused_write_is_retried_with_the_ranges_intact_on_the_real_gspread_worksheet(status):
    google = FakeGoogle(fail_first=1, status=status)
    ws = outreach._RetryingWorksheet(_real_worksheet(google))
    ws.batch_update([{"range": "R112", "values": [["2026-09-04 17:00:00"]]}, {"range": "R113", "values": [["x"]]}], value_input_option="RAW")
    assert len(google.bodies) == 2                                           # refused once, then accepted
    assert [e["range"] for e in google.bodies[-1]["data"]] == [f"'{TITLE}'!R112", f"'{TITLE}'!R113"]   # the tab's name ONCE


def test_several_refusals_in_a_row_still_end_with_clean_ranges():
    google = FakeGoogle(fail_first=3, status=429)
    ws = outreach._RetryingWorksheet(_real_worksheet(google))
    ws.batch_update([{"range": "R5", "values": [["x"]]}], value_input_option="RAW")
    assert len(google.bodies) == 4 and google.bodies[-1]["data"][0]["range"] == f"'{TITLE}'!R5"


def test_the_callers_own_list_is_never_rewritten():
    google = FakeGoogle(fail_first=1)
    ws = outreach._RetryingWorksheet(_real_worksheet(google))
    mine = [{"range": "R112", "values": [["x"]]}]
    ws.batch_update(mine, value_input_option="RAW")
    assert mine == [{"range": "R112", "values": [["x"]]}]


# ---------------------------------------------------------------- the same thing against a worksheet that rewrites exactly as gspread 6.1.2 does

class RewritingWorksheet:
    """gspread 6.1.2's Worksheet.batch_update, reproduced: it prefixes the tab name onto each range IN PLACE
    before sending, so an unprotected retry sees ranges that are already prefixed. (Reproduced here so this test
    fails on any gspread version, not only the one whose code does this.)"""
    title = TITLE

    def __init__(self, fail_first=1):
        self.calls, self.fail_first = 0, fail_first

    def batch_update(self, data, **kwargs):
        self.calls += 1
        for values in data:
            values["range"] = gspread.utils.absolute_range_name(self.title, values["range"])
        if self.calls <= self.fail_first:
            raise _error(429)
        assert all(v["range"].count("!") == 1 for v in data), [v["range"] for v in data]
        self.sent = [v["range"] for v in data]
        return {}


def test_a_quota_error_on_a_worksheet_that_rewrites_its_input_is_waited_out_not_made_permanent():
    ws = outreach._RetryingWorksheet(RewritingWorksheet(fail_first=1))
    ws.batch_update([{"range": "R112", "values": [["x"]]}], value_input_option="RAW")
    assert ws._real.sent == [f"'{TITLE}'!R112"]


def test_without_the_protection_the_failure_is_exactly_the_reported_one():
    """Proof that the scenario above really is the reported bug: calling the same worksheet through the plain
    retry function, which re-sends the same list, reproduces the doubled name."""
    raw = RewritingWorksheet(fail_first=1)
    with pytest.raises(AssertionError, match=r"'.*'!'.*'!R112"):
        outreach._call_with_gspread_retries(raw.batch_update, [{"range": "R112", "values": [["x"]]}], value_input_option="RAW")


def test_every_other_wrapped_method_also_gets_fresh_arguments_on_each_attempt():
    class Appender:
        def __init__(self):
            self.seen = []

        def append_rows(self, values, **kwargs):
            self.seen.append([list(row) for row in values])
            values[0].append("MUTATED")                  # a method that scribbles on its input
            if len(self.seen) == 1:
                raise _error(503)

    real = Appender()
    outreach._RetryingWorksheet(real).append_rows([["a", "b"]], value_input_option="RAW")
    assert real.seen == [[["a", "b"]], [["a", "b"]]]


def test_an_argument_that_cannot_be_copied_is_still_passed_through():
    class Uncopyable:
        def __deepcopy__(self, memo):
            raise TypeError("cannot copy")

    class Worksheet:
        def touch(self, thing):
            return thing

    thing = Uncopyable()
    assert outreach._RetryingWorksheet(Worksheet()).touch(thing) is thing


def test_a_real_error_that_is_not_retryable_still_surfaces_immediately():
    class Worksheet:
        calls = 0

        def batch_update(self, data, **kwargs):
            Worksheet.calls += 1
            raise _error(400)

    with pytest.raises(gspread.exceptions.APIError):
        outreach._RetryingWorksheet(Worksheet()).batch_update([{"range": "A1", "values": [["x"]]}])
    assert Worksheet.calls == 1


# ---------------------------------------------------------------- many rows, ONE request

class FakeResponsesWs:
    def __init__(self):
        self.batches, self.header_reads = [], 0

    def row_values(self, n):
        self.header_reads += 1
        return list(outreach.RESPONSES_COLUMNS)

    def batch_update(self, updates, **kwargs):
        self.batches.append((updates, kwargs))


def _connector():
    c = outreach.SheetsConnector.__new__(outreach.SheetsConnector)
    c.responses_ws, c._gspread = FakeResponsesWs(), gspread
    return c


def test_177_rows_are_written_in_one_request_not_177():
    c = _connector()
    rows = [{"row": r, "full_body": None, "snippet": None, "message_date": f"2026-09-04 17:{r % 60:02d}:00"} for r in range(2, 179)]
    assert c.update_response_rows(rows) == 177
    assert len(c.responses_ws.batches) == 1 and len(c.responses_ws.batches[0][0]) == 177     # ONE Google write
    assert c.responses_ws.header_reads == 1 and c.responses_ws.batches[0][1] == {"value_input_option": "RAW"}


def test_each_cell_lands_in_its_own_rows_message_date_column():
    c = _connector()
    c.update_response_rows([{"row": 7, "full_body": None, "snippet": None, "message_date": "D7"},
                            {"row": 112, "full_body": None, "snippet": None, "message_date": "D112"}])
    column = outreach.RESPONSES_COLUMNS.index("MessageDate") + 1
    assert {u["range"]: u["values"][0][0] for u in c.responses_ws.batches[0][0]} == {
        gspread.utils.rowcol_to_a1(7, column): "D7", gspread.utils.rowcol_to_a1(112, column): "D112"}


def test_text_intent_and_date_for_a_row_all_go_in_the_same_request():
    c = _connector()
    c.update_response_rows([{"row": 5, "full_body": "words", "snippet": "w", "intent_fields": {"Intent": "Interested"}, "message_date": "D"}])
    columns = {outreach.RESPONSES_COLUMNS[gspread.utils.a1_to_rowcol(u["range"])[1] - 1] for u in c.responses_ws.batches[0][0]}
    assert columns == {"FullBody", "Snippet", "Intent", "MessageDate"}


def test_nothing_to_write_means_no_request_at_all():
    c = _connector()
    assert c.update_response_rows([]) == 0
    assert c.update_response_rows([{"row": 3, "full_body": None, "snippet": None, "message_date": ""}]) == 0
    assert c.responses_ws.batches == []


def test_the_single_row_method_still_works_through_the_bulk_one():
    c = _connector()
    c.update_response_body(9, "text", "snip", {"Intent": "Interested"}, message_date="D")
    assert len(c.responses_ws.batches) == 1 and len(c.responses_ws.batches[0][0]) == 4


# ---------------------------------------------------------------- splitting into requests

def _u(row, text=""):
    return {"row": row, "full_body": text or None, "snippet": text[:500] or None, "message_date": "D"}


def test_updates_are_split_into_requests_of_at_most_100_rows_keeping_their_order():
    chunks = outreach._chunk_updates([_u(r) for r in range(2, 179)])
    assert [len(c) for c in chunks] == [100, 77]
    assert [u["row"] for c in chunks for u in c] == list(range(2, 179))


@pytest.mark.parametrize("count, expected", [(0, []), (1, [1]), (99, [99]), (100, [100]), (101, [100, 1]), (200, [100, 100]), (201, [100, 100, 1])])
def test_chunk_sizes_at_the_boundaries(count, expected):
    assert [len(c) for c in outreach._chunk_updates([_u(r) for r in range(count)])] == expected


def test_long_text_makes_requests_smaller_so_one_never_gets_too_big():
    chunks = outreach._chunk_updates([_u(r, "x" * 49_000) for r in range(80)])
    assert len(chunks) > 1
    for chunk in chunks:
        assert sum(len(u["full_body"]) for u in chunk) <= outreach.BACKFILL_WRITE_CHUNK_CHARS


def test_one_enormous_row_still_goes_out_on_its_own_rather_than_never():
    chunks = outreach._chunk_updates([_u(1), _u(2, "x" * 3_000_000), _u(3)])
    assert [[u["row"] for u in c] for c in chunks] == [[1], [2], [3]]


# ---------------------------------------------------------------- the reported run, end to end, and the writes that existed before it

def test_the_reported_run_177_rows_with_google_refusing_the_first_requests_ends_with_every_row_written():
    google = FakeGoogle(fail_first=2, status=429)
    real = _real_worksheet(google)
    real.row_values = lambda n: list(outreach.RESPONSES_COLUMNS)           # the header read (not what is under test)
    connector = outreach.SheetsConnector.__new__(outreach.SheetsConnector)
    connector.responses_ws, connector._gspread = outreach._RetryingWorksheet(real), gspread
    updates = [{"row": row, "full_body": None, "snippet": None, "message_date": "2026-09-04 17:00:00"} for row in range(2, 179)]
    assert connector.update_response_rows(updates) == 177
    assert len(google.bodies) == 3                                         # refused, refused, accepted
    sent = google.bodies[-1]["data"]
    assert len(sent) == 177 and all(entry["range"].startswith(f"'{TITLE}'!R") and entry["range"].count("!") == 1 for entry in sent)


class HeaderWorksheet(RewritingWorksheet):
    def row_values(self, n):
        return list(outreach.RESPONSES_COLUMNS)


def test_marking_replies_read_also_survives_a_quota_error_now():
    """This write existed long before the repair and had the same latent problem."""
    connector = outreach.SheetsConnector.__new__(outreach.SheetsConnector)
    real = HeaderWorksheet(fail_first=1)
    connector.responses_ws, connector._gspread = outreach._RetryingWorksheet(real), gspread
    assert connector.mark_responses_read({"<a@x>": 5, "<b@x>": 9}) == 2
    assert len(real.sent) == 2 and all(r.count("!") == 1 for r in real.sent)


def test_updating_how_a_reply_was_matched_also_survives_a_quota_error_now():
    connector = outreach.SheetsConnector.__new__(outreach.SheetsConnector)
    real = HeaderWorksheet(fail_first=1)
    connector.responses_ws, connector._gspread = outreach._RetryingWorksheet(real), gspread
    connector.update_response_match(7, "Header", "Stopped Sequence")
    assert len(real.sent) == 2 and all(r.count("!") == 1 for r in real.sent)
