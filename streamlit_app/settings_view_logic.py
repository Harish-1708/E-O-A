"""What the Settings tab shows around its controls: one row per sender account (with health and today's usage), what the account choices will really do,
whether the per-account limits fit the campaign limit, and the Readiness checklist.

PURE and READ-ONLY: nothing here saves or sends anything. The rules about WHICH accounts send are the engine's own (outreach.get_rotation_accounts and
current_valid_senders_for_campaign): an explicit list of accounts decides it whether or not rotation is switched on; with no list, rotation means every
configured account and no rotation means the single default sender. The sentences below say exactly that, so nobody has to guess what an empty selection means.
"""
from typing import Dict, List, Optional, Tuple

from campaign_status_logic import compute_campaign_readiness
from schedule_view_logic import parse_hhmm
from sequences_view_logic import placeholder_problems


# ---------------------------------------------------------------- accounts

def health_text(record: Optional[Dict]) -> Tuple[str, str]:
    """(words, kind) for an account's last health check. The check writes Connected, Disconnected or Unknown; Connected is shown as 'Healthy'.
    kind is 'ok', 'bad' or 'unknown'."""
    status = str((record or {}).get("status") or "").strip().lower()
    detail = str((record or {}).get("detail") or "").strip()
    if status == "connected":
        return "Healthy", "ok"
    if status == "disconnected":
        return (f"Disconnected — {detail}" if detail else "Disconnected"), "bad"
    return "Not checked yet", "unknown"


def pool_names(picked: List[str], rotation_on: bool, all_names: List[str], default_account: str = "") -> List[str]:
    """The accounts that will send, by the engine's rules: the picked list if there is one; otherwise every account when rotation is on; otherwise the single
    default sender (if it is a known account)."""
    if picked:
        return list(picked)
    if rotation_on:
        return list(all_names)
    return [default_account] if default_account and default_account in all_names else []


def rotation_summary(picked: List[str], rotation_on: bool, default_account: str = "") -> str:
    """One sentence saying what the account choices will actually do."""
    if picked:
        if len(picked) == 1:
            return f"All emails go out from {picked[0]}."
        return f"Emails go out from {len(picked)} accounts, taking turns: {', '.join(picked)}."
    if rotation_on:
        return "No accounts picked, so every configured account takes turns."
    if default_account:
        return f"No accounts picked and rotation is off, so the default sender ({default_account}) is used."
    return "No accounts picked and rotation is off, so the campaign's default sender is used."


def select_all_text(on: int, total: int) -> str:
    return f"Select all · {on} of {total} on"


def account_rows(directory: Dict[str, str], health: Dict[str, Dict], sent_today: Dict[str, int], picked: List[str], rotation_on: bool,
                 per_account_limit: Optional[int]) -> List[Dict]:
    """One row per configured account, sorted by name: {name, address, on, health, health_kind, sent, limit, fraction, note}.
    `limit` is the per-account limit when there is one (the bar is today's sends against it), else None. `note` is what shows under the name: the account's
    health when it is picked, otherwise why it is not in use."""
    rows = []
    for name in sorted(directory):
        on = name in picked
        words, kind = health_text(health.get(name))
        if on:
            note = words
        elif picked:
            note = "Not used in this campaign"
        else:
            note = "Not picked — used anyway while none is picked" if rotation_on else "Not picked"
        sent = int(sent_today.get(name, 0) or 0)
        limit = per_account_limit if (isinstance(per_account_limit, int) and not isinstance(per_account_limit, bool) and per_account_limit > 0) else None
        rows.append({"name": name, "address": directory[name], "on": on, "health": words, "health_kind": kind, "sent": sent, "limit": limit,
                     "fraction": min(1.0, sent / limit) if limit else 0.0, "note": note})
    return rows


def ordered_picks(stored: List[str], picked: "set", directory_names: List[str]) -> List[str]:
    """The picked accounts as they should be saved: those already in the saved list keep their saved order, newly picked ones follow in name order, and a name that is
    no longer a configured account is dropped (as the old picker did). Re-saving without changes therefore never reshuffles the list."""
    known = set(directory_names)
    kept = [name for name in dict.fromkeys(stored or []) if name in picked and name in known]
    added = [name for name in sorted(directory_names) if name in picked and name not in kept]
    return kept + added


