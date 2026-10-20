"""HTML campaigns in the engine: the format setting, safe rendering, the message that is actually sent, and unsubscribe requests. Every test that matters is paired with
the plain-text case beside it, to show plain-text campaigns are untouched."""
import email
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import outreach  # noqa: E402
import html_campaign_logic as hc  # noqa: E402
from test_outreach import ACCOUNTS, FakeSheets, _base_campaign_cfg, make_lead  # noqa: E402
from test_stage_wait_overrides import _campaign  # noqa: E402

FOOTER_YAML = "footer:\n  business_name: Kelson Agency\n  address: 12 Example Street\n"


def _html_campaign(tmp_path, override="format: html\n" + FOOTER_YAML, write_html=True, stages=("intro", "followup1")):
    kw = _campaign(tmp_path, override, stages=stages)
    folder = os.path.join(kw["templates_root"], "camp")
    if write_html:
        for stage in stages:
            with open(os.path.join(folder, f"{stage}_A.html"), "w", encoding="utf-8") as f:
                f.write("<p>Hi {{ FirstName }}</p>{{ footer }}")
    return kw


# ================================================================ the format setting

def test_a_campaign_with_no_format_is_plain_text(tmp_path):
    kw = _campaign(tmp_path, None)
    assert outreach.get_campaign("camp", **kw)["format"] == "text"


def test_an_html_campaign_loads_when_every_email_has_its_html_beside_its_text(tmp_path):
    cfg = outreach.get_campaign("camp", **_html_campaign(tmp_path))
    assert cfg["format"] == "html" and cfg["footer"] == {"business_name": "Kelson Agency", "address": "12 Example Street"}


def test_an_html_campaign_missing_html_files_is_refused_and_every_missing_file_is_named(tmp_path):
    with pytest.raises(outreach.ConfigError) as caught:
        outreach.get_campaign("camp", **_html_campaign(tmp_path, write_html=False))
    assert "missing 2 HTML file(s)" in str(caught.value) and "intro_A.html" in str(caught.value) and "followup1_A.html" in str(caught.value)


def test_one_missing_html_file_is_enough_to_refuse(tmp_path):
    kw = _html_campaign(tmp_path)
    os.remove(os.path.join(kw["templates_root"], "camp", "followup1_A.html"))
    with pytest.raises(outreach.ConfigError, match=r"missing 1 HTML file\(s\)[\s\S]*followup1_A.html"):
        outreach.get_campaign("camp", **kw)


def test_a_plain_text_campaign_does_not_care_about_stray_html_files_or_a_missing_one(tmp_path):
    kw = _campaign(tmp_path, "format: text\n")
    open(os.path.join(kw["templates_root"], "camp", "intro_A.html"), "w").write("<p>stray</p>")
    assert outreach.get_campaign("camp", **kw)["format"] == "text"


@pytest.mark.parametrize("value", ["pdf", "HTML", "Text", "yes", 5])
def test_any_format_other_than_text_or_html_is_refused(tmp_path, value):
    with pytest.raises(outreach.ConfigError, match="format must be 'text' or 'html'"):
        outreach.get_campaign("camp", **_campaign(tmp_path, f"format: {value!r}\n"))


@pytest.mark.parametrize("footer_yaml, message", [("footer: just text\n", "footer must be a mapping"), ("footer:\n  business_name: 5\n", "footer.business_name must be text"),
                                                  ("footer:\n  address: [a, b]\n", "footer.address must be text"), ("footer:\n  opt_out: true\n", "footer.opt_out must be text")])
def test_a_footer_that_is_not_text_is_refused(tmp_path, footer_yaml, message):
    with pytest.raises(outreach.ConfigError, match=message):
        outreach.get_campaign("camp", **_campaign(tmp_path, "format: text\n" + footer_yaml))


def test_the_html_files_can_be_checked_against_a_file_list_too(tmp_path):
    kw = _html_campaign(tmp_path)
    names = ["intro_A.txt", "followup1_A.txt", "intro_A.html", "followup1_A.html"]
    assert outreach.get_campaign("camp", template_filenames=names, **kw)["format"] == "html"
    with pytest.raises(outreach.ConfigError, match=r"missing 1 HTML file\(s\)[\s\S]*followup1_A.html"):
        outreach.get_campaign("camp", template_filenames=names[:3], **kw)


def test_the_html_files_do_not_change_which_stages_and_variants_are_found(tmp_path):
    kw = _html_campaign(tmp_path, stages=("intro", "followup1", "followup2"))
    assert [s["name"] for s in outreach.get_campaign("camp", **kw)["stages"]] == ["intro", "followup1", "followup2"]


# ================================================================ rendering

LEAD = {"FirstName": "Ann", "Company": "Acme", "Email": "a@x.com", "Region": "EU"}      # CompanyName in a template reads the Sheet's Company column


def test_plain_text_rendering_is_exactly_what_it_always_was():
    assert outreach.render_text("Hi {{ FirstName }} at {{ Region }}", LEAD) == "Hi Ann at EU"
    assert outreach.render_text("<b>{{ FirstName }}</b> & {{ nope }}", LEAD, missing_out=(m := [])) == "<b>Ann</b> & " and m == ["nope"]


def test_escaping_applies_to_values_from_the_lead_and_never_to_the_template_itself():
    lead = dict(LEAD, FirstName='<script>alert("x")</script>', Region="Tom & Jerry")
    out = outreach.render_text("<p>{{ FirstName }} / {{ Region }}</p>", lead, escape=outreach.html_lib.escape)
    assert out == "<p>&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; / Tom &amp; Jerry</p>"


def test_the_default_for_an_empty_value_is_escaped_too():
    out = outreach.render_text("{{ FirstName }}", dict(LEAD, FirstName=""), escape=lambda v: f"[{v}]")
    assert out == f"[{outreach.DEFAULT_VALUES['FirstName']}]" == "[there]"


def test_reserved_names_are_inserted_as_they_are_and_beat_a_lead_column_of_the_same_name():
    out = outreach.render_text("{{ footer }}|{{ FirstName }}", dict(LEAD, footer="from the sheet"), escape=outreach.html_lib.escape, reserved={"footer": "<div>F</div>"})
    assert out == "<div>F</div>|Ann"


def test_without_reserved_names_a_footer_placeholder_is_just_an_unknown_variable():
    missing = []
    assert outreach.render_text("{{ footer }}", LEAD, missing_out=missing) == "" and missing == ["footer"]


