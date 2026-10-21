"""The pure side of the HTML design editor: which leads can be previewed, reading an upload, rendering a preview with the very function the send uses, and the checks markup."""
import html
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import html_campaign_logic as hc  # noqa: E402
import html_editor_html as EH  # noqa: E402
import html_editor_logic as EL  # noqa: E402
import outreach  # noqa: E402

FOOTER = {"business_name": "Kelson Agency", "address": "12 Example Street"}
CFG = {"format": "html", "footer": FOOTER}


# ================================================================ leads

def test_with_no_leads_the_columns_are_unknown_not_empty():
    assert EL.known_variables([]) is None and EL.known_variables(None) is None


def test_the_known_variables_are_the_template_variables_plus_every_column_without_the_system_row_marker():
    known = EL.known_variables([{"_row": 2, "FirstName": "A", "Region": "EU"}, {"_row": 3, "Company": "X", "Tier": "1"}])
    assert known[:len(outreach.TEMPLATE_VARIABLE_MAP)] == list(outreach.TEMPLATE_VARIABLE_MAP) and "Region" in known and "Tier" in known and "_row" not in known
    assert len(known) == len(set(known))


def test_the_sample_lead_comes_first_then_real_leads_with_name_and_email():
    options = EL.lead_options([{"FirstName": "Bo", "LastName": "Li", "Email": "bo@x.com"}, {"FirstName": "", "LastName": "", "Email": "c@x.com"}, {"Company": "only"}])
    assert options[0] == ("sample", "Sample lead (Ann Lee)")
    assert options[1:] == [("lead_0", "Bo Li · bo@x.com"), ("lead_1", "c@x.com"), ("lead_2", "Lead 3")]


def test_only_the_first_few_leads_are_offered():
    assert len(EL.lead_options([{"Email": f"{i}@x.com"} for i in range(100)], limit=5)) == 6


@pytest.mark.parametrize("key, expected_first_name", [("sample", "Ann"), ("lead_0", "Bo"), ("lead_1", "Cy"), ("lead_9", "Ann"), ("lead_x", "Ann"), ("", "Ann"), (None, "Ann"), ("nonsense", "Ann")])
def test_a_key_gives_that_lead_and_anything_unknown_gives_the_sample_lead(key, expected_first_name):
    assert EL.pick_lead([{"FirstName": "Bo"}, {"FirstName": "Cy"}], key)["FirstName"] == expected_first_name


def test_picking_a_lead_never_changes_the_lead_or_the_sample():
    leads = [{"FirstName": "Bo"}]
    picked = EL.pick_lead(leads, "lead_0")
    picked["FirstName"] = "changed"
    sample = EL.pick_lead(leads, "sample")
    sample["FirstName"] = "changed"
    assert leads[0]["FirstName"] == "Bo" and EL.SAMPLE_LEAD["FirstName"] == "Ann"


# ================================================================ uploads

def test_a_normal_upload_is_read_as_text():
    assert EL.decode_upload("<p>Café ☕</p>".encode("utf-8")) == ("<p>Café ☕</p>", None)


def test_a_byte_order_mark_from_a_designers_editor_is_dropped():
    assert EL.decode_upload(b"\xef\xbb\xbf<p>x</p>") == ("<p>x</p>", None)


@pytest.mark.parametrize("data", [b"", b"   \n  ", None])
def test_an_empty_upload_is_refused(data):
    assert EL.decode_upload(data) == (None, "The file is empty.")


def test_a_too_large_upload_is_refused_with_its_size():
    text, error = EL.decode_upload(b"x" * (EL.UPLOAD_LIMIT_BYTES + 1))
    assert text is None and "too large for an email" in error and "KB" in error


def test_an_upload_that_is_not_utf8_is_refused_with_what_to_do():
    text, error = EL.decode_upload("café".encode("cp1252"))
    assert text is None and "UTF-8" in error


def test_an_upload_exactly_at_the_limit_is_accepted():
    assert EL.decode_upload(b"x" * EL.UPLOAD_LIMIT_BYTES)[1] is None


# ================================================================ the preview

