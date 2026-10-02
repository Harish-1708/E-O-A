import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests

from ugc_tracker_sync import (
    sync_once, ensure_tracker_header, SHEET_COLUMNS, _DriveListRequest, _call_with_transient_retries,
)


class FakeWorksheet:
    """Minimal gspread-worksheet-shaped fake. header_and_rows is the
    FULL raw grid exactly as get_all_values() would return it,
    including the real Sheet's own title/description/stats rows above
    the actual header — so tests exercise the real header-finding
    logic, not a simplified stand-in for it."""

    def __init__(self, header_and_rows):
        self._grid = [list(row) for row in header_and_rows]
        self.appended = []
        self.updated_cells = []  # list of (row, col, value)

    def get_all_values(self):
        return [list(row) for row in self._grid]

    def append_rows(self, rows, value_input_option="RAW"):
        self.appended.extend(rows)
        for row in rows:
            self._grid.append(list(row))

    def update_cell(self, row, col, value):
        self.updated_cells.append((row, col, value))
        # Keep the in-memory grid consistent so a second sync_once call
        # within the same test sees the update, same as a real sheet.
        row_idx = row - 1
        while len(self._grid) <= row_idx:
            self._grid.append([""] * len(SHEET_COLUMNS))
        while len(self._grid[row_idx]) < col:
            self._grid[row_idx].append("")
        self._grid[row_idx][col - 1] = value

    def insert_row(self, values, index=1, value_input_option="RAW"):
        self._grid.insert(index - 1, list(values))


class FakeFilesList:
    def __init__(self, items):
        self._items = items

    def execute(self):
        return {"files": self._items, "nextPageToken": None}


class FakeFiles:
    def __init__(self, items_by_folder):
        self._items_by_folder = items_by_folder

    def list(self, q, fields, pageToken, pageSize):
        # Extract the folder id from the query string the real code builds.
        folder_id = q.split("'")[1]
        items = self._items_by_folder.get(folder_id, [])
        if "folder'" in q and "!=" in q:
            items = [i for i in items if i.get("mimeType") != "application/vnd.google-apps.folder"]
        return FakeFilesList(items)


class FakeDriveService:
    def __init__(self, items_by_folder):
        self._files = FakeFiles(items_by_folder)

    def files(self):
        return self._files


_SHEET_PREAMBLE = [
    ["Kelson Sourced UGC — Tracker", "", "", "", "", "", "", "", ""],
    ["One row per video...", "", "", "", "", "", "", "", ""],
    ["Total", "26", "Not edited", "19", "Edited", "7", "Live", "0", ""],
    [""] * 9,
]


def _header_row():
    return list(SHEET_COLUMNS)


def _asana_task(gid, section_name, creator="", product="", rights_expiration=""):
    return {
        "gid": gid,
        "permalink_url": f"https://app.asana.com/0/0/{gid}",
        "memberships": [{"section": {"name": section_name}}],
        "custom_fields": [
            {"name": "Creator", "display_value": creator or None},
            {"name": "Product", "display_value": product or None},
            {"name": "Rights Expiration", "display_value": rights_expiration or None},
        ],
    }


def _drive_item(name, item_id, is_folder=False):
    return {
        "name": name, "id": item_id,
        "mimeType": "application/vnd.google-apps.folder" if is_folder else "video/mp4",
        "webViewLink": f"https://drive.google.com/file/d/{item_id}/view",
    }


# Arbitrary fake IDs — this test never touches a real Drive folder,
# and the real IDs live only in GitHub Actions secrets, never in
# committed code (this repo is public).
RAW_FOLDER_ID = "fake-raw-folder-id"
TIKTOK_FOLDER_ID = "fake-tiktok-folder-id"


# ---------- transient network retry: the actual reported production error ----------

def test_call_with_transient_retries_succeeds_on_a_later_attempt():
    """The actual reported production error: a one-off SSL connection
    reset while fetching the Sheet's own metadata, with permissions on
    both sides confirmed correct directly — a second attempt should
    simply work."""
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.exceptions.ConnectionError("Connection reset by peer")
        return "ok"

    with patch("ugc_tracker_sync.time.sleep"):
        result = _call_with_transient_retries(flaky)

    assert result == "ok"
    assert calls["n"] == 2


