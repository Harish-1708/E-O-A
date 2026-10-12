"""The emails WE sent that the system did not send by itself — typed by hand in the mailbox, or sent from
the control panel — stored in their own tab so the conversation view can show the whole conversation."""
import json
import os
import sys
from datetime import datetime, timedelta
from email.message import Message

import gspread
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import outreach  # noqa: E402

FMT = outreach.DATETIME_FMT
NOW = datetime(2026, 9, 8, 12, 0, 0)


# ================================================================ a tiny fake Google Sheet

class FakeTab:
    def __init__(self, header=None, rows=None):
        self.header, self.rows, self.requests, self.appended_with = list(header or []), [list(r) for r in (rows or [])], 0, []
        self.col_count = 40

    def row_values(self, n):
        self.requests += 1
        return list(self.header)

    def col_values(self, n):
        self.requests += 1
        return [self.header[n - 1]] + [r[n - 1] for r in self.rows if len(r) >= n]

    def append_row(self, values, **kw):
        if not self.header:
            self.header = list(values)
        else:
            self.rows.append(list(values))

    def append_rows(self, values, **kw):
        self.appended_with.append(kw)
        self.rows.extend(list(v) for v in values)

    def get_all_records(self):
        self.requests += 1
        return [dict(zip(self.header, r)) for r in self.rows]

    def resize(self, **kw):
        pass

    def update_cell(self, *a):
        pass


class FakeBook:
    def __init__(self, tabs=None):
        self.tabs, self.created = dict(tabs or {}), []

    def worksheet(self, title):
        if title not in self.tabs:
            raise gspread.exceptions.WorksheetNotFound(title)
        return self.tabs[title]

    def add_worksheet(self, title, rows, cols):
        self.created.append(title)
        self.tabs[title] = FakeTab()
        return self.tabs[title]


def _connector(book, tab="Camp Sent Mail"):
    c = outreach.SheetsConnector.__new__(outreach.SheetsConnector)
    c._spreadsheet, c._gspread, c._known_tabs = book, gspread, dict(book.tabs)
    c._sent_mail_tab = tab
    existing = book.tabs.get(tab) if tab else None
    c._sent_mail_ws = outreach._RetryingWorksheet(existing) if existing is not None else None
    return c


def _row(mid, lead="1", **extra):
    base = {"MessageID": mid, "LeadID": lead, "SentAt": "2026-09-04 16:52:00", "SenderAccount": "sales1", "To": "a@x.com",
            "Cc": "", "Subject": "Re: Hi", "Body": "text", "InReplyTo": "", "Source": "mailbox"}
    base.update(extra)
    return base


# ================================================================ the tab: lazy, raw, idempotent

def test_a_campaign_without_the_tab_configured_never_touches_it():
    book = FakeBook()
    c = _connector(book, tab=None)
    assert c.sent_mail_enabled is False
    assert c.get_sent_mail_ids() == set() and c.append_sent_mail_rows([_row("<a@x>")]) == 0
    assert book.created == []


def test_reading_before_the_tab_exists_makes_no_request_and_creates_nothing():
    book = FakeBook()
    assert _connector(book).get_sent_mail_ids() == set()
    assert book.created == []


def test_the_first_write_creates_the_tab_with_the_right_header_then_it_is_reused():
    book = FakeBook()
    c = _connector(book)
    assert c.append_sent_mail_rows([_row("<a@x>"), _row("<b@x>", lead="2")]) == 2
    assert book.created == ["Camp Sent Mail"]
    tab = book.tabs["Camp Sent Mail"]
    assert tab.header == outreach.SENT_MAIL_COLUMNS
    assert [r[0] for r in tab.rows] == ["<a@x>", "<b@x>"] and [r[1] for r in tab.rows] == ["1", "2"]
    c.append_sent_mail_rows([_row("<c@x>")])
    assert book.created == ["Camp Sent Mail"] and len(tab.rows) == 3                  # not created twice


def test_email_text_is_written_raw_never_as_a_formula():
    book = FakeBook()
    _connector(book).append_sent_mail_rows([_row("<a@x>", Body='=HYPERLINK("http://evil","x")')])
    assert book.tabs["Camp Sent Mail"].appended_with == [{"value_input_option": "RAW"}]


