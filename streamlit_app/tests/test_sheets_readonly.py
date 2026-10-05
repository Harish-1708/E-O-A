import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gspread
import pytest
from sheets_readonly import ReadOnlySheetsConnector, ReadOnlySheetsError


class FakeWorksheet:
    def __init__(self, records, header=None):
        self._records = records
        self._header = header or (list(records[0].keys()) if records else [])

    def get_all_records(self):
        return [dict(r) for r in self._records]

    def row_values(self, row_number):
        if row_number == 1:
            return list(self._header)
        raise NotImplementedError("Fake only supports reading the header row (row 1)")


class FakeSpreadsheet:
    def __init__(self, worksheets):
        self._worksheets = worksheets  # {title: FakeWorksheet}

    def worksheet(self, title):
        if title not in self._worksheets:
            raise gspread.exceptions.WorksheetNotFound(title)
        return self._worksheets[title]


def test_get_all_leads_adds_row_numbers_starting_at_2():
    ws = FakeWorksheet([{"Email": "a@abc.com"}, {"Email": "b@abc.com"}])
    connector = ReadOnlySheetsConnector(_spreadsheet=FakeSpreadsheet({"Master": ws}))

    leads = connector.get_all_leads("Master")
    assert leads[0]["_row"] == 2
    assert leads[1]["_row"] == 3
    assert leads[0]["Email"] == "a@abc.com"


def test_get_all_responses_passthrough():
    ws = FakeWorksheet([{"MessageID": "<m1>"}])
    connector = ReadOnlySheetsConnector(_spreadsheet=FakeSpreadsheet({"Responses": ws}))
    assert connector.get_all_responses("Responses") == [{"MessageID": "<m1>"}]


def test_get_all_send_log_passthrough():
    ws = FakeWorksheet([{"Status": "sent"}])
    connector = ReadOnlySheetsConnector(_spreadsheet=FakeSpreadsheet({"SendLog": ws}))
    assert connector.get_all_send_log("SendLog") == [{"Status": "sent"}]


def test_get_all_error_log_passthrough():
    ws = FakeWorksheet([{"ErrorType": "Send Failure"}])
    connector = ReadOnlySheetsConnector(_spreadsheet=FakeSpreadsheet({"ErrorLog": ws}))
    assert connector.get_all_error_log("ErrorLog") == [{"ErrorType": "Send Failure"}]


def test_missing_tab_raises_readonly_sheets_error_not_gspread_exception():
    connector = ReadOnlySheetsConnector(_spreadsheet=FakeSpreadsheet({}))
    with pytest.raises(ReadOnlySheetsError, match="doesn't exist yet"):
        connector.get_all_leads("Nonexistent Tab")


def test_connector_requires_service_account_info_or_spreadsheet():
    with pytest.raises(ReadOnlySheetsError):
        ReadOnlySheetsConnector()


def test_connector_has_no_write_methods():
    # Explicit guard against accidental future write-method additions —
    # this connector must remain read-only by construction.
    write_like = {"update_lead_fields", "append_response", "append_send_log",
                  "append_error_log", "clear", "update", "batch_update",
                  "append_lead", "update_lead_statuses"}
    connector_methods = {m for m in dir(ReadOnlySheetsConnector) if not m.startswith("_")}
    assert connector_methods.isdisjoint(write_like)


def test_get_header_returns_header_row_explicitly():
    ws = FakeWorksheet([], header=["LeadID", "FirstName", "Email", "Title", "Website"])
    connector = ReadOnlySheetsConnector(_spreadsheet=FakeSpreadsheet({"Master": ws}))
    assert connector.get_header("Master") == ["LeadID", "FirstName", "Email", "Title", "Website"]


def test_get_header_works_even_with_zero_data_rows():
    # The exact case get_all_records() alone can't handle — a brand new
    # sheet with a header but no leads yet would lose custom-column
    # visibility if we only ever inferred columns from record dict keys.
    ws = FakeWorksheet([], header=["LeadID", "Email", "CustomField"])
    connector = ReadOnlySheetsConnector(_spreadsheet=FakeSpreadsheet({"Master": ws}))
    assert "CustomField" in connector.get_header("Master")


