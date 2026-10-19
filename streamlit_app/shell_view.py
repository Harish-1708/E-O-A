"""The sidebar and the Home page, drawn with Streamlit. The pure parts are in shell_logic.py, the markup in shell_html.py and the shared reads in shell_data.py."""
import os
import sys
from datetime import datetime, timezone

import streamlit as st

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
import shell_html as H  # noqa: E402
import shell_logic as L  # noqa: E402
from shell_data import load_hub_bundle, load_sender_overview  # noqa: E402

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None


def local_now() -> datetime:
    """Now, in the visitor's own time zone when the browser reports one (so 'Good evening' means their evening), otherwise in UTC."""
    try:
        name = st.context.timezone
        if name and ZoneInfo is not None:
            return datetime.now(ZoneInfo(name))
    except Exception:  # noqa: BLE001 - an unknown or unavailable zone falls back to UTC
        pass
    return datetime.now(timezone.utc)


def date_text(now: datetime) -> str:
    return f"{now.strftime('%A, %B')} {now.day}"


def render_sidebar(pages, user_name: str, on_logout, current=None) -> None:
    """Our own sidebar: the app's name, one link per page, and the signed-in user with Log out at the bottom. (The links are Streamlit's own page links, so the page you are on
    is highlighted by a style rule on the container we name `__active`.)"""
    st.markdown(f"<style>{H.SHELL_CSS}</style>", unsafe_allow_html=True)
    with st.sidebar:
        st.markdown(H.brand_html(), unsafe_allow_html=True)
        for key, label, _path, icon in L.NAV_ITEMS:
            with st.container(key=L.nav_key(key, L.is_current_page(pages[key], current))):
                st.page_link(pages[key], label=label, icon=icon)
        with st.container(key="nav_user"):
            st.markdown(H.user_card_html(user_name), unsafe_allow_html=True)
            if st.button("Log out", key="nav_logout", icon=":material/logout:", width="stretch"):
                on_logout()
                st.rerun()


def render_sidebar_markers() -> None:
    """The unread number after Responses and the red dot after Email Accounts. Called AFTER the page has been drawn, so the page is never held up by it; it reuses the cached
    loads the pages make, and any problem here is swallowed (a marker must never break a page)."""
    try:
        _rows, _deleted, _errors, details = load_hub_bundle()
        unread = sum(L.unread_by_campaign(details, st.session_state.get("read_response_keys", set())).values())
        sender = load_sender_overview()
        disconnected = L.account_overview(sender["directory"], sender["health"])["bad"]
        css = L.marker_css(unread, disconnected)
    except Exception:  # noqa: BLE001
        return
    if css:
        st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def _go_with_flag(flag: str, page, pages=None) -> None:
    st.session_state[flag] = True
    st.switch_page(page)


def _render_attention(items, pages) -> None:
    st.markdown(H.section_html("Needs your attention"), unsafe_allow_html=True)
    if not items:
        st.markdown(H.all_clear_html(), unsafe_allow_html=True)
        return
    columns = st.columns(len(items))
    for column, item in zip(columns, items):
        with column:
            with st.container(key=f"home_att_{item['kind']}"):
                st.markdown(H.attention_card_html(item), unsafe_allow_html=True)
                if item["link"] == "responses":
                    st.page_link(pages["responses"], label="Open Responses →")
                elif item["link"] == "accounts":
                    st.page_link(pages["accounts"], label="Update its password →")
                elif st.button("Open the campaign →", key="home_open_attention_campaign", type="tertiary"):
                    st.session_state["selected_campaign"] = item.get("target")
                    st.switch_page(pages["campaigns"])


