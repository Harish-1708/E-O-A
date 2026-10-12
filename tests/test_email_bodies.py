"""Reading the text of incoming emails (including HTML-only ones), looking
specific logged emails up again in a mailbox, and repairing replies that were
logged with no text."""
import email
import os
import sys
from email.message import EmailMessage

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import outreach  # noqa: E402
from test_outreach import FakeSheets, make_lead, ACCOUNTS  # noqa: E402


# ---------------------------------------------------------------- building emails

def _message(**headers):
    m = EmailMessage()
    m["Subject"] = headers.get("subject", "Re: Collab")
    m["From"] = headers.get("sender", "Creator <creator@gmail.com>")
    m["Message-ID"] = headers.get("message_id", "<m1@mail.gmail.com>")
    return m


def _parsed(m):
    """Exactly what the engine sees: the message after a round trip through bytes."""
    return email.message_from_bytes(m.as_bytes())


def _body_of(html=None, plain=None, attachment=None):
    m = _message()
    if plain is not None and html is not None:
        m.set_content(plain)
        m.add_alternative(html, subtype="html")
    elif html is not None:
        m.set_content(html, subtype="html")
    else:
        m.set_content(plain if plain is not None else "")
    if attachment:
        m.add_attachment(attachment[1], maintype=attachment[0].split("/")[0], subtype=attachment[0].split("/")[1],
                         filename=attachment[2])
    return outreach._extract_plain_text_body(_parsed(m))


# ================================================================ extracting the text

def test_an_ordinary_plain_text_reply_is_returned_exactly_as_before():
    assert _body_of(plain="Sounds great, send details.") == "Sounds great, send details.\n"


def test_plain_text_wins_when_both_parts_have_content():
    assert _body_of(plain="The plain version.", html="<p>The HTML version.</p>") == "The plain version.\n"


def test_an_html_only_reply_is_read_instead_of_being_logged_empty():
    """THE reported problem: replies sent as HTML only were stored with no text at all."""
    got = _body_of(html="<html><body><p>I'm sorry. I don't have the raw video.</p><p>Thanks,<br>Tracy</p></body></html>")
    assert got == "I'm sorry. I don't have the raw video.\n\nThanks,\nTracy"


@pytest.mark.parametrize("empty_plain", ["", "\r\n", "   \n\n  "])
def test_an_empty_or_blank_plain_part_falls_back_to_the_html_part(empty_plain):
    assert _body_of(plain=empty_plain, html="<div>Here is what I kept getting: <b>error 403</b></div>") == \
        "Here is what I kept getting: error 403"


def test_an_email_with_nothing_readable_is_still_an_empty_string():
    assert _body_of(plain="", attachment=("image/png", b"\x89PNG-fake", "screenshot.png")) == ""


def test_a_plain_text_attachment_is_never_mistaken_for_the_message():
    assert _body_of(plain="", attachment=("text/plain", b"attached notes", "notes.txt")) == ""


def test_the_message_text_is_used_not_the_attachment_beside_it():
    assert _body_of(plain="see attached", attachment=("application/pdf", b"%PDF-1.4", "a.pdf")) == "see attached\n"


def test_an_unknown_charset_name_no_longer_makes_the_reply_vanish():
    """REGRESSION: LookupError escaped the parser, so the whole reply was dropped and never logged."""
    raw = (b'From: a@x.com\r\nSubject: Re: x\r\nContent-Type: text/plain; charset="x-unknown-charset"\r\n'
           b"Content-Transfer-Encoding: 8bit\r\n\r\nHello there\r\n")
    assert outreach._extract_plain_text_body(email.message_from_bytes(raw)).strip() == "Hello there"
    parsed = outreach._parse_email_message(raw)
    assert parsed["body"].strip() == "Hello there" and parsed["snippet"].strip() == "Hello there"


def test_non_ascii_html_in_quoted_printable_and_base64_is_decoded():
    for encoding in ("quoted-printable", "base64"):
        m = _message()
        m.set_content("<p>Café — naïve “quotes” 日本語</p>", subtype="html", cte=encoding)
        assert _body_of_message(m) == "Café — naïve “quotes” 日本語"


def _body_of_message(m):
    return outreach._extract_plain_text_body(_parsed(m))


