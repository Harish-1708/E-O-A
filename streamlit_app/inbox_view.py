"""The Inbox, laid out like a mail app's unified inbox: a status rail on the left, a compact conversation list in the
middle, and the open conversation on the right with each email as its own card and a Reply bar underneath — shared
by the all-campaigns Responses page and by every campaign's own Responses tab, so the two cannot drift apart.

It adds NO new way to send, mark read or look anything up. Replying uses the same functions the rest of the app
uses (validate_reply, build_reply_payload, reply_payload_path, build_attachment_entries) and the same two steps —
commit the payload file, then start the send-reply workflow — so what gets committed and dispatched is identical.
Marking read uses the same read keys.

How it looks is in inbox_html (pure builders, tested on their own). The whole page is escaped there: nothing from an
email reaches the page except through html.escape. The only things drawn with Streamlit's own widgets are the
invisible buttons laid over each row (so a click still works) and the Reply bar, the message box and its fields.
"""
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Set

import streamlit as st

import inbox_html as H
import inbox_keywords as K
from inbox_logic import MAX_ROWS, md_escape, build_conversation_rows, resolve_selection, conversation_key, sender_display
import outreach  # noqa: E402  (inbox_logic has put the repo root on sys.path)
from config import WORKFLOW_SEND_REPLY  # noqa: E402
from conversation_logic import (  # noqa: E402
    build_conversation_thread, build_incoming_messages_for_responses, build_outgoing_messages_from_sent_mail,
)
from data_import_logic import payload_to_bytes  # noqa: E402
from responses_hub_logic import all_response_keys_in_group, group_unread_count  # noqa: E402
from responses_reply_logic import (  # noqa: E402
    find_lead_for_response, build_reply_defaults, parse_email_list, validate_reply, build_reply_payload,
    reply_payload_path, build_attachment_entries, total_attachment_size_bytes,
)

PANE_HEIGHT = 700                                   # the three panes share one height; the lists scroll inside it
HEADER_HEIGHT, BAR_HEIGHT = 52, 62
WORKFLOW_SET_RESPONSE_STATUS = "set_response_status.yml"
COLLAPSE_THRESHOLD, KEEP_NEWEST = 8, 5      # a conversation of more than 8 emails shows only the newest 5 until asked for the rest
EMPTY_EMAIL_NOTE = "This email has no readable text — it may be only an image or an attachment. Open it in the mailbox to see it."


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
        "att_n": f"{ns}_attn_{gk}", "mark_read": f"{ns}_mark_read_{gk}", "composer": f"{ns}_composer_{gk}",
        "draft": f"{ns}_draft_{gk}",
        "reply": f"ux_reply_{ns}_{gk}",
    }


def _uploader_key(ns: str, gk: str) -> str:
    # A file uploader cannot be emptied from code, so a new key (a counter
    # bumped after each successful send) is what gives a fresh, empty one.
    return f"{ns}_attachments_{gk}_{st.session_state.get(_keys(ns, gk)['att_n'], 0)}"


# ---------------------------------------------------------------- callbacks (run before the page redraws)

def _pick(state_key: str, value: str) -> None:
    st.session_state[state_key] = value


def _select(selected_key: str, conversation: str) -> None:
    st.session_state[selected_key] = conversation


def _remember_draft(body_key: str, draft_key: str) -> None:
    """Streamlit forgets a widget's value on any run in which it is not drawn — such as when you open another
    conversation — so a half-written reply is copied to its own key whenever it changes and put back when the box
    is drawn again."""
    st.session_state[draft_key] = st.session_state.get(body_key, "")


def _toggle_composer(composer_key: str) -> None:
    st.session_state[composer_key] = not st.session_state.get(composer_key, False)