def _write(tmp_path, name, text):
    (tmp_path / name).write_text(text, encoding="utf-8")


CFG_WITH_FOOTER = {"footer": {"business_name": "Kelson Agency", "address": "12 Example Street", "opt_out": 'Reply "unsubscribe".'}}


def test_the_html_version_is_filled_in_with_escaped_values_and_the_footer(tmp_path):
    _write(tmp_path, "intro_A.html", '<p>Hi {{ FirstName }}, from {{ CompanyName }}</p><a href="https://x.com/?c={{ CompanyName }}">go</a>{{ footer }}')
    out = outreach.render_email_html(str(tmp_path), "intro", "A", dict(LEAD, FirstName="<b>Ann</b>", Company='Ac"me'), CFG_WITH_FOOTER)
    assert "Hi &lt;b&gt;Ann&lt;/b&gt;, from Ac&quot;me" in out["html"] and 'href="https://x.com/?c=Ac&quot;me"' in out["html"]
    assert "Kelson Agency" in out["html"] and "12 Example Street" in out["html"] and "{{" not in out["html"] and out["missing_variables"] == []


def test_a_name_full_of_symbols_can_never_inject_markup_into_the_design(tmp_path):
    _write(tmp_path, "intro_A.html", "<p>Hi {{ FirstName }}</p>{{ footer }}")
    for hostile in ['<script>alert(1)</script>', '"><img src=x onerror=alert(1)>', "<a href='javascript:x'>", "</p><iframe src=x>"]:
        out = outreach.render_email_html(str(tmp_path), "intro", "A", dict(LEAD, FirstName=hostile), CFG_WITH_FOOTER)["html"]
        assert hc.unsafe_problems(out) == [], hostile


def test_html_that_is_not_safe_is_never_rendered(tmp_path):
    for bad in ["<script>x</script>", '<p onclick="x()">a</p>', "<form></form>", '<a href="javascript:x">a</a>', '<link rel="stylesheet" href="x">']:
        _write(tmp_path, "intro_A.html", bad + "{{ footer }}")
        with pytest.raises(outreach.TemplateError, match="intro_A.html is not safe to send"):
            outreach.render_email_html(str(tmp_path), "intro", "A", LEAD, CFG_WITH_FOOTER)


def test_a_placeholder_that_matches_nothing_is_reported_once_not_sent_blank_silently(tmp_path):
    _write(tmp_path, "intro_A.html", "{{ frist }} {{ frist }} {{ other }} {{ FirstName }}{{ footer }}")
    assert outreach.render_email_html(str(tmp_path), "intro", "A", LEAD, CFG_WITH_FOOTER)["missing_variables"] == ["frist", "other"]


@pytest.mark.parametrize("footer", [None, {}, {"business_name": "K"}, {"address": "A"}])
def test_using_the_footer_with_an_incomplete_footer_is_refused_never_sent_half_empty(tmp_path, footer):
    _write(tmp_path, "intro_A.html", "<p>x</p>{{ footer }}")
    with pytest.raises(outreach.TemplateError, match="footer is incomplete"):
        outreach.render_email_html(str(tmp_path), "intro", "A", LEAD, {"footer": footer} if footer is not None else {})


def test_html_that_does_not_use_the_footer_needs_no_footer_settings(tmp_path):
    _write(tmp_path, "intro_A.html", "<p>Hi {{ FirstName }}. To unsubscribe, reply.</p>")
    assert "Hi Ann" in outreach.render_email_html(str(tmp_path), "intro", "A", LEAD, {})["html"]


def test_a_missing_html_file_is_a_clear_error(tmp_path):
    with pytest.raises(outreach.TemplateError, match="HTML template file not found"):
        outreach.render_email_html(str(tmp_path), "intro", "A", LEAD, CFG_WITH_FOOTER)


def test_the_plain_text_copy_gets_the_text_footer_in_its_subject_and_body(tmp_path):
    _write(tmp_path, "intro_A.txt", "Subject: News for {{ FirstName }}\n\nHi {{ FirstName }}\n\n{{ footer }}\n")
    reserved = outreach.footer_reserved(CFG_WITH_FOOTER, ["{{ footer }}"], as_html=False)
    out = outreach.render_email(str(tmp_path), "intro", "A", LEAD, is_first_stage=True, reserved_text=reserved)
    assert out["subject"] == "News for Ann" and out["body"].rstrip().endswith('--\nKelson Agency · 12 Example Street\nReply "unsubscribe".') and out["missing_variables"] == []


def test_the_text_copy_of_a_plain_text_campaign_still_reports_a_footer_placeholder_as_unknown(tmp_path):
    _write(tmp_path, "intro_A.txt", "Subject: Hi\n\nHello {{ footer }}\n")
    assert outreach.render_email(str(tmp_path), "intro", "A", LEAD, is_first_stage=True)["missing_variables"] == ["footer"]


# ================================================================ building a batch

def _batch_cfg(tmp_path, fmt, html_text="<p>Hi {{ FirstName }}</p>{{ footer }}"):
    cfg = _base_campaign_cfg()
    cfg["templates_dir"] = str(tmp_path)
    cfg["format"] = fmt
    cfg["footer"] = CFG_WITH_FOOTER["footer"]
    _write(tmp_path, "intro_A.txt", "Subject: Hello {{ FirstName }}\n\nHi {{ FirstName }}\n\n{{ footer }}\n" if fmt == "html" else "Subject: Hello {{ FirstName }}\n\nHi {{ FirstName }}\n")
    if html_text is not None:
        _write(tmp_path, "intro_A.html", html_text)
    return cfg


def test_an_html_campaigns_batch_carries_both_versions_for_every_lead(tmp_path):
    plan = outreach.build_batch(_batch_cfg(tmp_path, "html"), [make_lead(_row=2, FirstName="Ann")], "intro", 10, forced_variant="A")
    assert len(plan) == 1 and "Hi Ann" in plan[0]["html_body"] and "Kelson Agency" in plan[0]["html_body"]
    assert plan[0]["body"].startswith("Hi Ann") and "12 Example Street" in plan[0]["body"] and plan[0]["subject"] == "Hello Ann" and plan[0]["missing_variables"] == []


