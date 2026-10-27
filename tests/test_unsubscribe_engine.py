"""The hosted unsubscribe page in the engine: the signed link in an HTML email, the per-brand opt-out list read before every HTML send, who is marked in the sheet, and the test email.
Every test that matters is paired with the plain-text case beside it, because a text campaign must never use any of this."""
import argparse
import email
import json
import re
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import html_campaign_logic as hc  # noqa: E402
import outreach  # noqa: E402
import unsubscribe_logic as ul  # noqa: E402
from test_html_campaigns_engine import _Sent, _batch_cfg, _capture_smtp, _test_cfg  # noqa: E402
from test_outreach import ACCOUNTS, FakeSheets, make_lead  # noqa: E402

SECRET = "test-secret-1234567890"
PAGE = "https://script.google.com/macros/s/ID/exec"
REAL_TRACE = outreach.trace_unsubscribe_link
REAL_STATIC, REAL_CONFIG, REAL_API = outreach.probe_static_page, outreach.probe_page_config, outreach.probe_unsubscribe_api
REAL_PROBE = outreach.probe_unsubscribe_page                                 # the real function, kept before every test stubs it: no test may reach the internet


@pytest.fixture
def page_on(monkeypatch):
    monkeypatch.setenv("UNSUBSCRIBE_URL", PAGE)
    monkeypatch.setenv("UNSUBSCRIBE_SECRET", SECRET)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """The page check is stubbed for every test (it answers 'ok' unless a test says otherwise) and its calls are recorded."""
    calls = []
    monkeypatch.setattr(outreach, "probe_unsubscribe_page", lambda url, timeout=20, expect=(): calls.append(url) or ul.classify_probe(200, url, "<div>This link is not valid</div>", expect=expect))
    monkeypatch.setattr(outreach, "probe_static_page", lambda url, timeout=20: ("ok", "GitHub Pages served the unsubscribe page (HTTP 200)."))
    monkeypatch.setattr(outreach, "probe_page_config", lambda page_url, expected_api, timeout=20: ("ok", "the page's config.js names the same Google script as UNSUBSCRIBE_URL."))
    monkeypatch.setattr(outreach, "probe_unsubscribe_api", lambda api_url, token, origin, post=False, timeout=20, max_hops=6: ("ok", "every answer (2 steps) allows a page on " + origin + ", and the last one is the script's data."))
    monkeypatch.setattr(outreach, "trace_unsubscribe_link", lambda url, timeout=20, expect=(), frame_hosts=ul.FRAME_HOSTS, max_hops=6: [
        {"kind": "hop", "where": "script.google.com/macros/s/ID/exec", "status": 200, "state": "ok", "size": 100, "content_type": "text/html", "location": "", "cookies": [], "frame_options": ""}])
    return calls


@pytest.fixture(autouse=True)
def page_off_by_default(monkeypatch):
    monkeypatch.delenv("UNSUBSCRIBE_URL", raising=False)
    monkeypatch.delenv("UNSUBSCRIBE_SECRET", raising=False)


class ListSheets(FakeSheets):
    """The fake sheet plus the shared Unsubscribes tab."""

    def __init__(self, leads, rows=(), fail=None):
        super().__init__(leads)
        self.rows, self.fail, self.read_count = list(rows), fail, 0

    def read_unsubscribes(self):
        self.read_count += 1
        if self.fail:
            raise self.fail
        return [dict(row) for row in self.rows]


def opted_out(email_address, key="kelsonagency"):
    return {"Email": email_address, "BrandKey": key}


def link_in(html):
    return next(part for part in html.split('href="')[1:] if part.startswith(PAGE)).split('"')[0]


def html_cfg(tmp_path, name="Spring_Launch", business="Kelson Agency", tracking=False, html_text="<p>Hi {{ FirstName }}</p>{{ footer }}"):
    cfg = _batch_cfg(tmp_path, "html", html_text=html_text)
    cfg["_campaign_name"] = name
    cfg["footer"] = {"business_name": business, "address": "12 Example Street, New York, NY"}
    if tracking:
        cfg["tracking"] = {"utm": True}
    return cfg


def text_cfg(tmp_path):
    cfg = _batch_cfg(tmp_path, "text", html_text=None)
    cfg["_campaign_name"] = "Text_Campaign"
    return cfg


def sent_to(calls):
    return [call["to"] for call in calls]


# ================================================================ the signed link in the email

def test_with_the_page_not_set_up_the_footer_keeps_its_stand_in_and_no_link_is_made(tmp_path):
    cfg = html_cfg(tmp_path)
    out = outreach.render_email_html(str(tmp_path), "intro", "A", make_lead(FirstName="Ann"), cfg)
    assert out["unsubscribe_url"] is None and hc.UNSUBSCRIBE_TOKEN in out["html"] and PAGE not in out["html"]


def test_with_the_page_set_up_each_lead_gets_its_own_signed_link_in_the_footer(tmp_path, page_on):
    cfg = html_cfg(tmp_path)
    out = outreach.render_email_html(str(tmp_path), "intro", "A", make_lead(FirstName="Ann", LeadID="L-9", Email="ann@x.com"), cfg)
    assert out["unsubscribe_url"].startswith(PAGE + "?t=") and out["unsubscribe_url"] in out["html"] and hc.UNSUBSCRIBE_TOKEN not in out["html"]
    record = ul.verify_token(SECRET, out["unsubscribe_url"].split("?t=")[1])
    assert record == {"v": 1, "c": ul.campaign_code(SECRET, "Spring_Launch"), "l": "L-9", "e": "ann@x.com", "b": "Kelson Agency", "k": "kelsonagency", "a": "12 Example Street, New York, NY"}


def test_two_leads_get_two_different_links(tmp_path, page_on):
    cfg = html_cfg(tmp_path)
    first = outreach.render_email_html(str(tmp_path), "intro", "A", make_lead(LeadID="L1", Email="a@x.com"), cfg)["unsubscribe_url"]
    second = outreach.render_email_html(str(tmp_path), "intro", "A", make_lead(LeadID="L2", Email="b@x.com"), cfg)["unsubscribe_url"]
    assert first != second and ul.verify_token(SECRET, second.split("?t=")[1])["e"] == "b@x.com"


def test_the_page_address_is_never_given_link_tagging_labels(tmp_path, page_on):
    cfg = html_cfg(tmp_path, tracking=True, html_text='<p>Hi</p><a href="https://shop.example.com/x">shop</a>{{ footer }}')
    out = outreach.render_email_html(str(tmp_path), "intro", "A", make_lead(), cfg)
    assert "utm_source=email" in out["html"] and out["unsubscribe_url"] in out["html"] and "utm_" not in out["unsubscribe_url"]
    assert f'href="{out["unsubscribe_url"]}"' in out["html"]


def test_an_address_with_its_own_query_is_escaped_for_use_in_the_attribute(tmp_path, monkeypatch):
    monkeypatch.setenv("UNSUBSCRIBE_URL", "https://x.example/p?a=1&b=2")
    monkeypatch.setenv("UNSUBSCRIBE_SECRET", SECRET)
    out = outreach.render_email_html(str(tmp_path), "intro", "A", make_lead(), html_cfg(tmp_path))
    assert "https://x.example/p?a=1&amp;b=2&amp;t=" in out["html"] and out["unsubscribe_url"].startswith("https://x.example/p?a=1&b=2&t=")


def test_the_plain_text_half_shows_the_same_link(tmp_path, page_on):
    cfg = html_cfg(tmp_path)
    item = outreach.build_batch(cfg, [make_lead(_row=2, FirstName="Ann")], "intro", 10, forced_variant="A")[0]
    assert f"Unsubscribe ({item['unsubscribe_url']})" in item["body"] and item["unsubscribe_url"] in item["html_body"]


def test_a_design_with_no_footer_has_no_stand_in_and_so_gets_no_link_in_its_body_but_still_the_header(tmp_path, page_on):
    cfg = html_cfg(tmp_path, html_text='<p>Hi <a href="https://x.example/unsub">unsubscribe</a></p>')
    out = outreach.render_email_html(str(tmp_path), "intro", "A", make_lead(), cfg)
    assert PAGE not in out["html"] and out["unsubscribe_url"].startswith(PAGE)                  # the design is untouched; the header (below) still carries the page


def test_a_plain_text_campaign_never_makes_a_link_even_when_the_page_is_set_up(tmp_path, page_on):
    cfg = text_cfg(tmp_path)
    assert outreach.unsubscribe_page_link(cfg, make_lead(LeadID="L1", Email="a@x.com")) is None
    item = outreach.build_batch(cfg, [make_lead(_row=2)], "intro", 10, forced_variant="A")[0]
    assert item["unsubscribe_url"] is None and item["html_body"] is None and PAGE not in item["body"]


@pytest.mark.parametrize("env", [{"UNSUBSCRIBE_URL": PAGE}, {"UNSUBSCRIBE_SECRET": SECRET}, {"UNSUBSCRIBE_URL": "http://x.example/p", "UNSUBSCRIBE_SECRET": SECRET},
                                 {"UNSUBSCRIBE_URL": PAGE, "UNSUBSCRIBE_SECRET": "short"}])
