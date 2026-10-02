"""Standalone UGC Video Edits Tracker sync — deliberately separate
from outreach.py and the Streamlit app, run only via its own GitHub
Actions workflow (see .github/workflows/ugc_tracker_sync.yml).

What it does, once per run:
1. Reads the Creator Outreach Asana project, finds every task
   currently in Rights Secured.
2. Reads the Tracker Sheet's existing rows (keyed by a hidden Asana
   Task GID column) to see which of those tasks already have a row.
3. For each NEW task: assigns a duplicate-safe creator label, searches
   Drive's Raw Contents (files and folders) and Tiktok Contents (files
   only) for a match, and appends a new row — Creator, Brand, Rights
   expiration filled in immediately; Tiktok/Raw filled in only if a
   confident match was found; Status, Edited folder, and Notes left
   completely blank, since those three are the editing team's own,
   permanently, until a later, separate decision automates them.
4. For each EXISTING task whose Tiktok or Raw cell is still blank:
   re-checks Drive once more (raw/TikTok content often arrives after
   the row is first created) and fills in a cell that's still empty —
   never overwrites a cell that already has something in it.

Every ambiguous Drive match (more than one plausible file/folder) is
logged to stdout only — this becomes part of the GitHub Actions run
log and job summary, and is NEVER written to the Sheet. Notes stays
100% manual, by explicit instruction.
"""
import argparse
import os
import sys
import time
from typing import Dict, List, Optional

import gspread
import requests
from google.oauth2.service_account import Credentials
from google.auth.transport.requests import Request as GoogleAuthRequest

from ugc_tracker_logic import (
    extract_rights_secured_tasks, find_drive_match, build_hyperlink_formula,
    assign_duplicate_suffixes, rights_expiration_to_sheet_date,
)


# Column order exactly as it exists in the real Sheet today, plus the
# hidden Task GID column appended at the end for dedup. Status and
# Edited folder sit between Rights expiration and Notes, matching the
# Sheet's own current layout — this sync writes a blank string to
# both on row creation and never touches them again afterward.
SHEET_COLUMNS = [
    "Creator", "Brand", "Tiktok", "Raw", "Rights expiration",
    "Status", "Edited folder", "Notes", "Asana Task GID",
]
COL_CREATOR, COL_BRAND, COL_TIKTOK, COL_RAW, COL_RIGHTS_EXP = 1, 2, 3, 4, 5
COL_STATUS, COL_EDITED_FOLDER, COL_NOTES, COL_TASK_GID = 6, 7, 8, 9

def _extract_folder_id_from_query(q: str) -> str:
    return q.split("'")[1] if "'" in q else "?"


def _raise_clear_403(folder_id: str) -> None:
    # A service account has ZERO Drive access to anything not
    # explicitly shared with its own email address — sharing a PARENT
    # folder does not implicitly grant access to a child folder
    # queried directly by its own ID the way a human user's own folder
    # hierarchy would suggest. Each folder this script reads needs its
    # own, separate share.
    raise PermissionError(
        f"Google Drive refused to access folder {folder_id} (403 Forbidden). If this folder's sharing has "
        f"already been confirmed correct (both service accounts have Writer access, directly on this exact "
        f"folder, not just a parent), this is more likely a Shared Drive query configuration issue than a "
        f"sharing one — but sharing is the first, cheaper thing to rule out: share this exact folder with "
        f"the service account's own email address (the \"client_email\" field inside "
        f"GOOGLE_SERVICE_ACCOUNT_JSON) with at least Viewer access."
    )


class _DriveGetRequest:
    """One prepared Drive files.get call (fetching one file/folder's
    own metadata, not its children) — used only to look up which
    Shared Drive a folder belongs to, if any."""

    def __init__(self, access_token: str, file_id: str, fields: str):
        self._access_token = access_token
        self._file_id = file_id
        self._params = {"fields": fields, "supportsAllDrives": "true"}

    def execute(self) -> Dict:
        response = requests.get(
            f"https://www.googleapis.com/drive/v3/files/{self._file_id}",
            headers={"Authorization": f"Bearer {self._access_token}"},
            params=self._params, timeout=30,
        )
        if response.status_code == 403:
            _raise_clear_403(self._file_id)
        response.raise_for_status()
        return response.json()


