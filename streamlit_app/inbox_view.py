"""The two-pane Inbox: a conversation list on the left, the open conversation
on the right (chat bubbles, newest first, with a composer under it) — shared
by the all-campaigns Responses page and by every campaign's own Responses tab,
so the two cannot drift apart.

It adds NO new way to send, mark read or look anything up. Replying uses the
very same functions the classic layout uses (validate_reply, build_reply_
payload, reply_payload_path, build_attachment_entries) and the same two steps —
commit the payload file, then start the send-reply workflow — so what gets
committed and dispatched is identical. Marking read uses the same read keys.

Untrusted email text (names, subjects, previews) goes through md_escape before
it reaches any Markdown-rendering widget; message bodies are shown with
st.text, which does not interpret Markdown at all.
"""
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Set

import streamlit as st

from inbox_logic import (
    MAX_ROWS, md_escape, build_conversation_rows, resolve_selection, conversation_key, bubble_side,
    sender_display,
)
import outreach  # noqa: E402  (inbox_logic has put the repo root on sys.path)
from config import WORKFLOW_SEND_REPLY  # noqa: E402
from conversation_logic import (  # noqa: E402
    build_conversation_thread, build_incoming_messages_for_responses, build_outgoing_messages_from_sent_mail,
)
from data_import_logic import payload_to_bytes  # noqa: E402
from responses_hub_logic import all_response_keys_in_group  # noqa: E402
from responses_reply_logic import (  # noqa: E402
    find_lead_for_response, build_reply_defaults, parse_email_list, validate_reply, build_reply_payload,
    reply_payload_path, build_attachment_entries, total_attachment_size_bytes,
)

VIEW_INBOX = "Inbox"
VIEW_CLASSIC = "Classic"
# Read when the page runs (not at import), so the test suite can pin the
# layout its older tests were written against.
DEFAULT_VIEW = VIEW_INBOX

LIST_HEIGHT = 640
THREAD_HEIGHT = 420


def choose_view(key: str) -> str:
    """The Inbox / Classic switch. The Inbox is the default; Classic is the
    previous card layout, kept so nothing is lost while the new one beds in."""
    options = [VIEW_INBOX, VIEW_CLASSIC]
    return st.radio("Layout", options, index=options.index(DEFAULT_VIEW), horizontal=True, key=key)


@dataclass
class InboxContext:
    namespace: str                                         # widget-key prefix, unique per place it is drawn
    get_campaign_cfg: Callable[[str], Dict]
    leads_for: Callable[[str], List[Dict]]
    responses_for: Callable[[str, str], List[Dict]]        # (campaign, lead_id) -> EVERY reply of that conversation, unfiltered
    github_client: Callable[[], object]
    current_user: Callable[[], str]
    read_keys: Optional[Set[str]] = None                   # None: this place does not track read state
    pending_sync_keys: Optional[Set[str]] = None
    show_campaign: bool = True
    pause_before_dispatch: float = 0.0                     # a campaign's own tab waits a second between commit and dispatch, as it always has
    sent_for: Optional[Callable[[str, str], List[Dict]]] = None   # (campaign, lead_id) -> emails the team sent by hand / from the panel


# ---------------------------------------------------------------- widget keys

def _keys(ns: str, gk: str) -> Dict[str, str]:
    return {
        "to": f"{ns}_to_{gk}", "subject": f"{ns}_subject_{gk}", "cc": f"{ns}_cc_{gk}", "bcc": f"{ns}_bcc_{gk}",
        "body": f"{ns}_body_{gk}", "send": f"{ns}_send_{gk}", "result": f"{ns}_result_{gk}",
        "att_n": f"{ns}_attn_{gk}", "mark_read": f"{ns}_mark_read_{gk}",
    }


def _uploader_key(ns: str, gk: str) -> str:
    # A file uploader cannot be emptied from code, so a new key (a counter
    # bumped after each successful send) is what gives a fresh, empty one.
    return f"{ns}_attachments_{gk}_{st.session_state.get(_keys(ns, gk)['att_n'], 0)}"


# ---------------------------------------------------------------- callbacks (run before the page redraws)

def _select(selected_key: str, conversation: str) -> None:
    st.session_state[selected_key] = conversation


def _mark_read(ctx: InboxContext, group: Dict) -> None:
    for key in all_response_keys_in_group(group):
        ctx.read_keys.add(key)
        ctx.pending_sync_keys.add(key)


