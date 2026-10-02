import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ugc_tracker_logic import (
    extract_task_gid_from_name, normalize_for_matching, find_drive_match,
    build_hyperlink_formula, assign_duplicate_suffixes, extract_rights_secured_tasks,
    rights_expiration_to_sheet_date,
)


# ---------- extract_task_gid_from_name ----------

def test_extract_task_gid_from_name_finds_bracketed_id():
    assert extract_task_gid_from_name("DudeRobe - @ksmshaw – DudeRobe [1218941738388881].mov") == "1218941738388881"


def test_extract_task_gid_from_name_works_on_a_folder_name_too():
    assert extract_task_gid_from_name("DudeRobe - @sandycheeks92203 [1218598785333508]") == "1218598785333508"


def test_extract_task_gid_from_name_returns_none_for_old_naming():
    assert extract_task_gid_from_name("DudeRobe - @thatdadofthree – DudeRobe.mp4") is None


def test_extract_task_gid_from_name_never_raises_on_empty():
    assert extract_task_gid_from_name("") is None
    assert extract_task_gid_from_name(None) is None


# ---------- normalize_for_matching ----------

def test_normalize_handles_every_separator_variant_actually_seen_in_drive():
    # Real variance found directly: hyphen, en dash, underscore, double space.
    assert normalize_for_matching("DudeRobe - @ksmshaw – DudeRobe") == \
        normalize_for_matching("DudeRobe  @ksmshaw - DudeRobe")
    assert normalize_for_matching("DudeRobe_@gabrielwhittakerr_HookA") == "duderobe @gabrielwhittakerr hooka"


# ---------- find_drive_match: exact ID match ----------

def test_find_drive_match_exact_id_wins_even_with_a_fuzzy_candidate_present():
    items = [
        {"name": "DudeRobe - @ksmshaw – DudeRobe [1218941738388881].mov", "id": "f1"},
        {"name": "DudeRobe - @ksmshaw – DudeRobe.mov", "id": "f2"},  # old-style, would also fuzzy-match
    ]
    result = find_drive_match("1218941738388881", "@ksmshaw", "DudeRobe", items)
    assert result.status == "exact_id"
    assert result.item["id"] == "f1"


def test_find_drive_match_exact_id_works_for_a_folder_not_just_a_file():
    items = [{"name": "DudeRobe - @sandycheeks92203 [1218598785333508]", "id": "folder1",
              "mimeType": "application/vnd.google-apps.folder"}]
    result = find_drive_match("1218598785333508", "@sandycheeks92203", "DudeRobe", items)
    assert result.status == "exact_id"
    assert result.item["id"] == "folder1"


# ---------- find_drive_match: the actual reported ambiguous case ----------

def test_find_drive_match_the_real_ksmshaw_case_is_ambiguous_not_guessed():
    """The exact real-world case found directly in Drive: two raw
    files for the same creator, neither one bracketed with a Task
    GID yet (old naming). Must NOT guess which belongs to which of
    the creator's two Rights Secured tasks — must report ambiguous."""
    items = [
        {"name": "DudeRobe - @ksmshaw – DudeRobe.mov", "id": "f1"},
        {"name": "DudeRobe  @ksmshaw - DudeRobe.mov", "id": "f2"},
    ]
    result = find_drive_match("1218941738388881", "@ksmshaw", "DudeRobe", items)
    assert result.status == "ambiguous"
    assert {c["id"] for c in result.candidates} == {"f1", "f2"}


def test_find_drive_match_fuzzy_match_succeeds_when_genuinely_unique():
    items = [
        {"name": "DudeRobe - @thatdadofthree – DudeRobe.mp4", "id": "f1"},
        {"name": "DudeRobe - @codycooperfitness – DudeRobe.mp4", "id": "f2"},
    ]
    result = find_drive_match("999", "@thatdadofthree", "DudeRobe", items)
    assert result.status == "fuzzy"
    assert result.item["id"] == "f1"


def test_find_drive_match_distinguishes_by_product_for_same_handle():
    """A creator who did two videos for different product lines
    (confirmed real case: @jo.vall — DudeRobe client, SheRobe
    product) must match on product too, not handle alone."""
    items = [
        {"name": "DudeRobe - @jo.vall – DudeRobe.mp4", "id": "f1"},
        {"name": "DudeRobe - @jo.vall – SheRobe.mp4", "id": "f2"},
    ]
    result = find_drive_match("999", "@jo.vall", "SheRobe", items)
    assert result.status == "fuzzy"
    assert result.item["id"] == "f2"


def test_find_drive_match_double_extension_file_still_matches():
    """Real case found directly: 'DudeRobe - @mrs_smith_gets_real –
    DudeRobe.mp4.mp4' — a doubled extension from how it was uploaded.
    Stripping one extension must still leave enough of the name to
    match correctly."""
    items = [{"name": "DudeRobe - @mrs_smith_gets_real – DudeRobe.mp4.mp4", "id": "f1"}]
    result = find_drive_match("999", "@mrs_smith_gets_real", "DudeRobe", items)
    assert result.status == "fuzzy"
    assert result.item["id"] == "f1"