def test_an_existing_tab_is_found_without_another_lookup_and_its_ids_are_read():
    tab = FakeTab(outreach.SENT_MAIL_COLUMNS, [["<a@x>"] + [""] * 9, ["<b@x>"] + [""] * 9, [""] + [""] * 9])
    c = _connector(FakeBook({"Camp Sent Mail": tab}))
    assert c.get_sent_mail_ids() == {"<a@x>", "<b@x>"}                                # header skipped, blanks ignored


def test_nothing_is_written_when_there_is_nothing_to_write():
    book = FakeBook()
    assert _connector(book).append_sent_mail_rows([]) == 0 and book.created == []


# ================================================================ finding what to store

def _imap_message(mid, to, subject, date, body="Hello", in_reply_to="", references="", cc=""):
    return {"message_id": mid, "in_reply_to": in_reply_to, "references": references, "subject": subject,
            "from": "Harish <harish@kelson.agency>", "headers": {"to": to, "cc": cc, "date": ""}, "body": body,
            "snippet": body[:500], "date": date}


class FakeSent:
    """Stands in for imap_fetch_sent: applies the caller's `accept` test to each message's headers —
    exactly as the real one does before downloading — and records what would have been downloaded."""
    def __init__(self, by_address, broken=()):
        self.by_address, self.broken, self.downloaded, self.since = by_address, set(broken), [], []

    def __call__(self, address, password, since_dt, accept, **kw):
        self.since.append(since_dt)
        if address in self.broken:
            raise RuntimeError("could not find this mailbox's Sent folder")
        out = []
        for m in self.by_address.get(address, []):
            header = Message()
            header["Message-ID"], header["To"], header["Cc"] = m["message_id"], m["headers"]["to"], m["headers"]["cc"]
            header["In-Reply-To"], header["References"] = m["in_reply_to"], m["references"]
            if accept(header):
                self.downloaded.append(m["message_id"])
                out.append(m)
        return out


class StubSheets:
    sent_mail_enabled = True

    def __init__(self, leads, responses=(), send_log=(), stored=()):
        self.leads, self.responses, self.send_log, self.stored_ids = leads, list(responses), list(send_log), set(stored)
        self.appended = []

    def get_all_leads(self):
        return [dict(l) for l in self.leads]

    def get_all_responses(self):
        return [dict(r) for r in self.responses]

    def get_all_send_log(self):
        return [dict(r) for r in self.send_log]

    def get_sent_mail_ids(self):
        return set(self.stored_ids)

    def append_sent_mail_rows(self, rows):
        self.appended.extend(rows)
        self.stored_ids |= {r["MessageID"] for r in rows}
        return len(rows)


ACCTS = {"sales1": {"address": "s1@x.com", "app_password": "p"}, "sales2": {"address": "s2@x.com", "app_password": "p"}}
LEADS = [{"LeadID": "1", "Email": "andrea@creator.com", "SenderAccount": "sales1", "MessageID": "<intro@kelson>",
          "ThreadReferences": "<intro@kelson>", "ThreadSubject": "Meta Ad Licensing Collaboration"},
         {"LeadID": "2", "Email": "other@creator.com", "SenderAccount": "sales2", "MessageID": "<intro2@kelson>",
          "ThreadReferences": "", "ThreadSubject": "A different campaign subject"}]
INBOUND = [{"LeadID": "1", "MessageID": "<her1@icloud>"}]


@pytest.fixture(autouse=True)
def _accounts(monkeypatch):
    monkeypatch.setattr(outreach, "imap_connection_settings", lambda account: ("imap.x", 993, account["address"]))
    monkeypatch.setattr(outreach, "get_imap_password", lambda account: account["app_password"])


def _find(sheets, sent, hours=24, accounts=ACCTS):
    return outreach.find_sent_mail_to_log(sheets, accounts, hours, fetch_sent=sent, now=NOW)


