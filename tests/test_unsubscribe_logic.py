"""The unsubscribe page's engine-side core: signed links, brands, and who has opted out. The two KNOWN-ANSWER links below are repeated in apps_script/test_unsubscribe.js, so the
Python that makes a link and the Google script that checks it are proved to agree on exactly the same bytes."""
import base64
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unsubscribe_logic as ul  # noqa: E402

SECRET = "test-secret-1234567890"
KNOWN_PLAIN = ("eyJhIjoiMTIgRXhhbXBsZSBTdHJlZXQsIE5ldyBZb3JrLCBOWSIsImIiOiJLZWxzb24gQWdlbmN5IiwiYyI6IlNwcmluZ19MYXVuY2giLCJlIjoiYW5uQGV4YW1wbGUuY29tIiwiayI6ImtlbHNvbmFnZW5jeSIs"
               "ImwiOiJMLTE3IiwidiI6MX0.KTXW_9O4mzmdoFM_vQyaNHSGio7Q71CUfhZwxGNpCY0")
KNOWN_UNICODE_TEST = ("eyJhIjoiUnVlIGRlIGwnw4l0w6kgMyIsImIiOiJDYWbDqSDDnG7Dr2NvZGUg4piVIiwiYyI6IkNhZsOpX8OJdMOpIiwiZSI6Inpvw6tAZXhhbXBsZS5jb20iLCJrIjoiY2FmZXVuaWNvZGUiLCJsIjoiTC0xOCIs"
                      "InQiOjEsInYiOjF9.1FZsRhrf_nyhBH6RIbwX_5qcvbp6jFiVNnJYHUccPdc")


def make(**kw):
    base = dict(campaign="Spring_Launch", lead_id="L-17", email="ann@example.com", brand="Kelson Agency", key="kelsonagency", address="12 Example Street, New York, NY")
    base.update(kw)
    return ul.make_token(SECRET, **base)


# ================================================================ the signed link

def test_the_two_known_answer_links_are_exactly_what_the_code_makes_today():
    assert make() == KNOWN_PLAIN
    assert make(campaign="Café_Été", lead_id="L-18", email="zoë@example.com", brand="Café Ünïcode ☕", key="cafeunicode", address="Rue de l'Été 3", test=True) == KNOWN_UNICODE_TEST


def test_a_link_comes_back_with_everything_that_went_into_it():
    assert ul.verify_token(SECRET, KNOWN_PLAIN) == {"v": 1, "c": "Spring_Launch", "l": "L-17", "e": "ann@example.com", "b": "Kelson Agency", "k": "kelsonagency", "a": "12 Example Street, New York, NY"}
    unicode_record = ul.verify_token(SECRET, KNOWN_UNICODE_TEST)
    assert unicode_record["b"] == "Café Ünïcode ☕" and unicode_record["a"] == "Rue de l'Été 3" and unicode_record["t"] == 1


def test_only_a_test_link_carries_the_test_marker():
    assert "t" not in ul.verify_token(SECRET, make()) and ul.verify_token(SECRET, make(test=True))["t"] == 1


def test_the_same_inputs_always_make_the_same_link_and_any_difference_makes_a_different_one():
    assert make() == make()
    assert len({make(), make(lead_id="L-18"), make(email="bob@example.com"), make(campaign="Other"), make(brand="Other Brand"), make(key="other"), make(address="x"), make(test=True)}) == 8


def test_a_link_is_safe_to_put_in_a_web_address_and_in_an_email_header():
    token = make(brand="A&B <Co> \"quoted\" ?x=1", address="line\nbreak")
    assert all(ch.isalnum() or ch in "-_." for ch in token) and token.count(".") == 1 and "\n" not in token and ul.verify_token(SECRET, token)["a"] == "line\nbreak"


@pytest.mark.parametrize("damage", ["flip_payload", "flip_signature", "drop_signature", "extra_part", "empty", "no_dot", "swap_parts", "truncate", "append"])
def test_a_link_that_has_been_altered_in_any_way_is_refused(damage):
    body, signature = KNOWN_PLAIN.split(".")
    altered = {"flip_payload": ("A" if body[10] != "A" else "B").join([body[:10], body[11:]]) + "." + signature,
               "flip_signature": body + "." + ("A" if signature[0] != "A" else "B") + signature[1:], "drop_signature": body + ".", "extra_part": KNOWN_PLAIN + ".x",
               "empty": "", "no_dot": body + signature, "swap_parts": signature + "." + body, "truncate": KNOWN_PLAIN[:-3], "append": KNOWN_PLAIN + "A"}[damage]
    assert ul.verify_token(SECRET, altered) is None


def test_a_link_signed_with_another_secret_is_refused_and_so_is_garbage():
    assert ul.verify_token("another-secret-0987654321", KNOWN_PLAIN) is None
    for junk in (None, 5, [], {}, "a.b", "....", "é.é", "x" * 5000):
        assert ul.verify_token(SECRET, junk) is None


