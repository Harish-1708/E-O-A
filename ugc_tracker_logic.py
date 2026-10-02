"""Pure logic for the UGC Video Edits Tracker sync — a standalone
automation, deliberately separate from outreach.py and the Streamlit
app. Watches the Creator Outreach Asana project's Rights Secured
section and keeps the UGC Video Edits Tracker Google Sheet current:
one row per video, Creator/Brand/Rights expiration filled in
immediately, Tiktok/Raw links filled in once a matching file (or
folder — some creators send multiple short clips, stored as a
subfolder) actually appears in Drive.

Nothing in this module touches Status, "Edited folder", or Notes —
those three columns are permanently the editing team's own, by
design. The only thing this module ever writes automatically is
Creator, Brand, Rights expiration, Tiktok, and Raw.

Everything here is pure (no Asana/Drive/Sheets API calls) so it can be
tested without live credentials. A thin orchestration script wires
this to the real APIs.
"""
import re
from typing import Dict, List, Optional

RIGHTS_SECURED_SECTION_NAME = "Rights Secured"

# Matches a bracketed Asana Task GID anywhere in a file or folder name,
# e.g. "DudeRobe - @ksmshaw – DudeRobe [1218941738388881].mov" or a
# folder named "DudeRobe - @sandycheeks92203 [1218598785333508]".
_TASK_GID_PATTERN = re.compile(r"\[(\d+)\]")

# Collapses the punctuation variance actually observed in this Drive
# folder — hyphens, en dashes, em dashes, underscores all used
# interchangeably as separators, plus inconsistent spacing — down to
# single spaces, so "DudeRobe - @foo – Bar" and "DudeRobe_@foo_Bar"
# normalize identically.
_SEPARATOR_PATTERN = re.compile(r"[-–—_]+")
_WHITESPACE_PATTERN = re.compile(r"\s+")


def extract_task_gid_from_name(name: str) -> Optional[str]:
    """The bracketed Task GID from a file or folder name, if present —
    an exact, zero-ambiguity identifier once files are named this way.
    Returns None for anything still using the old naming (no bracket),
    never raises on a malformed or absent bracket."""
    match = _TASK_GID_PATTERN.search(name or "")
    return match.group(1) if match else None


def normalize_for_matching(text: str) -> str:
    """Lowercases and collapses punctuation/whitespace variance so
    "DudeRobe - @foo – Bar.mp4" and "duderobe_@foo_bar" compare equal
    after this. Deliberately permissive — the goal is tolerating real
    inconsistent naming, not strict validation."""
    text = (text or "").lower()
    text = _SEPARATOR_PATTERN.sub(" ", text)
    text = _WHITESPACE_PATTERN.sub(" ", text)
    return text.strip()


def _strip_extension(filename: str) -> str:
    """Drops a single trailing extension if present — deliberately
    only one, since some files in this Drive have been seen with a
    doubled extension (e.g. "....mp4.mov"), and stripping twice would
    risk eating part of a genuine name."""
    idx = filename.rfind(".")
    if idx > 0 and len(filename) - idx <= 6:  # plausible extension length
        return filename[:idx]
    return filename


class MatchResult:
    """status is one of:
    - "exact_id": a single item's bracketed Task GID matched exactly —
      fully confident, use it.
    - "fuzzy": no ID match anywhere, but exactly one item's normalized
      name contains both the creator handle and the product —
      confident enough to use, unless a stricter policy is wanted.
    - "ambiguous": more than one candidate, by ID or by fuzzy text —
      never guessed; candidates lists what was found, for logging only
      (never written to the Sheet — Notes stays fully manual).
    - "none": nothing found at all.
    item is the single matched Drive item ({"name", "id", "mimeType",
    "webViewLink"}) when status is "exact_id" or "fuzzy", else None.
    """

    def __init__(self, status: str, item: Optional[Dict] = None, candidates: Optional[List[Dict]] = None):
        self.status = status
        self.item = item
        self.candidates = candidates or []

    def __repr__(self):
        return f"MatchResult(status={self.status!r}, item={self.item!r}, candidates={self.candidates!r})"

    def __eq__(self, other):
        if not isinstance(other, MatchResult):
            return NotImplemented
        return (self.status, self.item, self.candidates) == (other.status, other.item, other.candidates)


