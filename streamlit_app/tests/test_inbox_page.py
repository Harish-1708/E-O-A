"""Page-level tests for the two-pane Inbox: on the all-campaigns Responses page
and inside each campaign's own Responses tab. The real default layout applies
here (Inbox); the Classic layout is covered by the older tests in
test_pages_smoke.py."""
import base64
import contextlib
import json
import os
from unittest.mock import patch

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import inbox_view
from github_client import GitHubActionsError
from test_pages_smoke import (
    FakeWorksheet, FakeSpreadsheet, FIXTURE_CAMPAIGN, PAGES_DIR, _dashboard_secrets, _authed_session,
    _mock_github_writes,
)

HUB_PAGE = os.path.join(PAGES_DIR, "responses.py")
CAMPAIGNS_PAGE = os.path.join(PAGES_DIR, "campaigns.py")
OTHER = "OtherCampaign"
A1, A5, B5 = f"{FIXTURE_CAMPAIGN}:1", f"{FIXTURE_CAMPAIGN}:5", f"{OTHER}:5"


@pytest.fixture(autouse=True)
def _every_test_uses_the_fixture_repo(fixture_repo):
    """Stable, test-owned campaigns and templates (see conftest), never the live ones."""
    yield


@pytest.fixture(autouse=True)
def _stub_the_live_template_reads_other_tabs_make():
    """A campaign page runs EVERY tab's body on each rerun, and the Sequences
    tab reads templates live from GitHub (see test_pages_smoke's fixture of
    the same purpose). Stubbed so opening a campaign never calls GitHub."""
    filenames = [f"{stage}_{v}.txt" for stage in ["intro", "followup1", "followup2", "followup3", "followup4"] for v in "ABCD"]

    def content(self, path, ref="main"):
        return f"Subject: {path.rsplit('/', 1)[-1][:-4]}\n\nBody.".encode("utf-8")

    with patch("github_client.GitHubClient.list_directory_files", lambda self, path, ref="main": list(filenames)), \
         patch("github_client.GitHubClient.get_file_content", content):
        yield


@pytest.fixture(autouse=True)
def _clear_streamlit_caches():
    st.cache_resource.clear()
    st.cache_data.clear()
    yield
    st.cache_resource.clear()
    st.cache_data.clear()


# ---------------------------------------------------------------- data

def _resp(rid, lead, sender, subject, snippet, received, **extra):
    row = {"ResponseID": rid, "LeadID": lead, "From": sender, "Subject": subject, "Snippet": snippet,
           "FullBody": extra.pop("FullBody", snippet), "Classification": "Genuine Reply", "ReceivedAt": received,
           "MessageID": f"<{rid}@mail.gmail.com>", "ActionTaken": "Stopped Sequence"}
    row.update(extra)
    return row


def _two_campaign_ws(extra_a_responses=()):
    """Campaign A: Kari (lead 1, two messages, replied to our intro) and Zed (lead 5).
    Campaign B: a DIFFERENT person who is also lead 5 — lead IDs restart in every campaign."""
    return {
        f"{FIXTURE_CAMPAIGN} Master Sheet": FakeWorksheet([
            {"LeadID": "1", "Email": "kari@abc.com", "FirstName": "Kari", "SenderAccount": "sales1",
             "IntroSentAt": "2026-10-10 10:00:00", "IntroVariant": "A", "Status": "Stopped - Replied",
             "ThreadReferences": "<our1@mail.gmail.com>"},
            {"LeadID": "5", "Email": "zed@abc.com", "FirstName": "Zed", "SenderAccount": "sales1", "Status": ""},
        ]),
        f"{FIXTURE_CAMPAIGN} Response Sheet": FakeWorksheet([
            _resp("rA1", "1", '"Kari B" <kari@abc.com>', "Re: Partnership", "Interested, tell me more about rates",
                  "2026-10-12 14:00:00", FullBody="Hi! Interested — what are your rates?", Intent="Interested",
                  IntentConfidence="High"),
            _resp("rA2", "1", '"Kari B" <kari@abc.com>', "Re: Partnership", "Also do you cover shipping?",
                  "2026-10-12 14:30:00", FullBody="Also, do you cover shipping?"),
            _resp("rA3", "5", '"Zed Z" <zed@abc.com>', "Re: Hello", "Not for me thanks", "2026-10-11 09:00:00",
                  FullBody="Not for me, thanks.", Intent="Not Interested", IntentConfidence="High"),
            *extra_a_responses,
        ]),
        f"{FIXTURE_CAMPAIGN} Custom Log Sheet": FakeWorksheet([]),
        f"{FIXTURE_CAMPAIGN} Error Log": FakeWorksheet([]),
        f"{OTHER} Master Sheet": FakeWorksheet([
            {"LeadID": "5", "Email": "stranger@b.com", "FirstName": "Stranger", "SenderAccount": "sales2", "Status": ""}]),
        f"{OTHER} Response Sheet": FakeWorksheet([
            _resp("rB1", "5", '"Stranger" <stranger@b.com>', "Re: Other thing", "I am a different person",
                  "2026-10-12 12:00:00", FullBody="I am a different person entirely")]),
        f"{OTHER} Custom Log Sheet": FakeWorksheet([]),
        f"{OTHER} Error Log": FakeWorksheet([]),
    }