def test_a_correctly_signed_record_of_the_wrong_shape_or_version_is_refused():
    def signed(payload):
        body = ul._b64(json.dumps(payload).encode("utf-8"))
        return f"{body}.{ul._sign(SECRET, body)}"
    good = {"v": 1, "c": "c", "l": "l", "e": "e", "b": "b", "k": "k", "a": "a"}
    assert ul.verify_token(SECRET, signed(good)) == good
    for bad in ({**good, "v": 2}, {**good, "v": "1"}, {k: v for k, v in good.items() if k != "k"}, {**good, "e": 5}, {**good, "c": None}, [1, 2], "text", 7):
        assert ul.verify_token(SECRET, signed(bad)) is None


def test_the_page_address_joins_the_link_with_the_right_separator():
    assert ul.page_url("https://script.google.com/macros/s/ID/exec", "T") == "https://script.google.com/macros/s/ID/exec?t=T"
    assert ul.page_url("https://x.example/p?a=1", "T") == "https://x.example/p?a=1&t=T"


@pytest.mark.parametrize("email, shown", [("ann@gmail.com", "a***@gmail.com"), ("a@x.com", "a***@x.com"), ("zoë@x.com", "z***@x.com"), ("", "***"), ("nobody", "***"), ("@x.com", "***"), ("a@", "***"), (None, "***")])
def test_an_address_is_shown_partly_hidden(email, shown):
    assert ul.mask_email(email) == shown


# ================================================================ which brand an opt-out belongs to

@pytest.mark.parametrize("name", ["DubeRobe", "Dube Robe", "dube-robe", "DUBE ROBE", "  Dube_Robe  ", "Dubé Robé", "DUBÉ-ROBE", "Duberobe™"])
def test_spelling_differences_in_case_spaces_punctuation_and_accents_are_one_brand(name):
    assert ul.brand_key(name, "Any_Campaign") == "duberobe"


def test_different_brands_have_different_keys_and_the_campaign_does_not_matter_when_there_is_a_name():
    assert ul.brand_key("Defi Snacks", "A") == ul.brand_key("DEFI SNACKS", "B") == "defisnacks" and ul.brand_key("Swoveralls", "A") != ul.brand_key("DubeRobe", "A")
    assert ul.brand_key("Kelson Agency Inc", "A") != ul.brand_key("Kelson Agency", "A")                    # a different name is a different brand: the footer name is the identity


@pytest.mark.parametrize("name", [None, "", "   ", "---", "!!!"])
def test_a_campaign_with_no_business_name_is_its_own_brand(name):
    assert ul.brand_key(name, "Spring_Launch") == "campaign:spring_launch" and ul.brand_key(name, "Spring_Launch") != ul.brand_key(name, "Other")


def test_a_brand_name_in_another_alphabet_still_has_its_own_key():
    assert ul.brand_key("ブランド", "A") == "ブランド" and ul.brand_key("Бренд ", "A") == "бренд" and ul.brand_key("Йога", "A") == "йога"
    assert ul.brand_key("ブランド", "A") != ul.brand_key("フランド", "A")                            # a sound mark makes a different word, so a different brand


# ================================================================ who has opted out

ROWS = [{"Email": "Ann@Example.com ", "BrandKey": "duberobe"}, {"Email": "bob@example.com", "BrandKey": "swoveralls"}, {"Email": "cy@example.com", "BrandKey": ""}, {"Email": "", "BrandKey": "x"},
        {"Email": "dee@example.com"}]


def test_the_list_is_read_with_addresses_in_lower_case_and_blank_rows_ignored():
    assert ul.load_pairs(ROWS) == {("ann@example.com", "duberobe"), ("bob@example.com", "swoveralls"), ("cy@example.com", ""), ("dee@example.com", "")}
    assert ul.load_pairs([]) == set() and ul.load_pairs(None) == set()


def test_an_opt_out_applies_to_that_brand_only():
    pairs = ul.load_pairs(ROWS)
    assert ul.is_suppressed("ann@example.com", "duberobe", pairs) and not ul.is_suppressed("ann@example.com", "swoveralls", pairs)
    assert ul.is_suppressed("bob@example.com", "swoveralls", pairs) and not ul.is_suppressed("bob@example.com", "duberobe", pairs)


def test_the_comparison_ignores_case_and_spaces_in_the_address():
    pairs = ul.load_pairs(ROWS)
    assert ul.is_suppressed("  ANN@example.COM", "duberobe", pairs)


def test_a_row_added_by_hand_with_no_brand_applies_to_every_brand():
    pairs = ul.load_pairs(ROWS)
    assert all(ul.is_suppressed("cy@example.com", key, pairs) for key in ("duberobe", "swoveralls", "campaign:x")) and ul.is_suppressed("dee@example.com", "anything", pairs)


def test_an_address_not_on_the_list_or_blank_is_never_suppressed():
    pairs = ul.load_pairs(ROWS)
    assert not ul.is_suppressed("new@example.com", "duberobe", pairs) and not ul.is_suppressed("", "duberobe", pairs) and not ul.is_suppressed(None, "duberobe", pairs)
    assert not ul.is_suppressed("ann@example.com", "duberobe", set())


def test_the_leads_that_have_opted_out_are_picked_out_of_a_list_in_order():
    pairs = ul.load_pairs(ROWS)
    leads = [{"Email": "x@example.com"}, {"Email": "ANN@example.com"}, {"Email": "bob@example.com"}, {"Email": "cy@example.com"}, {}]
    assert [lead.get("Email") for lead in ul.suppressed_leads(leads, "duberobe", pairs)] == ["ANN@example.com", "cy@example.com"]


