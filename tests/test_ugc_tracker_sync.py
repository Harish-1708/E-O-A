import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests

from ugc_tracker_sync import (
    sync_once, ensure_tracker_header, ensure_status_dropdown, ensure_status_colors, ensure_product_column,
    build_new_row, SHEET_COLUMNS, STATUS_OPTIONS, STATUS_COLORS,
    COL_BRAND, COL_PRODUCT, COL_CREATOR, COL_STATUS, COL_EDITED_FOLDER, COL_NOTES,
    _DriveListRequest, _call_with_transient_retries, _discover_drive_id, list_drive_folder_items,
)


class FakeSpreadsheetForWorksheet:
    """Just enough of gspread's Spreadsheet shape for
    ensure_status_dropdown/ensure_status_colors. Records every request
    body it's given, AND simulates how Sheets itself would actually
    apply add/delete conditional-format-rule requests — so a test can
    genuinely verify "calling this twice doesn't stack duplicates,"
    not just that delete requests were present in the request body."""

    def __init__(self, sheet_id, worksheet=None):
        self.batch_update_calls = []
        self._sheet_id = sheet_id
        self._worksheet = worksheet  # set after construction — see FakeWorksheet.__init__
        self._conditional_formats = []  # simulates the real sheet's own current state

    def batch_update(self, body):
        self.batch_update_calls.append(body)
        for request in body.get("requests", []):
            if "deleteConditionalFormatRule" in request:
                idx = request["deleteConditionalFormatRule"]["index"]
                del self._conditional_formats[idx]
            elif "addConditionalFormatRule" in request:
                rule = request["addConditionalFormatRule"]["rule"]
                index = request["addConditionalFormatRule"].get("index", len(self._conditional_formats))
                self._conditional_formats.insert(index, rule)
            elif "insertDimension" in request:
                rng = request["insertDimension"]["range"]
                assert rng["dimension"] == "COLUMNS"
                self._worksheet.insert_column_at(rng["startIndex"] + 1)  # 0-indexed -> 1-indexed

    def fetch_sheet_metadata(self):
        return {"sheets": [{"properties": {"sheetId": self._sheet_id},
                             "conditionalFormats": list(self._conditional_formats)}]}


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
        self.id = 123456789  # arbitrary fake sheetId
        self.spreadsheet = FakeSpreadsheetForWorksheet(self.id, worksheet=self)

    def get_all_values(self):
        return [list(row) for row in self._grid]

    def row_values(self, row_number):
        row_idx = row_number - 1
        return list(self._grid[row_idx]) if row_idx < len(self._grid) else []

    def insert_column_at(self, index_1indexed):
        """Simulates insertDimension's real effect — a genuinely new,
        blank column, with every existing cell to its right shifted
        over by one, not overwritten in place."""
        for row in self._grid:
            while len(row) < index_1indexed - 1:
                row.append("")
            row.insert(index_1indexed - 1, "")

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


class FakeFilesGet:
    def __init__(self, drive_id):
        self._drive_id = drive_id

    def execute(self):
        return {"driveId": self._drive_id} if self._drive_id else {}


class FakeFiles:
    def __init__(self, items_by_folder, drive_ids_by_folder=None):
        self._items_by_folder = items_by_folder
        # Defaults every folder to "not in a Shared Drive" (no driveId)
        # unless a test specifically says otherwise — matching what
        # every existing test here was written against, since none of
        # them are testing Shared Drive discovery itself.
        self._drive_ids_by_folder = drive_ids_by_folder or {}

    def list(self, q, fields, pageToken, pageSize, driveId=None):
        # Extract the folder id from the query string the real code builds.
        folder_id = q.split("'")[1]
        items = self._items_by_folder.get(folder_id, [])
        if "folder'" in q and "!=" in q:
            items = [i for i in items if i.get("mimeType") != "application/vnd.google-apps.folder"]
        return FakeFilesList(items)

    def get(self, fileId, fields):
        return FakeFilesGet(self._drive_ids_by_folder.get(fileId))


class FakeDriveService:
    def __init__(self, items_by_folder, drive_ids_by_folder=None):
        self._files = FakeFiles(items_by_folder, drive_ids_by_folder)

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


# ---------- Shared Drive ID discovery: supportsAllDrives/includeItemsFromAllDrives alone wasn't enough ----------

def test_discover_drive_id_finds_it_when_folder_is_in_a_shared_drive():
    ds = FakeDriveService({}, drive_ids_by_folder={"folder123": "shared-drive-abc"})
    assert _discover_drive_id(ds, "folder123") == "shared-drive-abc"


def test_discover_drive_id_returns_none_for_an_ordinary_my_drive_folder():
    ds = FakeDriveService({})  # no drive_ids_by_folder at all
    assert _discover_drive_id(ds, "folder123") is None