def test_a_plain_text_campaigns_batch_has_no_html_and_is_otherwise_the_same(tmp_path):
    plan = outreach.build_batch(_batch_cfg(tmp_path, "text", html_text="<p>ignored</p>"), [make_lead(_row=2, FirstName="Ann")], "intro", 10, forced_variant="A")
    assert plan[0]["html_body"] is None and plan[0]["body"].strip() == "Hi Ann" and plan[0]["subject"] == "Hello Ann"


def test_a_campaign_with_no_format_key_at_all_builds_exactly_as_before(tmp_path):
    cfg = _batch_cfg(tmp_path, "text")
    del cfg["format"]
    plan = outreach.build_batch(cfg, [make_lead(_row=2, FirstName="Ann")], "intro", 10, forced_variant="A")
    assert plan[0]["html_body"] is None and plan[0]["body"].strip() == "Hi Ann"


def test_missing_variables_from_the_html_are_added_to_the_ones_from_the_text_without_duplicates(tmp_path):
    cfg = _batch_cfg(tmp_path, "html", html_text="{{ a }} {{ b }}{{ footer }}")
    _write(tmp_path, "intro_A.txt", "Subject: Hi\n\n{{ b }} {{ c }} {{ footer }}\n")
    assert outreach.build_batch(cfg, [make_lead(_row=2)], "intro", 10, forced_variant="A")[0]["missing_variables"] == ["b", "c", "a"]


def test_an_unsafe_html_file_stops_the_batch_instead_of_being_sent(tmp_path):
    with pytest.raises(outreach.TemplateError, match="not safe to send"):
        outreach.build_batch(_batch_cfg(tmp_path, "html", html_text="<script>x</script>{{ footer }}"), [make_lead(_row=2)], "intro", 10, forced_variant="A")


def test_an_incomplete_footer_stops_the_batch_instead_of_sending_a_half_empty_footer(tmp_path):
    cfg = _batch_cfg(tmp_path, "html")
    cfg["footer"] = {"business_name": "Kelson Agency"}
    with pytest.raises(outreach.TemplateError, match="footer is incomplete"):
        outreach.build_batch(cfg, [make_lead(_row=2)], "intro", 10, forced_variant="A")


# ================================================================ the message that is sent

def test_an_html_email_is_one_message_with_the_text_copy_first_and_the_design_second():
    msg, message_id = outreach._build_outbound_message("sales@kelson.agency", "ann@x.com", "Hello", "plain words", html_body="<p>designed</p>")
    assert msg.get_content_type() == "multipart/alternative"
    parts = msg.get_payload()
    assert [p.get_content_type() for p in parts] == ["text/plain", "text/html"] and parts[0].get_payload(decode=True).decode() == "plain words" and parts[1].get_payload(decode=True).decode() == "<p>designed</p>"
    assert all(p.get_content_charset() == "utf-8" for p in parts) and message_id == msg["Message-ID"]


def test_an_html_email_carries_an_unsubscribe_header_that_emails_the_sending_mailbox():
    msg, _ = outreach._build_outbound_message("sales@kelson.agency", "ann@x.com", "Hello", "t", html_body="<p>h</p>")
    assert msg["List-Unsubscribe"] == "<mailto:sales@kelson.agency?subject=unsubscribe>"


def test_threading_headers_survive_in_an_html_email():
    msg, _ = outreach._build_outbound_message("s@k.agency", "a@x.com", "Re: Hello", "t", in_reply_to="<m1@x>", references="<m0@x> <m1@x>", html_body="<p>h</p>")
    assert msg["In-Reply-To"] == "<m1@x>" and msg["References"] == "<m0@x> <m1@x>" and msg["Subject"] == "Re: Hello" and msg["To"] == "a@x.com" and msg["From"] == "s@k.agency"


def test_a_plain_text_email_is_exactly_what_it_was_with_no_unsubscribe_header():
    msg, _ = outreach._build_outbound_message("s@k.agency", "a@x.com", "Hello", "plain")
    assert msg.get_content_type() == "text/plain" and msg["List-Unsubscribe"] is None and msg.get_payload(decode=True).decode() == "plain"


def test_a_plain_text_email_with_an_attachment_is_unchanged():
    msg, _ = outreach._build_outbound_message("s@k.agency", "a@x.com", "Hello", "plain", attachments=[{"filename": "a.txt", "content": b"x"}])
    assert msg.get_content_type() == "multipart/mixed" and msg["List-Unsubscribe"] is None


def test_an_html_email_cannot_also_carry_attachments():
    with pytest.raises(ValueError, match="cannot also carry attachments"):
        outreach._build_outbound_message("s@k.agency", "a@x.com", "Hello", "plain", attachments=[{"filename": "a.txt", "content": b"x"}], html_body="<p>h</p>")


def test_an_empty_html_body_is_still_an_html_email_not_silently_plain():
    msg, _ = outreach._build_outbound_message("s@k.agency", "a@x.com", "Hello", "plain", html_body="")
    assert msg.get_content_type() == "multipart/alternative"


def test_non_english_text_survives_in_both_versions():
    msg, _ = outreach._build_outbound_message("s@k.agency", "a@x.com", "Héllo", "Café ☕ 你好", html_body="<p>Café ☕ 你好</p>")
    parts = msg.get_payload()
    assert parts[0].get_payload(decode=True).decode("utf-8") == "Café ☕ 你好" and parts[1].get_payload(decode=True).decode("utf-8") == "<p>Café ☕ 你好</p>"


class _FakeSMTP:
    sent = []

    def __init__(self, host, port, context=None):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, user, password):
        pass

    def sendmail(self, sender, recipients, message):
        _FakeSMTP.sent.append((sender, recipients, message))


def test_smtp_send_puts_the_whole_html_message_on_the_wire(monkeypatch):
    _FakeSMTP.sent = []
    monkeypatch.setattr(outreach.smtplib, "SMTP_SSL", _FakeSMTP)
    outreach.smtp_send("s@k.agency", "pw", to="a@x.com", subject="Hello", body_text="plain", html_body="<p>design</p>")
    sender, recipients, raw = _FakeSMTP.sent[0]
    parsed = email.message_from_string(raw)
    assert sender == "s@k.agency" and recipients == ["a@x.com"] and parsed.get_content_type() == "multipart/alternative" and parsed["List-Unsubscribe"].startswith("<mailto:s@k.agency")
    assert [p.get_content_type() for p in parsed.get_payload()] == ["text/plain", "text/html"]


