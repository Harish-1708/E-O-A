"""The hosted unsubscribe page, engine side: the signed link in an HTML email, which brand an opt-out belongs to, and whether an address has opted out. PURE: it reads no Sheet
and sends nothing; the engine (outreach.py) feeds it rows and sends what it builds.

HTML campaigns only. A text campaign never builds a link and never reads the list.

How it fits together
  * Each HTML email carries a private link made for that one lead: a small record (campaign, lead, address, brand name, brand key, address) signed with a secret, so it cannot be
    forged or edited. The Google page (apps_script/Code.gs) checks the signature with the same secret and writes a row to the shared "Unsubscribes" tab.
  * An opt-out belongs to a BRAND: every HTML campaign that uses the same business name in its footer. One brand's opt-out never silences another brand. A campaign with no
    business name is its own brand. The key is worked out HERE and travels inside the signed link, so the page needs no copy of the rule.
  * Before every HTML send the engine reads the tab once and skips the addresses on it for that brand.
"""
import base64
import hashlib
import hmac
import json
import re
import unicodedata
from urllib.parse import urlsplit
from typing import Dict, Iterable, List, NamedTuple, Optional, Set, Tuple

UNSUBSCRIBE_TAB = "Unsubscribes"
UNSUBSCRIBE_COLUMNS = ["Timestamp", "Email", "Brand", "BrandKey", "Campaign", "LeadID", "Reason", "Details", "Source"]
REASONS = ["Too many emails", "Not relevant to me", "I never signed up", "Other"]
TOKEN_VERSION = 1
MIN_SECRET_LENGTH = 16
_REQUIRED_FIELDS = ("c", "l", "e", "b", "k", "a")


class PageConfig(NamedTuple):
    url: str
    secret: str


def address_problem(url: str) -> Tuple[bool, str]:
    """(refuse, note) for a page address that is usable as a web address but is the wrong KIND of Google address. A Google web app has a TEST address (ends in /dev) that opens only for the
    script's owner: pasted into the secret it works when YOU test it and shows everybody else 'Sorry, unable to open the file'. Refused. An address for people inside one Google
    Workspace (/a/macros/...) is kept but flagged, because it may not open for people outside it."""
    if re.search(r"\s", url):
        return True, "UNSUBSCRIBE_URL contains a space or a line break (check it was pasted whole, on one line), so the unsubscribe page is OFF."
    parts = urlsplit(url)
    if (parts.hostname or "").lower() != "script.google.com":
        return False, ""                                                  # some other host: nothing Google-specific to check
    path = parts.path.rstrip("/")
    if path.endswith("/dev"):
        return True, ("UNSUBSCRIBE_URL ends in /dev, which is Google's TEST address: it opens only for the script's owner and shows everyone else 'unable to open the file'. "
                      "Use the Web app URL from Deploy > Manage deployments (it ends in /exec), so the unsubscribe page is OFF.")
    if not path.endswith("/exec"):
        return True, "UNSUBSCRIBE_URL should end in /exec (the Web app URL from Deploy > Manage deployments), so the unsubscribe page is OFF."
    if "/a/macros/" in path:
        return False, ("UNSUBSCRIBE_URL contains /a/macros/, an address for people inside one Google Workspace. If people outside it see 'unable to open the file', set 'Who has access' to Anyone "
                       "and use the address that starts https://script.google.com/macros/s/.")
    return False, ""


def configuration_status(environ) -> Tuple[Optional[PageConfig], str]:
    """(config, note). `config` is None unless BOTH settings are present and usable; `note` says why (empty when it is simply not set up, which is a normal state: the emails then
    use the email-based unsubscribe exactly as before). A half-done or malformed set-up is reported, never guessed at. A warning can also come WITH a usable config."""
    url = str(environ.get("UNSUBSCRIBE_URL") or "").strip()
    secret = str(environ.get("UNSUBSCRIBE_SECRET") or "").strip()
    if not url and not secret:
        return None, ""
    if not url or not secret:
        return None, "UNSUBSCRIBE_URL and UNSUBSCRIBE_SECRET must both be set; only one is, so the unsubscribe page is OFF."
    if not url.lower().startswith("https://"):
        return None, "UNSUBSCRIBE_URL must start with https:// (mail apps only use secure links), so the unsubscribe page is OFF."
    if len(secret) < MIN_SECRET_LENGTH:
        return None, f"UNSUBSCRIBE_SECRET is too short (use at least {MIN_SECRET_LENGTH} characters), so the unsubscribe page is OFF."
    refuse, note = address_problem(url)
    if refuse:
        return None, note
    return PageConfig(url, secret), note


