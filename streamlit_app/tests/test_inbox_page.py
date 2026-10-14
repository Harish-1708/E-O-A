"""Page-level tests for the Unibox-style Inbox: on the all-campaigns Responses page and inside each campaign's own
Responses tab. The Inbox is the only layout. The page is drawn as HTML, so these tests read it back through a small HTML parser."""
import base64
import contextlib
import json
import os
from datetime import datetime
from html.parser import HTMLParser
from unittest.mock import patch

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import inbox_html
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
TAB = f"tab_{FIXTURE_CAMPAIGN}"
SENT_TAB = f"{FIXTURE_CAMPAIGN} Sent Mail"
NOW = datetime(2026, 10, 12, 15, 0, 0)


@pytest.fixture(autouse=True)
def _every_test_uses_the_fixture_repo(fixture_repo):
    """Stable, test-owned campaigns and templates (see conftest), never the live ones."""
    yield


@pytest.fixture(autouse=True)
def _stub_the_live_template_reads_other_tabs_make():
    """A campaign page runs EVERY tab's body on each rerun, and the Sequences tab reads templates live from GitHub.
    Stubbed so opening a campaign never calls GitHub."""
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


@pytest.fixture(autouse=True)
def _the_waiting_nudge_counts_from_a_fixed_now(monkeypatch):
    real = inbox_html.waiting_nudge
    monkeypatch.setattr(inbox_html, "waiting_nudge", lambda unread, received, now=None: real(unread, received, NOW))


# ---------------------------------------------------------------- reading the page back

class _Node:
    def __init__(self, tag, attrs, parent=None):
        self.tag, self.attrs, self.parent, self.children, self.parts = tag, dict(attrs), parent, [], []

    @property
    def classes(self):
        return set((self.attrs.get("class") or "").split())

    def text(self):
        return "".join(p if isinstance(p, str) else p.text() for p in self.parts)


class _Tree(HTMLParser):
    VOID = {"br", "img", "hr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = self.cur = _Node("root", [])

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, attrs, self.cur)
        self.cur.children.append(node)
        self.cur.parts.append(node)
        if tag not in self.VOID:
            self.cur = node

    def handle_endtag(self, tag):
        node = self.cur
        while node is not self.root and node.tag != tag:
            node = node.parent
        if node is not self.root:
            self.cur = node.parent

    def handle_data(self, data):
        self.cur.parts.append(data)


def _parse(markup):
    tree = _Tree()
    tree.feed(markup)
    tree.close()
    return tree.root


def _find(node, cls):
    for child in node.children:
        if cls in child.classes:
            yield child
        yield from _find(child, cls)


def _nodes(at, cls):
    found = []
    for element in at.markdown:
        if cls in element.value:
            found.extend(_find(_parse(element.value), cls))
    return found


def _first(node, cls, default=""):
    match = next(_find(node, cls), None)
    return match.text().strip() if match is not None else default


def _rows(at):
    return [{"name": _first(n, "ux-name"), "subject": _first(n, "ux-subject"), "preview": _first(n, "ux-preview"),
             "date": _first(n, "ux-date"), "meta": _first(n, "ux-meta"), "nudge": _first(n, "ux-nudge"),
             "selected": "ux-selected" in n.classes, "unread": "ux-unread" in n.classes} for n in _nodes(at, "ux-row")]


def _cards(at):
    cards = []
    for n in _nodes(at, "ux-card"):
        lines = []
        body = [x for x in _find(n, "ux-body")]
        if body:
            for x in body[0].children:
                lines.append((("quoted" if "ux-qline" in x.classes else "plain"), x.text().replace("\xa0", "")))
        meta = [x.text().strip() for x in _find(n, "ux-meta-line")]
        clamped = any("ux-clamped" in x.classes for x in body)
        cards.append({"clamped": clamped, "title": _first(n, "ux-title"), "from": meta[0].removeprefix("From:").strip() if meta else "",
                      "to": meta[1].removeprefix("To:").strip() if len(meta) > 1 else "", "date": _first(n, "ux-card-date"),
                      "lines": lines, "empty": _first(n, "ux-empty"), "body": "\n".join(t for _, t in lines).strip("\n")})
    return cards


def _bodies(at):
    return [c["body"] for c in _cards(at)]


def _header(at):
    nodes = _nodes(at, "ux-thread-head")
    if not nodes:
        return None
    return {"name": _first(nodes[0], "ux-head-name"), "email": _first(nodes[0], "ux-head-email"),
            "chips": [c.text().strip() for c in _find(nodes[0], "ux-chip")]}


def _rail(at):
    return [{"label": _first(n, "ux-rail-label"), "count": _first(n, "ux-count"), "selected": "ux-selected" in n.classes}
            for n in _nodes(at, "ux-rail-item")]


def _rail_counts(at):
    return {r["label"]: r["count"] for r in _rail(at)}


def _open_buttons(at, namespace="hub"):
    return [b for b in at.button if (b.key or "").startswith(f"{namespace}_open_")]


def _row_keys(at, namespace="hub"):
    return [b.key[len(f"{namespace}_open_"):] for b in _open_buttons(at, namespace)]


def _has(widgets, key):
    return any(w.key == key for w in widgets)


def _committed(captured):
    return json.loads(captured["commits"][0]["content"].decode("utf-8"))


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
                  "2026-10-12 14:00:00", FullBody="Hi! Interested — what are your rates?", Intent="Interested", IntentConfidence="High"),
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


def _sent(mid, at, body, lead="1", subject="Re: Partnership", source="mailbox"):
    return {"MessageID": mid, "LeadID": lead, "SentAt": at, "SenderAccount": "sales1", "To": "kari@abc.com", "Cc": "",
            "Subject": subject, "Body": body, "InReplyTo": "", "Source": source}


def _with_sent_mail(*rows, ws=None):
    ws = ws or _two_campaign_ws()
    ws[SENT_TAB] = FakeWorksheet(list(rows))
    return ws


def _fake_dispatch(captured, dispatch_error=None):
    def fake_dispatch(self, workflow_file, inputs, ref="main"):
        if dispatch_error:
            raise dispatch_error
        captured.setdefault("dispatches", []).append((workflow_file, inputs))
        return {"id": 1, "html_url": "https://github.com/x"}
    return fake_dispatch


@contextlib.contextmanager
def hub(ws=None, dispatch_error=None):
    """The all-campaigns Responses page, running against fake Sheets and a fake GitHub."""
    ws = ws or _two_campaign_ws()
    captured, fake_create_file = _mock_github_writes()
    with patch("gspread.authorize", return_value=type("C", (), {"open_by_key": lambda self, k: FakeSpreadsheet(ws)})()), \
         patch("google.oauth2.service_account.Credentials.from_service_account_info", return_value=object()), \
         patch("preview_logic.list_campaigns", return_value=[FIXTURE_CAMPAIGN, OTHER]), \
         patch("preview_logic.get_campaign_cfg", lambda name: {
             "_campaign_name": name, "master_tab": f"{name} Master Sheet", "responses_tab": f"{name} Response Sheet",
             "send_log_tab": f"{name} Custom Log Sheet", "error_log_tab": f"{name} Error Log"}), \
         patch("github_client.GitHubClient.create_file", fake_create_file), \
         patch("github_client.GitHubClient.dispatch_workflow", _fake_dispatch(captured, dispatch_error)):
        at = AppTest.from_file(HUB_PAGE)
        at.secrets.update(_dashboard_secrets())
        for key, value in _authed_session().items():
            at.session_state[key] = value
        at.run(timeout=25)
        yield at, captured


@contextlib.contextmanager
def _campaign_tab(ws=None, dispatch_error=None):
    ws = ws or _two_campaign_ws()
    captured, fake_create_file = _mock_github_writes()
    with patch("gspread.authorize", return_value=type("C", (), {"open_by_key": lambda self, k: FakeSpreadsheet(ws)})()), \
         patch("google.oauth2.service_account.Credentials.from_service_account_info", return_value=object()), \
         patch("github_client.GitHubClient.create_file", fake_create_file), \
         patch("github_client.GitHubClient.dispatch_workflow", _fake_dispatch(captured, dispatch_error)):
        at = AppTest.from_file(CAMPAIGNS_PAGE)
        at.secrets.update(_dashboard_secrets())
        for key, value in _authed_session().items():
            at.session_state[key] = value
        at.session_state["selected_campaign"] = FIXTURE_CAMPAIGN
        at.run(timeout=25)
        yield at, captured


def _click(at, key):
    at.button(key=key).click()
    at.run(timeout=25)


def _open_conversation(at, key, namespace="hub"):
    _click(at, f"{namespace}_open_{key}")