@contextlib.contextmanager
def hub(ws=None, dispatch_error=None):
    """The all-campaigns Responses page, running against fake Sheets and a fake GitHub."""
    ws = ws or _two_campaign_ws()
    captured, fake_create_file = _mock_github_writes()

    def fake_dispatch(self, workflow_file, inputs, ref="main"):
        if dispatch_error:
            raise dispatch_error
        captured.setdefault("dispatches", []).append((workflow_file, inputs))
        return {"id": 1, "html_url": "https://github.com/x"}

    with patch("gspread.authorize", return_value=type("C", (), {"open_by_key": lambda self, k: FakeSpreadsheet(ws)})()), \
         patch("google.oauth2.service_account.Credentials.from_service_account_info", return_value=object()), \
         patch("preview_logic.list_campaigns", return_value=[FIXTURE_CAMPAIGN, OTHER]), \
         patch("preview_logic.get_campaign_cfg", lambda name: {
             "_campaign_name": name, "master_tab": f"{name} Master Sheet", "responses_tab": f"{name} Response Sheet",
             "send_log_tab": f"{name} Custom Log Sheet", "error_log_tab": f"{name} Error Log"}), \
         patch("github_client.GitHubClient.create_file", fake_create_file), \
         patch("github_client.GitHubClient.dispatch_workflow", fake_dispatch):
        at = AppTest.from_file(HUB_PAGE)
        at.secrets.update(_dashboard_secrets())
        for key, value in _authed_session().items():
            at.session_state[key] = value
        at.run(timeout=25)
        yield at, captured


def _open_buttons(at, namespace="hub"):
    return [b for b in at.button if (b.key or "").startswith(f"{namespace}_open_")]


def _row_keys(at, namespace="hub"):
    return [b.key[len(f"{namespace}_open_"):] for b in _open_buttons(at, namespace)]


def _thread_texts(at):
    return [t.value for t in at.text]


def _committed(captured):
    return json.loads(captured["commits"][0]["content"].decode("utf-8"))


# ================================================================ the all-campaigns page

def test_the_inbox_is_the_default_layout_with_a_list_and_an_open_conversation():
    with hub() as (at, _):
        assert list(at.exception) == [], list(at.exception)
        assert list(at.error) == []
        assert at.radio(key="responses_view").value == "Inbox"
        assert _row_keys(at) == [A1, B5, A5]                                  # newest first, one row per conversation
        assert at.session_state["hub_selected"] == A1                         # the newest is open
        assert "Kari B" in [s.value for s in at.subheader]
        assert not any("View full conversation" in e.label for e in at.expander)     # not the classic cards


def test_clicking_a_conversation_opens_it():
    with hub() as (at, _):
        at.button(key=f"hub_open_{A5}").click()
        at.run(timeout=25)
        assert at.session_state["hub_selected"] == A5
        assert "Zed Z" in [s.value for s in at.subheader] and "Kari B" not in [s.value for s in at.subheader]
        assert "Not for me, thanks." in _thread_texts(at)


def test_a_conversation_shows_our_emails_and_theirs_in_chat_bubbles_newest_first():
    with hub() as (at, _):
        bubbles = list(at.chat_message)
        assert [b.avatar for b in bubbles].count("👤") == 2                  # Kari's two replies
        assert [b.avatar for b in bubbles].count("✉️") == 1                   # our intro, re-rendered from its template
        assert [b.avatar for b in bubbles][0] == "👤"                          # newest first: her latest reply is on top
        texts = _thread_texts(at)
        assert texts.index("Also, do you cover shipping?") < texts.index("Hi! Interested — what are your rates?")


def test_turning_off_newest_first_puts_the_thread_in_chronological_order():
    with hub() as (at, _):
        at.checkbox(key="hub_newest_first").uncheck()
        at.run(timeout=25)
        texts = _thread_texts(at)
        assert texts.index("Hi! Interested — what are your rates?") < texts.index("Also, do you cover shipping?")
        assert [b.avatar for b in at.chat_message][0] == "✉️"                   # oldest first: our intro leads


def test_unread_conversations_show_a_dot_and_how_many_are_new():
    with hub() as (at, _):
        labels = {b.key[len("hub_open_"):]: b.label for b in _open_buttons(at)}
        assert all(label.startswith("🔵 ") for label in labels.values())     # nothing has been read yet
        captions = " ".join(c.value for c in at.caption)
        assert "2 new" in captions                                              # Kari: two unread messages


def test_merely_opening_or_loading_never_marks_anything_read():
    with hub() as (at, _):
        at.button(key=f"hub_open_{A5}").click()
        at.run(timeout=25)
        assert at.session_state["read_response_keys"] == set()
        assert at.session_state["pending_sync_keys"] == set()