def filter_accounts(rows: List[Dict], query: str) -> List[Dict]:
    """The rows whose name or address contains the search text (case does not matter); an empty search keeps them all."""
    needle = str(query or "").strip().lower()
    if not needle:
        return list(rows)
    return [row for row in rows if needle in row["name"].lower() or needle in str(row["address"]).lower()]


# ---------------------------------------------------------------- limits

def limits_check(daily_limit: int, per_account_on: bool, per_account_limit: Optional[int], accounts: int) -> Optional[Dict]:
    """The live check under the limits: do the per-account limits add up to the campaign limit? {text, kind} with kind 'ok', 'warn' or 'info', or None when
    there is nothing to compare (no per-account limit, or no accounts)."""
    if not per_account_on or not per_account_limit or accounts < 1 or not daily_limit:
        return None
    total = per_account_limit * accounts
    parts = " + ".join([str(per_account_limit)] * accounts) if accounts <= 6 else f"{per_account_limit} × {accounts}"
    head = f"Per-account limits add up to {total} ({parts}) — "
    if total == daily_limit:
        return {"kind": "ok", "text": head + "the same as the campaign limit, so no account is starved."}
    if total < daily_limit:
        return {"kind": "warn", "text": head + f"less than the campaign limit of {daily_limit}, so the campaign will stop at {total} a day."}
    return {"kind": "info", "text": head + f"more than the campaign limit of {daily_limit}, so the campaign limit is reached first and some accounts may send less than their own limit."}


# ---------------------------------------------------------------- readiness

def _item(kind: str, text: str) -> Dict:
    return {"kind": kind, "text": text}


def schedule_readiness(schedule: Optional[Dict]) -> Dict:
    """The schedule line of the checklist: fine with send days and a real window; a plain note when there is no schedule at all (the campaign then sends anytime,
    which is allowed); a problem when a schedule exists but cannot work."""
    if not schedule:
        return _item("info", "No schedule is set, so this campaign sends anytime")
    start, end = parse_hhmm(schedule.get("window_start")), parse_hhmm(schedule.get("window_end"))
    if schedule.get("send_days") and start is not None and end is not None:
        return _item("ok", "Schedule has send days and a window")
    return _item("bad", "Schedule needs send days and a valid window")


def readiness_items(campaign_cfg: Dict, leads: List[Dict], intro_texts: Optional[List[str]], health: Dict[str, Dict], pool: List[str]) -> List[Dict]:
    """The Readiness checklist, in order: [{kind: 'ok' | 'bad' | 'info' | 'warn', text}].
    Leads, templates and the sender use the app's own readiness rules (compute_campaign_readiness); the intro check asks the same question the Sequences tab's
    'Check before saving' does — does a placeholder match no column at all? — across every intro variant. `intro_texts` is None when the templates could not be read."""
    _ready, problems = compute_campaign_readiness(campaign_cfg, leads)
    items = []
    with_email = sum(1 for lead in leads if str(lead.get("Email") or "").strip())
    items.append(_item("ok", f"Leads imported ({with_email})") if with_email else _item("bad", "No leads with an email address"))

    if not campaign_cfg.get("stages"):
        items.append(_item("bad", "No template stages found"))
    elif intro_texts is None:
        items.append(_item("info", "Couldn't check the intro template just now"))
    elif not leads:
        items.append(_item("info", "Add leads to check the intro template's placeholders"))
    else:
        columns = {str(key): "x" for lead in leads for key in lead}
        unknown = list(dict.fromkeys(name for text in intro_texts for name in placeholder_problems(text, columns)[0]))
        if unknown:
            items.append(_item("bad", "Intro template uses " + ", ".join("{{" + name + "}}" for name in unknown) + ", which matches no column"))
        else:
            items.append(_item("ok", "Intro template has all placeholders"))

    items.append(_item("bad", "No sender account configured") if "No sender account configured" in problems else _item("ok", "At least one sender account"))
    items.append(schedule_readiness(campaign_cfg.get("schedule")))

    for name in pool:
        words, kind = health_text(health.get(name))
        if kind == "bad":
            items.append(_item("warn", f"{name}: {words[0].lower() + words[1:]}"))
    return items