def test_call_with_transient_retries_gives_up_after_exhausting_retries():
    def always_fails():
        raise requests.exceptions.ConnectionError("still down")

    with patch("ugc_tracker_sync.time.sleep"):
        with pytest.raises(RuntimeError, match="transient network error"):
            _call_with_transient_retries(always_fails)


def test_call_with_transient_retries_never_retries_a_non_transient_error():
    """A real permissions problem or a programming bug must surface
    immediately — retrying it several times with delays just wastes
    the run's time before failing anyway."""
    calls = {"n": 0}

    def fails_for_real():
        calls["n"] += 1
        raise PermissionError("genuinely not shared")

    with patch("ugc_tracker_sync.time.sleep"):
        with pytest.raises(PermissionError):
            _call_with_transient_retries(fails_for_real)

    assert calls["n"] == 1  # never retried


# ---------- Shared Drive support: the actual reported root cause ----------

def test_drive_list_request_always_includes_shared_drive_parameters():
    """The actual reported production error, root-caused properly:
    "Contributor" is a role name that only exists for Google Workspace
    Shared Drives — its presence in the report meant this folder lives
    in one, and the Drive API silently excludes Shared Drive items
    from files.list unless the request explicitly opts in, regardless
    of how correctly the folder was shared. Every request this script
    makes must always include both flags — harmless for a regular "My
    Drive" folder, required for a Shared Drive one, and there's no way
    to know in advance which kind any given folder id is."""
    request = _DriveListRequest("fake-token", q="'somefolder' in parents and trashed = false",
                                 fields="files(id,name)", pageToken=None, pageSize=1000)

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"files": []}

    with patch("ugc_tracker_sync.requests.get", return_value=FakeResponse()) as mock_get:
        request.execute()

    _, kwargs = mock_get.call_args
    assert kwargs["params"]["supportsAllDrives"] == "true"
    assert kwargs["params"]["includeItemsFromAllDrives"] == "true"


# ---------- the actual reported production error: a 403 from Drive ----------

def test_drive_list_request_403_names_the_specific_folder_and_explains_sharing():
    """The actual reported production error: a service account was
    only shared one of its two required folders, directly, not the
    parent — and the raw 403 traceback gave no hint which folder or
    why. Must name the exact folder id and explain that each folder
    needs its own direct share, not just the parent."""
    request = _DriveListRequest("fake-token", q="'1CU4ZVPWp8enP4RCnESk_anUhCqhJJXpc' in parents and trashed = false",
                                 fields="files(id,name)", pageToken=None, pageSize=1000)

    class FakeResponse:
        status_code = 403

    with patch("ugc_tracker_sync.requests.get", return_value=FakeResponse()):
        with pytest.raises(PermissionError) as exc_info:
            request.execute()

    message = str(exc_info.value)
    assert "1CU4ZVPWp8enP4RCnESk_anUhCqhJJXpc" in message
    assert "service account" in message.lower()
    assert "parent" in message.lower()  # the specific misunderstanding this is clarifying


# ---------- ensure_tracker_header: the actual reported bug ----------

def test_ensure_tracker_header_creates_it_on_a_brand_new_empty_sheet():
    """The actual reported production bug: a fresh Tracker sheet, set
    up for the first time with nothing in it at all, crashed instead
    of being usable. Must create the header automatically."""
    ws = FakeWorksheet([])
    created = ensure_tracker_header(ws)
    assert created is True
    assert ws.get_all_values()[0] == SHEET_COLUMNS


def test_ensure_tracker_header_does_nothing_when_one_already_exists():
    """Must never touch the agency's existing, already-set-up Tracker
    — title row, description row, stats row, and all — just because
    this check runs on every sync."""
    ws = FakeWorksheet(_SHEET_PREAMBLE + [_header_row()])
    original = ws.get_all_values()
    created = ensure_tracker_header(ws)
    assert created is False
    assert ws.get_all_values() == original