def test_the_preview_is_what_the_send_would_produce_for_that_lead():
    raw = "<p>Hi {{ FirstName }} at {{ CompanyName }}</p>{{ footer }}"
    lead = {"FirstName": "Ann", "Company": "Acme"}
    preview = EL.render_preview(raw, lead, CFG)
    assert preview["errors"] == [] and preview["missing"] == [] and preview["html"] == hc.fill_unsubscribe_html(outreach.render_html_text(raw, lead, CFG)["html"], hc.SAMPLE_SENDER) and "Hi Ann at Acme" in preview["html"]


def test_unsafe_html_is_never_rendered_in_the_preview():
    preview = EL.render_preview("<script>alert(1)</script>{{ footer }}", EL.SAMPLE_LEAD, CFG)
    assert preview["html"] is None and preview["errors"] == hc.unsafe_problems("<script>alert(1)</script>{{ footer }}") and "must not have" in preview["errors"][0]


def test_an_incomplete_footer_is_reported_but_the_layout_is_still_previewed_with_a_marker():
    preview = EL.render_preview("<p>Hi {{ FirstName }}</p>{{ footer }}", EL.SAMPLE_LEAD, {"format": "html"})
    assert "footer is incomplete" in preview["errors"][0] and "Hi Ann" in preview["html"] and "Your footer will appear here" in preview["html"]


def test_placeholders_that_match_nothing_are_listed_once_in_the_preview():
    preview = EL.render_preview("{{ nope }} {{ nope }} {{ other }}{{ footer }}", EL.SAMPLE_LEAD, CFG)
    assert preview["missing"] == ["nope", "other"]


@pytest.mark.parametrize("hostile", ['<script>alert(1)</script>', '"><img src=x onerror=alert(1)>'])
def test_a_hostile_lead_can_never_inject_markup_into_the_preview(hostile):
    preview = EL.render_preview("<p>{{ FirstName }}</p>{{ footer }}", {"FirstName": hostile}, CFG)
    assert hostile not in preview["html"] and html.escape(hostile) in preview["html"]


def test_a_fragment_is_wrapped_as_a_page_and_a_full_page_is_kept_with_the_fitting_script_before_the_end_of_its_body():
    fragment = EL.preview_document("<p>x</p>")
    assert fragment.startswith("<html><body") and "<p>x</p>" in fragment and fragment.endswith(EL.FIT_SCRIPT + "</body></html>")
    page = "<!DOCTYPE html><HTML><body>y</body></HTML>"
    assert EL.preview_document(page) == "<!DOCTYPE html><HTML><body>y" + EL.FIT_SCRIPT + "</body></HTML>"
    assert EL.preview_document("<html><p>no body tag</p></html>").endswith(EL.FIT_SCRIPT)


def test_the_fitting_script_resizes_the_frame_and_the_box_around_it_never_lets_the_frame_outgrow_the_box_and_caps_a_very_long_email():
    script = EL.FIT_SCRIPT
    assert "window.frameElement" in script and "f.parentElement" in script and script.count("setProperty('height'") == 2 and "'important'" in script
    assert "Math.min(h,w.getBoundingClientRect().height||h)" in script                       # the frame is never taller than its box really became, so it can never overlap what is below
    assert f"cap={EL.PREVIEW_MAX_HEIGHT}" in script and "Math.min(Math.max(d.scrollHeight" in script
    assert "catch(e){}" in script and 600 <= EL.PREVIEW_MAX_HEIGHT <= 2000 and EL.PREVIEW_HEIGHT < EL.PREVIEW_MAX_HEIGHT
    for forbidden in ("fetch", "XMLHttpRequest", "parent.", "top.", "document.cookie", "localStorage", "eval("):
        assert forbidden not in script


def test_the_fitting_script_is_never_part_of_what_is_sent_or_checked():
    raw = "<p>Hi {{ FirstName }}</p>{{ footer }}"
    preview = EL.render_preview(raw, EL.SAMPLE_LEAD, CFG)
    assert "<script" not in preview["html"] and "<script" in EL.preview_document(preview["html"])
    assert hc.fill_unsubscribe_html(outreach.render_html_text(raw, EL.SAMPLE_LEAD, CFG)["html"], hc.SAMPLE_SENDER) == preview["html"]