def test_html_nested_inside_multipart_related_with_an_inline_image_is_found():
    m = _message()
    m.set_content("")
    m.add_alternative("<p>Look at this: <img src='cid:logo'></p>", subtype="html")
    m.get_payload()[1].add_related(b"\x89PNG", maintype="image", subtype="png", cid="<logo>")
    assert _body_of_message(m) == "Look at this:"


# ---- how the HTML turns into text

@pytest.mark.parametrize("html, expected", [
    ("<p>First.</p><p>Second.</p>", "First.\n\nSecond."),
    ("Intro text<p>Paragraph</p>after", "Intro text\n\nParagraph\n\nafter"),          # a paragraph opening right after plain text
    ("<p>One<br>two<br><br>three</p>", "One\ntwo\n\nthree"),
    ("<div>Hi,</div><div><br></div><div>Line.</div><div>Bye</div>", "Hi,\n\nLine.\nBye"),
    ("<ul><li>one</li><li>two &amp; three</li></ul>", "- one\n- two & three"),
    ("<table><tr><td>a</td><td>b</td></tr><tr><td>c</td><td>d</td></tr></table>", "a b\nc d"),
    ("<p>5 &lt; 6 &amp;&amp; 7&nbsp;&gt; 2</p>", "5 < 6 && 7 > 2"),
    ("<html><head><title>T</title><style>p{color:red}</style></head><body>  <p>  Hello   there  </p> </body></html>", "Hello there"),
    ("<p>Real text</p><script>alert('x')</script><style>.a{}</style>", "Real text"),
    ("<p>Hi <b>there</b>, <i>friend</i>!</p>", "Hi there, friend!"),
])
def test_html_becomes_readable_text(html, expected):
    assert outreach._html_to_text(html) == expected


def test_a_link_keeps_its_address_only_when_the_address_is_not_already_visible():
    assert outreach._html_to_text("<a href='https://example.com/page'>my page</a>") == "my page (https://example.com/page)"
    assert outreach._html_to_text("<a href='https://example.com'>https://example.com</a>") == "https://example.com"
    assert outreach._html_to_text("<a href='https://example.com/'>https://example.com</a>") == "https://example.com"
    assert outreach._html_to_text("<a href='mailto:a@b.com'>email me</a>") == "email me"          # not a web address: left out
    assert outreach._html_to_text("<a href='javascript:alert(1)'>click</a>") == "click"            # never carried into the text


@pytest.mark.parametrize("markup", [
    "<p><b>unclosed <i>tags", "<<>> <p <p>", "</p></div></span>text", "<p>a</p" * 50, "\x00\x01\x02<p>binary</p>",
    "<![CDATA[x]]>", "<!--unterminated comment <p>hidden", "&#99999999999; &bogus; &#xZZ;",
])
def test_malformed_html_never_raises(markup):
    assert isinstance(outreach._html_to_text(markup), str)


def test_a_huge_html_body_is_bounded_not_a_denial_of_service():
    unit = "<p>word "
    got = outreach._html_to_text(unit * 400_000)                   # ~3 MB of markup
    assert 0 < got.count("word") <= 1_000_000 // len(unit)        # only the first million characters were read (a fixed number, not the module's own constant)


def test_if_the_html_parser_itself_fails_the_text_is_still_recovered_by_stripping_tags(monkeypatch):
    def broken(self, data):
        raise RuntimeError("parser blew up")
    monkeypatch.setattr(outreach._HTMLToText, "feed", broken)
    assert outreach._html_to_text("<p>Still <b>readable</b> &amp; safe</p>") == "Still readable & safe"


def test_deeply_nested_html_does_not_crash():
    assert "deep" in outreach._html_to_text("<div>" * 5000 + "deep" + "</div>" * 5000)


# ================================================================ the whole message

def test_an_html_only_message_gets_a_body_and_a_snippet_through_the_real_parser():
    m = _message()
    m.set_content("<p>I'm interested — what are the rates?</p>", subtype="html")
    parsed = outreach._parse_email_message(m.as_bytes())
    assert parsed["body"] == "I'm interested — what are the rates?"
    assert parsed["snippet"] == parsed["body"]


