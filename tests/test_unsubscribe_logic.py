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
                                 "https://script.google.com/macros/s/AKfy/exec?a=1", "https://unsubscribe.kelson.agency/page", "https://example.com/dev"])
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
