"""Reading a mailbox's Sent folder: finding it, staying read-only, and downloading only what is needed."""
import os
import sys
from datetime import datetime
from email.message import EmailMessage

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import outreach  # noqa: E402


def _raw(mid, to="andrea@creator.com", subject="Re: x", date="Fri, 04 Sep 2026 17:00:00 +0000", body="hello"):
    m = EmailMessage()
    m["Message-ID"], m["To"], m["Subject"], m["Date"], m["From"] = mid, to, subject, date, "harish@kelson.agency"
    m.set_content(body)
    return m.as_bytes()


class FakeMailbox:
    """Folders of messages. Anything that could change a message raises."""
    instances = []

    def __init__(self, host, port):
        FakeMailbox.instances.append(self)
        self.calls, self.logged_out, self.selected = [], False, None

    list_lines = []
    folders = {}
    list_error = None

    def login(self, user, password):
        self.calls.append(("login", user))

    def list(self):
        self.calls.append(("list",))
        if FakeMailbox.list_error:
            raise FakeMailbox.list_error
        return "OK", list(FakeMailbox.list_lines)

    def select(self, folder, readonly=False):
        self.calls.append(("select", folder, readonly))
        name = folder[1:-1].replace('\\"', '"').replace("\\\\", "\\") if folder.startswith('"') else folder
        if name not in FakeMailbox.folders:
            return "NO", [b"no such folder"]
        self.selected = name
        return "OK", [b"1"]

    def search(self, charset, *criteria):
        self.calls.append(("search", criteria))
        return "OK", [b" ".join(str(i).encode() for i in FakeMailbox.folders[self.selected])]

    def fetch(self, num, spec):
        self.calls.append(("fetch", int(num), spec))
        raw = FakeMailbox.folders[self.selected][int(num)]
        if raw is None:
            raise OSError("this message cannot be read")
        if "HEADER.FIELDS" in spec:
            head = raw.split(b"\n\n", 1)[0] + b"\n\n"
            return "OK", [(b"1 (BODY[HEADER.FIELDS] {1}", head), b")"]
        return "OK", [(b"1 (RFC822 {1}", raw), b")"]

    def logout(self):
        self.logged_out = True

    def store(self, *a, **k):
        raise AssertionError("reading the Sent folder must never change a message")

    expunge = copy = delete = append = store


GMAIL_LIST = [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Sent Mail"',
              b'(\\HasNoChildren \\Trash) "/" "[Gmail]/Trash"', b'(\\HasNoChildren \\Drafts) "/" "[Gmail]/Drafts"']


@pytest.fixture
def mailbox(monkeypatch):
    FakeMailbox.instances, FakeMailbox.list_error = [], None
    FakeMailbox.list_lines = list(GMAIL_LIST)
    FakeMailbox.folders = {"INBOX": {1: _raw("<in@x>")}, "[Gmail]/Sent Mail": {1: _raw("<a@x>"), 2: _raw("<b@x>", to="stranger@else.com")}}
    monkeypatch.setattr(outreach.imaplib, "IMAP4_SSL", FakeMailbox)
    return FakeMailbox


SINCE = datetime(2026, 9, 1)


def _fetch(accept=lambda header: True, **kw):
    return outreach.imap_fetch_sent("h@kelson.agency", "pw", SINCE, accept, imap_username=kw.get("username"))


# ================================================================ finding the Sent folder

@pytest.mark.parametrize("line, expected", [
    ('(\\HasNoChildren \\Sent) "/" "[Gmail]/Sent Mail"', ["[Gmail]/Sent Mail"]),
    ('(\\HasNoChildren \\Sent) "/" "[Gmail]/Gesendet"', ["[Gmail]/Gesendet"]),                       # any language
    ('(\\Sent) "." INBOX.Sent', ["INBOX.Sent"]),                                                    # unquoted name
    ('(\\HasNoChildren \\Sent) NIL "Sent Items"', ["Sent Items"]),                                  # no hierarchy delimiter
    ('(\\HasNoChildren \\Sent) "/" "[Gmail]/&MFEwjDCrMOkw-"', ["[Gmail]/&MFEwjDCrMOkw-"]),          # modified UTF-7 passed back untouched
    ('(\\HasNoChildren \\Trash) "/" "[Gmail]/Trash"', []),
    ('(\\HasNoChildren) "/" "Sent"', []),                                                           # a folder merely NAMED Sent, not flagged
    ("garbage that is not a LIST line", []),
])
def test_the_servers_own_sent_flag_is_what_finds_the_folder(mailbox, line, expected):
    mailbox.list_lines = [line.encode()]
    found = outreach._sent_folder_candidates(mailbox("h", 1))
    assert found[:len(expected)] == expected
    assert found[len(expected):] == [n for n in ("[Gmail]/Sent Mail", "Sent", "Sent Items", "Sent Messages", "INBOX.Sent") if n not in expected]