def _send_reply(ctx: InboxContext, group: Dict, latest: Dict, lead: Optional[Dict], defaults: Dict, campaign_name: str,
                gk: str) -> None:
    """Validates, commits the payload and starts the send-reply workflow —
    the same steps as the classic Send Reply — then clears the composer so a
    second click cannot send the same text twice. Always leaves a result for
    the page to show; never raises into the page."""
    k = _keys(ctx.namespace, gk)
    to_email = st.session_state.get(k["to"], defaults["to"])
    subject = st.session_state.get(k["subject"], defaults["subject"])
    body = st.session_state.get(k["body"], "")
    cc = parse_email_list(st.session_state.get(k["cc"], ""))
    bcc = parse_email_list(st.session_state.get(k["bcc"], ""))
    uploaded = st.session_state.get(_uploader_key(ctx.namespace, gk)) or []
    attachment_bytes = total_attachment_size_bytes(uploaded) if uploaded else 0

    errors = validate_reply(to_email, body, defaults["sender_account"], cc, bcc, attachment_total_bytes=attachment_bytes)
    if errors:
        st.session_state[k["result"]] = {"ok": False, "messages": list(errors)}
        return
    try:
        attachments = build_attachment_entries(uploaded) if uploaded else None
        payload = build_reply_payload(latest, lead, defaults["sender_account"], to_email, subject, body, cc, bcc,
                                      attachments=attachments)
        path = reply_payload_path(campaign_name, latest.get("ResponseID", "unknown"))
        client = ctx.github_client()
        client.create_file(
            path, payload_to_bytes(payload),
            message=f"Send reply for {campaign_name} (via Streamlit, by {ctx.current_user()})",
        )
        if ctx.pause_before_dispatch:
            time.sleep(ctx.pause_before_dispatch)
        client.dispatch_workflow(WORKFLOW_SEND_REPLY, {"campaign": campaign_name, "payload_path": path})
    except Exception as exc:  # noqa: BLE001 - a failed send must show as a message, never crash the page
        st.session_state[k["result"]] = {"ok": False, "messages": [f"Failed to send: {exc}"]}
        return

    if ctx.read_keys is not None:      # replying means you have seen the whole conversation
        _mark_read(ctx, group)
    st.session_state[k["body"]] = ""
    st.session_state[k["att_n"]] = st.session_state.get(k["att_n"], 0) + 1
    st.session_state[k["result"]] = {"ok": True, "messages": [
        "Reply queued — it'll be sent within a minute or two, and will then appear in this conversation."]}


# ---------------------------------------------------------------- the left pane

def _render_row(row: Dict, selected: str, selected_key: str, ctx: InboxContext) -> None:
    dot = "🔵 " if row["unread"] else ""
    when = f" · {row['when']}" if row["when"] else ""
    st.button(f"{dot}{md_escape(row['name'])}{when}", key=f"{ctx.namespace}_open_{row['key']}",
              on_click=_select, args=(selected_key, row["key"]),
              type="primary" if row["key"] == selected else "secondary", use_container_width=True)
    subject = md_escape(row["subject"])
    st.caption(f"**{subject}**" if row["unread"] else subject)
    st.caption(md_escape(row["preview"]))
    meta = []
    if ctx.show_campaign:
        meta.append(md_escape(row["campaign"]))
    if row["intent"]:
        meta.append(f"🎯 {md_escape(row['intent'])}")
    if row["message_count"] > 1:
        meta.append(f"{row['message_count']} messages")
    if row["unread_count"]:
        meta.append(f"{row['unread_count']} new")
    if meta:
        st.caption(" · ".join(meta))


# ---------------------------------------------------------------- the right pane

def _thread_for(ctx: InboxContext, group: Dict, lead: Optional[Dict], campaign_name: str):
    """(messages, note). With the lead known: everything we sent plus every
    reply, in order. Without it we cannot re-render what was sent, so only the
    replies that arrived are shown — and the note says so."""
    every_reply = ctx.responses_for(campaign_name, group["lead_id"]) or group["messages"]
    sent_by_hand, sent_note = [], ""
    if ctx.sent_for is not None:
        try:
            sent_by_hand = ctx.sent_for(campaign_name, group["lead_id"]) or []
        except Exception:  # noqa: BLE001 - the conversation must still show; only these messages are missing
            sent_note = "The emails you sent yourself could not be loaded just now."
    if lead is None:
        messages = sorted(build_incoming_messages_for_responses(every_reply)
                          + build_outgoing_messages_from_sent_mail(sent_by_hand), key=lambda m: m.get("timestamp", ""))
        return messages, ("No matching lead was found in the Master Sheet (the reply may predate the lead being added), "
                          "so the emails the system sent are not shown." + (f" {sent_note}" if sent_note else ""))
    return build_conversation_thread(ctx.get_campaign_cfg(campaign_name), lead, every_reply, sent_by_hand), sent_note


def _render_bubble(message: Dict) -> None:
    right = bubble_side(message) == "right"
    who = "You" if right else md_escape(sender_display(message.get("from", ""))["name"] or "Them")
    column = st.columns([1, 4])[1] if right else st.columns([4, 1])[0]
    with column:
        with st.chat_message("assistant" if right else "user", avatar="✉️" if right else "👤"):
            st.markdown(f"**{who}** · {md_escape(message.get('timestamp', ''))}")
            subject = (message.get("subject") or "").strip()
            if subject:
                st.caption(md_escape(subject))
            body = message.get("body", "")
            if body.strip():
                st.text(body)
            else:
                # Never an empty bubble: it reads as a bug. Some emails really have no text (a lone
                # screenshot or attachment), and replies logged before HTML emails could be read are
                # empty until repaired — either way, say so and point at the mailbox.
                st.caption("(This email has no readable text — it may be only an image or an attachment. "
                           "Open it in the mailbox to see it.)")