def _compose(at, gk, namespace="hub"):
    """Opens the message box (it is closed until Reply is pressed)."""
    _click(at, f"ux_reply_{namespace}_{gk}")


def _chronological(at, namespace="hub"):
    at.checkbox(key=f"{namespace}_newest_first").uncheck()
    at.run(timeout=25)


def _order_of(texts, *needles):
    positions = [next(i for i, t in enumerate(texts) if n in t) for n in needles]
    return positions == sorted(positions)


# ================================================================ the all-campaigns page: layout

def test_the_inbox_is_the_default_layout_with_a_rail_a_list_and_an_open_conversation():
    with hub() as (at, _):
        assert list(at.exception) == [], list(at.exception)
        assert list(at.error) == []
        assert not _has(at.radio, "responses_view")                                 # there is no layout switch any more
        assert _row_keys(at) == [A1, B5, A5]                                    # newest first, one row per conversation
        assert at.session_state["hub_selected"] == A1                           # the newest is open
        assert _header(at)["name"] == "Kari B" and _header(at)["email"] == f"kari@abc.com · {FIXTURE_CAMPAIGN}"
        assert [r["selected"] for r in _rows(at)] == [True, False, False]
        assert not any("View full conversation" in e.label for e in at.expander)        # no card-per-reply expanders


def test_the_list_rows_show_the_sender_subject_preview_and_date():
    with hub() as (at, _):
        first = _rows(at)[0]
        assert first["name"] == "Kari B" and first["subject"] == "Re: Partnership"
        assert first["preview"] == "Also do you cover shipping?" and first["date"] == "Oct 12, 2026"
        assert _rows(at)[2]["name"] == "Zed Z" and _rows(at)[2]["date"] == "Oct 11, 2026"


def test_the_list_shows_the_date_an_email_was_really_sent_not_the_day_it_was_logged():
    ws = _two_campaign_ws()
    for record in ws[f"{FIXTURE_CAMPAIGN} Response Sheet"]._records:
        if record["ResponseID"] == "rA3":
            record["ReceivedAt"], record["MessageDate"] = "2026-10-13 01:00:00", "2026-10-11 20:00:00"      # logged the next day
    with hub(ws) as (at, _):
        rows = _rows(at)                                                          # the list is ordered by when each was LOGGED, so Zed is first
        assert [r["name"] for r in rows] == ["Zed Z", "Kari B", "Stranger"]
        assert [r["date"] for r in rows] == ["Oct 11, 2026", "Oct 12, 2026", "Oct 12, 2026"]       # but each shows the day it was really sent


def test_the_stylesheet_is_put_on_the_page_exactly_once():
    with hub() as (at, _):
        styles = [m.value for m in at.markdown if m.value.lstrip().startswith("<style>")]
        assert len(styles) == 1 and styles[0] == f"<style>{inbox_html.inbox_css(inbox_html.SIZES[inbox_html.DEFAULT_SIZE])}</style>"


def test_the_panes_are_given_the_container_names_the_stylesheet_looks_for():
    """The stylesheet finds each pane by the name of its container; if a name drifted, the layout would silently fall apart."""
    with hub() as (at, _):
        names = {m.value.split('class="')[1].split('"')[0].split()[0] for m in at.markdown if "ux-" in m.value and "<style>" not in m.value}
        assert {"ux-rail-title", "ux-rail-item", "ux-list-head", "ux-row", "ux-thread-head", "ux-card"} <= names
        keys = {b.key for b in at.button if b.key}
        assert "ux_railbtn_hub_all" in keys and f"ux_reply_hub_{A1}" in keys and f"hub_open_{A1}" in keys


def test_clicking_a_conversation_opens_it():
    with hub() as (at, _):
        _open_conversation(at, A5)
        assert at.session_state["hub_selected"] == A5
        assert _header(at)["name"] == "Zed Z" and "Not for me, thanks." in _bodies(at)
        assert [r["selected"] for r in _rows(at)] == [False, False, True]


def test_the_conversation_is_a_stack_of_email_cards_newest_first_with_ours_from_you():
    with hub() as (at, _):
        cards = _cards(at)
        assert len(cards) == 3                                                  # her two replies and our template intro
        assert cards[0]["body"] == "Also, do you cover shipping?" and cards[1]["body"] == "Hi! Interested — what are your rates?"
        assert cards[0]["from"] == "Kari B <kari@abc.com>" and cards[0]["to"] == "You"
        assert cards[2]["from"] == "You" and cards[2]["to"] == "Kari B <kari@abc.com>"            # the intro we sent
        assert all(c["title"] and c["date"].endswith("UTC") for c in cards)
        assert cards[0]["title"] == "Re: Partnership" and cards[0]["date"] == "Oct 12, 2026, 2:30 PM UTC"


def test_turning_off_newest_first_puts_the_thread_in_chronological_order():
    with hub() as (at, _):
        _chronological(at)
        cards = _cards(at)
        assert cards[0]["from"] == "You"                                        # our intro leads
        assert [c["body"] for c in cards[1:]] == ["Hi! Interested — what are your rates?", "Also, do you cover shipping?"]


def test_the_header_shows_what_the_system_did_and_how_sure_the_ai_was():
    with hub() as (at, _):
        assert _header(at)["chips"] == ["Sequence stopped", "👍 Interested · High confidence"]


def test_a_logged_only_reply_says_plainly_that_the_sequence_was_not_stopped():
    ws = _two_campaign_ws()
    for record in ws[f"{FIXTURE_CAMPAIGN} Response Sheet"]._records:
        if record["ResponseID"] == "rA2":
            record["ActionTaken"] = "Logged Only (Unverified Match)"
    with hub(ws) as (at, _):
        assert "Logged Only (Unverified Match) — sequence NOT stopped" in _header(at)["chips"]
        assert "Stopped - Replied" in _header(at)["chips"]                       # and the lead IS stopped for another reason


# ================================================================ the status rail

def test_the_rail_counts_conversations_by_status_and_unread():
    with hub() as (at, _):
        assert _rail_counts(at) == {"All": "3", "Unread": "3", "Lead": "0", "Interested": "1", "Not interested": "1",
                                    "Unclear": "1", "Auto-reply": "0", "Bounced": "0"}
        assert [r["label"] for r in _rail(at) if r["selected"]] == ["All"]


@pytest.mark.parametrize("button, expected", [
    ("interested", [A1]), ("not_interested", [A5]), ("unclear", [B5]), ("lead", []), ("auto", []), ("bounced", []),
])
def test_choosing_a_status_in_the_rail_shows_only_those_conversations(button, expected):
    with hub() as (at, _):
        _click(at, f"ux_railbtn_hub_{button}")
        assert _row_keys(at) == expected
        assert [r["label"] for r in _rail(at) if r["selected"]] != ["All"]


def test_an_empty_status_says_so_and_the_other_pane_does_too():
    with hub() as (at, _):
        _click(at, "ux_railbtn_hub_bounced")
        assert list(at.exception) == [] and _row_keys(at) == [] and _header(at) is None
        assert sum("No conversations in this view." in m.value for m in at.markdown) == 2
        assert at.session_state["hub_selected"] is None


def test_choosing_all_again_brings_everything_back():
    with hub() as (at, _):
        _click(at, "ux_railbtn_hub_interested")
        _click(at, "ux_railbtn_hub_all")
        assert _row_keys(at) == [A1, B5, A5]


def test_if_the_open_conversation_is_filtered_out_the_first_one_left_opens():
    with hub() as (at, _):
        _open_conversation(at, A5)
        _click(at, "ux_railbtn_hub_interested")
        assert at.session_state["hub_selected"] == A1 and _header(at)["name"] == "Kari B"


def test_a_later_follow_up_that_was_never_classified_does_not_turn_an_interested_conversation_into_unclear():
    with hub() as (at, _):
        _click(at, "ux_railbtn_hub_unclear")
        assert A1 not in _row_keys(at)                                          # Kari asked about shipping later, unclassified; still Interested


def test_a_later_not_interested_overrides_an_earlier_interested():
    ws = _two_campaign_ws(extra_a_responses=[
        _resp("rA9", "1", '"Kari B" <kari@abc.com>', "Re: Partnership", "Actually no", "2026-10-13 09:00:00", FullBody="Actually, no thanks.",
              Intent="Not Interested", IntentConfidence="High")])
    with hub(ws) as (at, _):
        _click(at, "ux_railbtn_hub_not_interested")
        assert set(_row_keys(at)) == {A1, A5}
        _click(at, "ux_railbtn_hub_interested")
        assert _row_keys(at) == []