def test_check_replies_logs_an_html_only_reply_with_its_text_and_reads_its_intent_from_that_text(monkeypatch):
    """The visible result of the fix: the logged row has text, and the AI intent reader is shown the
    real words rather than an empty message (which could only ever come back 'Unclear')."""
    m = _message(subject="Re: DudeRobe Paid Creator Collaboration", message_id="<html1@mail.gmail.com>")
    m.set_content("<p>Yes please! Send me the contract.</p>", subtype="html")
    parsed = outreach._parse_email_message(m.as_bytes())
    sheets = FakeSheets([make_lead(_row=2, LeadID="L1", Email="creator@gmail.com", MessageID="")])
    monkeypatch.setattr(outreach, "imap_fetch_recent", lambda *a, **k: [parsed])
    shown_to_reader = []

    def fake_reader(body, api_key):
        shown_to_reader.append(body)
        return {"intent": "Interested", "confidence": "High"}

    monkeypatch.setattr(outreach, "classify_reply_intent", fake_reader)
    outreach.check_replies(sheets, {"sales1": ACCOUNTS["sales1"]}, lookback_hours=24, anthropic_api_key="key")
    (row,) = sheets.responses
    assert row["FullBody"] == "Yes please! Send me the contract." and row["Snippet"] == row["FullBody"]
    assert shown_to_reader == ["Yes please! Send me the contract."]
    assert row["Intent"] == "Interested"


# ================================================================ looking specific emails up again

class FakeIMAP:
    """A mailbox with folders; refuses every operation that could change it."""
    instances = []

    def __init__(self, host, port, folders=None):
        self.folders = folders if folders is not None else FakeIMAP.folders
        self.selected = None
        self.calls = []
        self.logged_out = False
        FakeIMAP.instances.append(self)

    folders = {}

    def login(self, user, password):
        self.calls.append(("login", user))

    def select(self, folder, readonly=False):
        self.calls.append(("select", folder, readonly))
        if folder not in self.folders:
            raise imaplib_error("no such folder")
        self.selected = folder
        return "OK", [b"1"]

    def search(self, charset, *criteria):
        self.calls.append(("search", criteria))
        assert criteria[0] == "HEADER" and criteria[1] == "Message-ID"
        wanted = criteria[2].strip('"')
        hits = [str(i).encode() for i, (mid, _) in self.folders[self.selected].items() if mid == wanted]
        return "OK", [b" ".join(hits)]

    def fetch(self, num, spec):
        self.calls.append(("fetch", num, spec))
        return "OK", [(b"1 (RFC822 {1})", self.folders[self.selected][int(num)][1])]

    def logout(self):
        self.logged_out = True

    def store(self, *a, **k):
        raise AssertionError("the lookup must never change a message")

    expunge = copy = delete = append = store


def imaplib_error(text):
    import imaplib
    return imaplib.IMAP4.error(text)


def _raw_html(message_id, text):
    m = _message(message_id=message_id)
    m.set_content(f"<p>{text}</p>", subtype="html")
    return m.as_bytes()


@pytest.fixture
def mailbox(monkeypatch):
    FakeIMAP.instances = []
    FakeIMAP.folders = {"INBOX": {1: ("<a@x>", _raw_html("<a@x>", "Text A")), 2: ("<b@x>", _raw_html("<b@x>", "Text B"))}}
    monkeypatch.setattr(outreach.imaplib, "IMAP4_SSL", FakeIMAP)
    return FakeIMAP


def _lookup(ids, **kw):
    return outreach.imap_fetch_by_message_ids("a@x.com", "pw", ids, imap_username=kw.get("username"))


def test_a_logged_email_is_found_by_its_message_id_and_parsed(mailbox):
    found = _lookup(["<b@x>"])
    assert set(found) == {"<b@x>"} and found["<b@x>"]["body"] == "Text B"


def test_the_lookup_is_read_only_logs_in_once_and_always_logs_out(mailbox):
    _lookup(["<a@x>", "<b@x>"], username="login-name")
    (imap,) = mailbox.instances
    assert [c for c in imap.calls if c[0] == "login"] == [("login", "login-name")]
    assert all(c[2] is True for c in imap.calls if c[0] == "select")             # readonly
    assert imap.logged_out


