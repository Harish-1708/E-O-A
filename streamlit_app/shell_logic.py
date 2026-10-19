"""Pure logic for the sidebar and the Home page: the navigation, the greeting, and everything Home says about right now (what needs attention, the figures, the running
campaigns). It works on data the pages already load; nothing here reads a Sheet. No Streamlit.
"""
import re
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from campaign_status_logic import STATUS_RUNNING
from email_accounts_view_logic import account_cards, summarize_accounts
from responses_hub_logic import count_unread_conversations, tag_responses_with_campaign
from work_queue_logic import NOT_IN_PLAY_LABELS, build_campaign_queue, summarize_queue
from accounts_logic import build_account_rows

# (key, label, page path, Material icon). The order is the order in the sidebar.
NAV_ITEMS = [
    ("home", "Home", None, ":material/home:"),
    ("performance", "Performance", "pages/performance.py", ":material/bar_chart:"),
    ("campaigns", "Campaigns", "pages/campaigns.py", ":material/folder:"),
    ("responses", "Responses", "pages/responses.py", ":material/chat_bubble:"),
    ("accounts", "Email Accounts", "pages/email_accounts.py", ":material/mail:"),
]

BADGE_STYLE = ("margin-left: auto; padding: 1px 8px; border-radius: 999px; background: #2563eb; color: #fff; font-size: 11.5px; font-weight: 700; line-height: 1.5;")
DOT_STYLE = "margin-left: auto; width: 9px; height: 9px; border-radius: 50%; background: #dc2626;"


def is_current_page(page, current) -> bool:
    """Whether `page` is the page being shown. Streamlit gives us the shown page back from st.navigation, and its url_path says which it is (the home page's is empty)."""
    if page is None or current is None:
        return False
    return getattr(page, "url_path", object()) == getattr(current, "url_path", None)


def nav_key(key: str, active: bool) -> str:
    """The name of the container around a sidebar link. The one for the page you are on ends in '__active', which is what the highlight's style rule is aimed at (Streamlit
    itself does not leave a stable mark on the active link). Both forms still contain 'nav_<key>', which the markers' rules rely on."""
    return f"nav_{key}__active" if active else f"nav_{key}"


def greeting(hour: Optional[int], name: str = "") -> str:
    """'Good morning, Harish' before noon, 'Good afternoon' until six, 'Good evening' after (plain 'Hello' when the hour is not known)."""
    if not isinstance(hour, int) or isinstance(hour, bool) or not 0 <= hour <= 23:
        word = "Hello"
    else:
        word = "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"
    return f"{word}, {name}" if name else word


def marker_css(unread: int, disconnected: int) -> str:
    """The two small markers in the sidebar: the unread number after Responses, and a red dot after Email Accounts while an account is disconnected. Drawn by style rules (the
    links themselves never change), and absent when there is nothing to show."""
    rules = []
    try:
        unread_count = int(unread)
    except (TypeError, ValueError):
        unread_count = 0
    if unread_count > 0:
        rules.append(f'[class*="st-key-nav_responses"] a::after {{ content: "{unread_count}"; {BADGE_STYLE} }}')
    try:
        bad_count = int(disconnected)
    except (TypeError, ValueError):
        bad_count = 0
    if bad_count > 0:
        rules.append(f'[class*="st-key-nav_accounts"] a::after {{ content: ""; {DOT_STYLE} }}')
    return "\n".join(rules)


def unread_by_campaign(details: Dict[str, Dict], read_keys: Set[str]) -> Dict[str, int]:
    """{campaign name: conversations with something unread}, only for campaigns that have any."""
    counts = {}
    for name, data in (details or {}).items():
        count = count_unread_conversations(tag_responses_with_campaign(data.get("responses") or [], name), read_keys or set())
        if count:
            counts[name] = count
    return counts


def account_overview(directory: Dict[str, str], health: Dict[str, Dict]) -> Dict:
    """What Home and the sidebar need to know about the sender accounts: how many, how many are connected, which are disconnected (with the reason), and the latest check."""
    rows = build_account_rows(directory or {}, {}, "", health_lookup=health or {})
    cards = account_cards(rows, {})
    summary = summarize_accounts(cards)
    bad = [{"name": card["name"], "address": card["address"], "detail": card["detail"]} for card in cards if card["kind"] == "bad"]
    return {"total": summary["total"], "connected": summary["connected"], "bad": summary["bad"], "bad_accounts": bad, "last_checked": summary["last_checked"]}


def stage_label(name: str) -> str:
    """'intro' -> 'Intro', 'followup4' -> 'Follow-up 4'; anything else is shown as it is."""
    text = str(name or "")
    if text == "intro":
        return "Intro"
    match = re.fullmatch(r"followup(\d+)", text)
    return f"Follow-up {match.group(1)}" if match else text


def window_pill(window: Dict) -> Tuple[str, str]:
    """(kind, words) for a campaign's sending window: 'open' while it is open, 'shut' with when it next opens, 'unknown' when it could not be read."""
    state = (window or {}).get("open")
    if state is True:
        return "open", "● Open now"
    if state is False:
        opens = (window or {}).get("next_open")
        return "shut", f"○ Closed · opens {opens}" if opens else "○ Closed"
    return "unknown", "Sending window unknown"