def test_smtp_send_without_html_is_a_plain_single_part_message(monkeypatch):
    _FakeSMTP.sent = []
    monkeypatch.setattr(outreach.smtplib, "SMTP_SSL", _FakeSMTP)
    outreach.smtp_send("s@k.agency", "pw", to="a@x.com", subject="Hello", body_text="plain")
    parsed = email.message_from_string(_FakeSMTP.sent[0][2])
    assert parsed.get_content_type() == "text/plain" and parsed["List-Unsubscribe"] is None


def _plan_item(html_body):
    return {"lead": make_lead(_row=2, LeadID="L1", Email="john@abc.com"), "variant": "A", "subject": "S", "body": "B", "html_body": html_body, "missing_variables": [],
            "in_reply_to": None, "references": None, "thread_subject": "S", "is_continuation": False}


@pytest.mark.parametrize("html_body, expect_kwarg", [("<p>x</p>", True), (None, False)])
def test_sending_hands_the_html_to_the_send_function_only_for_an_html_email(monkeypatch, html_body, expect_kwarg):
    seen = {}

    def fake_smtp_send(address, app_password, to, subject, body_text, **kwargs):
        seen.update(kwargs)
        return {"message_id": "<m1@x>"}

    monkeypatch.setattr(outreach, "build_batch", lambda *a, **k: [_plan_item(html_body)])
    monkeypatch.setattr(outreach, "smtp_send", fake_smtp_send)
    results = outreach.send_batch(_base_campaign_cfg(), FakeSheets([make_lead(_row=2, LeadID="L1", Email="john@abc.com")]), ACCOUNTS, "intro", 10)
    assert results[0]["status"] == "sent" and ("html_body" in seen) is expect_kwarg and (not expect_kwarg or seen["html_body"] == "<p>x</p>")


# ================================================================ unsubscribe requests

@pytest.mark.parametrize("subject, expected", [("unsubscribe", True), ("Unsubscribe", True), ("UNSUBSCRIBE", True), ("Re: unsubscribe", True), ("RE: Re: Unsubscribe", True), ("Fwd: Unsubscribe", True),
                                               ("  unsubscribe  ", True), ("Unsubscribe me", True), ("unsubscribe from Kelson", True), ("", False), (None, False), ("Hello", False),
                                               ("How do I unsubscribe?", False), ("Re: Our collaboration", False), ("Not interested", False),
                                               ("unsubscribe " + "x" * 60, False)])
def test_an_unsubscribe_request_is_recognised_by_its_subject_only(subject, expected):
    assert outreach.is_unsubscribe_request(subject) is expected


def _fetch_one(subject, from_addr="john@abc.com", message_id="<unsub1@mail.gmail.com>", in_reply_to="", body=""):
    def fake_imap_fetch_recent(address, app_password, since_dt, imap_host=None, imap_port=None, imap_username=None):
        return [{"message_id": message_id, "in_reply_to": in_reply_to, "references": in_reply_to, "subject": subject, "from": from_addr, "headers": {}, "body": body, "snippet": body, "date": None}]
    return fake_imap_fetch_recent


def _check(monkeypatch, fake_sheets, subject, handle, **kw):
    monkeypatch.setattr(outreach, "imap_fetch_recent", _fetch_one(subject, **kw))
    extra = {} if handle is None else {"handle_unsubscribe": handle}                 # None: leave it to the default
    return outreach.check_replies(fake_sheets, {"sales1": ACCOUNTS["sales1"]}, lookback_hours=24, campaign_name="html_campaign", **extra)


def test_an_unsubscribe_email_from_a_lead_stops_that_lead_in_an_html_campaign(monkeypatch):
    sheets = FakeSheets([make_lead(_row=2, LeadID="L1", Email="john@abc.com", MessageID="<intro1@x>")])
    actions = _check(monkeypatch, sheets, "unsubscribe", True)
    assert [a["action"] for a in actions] == [outreach.ACTION_UNSUBSCRIBED] and actions[0]["lead_id"] == "L1"
    lead = sheets._leads[0]
    assert lead["Status"] == outreach.STATUS_STOPPED_UNSUBSCRIBED == "Stopped - Unsubscribed"
    assert lead.get("ReplyStatus", "") != "Replied"                                      # an unsubscribe is not a reply: it must never count in the reply rate
    assert len(sheets.responses) == 1 and sheets.responses[0]["ActionTaken"] == outreach.ACTION_UNSUBSCRIBED and sheets.responses[0]["Subject"] == "unsubscribe"


def test_the_same_email_in_a_plain_text_campaign_is_only_logged_and_stops_nothing(monkeypatch):
    sheets = FakeSheets([make_lead(_row=2, LeadID="L1", Email="john@abc.com", MessageID="<intro1@x>")])
    actions = _check(monkeypatch, sheets, "unsubscribe", False)
    assert [a["action"] for a in actions] == [outreach.ACTION_LOGGED_UNVERIFIED] and sheets._leads[0].get("Status", "") != outreach.STATUS_STOPPED_UNSUBSCRIBED


def test_an_unsubscribe_lead_is_never_sent_to_again():
    lead = make_lead(_row=2, Status=outreach.STATUS_STOPPED_UNSUBSCRIBED)
    cfg = _base_campaign_cfg()
    assert outreach.get_eligible_leads([lead], cfg["stages"], 0) == [] and outreach.get_eligible_leads([make_lead(_row=2)], cfg["stages"], 0) != []
    assert outreach.STATUS_STOPPED_UNSUBSCRIBED in outreach.TERMINAL_STATUSES


def test_an_unsubscribe_from_an_address_that_is_not_a_lead_does_nothing(monkeypatch):
    sheets = FakeSheets([make_lead(_row=2, LeadID="L1", Email="john@abc.com")])
    assert _check(monkeypatch, sheets, "unsubscribe", True, from_addr="stranger@elsewhere.com") == [] and sheets.responses == []


def test_a_subject_that_only_mentions_unsubscribing_in_a_longer_question_is_not_a_request(monkeypatch):
    sheets = FakeSheets([make_lead(_row=2, LeadID="L1", Email="john@abc.com")])
    actions = _check(monkeypatch, sheets, "How do I unsubscribe from the weekly report emails you send?", True)
    assert [a["action"] for a in actions] == [outreach.ACTION_LOGGED_UNVERIFIED] and sheets._leads[0].get("Status", "") == ""