def test_an_email_archived_out_of_the_inbox_is_found_in_all_mail(mailbox):
    mailbox.folders["[Gmail]/All Mail"] = {9: ("<archived@x>", _raw_html("<archived@x>", "Archived text"))}
    mailbox.folders['"[Gmail]/All Mail"'] = mailbox.folders.pop("[Gmail]/All Mail")
    assert _lookup(["<a@x>", "<archived@x>"])["<archived@x>"]["body"] == "Archived text"


def test_a_mailbox_without_an_all_mail_folder_is_fine(mailbox):
    assert _lookup(["<a@x>", "<not-here@x>"]).keys() == {"<a@x>"}


def test_unknown_ids_are_simply_absent(mailbox):
    assert _lookup(["<nope@x>"]) == {}


@pytest.mark.parametrize("hostile", ['<a@x>" ALL', "<a@x>\r\nA002 LOGOUT", "<a b@x>", "a@x", "<a\\b@x>", '<a"b@x>', "", "<>"])
def test_a_message_id_that_is_not_a_plain_address_shape_is_never_sent_to_the_server(mailbox, hostile):
    """Message-IDs come from other people's email, then the Sheet: they must never become IMAP commands."""
    assert _lookup([hostile]) == {}
    assert not mailbox.instances or not any(c[0] == "search" for i in mailbox.instances for c in i.calls)


def test_no_usable_ids_means_no_connection_at_all(mailbox):
    assert _lookup([]) == {} and _lookup(["", "<>"]) == {}
    assert mailbox.instances == []


def test_one_unreadable_message_does_not_stop_the_others(mailbox, monkeypatch):
    real = outreach._parse_email_message
    monkeypatch.setattr(outreach, "_parse_email_message",
                        lambda raw: (_ for _ in ()).throw(ValueError("bad")) if b"Text A" in raw else real(raw))
    assert _lookup(["<a@x>", "<b@x>"]).keys() == {"<b@x>"}


def test_it_logs_out_even_when_the_login_fails(monkeypatch):
    class Failing(FakeIMAP):
        def login(self, user, password):
            raise imaplib_error("AUTHENTICATIONFAILED")
    FakeIMAP.instances = []
    monkeypatch.setattr(outreach.imaplib, "IMAP4_SSL", Failing)
    with pytest.raises(Exception):
        _lookup(["<a@x>"])
    assert Failing.instances[0].logged_out


# ================================================================ writing the recovered text

class FakeResponsesWs:
    def __init__(self):
        self.batches = []

    def row_values(self, n):
        return list(outreach.RESPONSES_COLUMNS)

    def batch_update(self, updates, **kwargs):
        self.batches.append((updates, kwargs))


def _connector():
    c = outreach.SheetsConnector.__new__(outreach.SheetsConnector)
    c.responses_ws = FakeResponsesWs()
    c._gspread = outreach.gspread
    return c


def test_update_response_body_writes_the_right_cells_as_plain_text_never_formulas():
    c = _connector()
    c.update_response_body(7, "=HYPERLINK(\"http://evil\",\"x\") real text", "=snip", {"Intent": "Interested"})
    ((updates, kwargs),) = c.responses_ws.batches
    columns = outreach.RESPONSES_COLUMNS
    expected = {outreach.gspread.utils.rowcol_to_a1(7, columns.index(name) + 1): value
                for name, value in {"FullBody": "=HYPERLINK(\"http://evil\",\"x\") real text", "Snippet": "=snip",
                                    "Intent": "Interested"}.items()}
    assert {u["range"]: u["values"][0][0] for u in updates} == expected
    assert kwargs.get("value_input_option") == "RAW"            # a body starting with "=" must not run as a formula


def test_update_response_body_touches_only_the_text_and_intent_cells():
    c = _connector()
    c.update_response_body(3, "text", "snip")
    ((updates, _),) = c.responses_ws.batches
    touched = {u["range"] for u in updates}
    forbidden = {outreach.gspread.utils.rowcol_to_a1(3, outreach.RESPONSES_COLUMNS.index(n) + 1)
                 for n in ("Classification", "MatchMethod", "ActionTaken", "LeadID", "MessageID", "ReceivedAt", "IsRead")}
    assert not (touched & forbidden) and len(touched) == 2


# ================================================================ the repair

