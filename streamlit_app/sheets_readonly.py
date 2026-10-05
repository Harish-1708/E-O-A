"""Read-only counterpart to outreach.SheetsConnector.

Deliberately a SEPARATE, minimal class rather than reusing SheetsConnector:
- It authenticates with a Viewer-scoped service account, not the
  Editor-scoped one GitHub Actions uses to actually send email. If this
  dashboard's credential ever leaked, it could not write or delete anything.
- It never creates a worksheet (SheetsConnector._get_or_create_ws does, on
  purpose, for outreach.py's own runs — that's a write operation this
  module should never perform).

Everything else (column names, record shapes) intentionally matches
outreach.py's own get_all_leads/get_all_responses/etc. exactly, so the
dashboard math functions imported from outreach.py work unmodified.
"""
import threading
from typing import Callable, Dict, List

import gspread
from google.oauth2.service_account import Credentials

SCOPES_READONLY = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


class ReadOnlySheetsError(Exception):
    pass


class ReadOnlySheetsConnector:
    def __init__(self, service_account_info: dict = None, sheet_id: str = None,
                 _spreadsheet=None):
        """Pass _spreadsheet directly (a gspread-like Spreadsheet object) in
        tests to skip real Google auth entirely."""
        # Worksheet objects by tab title, so each tab is looked up at most
        # once instead of before every single read — see _ws.
        self._worksheet_cache: Dict[str, object] = {}
        self._worksheet_cache_lock = threading.Lock()
        if _spreadsheet is not None:
            self._spreadsheet = _spreadsheet
            return
        if not service_account_info or not sheet_id:
            raise ReadOnlySheetsError(
                "ReadOnlySheetsConnector needs service_account_info and sheet_id."
            )
        creds = Credentials.from_service_account_info(service_account_info, scopes=SCOPES_READONLY)
        client = gspread.authorize(creds)
        self._spreadsheet = client.open_by_key(sheet_id)

    def _ws(self, title: str, refresh: bool = False):
        """The worksheet for `title`, looked up from Google at most once
        and then reused.

        The real reason this exists: in the gspread version this app
        runs, Spreadsheet.worksheet(title) silently makes its OWN full
        API request (fetching the whole spreadsheet's metadata) every
        single time it's called, before any data is read — so every tab
        read cost two Google requests, all running one after another,
        and a page loading several campaigns made dozens. A Worksheet
        object read this way is safe to keep: reads go through one
        values request keyed by the tab's TITLE and never consult any
        cached sheet properties, so there's no stale state in it that
        could produce wrong data.

        refresh=True forces a fresh lookup — used by _read after a read
        fails, so a tab that was deleted or renamed behaves exactly as
        it always did (the same "doesn't exist yet" error) instead of
        failing on a stale cached object."""
        if not refresh:
            with self._worksheet_cache_lock:
                cached = self._worksheet_cache.get(title)
            if cached is not None:
                return cached
        try:
            ws = self._spreadsheet.worksheet(title)
        except gspread.exceptions.WorksheetNotFound:
            with self._worksheet_cache_lock:
                self._worksheet_cache.pop(title, None)
            raise ReadOnlySheetsError(
                f"Tab '{title}' doesn't exist yet. It's created automatically the first "
                "time Preview, Send, or Check Replies actually runs for this campaign — "
                "run one of those first."
            )
        with self._worksheet_cache_lock:
            self._worksheet_cache[title] = ws
        return ws

    def _read(self, title: str, reader: Callable[[object], object]):
        """reader(worksheet), retried exactly once with a freshly looked-up
        worksheet if the first attempt fails because the tab no longer
        exists as cached (Google answers 400/404 for a range on a tab
        that was deleted or renamed). Any other failure — notably a 429
        quota error, where an immediate retry would only add load —
        propagates untouched, as it always did."""
        try:
            return reader(self._ws(title))
        except gspread.exceptions.APIError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status not in (400, 404):
                raise
        return reader(self._ws(title, refresh=True))

    def get_all_leads(self, master_tab: str) -> List[Dict]:
        records = self._read(master_tab, lambda ws: ws.get_all_records())
        leads = []
        for i, record in enumerate(records, start=2):  # row 1 is header
            record["_row"] = i
            leads.append(record)
        return leads

    def get_all_responses(self, responses_tab: str) -> List[Dict]:
        return self._read(responses_tab, lambda ws: ws.get_all_records())

    def get_all_send_log(self, send_log_tab: str) -> List[Dict]:
        return self._read(send_log_tab, lambda ws: ws.get_all_records())

    def get_all_error_log(self, error_log_tab: str) -> List[Dict]:
        return self._read(error_log_tab, lambda ws: ws.get_all_records())

    def get_header(self, tab_name: str) -> List[str]:
        """The tab's actual header row — used to discover custom trailing
        columns (Title, Website, LinkedIn, ...) that exist in the real
        Sheet but aren't part of outreach.MASTER_COLUMNS, so the Data
        tab's column-mapping UI can offer them as valid targets without
        guessing."""
        return self._read(tab_name, lambda ws: ws.row_values(1))

    def get_account_health(self, tab_name: str = "Email Accounts Health") -> List[Dict]:
        """The shared (not per-campaign) account connectivity snapshot
        written by check_account_health.yml. Returns [] rather than
        raising if the tab doesn't exist yet — unlike every per-campaign
        tab, this one has no natural "first run creates it" trigger from
        the Streamlit side, so a brand new deployment shouldn't show an
        error here before the periodic workflow has ever run once."""
        try:
            return self._read(tab_name, lambda ws: ws.get_all_records())
        except ReadOnlySheetsError:
            return []
