"""The static unsubscribe page (docs/unsubscribe.html, shown by GitHub Pages): what it is allowed to do, that it shows everything it is given as plain text, that it sends no cookies,
and that its wording, reasons and requests stay in step with the Google script (apps_script/Code.gs). The page's behaviour in a real browser is proved separately, in a browser."""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unsubscribe_logic as ul  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


HTML, SCRIPT, CONFIG, CODE = read("docs", "unsubscribe.html"), read("docs", "unsubscribe.js"), read("docs", "config.js"), read("apps_script", "Code.gs")


def policy():
    return re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)"', HTML).group(1)


# ================================================================ what the page may do

def test_the_page_may_load_only_its_own_script_and_talk_only_to_the_google_script():
    rules = dict(rule.strip().split(" ", 1) for rule in policy().split(";") if rule.strip())
    assert rules["default-src"] == "'none'" and rules["script-src"] == "'self'" and rules["connect-src"] == "https://script.google.com https://script.googleusercontent.com"
    assert rules["base-uri"] == "'none'" and rules["form-action"] == "'none'" and "img-src" not in rules and "frame-src" not in rules and "'unsafe-eval'" not in policy()


def test_the_only_scripts_are_its_own_two_files_and_nothing_is_written_inline():
    scripts = re.findall(r"<script\b([^>]*)>(.*?)</script>", HTML, re.S)
    assert [attrs.strip() for attrs, _ in scripts] == ['src="config.js"', 'src="unsubscribe.js"'] and all(content.strip() == "" for _, content in scripts)
    assert not re.search(r"\son[a-z]+\s*=", HTML, re.I) and "javascript:" not in HTML.lower()


def test_the_page_loads_nothing_from_anywhere_else_and_is_kept_out_of_search_and_referrers():
    assert not re.search(r"<(link|img|iframe|object|embed|form)\b", HTML, re.I) and "@import" not in HTML and "url(" not in HTML
    assert '<meta name="robots" content="noindex, nofollow">' in HTML and '<meta name="referrer" content="no-referrer">' in HTML and 'name="viewport"' in HTML and '<html lang="en">' in HTML


def test_every_web_address_in_the_page_files_is_a_google_script_address():
    found = set(re.findall(r"https?://[A-Za-z0-9._/-]+", HTML + SCRIPT + CONFIG))
    assert {url for url in found if "script.google.com" not in url and "script.googleusercontent.com" not in url} == set()


def test_the_page_says_what_to_do_when_javascript_is_off():
    assert "<noscript>" in HTML and "reply <b>unsubscribe</b> to the email you received" in HTML


def test_the_marker_the_engines_check_looks_for_is_in_the_page():
    assert f"<!-- {ul.PAGE_MARKER}:" in HTML


# ================================================================ it shows text as text and sends nothing but what it must