class _DriveListRequest:
    """One prepared Drive files.list call — .execute() is what
    actually makes the HTTP request, mirroring googleapiclient's own
    lazy-request shape closely enough that list_drive_folder_items,
    and every test's FakeDriveService, need no changes at all."""

    def __init__(self, access_token: str, q: str, fields: str, pageToken, pageSize: int,
                 drive_id: Optional[str] = None):
        self._access_token = access_token
        self._params = {
            "q": q, "fields": fields, "pageSize": pageSize,
            # Required for anything living in a Google Workspace Shared
            # Drive (as opposed to a personal "My Drive") — without
            # these two, the Drive API silently excludes Shared Drive
            # items from a files.list call entirely, which surfaces as
            # a 403 even when the folder genuinely has been shared with
            # the service account ("organizer"/"fileOrganizer" are
            # Shared-Drive-only role names — their presence is itself
            # the signal this is a Shared Drive, not a regular folder).
            # Harmless no-ops for anything in a regular "My Drive", so
            # always sent, whether or not drive_id below is known.
            "supportsAllDrives": "true", "includeItemsFromAllDrives": "true",
        }
        if drive_id:
            # The piece supportsAllDrives/includeItemsFromAllDrives
            # alone turned out NOT to be sufficient for — confirmed
            # directly, after those two were already in place and a
            # 403 still happened, with sharing independently confirmed
            # correct on both the folder and both service accounts.
            # Scopes the search to this specific Shared Drive, which
            # list_drive_folder_items discovers by looking up the
            # folder's own metadata first. Omitted entirely for a
            # folder NOT in a Shared Drive at all, since corpora/driveId
            # are meaningless (and can themselves cause errors) there.
            self._params["corpora"] = "drive"
            self._params["driveId"] = drive_id
        if pageToken:
            self._params["pageToken"] = pageToken

    def execute(self) -> Dict:
        response = requests.get(
            "https://www.googleapis.com/drive/v3/files",
            headers={"Authorization": f"Bearer {self._access_token}"},
            params=self._params, timeout=30,
        )
        if response.status_code == 403:
            _raise_clear_403(_extract_folder_id_from_query(self._params.get("q", "")))
        response.raise_for_status()
        return response.json()


class _DriveService:
    """A minimal stand-in for googleapiclient's Drive service object —
    just enough of its .files().list(...)/.files().get(...).execute()
    shape to avoid depending on the (fairly heavy) google-api-python-client
    library, which nothing else in this codebase uses; everywhere else
    talks to its APIs directly over requests, including this module's
    own Asana client."""

    def __init__(self, credentials: Credentials):
        # Refreshed once, up front, rather than per-call — a single
        # sync run makes at most a handful of Drive calls, well within
        # one token's lifetime, so there's no need to re-refresh.
        credentials.refresh(GoogleAuthRequest())
        self._access_token = credentials.token

    def files(self):
        return self

    def list(self, q, fields, pageToken=None, pageSize=100, driveId=None) -> _DriveListRequest:
        return _DriveListRequest(self._access_token, q, fields, pageToken, pageSize, drive_id=driveId)

    def get(self, fileId, fields) -> _DriveGetRequest:
        return _DriveGetRequest(self._access_token, fileId, fields)


def _discover_drive_id(drive_service, folder_id: str) -> Optional[str]:
    """Which Shared Drive, if any, a folder belongs to — looked up
    fresh each call rather than hardcoded or passed in as another
    secret, so this keeps working correctly even if a folder ever
    moves between drives. Returns None for an ordinary "My Drive"
    folder (the common case for anyone NOT using Shared Drives at
    all), in which case the caller correctly omits corpora/driveId
    from the actual listing query entirely."""
    metadata = drive_service.files().get(fileId=folder_id, fields="driveId").execute()
    return metadata.get("driveId")