def test_a_message_you_typed_in_reply_to_her_reply_is_found_by_its_threading_headers():
    sent = FakeSent({"s1@x.com": [_imap_message("<mine1@gmail>", "Andrea <andrea@creator.com>", "Re: Meta Ad Licensing Collaboration",
                                                datetime(2026, 9, 4, 16, 52, 30), body="We can do $200.", in_reply_to="<her1@icloud>")]})
    (row,) = _find(StubSheets(LEADS, INBOUND), sent)["rows"]
    assert row == {"MessageID": "<mine1@gmail>", "LeadID": "1", "SentAt": "2026-09-04 16:52:30", "SenderAccount": "sales1",
                   "To": "Andrea <andrea@creator.com>", "Cc": "", "Subject": "Re: Meta Ad Licensing Collaboration",
                   "Body": "We can do $200.", "InReplyTo": "<her1@icloud>", "Source": "mailbox"}


def test_a_chain_is_followed_each_message_recognised_by_the_one_before_it_whatever_order_they_arrive_in():
    first = _imap_message("<mine1@gmail>", "andrea@creator.com", "Re: x", datetime(2026, 9, 4, 17, 0), in_reply_to="<her1@icloud>")
    second = _imap_message("<mine2@gmail>", "andrea@creator.com", "Re: y", datetime(2026, 9, 4, 17, 30), in_reply_to="<mine1@gmail>")
    third = _imap_message("<mine3@gmail>", "andrea@creator.com", "Re: z", datetime(2026, 9, 4, 18, 0), references="<intro@kelson> <mine2@gmail>")
    sent = FakeSent({"s1@x.com": [third, first, second]})                 # the mailbox returns them scrambled
    rows = _find(StubSheets(LEADS, INBOUND), sent)["rows"]
    assert [r["MessageID"] for r in rows] == ["<mine1@gmail>", "<mine2@gmail>", "<mine3@gmail>"]     # oldest first
    assert {r["LeadID"] for r in rows} == {"1"}


def test_with_no_threading_headers_a_reply_to_her_with_the_threads_subject_is_still_found():
    """Some mail apps drop In-Reply-To / References entirely."""
    msg = _imap_message("<mine@gmail>", "Andrea <ANDREA@creator.com>", "Re: Re: Meta Ad Licensing Collaboration", datetime(2026, 9, 4, 17, 0))
    assert [r["LeadID"] for r in _find(StubSheets(LEADS, INBOUND), FakeSent({"s1@x.com": [msg]}))["rows"]] == ["1"]


def test_your_unrelated_mail_to_the_same_person_is_ignored_and_never_downloaded():
    unrelated = _imap_message("<other@gmail>", "andrea@creator.com", "Lunch on Friday?", datetime(2026, 9, 4, 17, 0))
    stranger = _imap_message("<s@gmail>", "stranger@else.com", "Re: Meta Ad Licensing Collaboration", datetime(2026, 9, 4, 17, 5))
    sent = FakeSent({"s1@x.com": [unrelated, stranger]})
    result = _find(StubSheets(LEADS, INBOUND), sent)
    assert result["rows"] == []
    assert "<s@gmail>" not in sent.downloaded                           # a stranger's mail is never even fetched in full


def test_a_reply_that_only_looks_like_the_subject_but_is_to_someone_else_is_not_attributed_to_the_lead():
    msg = _imap_message("<m@gmail>", "other@creator.com", "Re: Meta Ad Licensing Collaboration", datetime(2026, 9, 4, 17, 0))
    assert _find(StubSheets(LEADS, INBOUND), FakeSent({"s2@x.com": [msg]}))["rows"] == []      # lead 2's subject is a different one


def test_a_lead_with_no_thread_subject_is_never_matched_by_subject_alone():
    leads = [{**LEADS[0], "ThreadSubject": ""}]
    msg = _imap_message("<m@gmail>", "andrea@creator.com", "Re: ", datetime(2026, 9, 4, 17, 0))
    assert _find(StubSheets(leads, INBOUND), FakeSent({"s1@x.com": [msg]}))["rows"] == []


def test_what_the_system_sent_by_itself_is_skipped_but_a_control_panel_reply_whose_copy_was_lost_is_not():
    # a real automated follow-up threads under the intro, so it WOULD match the lead if it were not recognised as ours
    auto = _imap_message("<auto@kelson>", "andrea@creator.com", "Re: Meta Ad Licensing Collaboration", datetime(2026, 9, 6, 14, 36),
                         in_reply_to="<intro@kelson>", references="<intro@kelson>")
    panel = _imap_message("<panel@kelson>", "andrea@creator.com", "Re: Meta Ad Licensing Collaboration", datetime(2026, 9, 4, 17, 0), in_reply_to="<her1@icloud>")
    log = [{"LeadID": "1", "MessageID": "<auto@kelson>", "Stage": "intro"}, {"LeadID": "1", "MessageID": "<panel@kelson>", "Stage": "manual_reply"}]
    rows = _find(StubSheets(LEADS, INBOUND, send_log=log), FakeSent({"s1@x.com": [auto, panel]}))["rows"]
    assert [r["MessageID"] for r in rows] == ["<panel@kelson>"]