def test_mark_as_read_marks_the_whole_conversation_and_queues_the_sync():
    with hub() as (at, _):
        at.button(key=f"hub_mark_read_{A1}").click()
        at.run(timeout=25)
        expected = {f"{FIXTURE_CAMPAIGN}:rA1", f"{FIXTURE_CAMPAIGN}:rA2"}
        assert at.session_state["read_response_keys"] == expected == at.session_state["pending_sync_keys"]
        assert not {b.key: b.label for b in _open_buttons(at)}[f"hub_open_{A1}"].startswith("🔵")
        assert not any((b.key or "") == f"hub_mark_read_{A1}" for b in at.button)    # nothing left to mark


def test_the_unread_chip_hides_conversations_that_have_been_read():
    with hub() as (at, _):
        at.button(key=f"hub_mark_read_{A1}").click()
        at.run(timeout=25)
        at.radio(key="responses_inbox_chip").set_value("Unread only")
        at.run(timeout=25)
        assert A1 not in _row_keys(at) and set(_row_keys(at)) == {B5, A5}


def test_search_narrows_the_list():
    with hub() as (at, _):
        at.text_input(key="responses_search_query").set_value("rates")
        at.run(timeout=25)
        assert _row_keys(at) == [A1]
        at.text_input(key="responses_search_query").set_value("stranger")
        at.run(timeout=25)
        assert _row_keys(at) == [B5]


def test_filtering_out_the_open_conversation_falls_back_to_the_first_one_left():
    with hub() as (at, _):
        at.button(key=f"hub_open_{A5}").click()
        at.run(timeout=25)
        at.text_input(key="responses_search_query").set_value("stranger")
        at.run(timeout=25)
        assert list(at.exception) == []
        assert at.session_state["hub_selected"] == B5


def test_nothing_matching_says_so_and_does_not_crash():
    with hub() as (at, _):
        at.text_input(key="responses_search_query").set_value("zzzz-no-such-text")
        at.run(timeout=25)
        assert list(at.exception) == []
        assert any("No responses match these filters." in i.value for i in at.info)
        assert _open_buttons(at) == []


# ---- the cross-campaign bug (found while building this)

def test_each_conversation_shows_only_its_own_campaigns_messages_even_when_lead_ids_match():
    """REGRESSION: Campaign A's lead 5 and Campaign B's lead 5 are two different people.
    The page used to build A's thread from every campaign's lead-5 replies."""
    with hub() as (at, _):
        at.button(key=f"hub_open_{A5}").click()
        at.run(timeout=25)
        texts = _thread_texts(at)
        assert "Not for me, thanks." in texts and "I am a different person entirely" not in texts
        at.button(key=f"hub_open_{B5}").click()
        at.run(timeout=25)
        texts = _thread_texts(at)
        assert "I am a different person entirely" in texts and "Not for me, thanks." not in texts


def test_the_classic_layout_no_longer_mixes_campaigns_either():
    with hub() as (at, _):
        at.radio(key="responses_view").set_value("Classic")
        at.run(timeout=25)
        texts = _thread_texts(at)
        # B's message belongs to exactly one conversation (its own). Under the bug it also appeared in A's lead-5 thread.
        assert texts.count("I am a different person entirely") == 1
        assert texts.count("Not for me, thanks.") == 1


# ---- sending a reply

def test_sending_a_reply_commits_the_payload_starts_the_workflow_clears_the_box_and_marks_read():
    with hub() as (at, captured):
        at.text_area(key=f"hub_body_{A1}").set_value("Thanks Kari — our rate is $150.")
        at.run(timeout=25)
        at.button(key=f"hub_send_{A1}").click()
        at.run(timeout=25)
        assert list(at.exception) == [], list(at.exception)
        assert list(at.error) == []
        assert any("Reply queued" in s.value for s in at.success)
        payload = _committed(captured)
        assert captured["commits"][0]["path"].startswith(f"replies/{FIXTURE_CAMPAIGN}/")
        assert payload["to"] == "kari@abc.com" and payload["sender_account"] == "sales1"
        assert payload["subject"] == "Re: Partnership" and payload["body"] == "Thanks Kari — our rate is $150."
        assert payload["in_reply_to"] == "<rA2@mail.gmail.com>"                # threaded off her LATEST message
        assert payload["references"] == "<our1@mail.gmail.com> <rA2@mail.gmail.com>"
        assert captured["dispatches"] == [("send_reply.yml", {"campaign": FIXTURE_CAMPAIGN, "payload_path": captured["commits"][0]["path"]})]
        assert at.text_area(key=f"hub_body_{A1}").value == ""                  # cleared: a second click cannot send it twice
        assert at.session_state["pending_sync_keys"] == {f"{FIXTURE_CAMPAIGN}:rA1", f"{FIXTURE_CAMPAIGN}:rA2"}