def test_get_header_missing_tab_raises_readonly_sheets_error():
    connector = ReadOnlySheetsConnector(_spreadsheet=FakeSpreadsheet({}))
    with pytest.raises(ReadOnlySheetsError, match="doesn't exist yet"):
        connector.get_header("Nonexistent Tab")


def test_get_account_health_returns_records_when_tab_exists():
    ws = FakeWorksheet([{"AccountName": "sales1", "Address": "sales1@x.com", "Status": "Connected",
                          "Detail": "", "CheckedAt": "2026-08-29 09:00:00"}])
    connector = ReadOnlySheetsConnector(_spreadsheet=FakeSpreadsheet({"Email Accounts Health": ws}))
    records = connector.get_account_health()
    assert len(records) == 1
    assert records[0]["Status"] == "Connected"


def test_get_account_health_returns_empty_list_when_tab_missing():
    """Unlike every other tab, this one has no 'first run creates it'
    trigger from Streamlit's side — a fresh deployment before the
    periodic health-check workflow has ever run shouldn't show an error."""
    connector = ReadOnlySheetsConnector(_spreadsheet=FakeSpreadsheet({}))
    assert connector.get_account_health() == []


def test_get_account_health_respects_custom_tab_name():
    ws = FakeWorksheet([{"AccountName": "sales1"}])
    connector = ReadOnlySheetsConnector(_spreadsheet=FakeSpreadsheet({"Custom Tab Name": ws}))
    records = connector.get_account_health(tab_name="Custom Tab Name")
    assert len(records) == 1


# ---------- worksheet lookup caching (a lookup used to cost a full extra Google request per read) ----------

class CountingSpreadsheet(FakeSpreadsheet):
    """Counts worksheet() calls — in real gspread each one is its own
    full API request, made before any data is read."""

    def __init__(self, worksheets):
        super().__init__(worksheets)
        self.lookup_calls = []

    def worksheet(self, title):
        self.lookup_calls.append(title)
        return super().worksheet(title)


def _api_error(status):
    from unittest.mock import MagicMock
    resp = MagicMock()
    resp.status_code = status
    resp.text = "boom"
    resp.json.return_value = {"error": {"code": status, "message": "boom", "status": "X"}}
    return gspread.exceptions.APIError(resp)


def test_repeated_reads_of_the_same_tab_look_it_up_only_once():
    """The actual reported slowness: every read re-fetched the whole
    spreadsheet's metadata first, doubling every page's Google requests."""
    ss = CountingSpreadsheet({"Master": FakeWorksheet([{"Email": "a@abc.com"}])})
    connector = ReadOnlySheetsConnector(_spreadsheet=ss)
    for _ in range(5):
        connector.get_all_leads("Master")
    assert ss.lookup_calls == ["Master"]


def test_different_read_methods_on_the_same_tab_share_one_lookup():
    ss = CountingSpreadsheet({"Master": FakeWorksheet([{"Email": "a@abc.com"}])})
    connector = ReadOnlySheetsConnector(_spreadsheet=ss)
    connector.get_all_leads("Master")
    connector.get_header("Master")
    assert ss.lookup_calls == ["Master"]


def test_each_distinct_tab_is_looked_up_once_each():
    ss = CountingSpreadsheet({"A": FakeWorksheet([{"x": 1}]), "B": FakeWorksheet([{"x": 2}])})
    connector = ReadOnlySheetsConnector(_spreadsheet=ss)
    connector.get_all_responses("A"); connector.get_all_responses("B")
    connector.get_all_responses("A"); connector.get_all_responses("B")
    assert sorted(ss.lookup_calls) == ["A", "B"]