def test_sync_once_works_end_to_end_starting_from_a_completely_empty_sheet():
    """The full reported scenario, not just the header check in
    isolation: a brand new empty Tracker sheet must be usable
    immediately — header created, then the row for a Rights Secured
    task appended right after it, all in one run."""
    tasks = [_asana_task("111", "Rights Secured", creator="@newcreator", product="DudeRobe",
                          rights_expiration="2027-04-01T00:00:00.000Z")]
    ws = FakeWorksheet([])
    logged = []

    summary = sync_once(tasks, ws, FakeDriveService({}), RAW_FOLDER_ID, TIKTOK_FOLDER_ID, print_fn=logged.append)

    assert summary["new_rows"] == 1
    grid = ws.get_all_values()
    assert grid[0] == SHEET_COLUMNS
    assert grid[1][0] == "@newcreator"
    assert any("brand new Tracker sheet" in line for line in logged)


# ---------- new row creation ----------

def test_sync_once_creates_a_new_row_for_a_rights_secured_task_not_yet_in_tracker():
    tasks = [_asana_task("111", "Rights Secured", creator="@newcreator", product="DudeRobe",
                          rights_expiration="2027-04-01T00:00:00.000Z")]
    ws = FakeWorksheet(_SHEET_PREAMBLE + [_header_row()])
    drive = FakeDriveService({})

    summary = sync_once(tasks, ws, drive, RAW_FOLDER_ID, TIKTOK_FOLDER_ID)

    assert summary["new_rows"] == 1
    assert len(ws.appended) == 1
    row = ws.appended[0]
    assert row[0] == "@newcreator"
    assert row[1] == "DudeRobe"
    assert row[4] == "4/1/2027"
    assert row[8] == "111"  # Asana Task GID


def test_sync_once_leaves_status_edited_folder_and_notes_blank_on_a_new_row():
    """The explicit instruction: Status, Edited folder, and Notes are
    never set by this sync — not even a default value."""
    tasks = [_asana_task("111", "Rights Secured", creator="@x", product="DudeRobe")]
    ws = FakeWorksheet(_SHEET_PREAMBLE + [_header_row()])
    summary = sync_once(tasks, ws, FakeDriveService({}), RAW_FOLDER_ID, TIKTOK_FOLDER_ID)
    assert summary["new_rows"] == 1
    row = ws.appended[0]
    assert row[5] == ""  # Status
    assert row[6] == ""  # Edited folder
    assert row[7] == ""  # Notes


def test_sync_once_skips_a_task_already_in_the_tracker():
    tasks = [_asana_task("111", "Rights Secured", creator="@existing", product="DudeRobe")]
    ws = FakeWorksheet(_SHEET_PREAMBLE + [_header_row(),
                        ["@existing", "DudeRobe", "", "", "3/1/2027", "Not edited", "", "", "111"]])
    summary = sync_once(tasks, ws, FakeDriveService({}), RAW_FOLDER_ID, TIKTOK_FOLDER_ID)
    assert summary["new_rows"] == 0
    assert ws.appended == []


def test_sync_once_ignores_tasks_outside_rights_secured():
    tasks = [_asana_task("111", "Negotiating", creator="@x", product="DudeRobe")]
    ws = FakeWorksheet(_SHEET_PREAMBLE + [_header_row()])
    summary = sync_once(tasks, ws, FakeDriveService({}), RAW_FOLDER_ID, TIKTOK_FOLDER_ID)
    assert summary["new_rows"] == 0


# ---------- Drive matching on new row creation ----------

def test_sync_once_fills_tiktok_and_raw_when_exact_id_match_found():
    tasks = [_asana_task("111", "Rights Secured", creator="@x", product="DudeRobe")]
    ws = FakeWorksheet(_SHEET_PREAMBLE + [_header_row()])
    items_by_folder = {
        RAW_FOLDER_ID: [_drive_item("DudeRobe - @x – DudeRobe [111].mov", "raw1")],
        TIKTOK_FOLDER_ID: [_drive_item("DudeRobe - @x – DudeRobe [111].mp4", "tt1")],
    }
    summary = sync_once(tasks, ws, FakeDriveService(items_by_folder), RAW_FOLDER_ID, TIKTOK_FOLDER_ID)
    assert summary["new_rows"] == 1
    row = ws.appended[0]
    assert "HYPERLINK" in row[2]  # Tiktok
    assert "@x_Tiktok" in row[2]
    assert "raw1" in row[2] or "drive.google.com/file/d/tt1" in row[2]
    assert "HYPERLINK" in row[3]  # Raw
    assert "@x_Raw" in row[3]