def test_a_conversation_is_classified_by_its_latest_message():
    ws = _two_campaign_ws(extra_a_responses=[
        _resp("rBounce", "5", '"Mail Daemon" <mailer-daemon@x.com>', "Undelivered", "550 no such user", "2026-10-12 16:00:00",
              Classification="Bounce (Hard)")])
    with hub(ws) as (at, _):
        _click(at, "ux_railbtn_hub_bounced")
        assert _row_keys(at) == [A5]
        _click(at, "ux_railbtn_hub_not_interested")
        assert _row_keys(at) == []                                              # Zed's latest message is the bounce, not his refusal


# ---- statuses worked out from the words, when the AI gave no definite reading

def _zed_says(body, intent="", confidence="", classification="Genuine Reply"):
    """Campaign A's lead 5 (Zed), with his one reply replaced."""
    ws = _two_campaign_ws()
    for record in ws[f"{FIXTURE_CAMPAIGN} Response Sheet"]._records:
        if record["ResponseID"] == "rA3":
            record.update({"FullBody": body, "Snippet": body, "Intent": intent, "IntentConfidence": confidence, "Classification": classification})
    return ws


def _status_of_zed(ws):
    """Which rail category Zed's conversation lands in, and the header's status pill."""
    with hub(ws) as (at, _):
        landed = [key for key in ("lead", "interested", "not_interested", "unclear", "auto", "bounced")
                  if (_click(at, f"ux_railbtn_hub_{key}"), A5 in _row_keys(at))[1]]
        _click(at, "ux_railbtn_hub_all")
        _open_conversation(at, A5)
        return landed, _header(at)["chips"][-1]


@pytest.mark.parametrize("words, expected", [
    ("Not interested", "not_interested"), ("No thank you", "not_interested"), ("No thanks!", "not_interested"),
    ("Thanks for reaching out but I'm not taking on brand work right now", "not_interested"),
    ("Yes! I'd love to do this", "interested"), ("Sounds great, send me the details", "interested"),
    ("What are your rates?", "lead"), ("I can't accept that offer. It's extremely low.", "lead"),
    ("Thanks!", "unclear"),
])
def test_a_reply_with_no_ai_reading_is_labelled_from_what_it_says(words, expected):
    landed, pill = _status_of_zed(_zed_says(words))
    assert landed == [expected], (words, landed)
    if expected != "unclear":
        assert pill.endswith("· by keywords"), pill                               # and it says the label came from keywords, not the AI


def test_when_the_full_text_is_blank_the_short_preview_is_read_instead():
    ws = _zed_says("")
    for record in ws[f"{FIXTURE_CAMPAIGN} Response Sheet"]._records:
        if record["ResponseID"] == "rA3":
            record["Snippet"] = "No thanks, not for me"
    assert _status_of_zed(ws)[0] == ["not_interested"]


def test_the_ai_reading_is_used_when_it_is_definite_even_if_the_words_say_otherwise():
    landed, pill = _status_of_zed(_zed_says("No thank you, not interested", intent="Interested", confidence="High"))
    assert landed == ["interested"] and pill == "👍 Interested · High confidence"


def test_an_ai_reading_of_unclear_gives_way_to_the_words():
    landed, pill = _status_of_zed(_zed_says("No thank you", intent="Unclear", confidence="Low"))
    assert landed == ["not_interested"] and pill == "👎 Not interested · by keywords"


def test_a_question_we_asked_in_quoted_history_is_not_read_as_their_answer():
    landed, _ = _status_of_zed(_zed_says("Thanks!\n\n> Are you interested in a paid collab? Not interested is fine too."))
    assert landed == ["unclear"]


def test_an_auto_replys_words_are_never_taken_as_the_persons_answer():
    landed, _ = _status_of_zed(_zed_says("I am not interested in any emails while away", classification="Auto-Reply"))
    assert landed == ["auto"]


def test_an_older_auto_replys_words_are_not_taken_as_the_answer_to_a_later_reply():
    """An out-of-office that happens to say 'interested' or 'not interested' must not label the conversation once a real reply follows."""
    ws = _zed_says("I am interested in hearing from you when I am back in the office", classification="Auto-Reply")
    real = _resp("rR", "5", '"Zed Z" <zed@abc.com>', "Re: Hello", "x", "2026-10-12 10:00:00", FullBody="Thanks!")
    assert _status_of_zed(_with_extra(ws, real))[0] == ["unclear"]


def test_a_later_question_does_not_override_an_earlier_yes():
    later = _resp("rQ", "5", '"Zed Z" <zed@abc.com>', "Re: Hello", "x", "2026-10-12 10:00:00", FullBody="Do you cover shipping?")
    assert _status_of_zed(_with_extra(_zed_says("Sounds great, send details"), later))[0] == ["interested"]


def test_a_later_refusal_overrides_an_earlier_yes():
    later = _resp("rN", "5", '"Zed Z" <zed@abc.com>', "Re: Hello", "x", "2026-10-12 10:00:00", FullBody="Actually, no thank you")
    assert _status_of_zed(_with_extra(_zed_says("Sounds great, send details"), later))[0] == ["not_interested"]


def _with_extra(ws, response):
    ws[f"{FIXTURE_CAMPAIGN} Response Sheet"]._records.append(response)
    return ws


def test_the_rail_counts_include_the_keyword_readings():
    with hub(_zed_says("Not interested")) as (at, _):
        assert _rail_counts(at)["Not interested"] == "1" and _rail_counts(at)["Unclear"] == "1"


# ---- text size and fitting the screen

def _font_sizes(at):
    css = next(m.value for m in at.markdown if m.value.lstrip().startswith("<style>"))
    return [float(x) for x in __import__("re").findall(r"font-size: ([\d.]+)px", css)]


def test_the_text_is_larger_by_default_and_the_size_can_be_changed_from_the_menu():
    with hub() as (at, _):
        assert at.radio(key="hub_size").value == "Large"
        large = _font_sizes(at)
        at.radio(key="hub_size").set_value("Normal")
        at.run(timeout=25)
        normal = _font_sizes(at)
        at.radio(key="hub_size").set_value("Extra large")
        at.run(timeout=25)
        extra = _font_sizes(at)
        assert normal == _font_sizes_of(inbox_html.INBOX_CSS)
        assert all(n < l < x for n, l, x in zip(normal, large, extra))


def _font_sizes_of(css):
    return [float(x) for x in __import__("re").findall(r"font-size: ([\d.]+)px", css)]


def test_the_size_choice_is_kept_when_another_conversation_is_opened():
    with hub() as (at, _):
        at.radio(key="hub_size").set_value("Extra large")
        at.run(timeout=25)
        _open_conversation(at, A5)
        assert at.radio(key="hub_size").value == "Extra large"
        assert _font_sizes(at) == _font_sizes_of(inbox_html.inbox_css(1.3))


def test_the_campaign_tab_has_its_own_size_choice():
    with _campaign_tab() as (at, _):
        assert at.radio(key=f"{TAB}_size").value == "Large"


def test_the_rail_counts_follow_the_pages_own_search():
    with hub() as (at, _):
        at.text_input(key="responses_search_query").set_value("stranger")
        at.run(timeout=25)
        assert _rail_counts(at)["All"] == "1" and _rail_counts(at)["Unclear"] == "1" and _rail_counts(at)["Interested"] == "0"


# ================================================================ unread, mark as read, the page's own filters

def test_unread_conversations_show_a_dot_and_how_many_are_new():
    with hub() as (at, _):
        rows = _rows(at)
        assert all(r["unread"] for r in rows)                                    # nothing has been read yet
        assert "2 messages · 2 new" in rows[0]["meta"]
        assert "ux-dot" in next(m.value for m in at.markdown if "ux-row" in m.value)


def test_a_conversation_still_unread_after_a_day_is_marked_as_waiting_for_a_reply():
    with hub() as (at, _):
        nudges = [r["nudge"] for r in _rows(at)]
        assert nudges == ["", "", "Received 1 day ago. Reply?"]                  # Zed wrote on the 11th; "now" is 15:00 on the 12th


def test_a_read_conversation_is_no_longer_marked_as_waiting():
    ws = _two_campaign_ws()
    with hub(ws) as (at, _):
        at.session_state["read_response_keys"] = {f"{FIXTURE_CAMPAIGN}:rA3"}
        at.run(timeout=25)
        assert [r["nudge"] for r in _rows(at)] == ["", "", ""] and [r["unread"] for r in _rows(at)] == [True, True, False]


def test_merely_opening_or_loading_never_marks_anything_read():
    with hub() as (at, _):
        _open_conversation(at, A5)
        assert at.session_state["read_response_keys"] == set() and at.session_state["pending_sync_keys"] == set()