def test_a_half_done_set_up_makes_no_link_at_all(tmp_path, monkeypatch, env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert outreach.unsubscribe_page_link(html_cfg(tmp_path), make_lead()) is None


# ================================================================ which brand a campaign belongs to

def test_campaigns_with_the_same_business_name_share_a_brand_and_different_names_do_not():
    assert outreach.campaign_brand_key({"_campaign_name": "A", "footer": {"business_name": "Dube Robe"}}) == outreach.campaign_brand_key({"_campaign_name": "B", "footer": {"business_name": "DUBEROBE"}}) == "duberobe"
    assert outreach.campaign_brand_key({"_campaign_name": "A", "footer": {"business_name": "Swoveralls"}}) != outreach.campaign_brand_key({"_campaign_name": "A", "footer": {"business_name": "DubeRobe"}})


def test_a_campaign_with_no_business_name_is_its_own_brand():
    assert outreach.campaign_brand_key({"_campaign_name": "Spring"}) == "campaign:spring" and outreach.campaign_brand_key({"_campaign_name": "Spring", "footer": {}}) == "campaign:spring"
    assert outreach.campaign_brand_key({"_campaign_name": "Spring", "footer": None}) == "campaign:spring"


# ================================================================ the message that is sent

def build(**kw):
    return outreach._build_outbound_message("sales@kelson.agency", "ann@x.com", "Hi", "plain", html_body="<p>x</p>", **kw)[0]


def test_with_the_page_the_header_offers_the_one_tap_link_first_and_the_email_address_as_the_fallback():
    msg = email.message_from_string(build(unsubscribe_url=PAGE + "?t=ABC").as_string())
    assert msg["List-Unsubscribe"] == f"<{PAGE}?t=ABC>, <mailto:sales@kelson.agency?subject=unsubscribe>"
    assert msg["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


def test_a_long_signed_link_survives_in_the_header_exactly(tmp_path, page_on):
    long_url = outreach.render_email_html(str(tmp_path), "intro", "A", make_lead(LeadID="L" * 40, Email="a" * 60 + "@x.com"), html_cfg(tmp_path, business="B" * 80))["unsubscribe_url"]
    assert len(long_url) > 400
    msg = email.message_from_string(build(unsubscribe_url=long_url).as_string())
    assert msg["List-Unsubscribe"].replace("\n", "").replace("\r", "").replace(" ", "").startswith(f"<{long_url}>,<mailto:")


def test_without_the_page_the_header_is_exactly_what_it_was_and_there_is_no_one_click_header():
    msg = email.message_from_string(build().as_string())
    assert msg["List-Unsubscribe"] == "<mailto:sales@kelson.agency?subject=unsubscribe>" and msg["List-Unsubscribe-Post"] is None


def test_a_plain_text_email_never_carries_either_header_even_if_a_link_were_passed():
    msg, _ = outreach._build_outbound_message("sales@kelson.agency", "ann@x.com", "Hi", "plain", unsubscribe_url=PAGE + "?t=ABC")
    assert msg["List-Unsubscribe"] is None and msg["List-Unsubscribe-Post"] is None and not msg.is_multipart()


def test_smtp_send_hands_the_link_to_the_message_builder_for_html_only(monkeypatch):
    seen = []

    def spy(*args, **kwargs):
        seen.append(kwargs)
        raise RuntimeError("stop before connecting")
    monkeypatch.setattr(outreach, "_build_outbound_message", spy)
    for kwargs in ({"html_body": "<p>x</p>", "unsubscribe_url": "U"}, {"html_body": "<p>x</p>"}, {"unsubscribe_url": "U"}, {}):
        with pytest.raises(RuntimeError):
            outreach.smtp_send("a@x.com", "pw", "b@x.com", "S", "B", **kwargs)
    assert seen[0].get("unsubscribe_url") == "U" and "unsubscribe_url" not in seen[1] and "unsubscribe_url" not in seen[2] and "html_body" not in seen[3]


# ================================================================ who is skipped (per brand)

def planned(cfg, leads, pairs, size=10):
    return [item["lead"]["Email"] for item in outreach.build_batch(cfg, leads, "intro", size, forced_variant="A", suppressed_pairs=pairs)]


def leads_of(*addresses):
    return [make_lead(_row=i + 2, LeadID=f"L{i}", Email=address) for i, address in enumerate(addresses)]


def test_a_lead_that_opted_out_of_this_brand_is_not_planned_and_the_others_are(tmp_path):
    cfg = html_cfg(tmp_path)
    pairs = ul.load_pairs([opted_out("b@x.com")])
    assert planned(cfg, leads_of("a@x.com", "b@x.com", "c@x.com"), pairs) == ["a@x.com", "c@x.com"]


def test_an_opt_out_from_another_brand_does_not_stop_this_brands_email(tmp_path):
    cfg = html_cfg(tmp_path, business="Kelson Agency")
    pairs = ul.load_pairs([opted_out("b@x.com", key="duberobe")])
    assert planned(cfg, leads_of("a@x.com", "b@x.com"), pairs) == ["a@x.com", "b@x.com"]


def test_the_same_address_is_stopped_for_one_brand_and_still_emailed_for_another(tmp_path):
    pairs = ul.load_pairs([opted_out("b@x.com", key="duberobe")])
    leads = leads_of("a@x.com", "b@x.com")
    assert planned(html_cfg(tmp_path, business="DubeRobe"), leads, pairs) == ["a@x.com"]
    assert planned(html_cfg(tmp_path, business="Swoveralls"), leads, pairs) == ["a@x.com", "b@x.com"]


def test_spelling_differences_in_the_footer_name_do_not_split_a_brand(tmp_path):
    pairs = ul.load_pairs([opted_out("b@x.com", key="duberobe")])
    for spelling in ("Dube Robe", "DUBEROBE", "dube-robe"):
        assert planned(html_cfg(tmp_path, business=spelling), leads_of("b@x.com"), pairs) == [], spelling


def test_a_campaign_with_no_business_name_is_only_stopped_by_an_opt_out_for_itself(tmp_path):
    cfg = html_cfg(tmp_path, name="Spring", business="", html_text='<p>Hi {{ FirstName }}. <a href="https://x.example/stop">Unsubscribe</a></p>')       # no {{ footer }}: the design has its own line
    assert planned(cfg, leads_of("b@x.com"), ul.load_pairs([opted_out("b@x.com", key="campaign:spring")])) == []
    assert planned(cfg, leads_of("b@x.com"), ul.load_pairs([opted_out("b@x.com", key="campaign:other")])) == ["b@x.com"]


def test_a_row_added_by_hand_without_a_brand_stops_the_address_for_every_brand(tmp_path):
    pairs = ul.load_pairs([{"Email": "b@x.com"}])
    assert planned(html_cfg(tmp_path, business="Anything"), leads_of("b@x.com"), pairs) == []


def test_the_address_comparison_ignores_case_and_surrounding_spaces(tmp_path):
    pairs = ul.load_pairs([opted_out("  B@X.com ")])
    assert planned(html_cfg(tmp_path), leads_of("b@x.COM"), pairs) == []


def test_opted_out_leads_are_removed_before_the_batch_is_cut_to_size(tmp_path):
    cfg = html_cfg(tmp_path)
    pairs = ul.load_pairs([opted_out("a@x.com")])
    assert planned(cfg, leads_of("a@x.com", "b@x.com", "c@x.com", "d@x.com"), pairs, size=2) == ["b@x.com", "c@x.com"]          # not just ['b@x.com']


def test_no_list_or_an_empty_one_changes_nothing(tmp_path):
    cfg = html_cfg(tmp_path)
    for pairs in (None, set()):
        assert planned(cfg, leads_of("a@x.com", "b@x.com"), pairs) == ["a@x.com", "b@x.com"]


def test_a_plain_text_campaign_ignores_the_list_even_if_one_is_passed(tmp_path):
    cfg = text_cfg(tmp_path)
    pairs = ul.load_pairs([{"Email": "b@x.com"}, opted_out("a@x.com", key="campaign:text_campaign")])
    assert planned(cfg, leads_of("a@x.com", "b@x.com"), pairs) == ["a@x.com", "b@x.com"]


# ================================================================ a real send

def send(cfg, sheets, monkeypatch, stage="intro"):
    monkeypatch.setattr(outreach.time, "sleep", lambda seconds: None)
    _capture_smtp(monkeypatch)
    return outreach.send_batch(cfg, sheets, ACCOUNTS, stage, 10, forced_variant="A")


def test_an_html_send_does_not_email_someone_who_opted_out_and_marks_them_in_their_own_tab(tmp_path, monkeypatch):
    sheets = ListSheets(leads_of("a@x.com", "b@x.com", "c@x.com"), rows=[opted_out("b@x.com")])
    results = send(html_cfg(tmp_path), sheets, monkeypatch)
    assert sorted(sent_to(_Sent.calls)) == ["a@x.com", "c@x.com"] and [r["status"] for r in results] == ["sent", "sent"]
    marked = {lead["Email"]: lead.get("Status") for lead in sheets._leads}
    assert marked["b@x.com"] == "Stopped - Unsubscribed" and marked["a@x.com"] != "Stopped - Unsubscribed" and sheets._leads[1]["LastActionAt"]


def test_the_list_is_read_once_per_run_however_many_leads_and_rounds(tmp_path, monkeypatch):
    sheets = ListSheets(leads_of(*[f"u{i}@x.com" for i in range(8)]), rows=[opted_out("u3@x.com")])
    send(html_cfg(tmp_path), sheets, monkeypatch)
    assert sheets.read_count == 1 and len(_Sent.calls) == 7


def test_two_brands_sharing_one_sheet_each_honour_only_their_own_opt_outs(tmp_path, monkeypatch):
    rows = [opted_out("a@x.com", key="duberobe"), opted_out("b@x.com", key="swoveralls")]
    first = ListSheets(leads_of("a@x.com", "b@x.com"), rows=rows)
    send(html_cfg(tmp_path, name="Dube_Spring", business="DubeRobe"), first, monkeypatch)
    assert sent_to(_Sent.calls) == ["b@x.com"]
    second = ListSheets(leads_of("a@x.com", "b@x.com"), rows=rows)
    send(html_cfg(tmp_path, name="Swov_Spring", business="Swoveralls"), second, monkeypatch)
    assert sent_to(_Sent.calls) == ["a@x.com"]


def test_a_lead_whose_outcome_is_already_decided_is_left_as_it_is_and_still_not_emailed(tmp_path, monkeypatch):
    lead = make_lead(_row=2, LeadID="L0", Email="b@x.com", Status="Stopped - Replied", ReplyStatus="Replied")
    sheets = ListSheets([lead], rows=[opted_out("b@x.com")])
    send(html_cfg(tmp_path), sheets, monkeypatch)
    assert _Sent.calls == [] and sheets._leads[0]["Status"] == "Stopped - Replied"


def test_when_the_list_cannot_be_read_nothing_is_sent_and_the_reason_says_why(tmp_path, monkeypatch):
    sheets = ListSheets(leads_of("a@x.com", "b@x.com"), fail=RuntimeError("quota exceeded"))
    with pytest.raises(outreach.UnsubscribeListUnreadableError) as caught:
        send(html_cfg(tmp_path), sheets, monkeypatch)
    assert _Sent.calls == [] and "quota exceeded" in str(caught.value) and "nothing was sent" in str(caught.value) and "next run tries again" in str(caught.value)
    assert all(lead.get("Status") != "Stopped - Unsubscribed" for lead in sheets._leads)


def test_an_html_campaign_on_a_connector_without_the_list_also_refuses_to_send_rather_than_guess(tmp_path, monkeypatch):
    with pytest.raises(outreach.UnsubscribeListUnreadableError):
        send(html_cfg(tmp_path), FakeSheets(leads_of("a@x.com")), monkeypatch)
    assert _Sent.calls == []


def test_a_plain_text_campaign_never_reads_the_list_and_sends_exactly_as_before(tmp_path, monkeypatch):
    sheets = ListSheets(leads_of("a@x.com", "b@x.com"), rows=[{"Email": "b@x.com"}], fail=RuntimeError("must not be read"))
    send(text_cfg(tmp_path), sheets, monkeypatch)
    assert sheets.read_count == 0 and sorted(sent_to(_Sent.calls)) == ["a@x.com", "b@x.com"]
    assert all("html_body" not in call and "unsubscribe_url" not in call for call in _Sent.calls)           # the very same arguments a plain-text email always had


def test_a_plain_text_campaign_works_on_a_connector_that_has_no_list_at_all(tmp_path, monkeypatch):
    send(text_cfg(tmp_path), FakeSheets(leads_of("a@x.com")), monkeypatch)
    assert sent_to(_Sent.calls) == ["a@x.com"]


def test_the_signed_link_reaches_the_send_for_each_lead_when_the_page_is_set_up(tmp_path, monkeypatch, page_on):
    send(html_cfg(tmp_path), ListSheets(leads_of("a@x.com", "b@x.com")), monkeypatch)
    links = {call["to"]: call["unsubscribe_url"] for call in _Sent.calls}
    assert set(links) == {"a@x.com", "b@x.com"} and links["a@x.com"] != links["b@x.com"]
    assert all(ul.verify_token(SECRET, url.split("?t=")[1])["e"] == to for to, url in links.items())


def test_without_the_page_the_send_passes_no_link_so_the_email_is_exactly_as_it_was(tmp_path, monkeypatch):
    send(html_cfg(tmp_path), ListSheets(leads_of("a@x.com")), monkeypatch)
    assert "unsubscribe_url" not in _Sent.calls[0] and "html_body" in _Sent.calls[0]


def test_one_failed_write_while_marking_never_stops_the_others_or_the_sending(tmp_path, monkeypatch, capsys):
    class Flaky(ListSheets):
        def update_lead_fields(self, row_number, fields):
            if row_number == 3:
                raise RuntimeError("write refused")
            super().update_lead_fields(row_number, fields)
    sheets = Flaky(leads_of("a@x.com", "b@x.com", "c@x.com", "d@x.com"), rows=[opted_out("b@x.com"), opted_out("c@x.com")])
    send(html_cfg(tmp_path), sheets, monkeypatch)
    assert sorted(sent_to(_Sent.calls)) == ["a@x.com", "d@x.com"]
    marked = {lead["Email"]: lead.get("Status") for lead in sheets._leads}
    assert marked["c@x.com"] == "Stopped - Unsubscribed" and marked["b@x.com"] != "Stopped - Unsubscribed"
    assert "could not mark b@x.com as unsubscribed" in capsys.readouterr().out


# ================================================================ the real connector's read

class FakeTab:
    def __init__(self, records):
        self.records = records

    def get_all_records(self):
        return self.records


def connector(known):
    shell = object.__new__(outreach.SheetsConnector)
    shell._known_tabs = known
    return shell


def test_a_missing_unsubscribes_tab_means_nobody_has_opted_out():
    assert connector({}).read_unsubscribes() == [] and connector({"Other": FakeTab([{"x": 1}])}).read_unsubscribes() == []


def test_the_tab_is_read_from_the_shared_sheet_by_its_exact_name():
    rows = [{"Email": "a@x.com", "BrandKey": "k"}]
    assert connector({ul.UNSUBSCRIBE_TAB: FakeTab(rows)}).read_unsubscribes() == rows


def test_an_error_while_reading_the_tab_is_not_hidden_inside_the_connector():
    class Broken:
        def get_all_records(self):
            raise RuntimeError("boom")
    with pytest.raises(RuntimeError, match="boom"):
        connector({ul.UNSUBSCRIBE_TAB: Broken()}).read_unsubscribes()


# ================================================================ copying the list into the sheet after a reply check

def test_the_reply_check_copies_the_list_into_an_html_campaigns_sheet(tmp_path):
    sheets = ListSheets(leads_of("a@x.com", "b@x.com"), rows=[opted_out("a@x.com")])
    outreach._mirror_unsubscribes_quietly(html_cfg(tmp_path), sheets)
    assert sheets._leads[0]["Status"] == "Stopped - Unsubscribed" and sheets._leads[1].get("Status") != "Stopped - Unsubscribed" and sheets.read_count == 1


def test_it_does_nothing_at_all_for_a_plain_text_campaign(tmp_path):
    sheets = ListSheets(leads_of("a@x.com"), rows=[{"Email": "a@x.com"}])
    outreach._mirror_unsubscribes_quietly(text_cfg(tmp_path), sheets)
    assert sheets.read_count == 0 and sheets._leads[0].get("Status") != "Stopped - Unsubscribed" and sheets.get_all_leads_call_count == 0


def test_it_never_raises_when_the_list_cannot_be_read_and_says_so(tmp_path, capsys):
    outreach._mirror_unsubscribes_quietly(html_cfg(tmp_path), ListSheets(leads_of("a@x.com"), fail=RuntimeError("down")))
    assert "could not copy the Unsubscribes list into the sheet" in capsys.readouterr().out


def test_it_reports_how_many_leads_it_marked(tmp_path, capsys):
    outreach._mirror_unsubscribes_quietly(html_cfg(tmp_path), ListSheets(leads_of("a@x.com", "b@x.com"), rows=[opted_out("a@x.com"), opted_out("b@x.com")]))
    assert "marked 2 lead(s) as unsubscribed" in capsys.readouterr().out


def test_both_reply_check_commands_copy_the_list_for_html_campaigns_only(tmp_path, monkeypatch):
    html_sheets = ListSheets(leads_of("a@x.com"), rows=[opted_out("a@x.com")])
    text_sheets = ListSheets(leads_of("a@x.com"), rows=[{"Email": "a@x.com"}])
    campaigns = {"H": (html_cfg(tmp_path), html_sheets), "T": (text_cfg(tmp_path), text_sheets)}
    monkeypatch.setattr(outreach, "get_campaign", lambda name, **kw: campaigns[name][0])
    monkeypatch.setattr(outreach, "_connect_sheets", lambda cfg: next(s for c, s in campaigns.values() if c is cfg))
    monkeypatch.setattr(outreach, "load_email_accounts", lambda: ACCOUNTS)
    monkeypatch.setattr(outreach, "check_replies", lambda *a, **kw: [])
    monkeypatch.setattr(outreach, "_sync_sent_mail_quietly", lambda *a, **kw: None)
    monkeypatch.setattr(outreach, "discover_campaign_names", lambda: ["H", "T"])
    outreach.cmd_check_replies(argparse.Namespace(campaign="H"))
    outreach.cmd_check_replies(argparse.Namespace(campaign="T"))
    assert html_sheets._leads[0]["Status"] == "Stopped - Unsubscribed" and text_sheets._leads[0].get("Status") != "Stopped - Unsubscribed" and text_sheets.read_count == 0
    html_sheets._leads[0]["Status"] = ""
    outreach.cmd_check_replies_all(argparse.Namespace())
    assert html_sheets._leads[0]["Status"] == "Stopped - Unsubscribed" and text_sheets.read_count == 0


# ================================================================ the test email

def test_a_test_email_carries_a_test_link_that_the_page_will_not_record(tmp_path, monkeypatch, page_on):
    _capture_smtp(monkeypatch)
    sent = outreach.send_test_email(_test_cfg(tmp_path, "html"), ACCOUNTS, "intro", "A", "team@kelson.agency")
    url = _Sent.calls[0]["unsubscribe_url"]
    record = ul.verify_token(SECRET, url.split("?t=")[1])
    assert record["t"] == 1 and record["l"] == "TEST" and record["e"] == "team@kelson.agency" and record["b"] == "Kelson Agency"
    assert sent["unsubscribe_page"] is True and sent["unsubscribe_header"].startswith(f"<{url}>, <mailto:")
    assert url in _Sent.calls[0]["html_body"] and url in _Sent.calls[0]["body"]


def test_a_test_email_without_the_page_says_so_and_sends_no_link(tmp_path, monkeypatch):
    _capture_smtp(monkeypatch)
    sent = outreach.send_test_email(_test_cfg(tmp_path, "html"), ACCOUNTS, "intro", "A", "team@kelson.agency")
    assert sent["unsubscribe_page"] is False and "unsubscribe_url" not in _Sent.calls[0] and sent["unsubscribe_header"].startswith("<mailto:") and ", <mailto" not in sent["unsubscribe_header"]


def test_a_plain_text_test_email_has_no_page_and_no_header_even_when_the_page_is_set_up(tmp_path, monkeypatch, page_on):
    _capture_smtp(monkeypatch)
    sent = outreach.send_test_email(_test_cfg(tmp_path, "text"), ACCOUNTS, "intro", "A", "team@kelson.agency")
    assert sent["unsubscribe_page"] is False and sent["unsubscribe_header"] == "" and "unsubscribe_url" not in _Sent.calls[0] and "html_body" not in _Sent.calls[0]


def run_test_command(tmp_path, monkeypatch, capsys, fmt="html"):
    _capture_smtp(monkeypatch)
    monkeypatch.setattr(outreach, "load_email_accounts", lambda: ACCOUNTS)
    monkeypatch.setattr(outreach, "get_campaign", lambda name, **kw: _test_cfg(tmp_path, fmt))
    outreach.cmd_send_test(argparse.Namespace(campaign="C", stage="intro", variant="A", to="team@kelson.agency", account=""))
    return capsys.readouterr().out


def test_the_test_command_says_the_page_is_on_and_that_the_test_link_records_nothing(tmp_path, monkeypatch, capsys, page_on):
    out = run_test_command(tmp_path, monkeypatch, capsys)
    assert "Unsubscribe page: ON" in out and "records nothing" in out and "OFF" not in out


def test_the_test_command_says_the_page_is_off_when_it_is_not_set_up(tmp_path, monkeypatch, capsys):
    out = run_test_command(tmp_path, monkeypatch, capsys)
    assert "Unsubscribe page: OFF (not set up yet): the email-based unsubscribe is used." in out


def test_the_test_command_explains_a_half_done_set_up(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("UNSUBSCRIBE_URL", "http://not-secure.example/p")
    monkeypatch.setenv("UNSUBSCRIBE_SECRET", SECRET)
    out = run_test_command(tmp_path, monkeypatch, capsys)
    assert "Unsubscribe page: OFF. UNSUBSCRIBE_URL must start with https://" in out


def test_the_test_command_for_a_plain_text_campaign_says_nothing_about_the_page(tmp_path, monkeypatch, capsys, page_on):
    out = run_test_command(tmp_path, monkeypatch, capsys, fmt="text")
    assert "Unsubscribe" not in out


# ================================================================ the preview

def run_preview(tmp_path, monkeypatch, capsys, cfg, sheets):
    monkeypatch.setattr(outreach, "get_campaign", lambda name, **kw: cfg)
    monkeypatch.setattr(outreach, "_connect_sheets", lambda c: sheets)
    outreach.cmd_preview(argparse.Namespace(campaign="C", stage="intro", batch_size=10, variant="A", ignore_wait_days=False))
    return capsys.readouterr().out


def test_the_preview_leaves_out_people_who_opted_out(tmp_path, monkeypatch, capsys):
    out = run_preview(tmp_path, monkeypatch, capsys, html_cfg(tmp_path), ListSheets(leads_of("a@x.com", "b@x.com"), rows=[opted_out("b@x.com")]))
    assert "a@x.com" in out and "b@x.com" not in out


def test_the_preview_still_works_when_the_list_cannot_be_read_and_warns_that_it_ignored_it(tmp_path, monkeypatch, capsys):
    out = run_preview(tmp_path, monkeypatch, capsys, html_cfg(tmp_path), ListSheets(leads_of("a@x.com", "b@x.com"), fail=RuntimeError("down")))
    assert "WARNING" in out and "does not leave out anyone who has opted out" in out and "a@x.com" in out and "b@x.com" in out


def test_the_preview_of_a_plain_text_campaign_never_reads_the_list(tmp_path, monkeypatch, capsys):
    sheets = ListSheets(leads_of("a@x.com"), fail=RuntimeError("must not be read"))
    out = run_preview(tmp_path, monkeypatch, capsys, text_cfg(tmp_path), sheets)
    assert sheets.read_count == 0 and "a@x.com" in out and "WARNING" not in out


# ================================================================ marking leads as unsubscribed (the function itself)

def test_marking_returns_exactly_the_leads_it_changed_and_updates_their_records_in_place(tmp_path):
    leads = leads_of("a@x.com", "b@x.com", "c@x.com")
    sheets = ListSheets([dict(lead) for lead in leads])
    pairs = ul.load_pairs([opted_out("a@x.com"), opted_out("c@x.com")])
    changed = outreach.reconcile_unsubscribes(html_cfg(tmp_path), sheets, leads, pairs)
    assert [lead["Email"] for lead in changed] == ["a@x.com", "c@x.com"]
    assert leads[0]["Status"] == "Stopped - Unsubscribed" and leads[2]["Status"] == "Stopped - Unsubscribed"                  # the records handed in are updated too
    assert all(lead["Status"] == "Stopped - Unsubscribed" and lead["LastActionAt"] for lead in changed) and leads[1].get("Status") != "Stopped - Unsubscribed"
    assert [row["Status"] for row in sheets._leads if row.get("Status") == "Stopped - Unsubscribed"] == ["Stopped - Unsubscribed", "Stopped - Unsubscribed"]


def test_marking_twice_changes_nothing_the_second_time(tmp_path):
    leads = leads_of("a@x.com")
    sheets = ListSheets([dict(lead) for lead in leads])
    pairs = ul.load_pairs([opted_out("a@x.com")])
    assert len(outreach.reconcile_unsubscribes(html_cfg(tmp_path), sheets, leads, pairs)) == 1
    assert outreach.reconcile_unsubscribes(html_cfg(tmp_path), sheets, leads, pairs) == []


def test_marking_skips_a_lead_with_no_row_number_and_one_for_another_brand(tmp_path):
    leads = [{"Email": "a@x.com"}, make_lead(_row=3, Email="b@x.com")]
    pairs = ul.load_pairs([opted_out("a@x.com"), opted_out("b@x.com", key="duberobe")])
    assert outreach.reconcile_unsubscribes(html_cfg(tmp_path), ListSheets([]), leads, pairs) == []



# ================================================================ the page check in a test run: can an outsider open it?

import http.server  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

SEEN = []


class Google(http.server.BaseHTTPRequestHandler):
    """A local web server that plays each answer Google might give to a visitor who is not signed in."""

    def log_message(self, *args):
        pass

    def reply(self, code, body="", location=None, headers=()):
        SEEN.append({"method": self.command, "path": self.path, "headers": dict(self.headers), "body": getattr(self, "request_body", "")})
        self.send_response(code)
        if location:
            self.send_header("Location", location)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def do_POST(self):
        self.request_body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8")
        star = [("Access-Control-Allow-Origin", "*")]
        if self.path.startswith("/api/post-good"):
            return self.reply(302, location="/api/echo-post", headers=star)
        if self.path.startswith("/api/post-no-acao"):
            return self.reply(302, location="/api/echo-post")
        return self.reply(404, "")

    def do_GET(self):
        star = [("Access-Control-Allow-Origin", "*")]
        route = self.path.split("?")[0]
        if route == "/api/good":
            return self.reply(302, location="/api/echo-good", headers=star)
        if route == "/api/echo-good":
            return self.reply(200, '{"ok": true, "state": "test", "brand": "Harish"}', headers=star + [("Content-Type", "application/json")])
        if route == "/api/echo-post":
            return self.reply(200, '{"ok": true, "masked": "h***@gmail.com", "test": true}', headers=star)
        if route == "/api/no-acao-redirect":
            return self.reply(302, location="/api/echo-good")
        if route == "/api/no-acao-final":
            return self.reply(302, location="/api/echo-plain", headers=star)
        if route == "/api/echo-plain":
            return self.reply(200, '{"ok": true, "state": "test"}')
        if route == "/api/html":
            return self.reply(200, "<!doctype html><html>the page</html>", headers=star)
        if route == "/api/invalid":
            return self.reply(200, '{"ok": false, "state": "invalid"}', headers=star)
        if route == "/api/wrong-state":
            return self.reply(200, '{"ok": true, "state": "valid"}', headers=star)
        if route == "/page/good/unsubscribe.html":
            return self.reply(200, "<!doctype html><!-- unsubscribe-page-v1 --><html></html>")
        if route == "/page/other/unsubscribe.html":
            return self.reply(200, "<html>some other page</html>")
        if route == "/page/good/config.js":
            return self.reply(200, 'window.UNSUBSCRIBE_API = "https://script.google.com/macros/s/AKfycbyID/exec";')
        if route == "/page/other/config.js":
            return self.reply(200, 'window.UNSUBSCRIBE_API = "https://script.google.com/macros/s/OTHER/exec";')
        if route == "/page/empty/config.js":
            return self.reply(200, "var nothing = 1;")
        routes = {"/valid": (200, "<html><div>This link is not valid</div></html>"), "/plain": (200, "<html>hello</html>"),
                  "/error-page": (200, "<html>Sorry, unable to open the file at present. Please check the address and try again.</html>"), "/missing": (404, "<html>Not found</html>"),
                  "/forbidden": (403, "<html>no</html>"), "/server-error": (500, "<html>oops</html>"), "/accounts.google.com/ServiceLogin": (200, "<html>Sign in</html>")}
        if self.path.split("?")[0] in routes:
            return self.reply(*routes[self.path.split("?")[0]])
        if self.path.startswith("/gate"):
            return self.reply(200, "<html>Sorry, unable to open the file at present. Please check the address and try again.</html>") if "t=" in self.path else self.reply(200, "<html><div>This link is not valid</div></html>")
        if self.path.startswith("/wrapped"):
            return self.reply(200, "<html><script>goog.script.init(\"\\x3cdiv class\\x3d\\x22bd\\x22\\x3e\\x3ch1\\x3eUnsubscribe\\x3c/h1\\x3e\\x3cbutton\\x3eUnsubscribe me\\x3c/button\\x3e\", \"\")</script></html>")
        port = self.server.server_address[1]
        if self.path.startswith("/t/start"):
            return self.reply(302, location="/t/wrapper?t=SECRETTOKEN", headers=[("Set-Cookie", "NID=abc; Path=/")])
        if self.path.startswith("/t/wrapper"):
            return self.reply(200, f'<html><script>goog.script.init("\\x3cdiv\\x3eUnsubscribe me\\x3c/div\\x3e http:\\/\\/127.0.0.1:{port}\\/t\\/frame\\x3fa\\x3d1")</script></html>', headers=[("Set-Cookie", "GAPS=xyz; Path=/"), ("X-Frame-Options", "SAMEORIGIN")])
        if self.path.startswith("/t/frame-blocked-wrapper"):
            return self.reply(200, f'<html>Unsubscribe me http://127.0.0.1:{port}/t/blocked</html>')
        if self.path.startswith("/t/blocked"):
            return self.reply(200, "<html>Sorry, unable to open the file at present. Please check the address and try again.</html>")
        if self.path.startswith("/t/frame"):
            ok = "NID=abc" in (self.headers.get("Cookie") or "") and (self.headers.get("Referer") or "") == f"http://127.0.0.1:{port}/"
            return self.reply(200, "<html>frame</html>") if ok else self.reply(403, "<html>no cookie or referrer</html>")
        if self.path.startswith("/t/no-frame"):
            return self.reply(200, "<html>Unsubscribe me</html>")
        if self.path.startswith("/t/loop"):
            return self.reply(302, location="/t/loop")
        if self.path.startswith("/t/to-signin"):
            return self.reply(302, location="/accounts.google.com/ServiceLogin")
        if self.path.startswith("/to-valid"):
            return self.reply(302, location="/valid")
        if self.path.startswith("/to-signin"):
            return self.reply(302, location="/accounts.google.com/ServiceLogin")
        if self.path.startswith("/slow"):
            time.sleep(3)
            return self.reply(200, "late")
        return self.reply(404, "")


@pytest.fixture
def google():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Google)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    SEEN.clear()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.mark.parametrize("path, state", [("/valid", "ok"), ("/plain", "ok"), ("/to-valid", "ok"), ("/error-page", "blocked"), ("/missing", "blocked"), ("/forbidden", "blocked"),
                                         ("/to-signin", "signin"), ("/server-error", "unknown")])
def test_the_real_check_reports_what_a_visitor_who_is_not_signed_in_would_get(google, path, state):
    assert REAL_PROBE(google + path)[0] == state


def test_the_real_check_sends_nothing_that_would_make_it_more_than_an_outsider(google):
    REAL_PROBE(google + "/valid?x=1")
    sent = {name.lower() for name in SEEN[0]["headers"]}
    assert "authorization" not in sent and "cookie" not in sent and SEEN[0]["headers"]["User-Agent"].startswith("Mozilla/5.0")


def test_the_real_check_follows_redirects_as_a_browser_does(google):
    state, message = REAL_PROBE(google + "/to-valid")
    assert state == "ok" and "answered as the unsubscribe page" in message and [hit["path"] for hit in SEEN] == ["/to-valid", "/valid"]


def test_a_slow_or_unreachable_page_is_unknown_never_blocked(google):
    assert REAL_PROBE(google + "/slow", timeout=1)[0] == "unknown"
    assert REAL_PROBE("http://127.0.0.1:1/never")[0] == "unknown"


def run_command(tmp_path, monkeypatch, capsys, fmt="html"):
    _capture_smtp(monkeypatch)
    monkeypatch.setattr(outreach, "load_email_accounts", lambda: ACCOUNTS)
    monkeypatch.setattr(outreach, "get_campaign", lambda name, **kw: _test_cfg(tmp_path, fmt))
    try:
        outreach.cmd_send_test(argparse.Namespace(campaign="C", stage="intro", variant="A", to="team@kelson.agency", account=""))
        code = 0
    except SystemExit as exit_:
        code = exit_.code
    return capsys.readouterr().out, code


def stub_probe(monkeypatch, state, message="stub"):
    monkeypatch.setattr(outreach, "probe_unsubscribe_page", lambda url, timeout=20, expect=(): (state, message))


def test_a_test_run_opens_the_page_address_and_reports_it_next_to_the_address_in_use(tmp_path, monkeypatch, capsys, page_on, no_network):
    out, code = run_command(tmp_path, monkeypatch, capsys)
    assert code == 0 and len(no_network) == 2 and no_network[0] == PAGE
    assert f"Page address in use: {PAGE}" in out and "Check 1, the page address on its own (opened from GitHub without signing in): OK." in out and "t=" not in out.split("Page address in use:")[1].split("\n")[0]


def test_when_google_refuses_the_page_the_run_says_so_clearly_and_fails_but_the_email_was_sent(tmp_path, monkeypatch, capsys, page_on):
    stub_probe(monkeypatch, "blocked", "Google answered 'Sorry, unable to open the file at present'. Fix: Deploy > Manage deployments")
    out, code = run_command(tmp_path, monkeypatch, capsys)
    assert code == 1 and "Test email sent to team@kelson.agency" in out and len(_Sent.calls) == 1
    assert "Check 1, the page address on its own (opened from GitHub without signing in): BLOCKED." in out and "PROBLEM: recipients would not be able to open the unsubscribe page" in out and "the test email itself was sent" in out


def test_a_page_that_wants_a_google_sign_in_also_fails_the_run(tmp_path, monkeypatch, capsys, page_on):
    stub_probe(monkeypatch, "signin", "Google asked for a sign-in")
    out, code = run_command(tmp_path, monkeypatch, capsys)
    assert code == 1 and "SIGNIN." in out and "PROBLEM" in out


def test_a_check_that_could_not_be_made_is_reported_but_does_not_fail_the_run(tmp_path, monkeypatch, capsys, page_on):
    stub_probe(monkeypatch, "unknown", "could not check the page from here (timed out).")
    out, code = run_command(tmp_path, monkeypatch, capsys)
    assert code == 0 and "UNKNOWN. could not check the page from here" in out and "PROBLEM" not in out


def test_a_workspace_address_is_used_but_with_its_warning(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("UNSUBSCRIBE_URL", "https://script.google.com/a/macros/kelson.agency/s/ID/exec")
    monkeypatch.setenv("UNSUBSCRIBE_SECRET", SECRET)
    out, code = run_command(tmp_path, monkeypatch, capsys)
    assert code == 0 and "Unsubscribe page: ON" in out and "WARNING: UNSUBSCRIBE_URL contains /a/macros/" in out and len(_Sent.calls) == 1 and "unsubscribe_url" in _Sent.calls[0]


def test_googles_test_address_turns_the_page_off_with_the_reason_and_the_email_still_goes_out_the_old_way(tmp_path, monkeypatch, capsys, no_network):
    monkeypatch.setenv("UNSUBSCRIBE_URL", "https://script.google.com/macros/s/ID/dev")
    monkeypatch.setenv("UNSUBSCRIBE_SECRET", SECRET)
    out, code = run_command(tmp_path, monkeypatch, capsys)
    assert code == 0 and "Unsubscribe page: OFF. UNSUBSCRIBE_URL ends in /dev, which is Google's TEST address" in out
    assert no_network == [] and "unsubscribe_url" not in _Sent.calls[0] and "html_body" in _Sent.calls[0]


def test_the_check_is_not_made_when_the_page_is_off_or_for_a_plain_text_campaign(tmp_path, monkeypatch, capsys, no_network):
    run_command(tmp_path, monkeypatch, capsys)
    monkeypatch.setenv("UNSUBSCRIBE_URL", PAGE)
    monkeypatch.setenv("UNSUBSCRIBE_SECRET", SECRET)
    run_command(tmp_path, monkeypatch, capsys, fmt="text")
    assert no_network == []


def test_a_real_send_never_makes_the_page_check(tmp_path, monkeypatch, page_on, no_network):
    send(html_cfg(tmp_path), ListSheets(leads_of("a@x.com", "b@x.com")), monkeypatch)
    assert no_network == [] and len(_Sent.calls) == 2


def test_the_test_address_in_a_real_send_means_a_plain_email_unsubscribe_and_never_a_link_that_only_the_owner_can_open(tmp_path, monkeypatch):
    monkeypatch.setenv("UNSUBSCRIBE_URL", "https://script.google.com/macros/s/ID/dev")
    monkeypatch.setenv("UNSUBSCRIBE_SECRET", SECRET)
    send(html_cfg(tmp_path), ListSheets(leads_of("a@x.com")), monkeypatch)
    assert "unsubscribe_url" not in _Sent.calls[0] and "html_body" in _Sent.calls[0]



# ================================================================ the EXACT link in the email is what gets checked, and printed

def real_links(tmp_path, monkeypatch, capsys):
    out, code = run_command(tmp_path, monkeypatch, capsys)
    return out, code, _Sent.calls[0]


def test_the_check_opens_the_exact_link_that_is_in_the_email_not_just_the_bare_address(tmp_path, monkeypatch, capsys, page_on, no_network):
    out, code, call = real_links(tmp_path, monkeypatch, capsys)
    exact = call["unsubscribe_url"]
    assert no_network == [PAGE, exact] and exact.startswith(PAGE + "?t=") and len(exact) > len(PAGE) + 100
    assert exact in call["html_body"].replace("&amp;", "&") and exact in call["body"]                       # the very same string is in the email's HTML and plain-text copy


def test_the_run_prints_the_whole_link_so_it_can_be_compared_character_by_character_with_the_one_in_the_email(tmp_path, monkeypatch, capsys, page_on):
    out, code, call = real_links(tmp_path, monkeypatch, capsys)
    assert f"Link in this test email (exact): {call['unsubscribe_url']}" in out


def test_the_run_reports_the_links_anatomy(tmp_path, monkeypatch, capsys, page_on):
    out, code, call = real_links(tmp_path, monkeypatch, capsys)
    line = next(l for l in out.splitlines() if l.startswith("Link check:"))
    assert "starts with the address in use: YES" in line and f"length {len(call['unsubscribe_url'])} characters" in line
    assert "only safe characters in the token: YES" in line and "the same link in the footer, the header and the plain-text copy: YES" in line


def test_both_checks_are_reported_separately_with_the_exact_link_expecting_the_real_page(tmp_path, monkeypatch, capsys, page_on):
    seen = []
    monkeypatch.setattr(outreach, "probe_unsubscribe_page", lambda url, timeout=20, expect=(): seen.append((url, expect)) or ("ok", "stub"))
    out, code = run_command(tmp_path, monkeypatch, capsys)
    assert seen[0] == (PAGE, ()) and seen[1][0].startswith(PAGE + "?t=") and seen[1][1] == ul.LINK_MARKERS
    assert "Check 1, the page address on its own" in out and "Check 2, the exact link in this email (opened the same way): OK. stub" in out


def stub_two(monkeypatch, bare, exact):
    answers = iter([bare, exact])
    monkeypatch.setattr(outreach, "probe_unsubscribe_page", lambda url, timeout=20, expect=(): next(answers))


def test_when_the_address_opens_but_the_exact_link_does_not_the_run_says_the_link_is_the_problem_and_fails(tmp_path, monkeypatch, capsys, page_on):
    stub_two(monkeypatch, ("ok", "served"), ("blocked", "Google answered 'unable to open the file'"))
    out, code = run_command(tmp_path, monkeypatch, capsys)
    assert code == 1 and "Check 1, the page address on its own (opened from GitHub without signing in): OK." in out and "Check 2, the exact link in this email (opened the same way): BLOCKED." in out
    assert "the address on its own opens but the exact link in the email does NOT" in out and "problem is the link" in out and "PROBLEM" in out


def test_when_both_open_the_run_passes_and_points_at_the_recipients_browser_or_google_sign_in(tmp_path, monkeypatch, capsys, page_on):
    stub_two(monkeypatch, ("ok", "served"), ("ok", "served the unsubscribe page itself"))
    out, code = run_command(tmp_path, monkeypatch, capsys)
    assert code == 0 and "BOTH open for an anonymous visitor" in out and "several Google accounts signed in at once" in out and "private window" in out and "PROBLEM" not in out


def test_when_the_address_itself_is_refused_the_diagnosis_blames_the_deployment(tmp_path, monkeypatch, capsys, page_on):
    stub_two(monkeypatch, ("blocked", "refused"), ("blocked", "refused"))
    out, code = run_command(tmp_path, monkeypatch, capsys)
    assert code == 1 and "the page address itself is refused" in out and "the deployment or the address in the secret" in out


def test_when_the_exact_link_needs_a_google_sign_in_the_run_fails_too(tmp_path, monkeypatch, capsys, page_on):
    stub_two(monkeypatch, ("ok", "served"), ("signin", "asked for a sign-in"))
    out, code = run_command(tmp_path, monkeypatch, capsys)
    assert code == 1 and "SIGNIN." in out and "problem is the link" in out


def test_a_check_that_cannot_be_made_gives_no_verdict_and_does_not_fail(tmp_path, monkeypatch, capsys, page_on):
    stub_two(monkeypatch, ("ok", "served"), ("unknown", "could not check the page from here (timed out)."))
    out, code = run_command(tmp_path, monkeypatch, capsys)
    assert code == 0 and "not conclusive" in out and "PROBLEM" not in out


def test_the_real_check_requests_the_link_verbatim_including_its_whole_token_and_still_sends_no_cookies(google, page_on):
    token = "x" * 330 + ".sig_-9"
    REAL_PROBE(google + "/valid?t=" + token, expect=ul.LINK_MARKERS)
    assert SEEN[0]["path"] == "/valid?t=" + token and "cookie" not in {name.lower() for name in SEEN[0]["headers"]} and "authorization" not in {name.lower() for name in SEEN[0]["headers"]}


def test_a_page_that_opens_bare_but_refuses_the_same_address_with_a_token_is_told_apart_by_the_two_checks(google):
    bare, exact = REAL_PROBE(google + "/gate"), REAL_PROBE(google + "/gate?t=abc.def", expect=ul.LINK_MARKERS)
    assert bare[0] == "ok" and exact[0] == "blocked" and "problem is the link" in ul.compare_probes(bare[0], exact[0])


def test_the_real_check_recognises_the_real_page_text_when_google_delivers_it_inside_a_script_string(google):
    state, message = REAL_PROBE(google + "/wrapped?t=abc", expect=ul.LINK_MARKERS)
    assert state == "ok" and "served the unsubscribe page itself" in message and "t=abc" not in message               # and the token is never echoed back in the message


def test_an_ok_answer_without_the_pages_text_says_it_cannot_confirm_more(google):
    state, message = REAL_PROBE(google + "/plain?t=abc", expect=ul.LINK_MARKERS)
    assert state == "ok" and "could not see the unsubscribe page's own text" in message


# ================================================================ nothing about the link changes between the engine and the recipient

def test_a_realistically_long_link_leaves_the_sender_identical_in_the_header_the_html_and_the_plain_text(tmp_path, monkeypatch, page_on):
    import re as regex
    cfg = html_cfg(tmp_path, name="Kelson_Creators_Licensing_17SEP", business="Kelson Agency")
    cfg["footer"]["address"] = "12 Example Street, Suite 400, New York, NY 10001, United States"
    lead = make_lead(LeadID="L-123456", Email="a.recipient+newsletter@some-company.co.uk", FirstName="Ann")
    rendered = outreach.render_email_html(str(tmp_path), "intro", "A", lead, cfg)
    url = rendered["unsubscribe_url"]
    msg, _ = outreach._build_outbound_message("sales@kelson.agency", lead["Email"], "S", hc.html_to_text(rendered["html"]), html_body=rendered["html"], unsubscribe_url=url)
    received = email.message_from_bytes(msg.as_bytes(), policy=email.policy.default)
    header = regex.sub(r"\r?\n(?=[ \t])", "", received.get_all("List-Unsubscribe")[0] if False else email.message_from_bytes(msg.as_bytes())["List-Unsubscribe"])
    inside = regex.match(r"\s*<([^>]*)>,\s*<mailto:[^>]+>\s*$", header)
    assert inside and inside.group(1) == url and not regex.search(r"\s", url) and len(url) < 900
    html_part = received.get_body(preferencelist=("html",)).get_content()
    text_part = received.get_body(preferencelist=("plain",)).get_content()
    assert regex.search(r'href="(https://script[^"]+)"', html_part).group(1) == url and url in text_part
    assert max(len(line) for line in text_part.splitlines()) < 998 and all(part.get("Content-Transfer-Encoding") == "base64" for part in received.iter_parts())
    folded = msg.as_string().split("List-Unsubscribe:")[1].split("List-Unsubscribe-Post")[0]
    assert any(url in physical for physical in folded.split("\n"))                                           # the link is never split across two physical lines


# ================================================================ the link check says NO when the link really is wrong

def run_with_link(tmp_path, monkeypatch, capsys, link, in_every_copy=True):
    real = outreach.send_test_email

    def fake(*args, **kwargs):
        sent = real(*args, **kwargs)
        return {**sent, "unsubscribe_url": link, "link_in_every_copy": in_every_copy}
    monkeypatch.setattr(outreach, "send_test_email", fake)
    out, code = run_command(tmp_path, monkeypatch, capsys)
    return next(line for line in out.splitlines() if line.startswith("Link check:"))


def test_the_link_check_says_no_when_the_link_does_not_start_with_the_address_in_use(tmp_path, monkeypatch, capsys, page_on):
    line = run_with_link(tmp_path, monkeypatch, capsys, "https://other.example/exec?t=abc.def")
    assert "starts with the address in use: NO" in line and "only safe characters in the token: NO" in line


def test_the_link_check_says_no_when_the_token_has_a_character_that_a_mail_app_could_change(tmp_path, monkeypatch, capsys, page_on):
    for bad in ("abc def", "abc)def", "abc,def", "abc=def", "abc%20def", "abc\u00e9def", "abc!def"):
        line = run_with_link(tmp_path, monkeypatch, capsys, f"{PAGE}?t={bad}")
        assert "starts with the address in use: YES" in line and "only safe characters in the token: NO" in line, bad


def test_the_link_check_says_yes_for_every_character_a_token_may_hold(tmp_path, monkeypatch, capsys, page_on):
    line = run_with_link(tmp_path, monkeypatch, capsys, f"{PAGE}?t=AZaz09._-.AZaz09")
    assert "starts with the address in use: YES" in line and "only safe characters in the token: YES" in line


def test_the_link_check_says_no_when_the_link_is_not_in_every_copy(tmp_path, monkeypatch, capsys, page_on):
    assert "the same link in the footer, the header and the plain-text copy: NO" in run_with_link(tmp_path, monkeypatch, capsys, f"{PAGE}?t=abc.def", in_every_copy=False)
    assert "the same link in the footer, the header and the plain-text copy: YES" in run_with_link(tmp_path, monkeypatch, capsys, f"{PAGE}?t=abc.def", in_every_copy=True)


def test_the_real_copies_flag_is_yes_with_the_footer_and_no_for_a_design_whose_body_has_no_link(tmp_path, monkeypatch, page_on):
    _capture_smtp(monkeypatch)
    with_footer = outreach.send_test_email(_test_cfg(tmp_path, "html"), ACCOUNTS, "intro", "A", "team@kelson.agency")
    assert with_footer["link_in_every_copy"] is True
    cfg = _test_cfg(tmp_path, "html")
    (tmp_path / "intro_A.html").write_text('<p>Hi {{ FirstName }} <a href="https://x.example/stop">unsubscribe</a></p>', encoding="utf-8")
    cfg["footer"] = {}
    without = outreach.send_test_email(cfg, ACCOUNTS, "intro", "A", "team@kelson.agency")
    assert without["unsubscribe_page"] is True and without["link_in_every_copy"] is False                    # the header carries the link; the body, by the designer's choice, does not



# ================================================================ the browser-style trace

def trace(base, path, **kw):
    return REAL_TRACE(base + path, frame_hosts=("127.0.0.1",), expect=ul.LINK_MARKERS, **kw)


def test_the_trace_follows_each_redirect_one_step_at_a_time_then_asks_for_the_content_frame(google):
    steps = trace(google, "/t/start?t=SECRETTOKEN")
    assert [(s["kind"], s["status"], s["state"]) for s in steps] == [("hop", 302, "ok"), ("hop", 200, "ok"), ("frame", 200, "ok")]
    assert steps[0]["location"].endswith("/t/wrapper") and steps[0]["cookies"] == ["NID"] and steps[1]["cookies"] == ["GAPS"] and steps[1]["frame_options"] == "SAMEORIGIN"
    assert [hit["path"] for hit in SEEN] == ["/t/start?t=SECRETTOKEN", "/t/wrapper?t=SECRETTOKEN", "/t/frame?a=1"]


def test_the_content_frame_is_requested_with_the_cookies_so_far_and_only_the_origin_as_referrer(google):
    steps = trace(google, "/t/start")
    frame_request = next(hit for hit in SEEN if hit["path"].startswith("/t/frame"))
    assert "NID=abc" in frame_request["headers"]["Cookie"] and frame_request["headers"]["Referer"] == google + "/" and steps[-1]["state"] == "ok"


def test_a_server_that_wants_the_cookie_refuses_a_check_that_does_not_keep_cookies(google):
    SEEN.clear()
    assert REAL_PROBE(google + "/t/frame?a=1")[0] == "blocked"                                # the same address, asked for without the cookie and the referrer


def test_the_trace_never_shows_a_links_token_in_any_step(google):
    steps = trace(google, "/t/start?t=SECRETTOKEN")
    assert "SECRETTOKEN" not in repr(steps) and "SECRETTOKEN" not in "\n".join(ul.format_trace(steps)) and "SECRETTOKEN" not in ul.trace_verdict(steps)


def test_a_refused_content_frame_is_named_as_the_second_request(google):
    steps = trace(google, "/t/frame-blocked-wrapper")
    assert [s["state"] for s in steps] == ["ok", "blocked"] and steps[1]["kind"] == "frame"
    assert "refused at the page's own content frame" in ul.trace_verdict(steps)


def test_an_answer_with_no_content_frame_says_the_second_request_could_not_be_tested(google):
    steps = trace(google, "/t/no-frame")
    assert [s["kind"] for s in steps] == ["hop"] and "No separate content frame was found" in ul.trace_verdict(steps)


def test_a_redirect_loop_stops_at_the_limit_instead_of_running_forever(google):
    steps = trace(google, "/t/loop", max_hops=4)
    assert len(steps) == 4 and all(s["status"] == 302 for s in steps)


def test_a_redirect_to_a_google_sign_in_is_reported_and_followed_no_further(google):
    steps = trace(google, "/t/to-signin")
    assert [s["state"] for s in steps] == ["signin"] and "refused at the first request" in ul.trace_verdict(steps)


def test_an_unreachable_address_is_one_step_that_could_not_be_made(google):
    steps = REAL_TRACE("http://127.0.0.1:1/never", frame_hosts=("127.0.0.1",))
    assert len(steps) == 1 and steps[0]["state"] == "unknown" and steps[0]["error"] and "not conclusive" in ul.trace_verdict(steps)


def test_a_slow_answer_is_a_step_that_could_not_be_made_not_a_refusal(google):
    steps = REAL_TRACE(google + "/slow", timeout=1)
    assert steps[0]["state"] == "unknown" and "error" in steps[0]


def stub_trace(monkeypatch, steps):
    monkeypatch.setattr(outreach, "trace_unsubscribe_link", lambda url, timeout=20, expect=(), frame_hosts=ul.FRAME_HOSTS, max_hops=6: steps)


OK_STEPS = [{"kind": "hop", "where": "script.google.com/macros/s/ID/exec", "status": 200, "state": "ok", "size": 5000, "content_type": "text/html; charset=utf-8", "location": "", "cookies": ["NID"], "frame_options": ""},
            {"kind": "frame", "where": "n-1-script.googleusercontent.com/userCodeAppPanel", "status": 200, "state": "ok", "size": 900, "content_type": "text/html", "location": "", "cookies": [], "frame_options": ""}]


def test_the_run_prints_the_trace_and_its_verdict_and_stays_green_when_every_step_opens(tmp_path, monkeypatch, capsys, page_on):
    stub_trace(monkeypatch, OK_STEPS)
    out, code = run_command(tmp_path, monkeypatch, capsys)
    assert code == 0 and "Browser-style trace of the exact link (cookies kept, each redirect followed one step at a time, then the page's own content frame requested):" in out
    assert "  step 1 (request) script.google.com/macros/s/ID/exec -> HTTP 200 [OK]" in out and "step 2 (content frame) n-1-script.googleusercontent.com/userCodeAppPanel -> HTTP 200 [OK]" in out
    assert "TRACE VERDICT: every step opened, including the page's own content frame." in out and "NOT TESTED HERE: a request made while signed in to a Google account" in out and "PROBLEM" not in out


def test_a_refused_content_frame_turns_the_run_red_even_when_both_earlier_checks_were_ok(tmp_path, monkeypatch, capsys, page_on):
    stub_trace(monkeypatch, [OK_STEPS[0], {**OK_STEPS[1], "status": 200, "state": "blocked"}])
    out, code = run_command(tmp_path, monkeypatch, capsys)
    assert code == 1 and "step 2 (content frame)" in out and "[BLOCKED]" in out and "TRACE VERDICT: refused at the page's own content frame" in out and "PROBLEM" in out and len(_Sent.calls) == 1


def test_a_trace_that_could_not_be_made_is_reported_but_does_not_fail_the_run(tmp_path, monkeypatch, capsys, page_on):
    stub_trace(monkeypatch, [{"kind": "hop", "where": "script.google.com/x", "error": "timed out", "state": "unknown"}])
    out, code = run_command(tmp_path, monkeypatch, capsys)
    assert code == 0 and "could not be made (timed out)" in out and "TRACE VERDICT: not conclusive" in out and "PROBLEM" not in out


def test_the_trace_runs_on_the_exact_link_in_the_email_and_only_when_the_page_is_on(tmp_path, monkeypatch, capsys, page_on):
    seen = []
    monkeypatch.setattr(outreach, "trace_unsubscribe_link", lambda url, timeout=20, expect=(), frame_hosts=ul.FRAME_HOSTS, max_hops=6: seen.append((url, expect)) or OK_STEPS)
    run_command(tmp_path, monkeypatch, capsys)
    assert len(seen) == 1 and seen[0][0] == _Sent.calls[0]["unsubscribe_url"] and seen[0][1] == ul.LINK_MARKERS
    seen.clear()
    monkeypatch.delenv("UNSUBSCRIBE_URL")
    monkeypatch.delenv("UNSUBSCRIBE_SECRET")
    run_command(tmp_path, monkeypatch, capsys)
    run_command(tmp_path, monkeypatch, capsys, fmt="text")
    assert seen == []


# ================================================================ an address copied from a browser never reaches an email

BROWSER_ADDRESS = "https://script.google.com/macros/u/6/s/AKfycbyID/exec"
CLEAN_ADDRESS = "https://script.google.com/macros/s/AKfycbyID/exec"


@pytest.fixture
def browser_copied(monkeypatch):
    monkeypatch.setenv("UNSUBSCRIBE_URL", BROWSER_ADDRESS)
    monkeypatch.setenv("UNSUBSCRIBE_SECRET", SECRET)


def test_a_link_made_from_an_address_with_an_account_position_has_the_clean_path(tmp_path, browser_copied):
    link = outreach.unsubscribe_page_link(html_cfg(tmp_path), make_lead(LeadID="L1", Email="a@x.com"))
    assert link.startswith(CLEAN_ADDRESS + "?t=") and "/u/6/" not in link and ul.verify_token(SECRET, link.split("?t=")[1])["e"] == "a@x.com"


def test_every_copy_of_the_link_in_a_real_email_has_the_clean_path(tmp_path, monkeypatch, browser_copied):
    send(html_cfg(tmp_path), ListSheets(leads_of("a@x.com")), monkeypatch)
    call = _Sent.calls[0]
    assert call["unsubscribe_url"].startswith(CLEAN_ADDRESS + "?t=") and "/u/6/" not in call["html_body"] and "/u/6/" not in call["body"] and call["unsubscribe_url"] in call["body"]
    msg, _ = outreach._build_outbound_message("sales@kelson.agency", "a@x.com", "S", call["body"], html_body=call["html_body"], unsubscribe_url=call["unsubscribe_url"])
    assert "/u/6/" not in msg.as_string()


def test_the_test_run_says_the_address_was_tidied_prints_the_clean_address_and_checks_the_clean_link(tmp_path, monkeypatch, capsys, browser_copied, no_network):
    out, code = run_command(tmp_path, monkeypatch, capsys)
    assert code == 0 and f"Page address in use: {CLEAN_ADDRESS}" in out and "WARNING: UNSUBSCRIBE_URL contained /u/6/" in out and "/u/6/" not in out.split("WARNING:")[0]
    assert no_network[0] == CLEAN_ADDRESS and no_network[1].startswith(CLEAN_ADDRESS + "?t=") and "starts with the address in use: YES" in out
    assert "/macros/u/6/" not in "".join(no_network)


def test_the_test_address_with_an_account_position_still_turns_the_page_off(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("UNSUBSCRIBE_URL", "https://script.google.com/macros/u/6/s/AKfycbyID/dev")
    monkeypatch.setenv("UNSUBSCRIBE_SECRET", SECRET)
    out, code = run_command(tmp_path, monkeypatch, capsys)
    assert code == 0 and "Unsubscribe page: OFF. UNSUBSCRIBE_URL ends in /dev" in out and "unsubscribe_url" not in _Sent.calls[0]



# ================================================================ the static page on GitHub Pages: links, emails and checks

STATIC_PAGE = "https://unsubscribe.kelson.agency/"
GOOGLE = "https://script.google.com/macros/s/AKfycbyID/exec"


@pytest.fixture
def pages_on(monkeypatch):
    monkeypatch.setenv("UNSUBSCRIBE_URL", GOOGLE)
    monkeypatch.setenv("UNSUBSCRIBE_SECRET", SECRET)
    monkeypatch.setenv("UNSUBSCRIBE_PAGE_URL", STATIC_PAGE)


def test_with_a_page_address_the_footer_link_goes_to_github_pages_and_the_header_link_stays_on_google(tmp_path, pages_on):
    header, footer = outreach.unsubscribe_links(html_cfg(tmp_path), make_lead(LeadID="L1", Email="a@x.com"))
    assert header.startswith(GOOGLE + "?t=") and footer.startswith(STATIC_PAGE + "?t=") and header.split("?t=")[1] == footer.split("?t=")[1]
    assert ul.verify_token(SECRET, footer.split("?t=")[1])["e"] == "a@x.com"


def test_without_a_page_address_both_links_are_the_google_page_exactly_as_before(tmp_path, page_on):
    header, footer = outreach.unsubscribe_links(html_cfg(tmp_path), make_lead(LeadID="L1", Email="a@x.com"))
    assert header == footer and header.startswith(PAGE + "?t=")


def test_the_link_for_the_mail_apps_header_is_always_the_google_address(tmp_path, pages_on):
    assert outreach.unsubscribe_page_link(html_cfg(tmp_path), make_lead(LeadID="L1", Email="a@x.com")).startswith(GOOGLE + "?t=")


def test_an_unusable_page_address_means_the_footer_stays_on_the_google_page(tmp_path, monkeypatch, page_on):
    monkeypatch.setenv("UNSUBSCRIBE_PAGE_URL", "https://script.google.com/macros/s/X/exec")
    header, footer = outreach.unsubscribe_links(html_cfg(tmp_path), make_lead(LeadID="L1", Email="a@x.com"))
    assert header == footer and header.startswith(PAGE + "?t=")


def test_the_design_gets_the_github_pages_link_in_its_footer_and_plain_text(tmp_path, pages_on):
    out = outreach.render_email_html(str(tmp_path), "intro", "A", make_lead(FirstName="Ann", LeadID="L1", Email="a@x.com"), html_cfg(tmp_path))
    assert out["unsubscribe_footer_url"].startswith(STATIC_PAGE + "?t=") and out["unsubscribe_url"].startswith(GOOGLE + "?t=")
    assert f'href="{out["unsubscribe_footer_url"]}"' in out["html"] and out["unsubscribe_url"] not in out["html"] and hc.UNSUBSCRIBE_TOKEN not in out["html"]
    assert f"Unsubscribe ({out['unsubscribe_footer_url']})" in hc.html_to_text(out["html"])


def test_a_real_email_has_the_pages_link_in_the_footer_and_the_google_link_in_the_one_tap_header(tmp_path, monkeypatch, pages_on):
    send(html_cfg(tmp_path), ListSheets(leads_of("a@x.com")), monkeypatch)
    call = _Sent.calls[0]
    assert call["unsubscribe_url"].startswith(GOOGLE + "?t=") and f'href="{STATIC_PAGE}?t=' in call["html_body"] and f"({STATIC_PAGE}?t=" in call["body"]
    msg, _ = outreach._build_outbound_message("sales@kelson.agency", "a@x.com", "S", call["body"], html_body=call["html_body"], unsubscribe_url=call["unsubscribe_url"])
    assert msg["List-Unsubscribe"].startswith(f"<{GOOGLE}?t=") and msg["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click" and STATIC_PAGE not in msg["List-Unsubscribe"]


def test_both_links_in_one_email_name_the_same_person_brand_and_lead(tmp_path, monkeypatch, pages_on):
    send(html_cfg(tmp_path), ListSheets(leads_of("a@x.com")), monkeypatch)
    call = _Sent.calls[0]
    footer_token = call["html_body"].split(STATIC_PAGE + "?t=")[1].split('"')[0]
    assert footer_token == call["unsubscribe_url"].split("?t=")[1] and ul.verify_token(SECRET, footer_token)["l"] == "L0"


def test_a_plain_text_campaign_gets_no_link_of_any_kind_even_with_the_page_on(tmp_path, monkeypatch, pages_on):
    assert outreach.unsubscribe_links(text_cfg(tmp_path), make_lead(LeadID="L1", Email="a@x.com")) is None
    send(text_cfg(tmp_path), ListSheets(leads_of("a@x.com")), monkeypatch)
    assert all(key not in _Sent.calls[0] for key in ("unsubscribe_url", "html_body"))


def test_the_test_email_reports_both_links_and_that_the_footer_link_is_in_every_copy(tmp_path, monkeypatch, pages_on):
    _capture_smtp(monkeypatch)
    sent = outreach.send_test_email(_test_cfg(tmp_path, "html"), ACCOUNTS, "intro", "A", "team@kelson.agency")
    assert sent["unsubscribe_url"].startswith(GOOGLE + "?t=") and sent["unsubscribe_footer_url"].startswith(STATIC_PAGE + "?t=") and sent["link_in_every_copy"] is True
    assert sent["unsubscribe_footer_url"] in _Sent.calls[0]["body"] and ul.verify_token(SECRET, ul.token_from_url(sent["unsubscribe_footer_url"]))["t"] == 1


# ---------------------------------------------------------------- the probes, against a local server that imitates Google's redirects and permission headers

TOKEN = "abc.def_-9"


@pytest.mark.parametrize("path, state", [("/page/good/unsubscribe.html", "ok"), ("/page/other/unsubscribe.html", "blocked"), ("/page/missing/unsubscribe.html", "blocked")])
def test_the_real_static_page_check_reports_what_github_pages_answers(google, path, state):
    assert REAL_STATIC(google + path)[0] == state


def test_the_real_static_page_check_sends_no_cookie_and_an_unreachable_page_is_unknown(google):
    REAL_STATIC(google + "/page/good/unsubscribe.html")
    assert "cookie" not in {name.lower() for name in SEEN[0]["headers"]} and REAL_STATIC("http://127.0.0.1:1/x")[0] == "unknown"


@pytest.mark.parametrize("folder, state, words", [("good", "ok", "same Google script"), ("other", "blocked", "points at"), ("empty", "blocked", "does not set window.UNSUBSCRIBE_API"), ("missing", "blocked", "HTTP 404")])
def test_the_real_config_check_compares_the_pages_config_with_the_secret(google, folder, state, words):
    got_state, message = REAL_CONFIG(f"{google}/page/{folder}/unsubscribe.html", "https://script.google.com/macros/s/AKfycbyID/exec")
    assert got_state == state and words in message


def test_an_unreachable_config_is_unknown(google):
    assert REAL_CONFIG("http://127.0.0.1:1/unsubscribe.html", GOOGLE)[0] == "unknown"


def test_the_real_data_request_follows_google_style_redirects_and_checks_permission_on_every_step(google):
    state, message = REAL_API(google + "/api/good", TOKEN, "https://harish.github.io")
    assert state == "ok" and "every answer (2 steps) allows a page on https://harish.github.io" in message
    assert [(hit["method"], hit["path"]) for hit in SEEN] == [("GET", f"/api/good?api=info&t={TOKEN}"), ("GET", "/api/echo-good")]
    assert all(hit["headers"]["Origin"] == "https://harish.github.io" and "Cookie" not in hit["headers"] and "cookie" not in hit["headers"] for hit in SEEN)
    assert TOKEN not in message


@pytest.mark.parametrize("path, words", [("/api/no-acao-redirect", "step 1 "), ("/api/no-acao-final", "step 2 "), ("/api/html", "web page instead of data"), ("/api/invalid", "did not accept the link"),
                                         ("/api/wrong-state", "state 'valid' where 'test' was expected")])
def test_the_real_data_request_blocks_with_the_reason_when_google_would_refuse_the_page(google, path, words):
    state, message = REAL_API(google + path, TOKEN, "https://harish.github.io")
    assert state == "blocked" and words in message


def test_the_real_confirm_request_is_a_plain_text_post_then_a_get_after_the_redirect_with_no_cookies(google):
    state, message = REAL_API(google + "/api/post-good", TOKEN, "https://harish.github.io", post=True)
    assert state == "ok"
    first, second = SEEN[0], SEEN[1]
    assert first["method"] == "POST" and first["headers"]["Content-Type"].startswith("text/plain") and json.loads(first["body"]) == {"api": "confirm", "t": TOKEN, "reason": "", "details": ""}
    assert second["method"] == "GET" and second["body"] == "" and "Content-Type" not in second["headers"] and all("Cookie" not in hit["headers"] for hit in SEEN)


def test_a_confirm_redirect_without_permission_is_blocked_and_an_unreachable_script_is_unknown(google):
    assert REAL_API(google + "/api/post-no-acao", TOKEN, "https://harish.github.io", post=True)[0] == "blocked"
    assert REAL_API("http://127.0.0.1:1/exec", TOKEN, "https://harish.github.io")[0] == "unknown"


def test_the_redirect_limit_stops_a_loop_in_the_data_request(google):
    state, message = REAL_API(google + "/loop-not-an-api", TOKEN, "https://harish.github.io", max_hops=3)
    assert state in ("blocked", "unknown")


# ---------------------------------------------------------------- the printed checks


def with_probe_results(monkeypatch, static=("ok", "GitHub Pages served the unsubscribe page (HTTP 200)."), config=("ok", "the page's config.js names the same Google script as UNSUBSCRIBE_URL."),
                       get=("ok", "every answer (2 steps) allows a page on https://unsubscribe.kelson.agency, and the last one is the script's data."), post=("ok", "every answer (2 steps) allows a page on https://unsubscribe.kelson.agency, and the last one is the script's data.")):
    calls = {"static": [], "config": [], "api": []}
    monkeypatch.setattr(outreach, "probe_static_page", lambda url, timeout=20: calls["static"].append(url) or static)
    monkeypatch.setattr(outreach, "probe_page_config", lambda page_url, expected_api, timeout=20: calls["config"].append((page_url, expected_api)) or config)
    monkeypatch.setattr(outreach, "probe_unsubscribe_api", lambda api_url, token, origin, post_flag=False, timeout=20, max_hops=6, post=False: calls["api"].append((api_url, token, origin, post)) or (post_result if post else get))
    post_result = post
    return calls


def test_the_run_prints_the_footer_link_and_the_four_static_page_checks_and_stays_green(tmp_path, monkeypatch, capsys, pages_on):
    with_probe_results(monkeypatch)
    out, code = run_command(tmp_path, monkeypatch, capsys)
    footer = _Sent.calls[0]["html_body"].split('href="')[1].split('"')[0]
    assert code == 0 and f"Footer link (what recipients click, on GitHub Pages): {footer}" in out and footer.startswith(STATIC_PAGE + "?t=")
    for label in ("A, the page is being served: OK.", "B, its config.js names the same Google script: OK.", "C, the script answers the page's data request (GET) and allows a page on https://unsubscribe.kelson.agency: OK.",
                  "D, the script accepts the page's confirm request (POST, with this TEST link: records nothing): OK."):
        assert label in out, label
    assert "PAGE VERDICT: everything the page needs works from here" in out and "NOT TESTED HERE: a real browser" in out and "PROBLEM" not in out


def test_the_checks_are_made_on_the_footer_link_the_config_the_google_address_and_the_pages_origin(tmp_path, monkeypatch, capsys, pages_on):
    calls = with_probe_results(monkeypatch)
    out, code = run_command(tmp_path, monkeypatch, capsys)
    footer = _Sent.calls[0]["html_body"].split('href="')[1].split('"')[0]
    token = ul.token_from_url(footer)
    assert calls["static"] == [footer] and calls["config"] == [(STATIC_PAGE, GOOGLE)]
    assert calls["api"] == [(GOOGLE, token, "https://unsubscribe.kelson.agency", False), (GOOGLE, token, "https://unsubscribe.kelson.agency", True)] and ul.verify_token(SECRET, token)["t"] == 1


@pytest.mark.parametrize("which, label", [("static", "A, the page is being served: BLOCKED."), ("config", "B, its config.js names the same Google script: BLOCKED."),
                                          ("get", "(GET) and allows a page on https://unsubscribe.kelson.agency: BLOCKED."), ("post", "(POST, with this TEST link: records nothing): BLOCKED.")])
def test_any_blocked_static_page_check_fails_the_run_after_the_email_was_sent_and_says_which(tmp_path, monkeypatch, capsys, pages_on, which, label):
    with_probe_results(monkeypatch, **{which: ("blocked", "the reason in plain words")})
    out, code = run_command(tmp_path, monkeypatch, capsys)
    assert code == 1 and label in out and "the reason in plain words" in out and "PAGE VERDICT: at least one thing the page needs is not working" in out and "PROBLEM" in out and len(_Sent.calls) == 1


def test_a_check_that_could_not_be_made_is_not_conclusive_and_does_not_fail_the_run(tmp_path, monkeypatch, capsys, pages_on):
    with_probe_results(monkeypatch, get=("unknown", "step 1 could not be made (timed out)."))
    out, code = run_command(tmp_path, monkeypatch, capsys)
    assert code == 0 and "PAGE VERDICT: not conclusive" in out and "UNKNOWN. step 1 could not be made" in out and "PROBLEM" not in out


def test_without_a_page_address_the_run_says_why_one_is_needed_and_makes_none_of_the_page_checks(tmp_path, monkeypatch, capsys, page_on):
    calls = with_probe_results(monkeypatch)
    out, code = run_command(tmp_path, monkeypatch, capsys)
    assert code == 0 and "Note: UNSUBSCRIBE_PAGE_URL is not set, so recipients open the Google page itself" in out and "/u/N/" in out and "PAGE VERDICT" not in out and "Static page checks" not in out
    assert calls == {"static": [], "config": [], "api": []} and "OFF" not in out


def test_an_unusable_page_address_is_reported_and_the_google_page_is_used(tmp_path, monkeypatch, capsys, page_on):
    monkeypatch.setenv("UNSUBSCRIBE_PAGE_URL", "http://not-secure.example/u.html")
    calls = with_probe_results(monkeypatch)
    out, code = run_command(tmp_path, monkeypatch, capsys)
    assert code == 0 and "WARNING:" in out and "UNSUBSCRIBE_PAGE_URL must start with https://" in out and "Google page instead" in out and calls["api"] == []


def test_a_plain_text_test_run_and_a_page_that_is_off_never_make_the_page_checks(tmp_path, monkeypatch, capsys, pages_on):
    calls = with_probe_results(monkeypatch)
    run_command(tmp_path, monkeypatch, capsys, fmt="text")
    monkeypatch.delenv("UNSUBSCRIBE_URL")
    run_command(tmp_path, monkeypatch, capsys)
    assert calls == {"static": [], "config": [], "api": []}



def test_a_github_io_page_address_is_used_but_the_test_run_warns_that_it_shows_the_github_name(tmp_path, monkeypatch, capsys, pages_on):
    monkeypatch.setenv("UNSUBSCRIBE_PAGE_URL", "https://harish-1708.github.io/E-O-A/unsubscribe.html")
    out, code = run_command(tmp_path, monkeypatch, capsys)
    footer = _Sent.calls[0]["html_body"].split('href="')[1].split('"')[0]
    assert code == 0 and footer.startswith("https://harish-1708.github.io/E-O-A/unsubscribe.html?t=") and "WARNING: UNSUBSCRIBE_PAGE_URL is a github.io address with a repository name in its path" in out and "Static page checks" in out


def test_an_address_on_your_own_domain_gives_no_warning_and_a_clean_link(tmp_path, monkeypatch, capsys, pages_on):
    out, code = run_command(tmp_path, monkeypatch, capsys)
    footer = _Sent.calls[0]["html_body"].split('href="')[1].split('"')[0]
    assert code == 0 and "WARNING" not in out and footer.startswith("https://unsubscribe.kelson.agency/?t=") and "github" not in footer and "github" not in _Sent.calls[0]["html_body"].lower()


def test_no_part_of_a_real_email_names_github_when_the_page_is_on_your_own_domain(tmp_path, monkeypatch, pages_on):
    send(html_cfg(tmp_path), ListSheets(leads_of("a@x.com")), monkeypatch)
    call = _Sent.calls[0]
    msg, _ = outreach._build_outbound_message("sales@kelson.agency", "a@x.com", "S", call["body"], html_body=call["html_body"], unsubscribe_url=call["unsubscribe_url"])
    assert "github" not in msg.as_string().lower()



def test_a_neutral_organisation_address_on_github_io_works_with_only_a_reminder_and_names_nothing_personal(tmp_path, monkeypatch, capsys, pages_on):
    monkeypatch.setenv("UNSUBSCRIBE_PAGE_URL", "https://kelson-mail.github.io/")
    out, code = run_command(tmp_path, monkeypatch, capsys)
    call = _Sent.calls[0]
    footer = call["html_body"].split('href="')[1].split('"')[0]
    assert code == 0 and footer.startswith("https://kelson-mail.github.io/?t=") and "recipients can read its name (kelson-mail.github.io)" in out and "repository" not in out.split("WARNING:")[1].split("\n")[0]
    msg, _ = outreach._build_outbound_message("sales@kelson.agency", "a@x.com", "S", call["body"], html_body=call["html_body"], unsubscribe_url=call["unsubscribe_url"])
    assert "harish-1708" not in msg.as_string().lower() and "e-o-a" not in msg.as_string().lower()



# ================================================================ the link carries a short CODE, not the campaign's name

def decoded(link):
    return json.loads(ul._unb64(ul.token_from_url(link).split(".")[0]).decode("utf-8"))


def test_the_link_carries_the_campaigns_code_and_nowhere_in_it_is_the_name(tmp_path, pages_on):
    header, footer = outreach.unsubscribe_links(html_cfg(tmp_path, name="Kelson_Creators_Licensing_17SEP"), make_lead(LeadID="L1", Email="a@x.com"))
    record = decoded(footer)
    assert record["c"] == ul.campaign_code(SECRET, "Kelson_Creators_Licensing_17SEP") and re.match(ul.CAMPAIGN_CODE_PATTERN, record["c"])
    for text in (header, footer, json.dumps(record), ul._unb64(ul.token_from_url(footer).split(".")[0]).decode()):
        assert "kelson_creators" not in text.lower() and "licensing" not in text.lower() and "17sep" not in text.lower()
    assert ul.verify_token(SECRET, ul.token_from_url(footer)) == record and record["l"] == "L1" and record["e"] == "a@x.com"


def test_two_campaigns_get_two_different_codes_and_the_same_campaign_always_the_same(tmp_path, page_on):
    def code(name, lead="L1"):
        return decoded(outreach.unsubscribe_links(html_cfg(tmp_path, name=name), make_lead(LeadID=lead, Email="a@x.com"))[0])["c"]
    assert code("Spring_Launch") != code("Autumn_Launch") and code("Spring_Launch") == code("Spring_Launch", lead="L2")


def test_no_part_of_a_real_email_names_the_campaign(tmp_path, monkeypatch, page_on):
    send(html_cfg(tmp_path, name="Kelson_Creators_Licensing_17SEP"), ListSheets(leads_of("a@x.com")), monkeypatch)
    call = _Sent.calls[0]
    msg, _ = outreach._build_outbound_message("sales@kelson.agency", "a@x.com", "S", call["body"], html_body=call["html_body"], unsubscribe_url=call["unsubscribe_url"])
    everything = (msg.as_string() + call["html_body"] + call["body"]).lower()
    assert "kelson_creators" not in everything and "17sep" not in everything and decoded(call["unsubscribe_url"])["c"] == ul.campaign_code(SECRET, "Kelson_Creators_Licensing_17SEP")


def test_a_campaign_with_no_name_gets_no_code_not_a_code_of_nothing(tmp_path, page_on):
    cfg = html_cfg(tmp_path)
    cfg.pop("_campaign_name")
    assert decoded(outreach.unsubscribe_links(cfg, make_lead(LeadID="L1", Email="a@x.com"))[0])["c"] == ""


def test_a_link_made_before_codes_existed_still_opens_and_shows_its_name():
    old = ul.make_token(SECRET, campaign="Spring_Launch", lead_id="L1", email="a@x.com", brand="B", key="b")
    assert ul.verify_token(SECRET, old)["c"] == "Spring_Launch" and not re.match(ul.CAMPAIGN_CODE_PATTERN, "Spring_Launch")


# ---------------------------------------------------------------- the code list is published before a campaign sends

class CodeSheets(ListSheets):
    def __init__(self, leads, fail=None, **kw):
        super().__init__(leads, **kw)
        self.published, self.publish_fail, self.sent_when_published = [], fail, []

    def ensure_campaign_codes(self, wanted):
        self.sent_when_published.append(len(_Sent.calls))
        if self.publish_fail:
            raise self.publish_fail
        self.published.append(dict(wanted))
        return len(wanted)


def test_the_code_is_listed_with_its_name_before_the_first_email_goes_out(tmp_path, monkeypatch, page_on):
    sheets = CodeSheets(leads_of("a@x.com", "b@x.com"))
    send(html_cfg(tmp_path, name="Spring_Launch"), sheets, monkeypatch)
    assert sheets.published == [{ul.campaign_code(SECRET, "Spring_Launch"): "Spring_Launch"}] and sheets.sent_when_published == [0] and len(_Sent.calls) == 2


def test_if_the_code_cannot_be_listed_the_emails_still_go_out_and_the_person_is_told(tmp_path, monkeypatch, capsys, page_on):
    sheets = CodeSheets(leads_of("a@x.com"), fail=RuntimeError("quota"))
    send(html_cfg(tmp_path, name="Spring_Launch"), sheets, monkeypatch)
    out = capsys.readouterr().out
    assert len(_Sent.calls) == 1 and "WARNING: could not list the campaign code" in out and "(quota)" in out and "Campaign column" in out and "Nobody is affected." in out


def test_the_code_is_not_listed_when_it_would_not_be_used(tmp_path, monkeypatch, page_on):
    sheets = CodeSheets(leads_of("a@x.com"))
    send(text_cfg(tmp_path), sheets, monkeypatch)
    monkeypatch.delenv("UNSUBSCRIBE_URL")
    send(html_cfg(tmp_path), sheets, monkeypatch)
    nameless = html_cfg(tmp_path)
    nameless.pop("_campaign_name")
    monkeypatch.setenv("UNSUBSCRIBE_URL", PAGE)
    send(nameless, sheets, monkeypatch)
    assert sheets.published == []


def test_a_sheet_without_the_listing_feature_is_simply_skipped(tmp_path, monkeypatch, page_on):
    send(html_cfg(tmp_path), ListSheets(leads_of("a@x.com")), monkeypatch)
    assert len(_Sent.calls) == 1


def test_the_code_list_is_published_on_every_run_with_the_same_content(tmp_path, monkeypatch, page_on):
    sheets = CodeSheets(leads_of("a@x.com"))
    cfg = html_cfg(tmp_path, name="Spring_Launch")
    send(cfg, sheets, monkeypatch)
    send(cfg, sheets, monkeypatch)
    assert len(sheets.published) == 2 and sheets.published[0] == sheets.published[1]


# ---------------------------------------------------------------- the real connector writes the 'Campaign codes' tab

class CodesTab:
    def __init__(self, records=()):
        self.records, self.header = [dict(r) for r in records], None

    def get_all_records(self):
        return [dict(r) for r in self.records]

    def append_row(self, row, *args, **kwargs):
        if self.header is None and not self.records and row == ul.CODES_COLUMNS:
            self.header = list(row)
            return
        self.records.append(dict(zip(ul.CODES_COLUMNS, row)))


class CodesSpreadsheet:
    def __init__(self, existing=None, add_fails=False):
        self.tabs, self.added, self.add_fails = dict(existing or {}), [], add_fails

    def add_worksheet(self, title, rows, cols):
        self.added.append((title, rows, cols))
        if self.add_fails:
            raise RuntimeError("a sheet with that name already exists")
        self.tabs[title] = CodesTab()
        return self.tabs[title]

    def worksheet(self, title):
        return self.tabs[title]


def codes_connector(spreadsheet, known=None):
    shell = object.__new__(outreach.SheetsConnector)
    shell._known_tabs, shell._spreadsheet = dict(known or {}), spreadsheet
    return shell


def test_the_first_time_the_tab_is_made_with_its_header_and_the_row(page_on):
    spreadsheet = CodesSpreadsheet()
    shell = codes_connector(spreadsheet)
    assert shell.ensure_campaign_codes({"~abc": "Spring_Launch"}) == 1
    tab = spreadsheet.tabs[ul.CODES_TAB]
    assert tab.header == ul.CODES_COLUMNS and tab.records == [{"Code": "~abc", "Campaign": "Spring_Launch"}] and spreadsheet.added[0][0] == "Campaign codes" and shell._known_tabs[ul.CODES_TAB] is tab


def test_a_code_already_listed_is_not_added_again_and_other_rows_are_left_alone():
    tab = CodesTab([{"Code": "~abc", "Campaign": "Spring_Launch"}, {"Code": "~zzz", "Campaign": "Other"}])
    shell = codes_connector(CodesSpreadsheet(), {ul.CODES_TAB: tab})
    assert shell.ensure_campaign_codes({"~abc": "Spring_Launch"}) == 0 and len(tab.records) == 2
    assert shell.ensure_campaign_codes({"~abc": "Spring_Launch", "~new": "Autumn"}) == 1 and tab.records[-1] == {"Code": "~new", "Campaign": "Autumn"} and tab.records[1] == {"Code": "~zzz", "Campaign": "Other"}


def test_a_code_listed_against_a_different_name_is_reported_and_never_overwritten(capsys):
    tab = CodesTab([{"Code": "~abc", "Campaign": "Spring_Launch"}])
    assert codes_connector(CodesSpreadsheet(), {ul.CODES_TAB: tab}).ensure_campaign_codes({"~abc": "Someone_Else"}) == 0
    assert tab.records == [{"Code": "~abc", "Campaign": "Spring_Launch"}] and "already listed" in capsys.readouterr().out


def test_a_tab_made_by_another_run_a_moment_ago_is_used_instead_of_failing():
    existing = CodesTab([{"Code": "~abc", "Campaign": "Spring_Launch"}])
    spreadsheet = CodesSpreadsheet({ul.CODES_TAB: existing}, add_fails=True)
    shell = codes_connector(spreadsheet)
    assert shell.ensure_campaign_codes({"~abc": "Spring_Launch", "~new": "Autumn"}) == 1 and shell._known_tabs[ul.CODES_TAB] is existing and existing.records[-1]["Code"] == "~new"


def test_a_failure_reading_the_tab_is_not_hidden_inside_the_connector():
    class Broken:
        def get_all_records(self):
            raise RuntimeError("boom")
    with pytest.raises(RuntimeError, match="boom"):
        codes_connector(CodesSpreadsheet(), {ul.CODES_TAB: Broken()}).ensure_campaign_codes({"~abc": "x"})


# ---------------------------------------------------------------- the test run says whether the name is hidden

def test_the_test_run_says_the_campaign_name_is_hidden_and_what_else_is_still_readable(tmp_path, monkeypatch, capsys, page_on):
    out, code = run_command(tmp_path, monkeypatch, capsys)
    line = next(l for l in out.splitlines() if l.startswith("Campaign name in the link:"))
    assert code == 0 and "HIDDEN, the link carries only the code ~" in line and "the recipient's own address, the brand, the lead ID" in line and "only the campaign name is replaced" in line


def test_the_test_run_says_not_hidden_when_the_link_does_not_carry_the_code(tmp_path, monkeypatch, capsys, page_on):
    real = outreach.send_test_email

    def fake(*args, **kwargs):
        sent = real(*args, **kwargs)
        token = ul.make_token(SECRET, campaign="Spring_Launch", lead_id="TEST", email="t@x.com", brand="B", key="b", test=True)
        return {**sent, "unsubscribe_url": ul.page_url(PAGE, token)}
    monkeypatch.setattr(outreach, "send_test_email", fake)
    out, code = run_command(tmp_path, monkeypatch, capsys)
    assert "Campaign name in the link: NOT hidden." in out


def test_publishing_the_code_is_decided_by_the_function_itself_not_only_by_its_caller(tmp_path, page_on, monkeypatch):
    sheets = CodeSheets(leads_of("a@x.com"))
    outreach.publish_campaign_code(text_cfg(tmp_path), sheets)
    nameless = html_cfg(tmp_path)
    nameless.pop("_campaign_name")
    outreach.publish_campaign_code(nameless, sheets)
    blank = html_cfg(tmp_path, name="   ")
    outreach.publish_campaign_code(blank, sheets)
    assert sheets.published == []
    monkeypatch.delenv("UNSUBSCRIBE_SECRET")
    outreach.publish_campaign_code(html_cfg(tmp_path), sheets)
    assert sheets.published == []
    monkeypatch.setenv("UNSUBSCRIBE_SECRET", SECRET)
    outreach.publish_campaign_code(html_cfg(tmp_path, name="Spring_Launch"), sheets)
    assert sheets.published == [{ul.campaign_code(SECRET, "Spring_Launch"): "Spring_Launch"}]