def test_a_reply_goes_out_through_the_right_campaign_when_lead_ids_collide():
    with hub() as (at, captured):
        at.button(key=f"hub_open_{B5}").click()
        at.run(timeout=25)
        at.text_area(key=f"hub_body_{B5}").set_value("Hello Stranger")
        at.run(timeout=25)
        at.button(key=f"hub_send_{B5}").click()
        at.run(timeout=25)
        payload = _committed(captured)
        assert payload["to"] == "stranger@b.com" and payload["sender_account"] == "sales2"
        assert captured["dispatches"][0][1]["campaign"] == OTHER


def test_a_blank_message_is_refused_and_nothing_is_committed():
    with hub() as (at, captured):
        at.button(key=f"hub_send_{A1}").click()
        at.run(timeout=25)
        assert list(at.exception) == []
        assert at.error and "commits" not in captured and "dispatches" not in captured


def test_the_to_and_subject_fields_can_be_edited_before_sending():
    with hub() as (at, captured):
        at.text_input(key=f"hub_to_{A1}").set_value("someone.else@abc.com")
        at.text_input(key=f"hub_subject_{A1}").set_value("A different subject")
        at.text_area(key=f"hub_body_{A1}").set_value("Redirected.")
        at.run(timeout=25)
        at.button(key=f"hub_send_{A1}").click()
        at.run(timeout=25)
        payload = _committed(captured)
        assert payload["to"] == "someone.else@abc.com" and payload["subject"] == "A different subject"


def test_a_search_narrows_the_list_but_the_open_conversation_still_shows_every_message():
    """The list is filtered by what matches; the conversation itself must stay complete,
    or searching for one word would silently hide the rest of a thread."""
    with hub() as (at, _):
        at.text_input(key="responses_search_query").set_value("rates")           # matches only Kari's FIRST message
        at.run(timeout=25)
        assert _row_keys(at) == [A1]
        texts = _thread_texts(at)
        assert "Hi! Interested — what are your rates?" in texts and "Also, do you cover shipping?" in texts


def test_cc_and_bcc_are_sent():
    with hub() as (at, captured):
        at.text_input(key=f"hub_cc_{A1}").set_value("boss@abc.com, other@abc.com")
        at.text_input(key=f"hub_bcc_{A1}").set_value("archive@abc.com")
        at.text_area(key=f"hub_body_{A1}").set_value("Copying you in.")
        at.run(timeout=25)
        at.button(key=f"hub_send_{A1}").click()
        at.run(timeout=25)
        payload = _committed(captured)
        assert payload["cc"] == ["boss@abc.com", "other@abc.com"] and payload["bcc"] == ["archive@abc.com"]


def test_an_attachment_round_trips_and_the_uploader_is_emptied_afterwards():
    with hub() as (at, captured):
        uploader_key = f"hub_attachments_{A1}_0"
        at.file_uploader(key=uploader_key).upload("photo.png", b"fake-png-bytes", "image/png")
        at.text_area(key=f"hub_body_{A1}").set_value("Here is the photo.")
        at.run(timeout=25)
        at.button(key=f"hub_send_{A1}").click()
        at.run(timeout=25)
        assert list(at.exception) == []
        attachments = _committed(captured)["attachments"]
        assert attachments == [{"filename": "photo.png", "content_base64": base64.b64encode(b"fake-png-bytes").decode("ascii")}]
        assert any((u.key or "") == f"hub_attachments_{A1}_1" for u in at.file_uploader)    # a fresh, empty uploader
        assert not any((u.key or "") == uploader_key for u in at.file_uploader)


def test_no_attachment_means_no_attachments_key_in_the_payload():
    with hub() as (at, captured):
        at.text_area(key=f"hub_body_{A1}").set_value("No file this time.")
        at.run(timeout=25)
        at.button(key=f"hub_send_{A1}").click()
        at.run(timeout=25)
        assert "attachments" not in _committed(captured)


def test_a_failed_send_shows_the_reason_keeps_the_text_and_marks_nothing_read():
    with hub(dispatch_error=GitHubActionsError("boom")) as (at, captured):
        at.text_area(key=f"hub_body_{A1}").set_value("Important reply")
        at.run(timeout=25)
        at.button(key=f"hub_send_{A1}").click()
        at.run(timeout=25)
        assert list(at.exception) == []
        assert any("Failed to send: boom" in e.value for e in at.error)
        assert at.text_area(key=f"hub_body_{A1}").value == "Important reply"        # not lost
        assert at.session_state["pending_sync_keys"] == set()


# ---- the Classic switch

def test_switching_to_classic_shows_the_previous_card_layout():
    with hub() as (at, _):
        at.radio(key="responses_view").set_value("Classic")
        at.run(timeout=25)
        assert list(at.exception) == []
        assert any("View full conversation" in e.label for e in at.expander)
        assert _open_buttons(at) == []
        assert any(s.key == "responses_inbox_filter" for s in at.selectbox)        # the classic "More" filter is back


# ---- hostile text and large inboxes