def test_checking_again_does_not_log_the_same_unsubscribe_twice(monkeypatch):
    sheets = FakeSheets([make_lead(_row=2, LeadID="L1", Email="john@abc.com")])
    _check(monkeypatch, sheets, "unsubscribe", True)
    _check(monkeypatch, sheets, "unsubscribe", True)
    assert len(sheets.responses) == 1 and sheets._leads[0]["Status"] == outreach.STATUS_STOPPED_UNSUBSCRIBED


def test_a_lead_who_already_completed_the_sequence_can_still_unsubscribe(monkeypatch):
    sheets = FakeSheets([make_lead(_row=2, LeadID="L1", Email="john@abc.com", Status=outreach.STATUS_COMPLETED)])
    _check(monkeypatch, sheets, "unsubscribe", True)
    assert sheets._leads[0]["Status"] == outreach.STATUS_STOPPED_UNSUBSCRIBED


def test_a_real_reply_that_says_unsubscribe_still_goes_through_the_normal_reply_path(monkeypatch):
    """Replying to our email (matched by thread) is a reply, as ever: the sequence stops as 'Replied', not 'Unsubscribed'."""
    sheets = FakeSheets([make_lead(_row=2, LeadID="L1", Email="john@abc.com", MessageID="<intro1@x>")])
    actions = _check(monkeypatch, sheets, "Re: Hello", True, in_reply_to="<intro1@x>", body="please unsubscribe me")
    assert actions[0]["match_method"] == "Header" and actions[0]["action"] == outreach.ACTION_STOPPED and sheets._leads[0]["Status"] == outreach.STATUS_STOPPED_REPLIED


def test_an_unsubscribe_request_never_touches_another_lead(monkeypatch):
    sheets = FakeSheets([make_lead(_row=2, LeadID="L1", Email="john@abc.com"), make_lead(_row=3, LeadID="L2", Email="mary@abc.com")])
    _check(monkeypatch, sheets, "unsubscribe", True)
    assert sheets._leads[0]["Status"] == outreach.STATUS_STOPPED_UNSUBSCRIBED and sheets._leads[1].get("Status", "") == ""


# ================================================================ the starter layouts really work in the engine

@pytest.mark.parametrize("key", list(hc.STARTER_LAYOUTS))
def test_every_starter_layout_and_its_plain_text_copy_render_with_a_real_lead_and_no_unknown_placeholder(tmp_path, key):
    """REGRESSION: the layouts once used {{ first_name }}, which the engine does not know (it is {{ FirstName }}), and the layout test hid it by declaring that name valid."""
    _write(tmp_path, "intro_A.html", hc.starter_html(key))
    _write(tmp_path, "intro_A.txt", hc.starter_plain_text("Spring news"))
    cfg = dict(CFG_WITH_FOOTER, templates_dir=str(tmp_path))
    rendered_html = outreach.render_email_html(str(tmp_path), "intro", "A", LEAD, cfg)
    text = outreach.render_email(str(tmp_path), "intro", "A", LEAD, is_first_stage=True, reserved_text=outreach.footer_reserved(cfg, [hc.starter_plain_text()], as_html=False))
    assert rendered_html["missing_variables"] == [] and text["missing_variables"] == [] and "{{" not in rendered_html["html"] and "{{" not in text["body"]
    assert "Hi Ann" in text["body"] and "Ann" in rendered_html["html"] and "Kelson Agency" in rendered_html["html"] and text["subject"] == "Spring news"


def test_a_lead_with_no_first_name_gets_the_default_in_the_starter_layouts(tmp_path):
    _write(tmp_path, "intro_A.html", hc.starter_html("simple_letter"))
    assert "Hi there," in outreach.render_email_html(str(tmp_path), "intro", "A", dict(LEAD, FirstName=""), CFG_WITH_FOOTER)["html"]


@pytest.mark.parametrize("key", list(hc.STARTER_LAYOUTS))
def test_the_starter_layouts_pass_the_checks_with_the_engines_own_variable_names(key):
    known = list(outreach.TEMPLATE_VARIABLE_MAP)
    assert hc.html_checks(hc.starter_html(key), hc.starter_plain_text(), known, CFG_WITH_FOOTER["footer"]) == {"errors": [], "warnings": []}


def test_a_reply_to_our_email_whose_subject_says_unsubscribe_is_still_a_normal_reply(monkeypatch):
    """Matched by the thread, so it is a reply (the sequence stops as Replied); only a NEW message from the lead's own address is an unsubscribe request."""
    sheets = FakeSheets([make_lead(_row=2, LeadID="L1", Email="john@abc.com", MessageID="<intro1@x>")])
    actions = _check(monkeypatch, sheets, "unsubscribe", True, in_reply_to="<intro1@x>", body="no thanks")
    assert actions[0]["match_method"] == "Header" and actions[0]["action"] == outreach.ACTION_STOPPED and sheets._leads[0]["Status"] == outreach.STATUS_STOPPED_REPLIED


def test_a_second_unsubscribe_from_someone_already_unsubscribed_is_logged_as_an_unsubscribe_and_changes_nothing(monkeypatch):
    sheets = FakeSheets([make_lead(_row=2, LeadID="L1", Email="john@abc.com", Status=outreach.STATUS_STOPPED_UNSUBSCRIBED)])
    actions = _check(monkeypatch, sheets, "unsubscribe", True, message_id="<unsub2@mail.gmail.com>")
    assert [a["action"] for a in actions] == [outreach.ACTION_UNSUBSCRIBED] and sheets._leads[0]["Status"] == outreach.STATUS_STOPPED_UNSUBSCRIBED and len(sheets.responses) == 1


# ================================================================ unsubscribe handling is on for every campaign

def _drive_the_commands(monkeypatch, cfg):
    import argparse
    calls = []
    monkeypatch.setattr(outreach, "get_campaign", lambda name, **kw: cfg)
    monkeypatch.setattr(outreach, "_connect_sheets", lambda c: FakeSheets([]))
    monkeypatch.setattr(outreach, "load_email_accounts", lambda: {})
    monkeypatch.setattr(outreach, "discover_campaign_names", lambda: ["Foo"])
    monkeypatch.setattr(outreach, "_sync_sent_mail_quietly", lambda *a, **k: None)
    monkeypatch.setattr(outreach, "check_replies", lambda *a, **kw: calls.append(dict(kw)) or [])
    outreach.cmd_check_replies(argparse.Namespace(campaign="Foo"))
    outreach.cmd_check_replies_all(argparse.Namespace())
    return calls


