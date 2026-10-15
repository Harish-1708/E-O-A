"""What the Sequences tab shows around the editor: the stage cards, the real placeholders, the live results of a variant, the
'Check before saving' items, the lead the preview uses — and the store that keeps UNSAVED edits safe while you move between
stages and variants.

PURE and READ-ONLY: nothing here saves, sends or changes anything. The checks are ADVICE shown beside the editor; the blocking
validation (validate_variant_content) and every write stay exactly where they were.
"""
import re
from typing import Callable, Dict, Iterable, List, Optional, Tuple

import outreach
from leads_logic import humanize_stage
from performance_logic import format_rate

SUBJECT_LIMIT = 70

# Link shorteners an email filter treats as a red flag. Matched as whole domains, never as a piece of a longer word.
SHORTENER_DOMAINS = ["bit.ly", "tinyurl.com", "t.co", "goo.gl", "ow.ly", "buff.ly", "is.gd", "rebrand.ly", "cutt.ly", "tiny.cc", "shorturl.at", "lnkd.in", "rb.gy"]
_SHORTENER_RE = re.compile(r"(?<![\w.@-])(?:https?://)?(?:www\.)?(" + "|".join(re.escape(d) for d in SHORTENER_DOMAINS) + r")(?![\w-]|\.\w)", re.IGNORECASE)
_STANDARD_PLACEHOLDERS = ["FirstName", "LastName", "CompanyName", "Email"]


# ---------------------------------------------------------------- the stage cards

def _plural(count: int, word: str) -> str:
    return f"{count} {word}" + ("" if count == 1 else "s")


def stage_cards(stages: List[Dict], campaign_cfg_stages: Optional[List[Dict]], variant_count: int) -> List[Dict]:
    """One card per stage: {prefix, title, badge, variants}. The badge is 'Day 0' for the first stage and '+N days' (the wait after the previous
    stage, from the campaign's own settings) for the others; a stage the settings do not know yet gets no badge rather than an invented one."""
    waits = {str(s.get("template_prefix")): s.get("wait_days_after_previous") for s in (campaign_cfg_stages or [])}
    cards = []
    for index, stage in enumerate(stages):
        prefix = str(stage.get("template_prefix") or "")
        wait = waits.get(prefix)
        if index == 0:
            badge = "Day 0"
        elif isinstance(wait, int) and not isinstance(wait, bool) and wait >= 0:
            badge = f"+{_plural(wait, 'day')}"
        else:
            badge = ""
        cards.append({"prefix": prefix, "title": humanize_stage(prefix), "badge": badge, "variants": _plural(variant_count, "variant")})
    return cards


# ---------------------------------------------------------------- placeholders

def placeholder_tokens(leads: List[Dict], system_columns: Iterable[str]) -> List[str]:
    """The placeholders a template can use: the four standard ones, then one for every custom column the leads carry ({{Client}}, {{Product}} …).
    System columns (status, timestamps and so on) are never offered, and neither is any internal column whose name starts with an underscore (the Sheet
    connector adds `_row`, the row number, to every lead)."""
    system = set(system_columns) | set(_STANDARD_PLACEHOLDERS) | {"Company"}
    custom = sorted({str(key) for lead in leads for key in lead if re.fullmatch(r"\w+", str(key)) and not str(key).startswith("_") and str(key) not in system})
    return ["{{" + name + "}}" for name in _STANDARD_PLACEHOLDERS + custom]


def placeholder_problems(text: str, lead: Dict) -> Tuple[List[str], List[str]]:
    """(unknown, blank): placeholder names in `text` that match no column at all (a typo), and names whose value for THIS lead comes out empty."""
    unknown: List[str] = []
    outreach.render_text(text, lead, unknown)
    blank = []
    for name in dict.fromkeys(match.group(1) for match in outreach.PLACEHOLDER_RE.finditer(text)):
        if name in unknown:
            continue
        if name in outreach.TEMPLATE_VARIABLE_MAP:
            value = str(lead.get(outreach.TEMPLATE_VARIABLE_MAP[name]) or "").strip() or outreach.DEFAULT_VALUES.get(name, "")
        else:
            value = str(lead.get(name) or "").strip()
        if not value:
            blank.append(name)
    return list(dict.fromkeys(unknown)), blank


def shortener_hits(text: str) -> List[str]:
    """The link-shortener domains in `text`, lower-case, each once, in the order they appear."""
    return list(dict.fromkeys(match.group(1).lower() for match in _SHORTENER_RE.finditer(text or "")))


# ---------------------------------------------------------------- 'Check before saving'

def check_items(subject: str, body: str, lead: Optional[Dict], is_first_stage: bool) -> List[Dict]:
    """The three advisory checks beside the editor, each {ok, text}. They advise; they never block a save."""
    items = []
    if not lead:
        items.append({"ok": True, "text": "No lead to check the placeholders against"})
    else:
        unknown, blank = placeholder_problems(subject + "\n" + body, lead)
        if unknown:
            items.append({"ok": False, "text": "No column is named " + ", ".join("{{" + n + "}}" for n in unknown)})
        elif blank:
            items.append({"ok": False, "text": "No value for this lead: " + ", ".join("{{" + n + "}}" for n in blank)})
        else:
            items.append({"ok": True, "text": "Every placeholder has a value for this lead"})
    if not subject.strip():
        items.append({"ok": not is_first_stage, "text": "Subject is required for the first stage" if is_first_stage else "Subject is blank, so it continues the previous thread"})
    else:
        shown = outreach.render_text(subject, lead or {})
        length = len(shown)
        items.append({"ok": length <= SUBJECT_LIMIT, "text": f"Subject under {SUBJECT_LIMIT} characters" if length <= SUBJECT_LIMIT else f"Subject is {length} characters (over {SUBJECT_LIMIT})"})
    hits = shortener_hits(subject + "\n" + body)
    items.append({"ok": not hits, "text": "No link-shortener domains" if not hits else "Link shortener found: " + ", ".join(hits)})
    return items