def test_mark_as_read_marks_the_whole_conversation_and_queues_the_sync():
    with hub() as (at, _):
        _click(at, f"hub_mark_read_{A1}")
        expected = {f"{FIXTURE_CAMPAIGN}:rA1", f"{FIXTURE_CAMPAIGN}:rA2"}
        assert at.session_state["read_response_keys"] == expected == at.session_state["pending_sync_keys"]
        assert [r["unread"] for r in _rows(at)] == [False, True, True]
        assert not _has(at.button, f"hub_mark_read_{A1}")                        # nothing left to mark


def test_the_unread_item_in_the_rail_hides_conversations_that_have_been_read():
    with hub() as (at, _):
        _click(at, f"hub_mark_read_{A1}")
        _click(at, "ux_railbtn_hub_unread")
        assert A1 not in _row_keys(at) and set(_row_keys(at)) == {B5, A5} and _rail_counts(at)["Unread"] == "2"


def test_the_pages_own_unread_switch_still_works_alongside_the_rail():
    with hub() as (at, _):
        _click(at, f"hub_mark_read_{A1}")
        at.radio(key="responses_inbox_chip").set_value("Unread only")
        at.run(timeout=25)
        assert set(_row_keys(at)) == {B5, A5}


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
        _open_conversation(at, A5)
        at.text_input(key="responses_search_query").set_value("stranger")
        at.run(timeout=25)
        assert list(at.exception) == [] and at.session_state["hub_selected"] == B5


def test_nothing_matching_says_so_and_does_not_crash():
    with hub() as (at, _):
        at.text_input(key="responses_search_query").set_value("zzzz-no-such-text")
        at.run(timeout=25)
        assert list(at.exception) == []
        assert any("No responses match these filters." in i.value for i in at.info)
        assert _open_buttons(at) == []


# ---- the cross-campaign bug

def test_each_conversation_shows_only_its_own_campaigns_messages_even_when_lead_ids_match():
    """REGRESSION: Campaign A's lead 5 and Campaign B's lead 5 are two different people."""
    with hub() as (at, _):
        _open_conversation(at, A5)
        assert "Not for me, thanks." in _bodies(at) and "I am a different person entirely" not in _bodies(at)
        _open_conversation(at, B5)
        assert "I am a different person entirely" in _bodies(at) and "Not for me, thanks." not in _bodies(at)


# ================================================================ the Reply box and sending

def test_the_message_box_is_closed_until_reply_is_pressed_and_pressing_it_again_closes_it():
    with hub() as (at, _):
        assert not _has(at.text_area, f"hub_body_{A1}") and not _has(at.button, f"hub_send_{A1}")
        _compose(at, A1)
        assert _has(at.text_area, f"hub_body_{A1}") and _has(at.button, f"hub_send_{A1}")
        assert any("Sending as: sales1" in c.value for c in at.caption)
        _compose(at, A1)
        assert not _has(at.text_area, f"hub_body_{A1}")


def test_the_message_box_is_prefilled_with_the_leads_address_and_a_re_subject():
    with hub() as (at, _):
        _compose(at, A1)
        assert at.text_input(key=f"hub_to_{A1}").value == "kari@abc.com"
        assert at.text_input(key=f"hub_subject_{A1}").value == "Re: Partnership"


def test_the_campaign_tabs_message_box_is_prefilled_too():
    with _campaign_tab() as (at, _):
        _compose(at, A1, TAB)
        assert at.text_input(key=f"{TAB}_to_{A1}").value == "kari@abc.com"
        assert at.text_input(key=f"{TAB}_subject_{A1}").value == "Re: Partnership"


def test_each_conversation_has_its_own_message_box_and_keeps_what_was_typed_in_it():
    with hub() as (at, _):
        _compose(at, A1)
        at.text_area(key=f"hub_body_{A1}").set_value("half-written reply")
        at.run(timeout=25)
        _open_conversation(at, A5)
        assert not _has(at.text_area, f"hub_body_{A1}")                          # Zed's box is separate (and closed)
        _open_conversation(at, A1)
        assert at.text_area(key=f"hub_body_{A1}").value == "half-written reply"


def test_sending_a_reply_commits_the_payload_starts_the_workflow_clears_the_box_and_marks_read():
    with hub() as (at, captured):
        _compose(at, A1)
        at.text_area(key=f"hub_body_{A1}").set_value("Thanks Kari — our rate is $150.")
        at.run(timeout=25)
        _click(at, f"hub_send_{A1}")
        assert list(at.exception) == [], list(at.exception)
        assert list(at.error) == []
        assert any("Reply queued" in s.value for s in at.success)
        payload = _committed(captured)
        assert captured["commits"][0]["path"].startswith(f"replies/{FIXTURE_CAMPAIGN}/")
        assert payload["to"] == "kari@abc.com" and payload["sender_account"] == "sales1"
        assert payload["subject"] == "Re: Partnership" and payload["body"] == "Thanks Kari — our rate is $150."
        assert payload["in_reply_to"] == "<rA2@mail.gmail.com>"                  # threaded off her LATEST message
        assert payload["references"] == "<our1@mail.gmail.com> <rA2@mail.gmail.com>"
        assert captured["dispatches"] == [("send_reply.yml", {"campaign": FIXTURE_CAMPAIGN, "payload_path": captured["commits"][0]["path"]})]
        assert at.text_area(key=f"hub_body_{A1}").value == ""                    # cleared: a second click cannot send it twice
        assert at.session_state["pending_sync_keys"] == {f"{FIXTURE_CAMPAIGN}:rA1", f"{FIXTURE_CAMPAIGN}:rA2"}


def test_a_reply_that_has_been_sent_does_not_come_back_when_you_return_to_that_conversation():
    """The saved draft must be cleared on sending too, or the sent text would reappear in the box."""
    with hub() as (at, captured):
        _compose(at, A1)
        at.text_area(key=f"hub_body_{A1}").set_value("Sent already")
        at.run(timeout=25)
        _click(at, f"hub_send_{A1}")
        assert "commits" in captured
        _open_conversation(at, A5)
        _open_conversation(at, A1)
        assert at.text_area(key=f"hub_body_{A1}").value == ""


def test_the_message_after_sending_says_it_will_appear_in_the_conversation():
    with hub() as (at, _):
        _compose(at, A1)
        at.text_area(key=f"hub_body_{A1}").set_value("Thanks!")
        at.run(timeout=25)
        _click(at, f"hub_send_{A1}")
        assert any("will then appear in this conversation" in s.value for s in at.success)


def test_a_reply_goes_out_through_the_right_campaign_when_lead_ids_collide():
    with hub() as (at, captured):
        _open_conversation(at, B5)
        _compose(at, B5)
        at.text_area(key=f"hub_body_{B5}").set_value("Hello Stranger")
        at.run(timeout=25)
        _click(at, f"hub_send_{B5}")
        payload = _committed(captured)
        assert payload["to"] == "stranger@b.com" and payload["sender_account"] == "sales2"
        assert captured["dispatches"][0][1]["campaign"] == OTHER


def test_a_blank_message_is_refused_and_nothing_is_committed():
    with hub() as (at, captured):
        _compose(at, A1)
        _click(at, f"hub_send_{A1}")
        assert list(at.exception) == [] and at.error and "commits" not in captured and "dispatches" not in captured


def test_the_to_and_subject_fields_can_be_edited_before_sending():
    with hub() as (at, captured):
        _compose(at, A1)
        at.text_input(key=f"hub_to_{A1}").set_value("someone.else@abc.com")
        at.text_input(key=f"hub_subject_{A1}").set_value("A different subject")
        at.text_area(key=f"hub_body_{A1}").set_value("Redirected.")
        at.run(timeout=25)
        _click(at, f"hub_send_{A1}")
        payload = _committed(captured)
        assert payload["to"] == "someone.else@abc.com" and payload["subject"] == "A different subject"


def test_cc_and_bcc_are_sent():
    with hub() as (at, captured):
        _compose(at, A1)
        at.text_input(key=f"hub_cc_{A1}").set_value("boss@abc.com, other@abc.com")
        at.text_input(key=f"hub_bcc_{A1}").set_value("archive@abc.com")
        at.text_area(key=f"hub_body_{A1}").set_value("Copying you in.")
        at.run(timeout=25)
        _click(at, f"hub_send_{A1}")
        payload = _committed(captured)
        assert payload["cc"] == ["boss@abc.com", "other@abc.com"] and payload["bcc"] == ["archive@abc.com"]