@pytest.mark.parametrize("cfg", [{"reply_monitor": {}, "format": "html"}, {"reply_monitor": {}, "format": "text"}, {"reply_monitor": {}}])
def test_the_check_commands_call_the_reply_check_exactly_as_they_always_did_for_every_kind_of_campaign(monkeypatch, cfg):
    calls = _drive_the_commands(monkeypatch, cfg)
    assert len(calls) == 2 and all("handle_unsubscribe" not in kwargs for kwargs in calls)


def test_unsubscribe_handling_is_on_by_default():
    import inspect
    assert inspect.signature(outreach.check_replies).parameters["handle_unsubscribe"].default is True


def test_a_plain_text_campaign_stops_a_lead_who_unsubscribes_with_no_special_switch(monkeypatch):
    sheets = FakeSheets([make_lead(_row=2, LeadID="L1", Email="john@abc.com", MessageID="<intro1@x>")])
    actions = _check(monkeypatch, sheets, "unsubscribe", None)
    assert [a["action"] for a in actions] == [outreach.ACTION_UNSUBSCRIBED] and sheets._leads[0]["Status"] == outreach.STATUS_STOPPED_UNSUBSCRIBED


def test_one_unsubscribe_email_stops_the_same_address_in_every_campaign_that_has_it(monkeypatch):
    """Each campaign's reply check reads the same mailbox, so the same message is seen by each of them."""
    first = FakeSheets([make_lead(_row=2, LeadID="L1", Email="john@abc.com")])
    second = FakeSheets([make_lead(_row=2, LeadID="L9", Email="john@abc.com"), make_lead(_row=3, LeadID="L10", Email="other@abc.com")])
    _check(monkeypatch, first, "unsubscribe", None)
    _check(monkeypatch, second, "unsubscribe", None)
    assert first._leads[0]["Status"] == outreach.STATUS_STOPPED_UNSUBSCRIBED and second._leads[0]["Status"] == outreach.STATUS_STOPPED_UNSUBSCRIBED
    assert second._leads[1].get("Status", "") == ""                                          # a different address is untouched


# ================================================================ the test email

class _Sent:
    calls = []


def _capture_smtp(monkeypatch):
    _Sent.calls = []

    def fake(address, app_password, to, subject, body_text, **kwargs):
        _Sent.calls.append({"address": address, "to": to, "subject": subject, "body": body_text, **kwargs})
        return {"message_id": "<test1@x>"}
    monkeypatch.setattr(outreach, "smtp_send", fake)


def _test_cfg(tmp_path, fmt, footer=True, stages=("intro", "followup1")):
    cfg = _base_campaign_cfg()
    cfg["templates_dir"] = str(tmp_path)
    cfg["format"] = fmt
    cfg["stages"] = [{"name": s, "template_prefix": s, "wait_days": 0} for s in stages]
    cfg["variants"] = ["A"]
    if footer:
        cfg["footer"] = CFG_WITH_FOOTER["footer"]
    for s in stages:
        _write(tmp_path, f"{s}_A.txt", ("Subject: Hello {{ FirstName }}\n\nHi {{ FirstName }}\n\n{{ footer }}\n" if fmt == "html" else "Subject: Hello {{ FirstName }}\n\nHi {{ FirstName }}\n") if s == "intro"
               else ("Subject: \n\nFollow up {{ FirstName }}\n\n{{ footer }}\n" if fmt == "html" else "Subject: \n\nFollow up {{ FirstName }}\n"))
        _write(tmp_path, f"{s}_A.html", "<p>Hi {{ FirstName }} from {{ CompanyName }}</p>{{ footer }}")
    return cfg


def test_a_test_of_an_html_campaign_goes_out_as_the_two_version_message_with_a_test_subject(tmp_path, monkeypatch):
    _capture_smtp(monkeypatch)
    sent = outreach.send_test_email(_test_cfg(tmp_path, "html"), ACCOUNTS, "intro", "A", "team@kelson.agency")
    call = _Sent.calls[0]
    assert sent["format"] == "html" and sent["to"] == "team@kelson.agency" and call["to"] == "team@kelson.agency" and call["subject"] == "[TEST] Hello Test"
    assert "Hi Test" in call["body"] and "Kelson Agency" in call["body"] and "Hi Test from Your Company" in call["html_body"] and "Kelson Agency" in call["html_body"]


def test_a_test_of_a_plain_text_campaign_is_a_plain_message_with_no_html_argument(tmp_path, monkeypatch):
    _capture_smtp(monkeypatch)
    sent = outreach.send_test_email(_test_cfg(tmp_path, "text"), ACCOUNTS, "intro", "A", "team@kelson.agency")
    assert sent["format"] == "text" and "html_body" not in _Sent.calls[0] and _Sent.calls[0]["body"].strip() == "Hi Test" and _Sent.calls[0]["subject"] == "[TEST] Hello Test"


def test_a_follow_up_with_a_blank_subject_is_tested_as_continuing_a_made_up_thread(tmp_path, monkeypatch):
    _capture_smtp(monkeypatch)
    outreach.send_test_email(_test_cfg(tmp_path, "html"), ACCOUNTS, "followup1", "A", "team@kelson.agency")
    assert _Sent.calls[0]["subject"] == "[TEST] Re: Test of this campaign"


@pytest.mark.parametrize("address", ["", "not an address", "a@b", "x@@y.com", None])
def test_a_test_to_an_invalid_address_sends_nothing(tmp_path, monkeypatch, address):
    _capture_smtp(monkeypatch)
    with pytest.raises(outreach.InvalidEmailFormatError):
        outreach.send_test_email(_test_cfg(tmp_path, "html"), ACCOUNTS, "intro", "A", address)
    assert _Sent.calls == []


@pytest.mark.parametrize("stage, variant, words", [("followup9", "A", "not a stage"), ("intro", "Z", "not a variant")])
def test_a_test_of_a_stage_or_variant_that_does_not_exist_sends_nothing(tmp_path, monkeypatch, stage, variant, words):
    _capture_smtp(monkeypatch)
    with pytest.raises(ValueError, match=words):
        outreach.send_test_email(_test_cfg(tmp_path, "html"), ACCOUNTS, stage, variant, "team@kelson.agency")
    assert _Sent.calls == []