def test_sync_once_leaves_tiktok_and_raw_blank_when_nothing_matches():
    tasks = [_asana_task("111", "Rights Secured", creator="@nobody", product="DudeRobe")]
    ws = FakeWorksheet(_SHEET_PREAMBLE + [_header_row()])
    summary = sync_once(tasks, ws, FakeDriveService({}), RAW_FOLDER_ID, TIKTOK_FOLDER_ID)
    row = ws.appended[0]
    assert row[2] == ""
    assert row[3] == ""
    assert summary["ambiguous"] == 0


def test_sync_once_the_real_ksmshaw_ambiguous_case_leaves_raw_blank_and_logs_it():
    """The actual real-world case: two unlabeled raw files for one
    creator. Must not guess, must log it, must leave Raw blank."""
    tasks = [_asana_task("1218941738388881", "Rights Secured", creator="@ksmshaw", product="DudeRobe")]
    ws = FakeWorksheet(_SHEET_PREAMBLE + [_header_row()])
    items_by_folder = {
        RAW_FOLDER_ID: [
            _drive_item("DudeRobe - @ksmshaw – DudeRobe.mov", "f1"),
            _drive_item("DudeRobe  @ksmshaw - DudeRobe.mov", "f2"),
        ],
    }
    logged = []
    summary = sync_once(tasks, ws, FakeDriveService(items_by_folder), RAW_FOLDER_ID, TIKTOK_FOLDER_ID, print_fn=logged.append)
    row = ws.appended[0]
    assert row[3] == ""  # Raw stays blank
    assert summary["ambiguous"] == 1
    assert any("AMBIGUOUS" in line and "ksmshaw" in line for line in logged)


def test_sync_once_tiktok_folder_never_matches_a_folder_even_if_one_existed():
    """Explicit instruction: Tiktok content is only ever a direct file
    link — folders must never be considered there, even if one somehow
    existed in that folder."""
    tasks = [_asana_task("111", "Rights Secured", creator="@x", product="DudeRobe")]
    ws = FakeWorksheet(_SHEET_PREAMBLE + [_header_row()])
    items_by_folder = {
        TIKTOK_FOLDER_ID: [_drive_item("DudeRobe - @x – DudeRobe [111]", "folder1", is_folder=True)],
    }
    summary = sync_once(tasks, ws, FakeDriveService(items_by_folder), RAW_FOLDER_ID, TIKTOK_FOLDER_ID)
    assert summary["new_rows"] == 1
    assert ws.appended[0][2] == ""  # Tiktok stays blank — the folder must be excluded


def test_sync_once_raw_folder_does_match_a_folder_for_multi_clip_creators():
    tasks = [_asana_task("111", "Rights Secured", creator="@x", product="DudeRobe")]
    ws = FakeWorksheet(_SHEET_PREAMBLE + [_header_row()])
    items_by_folder = {
        RAW_FOLDER_ID: [_drive_item("DudeRobe - @x – DudeRobe [111]", "folder1", is_folder=True)],
    }
    summary = sync_once(tasks, ws, FakeDriveService(items_by_folder), RAW_FOLDER_ID, TIKTOK_FOLDER_ID)
    assert summary["new_rows"] == 1
    assert "folder1" in ws.appended[0][3]  # Raw


# ---------- duplicate creator handling ----------

def test_sync_once_assigns_suffix_for_a_creator_with_two_rights_secured_tasks_in_one_run():
    tasks = [
        _asana_task("100", "Rights Secured", creator="@ksmshaw", product="DudeRobe"),
        _asana_task("200", "Rights Secured", creator="@ksmshaw", product="DudeRobe"),
    ]
    ws = FakeWorksheet(_SHEET_PREAMBLE + [_header_row()])
    sync_once(tasks, ws, FakeDriveService({}), RAW_FOLDER_ID, TIKTOK_FOLDER_ID)
    labels = sorted(r[0] for r in ws.appended)
    assert labels == ["@ksmshaw", "@ksmshaw_2"]