# ================================================================ the set-up

def test_not_set_up_at_all_is_a_normal_quiet_state():
    assert ul.configuration_status({}) == (None, "") and ul.configuration({}) is None and ul.configuration({"UNSUBSCRIBE_URL": "  ", "UNSUBSCRIBE_SECRET": ""}) is None


def test_a_complete_set_up_gives_the_page_address_and_the_secret():
    env = {"UNSUBSCRIBE_URL": " https://script.google.com/macros/s/ID/exec ", "UNSUBSCRIBE_SECRET": " " + "s" * 20 + " "}
    config, note = ul.configuration_status(env)
    assert config == ul.PageConfig("https://script.google.com/macros/s/ID/exec", "s" * 20) and note == ""


@pytest.mark.parametrize("env, words", [({"UNSUBSCRIBE_URL": "https://x.example/p"}, "both be set"), ({"UNSUBSCRIBE_SECRET": "s" * 20}, "both be set"),
                                        ({"UNSUBSCRIBE_URL": "http://x.example/p", "UNSUBSCRIBE_SECRET": "s" * 20}, "https://"), ({"UNSUBSCRIBE_URL": "x.example/p", "UNSUBSCRIBE_SECRET": "s" * 20}, "https://"),
                                        ({"UNSUBSCRIBE_URL": "https://x.example/p", "UNSUBSCRIBE_SECRET": "short"}, "too short")])
def test_a_half_done_or_unusable_set_up_is_off_and_says_exactly_why(env, words):
    config, note = ul.configuration_status(env)
    assert config is None and words in note and "OFF" in note


def test_the_secret_length_rule_starts_at_sixteen_characters():
    assert ul.configuration({"UNSUBSCRIBE_URL": "https://x.example/p", "UNSUBSCRIBE_SECRET": "x" * 15}) is None
    assert ul.configuration({"UNSUBSCRIBE_URL": "https://x.example/p", "UNSUBSCRIBE_SECRET": "x" * 16}) is not None


def test_the_tab_and_its_columns_are_what_the_google_page_writes():
    assert ul.UNSUBSCRIBE_TAB == "Unsubscribes" and ul.UNSUBSCRIBE_COLUMNS == ["Timestamp", "Email", "Brand", "BrandKey", "Campaign", "LeadID", "Reason", "Details", "Source"]
    assert ul.REASONS == ["Too many emails", "Not relevant to me", "I never signed up", "Other"]


def test_a_decoded_payload_is_plain_json_with_no_padding_in_the_link():
    body = KNOWN_PLAIN.split(".")[0]
    assert "=" not in body and json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))["b"] == "Kelson Agency"


# ================================================================ the page address (the test address works for you and nobody else)

SECRET20 = "s" * 20


def status(url):
    return ul.configuration_status({"UNSUBSCRIBE_URL": url, "UNSUBSCRIBE_SECRET": SECRET20})


@pytest.mark.parametrize("url", ["https://script.google.com/macros/s/AKfycbx123/exec", "https://script.google.com/macros/s/AKfycbx123/exec/", "https://SCRIPT.GOOGLE.COM/macros/s/AKfy/exec",
                                 "https://unsubscribe.kelson.agency/page?a=1", "https://unsubscribe.kelson.agency/page", "https://example.com/dev"])
def test_a_proper_page_address_is_accepted_without_a_warning(url):
    config, note = status(url)
    assert config is not None and note == "" and config.url == url


@pytest.mark.parametrize("url", ["https://script.google.com/macros/s/AKfycbx123/dev", "https://script.google.com/macros/s/AKfycbx123/dev/", "https://SCRIPT.google.com/macros/s/AKfy/DEV".replace("DEV", "dev")])
def test_googles_test_address_is_refused_because_it_opens_only_for_the_scripts_owner(url):
    config, note = status(url)
    assert config is None and "ends in /dev" in note and "TEST address" in note and "only for the script's owner" in note and "/exec" in note and "OFF" in note


@pytest.mark.parametrize("url", ["https://script.google.com/macros/s/AKfycbx123", "https://script.google.com/macros/s/AKfycbx123/exec2", "https://script.google.com/macros/s/AKfycbx123/exe", "https://script.google.com/"])
def test_a_google_address_that_is_not_the_web_app_address_is_refused(url):
    config, note = status(url)
    assert config is None and "should end in /exec" in note and "OFF" in note


def test_an_address_for_people_inside_one_workspace_is_kept_but_flagged():
    config, note = status("https://script.google.com/a/macros/kelson.agency/s/AKfy/exec")
    assert config is not None and "/a/macros/" in note and "Who has access" in note and "Anyone" in note


@pytest.mark.parametrize("url", ["https://script.google.com/macros/s/AKfy/exec\nhttps://x", "https://script.google.com/macros/s/AKfy /exec", "https://script.google.com/macros/s/AKfy/exec\t"[:-1] + "\r\nx"])
def test_an_address_pasted_with_a_space_or_a_line_break_in_it_is_refused(url):
    config, note = status(url)
    assert config is None and "space or a line break" in note