def test_list_drive_folder_items_passes_the_discovered_drive_id_into_the_actual_query():
    """The real reported production fix: supportsAllDrives and
    includeItemsFromAllDrives alone were already in place and a 403
    still happened, with sharing independently confirmed correct. The
    missing piece was corpora=drive + driveId, scoped to the SPECIFIC
    Shared Drive this folder lives in — discovered here, not
    hardcoded, so this keeps working if a folder ever moves drives."""
    ds = FakeDriveService({"folder123": []}, drive_ids_by_folder={"folder123": "shared-drive-abc"})
    original_list = ds.files().list
    captured = {}

    def spy_list(**kwargs):
        captured.update(kwargs)
        return original_list(**kwargs)

    with patch.object(ds.files(), "list", side_effect=spy_list):
        list_drive_folder_items(ds, "folder123")

    assert captured.get("driveId") == "shared-drive-abc"


def test_list_drive_folder_items_skips_discovery_entirely_when_drive_id_given_explicitly():
    """The real production fix for a specific failure: the files.get
    discovery lookup itself 403'd on its own, even with the folder's
    sharing and the authenticating service account identity both
    independently confirmed correct. Passing shared_drive_id directly
    must skip that lookup call completely, not merely override its
    result — so this stays usable even when that lookup can't succeed
    at all."""
    ds = FakeDriveService({"folder123": []})  # NO drive_ids_by_folder — .get() would return nothing useful
    get_calls = []
    original_get = ds.files().get

    def spy_get(**kwargs):
        get_calls.append(kwargs)
        return original_get(**kwargs)

    captured = {}
    original_list = ds.files().list

    def spy_list(**kwargs):
        captured.update(kwargs)
        return original_list(**kwargs)

    with patch.object(ds.files(), "get", side_effect=spy_get), \
         patch.object(ds.files(), "list", side_effect=spy_list):
        list_drive_folder_items(ds, "folder123", shared_drive_id="explicit-drive-id")

    assert get_calls == []  # discovery never even attempted
    assert captured.get("driveId") == "explicit-drive-id"


def test_list_drive_folder_items_omits_drive_id_for_an_ordinary_folder():
    """Must never pass a meaningless driveId for a regular "My Drive"
    folder — doing so could itself cause a different error, and the
    vast majority of setups that don't use Shared Drives at all must
    see no behavior change from any of this."""
    ds = FakeDriveService({"folder123": []})  # no Shared Drive at all
    captured = {}
    original_list = ds.files().list

    def spy_list(**kwargs):
        captured.update(kwargs)
        return original_list(**kwargs)

    with patch.object(ds.files(), "list", side_effect=spy_list):
        list_drive_folder_items(ds, "folder123")

    assert captured.get("driveId") is None


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


# ---------- ensure_product_column: the actual reported missing column ----------

# The OLD 9-column header, exactly matching what's live on the real
# sheet right now — no Product column at all.
_OLD_HEADER_ROW = ["Asana Task GID", "Creator", "Brand", "Tiktok", "Raw", "Rights expiration",
                   "Status", "Edited folder", "Notes"]


def test_ensure_product_column_inserts_it_on_an_old_style_sheet():
    """The actual reported production gap: the live sheet has no
    Product column at all. Must detect this and insert one, named
    correctly, at the expected position."""
    ws = FakeWorksheet(_SHEET_PREAMBLE + [_OLD_HEADER_ROW])
    header_row_number = len(_SHEET_PREAMBLE) + 1
    inserted = ensure_product_column(ws, header_row_number)
    assert inserted is True
    new_header = ws.get_all_values()[header_row_number - 1]
    assert new_header[COL_PRODUCT - 1] == "Product"
    assert new_header == SHEET_COLUMNS


def test_ensure_product_column_preserves_every_existing_cell_in_its_correct_shifted_position():
    """The critical safety property explicitly requested: inserting
    the column must never disturb existing data — especially manually
    entered Status/Edited folder/Notes values — it must correctly
    shift everything from Tiktok onward one column to the right,
    exactly as a real Sheets column insert would."""
    existing_row = ["1218941738388881", "@ksmshaw", "DudeRobe", "@ksmshaw_Tiktok",
                    "@ksmshaw_Raw", "3/28/2027", "Edited", "https://drive/some-folder", "a manual note"]
    ws = FakeWorksheet(_SHEET_PREAMBLE + [_OLD_HEADER_ROW, existing_row])
    header_row_number = len(_SHEET_PREAMBLE) + 1
    ensure_product_column(ws, header_row_number)

    migrated_row = ws.get_all_values()[header_row_number]  # the data row, right after the header
    assert migrated_row[0] == "1218941738388881"  # GID untouched
    assert migrated_row[1] == "@ksmshaw"           # Creator untouched
    assert migrated_row[2] == "DudeRobe"           # Brand untouched
    assert migrated_row[3] == ""                   # brand new Product cell — blank, to be backfilled
    assert migrated_row[4] == "@ksmshaw_Tiktok"    # Tiktok correctly SHIFTED right, not overwritten
    assert migrated_row[5] == "@ksmshaw_Raw"       # Raw correctly shifted
    assert migrated_row[6] == "3/28/2027"          # Rights expiration correctly shifted
    assert migrated_row[7] == "Edited"             # the manually-set Status — preserved exactly
    assert migrated_row[8] == "https://drive/some-folder"  # the manual Edited folder link — preserved exactly
    assert migrated_row[9] == "a manual note"      # the manual Note — preserved exactly