def test_an_attachment_round_trips_and_the_uploader_is_emptied_afterwards():
    with hub() as (at, captured):
        _compose(at, A1)
        at.file_uploader(key=f"hub_attachments_{A1}_0").upload("photo.png", b"fake-png-bytes", "image/png")
        at.text_area(key=f"hub_body_{A1}").set_value("Here is the photo.")
        at.run(timeout=25)
        _click(at, f"hub_send_{A1}")
        assert list(at.exception) == []
        assert _committed(captured)["attachments"] == [{"filename": "photo.png", "content_base64": base64.b64encode(b"fake-png-bytes").decode("ascii")}]
        assert _has(at.file_uploader, f"hub_attachments_{A1}_1") and not _has(at.file_uploader, f"hub_attachments_{A1}_0")


def test_no_attachment_means_no_attachments_key_in_the_payload():
    with hub() as (at, captured):
        _compose(at, A1)
        at.text_area(key=f"hub_body_{A1}").set_value("No file this time.")
        at.run(timeout=25)
        _click(at, f"hub_send_{A1}")
        assert "attachments" not in _committed(captured)


def test_a_failed_send_shows_the_reason_keeps_the_text_and_marks_nothing_read():
    with hub(dispatch_error=GitHubActionsError("boom")) as (at, captured):
        _compose(at, A1)
        at.text_area(key=f"hub_body_{A1}").set_value("Important reply")
        at.run(timeout=25)
        _click(at, f"hub_send_{A1}")
        assert list(at.exception) == [] and any("Failed to send: boom" in e.value for e in at.error)
        assert at.text_area(key=f"hub_body_{A1}").value == "Important reply" and at.session_state["pending_sync_keys"] == set()


def test_the_all_campaigns_page_does_not_pause_before_dispatch():
    with hub() as (at, captured), patch.object(inbox_view.time, "sleep") as slept:
        _compose(at, A1)
        at.text_area(key=f"hub_body_{A1}").set_value("Quick reply")
        at.run(timeout=25)
        _click(at, f"hub_send_{A1}")
        assert "dispatches" in captured and not any(call.args == (1.0,) for call in slept.call_args_list)


# ================================================================ search keeps the whole thread, odd data

def test_a_search_narrows_the_list_but_the_open_conversation_still_shows_every_message():
    with hub() as (at, _):
        at.text_input(key="responses_search_query").set_value("rates")           # matches only Kari's FIRST message
        at.run(timeout=25)
        assert _row_keys(at) == [A1]
        assert "Hi! Interested — what are your rates?" in _bodies(at) and "Also, do you cover shipping?" in _bodies(at)


def test_there_is_no_layout_switch_and_none_of_the_old_card_widgets_remain():
    with hub() as (at, _):
        assert list(at.exception) == []
        assert not _has(at.radio, "responses_view") and not _has(at.selectbox, "responses_inbox_filter")
        assert not any("Classic" in str(getattr(w, "options", "")) for w in at.radio)
        assert not any("View full conversation" in e.label for e in at.expander)
        assert not any((b.key or "").startswith(("mark_read_", "send_reply_")) for b in at.button)       # the old per-card buttons


def test_an_email_with_no_readable_text_says_so_in_its_card_and_in_the_list():
    blank = _resp("rE", "5", '"Zed Z" <zed@abc.com>', "Re: Hello", "", "2026-10-12 17:00:00", FullBody="")
    with hub(_two_campaign_ws(extra_a_responses=[blank])) as (at, _):
        assert _rows(at)[0]["preview"] == "(no text in this email)"
        _open_conversation(at, A5)
        empties = [c for c in _cards(at) if c["empty"]]
        assert len(empties) == 1 and "has no readable text" in empties[0]["empty"] and empties[0]["lines"] == []
        assert "Not for me, thanks." in _bodies(at)                                # the real reply still shows


def test_a_reply_with_no_matching_lead_still_shows_what_was_received_and_says_what_cannot_be_shown():
    orphan = _resp("rZ", "77", '"Nobody Known" <nobody@abc.com>', "Re: Who?", "Who am I?", "2026-10-12 16:00:00", FullBody="Who am I replying to?")
    with hub(_two_campaign_ws(extra_a_responses=[orphan])) as (at, _):
        assert list(at.exception) == [] and f"{FIXTURE_CAMPAIGN}:77" in _row_keys(at)
        assert at.session_state["hub_selected"] == f"{FIXTURE_CAMPAIGN}:77"
        assert "Who am I replying to?" in _bodies(at)
        assert any("so the emails the system sent are not shown" in c.value for c in at.caption)
        _compose(at, f"{FIXTURE_CAMPAIGN}:77")
        assert any("Couldn't find this lead's sender account" in c.value for c in at.caption)


def test_quoted_history_in_a_reply_is_shown_as_a_quote_without_its_markers():
    quoted = _resp("rQ", "5", '"Zed Z" <zed@abc.com>', "Re: Hello", "x", "2026-10-12 17:00:00",
                   FullBody="Fine by me.\n\n> On Oct 11, Harish wrote:\n> Would you license it?")
    with hub(_two_campaign_ws(extra_a_responses=[quoted])) as (at, _):
        _open_conversation(at, A5)
        newest = _cards(at)[0]
        assert [kind for kind, _ in newest["lines"]] == ["plain", "plain", "quoted", "quoted"]
        assert newest["lines"][2][1] == "On Oct 11, Harish wrote:" and newest["lines"][3][1] == "Would you license it?"


def test_a_long_list_is_capped_and_says_how_many_are_shown(monkeypatch):
    monkeypatch.setattr(inbox_view, "MAX_ROWS", 2)
    with hub() as (at, _):
        assert len(_open_buttons(at)) == 2 and len(_rows(at)) == 2
        assert any("Showing the 2 most recent of 3" in c.value for c in at.caption)
        assert _rail_counts(at)["All"] == "3"                                      # the rail still counts everything


# ---- hostile text

HOSTILE_NAME = '[click](http://evil.example) **BOLD** :red[x] <b>tag</b>'
HOSTILE_SUBJECT = '![pixel](http://evil.example/p.png) $$bad$$ <script>alert(1)</script> "quoted"'
HOSTILE_BODY = '<script>alert(1)</script>\n**bold?** :smile: $$x^2$$\n[a link](http://evil.example)\n![img](http://evil.example/p.png)\n"><img src=x onerror=alert(1)>'


def _hostile_ws():
    hostile = _resp("rX", "9", f'"{HOSTILE_NAME}" <x@evil.example>', HOSTILE_SUBJECT, "[more](http://evil.example) <i>x</i>",
                    "2026-10-12 15:00:00", FullBody=HOSTILE_BODY)
    ws = _two_campaign_ws(extra_a_responses=[hostile])
    ws[f"{FIXTURE_CAMPAIGN} Master Sheet"] = FakeWorksheet([
        {"LeadID": "1", "Email": "kari@abc.com", "SenderAccount": "sales1"}, {"LeadID": "9", "Email": "x@evil.example", "SenderAccount": "sales1"}])
    return ws


class _Whitelist(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.tags, self.attrs = set(), set()

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag)
        self.attrs |= {name for name, _ in attrs}


def test_hostile_text_in_a_name_subject_preview_or_body_is_shown_as_plain_text_and_never_as_markup():
    with hub(_hostile_ws()) as (at, _):
        assert list(at.exception) == []
        found = _Whitelist()
        for element in at.markdown:
            if "ux-" in element.value and "<style>" not in element.value:
                found.feed(element.value)
        assert found.tags <= {"div", "span", "b"} and found.attrs <= {"class", "style", "title"}, (found.tags, found.attrs)
        row = _rows(at)[0]
        assert row["name"] == HOSTILE_NAME and row["subject"] == HOSTILE_SUBJECT          # exactly as written, as text
        assert _header(at)["name"] == HOSTILE_NAME and _cards(at)[0]["title"] == HOSTILE_SUBJECT
        assert _cards(at)[0]["body"] == HOSTILE_BODY


def test_the_invisible_buttons_labels_are_escaped_too():
    with hub(_hostile_ws()) as (at, _):
        label = next(b.label for b in _open_buttons(at) if b.key == f"hub_open_{FIXTURE_CAMPAIGN}:9")
        assert "\\[click\\]\\(http" in label and "\\*\\*BOLD\\*\\*" in label


# ================================================================ the emails the team sent by hand, or from here

def test_what_you_typed_by_hand_appears_in_the_conversation_in_the_right_place():
    ws = _with_sent_mail(_sent("<m1@gmail>", "2026-10-12 14:10:00", "We can offer $200 for six months."),
                         _sent("<m2@gmail>", "2026-10-12 14:45:00", "Great, I will send the agreement now."))
    with hub(ws) as (at, _):
        _chronological(at)
        assert list(at.exception) == [] and list(at.error) == []
        bodies = _bodies(at)
        assert _order_of(bodies, "Hi! Interested", "We can offer $200", "Also, do you cover shipping?", "Great, I will send the agreement")
        assert [c["from"] for c in _cards(at)].count("You") == 3                    # the template intro + the two you typed