def test_hostile_text_in_a_sender_name_or_subject_is_shown_as_plain_text():
    hostile = _resp("rX", "9", '"[click](http://evil.example) **BOLD** :red[x]" <x@evil.example>',
                    "![pixel](http://evil.example/p.png) $$bad$$", "[more](http://evil.example)", "2026-10-12 15:00:00")
    ws = _two_campaign_ws(extra_a_responses=[hostile])
    ws[f"{FIXTURE_CAMPAIGN} Master Sheet"] = FakeWorksheet([
        {"LeadID": "1", "Email": "kari@abc.com", "SenderAccount": "sales1"}, {"LeadID": "9", "Email": "x@evil.example", "SenderAccount": "sales1"}])
    with hub(ws) as (at, _):
        assert list(at.exception) == []
        label = {b.key: b.label for b in _open_buttons(at)}[f"hub_open_{FIXTURE_CAMPAIGN}:9"]
        assert "\\[click\\]\\(http" in label and "\\*\\*BOLD\\*\\*" in label            # every markdown character escaped
        captions = " ".join(c.value for c in at.caption)
        assert "\\!\\[pixel\\]" in captions and "![pixel]" not in captions
        assert "\\[more\\]\\(http" in captions and "[more](http" not in captions     # the preview line is escaped too


def test_a_reply_with_no_matching_lead_still_shows_what_was_received_and_says_what_cannot_be_shown():
    """A reply can predate its lead being added (or the lead was removed): the conversation
    cannot re-render what we sent, but must not hide what the person wrote."""
    orphan = _resp("rZ", "77", '"Nobody Known" <nobody@abc.com>', "Re: Who?", "Who am I?", "2026-10-12 16:00:00",
                   FullBody="Who am I replying to?")
    with hub(_two_campaign_ws(extra_a_responses=[orphan])) as (at, _):
        assert list(at.exception) == []
        assert f"{FIXTURE_CAMPAIGN}:77" in _row_keys(at)
        assert at.session_state["hub_selected"] == f"{FIXTURE_CAMPAIGN}:77"          # it is the newest
        assert "Who am I replying to?" in _thread_texts(at)
        assert any("so the emails the system sent are not shown" in c.value for c in at.caption)
        assert any("Couldn't find this lead's sender account" in c.value for c in at.caption)


def test_an_email_with_no_readable_text_says_so_instead_of_showing_an_empty_bubble():
    """REPORTED: some conversations showed a name and a time, then nothing. A bubble with no text reads as a bug."""
    blank = _resp("rE", "5", '"Zed Z" <zed@abc.com>', "Re: Hello", "", "2026-10-12 17:00:00", FullBody="")
    with hub(_two_campaign_ws(extra_a_responses=[blank])) as (at, _):
        at.button(key=f"hub_open_{A5}").click()
        at.run(timeout=25)
        assert list(at.exception) == []
        notes = [c.value for c in at.caption if "has no readable text" in c.value]
        assert len(notes) == 1                                                  # only the blank one; the real reply still shows
        assert "Not for me, thanks." in _thread_texts(at)


def test_the_list_says_when_an_email_has_no_text():
    blank = _resp("rE", "5", '"Zed Z" <zed@abc.com>', "Re: Hello", "", "2026-10-12 17:00:00", FullBody="")
    with hub(_two_campaign_ws(extra_a_responses=[blank])) as (at, _):
        assert any("no text in this email" in c.value for c in at.caption)


def test_each_message_shows_its_subject():
    with hub() as (at, _):
        captions = [c.value.replace("\\", "") for c in at.caption]
        assert any(c == "Re: Partnership" for c in captions)


# ---- the emails the team sent by hand, or from this page

SENT_TAB = f"{FIXTURE_CAMPAIGN} Sent Mail"


def _sent(mid, at, body, lead="1", subject="Re: Partnership", source="mailbox"):
    return {"MessageID": mid, "LeadID": lead, "SentAt": at, "SenderAccount": "sales1", "To": "kari@abc.com", "Cc": "",
            "Subject": subject, "Body": body, "InReplyTo": "", "Source": source}


def _with_sent_mail(*rows, ws=None):
    ws = ws or _two_campaign_ws()
    ws[SENT_TAB] = FakeWorksheet(list(rows))
    return ws


def _chronological(at):
    at.checkbox(key="hub_newest_first").uncheck()
    at.run(timeout=25)


def _order_of(texts, *needles):
    positions = [next(i for i, t in enumerate(texts) if n in t) for n in needles]
    return positions == sorted(positions)


def test_what_you_typed_by_hand_appears_in_the_conversation_in_the_right_place():
    ws = _with_sent_mail(_sent("<m1@gmail>", "2026-10-12 14:10:00", "We can offer $200 for six months."),
                         _sent("<m2@gmail>", "2026-10-12 14:45:00", "Great, I will send the agreement now."))
    with hub(ws) as (at, _):
        _chronological(at)
        assert list(at.exception) == [] and list(at.error) == []
        texts = _thread_texts(at)
        assert _order_of(texts, "Hi! Interested", "We can offer $200", "Also, do you cover shipping?", "Great, I will send the agreement")
        assert [b.avatar for b in at.chat_message].count("✉️") == 3               # the template intro + the two you typed
        assert any(m.value.replace("\\", "").startswith("**You** · 2026-10-12 14:10:00") for m in at.markdown)   # as a person reads it