def list_drive_folder_items(drive_service, folder_id: str, files_only: bool = False,
                             shared_drive_id: Optional[str] = None) -> List[Dict]:
    """Every file (and, unless files_only, folder) directly inside one
    Drive folder — name/id/mimeType/webViewLink only, paginated until
    exhausted. files_only=True for Tiktok Contents, which by design
    never needs folder matching (TikTok content is always a single
    direct link, never multiple clips).

    shared_drive_id, when given, is used directly, skipping
    _discover_drive_id's own files.get lookup entirely — a real
    reported case had that specific lookup call 403 on its own, even
    with the folder's sharing and the service account identity both
    independently confirmed correct, so bypassing it with a known-good
    value sidesteps whatever that call-specific issue actually was.
    Falls back to discovering it automatically when not given, for
    setups where that already works fine."""
    drive_id = shared_drive_id or _discover_drive_id(drive_service, folder_id)
    query = f"'{folder_id}' in parents and trashed = false"
    if files_only:
        query += " and mimeType != 'application/vnd.google-apps.folder'"
    items: List[Dict] = []
    page_token = None
    while True:
        response = drive_service.files().list(
            q=query, fields="nextPageToken, files(id, name, mimeType, webViewLink)",
            pageToken=page_token, pageSize=1000, driveId=drive_id,
        ).execute()
        items.extend(response.get("files", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            return items


def ensure_tracker_header(worksheet) -> bool:
    """If the Tracker sheet has no row starting with "Creator" yet —
    a brand new, empty sheet being used for this automation for the
    first time — creates the header row automatically, matching
    SHEET_COLUMNS exactly, rather than requiring someone to type it in
    by hand first. Does nothing, and never touches anything, if a
    header row already exists ANYWHERE in the sheet — including the
    agency's existing Tracker, with its own title/description/stats
    rows above the header, which this must never disturb.

    Returns True if it created the header, False if one already
    existed (useful for logging, not required by the caller)."""
    all_values = worksheet.get_all_values()
    for row in all_values:
        if row and row[0].strip() == "Creator":
            return False
    worksheet.insert_row(SHEET_COLUMNS, index=1, value_input_option="RAW")
    return True


def read_tracker_rows(worksheet) -> List[Dict]:
    """Every data row currently in the Tracker, as {column_name:
    value}, plus its own 1-indexed sheet row number under "_row" — the
    header is read live from the sheet itself (row 5 in the real
    Sheet's current layout: title, description, stats, blank, then
    header), never assumed, so this survives the header moving if the
    Sheet's own top section is ever edited."""
    all_values = worksheet.get_all_values()
    header_row_index = None
    for idx, row in enumerate(all_values):
        if row and row[0].strip() == "Creator":
            header_row_index = idx
            break
    if header_row_index is None:
        raise ValueError("Couldn't find the header row (a row starting with 'Creator') in the Tracker sheet.")
    header = all_values[header_row_index]
    rows = []
    for offset, row in enumerate(all_values[header_row_index + 1:]):
        if not any(cell.strip() for cell in row):
            continue
        record = {header[i]: (row[i] if i < len(row) else "") for i in range(len(header))}
        record["_row"] = header_row_index + 2 + offset  # 1-indexed sheet row
        rows.append(record)
    return rows


def existing_task_gids(tracker_rows: List[Dict]) -> set:
    return {r.get("Asana Task GID", "").strip() for r in tracker_rows if r.get("Asana Task GID", "").strip()}


def find_match_for_task(raw_items: List[Dict], tiktok_items: List[Dict], task_gid: str, creator: str,
                         product: str, already_logged_ambiguous: list) -> Dict[str, Optional[str]]:
    """Searches both Raw and Tiktok folder LISTINGS (fetched ONCE per
    run by the caller, not re-fetched per task — a real agency-scale
    run means dozens of tasks against the same two folders, and Drive
    listing calls aren't free) for one task. Returns {"raw_url":...,
    "tiktok_url":...} — either key is None when no confident match
    exists yet (left blank, not guessed). Ambiguous results are
    appended to already_logged_ambiguous for the caller to print,
    never written anywhere."""
    result = {"raw_url": None, "tiktok_url": None}

    raw_match = find_drive_match(task_gid, creator, product, raw_items)
    if raw_match.status in ("exact_id", "fuzzy"):
        result["raw_url"] = raw_match.item.get("webViewLink", "")
    elif raw_match.status == "ambiguous":
        already_logged_ambiguous.append((task_gid, creator, "Raw", raw_match.candidates))

    tiktok_match = find_drive_match(task_gid, creator, product, tiktok_items)
    if tiktok_match.status in ("exact_id", "fuzzy"):
        result["tiktok_url"] = tiktok_match.item.get("webViewLink", "")
    elif tiktok_match.status == "ambiguous":
        already_logged_ambiguous.append((task_gid, creator, "Tiktok", tiktok_match.candidates))

    return result


def build_new_row(task: Dict, creator_label: str, match: Dict[str, Optional[str]]) -> List[str]:
    """One full row, in SHEET_COLUMNS order. Tiktok/Raw become a
    =HYPERLINK(...) formula when a match was found, else a blank
    string. Status, Edited folder, and Notes are always blank —
    those three are never set by this sync."""
    row = [""] * len(SHEET_COLUMNS)
    row[COL_CREATOR - 1] = creator_label
    row[COL_BRAND - 1] = task["product"]
    if match.get("tiktok_url"):
        row[COL_TIKTOK - 1] = build_hyperlink_formula(match["tiktok_url"], f"{creator_label}_Tiktok")
    if match.get("raw_url"):
        row[COL_RAW - 1] = build_hyperlink_formula(match["raw_url"], f"{creator_label}_Raw")
    row[COL_RIGHTS_EXP - 1] = rights_expiration_to_sheet_date(task["rights_expiration"])
    row[COL_STATUS - 1] = ""
    row[COL_EDITED_FOLDER - 1] = ""
    row[COL_NOTES - 1] = ""
    row[COL_TASK_GID - 1] = task["task_gid"]
    return row


def sync_once(asana_tasks: List[Dict], worksheet, drive_service, raw_folder_id: str,
              tiktok_folder_id: str, print_fn=print, shared_drive_id: Optional[str] = None) -> Dict[str, int]:
    """The whole sync, one pass. Returns a small summary dict (counts)
    for the caller to log. Pure orchestration — every actual decision
    (matching, dedup labeling, date formatting) is delegated to
    ugc_tracker_logic, so this function stays thin and easy to trust.

    raw_folder_id/tiktok_folder_id are passed in explicitly rather than
    hardcoded — deliberately, since this repo is public and these IDs
    identify real internal company resources. main() sources both from
    GitHub Actions secrets, never from committed code.

    shared_drive_id is optional — see list_drive_folder_items for why
    it exists and when it's worth providing."""
    rights_secured = extract_rights_secured_tasks(asana_tasks)
    created_header = ensure_tracker_header(worksheet)
    if created_header:
        print_fn("No header row found — this looks like a brand new Tracker sheet. Created the header row.")
    tracker_rows = read_tracker_rows(worksheet)
    already_synced = existing_task_gids(tracker_rows)

    # Fetched ONCE for the whole run — every task's match check below
    # reuses these same two lists rather than re-listing Drive per task.
    raw_items = list_drive_folder_items(drive_service, raw_folder_id, files_only=False,
                                         shared_drive_id=shared_drive_id)
    tiktok_items = list_drive_folder_items(drive_service, tiktok_folder_id, files_only=True,
                                            shared_drive_id=shared_drive_id)

    new_tasks = [t for t in rights_secured if t["task_gid"] not in already_synced]
    # Dedup labels are computed across ALL Rights Secured tasks sharing
    # a handle (not just the new ones this run) so a second video for
    # a creator already in the tracker still gets "_2", never "" again.
    all_handles_by_gid = {t["task_gid"]: t["creator"] for t in rights_secured}
    ordered_gids = sorted(all_handles_by_gid, key=lambda g: all_handles_by_gid[g])
    labels_by_gid = dict(zip(ordered_gids, assign_duplicate_suffixes([all_handles_by_gid[g] for g in ordered_gids])))

    ambiguous: list = []
    new_rows = []
    for task in new_tasks:
        label = labels_by_gid.get(task["task_gid"], task["creator"])
        match = find_match_for_task(raw_items, tiktok_items, task["task_gid"], task["creator"],
                                     task["product"], ambiguous)
        new_rows.append(build_new_row(task, label, match))

    if new_rows:
        worksheet.append_rows(new_rows, value_input_option="USER_ENTERED")

    filled_in = 0
    for row in tracker_rows:
        task_gid = row.get("Asana Task GID", "").strip()
        if not task_gid:
            continue
        raw_blank = not (row.get("Raw", "") or "").strip()
        tiktok_blank = not (row.get("Tiktok", "") or "").strip()
        if not raw_blank and not tiktok_blank:
            continue
        task = next((t for t in rights_secured if t["task_gid"] == task_gid), None)
        if task is None:
            continue
        match = find_match_for_task(raw_items, tiktok_items, task_gid, task["creator"], task["product"], ambiguous)
        label = row.get("Creator", task["creator"])
        if tiktok_blank and match.get("tiktok_url"):
            worksheet.update_cell(row["_row"], COL_TIKTOK, build_hyperlink_formula(
                match["tiktok_url"], f"{label}_Tiktok"))
            filled_in += 1
        if raw_blank and match.get("raw_url"):
            worksheet.update_cell(row["_row"], COL_RAW, build_hyperlink_formula(match["raw_url"], f"{label}_Raw"))
            filled_in += 1

    for task_gid, creator, column, candidates in ambiguous:
        names = ", ".join(c.get("name", "") for c in candidates)
        print_fn(f"AMBIGUOUS — task {task_gid} ({creator}), {column}: {len(candidates)} possible "
                 f"matches, none written: {names}")

    return {"new_rows": len(new_rows), "filled_in": filled_in, "ambiguous": len(ambiguous)}


def _connect_sheets(sheet_id: str, service_account_info: Dict):
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive.readonly"]
    creds = Credentials.from_service_account_info(service_account_info, scopes=scopes)
    gc = gspread.authorize(creds)
    # A fresh Credentials object for the Drive shim, since .refresh()
    # mutates the token in place and gspread.authorize's own copy
    # shouldn't be touched by this module's unrelated Drive calls.
    drive_creds = Credentials.from_service_account_info(service_account_info, scopes=scopes)
    return gc.open_by_key(sheet_id), _DriveService(drive_creds)


_TRANSIENT_RETRY_DELAYS_SECONDS = [5, 15]


def _call_with_transient_retries(fn, *args, **kwargs):
    """Retries fn(*args, **kwargs) on a genuinely transient network
    failure — the actual reported production error: a one-off SSL
    connection reset while fetching the Sheet's own metadata, with
    nothing wrong on either side (permissions confirmed correct
    directly), that happened to hit mid-handshake. Not retried for
    anything else (a real 403, a missing env var, a malformed
    response) — only the specific transport-level failures below,
    which are the ones a brief pause and a second attempt can
    actually fix. The whole connect-and-sync sequence is safe to
    retry as a unit: sync_once is idempotent (already-synced tasks are
    skipped, an existing link is never overwritten), so re-running it
    from scratch after a failed first attempt can never duplicate or
    corrupt anything."""
    last_exc = None
    for attempt, delay in enumerate([0] + _TRANSIENT_RETRY_DELAYS_SECONDS):
        if delay:
            time.sleep(delay)
        try:
            return fn(*args, **kwargs)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            # Deliberately NOT a bare OSError/ConnectionError catch —
            # PermissionError (this module's own clear, deliberate
            # signal for a real 403) is itself a subclass of OSError in
            # Python, so a broad catch here would silently swallow and
            # uselessly retry that too, defeating the point of it
            # failing fast and clearly. requests always wraps the
            # lower-level network errors that actually occur (a raw
            # ConnectionResetError, an SSL handshake failure) into one
            # of these two exception types before they ever reach this
            # code, so there's nothing a broader catch would add here.
            last_exc = exc
            continue
    raise RuntimeError(f"Still failing after retrying a transient network error "
                        f"{len(_TRANSIENT_RETRY_DELAYS_SECONDS)} time(s): {last_exc}") from last_exc


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Required environment variable {name} is not set — see the workflow's "
                            f"'env:' block and the repo's Settings → Secrets and variables → Actions.")
    return value


def main():
    # Deliberately no --sheet-id or --folder-id CLI arguments: this
    # repo is public, and a value passed on the command line in a
    # workflow file is just as visible there as a hardcoded constant
    # would be in this script. Every identifying ID comes from an
    # environment variable instead, which the workflow sources from
    # GitHub Actions secrets — never committed anywhere.
    parser = argparse.ArgumentParser(description="Sync Asana Rights Secured into the UGC Video Edits Tracker.")
    parser.add_argument("--worksheet-name", default="Tracker")  # not identifying — safe as a plain argument
    args = parser.parse_args()

    sheet_id = _require_env("UGC_TRACKER_SHEET_ID")
    raw_folder_id = _require_env("UGC_TRACKER_RAW_FOLDER_ID")
    # Optional — only needed for a setup where discovering this
    # automatically (via a files.get lookup) 403s on its own, even
    # with everything else confirmed correct. When unset, falls back
    # to automatic discovery, which is fine for most setups.
    shared_drive_id = os.environ.get("UGC_TRACKER_SHARED_DRIVE_ID", "").strip() or None
    tiktok_folder_id = _require_env("UGC_TRACKER_TIKTOK_FOLDER_ID")
    asana_project_gid = _require_env("UGC_TRACKER_ASANA_PROJECT_GID")

    import json
    service_account_info = json.loads(_require_env("GOOGLE_SERVICE_ACCOUNT_JSON"))
    # Safe to print — an email address, not a secret value (the
    # private key itself is never touched here) — specifically to let
    # this be cross-checked directly against whichever account(s) have
    # actually been shared on the Sheet and both Drive folders, since
    # a persistent 403 across every kind of Drive call despite several
    # correct parameter fixes is also consistent with this secret
    # simply containing a different service account than the one(s)
    # that were shared.
    print(f"Authenticating as service account: {service_account_info.get('client_email', '(not found in JSON)')}")
    asana_token = _require_env("ASANA_TOKEN")

    def _connect_and_sync():
        # Everything that talks to a network, as one unit — connecting
        # (where the actual reported error happened, a one-off SSL
        # reset while fetching the Sheet's own metadata) through the
        # sync itself. Retried as a whole rather than call-by-call: the
        # whole thing is safe to simply run again from scratch (see
        # _call_with_transient_retries' own docstring).
        spreadsheet, drive_service = _connect_sheets(sheet_id, service_account_info)
        worksheet = spreadsheet.worksheet(args.worksheet_name)
        import asana_client  # thin wrapper, kept separate so this stays testable without a real Asana token
        asana_tasks = asana_client.get_all_project_tasks(asana_project_gid, asana_token)
        return sync_once(asana_tasks, worksheet, drive_service, raw_folder_id, tiktok_folder_id,
                          shared_drive_id=shared_drive_id)

    summary = _call_with_transient_retries(_connect_and_sync)
    print(f"New rows: {summary['new_rows']}. Links filled in on existing rows: {summary['filled_in']}. "
          f"Ambiguous (not written): {summary['ambiguous']}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