def test_your_quick_answer_never_appears_before_the_question_it_answers():
    """Her reply was LOGGED at 14:30 but really sent at 14:20; you answered at 14:25."""
    ws = _two_campaign_ws()
    for record in ws[f"{FIXTURE_CAMPAIGN} Response Sheet"]._records:
        if record["ResponseID"] == "rA2":
            record["MessageDate"] = "2026-10-12 14:20:00"
    ws = _with_sent_mail(_sent("<m@gmail>", "2026-10-12 14:25:00", "I have just sent the request."), ws=ws)
    with hub(ws) as (at, _):
        _chronological(at)
        assert _order_of(_bodies(at), "Also, do you cover shipping?", "I have just sent the request.")


def test_the_same_conversation_is_complete_inside_the_campaigns_own_tab():
    ws = _with_sent_mail(_sent("<m1@gmail>", "2026-10-12 14:10:00", "We can offer $200 for six months."))
    with _campaign_tab(ws) as (at, _):
        assert list(at.exception) == [] and list(at.error) == []
        assert "We can offer $200 for six months." in _bodies(at) and "Hi! Interested — what are your rates?" in _bodies(at)


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
        assert "Hi! Interested — what are your rates?" in _bodies(at)
        assert any("emails you sent yourself could not be loaded" in c.value for c in at.caption)


def test_another_campaigns_sent_mail_for_the_same_lead_number_is_never_mixed_in():
    ws = _with_sent_mail(_sent("<a5@gmail>", "2026-10-12 15:00:00", "Message to A's lead five", lead="5"))
    ws[f"{OTHER} Sent Mail"] = FakeWorksheet([_sent("<b5@gmail>", "2026-10-12 15:10:00", "Message to B's lead five", lead="5")])
    with hub(ws) as (at, _):
        _open_conversation(at, A5)
        assert "Message to A's lead five" in _bodies(at) and "Message to B's lead five" not in _bodies(at)
        _open_conversation(at, B5)
        assert "Message to B's lead five" in _bodies(at) and "Message to A's lead five" not in _bodies(at)


def test_only_the_open_leads_messages_are_shown_not_another_leads_in_the_same_campaign():
    with hub(_with_sent_mail(_sent("<z@gmail>", "2026-10-12 15:00:00", "Written to Zed only", lead="5"))) as (at, _):
        assert "Written to Zed only" not in _bodies(at)                             # Kari (lead 1) is open


def test_a_sent_message_with_no_text_says_so_like_any_other_empty_email():
    with hub(_with_sent_mail(_sent("<empty@gmail>", "2026-10-12 14:10:00", ""))) as (at, _):
        assert any("has no readable text" in c["empty"] for c in _cards(at))


def test_hand_typed_messages_still_show_when_the_lead_cannot_be_found_in_the_master_sheet():
    orphan = _resp("rZ", "77", '"Nobody Known" <nobody@abc.com>', "Re: Who?", "Who am I?", "2026-10-12 16:00:00",
                   FullBody="Who am I replying to?", MessageDate="2026-10-12 15:50:00")
    ws = _with_sent_mail(_sent("<o@gmail>", "2026-10-12 15:55:00", "Replying to someone not on the sheet", lead="77"),
                         ws=_two_campaign_ws(extra_a_responses=[orphan]))
    with hub(ws) as (at, _):
        _chronological(at)
        assert _order_of(_bodies(at), "Who am I replying to?", "Replying to someone not on the sheet")


def test_the_refresh_button_brings_in_messages_stored_since_the_page_loaded():
    ws = _with_sent_mail()
    with hub(ws) as (at, _):
        assert "Newly stored message" not in _bodies(at)
        ws[SENT_TAB]._records.append(_sent("<new@gmail>", "2026-10-12 15:00:00", "Newly stored message"))
        at.run(timeout=25)
        assert "Newly stored message" not in _bodies(at)                          # still the cached copy
        next(b for b in at.button if b.label == "🔄 Refresh").click()
        at.run(timeout=25)
        assert "Newly stored message" in _bodies(at)


def test_the_campaign_pages_refresh_data_button_does_the_same():
    ws = _with_sent_mail()
    with _campaign_tab(ws) as (at, _):
        assert "Newly stored message" not in _bodies(at)
        ws[SENT_TAB]._records.append(_sent("<new@gmail>", "2026-10-12 15:00:00", "Newly stored message"))
        at.run(timeout=25)
        assert "Newly stored message" not in _bodies(at)
        next(b for b in at.button if b.label == "🔄 Refresh data").click()
        at.run(timeout=25)
        assert "Newly stored message" in _bodies(at)


def test_the_reported_conversation_shows_every_message_in_true_order():
    """A creator replied five times and you answered by hand six times from the mailbox (made-up content)."""
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
    ws[SENT_TAB] = FakeWorksheet([_sent(f"<mine{i}@gmail>", at_, text) for i, (at_, text) in enumerate(mine, start=1)])
    with hub(ws) as (at, _):
        _chronological(at)
        assert list(at.exception) == []
        assert len(_cards(at)) == 5                                            # a long conversation is folded to its newest emails...
        _click(at, f"ux_expand_hub_{A1}")                                      # ...until the rest are asked for
        assert _order_of(_bodies(at), "Her message ONE", "My message ONE", "Her message TWO", "My message TWO", "My message THREE",
                         "Her message THREE", "Her message FOUR", "My message FOUR", "My message FIVE", "Her message FIVE", "My message SIX")
        assert len(_cards(at)) == 1 + 5 + 6 and not any(c["empty"] for c in _cards(at))


# ================================================================ a campaign's own Responses tab

def test_the_campaign_tab_opens_in_the_inbox_layout_with_only_that_campaigns_conversations():
    with _campaign_tab() as (at, _):
        assert list(at.exception) == [], list(at.exception)
        assert list(at.error) == []
        assert not _has(at.radio, f"responses_view_{FIXTURE_CAMPAIGN}")              # no layout switch any more
        assert _row_keys(at, TAB) == [A1, A5]                                       # Campaign B's conversation is not here
        assert at.session_state[f"{TAB}_selected"] == A1
        assert len(at.tabs) == 6                                                    # the tab count is unchanged


def test_the_campaign_tab_tracks_unread_replies_like_the_responses_page():
    """Changed on purpose: the campaign's own tab used to have no read tracking; it now shares the main page's, so unread replies are marked here too
    (and the tab's badge counts them)."""
    with _campaign_tab() as (at, _):
        rows = _rows(at)
        assert rows and all(r["unread"] for r in rows)                              # nothing in this campaign has been read yet
        assert any((b.key or "").startswith(f"{TAB}_mark_read_") for b in at.button)
        assert "Unread" in _rail_counts(at)                                          # and the rail has its Unread item



def test_the_campaign_tab_does_not_repeat_the_campaign_name_on_every_row():
    with _campaign_tab() as (at, _):
        assert not any(FIXTURE_CAMPAIGN in r["meta"] for r in _rows(at)) and FIXTURE_CAMPAIGN not in _header(at)["email"]


def test_the_all_campaigns_page_does_show_the_campaign_on_each_row_and_in_the_header():
    with hub() as (at, _):
        assert _header(at)["email"].endswith(f"· {FIXTURE_CAMPAIGN}")
        rows = _rows(at)
        assert rows[0]["meta"].startswith(FIXTURE_CAMPAIGN) and rows[1]["meta"].startswith(OTHER) and rows[2]["meta"].startswith(FIXTURE_CAMPAIGN)


def test_the_campaign_tab_shows_the_conversation_and_opens_another():
    with _campaign_tab() as (at, _):
        assert "Hi! Interested — what are your rates?" in _bodies(at)
        _open_conversation(at, A5, TAB)
        assert "Not for me, thanks." in _bodies(at) and "I am a different person entirely" not in _bodies(at)


def test_the_campaign_tab_rail_filters_its_own_list():
    with _campaign_tab() as (at, _):
        _click(at, f"ux_railbtn_{TAB}_not_interested")
        assert _row_keys(at, TAB) == [A5] and at.session_state[f"{TAB}_selected"] == A5


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
        _compose(at, A1, TAB)
        at.text_area(key=f"{TAB}_body_{A1}").set_value("Thanks Kari!")
        at.run(timeout=25)
        _click(at, f"{TAB}_send_{A1}")
        assert list(at.exception) == [] and list(at.error) == []
        payload = _committed(captured)
        assert payload["to"] == "kari@abc.com" and payload["body"] == "Thanks Kari!"
        assert captured["dispatches"][0] == ("send_reply.yml", {"campaign": FIXTURE_CAMPAIGN, "payload_path": captured["commits"][0]["path"]})
        assert any(call.args == (1.0,) for call in slept.call_args_list)             # as the tab always did
        assert at.text_area(key=f"{TAB}_body_{A1}").value == ""