class StubSheets:
    def __init__(self, leads, responses):
        self.leads, self.responses, self.writes = leads, responses, []

    def get_all_leads(self):
        return [dict(l) for l in self.leads]

    def get_all_responses(self):
        return [dict(r, _row=i) for i, r in enumerate(self.responses, start=2)]

    def update_response_body(self, row, full_body, snippet, intent_fields=None):
        self.writes.append((row, full_body, snippet, intent_fields))
        self.responses[row - 2].update({"FullBody": full_body, "Snippet": snippet, **(intent_fields or {})})


ACCTS = {"sales1": {"address": "s1@x.com", "app_password": "p"}, "sales2": {"address": "s2@x.com", "app_password": "p"}}


def _response(mid, lead="1", cls=outreach.CLASSIFICATION_GENUINE, full="", snippet=""):
    return {"ResponseID": mid, "MessageID": mid, "LeadID": lead, "Classification": cls, "FullBody": full, "Snippet": snippet}


class Fetcher:
    """An injectable mailbox lookup that records who was asked for what."""
    def __init__(self, by_account, broken=()):
        self.by_account, self.broken, self.asked = by_account, set(broken), []

    def __call__(self, address, password, ids, **kw):
        self.asked.append((address, list(ids)))
        if address in self.broken:
            raise OSError("connection refused")
        return {i: self.by_account.get(address, {})[i] for i in ids if i in self.by_account.get(address, {})}


def _parsed_msg(body):
    return {"body": body, "snippet": body[:500]}


@pytest.fixture(autouse=True)
def _no_real_accounts(monkeypatch):
    monkeypatch.setattr(outreach, "imap_connection_settings", lambda account: ("imap.x", 993, account["address"]))
    monkeypatch.setattr(outreach, "get_imap_password", lambda account: account["app_password"])


LEADS = [{"LeadID": "1", "SenderAccount": "sales1"}, {"LeadID": "2", "SenderAccount": "sales2"}]


def test_only_replies_with_no_text_at_all_are_candidates():
    sheets = StubSheets(LEADS, [_response("<has-full@x>", full="already here"), _response("<has-snippet@x>", snippet="short"),
                                _response("<blank@x>"), _response("<spaces@x>", full="  \n ", snippet=" ")])
    fetch = Fetcher({"s1@x.com": {"<blank@x>": _parsed_msg("hello"), "<spaces@x>": _parsed_msg("world")}})
    results = outreach.backfill_response_bodies(sheets, ACCTS, fetch=fetch)
    assert [(r["row"], r["status"]) for r in results] == [(4, "recovered"), (5, "recovered")]


def test_each_reply_is_looked_up_in_its_own_leads_mailbox_first():
    sheets = StubSheets(LEADS, [_response("<one@x>", lead="1"), _response("<two@x>", lead="2")])
    fetch = Fetcher({"s1@x.com": {"<one@x>": _parsed_msg("A")}, "s2@x.com": {"<two@x>": _parsed_msg("B")}})
    outreach.backfill_response_bodies(sheets, ACCTS, fetch=fetch)
    assert fetch.asked == [("s1@x.com", ["<one@x>"]), ("s2@x.com", ["<two@x>"])]      # nothing asked of the wrong mailbox


def test_a_reply_not_in_its_own_mailbox_is_then_tried_in_the_others_and_nothing_is_asked_twice():
    sheets = StubSheets(LEADS, [_response("<elsewhere@x>", lead="1")])
    fetch = Fetcher({"s2@x.com": {"<elsewhere@x>": _parsed_msg("found in the other inbox")}})
    (r,) = outreach.backfill_response_bodies(sheets, ACCTS, fetch=fetch)
    assert r["status"] == "recovered" and r["full_body"] == "found in the other inbox"
    assert fetch.asked == [("s1@x.com", ["<elsewhere@x>"]), ("s2@x.com", ["<elsewhere@x>"])]


def test_every_outcome_is_reported_with_its_reason_and_no_text_is_invented():
    sheets = StubSheets(LEADS, [_response("<ok@x>"), _response("<img@x>"), _response("<gone@x>"), {**_response(""), "MessageID": ""}])
    fetch = Fetcher({"s1@x.com": {"<ok@x>": _parsed_msg("Real words"), "<img@x>": _parsed_msg("")}})
    by_row = {r["row"]: r for r in outreach.backfill_response_bodies(sheets, ACCTS, fetch=fetch)}
    assert by_row[2]["status"] == "recovered" and by_row[2]["chars"] == 10
    assert by_row[3]["status"] == "still_empty" and "full_body" not in by_row[3]
    assert by_row[4]["status"] == "not_found"
    assert by_row[5]["status"] == "no_message_id"