def test_cached_reads_still_return_fresh_data_every_time():
    """Only the LOOKUP is cached — never the data. A row added to the
    tab between two reads must show up in the second one."""
    ws = FakeWorksheet([{"Email": "a@abc.com"}])
    connector = ReadOnlySheetsConnector(_spreadsheet=CountingSpreadsheet({"Master": ws}))
    assert len(connector.get_all_leads("Master")) == 1
    ws._records.append({"Email": "b@abc.com"})
    assert len(connector.get_all_leads("Master")) == 2


def test_a_missing_tab_still_raises_the_same_friendly_error_and_is_never_cached():
    ss = CountingSpreadsheet({})
    connector = ReadOnlySheetsConnector(_spreadsheet=ss)
    with pytest.raises(ReadOnlySheetsError, match="doesn't exist yet"):
        connector.get_all_leads("Master")
    # The tab gets created later (first Send/Preview run) — the very next
    # read must find it, not keep reporting "doesn't exist" from a cache.
    ss._worksheets["Master"] = FakeWorksheet([{"Email": "a@abc.com"}])
    assert connector.get_all_leads("Master")[0]["Email"] == "a@abc.com"


def test_a_tab_deleted_after_being_cached_gives_the_same_friendly_error_not_a_raw_api_error():
    class DeletedTabWorksheet(FakeWorksheet):
        def get_all_records(self):
            raise _api_error(400)  # Google's answer for a range on a tab that no longer exists
    ss = CountingSpreadsheet({"Master": FakeWorksheet([{"Email": "a@abc.com"}])})
    connector = ReadOnlySheetsConnector(_spreadsheet=ss)
    connector.get_all_leads("Master")                       # populates the cache
    ss._worksheets["Master"] = DeletedTabWorksheet([])      # same cached object now reads as deleted...
    connector._worksheet_cache["Master"] = ss._worksheets["Master"]
    del ss._worksheets["Master"]                            # ...and a fresh lookup finds nothing
    with pytest.raises(ReadOnlySheetsError, match="doesn't exist yet"):
        connector.get_all_leads("Master")
    assert "Master" not in connector._worksheet_cache


def test_a_tab_replaced_after_being_cached_is_picked_up_by_one_retry():
    class StaleWorksheet(FakeWorksheet):
        def get_all_records(self):
            raise _api_error(400)
    ss = CountingSpreadsheet({"Master": FakeWorksheet([{"Email": "old@abc.com"}])})
    connector = ReadOnlySheetsConnector(_spreadsheet=ss)
    connector.get_all_leads("Master")
    connector._worksheet_cache["Master"] = StaleWorksheet([])   # the cached object has gone stale
    ss._worksheets["Master"] = FakeWorksheet([{"Email": "new@abc.com"}])
    assert connector.get_all_leads("Master")[0]["Email"] == "new@abc.com"
    assert ss.lookup_calls == ["Master", "Master"]  # exactly one refresh


def test_a_quota_error_is_not_retried_and_propagates_untouched():
    """A 429 means "too many requests" — an immediate retry would only
    add load. It must surface exactly as it always did."""
    class QuotaWorksheet(FakeWorksheet):
        def get_all_records(self):
            raise _api_error(429)
    ss = CountingSpreadsheet({"Master": QuotaWorksheet([])})
    connector = ReadOnlySheetsConnector(_spreadsheet=ss)
    with pytest.raises(gspread.exceptions.APIError):
        connector.get_all_leads("Master")
    assert ss.lookup_calls == ["Master"]  # no refresh attempted


def test_concurrent_reads_through_one_connector_are_safe():
    import threading
    ss = CountingSpreadsheet({"Master": FakeWorksheet([{"Email": "a@abc.com"}])})
    connector = ReadOnlySheetsConnector(_spreadsheet=ss)
    results, errors = [], []
    def read():
        try:
            results.append(connector.get_all_leads("Master")[0]["Email"])
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
    threads = [threading.Thread(target=read) for _ in range(12)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert errors == [] and results == ["a@abc.com"] * 12
    assert len(ss.lookup_calls) <= 12  # racing first lookups are fine; it never grows per read afterward