def test_a_blank_reply_from_the_campaign_tab_is_refused():
    with _campaign_tab() as (at, captured):
        _compose(at, A1, TAB)
        _click(at, f"{TAB}_send_{A1}")
        assert at.error and "commits" not in captured


def test_a_campaign_with_no_replies_still_just_says_so():
    ws = _two_campaign_ws()
    ws[f"{FIXTURE_CAMPAIGN} Response Sheet"] = FakeWorksheet([])
    with _campaign_tab(ws) as (at, _):
        assert list(at.exception) == [] and any("No responses yet." in i.value for i in at.info)
        assert not _has(at.radio, f"responses_view_{FIXTURE_CAMPAIGN}")


# ================================================================ long conversations: Show earlier / Show fewer, Read more / Read less

def _long_conversation(n_sent, lead="1"):
    """Kari's 3 messages plus `n_sent` emails typed by hand: 3 + n_sent emails in all."""
    return _with_sent_mail(*[_sent(f"<m{i}@gmail>", f"2026-10-13 {i:02d}:00:00", f"Hand-typed email number {i}") for i in range(n_sent)])      # all later than her replies


def _expand_key(namespace="hub", gk=A1):
    return f"ux_expand_{namespace}_{gk}"


@pytest.mark.parametrize("n_sent, collapses", [(0, False), (4, False), (5, False), (6, True), (10, True)])
def test_a_conversation_of_more_than_eight_emails_is_folded_and_a_short_one_never_is(n_sent, collapses):
    with hub(_long_conversation(n_sent)) as (at, _):
        total = 1 + 2 + n_sent                                                  # the template intro, her two replies, and the ones typed by hand
        assert _has(at.button, _expand_key()) is collapses
        assert len(_cards(at)) == (5 if collapses else total)


def test_a_folded_conversation_shows_the_newest_emails_and_says_how_many_are_hidden():
    with hub(_long_conversation(10)) as (at, _):                                # 13 emails: 5 shown, 8 hidden
        bodies = _bodies(at)
        assert [b for b in bodies if b.startswith("Hand-typed")] == [f"Hand-typed email number {i}" for i in (9, 8, 7, 6, 5)]   # the newest five, newest first
        assert next(b.label for b in at.button if b.key == _expand_key()) == "▾ Show 8 earlier emails"


def test_show_earlier_opens_the_whole_conversation_and_show_fewer_folds_it_again():
    with hub(_long_conversation(10)) as (at, _):
        _click(at, _expand_key())
        assert len(_cards(at)) == 13
        assert next(b.label for b in at.button if b.key == _expand_key()) == "▴ Show fewer — hide the 8 earlier emails"
        _click(at, _expand_key())
        assert len(_cards(at)) == 5


def test_when_oldest_first_the_hidden_emails_are_the_old_ones_at_the_top_and_the_newest_five_are_shown():
    with hub(_long_conversation(10)) as (at, _):
        _chronological(at)
        bodies = _bodies(at)
        assert len(bodies) == 5 and "Hand-typed email number 9" in bodies[-1] and not any("number 0" in b for b in bodies)


def test_each_conversation_is_folded_or_opened_on_its_own_and_remembers_it():
    ws = _long_conversation(10)
    ws[SENT_TAB]._records.extend(_sent(f"<z{i}@gmail>", f"2026-10-12 12:{i:02d}:00", f"Zed email {i}", lead="5") for i in range(12))
    with hub(ws) as (at, _):
        _click(at, _expand_key())                                              # open Kari's
        _open_conversation(at, A5)
        assert _has(at.button, _expand_key("hub", A5)) and len(_cards(at)) == 5       # Zed's is still folded
        _open_conversation(at, A1)
        assert len(_cards(at)) == 13                                           # and Kari's is still open


def test_the_fold_is_a_button_that_does_not_break_the_one_line_safe_markup():
    with hub(_long_conversation(10)) as (at, _):
        assert list(at.exception) == [] and list(at.error) == []


def _with_long_body(lines):
    ws = _two_campaign_ws()
    for record in ws[f"{FIXTURE_CAMPAIGN} Response Sheet"]._records:
        if record["ResponseID"] == "rA1":
            record["FullBody"] = "\n".join(f"Line {i} of her long email" for i in range(lines))
    return ws


def _more_key(index, namespace="hub", gk=A1):
    return f"ux_more_{namespace}_{gk}_{index}"


def test_a_long_email_is_cropped_with_read_more_but_the_newest_email_never_is():
    with hub(_with_long_body(40)) as (at, _):
        cards = _cards(at)                                                      # newest first: rA2 (short), rA1 (long), the intro
        assert [c["clamped"] for c in cards] == [False, True, False]
        assert next(b.label for b in at.button if (b.key or "").startswith("ux_more_hub_")) == "Read more ▾"


def test_the_newest_email_is_never_cropped_however_long_it_is():
    ws = _two_campaign_ws()
    for record in ws[f"{FIXTURE_CAMPAIGN} Response Sheet"]._records:
        if record["ResponseID"] == "rA2":
            record["FullBody"] = "\n".join(f"Line {i}" for i in range(60))
    with hub(ws) as (at, _):
        assert _cards(at)[0]["clamped"] is False and not any((b.key or "").startswith("ux_more_") for b in at.button)


def test_read_more_shows_the_whole_email_and_read_less_crops_it_again():
    with hub(_with_long_body(40)) as (at, _):
        key = next(b.key for b in at.button if (b.key or "").startswith("ux_more_hub_"))
        _click(at, key)
        assert [c["clamped"] for c in _cards(at)] == [False, False, False]
        assert next(b.label for b in at.button if b.key == key) == "Read less ▴"
        _click(at, key)
        assert [c["clamped"] for c in _cards(at)] == [False, True, False]


def test_a_cropped_email_still_contains_every_line_so_nothing_is_hidden_from_the_page():
    with hub(_with_long_body(40)) as (at, _):
        assert len(_cards(at)[1]["lines"]) == 40


def test_a_short_email_has_no_read_more():
    with hub() as (at, _):
        assert not any((b.key or "").startswith("ux_more_") for b in at.button)


# ================================================================ the Status menu: a lead's status set by hand

def _status_key(gk=A1, namespace="hub"):
    return f"{namespace}_statuspick_{gk}"


def _set_status(at, status, gk=A1, namespace="hub"):
    at.radio(key=_status_key(gk, namespace)).set_value(status)
    at.run(timeout=25)


def _manual_rows(ws, intent, lead="1"):
    for record in ws[f"{FIXTURE_CAMPAIGN} Response Sheet"]._records:
        if str(record["LeadID"]) == lead:
            record.update({"Intent": intent, "IntentConfidence": "Manual"})
    return ws


def _status_payload(captured):
    return json.loads(captured["commits"][0]["content"].decode("utf-8"))


def test_the_status_menu_offers_automatic_and_the_four_statuses_and_starts_on_automatic():
    with hub() as (at, _):
        radio = at.radio(key=_status_key())
        assert radio.options == ["Automatic", "Lead", "Interested", "Not interested", "Unclear"] and radio.value == "auto"
        assert [r.label for r in at.radio if r.key == _status_key()] == ["Lead status"]


def test_choosing_a_status_commits_a_payload_and_starts_the_workflow():
    with hub() as (at, captured):
        _set_status(at, "not_interested")
        assert list(at.exception) == [] and list(at.error) == []
        payload = _status_payload(captured)
        assert payload["campaign"] == FIXTURE_CAMPAIGN and payload["lead_id"] == "1" and payload["status"] == "not_interested"
        assert captured["commits"][0]["path"].startswith(f"status_changes/{FIXTURE_CAMPAIGN}/") and captured["commits"][0]["path"].endswith("-1.json")
        assert captured["dispatches"] == [("set_response_status.yml", {"campaign": FIXTURE_CAMPAIGN, "payload_path": captured["commits"][0]["path"]})]
        assert any("Status saved" in s.value for s in at.success)


def test_the_payload_holds_no_personal_data_because_the_repository_is_public():
    with hub() as (at, captured):
        _set_status(at, "interested")
        text = captured["commits"][0]["content"].decode("utf-8")
        assert set(json.loads(text)) == {"campaign", "lead_id", "status", "set_at"}
        for private in ("@", "kari", "Kari", "rates", "Partnership"):
            assert private not in text