def test_surrounding_whitespace_from_a_paste_is_simply_trimmed():
    config, note = status("  https://script.google.com/macros/s/AKfy/exec \n")
    assert config is not None and config.url == "https://script.google.com/macros/s/AKfy/exec" and note == ""


def test_the_address_rules_do_not_apply_to_another_host():
    assert ul.address_problem("https://example.com/macros/s/X/dev") == (False, "")
    assert ul.address_problem("https://notscript.google.com.evil.example/macros/s/X/dev") == (False, "")


# ================================================================ what the page answers when opened without signing in

@pytest.mark.parametrize("status_code, url, body, state", [
    (200, "https://script.google.com/macros/s/X/exec", "<html>This link is not valid</html>", "ok"), (200, "https://x.example/p", "<html>anything</html>", "ok"),
    (200, "https://x/p", "Sorry, unable to open the file at present. Please check the address and try again.", "blocked"), (404, "https://x/p", "Not found", "blocked"),
    (403, "https://x/p", "", "blocked"), (401, "https://x/p", "", "blocked"), (410, "https://x/p", "", "blocked"),
    (200, "https://accounts.google.com/ServiceLogin?continue=x", "<html>Sign in</html>", "signin"), (302, "https://ACCOUNTS.google.com/v3/signin", "", "signin"),
    (500, "https://x/p", "oops", "unknown"), (302, "https://x/p", "", "unknown"), (None, "https://x/p", "", "unknown")])
def test_what_the_address_answered_is_classified_for_a_person(status_code, url, body, state):
    got_state, message = ul.classify_probe(status_code, url, body)
    assert got_state == state and message
    assert (state in ("blocked", "signin")) == ("Deploy > Manage deployments > Edit > Who has access: Anyone" in message)         # a problem always says how to fix it


def test_the_error_page_is_recognised_whatever_the_status_and_the_letter_case():
    assert ul.classify_probe(200, "https://x", "<b>SORRY, UNABLE TO OPEN THE FILE AT PRESENT</b>")[0] == "blocked"
    assert ul.classify_probe(404, "https://x", "unable to open the file")[0] == "blocked"


def test_our_own_not_valid_card_is_reported_as_the_unsubscribe_page_answering():
    assert "answered as the unsubscribe page" in ul.classify_probe(200, "https://x", "<div>This link is not valid</div>")[1]
    assert "answered as the unsubscribe page" not in ul.classify_probe(200, "https://x", "<div>other</div>")[1]


def test_a_problem_on_our_side_is_never_a_verdict_on_the_page():
    state, message = ul.classify_probe(None, "https://x", "", error="Name or service not known")
    assert state == "unknown" and "could not check" in message and "Name or service not known" in message


def test_only_unknown_is_not_a_problem():
    assert {ul.classify_probe(200, "u", "")[0], ul.classify_probe(500, "u", "")[0]} == {"ok", "unknown"}


# ================================================================ the exact link, and what two checks together mean

def test_where_a_request_ended_up_never_includes_the_links_token():
    assert ul._where("https://script.google.com/macros/s/AKfy/exec?t=SECRET.TOKEN") == "script.google.com/macros/s/AKfy/exec"
    assert ul._where("https://x.example/p") == "x.example/p" and ul._where("") == "" and ul._where(None) == ""


def test_every_answer_says_the_http_status_and_where_it_ended_but_never_the_token():
    for status, body in ((200, "x"), (404, ""), (200, "unable to open the file")):
        message = ul.classify_probe(status, "https://script.google.com/macros/s/AKfy/exec?t=SECRET.TOKEN", body)[1]
        assert f"HTTP {status}" in message and "script.google.com/macros/s/AKfy/exec" in message and "SECRET" not in message


def test_when_the_real_pages_text_is_expected_an_ok_answer_says_whether_it_was_seen():
    seen = ul.classify_probe(200, "https://x/p", "<button>Unsubscribe me</button>", expect=ul.LINK_MARKERS)
    unseen = ul.classify_probe(200, "https://x/p", "<html>hello</html>", expect=ul.LINK_MARKERS)
    assert seen[0] == unseen[0] == "ok" and "served the unsubscribe page itself" in seen[1] and "could not see the unsubscribe page's own text" in unseen[1]
    assert "served the unsubscribe page itself" not in unseen[1]


def test_the_test_banner_text_also_counts_as_the_real_page():
    assert "served the unsubscribe page itself" in ul.classify_probe(200, "https://x", "This is a test link from your team. Nothing will be recorded.", expect=ul.LINK_MARKERS)[1]


def test_expecting_text_never_turns_a_refusal_into_ok():
    assert ul.classify_probe(200, "https://x", "Sorry, unable to open the file at present. Unsubscribe me", expect=ul.LINK_MARKERS)[0] == "blocked"
    assert ul.classify_probe(404, "https://x", "Unsubscribe me", expect=ul.LINK_MARKERS)[0] == "blocked"


@pytest.mark.parametrize("bare, exact, words", [("blocked", "blocked", "page address itself is refused"), ("signin", "ok", "page address itself is refused"), ("blocked", "unknown", "page address itself is refused"),
                                                ("ok", "blocked", "the exact link in the email does NOT"), ("ok", "signin", "the exact link in the email does NOT"),
                                                ("ok", "ok", "BOTH open for an anonymous visitor"), ("ok", "unknown", "not conclusive"), ("unknown", "ok", "not conclusive"), ("unknown", "unknown", "not conclusive")])