@pytest.mark.parametrize("unsafe", ["innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "setInterval(", "location.href", "location.replace", "window.open", "localStorage", "sessionStorage",
                                    "document.cookie", "XMLHttpRequest", "postMessage", "importScripts"])
def test_the_script_never_uses_anything_that_could_run_text_or_keep_data(unsafe):
    assert unsafe not in SCRIPT


def test_everything_from_the_server_goes_onto_the_page_as_text():
    assert "textContent" in SCRIPT and "createTextNode" in SCRIPT and SCRIPT.count("textContent") >= 5
    assert "setTimeout(function () { controller.abort(); }" in SCRIPT and not re.search(r"setTimeout\(\s*[\"']", SCRIPT)


def test_the_requests_carry_no_cookies_no_referrer_and_no_cache_and_the_post_is_plain_text_so_there_is_no_preflight():
    assert "credentials: 'omit'" in SCRIPT and "referrerPolicy: 'no-referrer'" in SCRIPT and "cache: 'no-store'" in SCRIPT and "'Content-Type': 'text/plain;charset=utf-8'" in SCRIPT
    assert "application/json" not in SCRIPT and SCRIPT.count("fetch(") == 1 and "mode: 'no-cors'" not in SCRIPT


def test_it_will_only_talk_to_a_google_web_app_address_of_exactly_the_right_shape():
    assert r"/^https:\/\/script\.google\.com\/macros\/s\/[A-Za-z0-9_-]+\/exec$/" in SCRIPT
    pattern = re.compile(r"^https://script\.google\.com/macros/s/[A-Za-z0-9_-]+/exec$")
    assert pattern.match(CONFIG_ADDRESS) and not pattern.match("https://script.google.com/macros/u/6/s/X/exec") and not pattern.match("https://evil.example/macros/s/X/exec")
    assert not pattern.match("https://script.google.com/macros/s/X/dev") and not pattern.match("https://script.google.com/macros/s/X/exec?a=1")


def test_the_link_is_sent_only_to_that_address_and_a_very_long_link_is_refused_before_any_request():
    assert re.search(r"token\.length > 2000\)", SCRIPT) and "encodeURIComponent(token)" in SCRIPT and SCRIPT.count("request(") == 3


# ================================================================ the address in config.js

CONFIG_ADDRESS = re.search(r'window\.UNSUBSCRIBE_API = "([^"]*)";', CONFIG).group(1)


def test_config_js_names_one_clean_google_web_app_address_that_the_engine_accepts_unchanged():
    config, note = ul.configuration_status({"UNSUBSCRIBE_URL": CONFIG_ADDRESS, "UNSUBSCRIBE_SECRET": "s" * 20})
    assert config is not None and config.url == CONFIG_ADDRESS and note == "" and "/u/" not in CONFIG_ADDRESS


def test_config_js_tells_the_person_where_to_copy_the_address_from():
    assert "NEVER from your browser's address bar" in CONFIG and "/exec" in CONFIG and "NEW deployment" in CONFIG


# ================================================================ in step with the Google script

def js_list(name):
    return re.findall(r"'([^']*)'", re.search(rf"var {name} = \[(.*?)\];", SCRIPT, re.S).group(1))


def test_the_reasons_the_page_falls_back_to_are_the_four_the_script_offers():
    assert js_list("FALLBACK_REASONS") == ul.REASONS
    assert re.search(r"var REASONS = \[(.*?)\];", CODE, re.S) and re.findall(r"'([^']*)'", re.search(r"var REASONS = \[(.*?)\];", CODE, re.S).group(1)) == ul.REASONS


def test_the_limit_on_what_a_person_can_type_is_the_scripts_limit_and_is_put_on_the_box():
    assert re.search(r"var MAX_DETAILS = (\d+);", SCRIPT).group(1) == re.search(r"var MAX_DETAILS = (\d+);", CODE).group(1) == "500" and "details.maxLength = MAX_DETAILS;" in SCRIPT


def test_only_an_answer_that_says_ok_true_counts_as_unsubscribed():
    assert "if (!result || result.ok !== true) { throw new Error('refused'); }" in SCRIPT


def test_the_pages_two_requests_are_the_two_the_script_answers():
    assert "'?api=info&t='" in SCRIPT and "api === 'info'" in CODE and "api: 'confirm'" in SCRIPT and "body.api === 'confirm'" in CODE
    for field in ("state", "brand", "masked", "address", "reasons", "test"):
        assert f"{field}:" in CODE.split("function info_")[1].split("function confirmApi_")[0], field
    for state in ("valid", "test", "not set up"):
        assert f"'{state}'" in SCRIPT or state in SCRIPT


@pytest.mark.parametrize("words", ["This link is not valid", "The unsubscribe link is incomplete or was changed.", "To stop these emails, reply unsubscribe to the email you received and we will remove you.",
                                   "Unsubscribe is not available right now", "You are unsubscribed", "Thank you for telling us why. It helps us send better email.",
                                   "Unsubscribed by mistake? Reply to any of our emails and we will add you back.", "Test only: nothing was recorded.", "This is a test link from your team. Nothing will be recorded.",
                                   "No, keep me subscribed", "You are still subscribed", "No problem. Nothing has changed.", "Unsubscribe me", "Tell us more (optional)", "No information was recorded.",
                                   "Why are you leaving? ", "Something went wrong. "])
def test_what_the_page_says_is_what_the_google_page_says(words):
    stripped = words.replace("unsubscribe to the email", "<b>unsubscribe</b> to the email")
    assert words in SCRIPT and (words in CODE or stripped in CODE or words.strip() in CODE)


def test_the_thank_you_promises_only_what_the_system_does():
    assert "emails like this one" in SCRIPT and "all Kelson" not in SCRIPT and "from all" not in SCRIPT.lower()