def _status_changed(ctx: InboxContext, group: Dict, gk: str, widget_key: str) -> None:
    """The Status menu was used: commit a small payload and start the workflow that writes it into the Response Sheet
    (Streamlit itself only has a read-only Sheets credential), then show the new status at once, marked as saving,
    until the Sheet itself says the same. The payload holds no personal data — this repository is public."""
    k = _keys(ctx.namespace, gk)
    status = st.session_state.get(widget_key)
    campaign, lead_id = str(group["campaign"]), str(group["lead_id"])
    if status not in {key for key, _ in K.STATUS_CHOICES} or lead_id.startswith("_no_lead_"):
        return
    now = datetime.now(timezone.utc)
    path = f"status_changes/{re.sub(r'[^A-Za-z0-9_.-]', '_', campaign)}/{now:%Y-%m-%d-%H%M%S}-{now.microsecond:06d}-{re.sub(r'[^A-Za-z0-9_.-]', '_', lead_id)}.json"
    payload = {"campaign": campaign, "lead_id": lead_id, "status": status, "set_at": now.strftime("%Y-%m-%d %H:%M:%S")}
    try:
        client = ctx.github_client()
        client.create_file(path, payload_to_bytes(payload), message=f"Set lead status for {campaign} (via Streamlit)")
        client.dispatch_workflow(WORKFLOW_SET_RESPONSE_STATUS, {"campaign": campaign, "payload_path": path})
    except Exception as exc:  # noqa: BLE001 - a failed save must show as a message, never crash the page
        st.session_state[k["result"]] = {"ok": False, "messages": [f"Couldn't save the status: {exc}"]}
        return
    st.session_state.setdefault(f"{ctx.namespace}_pending_status", {})[gk] = status
    st.session_state[k["result"]] = {"ok": True, "messages": ["Status saved — it will show everywhere, including the Performance page, within a minute or two."]}


def _mark_read(ctx: InboxContext, group: Dict) -> None:
    for key in all_response_keys_in_group(group):
        ctx.read_keys.add(key)
        ctx.pending_sync_keys.add(key)


def _mark_all_read(ctx: InboxContext, view_groups: List[Dict], all_groups: List[Dict], scope_key: str) -> None:
    """Mark every unread conversation in the chosen scope read, with every reply inside it (the same thing 'Mark as read' does for one). Runs BEFORE the page is
    drawn again, so the counts and the Sync button already reflect it. Nothing is written to the Sheet here: like a single conversation, it is saved by
    'Sync read status'."""
    groups = view_groups if st.session_state.get(scope_key, "view") == "view" else all_groups
    replies = sum(group_unread_count(group, ctx.read_keys) for group in groups)               # counted before they are marked
    for group in groups:
        _mark_read(ctx, group)
    st.session_state[f"{ctx.namespace}_markall_result"] = (len(groups), replies)


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def _render_mark_all(ctx: InboxContext, view_groups: List[Dict], all_groups: List[Dict]) -> None:
    """'Mark all read' in the list header: a small menu that says exactly what will happen before it does — which conversations (this view or everything
    unread), how many, and how many replies that is — and only then a confirm button."""
    ns = ctx.namespace
    scope_key = f"{ns}_markall_scope"
    with st.container(key=f"ux_markall_{ns}"):
        with st.popover("✓ Mark all read", disabled=not all_groups):
            st.markdown("**Mark conversations as read**")
            if len(view_groups) != len(all_groups):
                st.radio("Which conversations?", ["view", "all"], index=0 if view_groups else 1, key=scope_key,
                         format_func=lambda choice: (f"Unread in this view ({len(view_groups)})" if choice == "view" else f"All unread ({len(all_groups)})"))
                chosen = view_groups if st.session_state.get(scope_key, "view") == "view" else all_groups
            else:
                chosen = all_groups
            replies = sum(group_unread_count(group, ctx.read_keys) for group in chosen)
            st.caption(f"{_plural(len(chosen), 'conversation', 'conversations')} · {_plural(replies, 'unread reply', 'unread replies')}. They are marked read here at once; "
                       "press “Sync read status” afterwards to save it to the Sheet.")
            st.button(f"✓ Mark {_plural(len(chosen), 'conversation', 'conversations')} as read", key=f"{ns}_markall_go", type="primary", disabled=not chosen,
                      on_click=_mark_all_read, args=(ctx, view_groups, all_groups, scope_key), use_container_width=True)