def test_one_unreachable_mailbox_does_not_stop_the_others_and_is_named_in_the_not_found_reason():
    sheets = StubSheets(LEADS, [_response("<ok@x>", lead="2"), _response("<lost@x>", lead="1")])
    fetch = Fetcher({"s2@x.com": {"<ok@x>": _parsed_msg("fine")}}, broken={"s1@x.com"})
    by_row = {r["row"]: r for r in outreach.backfill_response_bodies(sheets, ACCTS, fetch=fetch)}
    assert by_row[2]["status"] == "recovered"
    assert by_row[3]["status"] == "not_found" and set(by_row[3]["account_errors"]) == {"sales1"}


def test_the_text_is_capped_for_a_cell_and_the_snippet_is_the_first_500_characters():
    sheets = StubSheets(LEADS, [_response("<big@x>")])
    huge = "x" * 60_000
    (r,) = outreach.backfill_response_bodies(sheets, ACCTS, fetch=Fetcher({"s1@x.com": {"<big@x>": _parsed_msg(huge)}}))
    assert len(r["full_body"]) == 49000 and len(r["snippet"]) == 500 and r["chars"] == 60_000


def test_intent_is_read_again_only_for_recovered_genuine_replies_and_only_with_a_key(monkeypatch):
    seen = []
    monkeypatch.setattr(outreach, "classify_reply_intent",
                        lambda body, key: seen.append((body, key)) or {"intent": "Interested", "confidence": "High"})
    rows = [_response("<g@x>"), _response("<auto@x>", cls=outreach.CLASSIFICATION_AUTOREPLY), _response("<img@x>")]
    fetch = Fetcher({"s1@x.com": {"<g@x>": _parsed_msg("Yes please"), "<auto@x>": _parsed_msg("Out of office"),
                                  "<img@x>": _parsed_msg("")}})
    results = {r["row"]: r for r in outreach.backfill_response_bodies(StubSheets(LEADS, rows), ACCTS, "KEY", fetch=fetch)}
    assert seen == [("Yes please", "KEY")]                                       # not the auto-reply, not the empty one
    assert results[2]["intent_fields"]["Intent"] == "Interested" and results[2]["intent_fields"]["IntentClassifiedAt"]
    assert results[3]["intent_fields"] == {}
    seen.clear()
    outreach.backfill_response_bodies(StubSheets(LEADS, rows), ACCTS, None, fetch=fetch)
    assert seen == []                                                            # no key: no AI call at all


def test_the_repair_never_changes_classification_or_anything_but_the_text():
    rows = [dict(_response("<g@x>"), MatchMethod="Header", ActionTaken="Stopped Sequence", IsRead="Yes")]
    sheets = StubSheets(LEADS, rows)
    (r,) = outreach.backfill_response_bodies(sheets, ACCTS, fetch=Fetcher({"s1@x.com": {"<g@x>": _parsed_msg("hi")}}))
    assert set(r) >= {"row", "full_body", "snippet", "intent_fields"}
    assert sheets.writes == []                                                   # computing results writes nothing by itself


def test_two_rows_sharing_a_message_id_are_both_repaired():
    sheets = StubSheets(LEADS, [_response("<dup@x>"), _response("<dup@x>")])
    results = outreach.backfill_response_bodies(sheets, ACCTS, fetch=Fetcher({"s1@x.com": {"<dup@x>": _parsed_msg("same")}}))
    assert [(r["row"], r["status"]) for r in results] == [(2, "recovered"), (3, "recovered")]


# ---- the command

def _run(monkeypatch, capsys, sheets, fetch, dry_run, api_key=None):
    monkeypatch.setattr(outreach, "get_campaign", lambda name, **kw: {})
    monkeypatch.setattr(outreach, "_connect_sheets", lambda cfg: sheets)
    monkeypatch.setattr(outreach, "load_email_accounts", lambda: ACCTS)
    monkeypatch.setattr(outreach, "imap_fetch_by_message_ids", fetch)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    if api_key:
        monkeypatch.setenv("ANTHROPIC_API_KEY", api_key)
    outreach.cmd_backfill_response_bodies(type("Args", (), {"campaign": "C", "dry_run": dry_run})())
    return capsys.readouterr().out