def test_a_control_panel_reply_whose_copy_was_stored_is_not_stored_twice():
    panel = _imap_message("<panel@kelson>", "andrea@creator.com", "Re: x", datetime(2026, 9, 4, 17, 0), in_reply_to="<her1@icloud>")
    log = [{"LeadID": "1", "MessageID": "<panel@kelson>", "Stage": "manual_reply"}]
    assert _find(StubSheets(LEADS, INBOUND, send_log=log, stored={"<panel@kelson>"}), FakeSent({"s1@x.com": [panel]}))["rows"] == []


def test_running_it_again_finds_nothing_new():
    msg = _imap_message("<mine@gmail>", "andrea@creator.com", "Re: x", datetime(2026, 9, 4, 17, 0), in_reply_to="<her1@icloud>")
    sheets, sent = StubSheets(LEADS, INBOUND), FakeSent({"s1@x.com": [msg]})
    assert len(outreach.sync_sent_mail(sheets, ACCTS, 24, fetch_sent=sent, now=NOW)["rows"]) == 1
    assert outreach.sync_sent_mail(sheets, ACCTS, 24, fetch_sent=sent, now=NOW)["rows"] == [] and len(sheets.appended) == 1


def test_only_the_mailboxes_the_campaign_actually_sent_from_are_read():
    leads = [LEADS[0]]
    sent = FakeSent({})
    _find(StubSheets(leads, INBOUND), sent)
    assert len(sent.since) == 1                                          # sales1 only — sales2 never sent for this campaign


def test_if_no_lead_names_a_sender_every_mailbox_is_read():
    sent = FakeSent({})
    _find(StubSheets([{**LEADS[0], "SenderAccount": ""}], INBOUND), sent)
    assert len(sent.since) == 2


def test_an_unreadable_mailbox_is_reported_and_the_others_still_work():
    msg = _imap_message("<m2@gmail>", "other@creator.com", "Re: A different campaign subject", datetime(2026, 9, 4, 17, 0))
    result = _find(StubSheets(LEADS, INBOUND), FakeSent({"s2@x.com": [msg]}, broken={"s1@x.com"}))
    assert [r["LeadID"] for r in result["rows"]] == ["2"]
    assert len(result["account_errors"]) == 1 and result["account_errors"][0].startswith("sales1: ")


def test_the_look_back_window_is_passed_to_the_mailbox():
    sent = FakeSent({})
    _find(StubSheets(LEADS, INBOUND), sent, hours=72)
    assert sent.since == [NOW - timedelta(hours=72)] * len(sent.since)


def test_a_campaign_with_no_leads_reads_no_mailbox_at_all():
    sent = FakeSent({})
    assert _find(StubSheets([], []), sent) == {"rows": [], "account_errors": []} and sent.since == []


def test_a_message_without_a_date_is_stamped_with_the_time_it_was_found_and_a_huge_body_is_capped():
    msg = _imap_message("<m@gmail>", "andrea@creator.com", "Re: x", None, body="y" * 60_000, in_reply_to="<her1@icloud>")
    (row,) = _find(StubSheets(LEADS, INBOUND), FakeSent({"s1@x.com": [msg]}))["rows"]
    assert row["SentAt"] == NOW.strftime(FMT) and len(row["Body"]) == 49000


def test_a_cc_to_the_lead_is_enough_for_the_subject_fallback():
    msg = _imap_message("<m@gmail>", "someone@else.com", "Re: Meta Ad Licensing Collaboration", datetime(2026, 9, 4, 17, 0), cc="andrea@creator.com")
    assert [r["LeadID"] for r in _find(StubSheets(LEADS, INBOUND), FakeSent({"s1@x.com": [msg]}))["rows"]] == ["1"]