def test_a_localised_sent_folder_is_found_even_though_its_name_is_not_in_the_usual_list(mailbox):
    mailbox.list_lines = [b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Gesendet"']
    mailbox.folders = {"[Gmail]/Gesendet": {1: _raw("<a@x>")}}
    assert [m["message_id"] for m in _fetch()] == ["<a@x>"]


def test_when_the_server_lists_nothing_the_usual_names_are_tried(mailbox):
    mailbox.list_lines = []
    mailbox.folders = {"Sent Items": {1: _raw("<a@x>")}}
    assert [m["message_id"] for m in _fetch()] == ["<a@x>"]


def test_when_listing_folders_itself_fails_the_usual_names_are_still_tried(mailbox):
    mailbox.list_error = OSError("LIST not allowed")
    assert [m["message_id"] for m in _fetch()] == ["<a@x>", "<b@x>"]


def test_a_mailbox_with_no_sent_folder_raises_so_the_account_can_be_reported_and_still_logs_out(mailbox):
    mailbox.list_lines, mailbox.folders = [], {"INBOX": {1: _raw("<in@x>")}}
    with pytest.raises(RuntimeError, match="Sent folder"):
        _fetch()
    assert mailbox.instances[0].logged_out


def test_a_folder_name_with_spaces_or_quotes_is_quoted_for_the_server(mailbox):
    mailbox.list_lines = [b'(\\Sent) "/" "Sent \\"Mail\\" Items"']
    mailbox.folders = {'Sent "Mail" Items': {1: _raw("<a@x>")}}
    assert [m["message_id"] for m in _fetch()] == ["<a@x>"]


# ================================================================ read-only, and only what is needed

def test_it_is_read_only_logs_in_once_and_always_logs_out(mailbox):
    _fetch(username="login-name")
    (imap,) = mailbox.instances
    assert [c for c in imap.calls if c[0] == "login"] == [("login", "login-name")]
    assert all(c[2] is True for c in imap.calls if c[0] == "select")
    assert all("BODY.PEEK" in c[2] for c in imap.calls if c[0] == "fetch" and "HEADER" in c[2])
    assert imap.logged_out


def test_only_messages_the_caller_accepts_are_downloaded_in_full(mailbox):
    got = _fetch(accept=lambda header: header["To"] == "andrea@creator.com")
    assert [m["message_id"] for m in got] == ["<a@x>"]
    full_fetches = [c for c in mailbox.instances[0].calls if c[0] == "fetch" and "RFC822" in c[2]]
    assert [c[1] for c in full_fetches] == [1]                           # the stranger's message was never downloaded


def test_the_caller_is_shown_only_headers_to_decide_with(mailbox):
    seen = []
    _fetch(accept=lambda header: seen.append(sorted(k.lower() for k in header.keys())) or False)
    assert seen and all("subject" in keys and "to" in keys and "message-id" in keys for keys in seen)
    assert not [c for c in mailbox.instances[0].calls if c[0] == "fetch" and "RFC822" in c[2]]


def test_messages_older_than_the_window_are_dropped_even_if_the_server_matched_their_day(mailbox):
    mailbox.folders["[Gmail]/Sent Mail"] = {1: _raw("<old@x>", date="Tue, 01 Sep 2026 00:30:00 +0000"), 2: _raw("<new@x>")}
    got = outreach.imap_fetch_sent("h", "pw", datetime(2026, 9, 1, 12, 0), lambda header: True)
    assert [m["message_id"] for m in got] == ["<new@x>"]


def test_one_unreadable_message_does_not_stop_the_others(mailbox):
    mailbox.folders["[Gmail]/Sent Mail"] = {1: None, 2: _raw("<b@x>")}
    assert [m["message_id"] for m in _fetch()] == ["<b@x>"]


def test_an_empty_sent_folder_gives_an_empty_list(mailbox):
    mailbox.folders["[Gmail]/Sent Mail"] = {}
    assert _fetch() == []


def test_the_parsed_messages_carry_what_the_matcher_needs(mailbox):
    (message,) = _fetch(accept=lambda header: header["To"] == "andrea@creator.com")
    assert message["body"].strip() == "hello" and message["subject"] == "Re: x"
    assert message["headers"]["to"] == "andrea@creator.com" and message["date"] == datetime(2026, 9, 4, 17, 0)