def test_ensure_product_column_does_nothing_once_already_present():
    ws = FakeWorksheet(_SHEET_PREAMBLE + [_header_row()])
    original = ws.get_all_values()
    inserted = ensure_product_column(ws, len(_SHEET_PREAMBLE) + 1)
    assert inserted is False
    assert ws.get_all_values() == original


# ---------- Brand is a fixed constant, Product is the real Asana value ----------

def test_build_new_row_brand_is_always_the_constant_regardless_of_actual_product():
    """The actual clarified requirement: Brand never varies — it's
    always "DudeRobe", the overall client — even for the one real task
    whose Product is genuinely "SheRobe"."""
    task = {"task_gid": "111", "creator": "@jo.vall", "product": "SheRobe",
            "rights_expiration": "2027-03-28T00:00:00.000Z"}
    row = build_new_row(task, "@jo.vall", {"raw_url": None, "tiktok_url": None})
    assert row[COL_BRAND - 1] == "DudeRobe"
    assert row[COL_PRODUCT - 1] == "SheRobe"  # the real, varying value — correctly NOT overwritten


def test_sync_once_backfills_product_for_an_existing_row_missing_it():
    """The actual reported end-to-end scenario: an existing row,
    written before the Product column existed, has it blank even
    though Tiktok is already filled in — must still get backfilled,
    not skipped just because the other automation-owned cells are
    already done."""
    tasks = [_asana_task("111", "Rights Secured", creator="@x", product="SheRobe")]
    ws = FakeWorksheet(_SHEET_PREAMBLE + [_header_row(),
                        ["111", "@x", "DudeRobe", "", "@x_Tiktok", "@x_Raw", "3/1/2027", "Edited", "link", "note"]])

    summary = sync_once(tasks, ws, FakeDriveService({}), RAW_FOLDER_ID, TIKTOK_FOLDER_ID)

    assert summary["filled_in"] == 1
    assert ws.updated_cells == [(6, COL_PRODUCT, "SheRobe")]
    # Confirms the untouched columns stayed exactly as they were — the
    # manual Status/Edited folder/Notes values specifically.
    final_row = ws.get_all_values()[5]
    assert final_row[COL_STATUS - 1] == "Edited"
    assert final_row[COL_EDITED_FOLDER - 1] == "link"
    assert final_row[COL_NOTES - 1] == "note"


def test_sync_once_migrates_and_backfills_an_old_style_sheet_end_to_end():
    """The full, real-world migration path in one pass: an old-style
    sheet with no Product column, one existing row with real manual
    data, and one brand new Rights Secured task. Must insert the
    column, preserve the existing row's manual data exactly, backfill
    its Product cell, AND append the new row correctly — all without
    corrupting anything, in a single sync_once call."""
    existing_row = ["1218941738388881", "@ksmshaw", "DudeRobe", "@ksmshaw_Tiktok",
                    "@ksmshaw_Raw", "3/28/2027", "Edited", "https://drive/some-folder", "do not touch this"]
    ws = FakeWorksheet(_SHEET_PREAMBLE + [_OLD_HEADER_ROW, existing_row])
    tasks = [
        _asana_task("1218941738388881", "Rights Secured", creator="@ksmshaw", product="DudeRobe"),
        _asana_task("222", "Rights Secured", creator="@newperson", product="DudeRobe",
                    rights_expiration="2027-05-01T00:00:00.000Z"),
    ]

    summary = sync_once(tasks, ws, FakeDriveService({}), RAW_FOLDER_ID, TIKTOK_FOLDER_ID)

    assert summary["new_rows"] == 1
    assert summary["filled_in"] == 1  # the existing row's blank Product
    grid = ws.get_all_values()
    header_row_idx = len(_SHEET_PREAMBLE)
    migrated_existing = grid[header_row_idx + 1]
    assert migrated_existing[COL_PRODUCT - 1] == "DudeRobe"
    assert migrated_existing[COL_STATUS - 1] == "Edited"          # manual data, untouched
    assert migrated_existing[COL_EDITED_FOLDER - 1] == "https://drive/some-folder"
    assert migrated_existing[COL_NOTES - 1] == "do not touch this"
    new_row = ws.appended[0]
    assert new_row[COL_CREATOR - 1] == "@newperson"
    assert new_row[COL_BRAND - 1] == "DudeRobe"
    assert new_row[COL_PRODUCT - 1] == "DudeRobe"


# ---------- ensure_status_dropdown: the reported missing dropdown ----------