def _send_reply(ctx: InboxContext, group: Dict, latest: Dict, lead: Optional[Dict], defaults: Dict, campaign_name: str,
                gk: str) -> None:
    """Validates, commits the payload and starts the send-reply workflow —
    the same steps as every other reply — then clears the composer so a
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
    st.session_state[k["draft"]] = ""
    st.session_state[k["att_n"]] = st.session_state.get(k["att_n"], 0) + 1
    st.session_state[k["result"]] = {"ok": True, "messages": [
        "Reply queued — it'll be sent within a minute or two, and will then appear in this conversation."]}


# ---------------------------------------------------------------- the left rail

def _render_rail(ctx: InboxContext, items: List, active: str, rail_key: str) -> None:
    with st.container(key=f"ux_railpane_{ctx.namespace}"):
        st.markdown(H.rail_title_html("Status"), unsafe_allow_html=True)
        for index, (key, label, icon, count) in enumerate(items):
            with st.container(key=f"ux_ritem_{ctx.namespace}_{index}"):
                st.markdown(H.rail_item_html(label, count, icon, key == active), unsafe_allow_html=True)
                st.button(f"Show {label}", key=f"ux_railbtn_{ctx.namespace}_{key}", on_click=_pick, args=(rail_key, key))


# ---------------------------------------------------------------- the list

def _render_list(ctx: InboxContext, shown: List[Dict], total: int, filtered: int, by_key: Dict[str, Dict], selected: Optional[str],
                 selected_key: str, mark_all: Optional[tuple] = None) -> None:
    with st.container(key=f"ux_listpane_{ctx.namespace}"):
        st.markdown(H.list_header_html("Inbox", len(shown), total), unsafe_allow_html=True)
        if mark_all is not None:
            _render_mark_all(ctx, *mark_all)
        with st.container(key=f"ux_list_{ctx.namespace}", height=PANE_HEIGHT - 44, border=False):
            if not shown:
                st.markdown(H.empty_thread_html("No conversations in this view."), unsafe_allow_html=True)
            for index, row in enumerate(shown):
                latest = by_key[row["key"]]["latest"]
                received = str(latest.get("MessageDate") or "").strip() or latest.get("ReceivedAt", "")
                with st.container(key=f"ux_row_{ctx.namespace}_{index}"):
                    st.markdown(
                        H.row_html(row["name"], row["subject"], row["preview"], H.list_date(received),
                                   H.status_of(row["intent"], "" if row["manual"] else row["classification"]), row["key"] == selected, row["unread"],
                                   H.waiting_nudge(row["unread"], received), row["message_count"], row["unread_count"],
                                   row["campaign"] if ctx.show_campaign else ""),
                        unsafe_allow_html=True)
                    # the real button, invisible, laid over the whole row; its label is only for screen readers
                    st.button(f"Open conversation with {md_escape(row['name'])}", key=f"{ctx.namespace}_open_{row['key']}",
                              on_click=_select, args=(selected_key, row["key"]))
        if filtered > len(shown):
            st.caption(f"Showing the {len(shown)} most recent of {filtered} — search or filter to narrow it.")


# ---------------------------------------------------------------- the open conversation

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


def _conversation_intent(group: Dict, row: Dict, pending: Optional[str] = None):
    """(intent, note, set_by_hand) for the whole conversation. In order of authority:
      0. a status set by hand (IntentConfidence "Manual") — beats everything, including a later AI reading;
         `pending` is one just chosen on this page and not yet in the Sheet, shown at once as "saving";
      1. the AI's reading, newest message first, if definite (anything but Unclear);
      2. what the words say, by keywords (inbox_keywords) — either stored by the Label Replies job (IntentConfidence
         "Keywords") or worked out now for a reply not yet labelled.
    A later follow-up that says nothing clear must not turn an 'interested' conversation into 'unclear', a later 'not
    interested' still overrides an earlier 'interested', and a keyword reading of Lead (a question, a mention of money)
    is only a hint, used when nothing clearer exists anywhere in the conversation. The note says where the reading came
    from, so a label worked out from keywords or set by hand is never passed off as the AI's."""
    if pending and pending != "auto":
        return K.STATUS_TO_INTENT[pending], "set manually · saving…", True
    messages = sorted(group["messages"], key=lambda m: str(m.get("MessageDate") or m.get("ReceivedAt") or ""), reverse=True)
    confidence_of = lambda m: str(m.get("IntentConfidence") or "").strip()      # noqa: E731
    if pending != "auto":                                                         # "auto" is a manual status being taken off
        for message in messages:
            if confidence_of(message) == K.MANUAL_CONFIDENCE and (message.get("Intent") or "").strip():
                return message["Intent"].strip(), "set manually", True
    weak = None
    for message in messages:
        intent, confidence = (message.get("Intent") or "").strip(), confidence_of(message)
        if confidence == K.MANUAL_CONFIDENCE:
            intent = ""                                                           # being taken off: treated as blank, so the words are read
        if intent and intent.lower() != "unclear":
            if confidence == K.KEYWORD_CONFIDENCE:
                if K.is_weak(intent):
                    weak = weak or (intent, "by keywords")
                    continue
                return intent, "by keywords", False
            return intent, (f"{confidence} confidence" if confidence else ""), False
        if not str(message.get("Classification") or "").lower().startswith("genuine"):
            continue                                              # an auto-reply's words are not the person's answer
        words = next((v for v in (message.get("FullBody"), message.get("Snippet")) if (v or "").strip()), "")
        reading = K.keyword_reading(words)
        if reading and not K.is_weak(reading[0]):
            return reading[0], "by keywords", False
        if reading and weak is None:
            weak = (reading[0], "by keywords")
    if weak:
        return weak[0], weak[1], False
    if row["intent_confidence"] == K.MANUAL_CONFIDENCE:                       # the latest row's own value is a manual one being ignored
        return "", "", False
    return row["intent"], (f"{row['intent_confidence']} confidence" if row["intent_confidence"] else ""), False