# ================================================================ the footer in force

def test_the_footer_in_force_is_the_one_just_saved_else_the_campaigns_own():
    own = {"footer": {"business_name": " Own ", "address": "Addr", "opt_out": ""}}
    assert EL.effective_footer(own) == {"business_name": "Own", "address": "Addr", "opt_out": ""}
    assert EL.effective_footer(own, {"business_name": "New", "address": "Street", "opt_out": "Stop"}) == {"business_name": "New", "address": "Street", "opt_out": "Stop"}
    assert EL.effective_footer({}) == {"business_name": "", "address": "", "opt_out": ""} and EL.effective_footer({"footer": None}, None) == {"business_name": "", "address": "", "opt_out": ""}
    assert EL.effective_footer(own, {}) == {"business_name": "Own", "address": "Addr", "opt_out": ""}


def test_the_footer_in_force_never_carries_anything_but_the_three_fields():
    assert set(EL.effective_footer({"footer": {"business_name": "K", "address": "A", "evil": "<script>"}})) == {"business_name", "address", "opt_out"}


def test_a_just_saved_footer_makes_an_incomplete_campaign_footer_complete_for_the_preview():
    preview_before = EL.render_preview("<p>x</p>{{ footer }}", EL.SAMPLE_LEAD, {"format": "html"})
    cfg = {"format": "html", "footer": EL.effective_footer({}, {"business_name": "K", "address": "A", "opt_out": ""})}
    preview_after = EL.render_preview("<p>x</p>{{ footer }}", EL.SAMPLE_LEAD, cfg)
    assert preview_before["errors"] and preview_after["errors"] == [] and "K" in preview_after["html"]


def test_the_snapshot_key_is_per_campaign():
    assert EL.footer_snapshot_key("A") == "footer_saved_A" and EL.footer_snapshot_key("A") != EL.footer_snapshot_key("B")



def test_the_design_path_names_the_html_file_beside_the_text_file():
    assert EL.design_path("Camp", "followup1", "B") == "templates/Camp/followup1_B.html"


# ================================================================ the checks markup

def test_the_checks_show_errors_then_warnings_and_say_so_when_all_is_well():
    markup = EH.checks_html({"errors": ["bad one"], "warnings": ["soft one"]})
    assert markup.index("he-er") < markup.index("he-wn") and "bad one" in markup and "soft one" in markup and "Every check passed" not in markup and "No problems that stop sending" not in markup
    assert "Every check passed" in EH.checks_html({"errors": [], "warnings": []})
    warn_only = EH.checks_html({"errors": [], "warnings": ["soft one"]})
    assert "No problems that stop sending" in warn_only and "soft one" in warn_only


def test_extra_errors_are_listed_with_the_others():
    assert EH.checks_html({"errors": ["a"], "warnings": []}, ["extra"]).count("he-er") == 2


@pytest.mark.parametrize("hostile", ['<script>x</script>', '"><img src=x onerror=1>'])
def test_nothing_in_a_check_message_can_become_markup(hostile):
    markup = EH.checks_html({"errors": [hostile], "warnings": [hostile]}, [hostile]) + EH.title_html(hostile, hostile)
    assert "<script>" not in markup and "<img" not in markup and markup.count(html.escape(hostile)) == 5


def test_the_preview_shows_the_sample_sender_address_in_the_unsubscribe_link_and_never_the_stand_in():
    preview = EL.render_preview("<p>Hi</p>{{ footer }}", EL.SAMPLE_LEAD, CFG)
    assert f"mailto:{hc.SAMPLE_SENDER}?subject=unsubscribe" in preview["html"] and hc.UNSUBSCRIBE_TOKEN not in preview["html"]
    incomplete = EL.render_preview("<p>Hi</p>{{ footer }}", EL.SAMPLE_LEAD, {"format": "html"})
    assert hc.UNSUBSCRIBE_TOKEN not in incomplete["html"]