def find_drive_match(task_gid: str, creator_handle: str, product: str, drive_items: List[Dict]) -> MatchResult:
    """Finds the Raw or TikTok file/folder for one Rights Secured
    video, among everything currently listed in the relevant Drive
    folder (files AND folders both considered — see module docstring).

    Tries the exact bracketed Task GID first; only falls back to
    fuzzy creator+product text matching for items still using the old
    naming convention. Never guesses when more than one candidate is
    plausible — returns "ambiguous" instead, which the caller logs
    (GitHub Actions output only) rather than writing anywhere."""
    id_matches = [item for item in drive_items if extract_task_gid_from_name(item.get("name", "")) == task_gid]
    if len(id_matches) == 1:
        return MatchResult(status="exact_id", item=id_matches[0])
    if len(id_matches) > 1:
        return MatchResult(status="ambiguous", candidates=id_matches)

    handle_token = normalize_for_matching(creator_handle).lstrip("@")
    product_token = normalize_for_matching(product)
    if not handle_token:
        return MatchResult(status="none")

    fuzzy_matches = []
    for item in drive_items:
        normalized_name = normalize_for_matching(_strip_extension(item.get("name", "")))
        if handle_token in normalized_name and (not product_token or product_token in normalized_name):
            fuzzy_matches.append(item)
    if len(fuzzy_matches) == 1:
        return MatchResult(status="fuzzy", item=fuzzy_matches[0])
    if len(fuzzy_matches) > 1:
        return MatchResult(status="ambiguous", candidates=fuzzy_matches)
    return MatchResult(status="none")


def build_hyperlink_formula(url: str, display_text: str) -> str:
    """=HYPERLINK(url, display_text) — matching exactly how the
    existing Video and Edited folder columns in this Sheet already
    look. Escapes an embedded double-quote in either part (Sheets
    formula syntax), though neither a Drive URL nor a creator handle
    should ever actually contain one."""
    safe_url = url.replace('"', '""')
    safe_text = display_text.replace('"', '""')
    return f'=HYPERLINK("{safe_url}", "{safe_text}")'


def assign_duplicate_suffixes(creator_handles: List[str]) -> List[str]:
    """The SAME creator can have more than one Rights Secured video —
    confirmed directly (e.g. two separate @ksmshaw tasks). The first
    occurrence of a handle keeps it bare; the second gets "_2", the
    third "_3", and so on — applied consistently, unlike the one
    existing precedent in this Sheet (@2.fit.bros / @2.fit.bros_2)
    that was never applied to the OTHER duplicate already in the
    Sheet (@ksmshaw appears twice with no distinguishing suffix at
    all). Order is exactly the order handles are passed in — the
    caller decides what "first" means (e.g. task creation order)."""
    seen_counts: Dict[str, int] = {}
    labels = []
    for handle in creator_handles:
        seen_counts[handle] = seen_counts.get(handle, 0) + 1
        count = seen_counts[handle]
        labels.append(handle if count == 1 else f"{handle}_{count}")
    return labels


def extract_rights_secured_tasks(asana_tasks: List[Dict]) -> List[Dict]:
    """Filters a full list of Asana tasks (as returned by the Asana API
    with memberships.section.name and custom_fields included) down to
    just those CURRENTLY in Rights Secured, pulling out exactly the
    fields this sync needs. A task missing Creator or Rights
    Expiration is still returned — those gaps are visible in the
    Sheet itself (a blank cell) rather than silently dropped, since
    silently skipping a real Rights Secured video would be worse than
    showing an incomplete row for someone to notice and fix in Asana."""
    result = []
    for task in asana_tasks:
        section_names = {m.get("section", {}).get("name") for m in task.get("memberships", [])}
        if RIGHTS_SECURED_SECTION_NAME not in section_names:
            continue
        fields = {cf.get("name"): cf.get("display_value") for cf in task.get("custom_fields", [])}
        result.append({
            "task_gid": task.get("gid", ""),
            "creator": (fields.get("Creator") or "").strip(),
            "product": (fields.get("Product") or "").strip(),
            "rights_expiration": (fields.get("Rights Expiration") or "").strip(),
            "permalink_url": task.get("permalink_url", ""),
        })
    return result


def rights_expiration_to_sheet_date(iso_datetime: str) -> str:
    """Asana returns dates as full ISO datetimes
    ("2027-04-01T00:00:00.000Z"); the Sheet shows plain
    "4/1/2027"-style dates. Returns "" unchanged for a blank/missing
    value rather than raising, since an unset Rights Expiration is a
    real, valid state (a gap to notice in Asana, not a sync error)."""
    if not iso_datetime:
        return ""
    date_part = iso_datetime.split("T")[0]
    try:
        year, month, day = date_part.split("-")
        return f"{int(month)}/{int(day)}/{int(year)}"
    except ValueError:
        return ""