def test_your_quick_answer_never_appears_before_the_question_it_answers():
    """Her reply was LOGGED at 14:30 but really sent at 14:20; you answered at 14:25. Ordering by the logged time
    would show your answer first."""
    ws = _two_campaign_ws()
    records = ws[f"{FIXTURE_CAMPAIGN} Response Sheet"]._records
    for record in records:
        if record["ResponseID"] == "rA2":
            record["MessageDate"] = "2026-10-12 14:20:00"
    ws = _with_sent_mail(_sent("<m@gmail>", "2026-10-12 14:25:00", "I have just sent the request."), ws=ws)
    with hub(ws) as (at, _):
        _chronological(at)
        assert _order_of(_thread_texts(at), "Also, do you cover shipping?", "I have just sent the request.")


def test_the_same_conversation_is_complete_inside_the_campaigns_own_tab():
    ws = _with_sent_mail(_sent("<m1@gmail>", "2026-10-12 14:10:00", "We can offer $200 for six months."))
    with _campaign_tab(ws) as (at, _):
        assert list(at.exception) == [] and list(at.error) == []
        texts = _thread_texts(at)
        assert "We can offer $200 for six months." in texts and "Hi! Interested — what are your rates?" in texts


def test_a_campaign_whose_sent_mail_tab_does_not_exist_yet_shows_no_error_and_no_note():
    with hub() as (at, _):
        assert list(at.exception) == [] and list(at.error) == []
        assert not any("could not be loaded" in c.value for c in at.caption)


def test_if_the_sent_mail_cannot_be_loaded_the_conversation_still_shows_with_a_note():
    class Exploding(FakeWorksheet):
        def get_all_records(self):
            raise RuntimeError("quota exceeded")
    ws = _two_campaign_ws()
    ws[SENT_TAB] = Exploding([])
    with hub(ws) as (at, _):
        assert list(at.exception) == [] and list(at.error) == []
        assert "Hi! Interested — what are your rates?" in _thread_texts(at)             # her replies are all still there
        assert any("emails you sent yourself could not be loaded" in c.value for c in at.caption)


def test_another_campaigns_sent_mail_for_the_same_lead_number_is_never_mixed_in():
    ws = _with_sent_mail(_sent("<a5@gmail>", "2026-10-12 15:00:00", "Message to A's lead five", lead="5"))
    ws[f"{OTHER} Sent Mail"] = FakeWorksheet([_sent("<b5@gmail>", "2026-10-12 15:10:00", "Message to B's lead five", lead="5")])
    with hub(ws) as (at, _):
        at.button(key=f"hub_open_{A5}").click()
        at.run(timeout=25)
        texts = _thread_texts(at)
        assert "Message to A's lead five" in texts and "Message to B's lead five" not in texts
        at.button(key=f"hub_open_{B5}").click()
        at.run(timeout=25)
        texts = _thread_texts(at)
        assert "Message to B's lead five" in texts and "Message to A's lead five" not in texts


def test_only_the_open_leads_messages_are_shown_not_another_leads_in_the_same_campaign():
    ws = _with_sent_mail(_sent("<z@gmail>", "2026-10-12 15:00:00", "Written to Zed only", lead="5"))
    with hub(ws) as (at, _):
        assert "Written to Zed only" not in _thread_texts(at)                          # Kari (lead 1) is open


def test_a_sent_message_with_no_text_says_so_like_any_other_empty_email():
    ws = _with_sent_mail(_sent("<empty@gmail>", "2026-10-12 14:10:00", ""))
    with hub(ws) as (at, _):
        assert any("has no readable text" in c.value for c in at.caption)


def test_the_message_after_sending_a_reply_says_it_will_appear_in_the_conversation():
    with hub() as (at, _):
        at.text_area(key=f"hub_body_{A1}").set_value("Thanks!")
        at.run(timeout=25)
        at.button(key=f"hub_send_{A1}").click()
        at.run(timeout=25)
        assert any("will then appear in this conversation" in s.value for s in at.success)


def test_the_refresh_button_brings_in_messages_stored_since_the_page_loaded():
    ws = _with_sent_mail()
    with hub(ws) as (at, _):
        assert "Newly stored message" not in _thread_texts(at)
        ws[SENT_TAB]._records.append(_sent("<new@gmail>", "2026-10-12 15:00:00", "Newly stored message"))
        at.run(timeout=25)
        assert "Newly stored message" not in _thread_texts(at)                 # still the cached copy: it is only kept for a few seconds
        next(b for b in at.button if b.label == "🔄 Refresh").click()
        at.run(timeout=25)
        assert "Newly stored message" in _thread_texts(at)


def test_the_campaign_pages_refresh_data_button_does_the_same():
    ws = _with_sent_mail()
    with _campaign_tab(ws) as (at, _):
        assert "Newly stored message" not in _thread_texts(at)
        ws[SENT_TAB]._records.append(_sent("<new@gmail>", "2026-10-12 15:00:00", "Newly stored message"))
        at.run(timeout=25)
        assert "Newly stored message" not in _thread_texts(at)
        next(b for b in at.button if b.label == "🔄 Refresh data").click()
        at.run(timeout=25)
        assert "Newly stored message" in _thread_texts(at)