def test_sync_once_new_task_for_a_creator_already_in_tracker_gets_suffix_not_blank_duplicate():
    """A SECOND video for a creator who already has a row must still
    get a distinguishing suffix, not come in as a second bare
    "@handle" row — this is the case the one existing precedent in
    the real Sheet (@2.fit.bros) got right manually, but @ksmshaw
    didn't."""
    tasks = [
        _asana_task("100", "Rights Secured", creator="@2.fit.bros", product="DudeRobe"),
        _asana_task("200", "Rights Secured", creator="@2.fit.bros", product="DudeRobe"),
    ]
    ws = FakeWorksheet(_SHEET_PREAMBLE + [_header_row(),
                        ["@2.fit.bros", "DudeRobe", "", "", "3/1/2027", "", "", "", "100"]])
    summary = sync_once(tasks, ws, FakeDriveService({}), RAW_FOLDER_ID, TIKTOK_FOLDER_ID)
    assert summary["new_rows"] == 1
    assert ws.appended[0][0] == "@2.fit.bros_2"


# ---------- filling in blank cells on existing rows ----------

def test_sync_once_fills_in_raw_for_an_existing_row_once_content_appears_later():
    tasks = [_asana_task("111", "Rights Secured", creator="@x", product="DudeRobe")]
    ws = FakeWorksheet(_SHEET_PREAMBLE + [_header_row(),
                        ["@x", "DudeRobe", "", "", "3/1/2027", "Not edited", "", "", "111"]])
    items_by_folder = {RAW_FOLDER_ID: [_drive_item("DudeRobe - @x – DudeRobe [111].mov", "raw1")]}

    summary = sync_once(tasks, ws, FakeDriveService(items_by_folder), RAW_FOLDER_ID, TIKTOK_FOLDER_ID)

    assert summary["new_rows"] == 0
    assert summary["filled_in"] == 1
    assert ws.updated_cells == [(6, 4, ws.updated_cells[0][2])]  # row 6 (header is row 5), col 4 = Raw
    assert "HYPERLINK" in ws.updated_cells[0][2]
    assert "@x_Raw" in ws.updated_cells[0][2]


def test_sync_once_never_overwrites_a_cell_that_already_has_a_link():
    """Critical safety property: once Raw or Tiktok has something in
    it, this sync must never touch that cell again on a later run,
    even if Drive listing now shows something different."""
    existing_link = '=HYPERLINK("https://drive.google.com/file/d/old/view", "@x_Raw")'
    tasks = [_asana_task("111", "Rights Secured", creator="@x", product="DudeRobe")]
    ws = FakeWorksheet(_SHEET_PREAMBLE + [_header_row(),
                        ["@x", "DudeRobe", "", existing_link, "3/1/2027", "", "", "", "111"]])
    items_by_folder = {RAW_FOLDER_ID: [_drive_item("DudeRobe - @x – DudeRobe [111].mov", "new_raw")]}

    summary = sync_once(tasks, ws, FakeDriveService(items_by_folder), RAW_FOLDER_ID, TIKTOK_FOLDER_ID)

    assert summary["filled_in"] == 0
    assert ws.updated_cells == []


def test_sync_once_never_touches_status_or_edited_folder_on_an_existing_row():
    tasks = [_asana_task("111", "Rights Secured", creator="@x", product="DudeRobe")]
    ws = FakeWorksheet(_SHEET_PREAMBLE + [_header_row(),
                        ["@x", "DudeRobe", "", "", "3/1/2027", "Edited", "some-link", "a note", "111"]])
    items_by_folder = {RAW_FOLDER_ID: [_drive_item("DudeRobe - @x – DudeRobe [111].mov", "raw1")]}

    sync_once(tasks, ws, FakeDriveService(items_by_folder), RAW_FOLDER_ID, TIKTOK_FOLDER_ID)

    touched_cols = {col for (_row, col, _val) in ws.updated_cells}
    assert 6 not in touched_cols  # Status
    assert 7 not in touched_cols  # Edited folder
    assert 8 not in touched_cols  # Notes