def test_the_sender_is_the_one_asked_for_else_the_campaigns_default_else_the_first_account(tmp_path, monkeypatch):
    _capture_smtp(monkeypatch)
    cfg = _test_cfg(tmp_path, "text")
    names = list(ACCOUNTS)
    assert outreach.send_test_email(cfg, ACCOUNTS, "intro", "A", "t@k.agency", sender_account=names[-1])["account"] == names[-1]
    assert outreach.send_test_email(dict(cfg, default_sender_account=names[-1]), ACCOUNTS, "intro", "A", "t@k.agency")["account"] == names[-1]
    assert outreach.send_test_email(cfg, ACCOUNTS, "intro", "A", "t@k.agency")["account"] == names[0]


def test_an_unknown_sender_account_sends_nothing(tmp_path, monkeypatch):
    _capture_smtp(monkeypatch)
    with pytest.raises(outreach.MissingSenderAccountError):
        outreach.send_test_email(_test_cfg(tmp_path, "text"), ACCOUNTS, "intro", "A", "t@k.agency", sender_account="nobody")
    with pytest.raises(outreach.MissingSenderAccountError):
        outreach.send_test_email(_test_cfg(tmp_path, "text"), {}, "intro", "A", "t@k.agency")
    assert _Sent.calls == []


def test_a_test_refuses_an_html_email_with_an_incomplete_footer_or_unsafe_content_just_as_a_real_send_would(tmp_path, monkeypatch):
    _capture_smtp(monkeypatch)
    with pytest.raises(outreach.TemplateError, match="footer is incomplete"):
        outreach.send_test_email(_test_cfg(tmp_path, "html", footer=False), ACCOUNTS, "intro", "A", "t@k.agency")
    cfg = _test_cfg(tmp_path, "html")
    _write(tmp_path, "intro_A.html", "<script>x</script>{{ footer }}")
    with pytest.raises(outreach.TemplateError, match="not safe to send"):
        outreach.send_test_email(cfg, ACCOUNTS, "intro", "A", "t@k.agency")
    assert _Sent.calls == []


def test_the_test_command_prints_what_was_sent_and_a_failure_exits_with_an_error(tmp_path, monkeypatch, capsys):
    import argparse
    _capture_smtp(monkeypatch)
    monkeypatch.setattr(outreach, "get_campaign", lambda name, **kw: _test_cfg(tmp_path, "html"))
    monkeypatch.setattr(outreach, "load_email_accounts", lambda: ACCOUNTS)
    outreach.cmd_send_test(argparse.Namespace(campaign="C", stage="intro", variant="A", to="team@kelson.agency", account=""))
    assert "Test email sent to team@kelson.agency" in capsys.readouterr().out
    with pytest.raises(SystemExit) as caught:
        outreach.cmd_send_test(argparse.Namespace(campaign="C", stage="intro", variant="A", to="nope", account=""))
    assert caught.value.code == 1 and "the test email was NOT sent" in capsys.readouterr().out


def test_the_test_command_is_registered_and_the_workflow_passes_it_every_input():
    parser_text = open(os.path.join(os.path.dirname(__file__), "..", "outreach.py"), encoding="utf-8").read()
    workflow = open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows", "send_test_email.yml"), encoding="utf-8").read()
    assert 'sub.add_parser("send-test"' in parser_text and "p_test.set_defaults(func=cmd_send_test)" in parser_text
    for flag in ("--campaign", "--stage", "--variant", "--to", "--account"):
        assert flag in workflow
    assert "GOOGLE_SERVICE_ACCOUNT_JSON" not in workflow and "workflow_dispatch" in workflow          # it never touches a Sheet


# ================================================================ link tagging through the real engine paths

LINK_DESIGN = '<p>Hi {{ FirstName }}</p><a href="https://shop.example.com/offer?ref=a">Offer</a> <a href="mailto:hi@x.com">Mail</a>{{ footer }}'


def _tag_cfg(tmp_path, tracking, fmt="html"):
    cfg = _test_cfg(tmp_path, fmt)
    cfg["_campaign_name"] = "Spring_Launch"
    if tracking is not None:
        cfg["tracking"] = tracking
    _write(tmp_path, "intro_A.html", LINK_DESIGN)
    return cfg


def test_with_link_tagging_on_every_web_link_of_the_sent_email_is_labelled_with_its_campaign_and_email(tmp_path):
    cfg = _tag_cfg(tmp_path, {"utm": True})
    out = outreach.render_email_html(str(tmp_path), "intro", "A", LEAD, cfg)["html"]
    assert "ref=a&amp;utm_source=email&amp;utm_medium=outreach&amp;utm_campaign=Spring_Launch&amp;utm_content=intro_A" in out and 'href="mailto:hi@x.com"' in out


def test_the_label_says_which_stage_and_variant_the_click_came_from(tmp_path):
    cfg = _tag_cfg(tmp_path, {"utm": True})
    _write(tmp_path, "followup1_A.html", LINK_DESIGN)
    assert "utm_content=followup1_A" in outreach.render_email_html(str(tmp_path), "followup1", "A", LEAD, cfg)["html"]


@pytest.mark.parametrize("tracking", [None, {}, {"utm": False}])
def test_with_link_tagging_off_the_email_is_exactly_what_it_was(tmp_path, tracking):
    out = outreach.render_email_html(str(tmp_path), "intro", "A", LEAD, _tag_cfg(tmp_path, tracking))["html"]
    assert "utm_" not in out and 'href="https://shop.example.com/offer?ref=a"' in out


def test_the_plain_text_copy_is_never_tagged(tmp_path):
    cfg = _tag_cfg(tmp_path, {"utm": True})
    _write(tmp_path, "intro_A.txt", "Subject: Hi\n\nSee https://shop.example.com/offer {{ footer }}\n")
    text_reserved = outreach.footer_reserved(cfg, ["{{ footer }}"], as_html=False)
    assert "utm_" not in outreach.render_email(str(tmp_path), "intro", "A", LEAD, is_first_stage=True, reserved_text=text_reserved)["body"]


def test_a_batch_carries_the_tagged_html_and_an_untagged_text_copy(tmp_path):
    cfg = _batch_cfg(tmp_path, "html", html_text=LINK_DESIGN)
    cfg["tracking"] = {"utm": True}
    cfg["_campaign_name"] = "Spring_Launch"
    plan = outreach.build_batch(cfg, [make_lead(_row=2, FirstName="Ann")], "intro", 10, forced_variant="A")
    assert "utm_campaign=Spring_Launch" in plan[0]["html_body"] and "utm_" not in plan[0]["body"]