def test_ensure_status_dropdown_sets_the_right_options_on_the_right_column():
    """The actual reported gap: a new Tracker sheet had no dropdown on
    Status at all. Must set ONE_OF_LIST validation, with exactly the
    three documented options, scoped to the Status column specifically
    — not some other column shifted by the recent reorder."""
    ws = FakeWorksheet(_SHEET_PREAMBLE + [_header_row()])
    ensure_status_dropdown(ws, last_data_row=5)

    assert len(ws.spreadsheet.batch_update_calls) == 1
    request = ws.spreadsheet.batch_update_calls[0]["requests"][0]["setDataValidation"]
    assert request["range"]["startColumnIndex"] == COL_STATUS - 1
    assert request["range"]["endColumnIndex"] == COL_STATUS
    values = [v["userEnteredValue"] for v in request["rule"]["condition"]["values"]]
    assert values == STATUS_OPTIONS


def test_ensure_status_dropdown_range_covers_real_data_plus_a_small_lookahead_not_thousands_of_rows():
    """The actual reported concern: a blanket range covering hundreds
    or thousands of empty rows is wasteful and unnecessary. The range
    must track the real last-data-row, with a small, bounded lookahead
    — nowhere near 1000."""
    ws = FakeWorksheet(_SHEET_PREAMBLE + [_header_row()])
    ensure_status_dropdown(ws, last_data_row=5)
    request = ws.spreadsheet.batch_update_calls[0]["requests"][0]["setDataValidation"]
    assert request["range"]["endRowIndex"] < 100
    assert request["range"]["endRowIndex"] > 5  # still covers a handful of rows ahead


def test_ensure_status_dropdown_is_safe_to_call_repeatedly():
    """Unlike ensure_tracker_header, this has no "already exists" check
    at all — re-setting the same validation rule must be harmless, not
    something that needs guarding against."""
    ws = FakeWorksheet(_SHEET_PREAMBLE + [_header_row()])
    ensure_status_dropdown(ws, last_data_row=5)
    ensure_status_dropdown(ws, last_data_row=6)
    assert len(ws.spreadsheet.batch_update_calls) == 2  # both succeed, neither raises


# ---------- ensure_status_colors: the actual requested color-coding ----------

def test_ensure_status_colors_adds_three_rules_with_the_right_colors():
    ws = FakeWorksheet(_SHEET_PREAMBLE + [_header_row()])
    ensure_status_colors(ws, last_data_row=5)

    rules = ws.spreadsheet._conditional_formats
    assert len(rules) == 3
    by_status = {r["booleanRule"]["condition"]["values"][0]["userEnteredValue"]: r for r in rules}
    assert by_status["Not edited"]["booleanRule"]["format"]["backgroundColor"] == STATUS_COLORS["Not edited"]
    assert by_status["Edited"]["booleanRule"]["format"]["backgroundColor"] == STATUS_COLORS["Edited"]
    assert by_status["Live"]["booleanRule"]["format"]["backgroundColor"] == STATUS_COLORS["Live"]


def test_ensure_status_colors_never_stacks_duplicates_on_repeated_calls():
    """The real risk with conditional format rules specifically —
    unlike data validation, re-adding without first deleting would
    pile up duplicate rules indefinitely, one set per sync run."""
    ws = FakeWorksheet(_SHEET_PREAMBLE + [_header_row()])
    ensure_status_colors(ws, last_data_row=5)
    ensure_status_colors(ws, last_data_row=10)
    ensure_status_colors(ws, last_data_row=15)
    assert len(ws.spreadsheet._conditional_formats) == 3  # still exactly 3, not 9


def test_ensure_status_colors_updates_the_range_on_a_later_call():
    ws = FakeWorksheet(_SHEET_PREAMBLE + [_header_row()])
    ensure_status_colors(ws, last_data_row=5)
    ensure_status_colors(ws, last_data_row=50)
    ranges = [r["ranges"][0]["endRowIndex"] for r in ws.spreadsheet._conditional_formats]
    assert all(end_row > 50 for end_row in ranges)


def test_sync_once_sets_both_the_dropdown_and_the_colors_on_every_run():
    tasks = [_asana_task("111", "Rights Secured", creator="@x", product="DudeRobe")]
    ws = FakeWorksheet(_SHEET_PREAMBLE + [_header_row()])
    sync_once(tasks, ws, FakeDriveService({}), RAW_FOLDER_ID, TIKTOK_FOLDER_ID)
    assert len(ws.spreadsheet.batch_update_calls) == 2
    assert len(ws.spreadsheet._conditional_formats) == 3


def test_sync_once_range_covers_only_real_rows_not_a_blanket_thousand():
    """End-to-end version of the reported concern: a brand new sheet
    with one new row must get a dropdown/color range sized to that one
    row plus a small lookahead, not a fixed 1000+."""
    tasks = [_asana_task("111", "Rights Secured", creator="@x", product="DudeRobe")]
    ws = FakeWorksheet([])
    sync_once(tasks, ws, FakeDriveService({}), RAW_FOLDER_ID, TIKTOK_FOLDER_ID)
    dropdown_request = ws.spreadsheet.batch_update_calls[0]["requests"][0]["setDataValidation"]
    assert dropdown_request["range"]["endRowIndex"] < 50


# ---------- ensure_tracker_header: the actual reported bug ----------