def test_find_drive_match_none_when_nothing_present():
    result = find_drive_match("999", "@nobody", "DudeRobe", [])
    assert result.status == "none"
    assert result.item is None


def test_find_drive_match_blank_handle_never_matches_everything():
    """A defensive case: a blank creator handle (e.g. Asana's Creator
    field wasn't filled in) must never fall through to matching every
    file in the folder."""
    items = [{"name": "DudeRobe - @someone – DudeRobe.mp4", "id": "f1"}]
    result = find_drive_match("999", "", "DudeRobe", items)
    assert result.status == "none"


# ---------- build_hyperlink_formula ----------

def test_build_hyperlink_formula_matches_requested_display_text_style():
    formula = build_hyperlink_formula("https://drive.google.com/file/d/abc123/view", "@codycooperfitness_Tiktok")
    assert formula == '=HYPERLINK("https://drive.google.com/file/d/abc123/view", "@codycooperfitness_Tiktok")'


def test_build_hyperlink_formula_escapes_embedded_quotes():
    formula = build_hyperlink_formula('https://x.com/"weird"', 'a "label"')
    assert formula == '=HYPERLINK("https://x.com/""weird""", "a ""label""")'


# ---------- assign_duplicate_suffixes ----------

def test_assign_duplicate_suffixes_the_real_ksmshaw_case():
    labels = assign_duplicate_suffixes(["@ksmshaw", "@ksmshaw"])
    assert labels == ["@ksmshaw", "@ksmshaw_2"]


def test_assign_duplicate_suffixes_the_real_2fitbros_case_matches_existing_sheet_convention():
    labels = assign_duplicate_suffixes(["@2.fit.bros", "@2.fit.bros"])
    assert labels == ["@2.fit.bros", "@2.fit.bros_2"]


def test_assign_duplicate_suffixes_three_or_more():
    labels = assign_duplicate_suffixes(["@a", "@a", "@a"])
    assert labels == ["@a", "@a_2", "@a_3"]


def test_assign_duplicate_suffixes_leaves_unique_handles_untouched():
    labels = assign_duplicate_suffixes(["@a", "@b", "@c"])
    assert labels == ["@a", "@b", "@c"]


def test_assign_duplicate_suffixes_does_not_cross_contaminate_different_handles():
    labels = assign_duplicate_suffixes(["@a", "@b", "@a", "@b", "@b"])
    assert labels == ["@a", "@b", "@a_2", "@b_2", "@b_3"]


# ---------- extract_rights_secured_tasks ----------

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


def test_extract_rights_secured_tasks_filters_to_the_right_section_only():
    tasks = [
        _asana_task("1", "Negotiating", creator="@a"),
        _asana_task("2", "Rights Secured", creator="@b", product="DudeRobe",
                     rights_expiration="2027-04-01T00:00:00.000Z"),
        _asana_task("3", "Declined / Dead", creator="@c"),
    ]
    result = extract_rights_secured_tasks(tasks)
    assert len(result) == 1
    assert result[0]["task_gid"] == "2"
    assert result[0]["creator"] == "@b"
    assert result[0]["product"] == "DudeRobe"
    assert result[0]["rights_expiration"] == "2027-04-01T00:00:00.000Z"


def test_extract_rights_secured_tasks_keeps_a_task_with_missing_fields_visible_not_dropped():
    """A real Rights Secured video with a blank Creator field must
    still produce a row (with a blank Creator cell) rather than being
    silently skipped — the gap belongs in Asana getting noticed, not
    in this sync quietly hiding the video from the editing team."""
    tasks = [_asana_task("1", "Rights Secured")]  # no creator/product/expiration at all
    result = extract_rights_secured_tasks(tasks)
    assert len(result) == 1
    assert result[0]["creator"] == ""


def test_extract_rights_secured_tasks_empty_list_for_no_matches():
    assert extract_rights_secured_tasks([_asana_task("1", "Follow-up")]) == []


# ---------- rights_expiration_to_sheet_date ----------

def test_rights_expiration_to_sheet_date_matches_real_sheet_format():
    assert rights_expiration_to_sheet_date("2027-04-01T00:00:00.000Z") == "4/1/2027"
    assert rights_expiration_to_sheet_date("2027-03-03T00:00:00.000Z") == "3/3/2027"


def test_rights_expiration_to_sheet_date_blank_stays_blank():
    assert rights_expiration_to_sheet_date("") == ""


def test_rights_expiration_to_sheet_date_malformed_input_returns_blank_not_raise():
    assert rights_expiration_to_sheet_date("not-a-date") == ""