# ================================================================ storing it, and staying out of the way

def test_sync_stores_what_it_found_in_one_batch_and_nothing_when_there_is_nothing():
    sheets = StubSheets(LEADS, INBOUND)
    outreach.sync_sent_mail(sheets, ACCTS, 24, fetch_sent=FakeSent({}), now=NOW)
    assert sheets.appended == []


def test_the_scheduled_check_never_fails_because_of_the_sent_folder(monkeypatch, capsys):
    monkeypatch.setattr(outreach, "sync_sent_mail", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Sent folder exploded")))
    outreach._sync_sent_mail_quietly(StubSheets(LEADS), ACCTS, 24, "Camp")
    out = capsys.readouterr().out
    assert "reading Sent folders failed" in out and "replies were not affected" in out


def test_it_does_nothing_at_all_for_a_campaign_that_has_no_sent_mail_tab(monkeypatch, capsys):
    class Disabled(StubSheets):
        sent_mail_enabled = False
    monkeypatch.setattr(outreach, "sync_sent_mail", lambda *a, **k: pytest.fail("must not read any mailbox"))
    outreach._sync_sent_mail_quietly(Disabled(LEADS), ACCTS, 24, "Camp")
    outreach._sync_sent_mail_quietly(object(), ACCTS, 24, "Camp")                    # a sheet object that has never heard of it
    assert capsys.readouterr().out == ""


def test_the_quiet_sync_reports_counts_only_never_a_name_address_subject_or_text(monkeypatch, capsys):
    secret = _imap_message("<secret-id@private>", "andrea@creator.com", "Re: a private subject", datetime(2026, 9, 4, 17, 0),
                           body="private words", in_reply_to="<her1@icloud>")
    monkeypatch.setattr(outreach, "imap_fetch_sent", FakeSent({"s1@x.com": [secret], "s2@x.com": []}))
    outreach._sync_sent_mail_quietly(StubSheets(LEADS, INBOUND), ACCTS, 24, "Camp")
    out = capsys.readouterr().out
    assert "stored 1 message(s) you sent yourself" in out
    for private in ("andrea", "creator.com", "private subject", "private words", "secret-id"):
        assert private not in out


def test_the_quiet_sync_always_looks_back_at_least_a_day(monkeypatch):
    seen = []
    monkeypatch.setattr(outreach, "sync_sent_mail", lambda sheets, accounts, hours, **k: seen.append(hours) or {"rows": [], "account_errors": []})
    outreach._sync_sent_mail_quietly(StubSheets(LEADS), ACCTS, 6, "Camp")
    outreach._sync_sent_mail_quietly(StubSheets(LEADS), ACCTS, 72, "Camp")
    assert seen == [24, 72]


def _check_replies_command(monkeypatch, sheets, sync):
    monkeypatch.setattr(outreach, "get_campaign", lambda name, **kw: {})
    monkeypatch.setattr(outreach, "_connect_sheets", lambda cfg: sheets)
    monkeypatch.setattr(outreach, "load_email_accounts", lambda: ACCTS)
    monkeypatch.setattr(outreach, "check_replies", lambda *a, **k: [])
    monkeypatch.setattr(outreach, "sync_sent_mail", sync)
    outreach.cmd_check_replies(type("Args", (), {"campaign": "C"})())


def test_check_replies_looks_for_sent_mail_afterwards_and_a_failure_there_changes_nothing_about_it(monkeypatch, capsys):
    calls = []
    _check_replies_command(monkeypatch, StubSheets(LEADS), lambda *a, **k: calls.append(1) or {"rows": [], "account_errors": []})
    assert calls == [1] and "No new inbound messages matched to a lead." in capsys.readouterr().out
    _check_replies_command(monkeypatch, StubSheets(LEADS), lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    out = capsys.readouterr().out
    assert "replies were not affected" in out and "No new inbound messages matched to a lead." in out


# ================================================================ a reply sent from the control panel

class SendStub:
    sent_mail_enabled = True

    def __init__(self, fail_store=False, enabled=True):
        self.send_log, self.errors, self.stored, self.fail_store = [], [], [], fail_store
        self.sent_mail_enabled = enabled

    def append_send_log(self, row):
        self.send_log.append(row)

    def append_error_log(self, row):
        self.errors.append(row)

    def append_sent_mail_rows(self, rows):
        if self.fail_store:
            raise RuntimeError("Sheets said no")
        self.stored.extend(rows)
        return len(rows)


def _send_reply(monkeypatch, tmp_path, sheets, send=None, payload=None):
    payload = payload or {"sender_account": "sales1", "to": "andrea@creator.com", "subject": "Re: x", "body": "Thanks Andrea!",
                          "in_reply_to": "<her1@icloud>", "references": "<intro@kelson> <her1@icloud>", "cc": ["boss@x.com", "c@x.com"],
                          "lead_id": "1"}
    path = tmp_path / "p.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(outreach, "get_campaign", lambda name, **kw: {})
    monkeypatch.setattr(outreach, "_connect_sheets", lambda cfg: sheets)
    monkeypatch.setattr(outreach, "load_email_accounts", lambda: ACCTS)
    monkeypatch.setattr(outreach, "send_manual_reply", send or (lambda *a, **k: {"message_id": "<new@kelson>"}))
    outreach.cmd_send_reply(type("Args", (), {"campaign": "Camp", "file": str(path)})())


def test_a_reply_sent_from_the_control_panel_is_kept_so_the_conversation_can_show_it(monkeypatch, tmp_path):
    sheets = SendStub()
    _send_reply(monkeypatch, tmp_path, sheets)
    (row,) = sheets.stored
    assert {k: row[k] for k in ("MessageID", "LeadID", "SenderAccount", "To", "Cc", "Subject", "Body", "InReplyTo", "Source")} == {
        "MessageID": "<new@kelson>", "LeadID": "1", "SenderAccount": "sales1", "To": "andrea@creator.com", "Cc": "boss@x.com, c@x.com",
        "Subject": "Re: x", "Body": "Thanks Andrea!", "InReplyTo": "<her1@icloud>", "Source": "app"}
    assert datetime.strptime(row["SentAt"], FMT)
    assert sheets.send_log[0]["Stage"] == "manual_reply" and sheets.send_log[0]["Status"] == "sent"        # the Send Log entry is unchanged


def test_failing_to_keep_a_copy_never_makes_a_sent_email_look_like_it_failed(monkeypatch, tmp_path, capsys):
    sheets = SendStub(fail_store=True)
    _send_reply(monkeypatch, tmp_path, sheets)                        # must not raise
    assert "Sent. Message-ID: <new@kelson>" in capsys.readouterr().out
    assert sheets.send_log[0]["Status"] == "sent" and len(sheets.errors) == 1


def test_a_reply_that_fails_to_send_is_not_stored_as_sent(monkeypatch, tmp_path):
    sheets = SendStub()
    with pytest.raises(SystemExit):
        _send_reply(monkeypatch, tmp_path, sheets, send=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("SMTP refused")))
    assert sheets.stored == [] and sheets.send_log[0]["Status"] == "error"


def test_nothing_is_stored_for_a_campaign_without_the_tab(monkeypatch, tmp_path):
    sheets = SendStub(enabled=False)
    _send_reply(monkeypatch, tmp_path, sheets)
    assert sheets.stored == []


# ================================================================ the manual command

def _sync_command(monkeypatch, capsys, sheets, sent, dry_run, days=7):
    monkeypatch.setattr(outreach, "get_campaign", lambda name, **kw: {})
    monkeypatch.setattr(outreach, "_connect_sheets", lambda cfg: sheets)
    monkeypatch.setattr(outreach, "load_email_accounts", lambda: ACCTS)
    monkeypatch.setattr(outreach, "imap_fetch_sent", sent)
    outreach.cmd_sync_sent_mail(type("Args", (), {"campaign": "C", "days": days, "dry_run": dry_run})())
    return capsys.readouterr().out


def _private_message():
    return _imap_message("<secret-id@private>", "andrea@creator.com", "Re: a private subject", datetime(2026, 9, 4, 17, 0),
                         body="private words", in_reply_to="<her1@icloud>")


def test_a_dry_run_stores_nothing_and_says_so(monkeypatch, capsys):
    sheets = StubSheets(LEADS, INBOUND)
    out = _sync_command(monkeypatch, capsys, sheets, FakeSent({"s1@x.com": [_private_message()]}), dry_run=True)
    assert sheets.appended == [] and "Would store 1 message(s) you sent yourself, across 1 lead(s), from the last 7 day(s)." in out
    assert "Lead 1: 1 message(s)" in out and "DRY RUN — nothing was written" in out


def test_a_real_run_stores_it(monkeypatch, capsys):
    sheets = StubSheets(LEADS, INBOUND)
    out = _sync_command(monkeypatch, capsys, sheets, FakeSent({"s1@x.com": [_private_message()]}), dry_run=False)
    assert [r["MessageID"] for r in sheets.appended] == ["<secret-id@private>"] and "Stored 1 message(s)." in out


def test_the_command_output_never_contains_a_name_address_subject_or_text(monkeypatch, capsys):
    for dry_run in (True, False):
        out = _sync_command(monkeypatch, capsys, StubSheets(LEADS, INBOUND), FakeSent({"s1@x.com": [_private_message()]}), dry_run=dry_run)
        for private in ("andrea", "creator.com", "private subject", "private words", "secret-id"):
            assert private not in out, private


def test_the_days_option_becomes_the_look_back_window(monkeypatch, capsys):
    sent = FakeSent({})
    monkeypatch.setattr(outreach, "datetime", type("D", (datetime,), {"now": classmethod(lambda cls, tz=None: NOW)}))
    _sync_command(monkeypatch, capsys, StubSheets(LEADS, INBOUND), sent, dry_run=True, days=30)
    assert sent.since and set(sent.since) == {NOW - timedelta(days=30)}                # every mailbox read, each over the full 30 days


def test_unreadable_mailboxes_are_listed_by_name_not_by_account_secret(monkeypatch, capsys):
    out = _sync_command(monkeypatch, capsys, StubSheets(LEADS, INBOUND), FakeSent({}, broken={"s1@x.com"}), dry_run=True)
    assert "1 mailbox(es) could not be read:" in out and "sales1:" in out and "s1@x.com" not in out


def test_the_sent_mail_tab_has_a_default_name_like_every_other_tab():
    cfg = outreach.get_campaign("Kelson_Creators_Licensing")
    assert cfg["sent_mail_tab"] == "Kelson_Creators_Licensing Sent Mail"
    assert cfg["error_log_tab"] == "Kelson_Creators_Licensing Error Log"           # the pattern it follows


def test_an_explicit_tab_name_in_a_campaign_file_wins(monkeypatch, tmp_path):
    import yaml
    original = outreach.get_campaign("Kelson_Creators_Licensing")
    assert outreach.SENT_MAIL_COLUMNS and original["sent_mail_tab"]
    custom = dict(original, sent_mail_tab="My Own Name")
    seen = {}
    monkeypatch.setattr(outreach, "SheetsConnector", lambda **kw: seen.update(kw) or object())
    outreach._connect_sheets(custom)
    assert seen["sent_mail_tab"] == "My Own Name"
    outreach._connect_sheets(original)
    assert seen["sent_mail_tab"] == "Kelson_Creators_Licensing Sent Mail"
    assert yaml                                                                        # (keeps the import honest)


# ================================================================ the REAL constructor stays lazy

def _book_with_the_five_usual_tabs(extra=None):
    names = {"Camp Master Sheet": outreach.MASTER_COLUMNS, "Camp Response Sheet": outreach.RESPONSES_COLUMNS,
             "Camp Custom Log Sheet": outreach.SEND_LOG_COLUMNS, "Camp Error Log": outreach.ERROR_LOG_COLUMNS,
             "Camp Dashboard": outreach.DASHBOARD_COLUMNS}
    tabs = {name: FakeTab(header) for name, header in names.items()}
    tabs.update(extra or {})
    return FakeBook(tabs)


def _construct(monkeypatch, book, **kwargs):
    monkeypatch.setattr(outreach, "_build_gspread_client", lambda: (object(), gspread))
    monkeypatch.setattr(outreach, "_open_spreadsheet_with_retries", lambda client, sheet_id: book)
    monkeypatch.setattr(outreach, "_list_worksheets_by_title", lambda spreadsheet: dict(book.tabs))
    return outreach.SheetsConnector("sheet", "Camp Master Sheet", "Camp Response Sheet", "Camp Custom Log Sheet",
                                    "Camp Error Log", "Camp Dashboard", **kwargs)


def test_connecting_never_creates_the_sent_mail_tab(monkeypatch):
    """Connecting happens on every run for every campaign; an eager tab would appear in every campaign's Sheet."""
    book = _book_with_the_five_usual_tabs()
    connector = _construct(monkeypatch, book, sent_mail_tab="Camp Sent Mail")
    assert book.created == [] and "Camp Sent Mail" not in book.tabs
    assert connector.sent_mail_enabled and connector.get_sent_mail_ids() == set()


def test_connecting_finds_an_existing_sent_mail_tab_without_another_lookup(monkeypatch):
    tab = FakeTab(outreach.SENT_MAIL_COLUMNS, [["<a@x>"] + [""] * 9])
    book = _book_with_the_five_usual_tabs({"Camp Sent Mail": tab})
    connector = _construct(monkeypatch, book, sent_mail_tab="Camp Sent Mail")
    assert book.created == [] and connector.get_sent_mail_ids() == {"<a@x>"}


def test_connecting_the_old_way_without_a_sent_mail_tab_still_works_and_the_feature_is_off(monkeypatch):
    connector = _construct(monkeypatch, _book_with_the_five_usual_tabs())
    assert connector.sent_mail_enabled is False


def test_the_first_real_write_after_connecting_creates_the_tab_once(monkeypatch):
    book = _book_with_the_five_usual_tabs()
    connector = _construct(monkeypatch, book, sent_mail_tab="Camp Sent Mail")
    connector.append_sent_mail_rows([_row("<a@x>")])
    connector.append_sent_mail_rows([_row("<b@x>")])
    assert book.created == ["Camp Sent Mail"] and len(book.tabs["Camp Sent Mail"].rows) == 2


# ================================================================ the SCHEDULED path (what actually runs every hour)

def _check_replies_all(monkeypatch, campaigns, sync, check=None):
    monkeypatch.setattr(outreach, "discover_campaign_names", lambda: list(campaigns))
    monkeypatch.setattr(outreach, "get_campaign", lambda name, **kw: {})
    monkeypatch.setattr(outreach, "_connect_sheets", lambda cfg: StubSheets(LEADS))
    monkeypatch.setattr(outreach, "load_email_accounts", lambda: ACCTS)
    monkeypatch.setattr(outreach, "check_replies", check or (lambda *a, **k: []))
    monkeypatch.setattr(outreach, "_sync_sent_mail_quietly", sync)
    outreach.cmd_check_replies_all(type("Args", (), {})())


def test_the_hourly_check_looks_for_hand_typed_mail_in_every_campaign(monkeypatch, capsys):
    seen = []
    _check_replies_all(monkeypatch, ["Camp_A", "Camp_B"], lambda sheets, accounts, hours, name: seen.append(name))
    assert seen == ["Camp_A", "Camp_B"]
    assert capsys.readouterr().out.count("no new inbound messages matched") == 2


def test_the_sync_runs_after_the_reply_check_not_before_it(monkeypatch):
    order = []
    _check_replies_all(monkeypatch, ["Camp_A"], lambda *a: order.append("sync"), check=lambda *a, **k: order.append("replies") or [])
    assert order == ["replies", "sync"]                                 # replies are always checked first


def test_a_sync_failure_never_stops_the_hourly_check_or_makes_it_report_failure(monkeypatch, capsys):
    """_sync_sent_mail_quietly swallows its own errors; the hourly command must never exit non-zero because of it."""
    monkeypatch.setattr(outreach, "discover_campaign_names", lambda: ["Camp_A", "Camp_B"])
    monkeypatch.setattr(outreach, "get_campaign", lambda name, **kw: {})
    monkeypatch.setattr(outreach, "_connect_sheets", lambda cfg: StubSheets(LEADS))
    monkeypatch.setattr(outreach, "load_email_accounts", lambda: ACCTS)
    monkeypatch.setattr(outreach, "check_replies", lambda *a, **k: [])
    monkeypatch.setattr(outreach, "sync_sent_mail", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Sent folder exploded")))
    outreach.cmd_check_replies_all(type("Args", (), {})())              # SystemExit here would fail the test
    out = capsys.readouterr().out
    assert out.count("replies were not affected") == 2 and out.count("no new inbound messages matched") == 2