def test_a_test_email_is_tagged_exactly_like_a_real_one(tmp_path, monkeypatch):
    _capture_smtp(monkeypatch)
    outreach.send_test_email(_tag_cfg(tmp_path, {"utm": True}), ACCOUNTS, "intro", "A", "team@kelson.agency")
    assert "utm_content=intro_A" in _Sent.calls[0]["html_body"]


def test_the_preview_in_the_editor_is_tagged_exactly_like_the_send(tmp_path):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "streamlit_app"))          # this file must work on its own, not only after other test files set the path
    import html_editor_logic as EL
    cfg = _tag_cfg(tmp_path, {"utm": True})
    preview = EL.render_preview(LINK_DESIGN, LEAD, cfg, utm_content="intro_A")
    assert preview["html"] == outreach.render_email_html(str(tmp_path), "intro", "A", LEAD, cfg)["html"]


def test_a_designer_file_with_a_broken_link_still_sends(tmp_path):
    cfg = _tag_cfg(tmp_path, {"utm": True})
    _write(tmp_path, "intro_A.html", '<a href="https://[broken">x</a><a href="https://ok.com">y</a>{{ footer }}')
    out = outreach.render_email_html(str(tmp_path), "intro", "A", LEAD, cfg)["html"]
    assert 'href="https://[broken"' in out and "https://ok.com/?utm_source=email" in out


@pytest.mark.parametrize("tracking, message", [("yes", "tracking must be a mapping"), (["utm"], "tracking must be a mapping"), ({"utm": "yes"}, "tracking.utm must be true or false"),
                                               ({"utm": 1}, "tracking.utm must be true or false"), ({"source": 5}, "tracking.source must be text"), ({"medium": ["x"]}, "tracking.medium must be text")])
def test_a_tracking_setting_of_the_wrong_kind_is_refused(tmp_path, tracking, message):
    import yaml
    with pytest.raises(outreach.ConfigError, match=message):
        outreach.get_campaign("camp", **_campaign(tmp_path, "tracking: " + yaml.safe_dump(tracking, default_flow_style=True).strip() + "\n"))


def test_a_valid_tracking_setting_is_accepted(tmp_path):
    cfg = outreach.get_campaign("camp", **_campaign(tmp_path, "tracking:\n  utm: true\n  source: kelson\n  medium: newsletter\n"))
    assert cfg["tracking"] == {"utm": True, "source": "kelson", "medium": "newsletter"}


# ================================================================ the wait between sends (chosen per campaign)

def _pace(tmp_path, low, high):
    return outreach.get_campaign("camp", **_campaign(tmp_path, f"sending:\n  delay_min_minutes: {low}\n  delay_max_minutes: {high}\n"))


@pytest.mark.parametrize("low, high", [(3, 7), (0, 0), (5, 5), (0.5, 2.5), (1, 120), (0, 10)])
def test_any_sensible_wait_including_a_fixed_one_and_zero_is_accepted_and_reaches_the_sending_settings(tmp_path, low, high):
    cfg = _pace(tmp_path, low, high)
    assert cfg["sending"]["delay_min_minutes"] == low and cfg["sending"]["delay_max_minutes"] == high


def test_the_campaign_with_no_wait_of_its_own_keeps_the_shared_default(tmp_path):
    kw = _campaign(tmp_path, None)
    shared = outreach.load_settings(kw["settings_path"])["default_campaign_settings"]["sending"]
    cfg = outreach.get_campaign("camp", **kw)
    assert (cfg["sending"]["delay_min_minutes"], cfg["sending"]["delay_max_minutes"]) == (shared["delay_min_minutes"], shared["delay_max_minutes"])


def test_one_end_can_be_set_without_the_other_and_the_rest_comes_from_the_shared_default(tmp_path):
    kw = _campaign(tmp_path, "sending:\n  delay_max_minutes: 12\n")
    shared = outreach.load_settings(kw["settings_path"])["default_campaign_settings"]["sending"]
    cfg = outreach.get_campaign("camp", **kw)
    assert (cfg["sending"]["delay_min_minutes"], cfg["sending"]["delay_max_minutes"]) == (shared["delay_min_minutes"], 12)


@pytest.mark.parametrize("low, high", [(-1, 5), (3, -2), ("'3'", 7), (3, "'soon'"), ("true", 7), (3, "false"), (".nan", 7), (3, ".nan"), ("[1]", 7), ("null", 7)])
def test_a_wait_that_is_not_a_number_of_minutes_zero_or_more_is_refused(tmp_path, low, high):
    with pytest.raises(outreach.ConfigError, match=r"sending\.delay_(min|max)_minutes must be a number of minutes"):
        _pace(tmp_path, low, high)


@pytest.mark.parametrize("low, high", [(8, 7), (5, 4.5), (120, 1), (0.6, 0.5)])
def test_a_wait_whose_start_is_after_its_end_is_refused_and_both_numbers_are_named(tmp_path, low, high):
    with pytest.raises(outreach.ConfigError, match=rf"delay_min_minutes \({low}\) cannot be more than sending\.delay_max_minutes \({high}\)"):
        _pace(tmp_path, low, high)


def test_the_round_loop_waits_a_random_time_between_the_two_chosen_numbers_in_minutes(monkeypatch):
    waits = []
    monkeypatch.setattr(outreach.time, "sleep", lambda seconds: waits.append(seconds))
    monkeypatch.setattr(outreach.random, "uniform", lambda a, b: (a + b) / 2)
    cfg = _base_campaign_cfg()
    cfg["sending"].update({"delay_min_minutes": 2, "delay_max_minutes": 4})
    items = [_plan_item(None), dict(_plan_item(None), lead=make_lead(_row=3, LeadID="L2", Email="mary@abc.com"))]
    monkeypatch.setattr(outreach, "build_batch", lambda *a, **k: items)
    monkeypatch.setattr(outreach, "smtp_send", lambda *a, **k: {"message_id": "<m@x>"})
    outreach.send_batch(cfg, FakeSheets([make_lead(_row=2, LeadID="L1", Email="john@abc.com"), make_lead(_row=3, LeadID="L2", Email="mary@abc.com")]), ACCOUNTS, "intro", 10)
    assert waits and all(seconds == 180 for seconds in waits)                 # the middle of 2 and 4 minutes, in seconds
