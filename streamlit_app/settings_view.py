"""The Settings tab, laid out like the approved design: Sender accounts and Sending limits on the left; Campaign status, Readiness and the Danger zone on the right.
Maintenance, Asana Sync and Creator Tracker Sync (which the design does not show) are kept, collapsed, under the cards.

Saving does what it always did: the same validation, the same override file, the same commit and message — except that it now reads the LIVE settings file first,
so saving here can never undo something saved a moment earlier on this page or the Schedule tab. The account toggles save exactly what the old picker saved
(`rotation_accounts`, in the same order), and the rotation switch saves `sender_rotation`.
"""
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import streamlit as st

import outreach
import settings_html as H
import settings_view_logic as L
from github_client import GitHubActionsError
from settings_logic import build_updated_override, load_raw_override_live, override_file_path, override_to_yaml_bytes, validate_settings


@dataclass
class SettingsContext:
    """What the tab needs from the page. The page supplies everything that touches the outside world, so this module stays easy to test."""
    campaign_cfg: Dict
    leads: List[Dict]
    send_log: List[Dict]
    status: str                                   # the campaign's status key ('running', 'paused', 'draft', ...)
    status_label: str                             # its words, as the header shows them
    account_directory: Dict[str, str]             # {account name: address}
    health: Dict[str, Dict]                       # {account name: {status, detail, checked_at}}
    intro_texts: Optional[List[str]]              # the intro template of every variant, or None if they could not be read
    get_client: Callable
    current_user: Callable
    campaigns_dir: str
    update_status: Callable[[str, str], bool]     # (campaign, 'paused' | 'active') -> did it work
    request_launch: Callable[[], None]            # asks for the Launch confirmation shown in the header
    archive: Callable[[str, str], bool]           # (campaign, current status) -> did it work: hides the campaign, restorable from the Hub
    files_to_delete: Callable[[str], List[str]]
    name_matches: Callable[[str, str], bool]      # (typed, campaign name) -> is the confirmation right
    extras: Callable[[], None]                    # Maintenance, Asana Sync and Creator Tracker Sync, as they always were
    default_account: str = ""


def render_settings_tab(ctx: SettingsContext) -> None:
    st.markdown(f"<style>{H.SETTINGS_CSS}</style>", unsafe_allow_html=True)
    left, right = st.columns([1.55, 1], gap="large")
    with left:
        form = _render_accounts(ctx)
        _render_limits(ctx, form)
        ctx.extras()
    with right:
        _render_status(ctx)
        _render_readiness(ctx, form)
        _render_danger(ctx)


# ---------------------------------------------------------------- left: sender accounts

def _per_account_limit_now(sending: Dict, campaign_name: str) -> Optional[int]:
    """The per-account limit as it stands on screen: the switch and the number if the visitor has touched them this session, otherwise what is saved. (The usage bars
    are drawn before the limit boxes, so they cannot simply read the boxes' return values.)"""
    saved = sending.get("per_account_daily_limit")
    on = st.session_state.get(f"se_limit_peron_{campaign_name}", saved is not None)
    value = st.session_state.get(f"se_limit_per_{campaign_name}", saved or 20)
    return int(value) if on and value else None


def _store_key(campaign_name: str) -> str:
    return f"se_picked_{campaign_name}"


def _toggle_key(campaign_name: str, account: str) -> str:
    return f"se_acct_{campaign_name}_{account}"


def _picked(ctx: SettingsContext) -> set:
    """The accounts currently switched on, kept in session state on their own (not in the toggles): a toggle the search box hides is not drawn, and Streamlit forgets
    the state of a widget that is not drawn — which would silently un-pick it."""
    name = ctx.campaign_cfg["_campaign_name"]
    key = _store_key(name)
    if key not in st.session_state:
        stored = ((ctx.campaign_cfg.get("sending") or {}).get("rotation_accounts")) or []
        st.session_state[key] = {a for a in stored if a in ctx.account_directory} if ctx.account_directory else set(stored)
    return st.session_state[key]


def _flip(campaign_name: str, account: str) -> None:
    store = st.session_state[_store_key(campaign_name)]
    (store.add if st.session_state.get(_toggle_key(campaign_name, account)) else store.discard)(account)


def _select_all(campaign_name: str, accounts: List[str]) -> None:
    st.session_state[_store_key(campaign_name)] = set(accounts)
    for account in accounts:
        if _toggle_key(campaign_name, account) in st.session_state:
            st.session_state[_toggle_key(campaign_name, account)] = True