def configuration(environ) -> Optional[PageConfig]:
    return configuration_status(environ)[0]


def brand_key(business_name, campaign: str) -> str:
    """Which brand an opt-out belongs to: the footer's business name with case, spaces, punctuation and accents ignored ('Dube Robe', 'DUBEROBE' and 'dube-robe' are one brand).
    With no business name the campaign is its own brand, so an opt-out is never applied further than it can be justified."""
    folded = []
    for ch in str(business_name or "").casefold():
        if not ch.isalnum():
            continue                                                                          # spaces, punctuation, symbols such as ™ and loose accent marks are ignored
        base = unicodedata.normalize("NFKD", ch)[:1]
        folded.append(base if base.isascii() else unicodedata.normalize("NFKC", ch))          # an accent on a Latin letter is ignored; another alphabet's letters are left whole
    key = "".join(ch for ch in "".join(folded) if ch.isalnum())
    return key or "campaign:" + str(campaign or "").strip().casefold()


# ---------------------------------------------------------------- the signed link

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(secret: str, body: str) -> str:
    return _b64(hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest())


def make_token(secret: str, *, campaign: str, lead_id: str, email: str, brand: str, key: str, address: str = "", test: bool = False) -> str:
    """'<payload>.<signature>', both URL-safe. `test=True` marks a link made for a test email: the page works fully but records nothing."""
    payload = {"v": TOKEN_VERSION, "c": str(campaign), "l": str(lead_id), "e": str(email), "b": str(brand), "k": str(key), "a": str(address)}
    if test:
        payload["t"] = 1
    body = _b64(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    return f"{body}.{_sign(secret, body)}"


def verify_token(secret: str, token) -> Optional[Dict]:
    """The link's record when its signature is right and its shape is the expected one; None for anything else (altered, truncated, the wrong secret, the wrong version)."""
    try:
        body, signature = str(token).split(".")
        if not body or not hmac.compare_digest(signature.encode("ascii"), _sign(secret, body).encode("ascii")):
            return None
        payload = json.loads(_unb64(body).decode("utf-8"))
    except (ValueError, TypeError, UnicodeError):
        return None
    if not isinstance(payload, dict) or payload.get("v") != TOKEN_VERSION or any(not isinstance(payload.get(field), str) for field in _REQUIRED_FIELDS):
        return None
    return payload


def page_url(base_url: str, token: str) -> str:
    return f"{base_url}{'&' if '?' in base_url else '?'}t={token}"


def mask_email(email: str) -> str:
    """'ann@gmail.com' -> 'a***@gmail.com': enough for the person to recognise their address, not enough for anyone else to read it."""
    local, at, domain = str(email or "").partition("@")
    return f"{local[:1]}***@{domain}" if at and local and domain else "***"


# ---------------------------------------------------------------- the list

def load_pairs(rows: Iterable[Dict]) -> Set[Tuple[str, str]]:
    """{(address, brand key)} from the Unsubscribes tab's rows. An address is compared in lower case and without surrounding spaces."""
    pairs = set()
    for row in rows or []:
        email = str(row.get("Email") or "").strip().lower()
        if email:
            pairs.add((email, str(row.get("BrandKey") or "").strip()))
    return pairs


def is_suppressed(email: str, key: str, pairs: Set[Tuple[str, str]]) -> bool:
    """Whether this address has opted out of this brand. A row someone added by hand with no brand key means 'every brand'."""
    address = str(email or "").strip().lower()
    return bool(address) and ((address, key) in pairs or (address, "") in pairs)


def suppressed_leads(leads: List[Dict], key: str, pairs: Set[Tuple[str, str]]) -> List[Dict]:
    return [lead for lead in leads if is_suppressed(lead.get("Email", ""), key, pairs)]


# ---------------------------------------------------------------- can an outsider open the page?

PROBE_FIX = ("Almost always the deployment is not set to 'Who has access: Anyone', or the address is the test one. Fix: in Google, Deploy > Manage deployments > Edit > Who has access: Anyone > "
             "Version: New version > Deploy, then copy the Web app URL (ending /exec) into the UNSUBSCRIBE_URL secret.")


LINK_MARKERS = ("Unsubscribe me", "This is a test link from your team")        # text only the real page contains, to recognise it in what Google answers with


def _where(final_url: str) -> str:
    """Where the request ended up (host and path only, never the query, which holds the link's token)."""
    parts = urlsplit(str(final_url or ""))
    return f"{parts.netloc}{parts.path}" if parts.netloc else ""


def classify_probe(status: Optional[int], final_url: str, body: str, error: str = "", expect: Tuple[str, ...] = ()) -> Tuple[str, str]:
    """(state, message) for what an address answered when opened from the internet WITHOUT signing in, which is what a recipient's request looks like to Google. State is 'ok',
    'blocked', 'signin' or 'unknown'. `expect` is text only the real page contains: when given, an OK answer also says whether the page itself was seen. A best-effort check: only
    'blocked' and 'signin' are reported as problems, and 'unknown' never blocks anything."""
    text = str(body or "").lower()
    if error:
        return "unknown", f"could not check the page from here ({error})."
    where = _where(final_url)
    tail = f" (HTTP {status}, ended at {where})" if where else f" (HTTP {status})"
    if "unable to open the file" in text:
        return "blocked", "Google answered 'Sorry, unable to open the file at present'. That is what every recipient except you would see" + tail + ". " + PROBE_FIX
    if "accounts.google.com" in str(final_url or "").lower():
        return "signin", "Google asked for a sign-in, so a recipient who is not signed in to Google could not open it" + tail + ". " + PROBE_FIX
    if status in (401, 403, 404, 410):
        return "blocked", f"Google answered {status}, so recipients could not open the page{tail}. " + PROBE_FIX
    if status == 200:
        if expect:
            seen = any(marker.lower() in text for marker in expect)
            return "ok", ("Opened from the internet without signing in: Google served the unsubscribe page itself" + tail + "." if seen else
                          "Opened from the internet without signing in: Google answered with a page" + tail + ", but this check could not see the unsubscribe page's own text in it, so it cannot confirm more.")
        return "ok", ("Opened from the internet without signing in: Google served the page" + (" and it answered as the unsubscribe page" if "this link is not valid" in text else "") + tail + ".")
    return "unknown", f"the address answered with an unexpected status ({status})."


def compare_probes(bare_state: str, exact_state: str) -> str:
    """What the two checks together mean: the address on its own, and the exact link in the email (the address plus its token)."""
    bad = ("blocked", "signin")
    if bare_state in bad:
        return "DIAGNOSIS: the page address itself is refused, so the problem is the deployment or the address in the secret, not the individual link."
    if exact_state in bad:
        return ("DIAGNOSIS: the address on its own opens but the exact link in the email does NOT. The problem is the link (its length or its token), not the deployment: send the exact link "
                "printed above to whoever can fix it, and do not rely on it.")
    if bare_state == "ok" and exact_state == "ok":
        return ("DIAGNOSIS: BOTH open for an anonymous visitor, so the link itself is fine. If a recipient still sees Google's 'unable to open the file', it comes from THEIR browser or Google "
                "sign-in (several Google accounts signed in at once, a company account that blocks outside web apps, or an in-app browser). Ask them to open the same link in a private "
                "window or in their phone's own browser.")
    return "DIAGNOSIS: not conclusive: at least one of the two checks could not be made from here."