def test_ensure_tracker_header_creates_it_on_a_brand_new_empty_sheet():
    """The actual reported production bug: a fresh Tracker sheet, set
    up for the first time with nothing in it at all, crashed instead
    of being usable. Must create the header automatically."""
    ws = FakeWorksheet([])
    created, header_row = ensure_tracker_header(ws)
    assert created is True
    assert header_row == 1
    assert ws.get_all_values()[0] == SHEET_COLUMNS


def test_ensure_tracker_header_does_nothing_when_one_already_exists():
    """Must never touch the agency's existing, already-set-up Tracker
    — title row, description row, stats row, and all — just because
    this check runs on every sync."""
    ws = FakeWorksheet(_SHEET_PREAMBLE + [_header_row()])
    original = ws.get_all_values()
    created, header_row = ensure_tracker_header(ws)
    assert created is False
    assert header_row == len(_SHEET_PREAMBLE) + 1  # the real header position, after the preamble rows
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
    assert grid[1][1] == "@newcreator"  # column 0 is now Asana Task GID
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
    assert row[0] == "'111"  # Asana Task GID — apostrophe-prefixed to force text, never scientific notation
    assert row[1] == "@newcreator"
    assert row[2] == "DudeRobe"
    assert row[6] == "4/1/2027"


def test_sync_once_leaves_status_edited_folder_and_notes_blank_on_a_new_row():
    """The explicit instruction: Status, Edited folder, and Notes are
    never set by this sync — not even a default value."""
    tasks = [_asana_task("111", "Rights Secured", creator="@x", product="DudeRobe")]
    ws = FakeWorksheet(_SHEET_PREAMBLE + [_header_row()])
    summary = sync_once(tasks, ws, FakeDriveService({}), RAW_FOLDER_ID, TIKTOK_FOLDER_ID)
    assert summary["new_rows"] == 1
    row = ws.appended[0]
    assert row[7] == ""  # Status
    assert row[8] == ""  # Edited folder
    assert row[9] == ""  # Notes


def test_sync_once_skips_a_task_already_in_the_tracker():
    tasks = [_asana_task("111", "Rights Secured", creator="@existing", product="DudeRobe")]
    ws = FakeWorksheet(_SHEET_PREAMBLE + [_header_row(),
                        ["111", "@existing", "DudeRobe", "DudeRobe", "", "", "3/1/2027", "Not edited", "", ""]])
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
    assert "HYPERLINK" in row[4]  # Tiktok
    assert "@x_Tiktok" in row[4]
    assert "raw1" in row[4] or "drive.google.com/file/d/tt1" in row[4]
    assert "HYPERLINK" in row[5]  # Raw
    assert "@x_Raw" in row[5]


def test_sync_once_leaves_tiktok_and_raw_blank_when_nothing_matches():
    tasks = [_asana_task("111", "Rights Secured", creator="@nobody", product="DudeRobe")]
    ws = FakeWorksheet(_SHEET_PREAMBLE + [_header_row()])
    summary = sync_once(tasks, ws, FakeDriveService({}), RAW_FOLDER_ID, TIKTOK_FOLDER_ID)
    row = ws.appended[0]
    assert row[4] == ""
    assert row[5] == ""
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
    assert row[5] == ""  # Raw stays blank
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
    assert ws.appended[0][4] == ""  # Tiktok stays blank — the folder must be excluded


def test_sync_once_raw_folder_does_match_a_folder_for_multi_clip_creators():
    tasks = [_asana_task("111", "Rights Secured", creator="@x", product="DudeRobe")]
    ws = FakeWorksheet(_SHEET_PREAMBLE + [_header_row()])
    items_by_folder = {
        RAW_FOLDER_ID: [_drive_item("DudeRobe - @x – DudeRobe [111]", "folder1", is_folder=True)],
    }
    summary = sync_once(tasks, ws, FakeDriveService(items_by_folder), RAW_FOLDER_ID, TIKTOK_FOLDER_ID)
    assert summary["new_rows"] == 1
    assert "folder1" in ws.appended[0][5]  # Raw


# ---------- duplicate creator handling ----------

def test_sync_once_assigns_suffix_for_a_creator_with_two_rights_secured_tasks_in_one_run():
    tasks = [
        _asana_task("100", "Rights Secured", creator="@ksmshaw", product="DudeRobe"),
        _asana_task("200", "Rights Secured", creator="@ksmshaw", product="DudeRobe"),
    ]
    ws = FakeWorksheet(_SHEET_PREAMBLE + [_header_row()])
    sync_once(tasks, ws, FakeDriveService({}), RAW_FOLDER_ID, TIKTOK_FOLDER_ID)
    labels = sorted(r[1] for r in ws.appended)  # column 1 = Creator (GID now leads at column 0)
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
                        ["100", "@2.fit.bros", "DudeRobe", "DudeRobe", "", "", "3/1/2027", "", "", ""]])
    summary = sync_once(tasks, ws, FakeDriveService({}), RAW_FOLDER_ID, TIKTOK_FOLDER_ID)
    assert summary["new_rows"] == 1
    assert ws.appended[0][1] == "@2.fit.bros_2"