def _render_composer(ctx: InboxContext, group: Dict, latest: Dict, lead: Optional[Dict], campaign_name: str,
                     gk: str) -> None:
    ns = ctx.namespace
    k = _keys(ns, gk)
    defaults = build_reply_defaults(latest, lead)

    result = st.session_state.pop(k["result"], None)
    if result:
        for message in result["messages"]:
            (st.success if result["ok"] else st.error)(message)

    st.text_area("Write a message…", key=k["body"], height=110, label_visibility="collapsed",
                 placeholder="Write a message…")
    with st.expander("To, Cc, Bcc, subject and attachments"):
        st.text_input("To", value=defaults["to"], key=k["to"])
        st.text_input("Subject", value=defaults["subject"], key=k["subject"])
        st.text_input("Cc (comma-separated)", key=k["cc"])
        st.text_input("Bcc (comma-separated)", key=k["bcc"])
        uploaded = st.file_uploader("Attach images or files (optional)", accept_multiple_files=True,
                                    key=_uploader_key(ns, gk))
        if uploaded:
            total_bytes = total_attachment_size_bytes(uploaded)
            st.caption(f"{len(uploaded)} file(s), {total_bytes / (1024 * 1024):.1f} MB total "
                       f"(limit: {outreach.MAX_TOTAL_ATTACHMENT_BYTES / (1024 * 1024):.0f} MB)")

    info_col, send_col = st.columns([4, 1])
    with info_col:
        if defaults["sender_account"]:
            st.caption(f"Sending as: {md_escape(defaults['sender_account'])}")
        else:
            st.caption("⚠️ Couldn't find this lead's sender account — check the Master Sheet.")
    with send_col:
        st.button("Send ➤", type="primary", key=k["send"], on_click=_send_reply,
                  args=(ctx, group, latest, lead, defaults, campaign_name, gk), use_container_width=True)


def _render_conversation(group: Dict, row: Dict, ctx: InboxContext) -> None:
    ns, gk = ctx.namespace, row["key"]
    latest, campaign_name = group["latest"], group["campaign"]
    lead = find_lead_for_response(latest, ctx.leads_for(campaign_name))

    head_col, action_col = st.columns([3, 1])
    with head_col:
        st.subheader(md_escape(row["name"]))
        st.caption(md_escape(row["email"]) + (f" · {md_escape(campaign_name)}" if ctx.show_campaign else ""))
    with action_col:
        if ctx.read_keys is not None and row["unread"]:
            st.button(f"✓ Mark as read ({row['unread_count']})", key=_keys(ns, gk)["mark_read"],
                      on_click=_mark_read, args=(ctx, group), use_container_width=True)

    # What the system actually did about the latest reply — a "Logged Only" outcome means the sequence was NOT stopped.
    if row["action"]:
        stopped = row["action"] == "Stopped Sequence"
        st.caption(f"{'🛑' if stopped else '📝'} {md_escape(row['action'])}" + ("" if stopped else " — sequence NOT stopped"))
    if row["stopped_status"]:
        st.caption(f"🛑 {md_escape(row['stopped_status'])} — automated follow-ups stopped; you can still reply manually below.")
    if row["intent"]:
        confidence = f" ({md_escape(row['intent_confidence'])} confidence)" if row["intent_confidence"] else ""
        st.caption(f"🎯 {md_escape(row['intent'])}{confidence}")

    messages, note = _thread_for(ctx, group, lead, campaign_name)
    newest_first = st.checkbox("Newest message first", value=True, key=f"{ns}_newest_first")
    st.caption("Times are UTC (the Sheet's own clock)." + (f" {note}" if note else ""))
    with st.container(height=THREAD_HEIGHT, border=True):
        if not messages:
            st.caption("No messages found for this lead yet.")
        for message in (reversed(messages) if newest_first else messages):
            _render_bubble(message)

    _render_composer(ctx, group, latest, lead, campaign_name, gk)


# ---------------------------------------------------------------- the whole inbox

def render_inbox(groups: List[Dict], ctx: InboxContext) -> None:
    """`groups` are conversation groups (responses_hub_logic.group_responses_by_
    lead), already filtered and sorted newest first by the caller."""
    if not groups:
        return
    shown = groups[:MAX_ROWS]
    rows = build_conversation_rows(shown, ctx.read_keys, ctx.leads_for)
    by_key = {conversation_key(g): g for g in shown}
    selected_key = f"{ctx.namespace}_selected"
    selected = resolve_selection([r["key"] for r in rows], st.session_state.get(selected_key))
    st.session_state[selected_key] = selected

    list_col, thread_col = st.columns([2, 3], gap="medium")
    with list_col:
        hidden = f" — showing the {MAX_ROWS} most recent; search or filter to narrow it" if len(groups) > MAX_ROWS else ""
        st.caption(f"{len(groups)} conversation{'s' if len(groups) != 1 else ''}{hidden}")
        with st.container(height=LIST_HEIGHT, border=True):
            for row in rows:
                _render_row(row, selected, selected_key, ctx)
    with thread_col:
        _render_conversation(by_key[selected], next(r for r in rows if r["key"] == selected), ctx)
