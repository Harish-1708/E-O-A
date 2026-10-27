"""The hosted unsubscribe page in the engine: the signed link in an HTML email, the per-brand opt-out list read before every HTML send, who is marked in the sheet, and the test email.
Every test that matters is paired with the plain-text case beside it, because a text campaign must never use any of this."""
import argparse
import email
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
    assert record == {"v": 1, "c": "Spring_Launch", "l": "L-9", "e": "ann@x.com", "b": "Kelson Agency", "k": "kelsonagency", "a": "12 Example Street, New York, NY"}


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

    def reply(self, code, body="", location=None):
        SEEN.append({"path": self.path, "headers": dict(self.headers)})
        self.send_response(code)
        if location:
            self.send_header("Location", location)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def do_GET(self):
        routes = {"/valid": (200, "<html><div>This link is not valid</div></html>"), "/plain": (200, "<html>hello</html>"),
                  "/error-page": (200, "<html>Sorry, unable to open the file at present. Please check the address and try again.</html>"), "/missing": (404, "<html>Not found</html>"),
                  "/forbidden": (403, "<html>no</html>"), "/server-error": (500, "<html>oops</html>"), "/accounts.google.com/ServiceLogin": (200, "<html>Sign in</html>")}
        if self.path.split("?")[0] in routes:
            return self.reply(*routes[self.path.split("?")[0]])
        if self.path.startswith("/gate"):
            return self.reply(200, "<html>Sorry, unable to open the file at present. Please check the address and try again.</html>") if "t=" in self.path else self.reply(200, "<html><div>This link is not valid</div></html>")
        if self.path.startswith("/wrapped"):
            return self.reply(200, "<html><script>goog.script.init(\"\\x3cdiv class\\x3d\\x22bd\\x22\\x3e\\x3ch1\\x3eUnsubscribe\\x3c/h1\\x3e\\x3cbutton\\x3eUnsubscribe me\\x3c/button\\x3e\", \"\")</script></html>")
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