# ---------- filling in blank cells on existing rows ----------

def test_sync_once_fills_in_raw_for_an_existing_row_once_content_appears_later():
    tasks = [_asana_task("111", "Rights Secured", creator="@x", product="DudeRobe")]
    ws = FakeWorksheet(_SHEET_PREAMBLE + [_header_row(),
                        ["111", "@x", "DudeRobe", "DudeRobe", "", "", "3/1/2027", "Not edited", "", ""]])
    items_by_folder = {RAW_FOLDER_ID: [_drive_item("DudeRobe - @x – DudeRobe [111].mov", "raw1")]}

    summary = sync_once(tasks, ws, FakeDriveService(items_by_folder), RAW_FOLDER_ID, TIKTOK_FOLDER_ID)

    assert summary["new_rows"] == 0
    assert summary["filled_in"] == 1
    assert ws.updated_cells == [(6, 6, ws.updated_cells[0][2])]  # row 6 (header is row 5), col 6 = Raw
    assert "HYPERLINK" in ws.updated_cells[0][2]
    assert "@x_Raw" in ws.updated_cells[0][2]


def test_sync_once_never_overwrites_a_cell_that_already_has_a_link():
    """Critical safety property: once Raw or Tiktok has something in
    it, this sync must never touch that cell again on a later run,
    even if Drive listing now shows something different."""
    existing_link = '=HYPERLINK("https://drive.google.com/file/d/old/view", "@x_Raw")'
    tasks = [_asana_task("111", "Rights Secured", creator="@x", product="DudeRobe")]
    ws = FakeWorksheet(_SHEET_PREAMBLE + [_header_row(),
                        ["111", "@x", "DudeRobe", "DudeRobe", "", existing_link, "3/1/2027", "", "", ""]])
    items_by_folder = {RAW_FOLDER_ID: [_drive_item("DudeRobe - @x – DudeRobe [111].mov", "new_raw")]}

    summary = sync_once(tasks, ws, FakeDriveService(items_by_folder), RAW_FOLDER_ID, TIKTOK_FOLDER_ID)

    assert summary["filled_in"] == 0
    assert ws.updated_cells == []


def test_sync_once_never_touches_status_or_edited_folder_on_an_existing_row():
    tasks = [_asana_task("111", "Rights Secured", creator="@x", product="DudeRobe")]
    ws = FakeWorksheet(_SHEET_PREAMBLE + [_header_row(),
                        ["111", "@x", "DudeRobe", "DudeRobe", "", "", "3/1/2027", "Edited", "some-link", "a note"]])
    items_by_folder = {RAW_FOLDER_ID: [_drive_item("DudeRobe - @x – DudeRobe [111].mov", "raw1")]}

    sync_once(tasks, ws, FakeDriveService(items_by_folder), RAW_FOLDER_ID, TIKTOK_FOLDER_ID)

    touched_cols = {col for (_row, col, _val) in ws.updated_cells}
    assert 7 not in touched_cols  # Status
    assert 8 not in touched_cols  # Edited folder
    assert 9 not in touched_cols  # Notes


# ---------- Rights Secured assignee swap: orchestration ----------

import ugc_tracker_sync
from ugc_tracker_sync import reassign_rights_secured_tasks, _run_reassign_step

OLD_EMAIL, NEW_EMAIL = "old.person@example.com", "new.person@example.com"


def _rs_task(gid, assignee_gid="100", section="Rights Secured", completed=False):
    return {"gid": gid, "memberships": [{"section": {"name": section}}], "completed": completed,
            "assignee": ({"gid": assignee_gid, "email": "x@x.com"} if assignee_gid else None)}


class FakeAsanaModule:
    """Stands in for asana_client: records every call, can be told to fail."""

    def __init__(self, users=None, fail_updates_for=(), fail_user=None):
        self.users = users or {OLD_EMAIL: {"gid": "100"}, NEW_EMAIL: {"gid": "200"}}
        self.fail_updates_for = set(fail_updates_for)
        self.fail_user = fail_user
        self.get_user_calls, self.update_calls = [], []

    def get_user(self, identifier, token, label="that user"):
        self.get_user_calls.append(identifier)
        if self.fail_user == identifier:
            raise RuntimeError(f"Asana could not find or read {label} (HTTP 404).")
        return self.users[identifier]

    def update_task(self, task_gid, patch, token):
        self.update_calls.append((task_gid, patch))
        if task_gid in self.fail_updates_for:
            raise RuntimeError("boom")


def test_swap_is_off_and_makes_no_asana_call_unless_both_emails_are_given():
    for frm, to in [("", ""), (OLD_EMAIL, ""), ("", NEW_EMAIL), ("  ", "  ")]:
        fake = FakeAsanaModule()
        result = reassign_rights_secured_tasks([_rs_task("1")], "tok", frm, to, asana=fake)
        assert result["enabled"] is False
        assert fake.get_user_calls == [] and fake.update_calls == []