def test_the_two_checks_together_say_where_the_problem_is(bare, exact, words):
    assert words in ul.compare_probes(bare, exact) and ul.compare_probes(bare, exact).startswith("DIAGNOSIS:")


def test_each_diagnosis_gives_the_next_step_for_its_own_situation():
    assert "deployment or the address in the secret" in ul.compare_probes("blocked", "ok")
    assert "the link (its length or its token), not the deployment" in ul.compare_probes("ok", "blocked") and "printed above" in ul.compare_probes("ok", "blocked")
    both = ul.compare_probes("ok", "ok")
    assert "several Google accounts signed in at once" in both and "company account that blocks outside web apps" in both and "in-app browser" in both and "private window" in both


def test_the_page_texts_the_check_looks_for_are_really_in_the_google_script():
    with open(os.path.join(os.path.dirname(__file__), "..", "apps_script", "Code.gs"), encoding="utf-8") as handle:
        code = handle.read()
    assert all(marker in code for marker in ul.LINK_MARKERS)


# ================================================================ what a browser does after the first answer

ESCAPED = ('goog.script.init("\\x3ciframe src\\x3d\\x22https:\\/\\/n-abc-0lu-script.googleusercontent.com\\/userCodeAppPanel\\x3fx\\x3d1\\x26y\\x3d2\\x22\\x3e '
           '\\u003ca href\\u003d\\u0022https://n-q.googleusercontent.com/z\\u0022\\u003e https://script.google.com/static/x.js https://www.gstatic.com/a.js")')


def test_the_pages_content_frame_is_found_inside_googles_escaped_answer_with_its_query_intact():
    assert ul.find_frame_urls(ESCAPED) == ["https://n-abc-0lu-script.googleusercontent.com/userCodeAppPanel?x=1&y=2", "https://n-q.googleusercontent.com/z"]


def test_only_googleusercontent_addresses_count_and_the_app_panel_comes_first():
    body = "https://n-1.googleusercontent.com/other https://n-2.googleusercontent.com/userCodeAppPanel https://script.google.com/x https://evilgoogleusercontent.com/y https://n-1.googleusercontent.com/other"
    assert ul.find_frame_urls(body) == ["https://n-2.googleusercontent.com/userCodeAppPanel", "https://n-1.googleusercontent.com/other"]


@pytest.mark.parametrize("body", ["", None, "no addresses here", "https://script.google.com/only", "http://", "googleusercontent.com/without-scheme"])
def test_an_answer_with_no_content_frame_gives_an_empty_list(body):
    assert ul.find_frame_urls(body) == []


def test_trailing_punctuation_is_not_part_of_a_found_address():
    assert ul.find_frame_urls("see (https://n-1.googleusercontent.com/panel), then https://n-2.googleusercontent.com/p2.") == ["https://n-1.googleusercontent.com/panel", "https://n-2.googleusercontent.com/p2"]


def test_another_host_can_be_given_for_testing():
    assert ul.find_frame_urls("x http://127.0.0.1:8080/frame?a=1 y", hosts=("127.0.0.1",)) == ["http://127.0.0.1:8080/frame?a=1"]


def step(kind="hop", state="ok", **kw):
    return {"kind": kind, "where": "script.google.com/macros/s/X/exec", "status": 200, "state": state, "size": 10, **kw}


@pytest.mark.parametrize("steps, words", [
    ([step(state="blocked")], "refused at the first request (step 1)"), ([step(), step(state="blocked")], "refused at redirect step 2"),
    ([step(), step(kind="frame", state="blocked")], "refused at the page's own content frame (the second request a browser makes) (step 2)"),
    ([step(state="signin")], "refused at the first request"), ([step(), step(kind="frame", state="signin")], "content frame"),
    ([step(), {"kind": "frame", "where": "x", "error": "timed out", "state": "unknown"}], "not conclusive: a step could not be made (timed out)"),
    ([step()], "No separate content frame was found"), ([step(), step(kind="frame")], "including the page's own content frame")])
def test_a_trace_is_summed_up_in_one_sentence_that_says_where_it_went_wrong(steps, words):
    verdict = ul.trace_verdict(steps)
    assert verdict.startswith("TRACE VERDICT:") and words in verdict


def test_every_verdict_says_what_the_trace_could_not_test_and_only_a_refusal_says_how_to_fix_it():
    for steps in ([step()], [step(), step(kind="frame")], [step(state="blocked")], [step(state="signin")], [{"kind": "hop", "where": "x", "error": "e", "state": "unknown"}]):
        verdict = ul.trace_verdict(steps)
        assert "NOT TESTED HERE" in verdict and "signed in to a Google account" in verdict and "Unsubscribe diagnostics tab" in verdict
        assert ("Who has access: Anyone" in verdict) == (steps[0].get("state") in ("blocked", "signin"))