def test_the_reported_conversation_shows_every_message_in_true_order():
    """The reported case, with made-up content: a creator replied five times and you answered by hand six times
    from the mailbox. Four of her replies were stored with no text until repaired (shown here as repaired)."""
    ws = _two_campaign_ws()
    ws[f"{FIXTURE_CAMPAIGN} Response Sheet"] = FakeWorksheet([
        _resp("r1", "1", '"Kari B" <kari@abc.com>', "Re: Partnership", "ask1", "2026-10-12 12:04:00", FullBody="Her message ONE", MessageDate="2026-10-12 12:00:00"),
        _resp("r2", "1", '"Kari B" <kari@abc.com>', "Re: Partnership", "ask2", "2026-10-12 12:33:00", FullBody="Her message TWO", MessageDate="2026-10-12 12:30:00"),
        _resp("r3", "1", '"Kari B" <kari@abc.com>', "Re: Partnership", "ask3", "2026-10-12 12:58:00", FullBody="Her message THREE", MessageDate="2026-10-12 12:55:00"),
        _resp("r4", "1", '"Kari B" <kari@abc.com>', "Re: Partnership", "ask4", "2026-10-12 13:25:00", FullBody="Her message FOUR", MessageDate="2026-10-12 13:09:00"),
        _resp("r5", "1", '"Kari B" <kari@abc.com>', "Re: Partnership", "ask5", "2026-10-14 15:30:00", FullBody="Her message FIVE", MessageDate="2026-10-14 15:16:00"),
        _resp("o1", "5", '"Zed Z" <zed@abc.com>', "Re: Hello", "x", "2026-10-11 09:00:00", FullBody="Not for me, thanks."),
    ])
    mine = [("2026-10-12 12:22:00", "My message ONE"), ("2026-10-12 12:46:00", "My message TWO"), ("2026-10-12 12:55:00", "My message THREE"),
            ("2026-10-12 13:13:00", "My message FOUR"), ("2026-10-14 14:32:00", "My message FIVE"), ("2026-10-14 17:47:00", "My message SIX")]
    ws[SENT_TAB] = FakeWorksheet([_sent(f"<mine{i}@gmail>", at, text) for i, (at, text) in enumerate(mine, start=1)])
    with hub(ws) as (at, _):
        _chronological(at)
        assert list(at.exception) == []
        texts = _thread_texts(at)
        assert _order_of(texts, "Her message ONE", "My message ONE", "Her message TWO", "My message TWO", "My message THREE",
                         "Her message THREE", "Her message FOUR", "My message FOUR", "My message FIVE", "Her message FIVE", "My message SIX")
        assert len([b for b in at.chat_message]) == 1 + 5 + 6                           # the template intro + all eleven
        assert not any("has no readable text" in c.value for c in at.caption)


def test_hand_typed_messages_still_show_when_the_lead_cannot_be_found_in_the_master_sheet():
    orphan = _resp("rZ", "77", '"Nobody Known" <nobody@abc.com>', "Re: Who?", "Who am I?", "2026-10-12 16:00:00",
                   FullBody="Who am I replying to?", MessageDate="2026-10-12 15:50:00")
    ws = _with_sent_mail(_sent("<o@gmail>", "2026-10-12 15:55:00", "Replying to someone not on the sheet", lead="77"),
                         ws=_two_campaign_ws(extra_a_responses=[orphan]))
    with hub(ws) as (at, _):
        _chronological(at)
        texts = _thread_texts(at)
        assert _order_of(texts, "Who am I replying to?", "Replying to someone not on the sheet")


def test_a_long_list_is_capped_and_says_how_many_are_shown(monkeypatch):
    monkeypatch.setattr(inbox_view, "MAX_ROWS", 2)
    with hub() as (at, _):
        assert len(_open_buttons(at)) == 2
        assert any("3 conversations — showing the 2 most recent" in c.value for c in at.caption)


# ================================================================ a campaign's own Responses tab

def _campaign_tab(ws=None, dispatch_error=None):
    return _CampaignTab(ws or _two_campaign_ws(), dispatch_error)


@contextlib.contextmanager
def _CampaignTab(ws, dispatch_error=None):
    captured, fake_create_file = _mock_github_writes()

    def fake_dispatch(self, workflow_file, inputs, ref="main"):
        if dispatch_error:
            raise dispatch_error
        captured.setdefault("dispatches", []).append((workflow_file, inputs))
        return {"id": 1, "html_url": "https://github.com/x"}

    with patch("gspread.authorize", return_value=type("C", (), {"open_by_key": lambda self, k: FakeSpreadsheet(ws)})()), \
         patch("google.oauth2.service_account.Credentials.from_service_account_info", return_value=object()), \
         patch("github_client.GitHubClient.create_file", fake_create_file), \
         patch("github_client.GitHubClient.dispatch_workflow", fake_dispatch):
        at = AppTest.from_file(CAMPAIGNS_PAGE)
        at.secrets.update(_dashboard_secrets())
        for key, value in _authed_session().items():
            at.session_state[key] = value
        at.session_state["selected_campaign"] = FIXTURE_CAMPAIGN
        at.run(timeout=25)
        yield at, captured