def test_the_new_status_shows_at_once_marked_as_saving_and_moves_the_rail_counts():
    with hub() as (at, _):
        _set_status(at, "not_interested")
        assert _header(at)["chips"][-1] == "👎 Not interested · set manually · saving…"
        assert _rail_counts(at)["Not interested"] == "2" and _rail_counts(at)["Interested"] == "0"
        _click(at, "ux_railbtn_hub_not_interested")
        assert A1 in _row_keys(at)


def test_the_menu_keeps_showing_the_status_in_force():
    with hub() as (at, _):
        _set_status(at, "lead")
        assert at.radio(key=_status_key()).value == "lead"


def test_a_status_set_by_hand_in_the_sheet_beats_the_ai_and_the_keywords():
    ws = _manual_rows(_two_campaign_ws(), "Not Interested")                    # Kari's rows also say 'Interested' by AI and 'rates' in the words
    with hub(ws) as (at, _):
        assert _header(at)["chips"][-1] == "👎 Not interested · set manually"
        assert at.radio(key=_status_key()).value == "not_interested"


def test_a_status_set_by_hand_beats_even_a_later_ai_reading():
    ws = _manual_rows(_two_campaign_ws(), "Not Interested")
    ws[f"{FIXTURE_CAMPAIGN} Response Sheet"]._records.append(
        _resp("rLate", "1", '"Kari B" <kari@abc.com>', "Re: Partnership", "x", "2026-10-13 10:00:00", FullBody="Yes!", Intent="Interested", IntentConfidence="High"))
    with hub(ws) as (at, _):
        assert _header(at)["chips"][-1].startswith("👎 Not interested · set manually")


def test_a_status_set_by_hand_beats_a_latest_auto_reply_in_the_rail_too():
    ws = _manual_rows(_two_campaign_ws(), "Interested")
    ws[f"{FIXTURE_CAMPAIGN} Response Sheet"]._records.append(
        _resp("rOoo", "1", '"Kari B" <kari@abc.com>', "Automatic reply", "away", "2026-10-13 10:00:00", Classification="Auto-Reply"))
    with hub(ws) as (at, _):
        _click(at, "ux_railbtn_hub_interested")
        assert A1 in _row_keys(at)
        _click(at, "ux_railbtn_hub_auto")
        assert A1 not in _row_keys(at)


def test_automatic_ignores_a_manual_status_at_once_and_goes_back_to_the_automatic_reading():
    ws = _manual_rows(_two_campaign_ws(), "Not Interested")
    with hub(ws) as (at, captured):
        _set_status(at, "auto")
        assert _status_payload(captured)["status"] == "auto"
        assert _header(at)["chips"][-1] == "👍 Interested · by keywords"            # her words, now that the manual value is ignored
        assert _status_payload(captured)["lead_id"] == "1"


def test_automatic_on_a_reply_with_no_clear_words_falls_back_to_unclear_not_to_the_old_manual_value():
    ws = _manual_rows(_zed_says("Thanks!"), "Interested", lead="5")                 # Zed said nothing clear; a manual status was set on him
    with hub(ws) as (at, _):
        _open_conversation(at, A5)
        assert _header(at)["chips"][-1] == "👍 Interested · set manually"
        _set_status(at, "auto", gk=A5)
        assert _header(at)["chips"][-1] == "❔ Unclear"


def test_once_the_sheet_catches_up_the_word_saving_goes_away():
    ws = _two_campaign_ws()
    with hub(ws) as (at, _):
        _set_status(at, "not_interested")
        assert at.session_state["hub_pending_status"] == {A1: "not_interested"}
        _manual_rows(ws, "Not Interested")                                       # the workflow has written it
        next(b for b in at.button if b.label == "🔄 Refresh").click()
        at.run(timeout=25)
        assert _header(at)["chips"][-1] == "👎 Not interested · set manually" and at.session_state["hub_pending_status"] == {}


def test_a_failed_save_says_so_and_changes_nothing():
    with hub(dispatch_error=GitHubActionsError("boom")) as (at, _):
        _set_status(at, "not_interested")
        assert any("Couldn't save the status: boom" in e.value for e in at.error)
        assert at.session_state["hub_pending_status"] == {} and _header(at)["chips"][-1] == "👍 Interested · High confidence"


def test_a_status_for_one_conversation_does_not_touch_another():
    with hub() as (at, _):
        _set_status(at, "not_interested")
        _open_conversation(at, A5)
        assert at.radio(key=_status_key(A5)).value == "auto" and _header(at)["chips"][-1] == "👎 Not interested · High confidence"


def test_a_reply_that_is_not_linked_to_a_lead_cannot_be_given_a_status():
    orphan = _resp("rZ", "", '"Nobody" <nobody@abc.com>', "Re: Who?", "hi", "2026-10-13 16:00:00", FullBody="hi")
    with hub(_two_campaign_ws(extra_a_responses=[orphan])) as (at, _):
        key = next(k for k in _row_keys(at) if "_no_lead_" in k)
        _open_conversation(at, key)
        assert not _has(at.radio, _status_key(key)) and any("isn't linked to a lead" in c.value for c in at.caption)


def test_the_campaign_tab_has_its_own_status_menu_and_saves_for_that_campaign():
    with _campaign_tab() as (at, captured):
        _set_status(at, "interested", gk=A1, namespace=TAB)
        assert _status_payload(captured)["campaign"] == FIXTURE_CAMPAIGN and captured["dispatches"][0][0] == "set_response_status.yml"


# ---- statuses stored by the Label Replies job

def _stored_keywords(ws, response_id, intent):
    for record in ws[f"{FIXTURE_CAMPAIGN} Response Sheet"]._records:
        if record["ResponseID"] == response_id:
            record.update({"Intent": intent, "IntentConfidence": "Keywords"})
    return ws


def test_a_status_the_label_job_stored_from_the_words_is_shown_as_by_keywords():
    ws = _stored_keywords(_zed_says("No thank you"), "rA3", "Not Interested")
    with hub(ws) as (at, _):
        _open_conversation(at, A5)
        assert _header(at)["chips"][-1] == "👎 Not interested · by keywords"


def test_a_stored_keyword_lead_is_only_a_hint_and_does_not_override_a_clear_yes():
    ws = _two_campaign_ws()
    _stored_keywords(ws, "rA2", "Lead / Needs Follow-up")                       # the follow-up question, labelled Lead by the job
    with hub(ws) as (at, _):
        assert _header(at)["chips"][-1] == "👍 Interested · High confidence"


def test_a_stored_keyword_yes_or_no_counts_as_a_clear_reading():
    ws = _two_campaign_ws()
    _stored_keywords(ws, "rA2", "Not Interested")
    with hub(ws) as (at, _):
        assert _header(at)["chips"][-1] == "👎 Not interested · by keywords"       # newer than her earlier AI-read yes


# ================================================================ "Mark all read" on the all-campaigns Responses page

def _unread_response_keys(ws):
    """Every reply in every campaign's Response Sheet that the Sheet itself does not already call read, as the page keys it ('campaign:ResponseID')."""
    keys = set()
    for name, sheet in ws.items():
        if name.endswith(" Response Sheet"):
            campaign = name[: -len(" Response Sheet")]
            keys |= {f"{campaign}:{r['ResponseID']}" for r in sheet._records if str(r.get("IsRead") or "").strip().lower() not in ("yes", "true", "1")}
    return keys


def test_the_all_campaigns_page_marks_every_unread_conversation_of_every_campaign_read_in_one_go():
    ws = _two_campaign_ws()
    expected = _unread_response_keys(ws)
    assert expected and len({k.split(":")[0] for k in expected}) == 2                      # the premise: unread replies in BOTH campaigns
    with hub(ws) as (at, captured):
        popover = next(p for p in at.get("popover") if p.proto.popover.label == "✓ Mark all read")
        assert popover.proto.popover.disabled is False
        at.button(key="hub_markall_go").click()
        at.run(timeout=25)
        assert list(at.exception) == [] and list(at.error) == []
        assert at.session_state["read_response_keys"] == expected and at.session_state["pending_sync_keys"] == expected
        assert any(s.value.startswith("Marked ") and "as read" in s.value for s in at.success)
        assert any(b.label == f"🔄 Sync read status ({len(expected)} pending)" for b in at.button)
        assert captured.get("commits", []) == [] and captured.get("dispatches", []) == []   # nothing is written until Sync is pressed


def test_the_campaign_tab_and_the_all_campaigns_page_use_the_same_control_with_the_same_wording():
    with _campaign_tab() as (at, _):
        tab_go = at.button(key=f"{TAB}_markall_go")
        assert tab_go.label.startswith("✓ Mark ") and tab_go.label.endswith(" as read")
    with hub() as (at, _):
        hub_go = at.button(key="hub_markall_go")
        assert hub_go.label.startswith("✓ Mark ") and hub_go.label.endswith(" as read")