def test_a_trace_is_laid_out_one_line_per_request_with_what_a_person_needs_to_see():
    lines = ul.format_trace([step(status=302, location="script.google.com/macros/s/X/exec2", cookies=["NID"], size=0), step(kind="frame", content_type="text/html; charset=utf-8", frame_options="DENY", size=4321),
                             {"kind": "hop", "where": "x.example/p", "error": "timed out", "state": "unknown"}])
    assert lines[0].startswith("  step 1 (request) script.google.com/macros/s/X/exec -> HTTP 302 [OK]") and "redirects to script.google.com/macros/s/X/exec2" in lines[0] and "sets cookies: NID" in lines[0]
    assert lines[1].startswith("  step 2 (content frame)") and "text/html" in lines[1] and "charset" not in lines[1] and "X-Frame-Options: DENY" in lines[1] and "4321 bytes" in lines[1]
    assert lines[2] == "  step 3 (request) x.example/p: could not be made (timed out)"


def test_the_diagnostics_log_names_are_what_the_google_script_writes():
    assert ul.DIAGNOSTICS_TAB == "Unsubscribe diagnostics" and ul.DIAGNOSTICS_COLUMNS == ["Time", "Event", "State", "Brand", "Campaign", "QueryLength", "TokenLength", "ParamNames"]
    assert ul.DIAGNOSTIC_EVENTS == ["opened", "rendered", "confirmed", "one-tap"]


def test_an_address_written_entirely_in_unicode_escapes_is_read_whole():
    body = 'x "https:\\u002f\\u002fn-1.googleusercontent.com\\u002fuserCodeAppPanel\\u003fa\\u003d1\\u0026b\\u003d2" y'
    assert ul.find_frame_urls(body) == ["https://n-1.googleusercontent.com/userCodeAppPanel?a=1&b=2"]


def test_both_kinds_of_escape_can_be_mixed_in_one_address():
    assert ul.find_frame_urls('https:\\/\\/n-1.googleusercontent.com\\x2fp\\u003fa\\x3d1&amp;b=2') == ["https://n-1.googleusercontent.com/p?a=1&b=2"]


# ================================================================ a browser's own account position is not part of the page address

ID = "AKfycbytTNqD3rf05EYnWiuuVMzzrGP24nhxCBHs7UzBnUj3yxJwL2VZoeIwohb6lcMli_g9"
CANONICAL = f"https://script.google.com/macros/s/{ID}/exec"


@pytest.mark.parametrize("given, canonical, removed", [
    (CANONICAL, CANONICAL, []), (f"https://script.google.com/macros/u/6/s/{ID}/exec", CANONICAL, ["/u/6/"]), (f"https://script.google.com/macros/u/0/s/{ID}/exec", CANONICAL, ["/u/0/"]),
    (f"https://script.google.com/macros/u/12/s/{ID}/exec", CANONICAL, ["/u/12/"]), (f"{CANONICAL}?authuser=6", CANONICAL, ["?authuser=6"]), (f"{CANONICAL}#top", CANONICAL, ["#top"]),
    (f"https://script.google.com/macros/u/6/s/{ID}/exec?authuser=6&x=1#top", CANONICAL, ["/u/6/", "?authuser=6&x=1", "#top"]),
    (f"https://SCRIPT.google.com/macros/u/3/s/{ID}/exec", f"https://SCRIPT.google.com/macros/s/{ID}/exec", ["/u/3/"]),
    (f"https://script.google.com/macros/u/6/s/{ID}/dev", f"https://script.google.com/macros/s/{ID}/dev", ["/u/6/"])])
def test_what_only_means_something_in_the_browser_it_was_copied_from_is_taken_out_of_a_google_address(given, canonical, removed):
    assert ul.canonical_address(given) == (canonical, removed)


@pytest.mark.parametrize("url", ["https://example.com/macros/u/6/s/X/exec?y=1#z", "https://unsubscribe.kelson.agency/u/6/page?a=1", f"https://script.google.com.evil.example/macros/u/6/s/{ID}/exec",
                                 f"https://script.google.com/a/macros/kelson.agency/s/{ID}/exec", f"https://script.google.com/macros/s/{ID}/exec/u/6/", f"https://script.google.com/macros/s/u/6/s/{ID}/exec"])
def test_any_other_host_or_shape_is_left_exactly_as_given(url):
    assert ul.canonical_address(url) == (url, [])


def test_tidying_twice_changes_nothing_more():
    once = ul.canonical_address(f"https://script.google.com/macros/u/6/s/{ID}/exec?authuser=6")[0]
    assert ul.canonical_address(once) == (once, [])


def test_an_address_copied_from_a_browser_is_used_in_its_clean_form_and_the_person_is_told():
    config, note = ul.configuration_status({"UNSUBSCRIBE_URL": f"https://script.google.com/macros/u/6/s/{ID}/exec", "UNSUBSCRIBE_SECRET": "s" * 20})
    assert config is not None and config.url == CANONICAL
    assert "contained /u/6/" in note and "your position among the Google accounts signed in there" in note and f"the address used is {CANONICAL}" in note
    assert "never from the browser's address bar" in note and "Deploy dialog or Manage deployments" in note


def test_a_clean_address_gets_no_tidying_note():
    assert ul.configuration_status({"UNSUBSCRIBE_URL": CANONICAL, "UNSUBSCRIBE_SECRET": "s" * 20}) == (ul.PageConfig(CANONICAL, "s" * 20), "")