def _sheet_shows(group: Dict, pending: str) -> bool:
    """Has the Response Sheet itself caught up with a status just chosen on this page?"""
    manual = [m for m in group["messages"] if str(m.get("IntentConfidence") or "").strip() == K.MANUAL_CONFIDENCE and (m.get("Intent") or "").strip()]
    if pending == "auto":
        return not manual
    return any((m.get("Intent") or "").strip() == K.STATUS_TO_INTENT[pending] for m in manual)


def _card_for(message: Dict, row: Dict, clamp: bool = False) -> str:
    """One email as a card. Ours go From 'You' To the person; theirs the other way round."""
    if message.get("direction") == "outgoing":
        from_text = "You"
        to_text = f"{row['name']} <{row['email']}>" if row["email"] else row["name"]
    else:
        who = sender_display(message.get("from", ""))
        from_text = f"{who['name']} <{who['email']}>" if who["email"] else who["name"]
        to_text = "You"
    return H.card_html(message.get("subject", ""), from_text, to_text, message.get("timestamp", ""), message.get("body", ""),
                       EMPTY_EMAIL_NOTE, clamp=clamp)


def _render_cards(ctx: InboxContext, messages: List[Dict], row: Dict, gk: str, newest_first: bool) -> None:
    """The emails of one conversation. A long conversation (more than COLLAPSE_THRESHOLD emails) shows only the newest
    KEEP_NEWEST, with a button right where the hidden ones would be to show the rest — and, once shown, a button at the
    top to fold them away again. A long single email is cropped with a "Read more" (never the newest one: that is
    what you are reading). Which are open is remembered per conversation."""
    ns = ctx.namespace
    expanded_key = f"{ns}_expanded_{gk}"
    expanded = st.session_state.get(expanded_key, False)
    collapsible = len(messages) > COLLAPSE_THRESHOLD
    earlier = len(messages) - KEEP_NEWEST
    indexed = list(enumerate(messages))                                  # (position in time order, message)
    ordered = list(reversed(indexed)) if newest_first else indexed

    def toggle(label: str) -> None:
        st.button(label, key=f"ux_expand_{ns}_{gk}", on_click=_toggle_composer, args=(expanded_key,), use_container_width=True)

    def card(index: int, message: Dict) -> None:
        full_key = f"{ns}_full_{gk}_{index}"
        long_email = H.needs_clamp(message.get("body", "")) and index != len(messages) - 1
        opened = st.session_state.get(full_key, False)
        st.markdown(_card_for(message, row, clamp=long_email and not opened), unsafe_allow_html=True)
        if long_email:
            st.button("Read less ▴" if opened else "Read more ▾", key=f"ux_more_{ns}_{gk}_{index}", on_click=_toggle_composer, args=(full_key,))

    if collapsible and expanded:
        toggle(f"▴ Show fewer — hide the {earlier} earlier emails")
        for index, message in ordered:
            card(index, message)
    elif collapsible:
        shown = ordered[:KEEP_NEWEST] if newest_first else ordered[-KEEP_NEWEST:]
        if not newest_first:
            toggle(f"▾ Show {earlier} earlier emails")
        for index, message in shown:
            card(index, message)
        if newest_first:
            toggle(f"▾ Show {earlier} earlier emails")
    else:
        for index, message in ordered:
            card(index, message)