# ---------------------------------------------------------------- live results of a variant

def variant_stats(rows: List[Dict], prefix: str, variant: str) -> Dict:
    """Sent, replies and reply rate for one variant of one stage, from the Performance figures. Nothing sent yet gives 0, 0 and a dash."""
    for row in rows or []:
        if row.get("stage") == prefix and row.get("variant") == variant:
            return {"sent": row.get("sent", 0), "replies": row.get("replies", 0), "rate": format_rate(row.get("reply_rate"))}
    return {"sent": 0, "replies": 0, "rate": format_rate(None)}


# ---------------------------------------------------------------- the lead the preview uses

def lead_label(lead: Dict) -> str:
    name = " ".join(part for part in (str(lead.get("FirstName") or "").strip(), str(lead.get("LastName") or "").strip()) if part)
    email = str(lead.get("Email") or "").strip()
    return f"{name} <{email}>" if name and email else (name or email or f"Lead {lead.get('LeadID', '')}".strip())


def lead_options(leads: List[Dict], limit: int = 200) -> List[str]:
    """Labels for the preview's lead picker (the first `limit` leads, in sheet order), made unique so two people with one name stay distinguishable."""
    labels, seen = [], {}
    for lead in leads[:limit]:
        label = lead_label(lead)
        seen[label] = seen.get(label, 0) + 1
        labels.append(label if seen[label] == 1 else f"{label} ({seen[label]})")
    return labels


# ---------------------------------------------------------------- unsaved edits

def draft_key(prefix: str, variant: str) -> str:
    return f"{prefix}|{variant}"


def record_draft(drafts: Dict, prefix: str, variant: str, original: Dict, subject: str, body: str, changed: bool) -> None:
    """Keep what is typed for one variant while it differs from the saved text, and forget it as soon as it matches again. The saved text it was
    based on is kept too, so a draft can be recognised as out of date later."""
    key = draft_key(prefix, variant)
    if changed:
        drafts[key] = {"prefix": prefix, "variant": variant, "base": (original["subject"], original["body"]), "subject": subject, "body": body}
    else:
        drafts.pop(key, None)


def settle_drafts(drafts: Dict, originals: Dict[Tuple[str, str], Dict], changed: Callable[[Dict, str, str], bool]) -> Tuple[List[Tuple[str, str, str, str]], List[str]]:
    """The edits really waiting to be saved: [(prefix, variant, subject, body)], in a stable order. A draft is DROPPED (and reported) when its
    variant no longer exists, when the saved text has changed since it was started (saving it would overwrite the newer text), or when it no
    longer differs from the saved text."""
    pending, dropped = [], []
    for key in sorted(drafts):
        draft = drafts[key]
        original = originals.get((draft["prefix"], draft["variant"]))
        if original is None or (original["subject"], original["body"]) != tuple(draft["base"]):
            dropped.append(key)
            drafts.pop(key)
        elif not changed(original, draft["subject"], draft["body"]):
            drafts.pop(key)
        else:
            pending.append((draft["prefix"], draft["variant"], draft["subject"], draft["body"]))
    return pending, dropped


# ---------------------------------------------------------------- the add forms: tabs, progress and the wait

def tab_label(name: str, ready: bool) -> str:
    """A tab of the add form: 'Variant A ✓' when its content is valid, 'Variant B ○' when it still needs filling in."""
    return f"{name} {'✓' if ready else '○'}"


def join_names(names: List[str]) -> str:
    """'B', 'B and C', 'B, C and D' (and 'Follow-up 1, Follow-up 2 and Follow-up 3' the same way)."""
    names = [str(name) for name in names]
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def progress_text(ready: List[bool], names: List[str], plural: str, adding: str) -> str:
    """The line at the foot of an add form. ready[i] says whether names[i] is complete.
    '1 of 4 variants ready · fill B, C and D to add this stage' / 'All 4 variants ready'. (`plural` is 'variants' or 'stages'; `adding` is what is being added.)"""
    total, done = len(ready), sum(1 for flag in ready if flag)
    if total and done == total:
        return f"All {total} {plural} ready"
    missing = [name for name, flag in zip(names, ready) if not flag]
    return f"{done} of {total} {plural} ready · fill {join_names(missing)} to add {adding}"


def wait_sentence(previous_label: str, wait: Optional[int]) -> str:
    """'sends 2 days after Follow-up 9' for the stage about to be added; empty when the wait is not known (nothing is claimed that is not known)."""
    if not isinstance(wait, int) or isinstance(wait, bool) or wait < 0:
        return ""
    if wait == 0:
        return f"sends the same day as {previous_label}"
    return f"sends {wait} day{'' if wait == 1 else 's'} after {previous_label}"


def new_stage_badge(wait: Optional[int]) -> str:
    """The day badge on the card of a stage about to be added, the same wording as on the others: '+2 days', '+1 day', 'Same day'; empty when the wait is not known."""
    if not isinstance(wait, int) or isinstance(wait, bool) or wait < 0:
        return ""
    return "Same day" if wait == 0 else f"+{wait} day{'' if wait == 1 else 's'}"