def test_the_test_address_is_still_refused_after_the_tidying_and_a_pasted_line_break_is_still_caught_first():
    config, note = ul.configuration_status({"UNSUBSCRIBE_URL": f"https://script.google.com/macros/u/6/s/{ID}/dev", "UNSUBSCRIBE_SECRET": "s" * 20})
    assert config is None and "ends in /dev" in note and "contained" not in note
    config, note = ul.configuration_status({"UNSUBSCRIBE_URL": f"https://script.google.com/macros/u/6/s/{ID}/exec\nhttps://x", "UNSUBSCRIBE_SECRET": "s" * 20})
    assert config is None and "space or a line break" in note


def test_the_tidying_note_and_a_workspace_warning_can_come_together():
    config, note = ul.configuration_status({"UNSUBSCRIBE_URL": f"https://script.google.com/a/macros/kelson.agency/s/{ID}/exec?authuser=2", "UNSUBSCRIBE_SECRET": "s" * 20})
    assert config is not None and "contained ?authuser=2" in note and "/a/macros/" in note and "Who has access" in note


def test_the_link_made_from_a_tidied_address_has_the_clean_path_and_nothing_else_changed():
    config = ul.configuration({"UNSUBSCRIBE_URL": f"https://script.google.com/macros/u/6/s/{ID}/exec", "UNSUBSCRIBE_SECRET": "s" * 20})
    token = ul.make_token(SECRET, campaign="C", lead_id="L", email="a@b.com", brand="B", key="b")
    assert ul.page_url(config.url, token) == f"{CANONICAL}?t={token}"



# ================================================================ the static page on GitHub Pages

PAGES = "https://harish-1708.github.io/E-O-A/unsubscribe.html"


def with_page(page, url=CANONICAL):
    return ul.configuration_status({"UNSUBSCRIBE_URL": url, "UNSUBSCRIBE_SECRET": "s" * 20, "UNSUBSCRIBE_PAGE_URL": page})


def test_without_a_page_address_recipients_open_the_google_page_as_before():
    config, note = ul.configuration_status({"UNSUBSCRIBE_URL": CANONICAL, "UNSUBSCRIBE_SECRET": "s" * 20})
    assert config.page == "" and note == "" and ul.PageConfig("u", "s").page == ""


def test_a_github_pages_address_is_kept_exactly_and_without_a_warning():
    config, note = with_page(PAGES)
    assert config.page == PAGES and config.url == CANONICAL and note == ""
    assert with_page("  " + PAGES + " \n")[0].page == PAGES


@pytest.mark.parametrize("page, words", [("https://script.google.com/macros/s/X/exec", "is a Google address"), ("http://harish.github.io/u.html", "must start with https://"), ("harish.github.io/u.html", "must start with https://"),
                                         (PAGES + "?a=1", "with nothing after it"), (PAGES + "#top", "with nothing after it"), ("https://harish.github.io/u .html", "space or a line break"),
                                         ("https://SCRIPT.google.com/x", "is a Google address")])
def test_an_unusable_page_address_is_ignored_with_the_reason_and_recipients_get_the_google_page_instead(page, words):
    config, note = with_page(page)
    assert config is not None and config.page == "" and config.url == CANONICAL and words in note and "is NOT used" in note and "Google page instead" in note


def test_the_page_address_alone_never_turns_anything_on():
    assert ul.configuration_status({"UNSUBSCRIBE_PAGE_URL": PAGES}) == (None, "")
    assert ul.configuration_status({"UNSUBSCRIBE_PAGE_URL": PAGES, "UNSUBSCRIBE_URL": CANONICAL})[0] is None


def test_the_page_note_and_an_address_tidying_note_can_come_together():
    config, note = with_page("http://x.example/u.html", url=f"https://script.google.com/macros/u/6/s/{ID}/exec")
    assert config.url == CANONICAL and config.page == "" and "contained /u/6/" in note and "must start with https://" in note


@pytest.mark.parametrize("url, expected", [(PAGES, ""), ("https://unsubscribe.kelson.agency/", ""), ("https://x.example/a/b.html", "")])
def test_other_https_hosts_are_fine_as_a_page_address(url, expected):
    assert ul.page_address_problem(url) == expected


def test_the_token_and_the_origin_are_read_from_a_link():
    assert ul.token_from_url(f"{PAGES}?t=abc.def_-9") == "abc.def_-9" and ul.token_from_url(f"{PAGES}?x=1&t=abc") == "abc" and ul.token_from_url(PAGES) == "" and ul.token_from_url(None) == ""
    assert ul.origin_of(PAGES) == "https://harish-1708.github.io" and ul.origin_of("") == "" and ul.origin_of("not a url") == "" and ul.origin_of("http://127.0.0.1:8080/x") == "http://127.0.0.1:8080"


@pytest.mark.parametrize("status, body, state, words", [(200, "<!-- unsubscribe-page-v1 --> hello", "ok", "served the unsubscribe page"), (200, "<html>some other page</html>", "blocked", "not the unsubscribe page"),
                                                        (404, "", "blocked", "not being served"), (403, "", "blocked", "not being served"), (410, "", "blocked", "not being served"), (500, "", "unknown", "unexpected status")])