def _render_status_picker(ctx: InboxContext, group: Dict, row: Dict, gk: str) -> None:
    """The Status menu: set this lead's status by hand, or go back to Automatic."""
    if str(group["lead_id"]).startswith("_no_lead_"):
        st.caption("This reply isn't linked to a lead in the Master Sheet, so a status can't be saved for it.")
        return
    labels = dict(K.STATUS_CHOICES)
    current = K.INTENT_TO_STATUS.get(row["intent"], "auto") if row["manual"] else "auto"
    widget_key = f"{ctx.namespace}_statuspick_{gk}"
    st.session_state[widget_key] = current              # always shows what is in force (set before the widget exists)
    st.radio("Lead status", list(labels), format_func=labels.get, key=widget_key, on_change=_status_changed,
             args=(ctx, group, gk, widget_key))
    st.caption("Set by hand, a status beats the AI and the keyword reading everywhere, including the Performance page. "
               "Choose Automatic to go back to the automatic reading.")


def _show_result(ns: str, gk: str) -> None:
    result = st.session_state.pop(_keys(ns, gk)["result"], None)
    if result:
        for message in result["messages"]:
            (st.success if result["ok"] else st.error)(message)


def _render_composer(ctx: InboxContext, group: Dict, latest: Dict, lead: Optional[Dict], campaign_name: str,
                     gk: str) -> None:
    ns = ctx.namespace
    k = _keys(ns, gk)
    defaults = build_reply_defaults(latest, lead)
    if k["body"] not in st.session_state:
        st.session_state[k["body"]] = st.session_state.get(k["draft"], "")
    st.text_area("Write a message…", key=k["body"], height=110, label_visibility="collapsed",
                 placeholder="Write a message…", on_change=_remember_draft, args=(k["body"], k["draft"]))
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
    k = _keys(ns, gk)
    latest, campaign_name = group["latest"], group["campaign"]
    lead = find_lead_for_response(latest, ctx.leads_for(campaign_name))

    # What the system actually did about the latest reply — a "Logged Only" outcome means the sequence was NOT stopped.
    stopped_it = row["action"] == "Stopped Sequence"
    chips = []
    if row["action"]:
        chips.append("Sequence stopped" if stopped_it else f"{row['action']} — sequence NOT stopped")
    if row["stopped_status"] and not stopped_it:           # the lead is stopped for some other reason; worth saying
        chips.append(row["stopped_status"])
    address = row["email"] + (f" · {campaign_name}" if ctx.show_campaign else "")
    st.markdown(H.thread_header_html(row["name"], address, H.status_of(row["intent"], "" if row["manual"] else row["classification"]), chips,
                                     row["intent_note"]),
                unsafe_allow_html=True)

    messages, note = _thread_for(ctx, group, lead, campaign_name)
    if note:
        st.caption(note)
    newest_first = st.session_state.get(f"{ns}_newest_first", True)          # the popover below owns this checkbox
    with st.container(key=f"ux_threadbody_{ns}", height=PANE_HEIGHT - HEADER_HEIGHT - BAR_HEIGHT, border=False):
        if not messages:
            st.markdown(H.empty_thread_html("No messages found for this lead yet."), unsafe_allow_html=True)
        else:
            _render_cards(ctx, messages, row, gk, newest_first)

    _show_result(ns, gk)
    with st.container(key=f"ux_bar_{ns}"):
        st.button("↩ Reply", key=k["reply"], on_click=_toggle_composer, args=(k["composer"],))
        if ctx.read_keys is not None and row["unread"]:
            st.button(f"✓ Mark as read ({row['unread_count']})", key=k["mark_read"], on_click=_mark_read, args=(ctx, group))
        with st.popover("Status"):
            _render_status_picker(ctx, group, row, gk)
        with st.popover("⋯"):
            st.checkbox("Newest message first", value=True, key=f"{ns}_newest_first")
            st.radio("Text size", list(H.SIZES), index=list(H.SIZES).index(H.DEFAULT_SIZE), key=f"{ns}_size", horizontal=True)
    if st.session_state.get(k["composer"], False):
        _render_composer(ctx, group, latest, lead, campaign_name, gk)