def _secret_rows():
    return [dict(_response("<secret-id@private.example>"), From="Private Person <private.person@secret.example>",
                 Subject="Re: a private subject")]


def test_a_dry_run_writes_nothing_and_says_so(monkeypatch, capsys):
    sheets = StubSheets(LEADS, _secret_rows())
    out = _run(monkeypatch, capsys, sheets, Fetcher({"s1@x.com": {"<secret-id@private.example>": _parsed_msg("Private words")}}), dry_run=True)
    assert sheets.writes == [] and "Would fill in the text of 1 logged reply" in out and "DRY RUN — nothing was written" in out
    assert "Row 2 (lead 1): 13 characters" in out


def test_a_real_run_writes_the_recovered_text(monkeypatch, capsys):
    sheets = StubSheets(LEADS, _secret_rows())
    out = _run(monkeypatch, capsys, sheets, Fetcher({"s1@x.com": {"<secret-id@private.example>": _parsed_msg("Private words")}}), dry_run=False)
    assert sheets.writes == [(2, "Private words", "Private words", None)]
    assert "Wrote the text for 1 reply; read the intent again for 0." in out


def test_a_real_run_with_a_key_also_reads_the_intent_again(monkeypatch, capsys):
    monkeypatch.setattr(outreach, "classify_reply_intent", lambda body, key: {"intent": "Interested", "confidence": "High"})
    sheets = StubSheets(LEADS, _secret_rows())
    out = _run(monkeypatch, capsys, sheets, Fetcher({"s1@x.com": {"<secret-id@private.example>": _parsed_msg("hi")}}), dry_run=False, api_key="K")
    assert sheets.writes[0][3]["Intent"] == "Interested" and "read the intent again for 1" in out


def test_a_dry_run_makes_no_ai_call_and_tells_you_what_a_real_run_would_do(monkeypatch, capsys):
    monkeypatch.setattr(outreach, "classify_reply_intent", lambda *a: pytest.fail("a dry run must not call the AI"))
    out = _run(monkeypatch, capsys, StubSheets(LEADS, _secret_rows()),
               Fetcher({"s1@x.com": {"<secret-id@private.example>": _parsed_msg("hi")}}), dry_run=True, api_key="K")
    assert "1 would also have their intent read again" in out


def test_the_output_never_contains_a_name_an_address_a_subject_or_any_message_text(monkeypatch, capsys):
    """This repository's Actions logs are public."""
    for dry_run in (True, False):
        out = _run(monkeypatch, capsys, StubSheets(LEADS, _secret_rows()),
                   Fetcher({"s1@x.com": {"<secret-id@private.example>": _parsed_msg("Private words")}}), dry_run=dry_run)
        for private in ("Private Person", "private.person", "secret.example", "a private subject", "Private words", "secret-id"):
            assert private not in out, private


def test_the_reasons_for_rows_that_could_not_be_filled_in_are_printed(monkeypatch, capsys):
    rows = [_response("<img@x>"), _response("<gone@x>"), {**_response(""), "MessageID": ""}]
    out = _run(monkeypatch, capsys, StubSheets(LEADS, rows), Fetcher({"s1@x.com": {"<img@x>": _parsed_msg("")}}, broken={"s2@x.com"}), dry_run=True)
    assert "3 could not be filled in" in out
    assert "no readable text (probably only an image or attachment)" in out
    assert "not found in any mailbox that could be checked (1 mailbox(es) could not be reached: sales2 - " in out
    assert "no Message-ID was logged for it" in out


def test_running_the_repair_twice_changes_nothing_the_second_time(monkeypatch, capsys):
    sheets = StubSheets(LEADS, _secret_rows())
    fetch = Fetcher({"s1@x.com": {"<secret-id@private.example>": _parsed_msg("Private words")}})
    _run(monkeypatch, capsys, sheets, fetch, dry_run=False)
    assert len(sheets.writes) == 1
    out = _run(monkeypatch, capsys, sheets, fetch, dry_run=False)
    assert len(sheets.writes) == 1 and "Wrote the text for 0 replies" in out