def _render_accounts(ctx: SettingsContext) -> Dict:
    """Returns what the rest of the tab needs from this card: the choices as they stand."""
    cfg = ctx.campaign_cfg
    name = cfg["_campaign_name"]
    sending = cfg.get("sending") or {}
    directory = ctx.account_directory
    with st.container(key="se_card_accounts"):
        title_column, search_column = st.columns([2.2, 1.3])
        with title_column:
            st.markdown(H.card_heading_html("Sender accounts", "Emails rotate across the accounts you switch on."), unsafe_allow_html=True)
        search = ""
        if directory:
            with search_column:
                search = st.text_input("Search accounts", placeholder="🔍 Search accounts", key=f"se_search_{name}", label_visibility="collapsed")

        if not directory:
            st.info(
                "No accounts configured yet — add one on the Email Accounts page (➕ Add Account), or add "
                "[email_accounts_directory] to Streamlit Secrets for the legacy path. Sending will still use "
                "whatever's configured directly in EMAIL_ACCOUNTS_JSON either way."
            )
            picked = set(_picked(ctx))
        else:
            picked = _picked(ctx)

        rotation_column, all_column = st.columns([2, 1.3])
        with rotation_column:
            with st.container(key=f"se_rotation_{name}"):
                rotation_on = st.toggle("Rotate across multiple sender accounts", value=bool(sending.get("sender_rotation")), key=f"se_rotation_toggle_{name}")
        if directory:
            with all_column:
                with st.container(key=f"se_selectall_{name}"):
                    st.button(L.select_all_text(len(picked & set(directory)), len(directory)), key=f"se_selectall_button_{name}", on_click=_select_all, args=(name, list(directory)))

            health_rows = L.account_rows(directory, ctx.health, outreach._count_sent_today_by_account(ctx.send_log), picked, rotation_on,   # noqa: SLF001
                                         _per_account_limit_now(sending, name))
            for index, row in enumerate(L.filter_accounts(health_rows, search)):
                with st.container(key=f"se_acctrow_{index}"):
                    toggle_column, info_column, usage_column = st.columns([0.055, 0.575, 0.37])
                    with toggle_column:
                        st.toggle(f"Use {row['name']}", value=row["on"], key=_toggle_key(name, row["name"]), on_change=_flip, args=(name, row["name"]), label_visibility="collapsed")
                    info_column.markdown(H.account_info_html(row["address"], row["note"], row["health_kind"] if row["on"] else "unknown"), unsafe_allow_html=True)
                    if row["on"]:
                        usage_column.markdown(H.usage_html(row["sent"], row["limit"], row["fraction"]), unsafe_allow_html=True)
            if search and not L.filter_accounts(health_rows, search):
                st.caption("No account matches that search.")
        st.markdown(H.note_html(L.rotation_summary(L.ordered_picks(sending.get("rotation_accounts") or [], picked, list(directory)) if directory else sorted(picked), rotation_on,
                                                   ctx.default_account), "info"), unsafe_allow_html=True)
    return {"picked": picked, "rotation_on": rotation_on}


# ---------------------------------------------------------------- left: sending limits

def _render_limits(ctx: SettingsContext, form: Dict) -> None:
    cfg = ctx.campaign_cfg
    name = cfg["_campaign_name"]
    sending = cfg.get("sending") or {}
    directory = ctx.account_directory
    with st.container(key="se_card_limits"):
        st.markdown(H.card_heading_html("Sending limits", "Keeps each mailbox's reputation safe."), unsafe_allow_html=True)
        daily_column, per_column = st.columns(2, gap="large")
        with daily_column:
            with st.container(key=f"se_limit_daily_{name}"):
                daily_limit = st.number_input("Daily limit (across all accounts)", min_value=1, value=int(sending.get("daily_limit", 100)), key=f"se_limit_daily_input_{name}")
        with per_column:
            with st.container(key=f"se_limit_per_toggle_{name}"):
                per_on = st.toggle("Per-account daily limit", value=sending.get("per_account_daily_limit") is not None, key=f"se_limit_peron_{name}")
            per_value = st.number_input("Per-account daily limit amount", min_value=1, value=int(sending.get("per_account_daily_limit") or 20), key=f"se_limit_per_{name}",
                                        label_visibility="collapsed", disabled=not per_on)
        per_limit = int(per_value) if per_on else None
        pool = L.pool_names(sorted(form["picked"]), form["rotation_on"], list(directory), ctx.default_account)
        check = L.limits_check(int(daily_limit), per_on, per_limit, len(pool) or 1)                    # no pool at all means the single default sender
        if check:
            st.markdown(H.note_html(check["text"], check["kind"]), unsafe_allow_html=True)

        with st.container(key="se_save"):
            save_clicked = st.button("💾 Save settings", type="primary", key="se_save_button")
        if save_clicked:
            errors = validate_settings(int(daily_limit), per_limit)
            if errors:
                for e in errors:
                    st.error(e)
            else:
                try:
                    client = ctx.get_client()
                    raw_override = load_raw_override_live(name, client, ctx.campaigns_dir)                    # the LIVE file, not a possibly stale local copy
                    saved = L.ordered_picks((raw_override.get("sending") or {}).get("rotation_accounts") or sending.get("rotation_accounts") or [], form["picked"], list(directory)) \
                        if directory else sorted(form["picked"])
                    updated = build_updated_override(raw_override, int(daily_limit), per_limit, bool(form["rotation_on"]), saved)
                    client.create_file(
                        override_file_path(name), override_to_yaml_bytes(updated),
                        message=f"Update settings for {name} (via Streamlit, by {ctx.current_user()})",
                    )
                    st.success("Settings saved. May take a minute to actually reflect here while the app "
                               "redeploys — it's in effect for sending immediately either way.")
                except GitHubActionsError as exc:
                    st.error(f"Save failed: {exc}")


