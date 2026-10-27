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
import unicodedata
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


def configuration_status(environ) -> Tuple[Optional[PageConfig], str]:
    """(config, note). `config` is None unless BOTH settings are present and usable; `note` says why (empty when it is simply not set up, which is a normal state: the emails then
    use the email-based unsubscribe exactly as before). A half-done or malformed set-up is reported, never guessed at."""
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
    return PageConfig(url, secret), ""


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