def test_the_static_page_is_classified_by_what_github_pages_answers(status, body, state, words):
    got_state, message = ul.classify_page(status, body)
    assert got_state == state and words in message
    assert ("Settings > Pages" in message) == (state == "blocked")                                       # a problem says how to turn the page on


def test_a_check_that_could_not_be_made_is_not_a_verdict_on_the_page():
    assert ul.classify_page(None, "", error="timed out")[0] == "unknown"


ORIGIN = "https://harish-1708.github.io"


def hop(status=200, acao="*", body="", where="script.google.com/macros/s/X/exec"):
    return {"where": where, "status": status, "acao": acao, "body": body}


GOOD_JSON = '{"ok": true, "state": "test", "brand": "B"}'


def test_every_answer_on_the_way_allowing_the_page_and_ending_in_data_is_ok():
    state, message = ul.classify_api([hop(302, "*"), hop(200, "*", GOOD_JSON)], ORIGIN)
    assert state == "ok" and "every answer (2 steps) allows a page on" in message and ORIGIN in message
    assert ul.classify_api([hop(200, ORIGIN, GOOD_JSON)], ORIGIN)[0] == "ok" and "1 step)" in ul.classify_api([hop(200, "*", GOOD_JSON)], ORIGIN)[1]


@pytest.mark.parametrize("hops, step", [([hop(302, ""), hop(200, "*", GOOD_JSON)], 1), ([hop(302, "*"), hop(200, "", GOOD_JSON)], 2), ([hop(200, "https://other.example", GOOD_JSON)], 1),
                                       ([hop(302, "*"), hop(302, None), hop(200, "*", GOOD_JSON)], 2)])
def test_a_missing_or_wrong_permission_on_any_step_names_that_step_and_blocks(hops, step):
    state, message = ul.classify_api(hops, ORIGIN)
    assert state == "blocked" and f"step {step} " in message and "Access-Control-Allow-Origin" in message and "browser would refuse to read the answer" in message


def test_an_answer_that_is_a_web_page_instead_of_data_points_at_the_deployed_version():
    state, message = ul.classify_api([hop(200, "*", "<!doctype html><html>page</html>")], ORIGIN)
    assert state == "blocked" and "web page instead of data" in message and "NEW VERSION" in message


@pytest.mark.parametrize("body, words", [('{"ok": false, "state": "invalid"}', "did not accept the link"), ("[]", "did not accept the link"), ('{"ok": true, "state": "valid"}', "state 'valid' where 'test' was expected")])
def test_data_that_is_not_the_expected_answer_blocks_with_the_reason(body, words):
    state, message = ul.classify_api([hop(200, "*", body)], ORIGIN)
    assert state == "blocked" and words in message
    assert ("same text" in message) == ("did not accept" in words)


def test_the_post_answer_has_no_state_to_compare_and_is_ok_when_it_says_ok():
    assert ul.classify_api([hop(302), hop(200, "*", '{"ok": true, "masked": "a***@x.com", "test": true}')], ORIGIN, expect_state="")[0] == "ok"


def test_a_last_step_that_is_not_ok_or_a_step_that_could_not_be_made_is_reported_apart():
    assert ul.classify_api([hop(302), hop(500, "*", "")], ORIGIN)[0] == "blocked" and "HTTP 500" in ul.classify_api([hop(302), hop(500, "*", "")], ORIGIN)[1]
    assert ul.classify_api([hop(302), {"where": "x", "error": "timed out"}], ORIGIN) == ("unknown", "step 2 could not be made (timed out).")
    assert ul.classify_api([], ORIGIN)[0] == "unknown"


def test_the_google_address_in_a_pages_config_js_is_read_whatever_the_quotes():
    assert ul.config_js_address('window.UNSUBSCRIBE_API = "https://a/exec";') == "https://a/exec" and ul.config_js_address("window.UNSUBSCRIBE_API='https://a/exec' ;") == "https://a/exec"
    assert ul.config_js_address("// window.UNSUBSCRIBE_API = 'x'\nwindow.UNSUBSCRIBE_API = \"https://real/exec\";") in ("x", "https://real/exec")
    assert ul.config_js_address("") == "" and ul.config_js_address(None) == "" and ul.config_js_address("var other = 1;") == ""


@pytest.mark.parametrize("found, status, state, words", [(CANONICAL, 200, "ok", "names the same Google script"), (CANONICAL + "/", 200, "ok", "same Google script"), (f"https://script.google.com/macros/u/6/s/{ID}/exec", 200, "ok", "same"),
                                                         ("https://script.google.com/macros/s/OTHER/exec", 200, "blocked", "points at"), ("", 200, "blocked", "does not set window.UNSUBSCRIBE_API"),
                                                         (CANONICAL, 404, "blocked", "HTTP 404"), (CANONICAL, 500, "blocked", "HTTP 500")])
def test_the_pages_config_must_name_the_same_google_script_as_the_secret(found, status, state, words):
    got_state, message = ul.classify_config(found, CANONICAL, status)
    assert got_state == state and words in message


def test_a_config_that_could_not_be_read_is_unknown():
    assert ul.classify_config("", CANONICAL, None, error="timed out")[0] == "unknown"