# ---------------------------------------------------------------- right: status, readiness, danger zone

def _render_status(ctx: SettingsContext) -> None:
    name = ctx.campaign_cfg["_campaign_name"]
    with st.container(key="se_card_status"):
        st.markdown(H.card_heading_html("Campaign status", "Pausing stops all sending for this campaign. Replies are still collected."), unsafe_allow_html=True)
        pill_column, action_column = st.columns([1, 1])
        pill_column.markdown(H.status_pill_html(ctx.status, ctx.status_label), unsafe_allow_html=True)
        with action_column:
            with st.container(key="se_statusaction"):
                if ctx.status in ("running", "attention"):
                    if st.button("⏸ Pause", key="se_pause_button") and ctx.update_status(name, "paused"):
                        st.success("Paused. May take a minute to reflect here while the app redeploys.")
                elif ctx.status == "paused":
                    if st.button("▶ Resume", key="se_resume_button") and ctx.update_status(name, "active"):
                        st.success("Resumed. May take a minute to reflect here while the app redeploys.")
                elif ctx.status == "draft":
                    if st.button("🚀 Launch", type="primary", key="se_launch_button"):
                        ctx.request_launch()
                        st.rerun()


def _render_readiness(ctx: SettingsContext, form: Dict) -> None:
    directory = ctx.account_directory
    pool = L.pool_names(sorted(form["picked"]), form["rotation_on"], list(directory), ctx.default_account)
    items = L.readiness_items(ctx.campaign_cfg, ctx.leads, ctx.intro_texts, ctx.health, pool)
    with st.container(key="se_card_readiness"):
        st.markdown(H.card_heading_html("Readiness", "Everything a campaign needs before it can send."), unsafe_allow_html=True)
        st.markdown(H.readiness_html(items), unsafe_allow_html=True)


def _render_danger(ctx: SettingsContext) -> None:
    """The two existing actions, named as in the design. Archive is the old 'Temporarily Remove' (hides the campaign, nothing about it changes, restorable from
    Deleted Campaigns on the Campaigns Hub page); Delete is the old 'Permanently Delete' (the name must be typed)."""
    cfg = ctx.campaign_cfg
    name = cfg["_campaign_name"]
    with st.container(key="se_card_danger"):
        st.markdown(H.card_heading_html("Danger zone", "These can't be undone from here.", danger=True), unsafe_allow_html=True)
        archive_column, delete_column, _room = st.columns([1.3, 1.5, 0.9])
        with archive_column:
            with st.container(key="se_archive"):
                archive_clicked = st.button("Archive campaign", key="temp_remove_campaign_button",
                                            help="Hides the campaign from the everyday list and changes nothing about it. Find it under Deleted Campaigns on the Campaigns page and restore it any time.")
        with delete_column:
            with st.container(key="se_delete_open"):
                with st.popover("Delete campaign…"):
                    st.warning(
                        "This removes the campaign's templates — it will no longer appear anywhere, including "
                        "Deleted Campaigns, and this can't be undone from this app. Your Sheet data (leads, sends, "
                        "replies) is NOT deleted and stays fully accessible directly in the Sheet."
                    )
                    typed_name = st.text_input(f'Type the campaign name ("{name}") to confirm', key="delete_campaign_confirm_text")
                    confirmed = ctx.name_matches(typed_name, name)
                    delete_clicked = st.button("Permanently Delete Campaign", type="primary", disabled=not confirmed, key="delete_campaign_button")
        if archive_clicked:
            if ctx.archive(name, cfg.get("status") or "active"):
                st.success(f"'{name}' archived. Find it in Deleted Campaigns on the Campaigns Hub page to restore it later, exactly as it is now.")
                st.session_state["selected_campaign"] = None
                st.rerun()
        if delete_clicked:
            try:
                paths = ctx.files_to_delete(name)
                client = ctx.get_client()
                for path in paths:
                    client.delete_file(path, message=f"Delete campaign {name} (via Streamlit, by {ctx.current_user()})")
                st.success(f"'{name}' deleted ({len(paths)} file(s)). It'll disappear from the "
                           "Campaigns list within a minute or two. Its Sheet data is untouched.")
                st.session_state["selected_campaign"] = None
                st.rerun()
            except GitHubActionsError as exc:
                st.error(f"Failed to delete campaign: {exc}")
