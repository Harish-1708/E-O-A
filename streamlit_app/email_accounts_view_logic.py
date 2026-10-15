"""Pure logic for the Email Accounts page: one card per account (with its status, slot and whether this app can manage it), the health summary above the list, the
status filters and search, and showing a long list 25 at a time. No Streamlit here.
"""
from typing import Dict, List, Optional, Tuple

PAGE_SIZE = 25
SEARCH_MIN_ACCOUNTS = 4          # the search box is only useful once there are several accounts (the same threshold as the sender list on a campaign's Settings tab)
STATUS_FILTERS = [("all", "All"), ("Connected", "Connected"), ("Disconnected", "Disconnected"), ("Unknown", "Unknown")]
PALETTE = ["#2563eb", "#0d9488", "#7c3aed", "#db2777", "#ea580c", "#0891b2", "#4d7c0f", "#b45309"]
KIND_BY_STATUS = {"Connected": "ok", "Disconnected": "bad"}


def show_search(account_count: int) -> bool:
    return account_count >= SEARCH_MIN_ACCOUNTS


def status_kind(status: str) -> str:
    """'ok' (connected), 'bad' (disconnected) or 'unknown' for anything else, including no check yet."""
    return KIND_BY_STATUS.get(status, "unknown")


def avatar_initial(address: str, name: str) -> str:
    """The first letter of the address (or of the account name when the address is empty), upper case; '?' when there is neither."""
    for text in (str(address or "").strip(), str(name or "").strip()):
        if text:
            return text[0].upper()
    return "?"


def avatar_color(name: str, kind: str) -> str:
    """Red for an account that is disconnected, grey when its health is not known, otherwise one of eight colours chosen by its name (the same name always gets the same colour)."""
    if kind == "bad":
        return "#dc2626"
    if kind == "unknown":
        return "#9ca3af"
    return PALETTE[sum(ord(character) for character in str(name)) % len(PALETTE)]


def account_cards(rows: List[Dict], slot_mapping: Dict[str, Dict]) -> List[Dict]:
    """One dict per account from accounts_logic.build_account_rows plus what the card needs. An account is `managed` (editable and removable here) exactly when it is in the
    slot-mapping file; one that only exists in the old Streamlit secrets is `legacy`: this app cannot edit it, because GitHub never gives a secret's slot back."""
    cards = []
    for row in rows:
        kind = status_kind(row.get("status"))
        entry = (slot_mapping or {}).get(row["name"])
        cards.append({
            "name": row["name"], "address": row.get("address", ""), "initial": avatar_initial(row.get("address"), row["name"]), "kind": kind,
            "color": avatar_color(row["name"], kind), "status": row.get("status") if kind != "unknown" else "Unknown", "detail": (row.get("status_detail") or "").strip() if kind == "bad" else "",
            "checked_at": row.get("checked_at") or "", "sent_today": int(row.get("sent_today") or 0), "is_default": bool(row.get("is_default")),
            "managed": entry is not None, "slot": entry.get("slot") if entry else None,
        })
    return cards


def summarize_accounts(cards: List[Dict]) -> Dict:
    """The four figures above the list: connected of total, how many need attention (disconnected), sent today across all accounts, and the latest health check time."""
    checked = [card["checked_at"] for card in cards if card.get("checked_at")]
    return {"total": len(cards), "connected": sum(1 for card in cards if card["kind"] == "ok"), "bad": sum(1 for card in cards if card["kind"] == "bad"),
            "unknown": sum(1 for card in cards if card["kind"] == "unknown"), "sent_today": sum(card["sent_today"] for card in cards), "last_checked": max(checked) if checked else ""}


def status_filter_options(cards: List[Dict]) -> List[Tuple[str, str]]:
    """[(filter key, 'Connected 2'), ...] in a fixed order, the first being All with the total."""
    options = []
    for key, label in STATUS_FILTERS:
        count = len(cards) if key == "all" else sum(1 for card in cards if card["status"] == key)
        options.append((key, f"{label} {count}"))
    return options


def filter_accounts(cards: List[Dict], status: Optional[str], query: str) -> List[Dict]:
    """Keep the cards with this status ('all' or nothing keeps every one) whose name or address contains the search text (any case)."""
    kept = list(cards) if status in (None, "", "all") else [card for card in cards if card["status"] == status]
    needle = str(query or "").strip().lower()
    return [card for card in kept if needle in card["name"].lower() or needle in card["address"].lower()] if needle else kept


def visible_count(total: int, shown: int) -> Tuple[int, int]:
    """(how many to draw now, how many are still hidden) when `shown` is how many the person has asked to see (at least one page)."""
    shown = max(PAGE_SIZE, int(shown or 0))
    drawn = min(total, shown)
    return drawn, total - drawn


def show_more_label(hidden: int) -> str:
    """'Show 25 more (60 left)' / 'Show the last 7'."""
    return f"Show {PAGE_SIZE} more ({hidden} left)" if hidden > PAGE_SIZE else f"Show the last {hidden}"


def not_counted_note(unavailable: List[str]) -> str:
    """The footnote about campaigns whose Send Log tab does not exist yet (so nothing was sent for them): empty when there are none."""
    if not unavailable:
        return ""
    count = len(unavailable)
    return f"{count} campaign{'' if count == 1 else 's'} {'isn’t' if count == 1 else 'aren’t'} counted in “sent today” yet (nothing sent for {'it' if count == 1 else 'them'}): {', '.join(unavailable)}"


# ---------------------------------------------------------------- the Add account dialog: live hints and when it may be submitted

def account_name_feedback(name: str, managed: Dict[str, Dict], legacy_names) -> Tuple[str, str]:
    """What to tell someone typing an account name, as they type: ('hint', ...) while the box is empty, ('error', ...) for a name an account here already has, ('info', ...) for a
    name that only exists in the old Streamlit secrets (adding it takes that account over), ('ok', ...) otherwise. The same rules the Add button enforces."""
    name = str(name or "").strip()
    if not name:
        return "hint", "A short label you will see in campaigns, for example sales1."
    if name in (managed or {}):
        return "error", f"“{name}” already exists (slot {managed[name].get('slot')}). Use Edit on its card to change it."
    if name in set(legacy_names or ()):
        return "info", f"“{name}” is set up in Streamlit secrets. Adding it here takes over managing it."
    return "ok", "Free to use. This is the short label you will see in campaigns."


def address_feedback(address: str) -> Tuple[str, str]:
    """('error', ...) for something typed that cannot be an address (no @ with text on both sides), otherwise ('ok', '') (nothing to say) for a good one and ('hint', '') for an
    empty box. The Add button's own rule is only that the address contains an @; this is that rule, made a little clearer to read."""
    address = str(address or "").strip()
    if not address:
        return "hint", ""
    local, _, domain = address.partition("@")
    if "@" not in address or not local or not domain:
        return "error", "That is not a valid email address."
    return "ok", ""


def add_form_ready(name: str, address: str, managed: Dict[str, Dict]) -> bool:
    """Whether the Add account button may be pressed: a free name and an address with an @ (the confirmation is a separate, deliberate step). The PASSWORD is deliberately not part
    of this: a browser only hands a typed value to the app when the field loses focus, and pressing Tab in a password field moves focus to its show/hide eye, which does not count,
    so waiting for the password here would leave the button stuck for anyone who tabs through. A missing password is reported when Add is pressed instead."""
    return account_name_feedback(name, managed, ())[0] == "ok" and address_feedback(address)[0] == "ok"