TAB = f"tab_{FIXTURE_CAMPAIGN}"


def test_the_campaign_tab_opens_in_the_inbox_layout_with_only_that_campaigns_conversations():
    with _campaign_tab() as (at, _):
        assert list(at.exception) == [], list(at.exception)
        assert list(at.error) == []
        assert at.radio(key=f"responses_view_{FIXTURE_CAMPAIGN}").value == "Inbox"
        assert _row_keys(at, TAB) == [A1, A5]                                    # Campaign B's conversation is not here
        assert at.session_state[f"{TAB}_selected"] == A1
        assert len(at.tabs) == 6                                                  # the tab count is unchanged


def test_the_campaign_tab_has_no_unread_tracking_so_nothing_is_marked_unread():
    with _campaign_tab() as (at, _):
        assert not any(b.label.startswith("🔵") for b in _open_buttons(at, TAB))
        assert not any((b.key or "").startswith(f"{TAB}_mark_read_") for b in at.button)


def test_the_campaign_tab_does_not_repeat_the_campaign_name_on_every_row():
    with _campaign_tab() as (at, _):
        captions = " ".join(c.value for c in at.caption)
        assert f"{FIXTURE_CAMPAIGN} · " not in captions.replace("\\", "")


def test_the_campaign_tab_shows_the_conversation_and_opens_another():
    with _campaign_tab() as (at, _):
        assert "Hi! Interested — what are your rates?" in _thread_texts(at)
        at.button(key=f"{TAB}_open_{A5}").click()
        at.run(timeout=25)
        assert "Not for me, thanks." in _thread_texts(at)
        assert "I am a different person entirely" not in _thread_texts(at)


def test_the_campaign_tab_search_narrows_its_list():
    with _campaign_tab() as (at, _):
        at.text_input(key=f"responses_search_{FIXTURE_CAMPAIGN}").set_value("royal-none")
        at.run(timeout=25)
        assert any("No responses match this search." in i.value for i in at.info)
        at.text_input(key=f"responses_search_{FIXTURE_CAMPAIGN}").set_value("zed")
        at.run(timeout=25)
        assert _row_keys(at, TAB) == [A5]


def test_a_reply_from_the_campaign_tab_sends_the_same_payload_and_waits_a_second_before_dispatch():
    with _campaign_tab() as (at, captured), patch.object(inbox_view.time, "sleep") as slept:
        at.text_area(key=f"{TAB}_body_{A1}").set_value("Thanks Kari!")
        at.run(timeout=25)
        at.button(key=f"{TAB}_send_{A1}").click()
        at.run(timeout=25)
        assert list(at.exception) == [] and list(at.error) == []
        payload = _committed(captured)
        assert payload["to"] == "kari@abc.com" and payload["body"] == "Thanks Kari!"
        assert captured["dispatches"][0] == ("send_reply.yml", {"campaign": FIXTURE_CAMPAIGN, "payload_path": captured["commits"][0]["path"]})
        assert any(call.args == (1.0,) for call in slept.call_args_list)          # as the tab always did
        assert at.text_area(key=f"{TAB}_body_{A1}").value == ""


def test_the_all_campaigns_page_does_not_pause_before_dispatch():
    with hub() as (at, captured), patch.object(inbox_view.time, "sleep") as slept:
        at.text_area(key=f"hub_body_{A1}").set_value("Quick reply")
        at.run(timeout=25)
        at.button(key=f"hub_send_{A1}").click()
        at.run(timeout=25)
        assert "dispatches" in captured and not any(call.args == (1.0,) for call in slept.call_args_list)


def test_a_blank_reply_from_the_campaign_tab_is_refused():
    with _campaign_tab() as (at, captured):
        at.button(key=f"{TAB}_send_{A1}").click()
        at.run(timeout=25)
        assert at.error and "commits" not in captured


def test_the_campaign_tab_can_switch_back_to_the_classic_layout():
    with _campaign_tab() as (at, _):
        at.radio(key=f"responses_view_{FIXTURE_CAMPAIGN}").set_value("Classic")
        at.run(timeout=25)
        assert list(at.exception) == []
        assert any("View full conversation" in e.label for e in at.expander)
        assert _open_buttons(at, TAB) == []


def test_a_campaign_with_no_replies_still_just_says_so():
    ws = _two_campaign_ws()
    ws[f"{FIXTURE_CAMPAIGN} Response Sheet"] = FakeWorksheet([])
    with _campaign_tab(ws) as (at, _):
        assert list(at.exception) == []
        assert any("No responses yet." in i.value for i in at.info)
        assert not any(r.key == f"responses_view_{FIXTURE_CAMPAIGN}" for r in at.radio)    # nothing to lay out