def today_card(queue: Dict) -> Dict:
    """One running campaign's Today card, from the queue the engine's own rule produces (the same numbers the next automatic run will act on)."""
    window = queue.get("window") or {}
    limit = queue.get("daily_limit")
    sent = int(queue.get("sent_today") or 0)
    kind, pill = window_pill(window)
    stages = [dict(row, label=stage_label(row.get("stage"))) for row in queue.get("stages") or [] if row.get("due_now") or row.get("waiting")]
    return {"name": queue.get("campaign", ""), "window_kind": kind, "window_pill": pill, "window_state": {True: "Open", False: "Closed"}.get(window.get("open"), "Unknown"),
            "window_note": "" if window.get("open") is True else str(window.get("reason") or ""), "due": int(queue.get("due_total") or 0), "waiting": int(queue.get("waiting_total") or 0),
            "next_due": queue.get("next_due_text") or "", "sent_today": sent, "limit": limit, "percent": min(100, round(sent / limit * 100)) if limit else 0,
            "summary": summarize_queue(queue), "in_sequence": int(queue.get("in_sequence") or 0), "total_leads": int(queue.get("total_leads") or 0),
            "out_of_play": [(label, queue["out_of_play"][label]) for label in NOT_IN_PLAY_LABELS if (queue.get("out_of_play") or {}).get(label)], "stages": stages}


def today_section(rows: List[Dict], details: Dict[str, Dict], now_utc: datetime) -> Dict:
    """The Today part of Home: one card per RUNNING campaign (built from the settings and leads already loaded, so it reads nothing extra) and the totals above them. A running
    campaign whose data could not be read or whose queue could not be built is reported in `errors` rather than hiding the rest."""
    cards, errors = [], []
    for row in rows:
        if row.get("status") != STATUS_RUNNING:
            continue
        data = (details or {}).get(row["name"]) or {}
        if not data.get("cfg"):
            errors.append((row["name"], "its data could not be read"))
            continue
        try:
            card = today_card(build_campaign_queue(data["cfg"], data.get("leads") or [], now_utc))
            card["name"] = row["name"]                    # the name in the campaign list, whatever the settings happen to carry
            cards.append(card)
        except Exception as exc:  # noqa: BLE001 - one campaign's problem must not hide the others
            errors.append((row["name"], str(exc)[:120]))
    limits = [c["limit"] for c in cards if c["limit"]]
    tiles = {"running": len(cards), "due": sum(c["due"] for c in cards), "waiting": sum(c["waiting"] for c in cards), "sent_today": sum(c["sent_today"] for c in cards),
             "limit_total": sum(limits) if limits else None, "open": sum(1 for c in cards if c["window_kind"] == "open")}
    return {"tiles": tiles, "cards": cards, "errors": errors, "not_running": [(r["name"], r.get("status")) for r in rows if r.get("status") != STATUS_RUNNING]}


def attention_items(unread_counts: Dict[str, int], accounts: Dict, rows: List[Dict]) -> List[Dict]:
    """The cards under 'Needs your attention': {kind: 'unread'|'account'|'campaign', title, detail, link: 'responses'|'accounts'|'campaigns'}. Only what is true right now."""
    items = []
    total_unread = sum(unread_counts.values())
    if total_unread:
        campaigns = len(unread_counts)
        items.append({"kind": "unread", "title": f"{total_unread} unread {'reply' if total_unread == 1 else 'replies'}", "link": "responses",
                      "detail": f"Across {campaigns} campaign{'' if campaigns == 1 else 's'}." if campaigns > 1 else f"In {next(iter(unread_counts))}."})
    if accounts.get("bad"):
        first = accounts["bad_accounts"][0]
        reason = f" · {first['detail']}" if first.get("detail") else ""
        more = f" (and {accounts['bad'] - 1} more)" if accounts["bad"] > 1 else ""
        items.append({"kind": "account", "title": f"{accounts['bad']} account{'' if accounts['bad'] == 1 else 's'} disconnected", "link": "accounts",
                      "detail": f"{first['address'] or first['name']}{reason}{more}. Campaigns using it cannot send."})
    stuck = [row for row in rows if row.get("status") == "attention"]
    if stuck:
        first = stuck[0]
        problem = (first.get("problems") or [""])[0]
        more = f" (and {len(stuck) - 1} more)" if len(stuck) > 1 else ""
        items.append({"kind": "campaign", "title": f"{len(stuck)} campaign{' needs' if len(stuck) == 1 else 's need'} attention", "link": "campaigns", "target": first["name"],
                      "detail": f"{first['name']}{' · ' + problem if problem else ''}{more}."})
    return items


def home_summary(rows: List[Dict], details: Dict[str, Dict], read_keys: Set[str], directory: Dict[str, str], health: Dict[str, Dict], now_utc: datetime) -> Dict:
    """Everything the live part of Home shows, in one dict."""
    unread = unread_by_campaign(details, read_keys)
    accounts = account_overview(directory, health)
    return {"campaigns": len(rows), "unread_total": sum(unread.values()), "unread_by_campaign": unread, "accounts": accounts, "attention": attention_items(unread, accounts, rows),
            "today": today_section(rows, details, now_utc)}