def test_swap_is_off_when_both_emails_are_the_same_person_in_any_casing():
    fake = FakeAsanaModule()
    result = reassign_rights_secured_tasks([_rs_task("1")], "tok", OLD_EMAIL, OLD_EMAIL.upper(), asana=fake)
    assert result["enabled"] is False and fake.update_calls == []


def test_swap_is_off_when_two_different_emails_resolve_to_the_same_asana_user():
    fake = FakeAsanaModule(users={OLD_EMAIL: {"gid": "100"}, NEW_EMAIL: {"gid": "100"}})
    result = reassign_rights_secured_tasks([_rs_task("1")], "tok", OLD_EMAIL, NEW_EMAIL, asana=fake)
    assert result["enabled"] is False and fake.update_calls == []


def test_swap_changes_only_the_matching_tasks_with_the_new_persons_id():
    tasks = [_rs_task("a"), _rs_task("b"), _rs_task("c", assignee_gid="999"), _rs_task("d", assignee_gid=None),
             _rs_task("e", section="Negotiating"), _rs_task("f", completed=True)]
    fake = FakeAsanaModule()
    result = reassign_rights_secured_tasks(tasks, "tok", OLD_EMAIL, NEW_EMAIL, asana=fake)
    assert fake.update_calls == [("a", {"assignee": "200"}), ("b", {"assignee": "200"})]
    assert result["reassigned"] == 2 and result["failed"] == 0
    assert (result["left_other_assignee"], result["left_unassigned"], result["skipped_completed"]) == (1, 1, 1)
    assert fake.get_user_calls == [OLD_EMAIL, NEW_EMAIL]  # each person looked up once, not per task


def test_one_task_failing_does_not_stop_the_others_and_is_reported(capsys):
    fake = FakeAsanaModule(fail_updates_for={"b"})
    result = reassign_rights_secured_tasks([_rs_task("a"), _rs_task("b"), _rs_task("c")], "tok",
                                            OLD_EMAIL, NEW_EMAIL, asana=fake)
    assert [c[0] for c in fake.update_calls] == ["a", "b", "c"]   # c was still attempted after b failed
    assert result["reassigned"] == 2 and result["failed"] == 1 and result["failed_task_gids"] == ["b"]
    assert "could not update task b" in capsys.readouterr().out


def test_an_unresolvable_user_raises_before_any_task_is_changed():
    fake = FakeAsanaModule(fail_user=NEW_EMAIL)
    with pytest.raises(RuntimeError):
        reassign_rights_secured_tasks([_rs_task("a")], "tok", OLD_EMAIL, NEW_EMAIL, asana=fake)
    assert fake.update_calls == []


def test_running_it_twice_changes_nothing_the_second_time():
    tasks = [_rs_task("a"), _rs_task("b")]
    fake = FakeAsanaModule()
    reassign_rights_secured_tasks(tasks, "tok", OLD_EMAIL, NEW_EMAIL, asana=fake)
    for gid, change in fake.update_calls:                 # apply what Asana would now hold
        next(t for t in tasks if t["gid"] == gid)["assignee"] = {"gid": change["assignee"], "email": "n@x.com"}
    fake2 = FakeAsanaModule()
    result = reassign_rights_secured_tasks(tasks, "tok", OLD_EMAIL, NEW_EMAIL, asana=fake2)
    assert fake2.update_calls == [] and result["reassigned"] == 0


# ---- the report line, and the exit-code contribution ----

def test_report_when_unconfigured_says_off_and_is_not_a_failure(capsys):
    fake = FakeAsanaModule()
    assert _run_reassign_step([_rs_task("1")], "tok", "", "", asana=fake) is False
    out = capsys.readouterr().out
    assert "off" in out and ugc_tracker_sync.REASSIGN_FROM_ENV in out
    assert fake.update_calls == []


def test_report_when_only_one_email_is_set_is_loud_and_a_failure(capsys):
    """Half a configuration is a mistake worth a red run — otherwise the
    swap would sit silently off while looking set up."""
    assert _run_reassign_step([_rs_task("1")], "tok", OLD_EMAIL, "", asana=FakeAsanaModule()) is True
    assert "only one of" in capsys.readouterr().out


def test_report_on_success_gives_exact_counts_and_is_not_a_failure(capsys):
    fake = FakeAsanaModule()
    failed = _run_reassign_step([_rs_task("a"), _rs_task("b", assignee_gid="999"), _rs_task("c", assignee_gid=None)],
                                 "tok", OLD_EMAIL, NEW_EMAIL, asana=fake)
    assert failed is False
    assert ("3 task(s) in Rights Secured: swapped 1, failed 0. Left alone: 1 assigned to someone else, "
            "1 unassigned, 0 completed.") in capsys.readouterr().out


def test_report_on_a_task_failure_is_a_failed_run():
    assert _run_reassign_step([_rs_task("a")], "tok", OLD_EMAIL, NEW_EMAIL,
                               asana=FakeAsanaModule(fail_updates_for={"a"})) is True