# ---------------------------------------------------------------- the whole inbox

def render_inbox(groups: List[Dict], ctx: InboxContext) -> None:
    """`groups` are conversation groups (responses_hub_logic.group_responses_by_
    lead), already filtered and sorted newest first by the caller."""
    if not groups:
        return
    ns = ctx.namespace
    scale = H.SIZES.get(st.session_state.get(f"{ns}_size", H.DEFAULT_SIZE), H.SIZES[H.DEFAULT_SIZE])
    st.markdown(f"<style>{H.inbox_css(scale)}</style>", unsafe_allow_html=True)
    marked = st.session_state.pop(f"{ns}_markall_result", None)
    if marked:
        st.success(f"Marked {_plural(marked[0], 'conversation', 'conversations')} as read ({_plural(marked[1], 'reply', 'replies')}). "
                   "Press “Sync read status” to save it to the Sheet.")
    all_rows = build_conversation_rows(groups, ctx.read_keys, ctx.leads_for)
    by_key = {conversation_key(g): g for g in groups}
    pending_map = st.session_state.setdefault(f"{ns}_pending_status", {})
    for row in all_rows:
        group = by_key[row["key"]]
        pending = pending_map.get(row["key"])
        if pending and _sheet_shows(group, pending):
            pending_map.pop(row["key"], None)                      # the Sheet has caught up: stop saying "saving"
            pending = None
        row["intent"], row["intent_note"], row["manual"] = _conversation_intent(group, row, pending)
    category = {r["key"]: H.status_of(r["intent"], "" if r["manual"] else r["classification"]) for r in all_rows}

    items = [("all", "All", "📥", len(all_rows))]
    if ctx.read_keys is not None:
        items.append(("unread", "Unread", "🔵", sum(1 for r in all_rows if r["unread"])))
    items += [(key, label, icon, sum(1 for r in all_rows if category[r["key"]] == key)) for key, label, icon, _ in H.STATUSES]
    rail_key = f"{ns}_rail"
    active = st.session_state.get(rail_key, "all")
    if active not in [item[0] for item in items]:
        active = "all"

    def kept(row: Dict) -> bool:
        return active == "all" or (row["unread"] if active == "unread" else category[row["key"]] == active)

    filtered = [r for r in all_rows if kept(r)]
    mark_all = None if ctx.read_keys is None else ([by_key[r["key"]] for r in filtered if r["unread"]], [by_key[r["key"]] for r in all_rows if r["unread"]])
    shown = filtered[:MAX_ROWS]
    selected_key = f"{ns}_selected"
    selected = resolve_selection([r["key"] for r in shown], st.session_state.get(selected_key))
    st.session_state[selected_key] = selected

    with st.container(key=f"ux_shell_{ns}"):
        rail_col, list_col, thread_col = st.columns([1.35, 3.0, 5.9], gap="small")
        with rail_col:
            _render_rail(ctx, items, active, rail_key)
        with list_col:
            _render_list(ctx, shown, len(all_rows), len(filtered), by_key, selected, selected_key, mark_all)
        with thread_col:
            with st.container(key=f"ux_threadpane_{ns}"):
                if selected is None:
                    st.markdown(H.empty_thread_html("No conversations in this view."), unsafe_allow_html=True)
                else:
                    _render_conversation(by_key[selected], next(r for r in shown if r["key"] == selected), ctx)
