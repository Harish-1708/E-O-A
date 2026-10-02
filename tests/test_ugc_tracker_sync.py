import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ugc_tracker_sync import sync_once, SHEET_COLUMNS


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