def test_report_when_a_user_cannot_be_found_is_a_failed_run_that_changed_nothing(capsys):
    fake = FakeAsanaModule(fail_user=OLD_EMAIL)
    assert _run_reassign_step([_rs_task("a")], "tok", OLD_EMAIL, NEW_EMAIL, asana=fake) is True
    assert "FAILED before changing anything" in capsys.readouterr().out
    assert fake.update_calls == []


def test_no_report_line_ever_prints_an_email_address(capsys):
    """This repository's Actions logs are public."""
    for kwargs in (dict(frm="", to=""), dict(frm=OLD_EMAIL, to=""), dict(frm=OLD_EMAIL, to=NEW_EMAIL),
                   dict(frm=OLD_EMAIL, to=OLD_EMAIL)):
        _run_reassign_step([_rs_task("a")], "tok", kwargs["frm"], kwargs["to"], asana=FakeAsanaModule())
    _run_reassign_step([_rs_task("a")], "tok", OLD_EMAIL, NEW_EMAIL, asana=FakeAsanaModule(fail_user=NEW_EMAIL))
    out = capsys.readouterr().out
    assert "@" not in out and "old.person" not in out and "new.person" not in out


# ---- main(): where the step sits in a real run ----

def _run_main(monkeypatch, env, sync_result=None, tasks=None, fake_asana=None, sync_raises=None):
    """Runs the real main() with every network edge replaced, returning
    (exit_code, ordered log of what happened)."""
    import asana_client
    log = []
    for k, v in {"UGC_TRACKER_SHEET_ID": "S", "UGC_TRACKER_RAW_FOLDER_ID": "R", "UGC_TRACKER_TIKTOK_FOLDER_ID": "T",
                 "UGC_TRACKER_ASANA_PROJECT_GID": "P", "GOOGLE_SERVICE_ACCOUNT_JSON": '{"client_email": "sa@x"}',
                 "ASANA_TOKEN": "tok"}.items():
        monkeypatch.setenv(k, v)
    for k in (ugc_tracker_sync.REASSIGN_FROM_ENV, ugc_tracker_sync.REASSIGN_TO_ENV):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    class FakeSpreadsheet:
        def worksheet(self, name):
            return object()

    monkeypatch.setattr(sys, "argv", ["ugc_tracker_sync.py"])
    monkeypatch.setattr(ugc_tracker_sync, "_connect_sheets", lambda sid, info: (FakeSpreadsheet(), object()))

    def fake_sync_once(*a, **k):
        log.append("sheet sync")
        if sync_raises:
            raise sync_raises
        return sync_result or {"new_rows": 0, "filled_in": 0, "ambiguous": 0}

    monkeypatch.setattr(ugc_tracker_sync, "sync_once", fake_sync_once)
    monkeypatch.setattr(asana_client, "get_all_project_tasks", lambda gid, tok: tasks if tasks is not None else [_rs_task("a")])
    fake_asana = fake_asana or FakeAsanaModule()
    monkeypatch.setattr(asana_client, "get_user", lambda *a, **k: (log.append("get_user"), fake_asana.get_user(*a, **k))[1])
    monkeypatch.setattr(asana_client, "update_task", lambda *a, **k: (log.append("update_task"), fake_asana.update_task(*a, **k))[1])
    return ugc_tracker_sync.main(), log


def test_main_without_the_secrets_never_touches_asana_and_exits_clean(monkeypatch):
    code, log = _run_main(monkeypatch, env={})
    assert code == 0 and log == ["sheet sync"]


def test_main_runs_the_swap_only_after_the_sheet_sync_has_finished(monkeypatch):
    code, log = _run_main(monkeypatch, env={ugc_tracker_sync.REASSIGN_FROM_ENV: OLD_EMAIL,
                                              ugc_tracker_sync.REASSIGN_TO_ENV: NEW_EMAIL})
    assert code == 0
    assert log == ["sheet sync", "get_user", "get_user", "update_task"]


def test_main_a_swap_failure_never_undoes_or_blocks_the_sheet_sync_but_fails_the_run(monkeypatch, capsys):
    code, log = _run_main(monkeypatch, env={ugc_tracker_sync.REASSIGN_FROM_ENV: OLD_EMAIL,
                                              ugc_tracker_sync.REASSIGN_TO_ENV: NEW_EMAIL},
                          fake_asana=FakeAsanaModule(fail_updates_for={"a"}))
    assert code == 1 and log[0] == "sheet sync"
    assert "New rows:" in capsys.readouterr().out   # the sheet sync's own result was still reported


def test_main_does_not_attempt_the_swap_when_the_sheet_sync_itself_failed(monkeypatch):
    with pytest.raises(RuntimeError, match="sheet down"):
        _run_main(monkeypatch, env={ugc_tracker_sync.REASSIGN_FROM_ENV: OLD_EMAIL,
                                    ugc_tracker_sync.REASSIGN_TO_ENV: NEW_EMAIL},
                  sync_raises=RuntimeError("sheet down"))