def _render_today(today, pages) -> None:
    """Today, as a part of Home: what is going out and what is waiting for every running campaign, with the detail one tap away."""
    st.markdown(H.section_html("Today"), unsafe_allow_html=True)
    if not today["cards"]:
        st.caption("No campaign is running right now, so nothing is due.")
    else:
        st.markdown(H.today_tiles_html(today["tiles"]), unsafe_allow_html=True)
        st.markdown('<div style="height:14px"></div>', unsafe_allow_html=True)
    for index, card in enumerate(today["cards"]):
        with st.container(key=f"home_today_{'open' if card['window_kind'] == 'open' else 'shut'}_{index}"):
            body_column, open_column = st.columns([5.6, 1])
            body_column.markdown(H.today_card_html(card), unsafe_allow_html=True)
            with open_column:
                if st.button("Open →", key=f"home_open_{card['name']}", width="stretch"):
                    st.session_state["selected_campaign"] = card["name"]
                    st.switch_page(pages["campaigns"])
            with st.expander("Stage breakdown"):
                if card["stages"]:
                    st.markdown(H.stage_table_html(card["stages"]), unsafe_allow_html=True)
                else:
                    st.caption("Nobody is due or waiting at any stage.")
            if card["due"]:
                with st.expander(f"Who is due now ({card['due']})"):
                    for stage in card["stages"]:
                        if stage["due_now"]:
                            st.markdown(f"**{stage['label']}** — {stage['due_now']} due now")
                            st.dataframe(stage["due_leads"], width="stretch", hide_index=True)
                            if stage["due_leads_hidden"]:
                                st.caption(f"…and {stage['due_leads_hidden']} more not listed.")
    notes = ["Dates are in each campaign's own time zone; times stored in the Sheet are UTC."]
    if today["not_running"]:
        notes.append("Not running: " + ", ".join(f"{name} ({status})" for name, status in today["not_running"]) + ".")
    st.caption(" ".join(notes))
    if today["errors"]:
        with st.expander(f"{len(today['errors'])} campaign(s) could not be shown here"):
            for name, message in today["errors"]:
                st.write(f"**{name}** — {message}")


def _render_live(summary, pages) -> None:
    _render_attention(summary["attention"], pages)
    _render_today(summary["today"], pages)


GUIDE = [("performance", "Performance", "perf", "Positive replies, which follow-ups and variants work, and the what-worked-best report."),
         ("campaigns", "Campaigns", "camp", "Create, open and manage every campaign. The everyday place to work."),
         ("responses", "Responses", "chat", "Every reply from every campaign in one inbox. Reply straight from here."),
         ("accounts", "Email Accounts", "mail", "Sender accounts, their connection health and how much each sent today.")]


def render_home(pages, user_name: str) -> None:
    """Home: the greeting and the quick actions and the guide appear at once; what needs attention, the figures and the running campaigns fill in below as soon as the campaign
    data is read (the same cached data the Campaigns page uses)."""
    st.markdown(f"<style>{H.SHELL_CSS}</style>", unsafe_allow_html=True)
    now = local_now()
    st.markdown(H.greeting_html(L.greeting(now.hour, user_name), date_text(now)), unsafe_allow_html=True)

    live = st.container()
    with live:
        placeholder = st.empty()
        placeholder.markdown(H.skeleton_html(), unsafe_allow_html=True)

    st.markdown(H.section_html("Quick actions"), unsafe_allow_html=True)
    with st.container(key="home_qa_row"):
        first, second, third, _spare = st.columns([1.2, 1.2, 1.2, 1.6])
    with first:
        if st.button("＋ New campaign", type="primary", key="home_new_campaign", width="stretch"):
            _go_with_flag("hub_open_new_on_arrival", pages["campaigns"])
    with second:
        with st.container(key="home_qa_responses"):
            st.page_link(pages["responses"], label="Open Responses", icon=":material/chat_bubble:", width="stretch")
    with third:
        if st.button("＋ Add account", key="home_add_account", width="stretch"):
            _go_with_flag("ea_open_add_on_arrival", pages["accounts"])

    st.markdown(H.section_html("Where things are"), unsafe_allow_html=True)
    for column, (key, title, icon, text) in zip(st.columns(4), GUIDE):
        with column:
            with st.container(key=f"home_guide_{key}"):
                st.markdown(H.guide_card_html(icon, title, text), unsafe_allow_html=True)
                st.page_link(pages[key], label="Open →")

    try:
        rows, _deleted, _errors, details = load_hub_bundle()
        sender = load_sender_overview()
        summary = L.home_summary(rows, details, st.session_state.get("read_response_keys", set()), sender["directory"], sender["health"], datetime.now(timezone.utc))
    except Exception as exc:  # noqa: BLE001 - the page still works without its live part
        placeholder.empty()
        with live:
            st.markdown(H.load_error_html(str(exc)[:120]), unsafe_allow_html=True)
        return
    placeholder.empty()
    with live:
        _render_live(summary, pages)
