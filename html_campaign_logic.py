"""Everything about HTML campaigns that needs no Sheet, no network and no Streamlit: the checks an HTML email must pass before it is used, the footer, the starter layouts,
and a plain-text version of an HTML email. Pure functions, so every rule can be tested on its own.

Placeholders are written {{ FirstName }} (double braces, named like the Sheet's columns), the same as in plain-text templates; the one reserved name is {{ footer }}, which stands for the campaign's footer.
"""
import html
import re
import urllib.parse
from html.parser import HTMLParser
from typing import Dict, Iterable, List, Optional, Tuple

FORMAT_TEXT = "text"
FORMAT_HTML = "html"
VALID_FORMATS = (FORMAT_TEXT, FORMAT_HTML)

PLACEHOLDER_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")
RESERVED_PLACEHOLDERS = ("footer",)
DEFAULT_OPT_OUT = 'To stop receiving these emails, reply "unsubscribe".'

GMAIL_CLIP_BYTES = 102 * 1024          # Gmail cuts a message off after about this much
SIZE_WARNING_BYTES = 90 * 1024         # warn a little before it
MANY_LINKS = 15                        # more links than this looks like spam
MIN_VISIBLE_TEXT = 80                  # an email with almost no text (only pictures) looks like spam

_DANGEROUS_TAGS = {"script", "iframe", "frame", "frameset", "object", "embed", "form", "input", "button", "textarea", "select", "applet", "base"}
_VOID_OR_IGNORED_TEXT_TAGS = {"script", "style", "head", "title"}


# ---------------------------------------------------------------- reading the HTML

class _Scan(HTMLParser):
    """Collects what the checks need: tags, attributes, images, links, stylesheet links and the visible text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags: List[str] = []
        self.images: List[Dict[str, Optional[str]]] = []
        self.links: List[str] = []
        self.event_handlers: List[str] = []
        self.js_urls: List[str] = []
        self.stylesheets: List[str] = []
        self.meta_refresh = 0
        self.style_blocks = 0
        self.text_parts: List[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        self.tags.append(tag)
        attributes = {name.lower(): value for name, value in attrs}
        for name, value in attributes.items():
            if name.startswith("on"):
                self.event_handlers.append(f"{tag} {name}")
            if name in ("href", "src", "action", "formaction", "background", "poster") and value and re.match(r"\s*(javascript|vbscript)\s*:", value, re.I):
                self.js_urls.append(f"{tag} {name}")
        if tag == "img":
            self.images.append({"src": attributes.get("src"), "alt": attributes.get("alt")})
        if tag == "a" and attributes.get("href"):
            self.links.append(attributes["href"])
        if tag == "meta" and (attributes.get("http-equiv") or "").strip().lower() == "refresh":
            self.meta_refresh += 1
        if tag == "link" and (attributes.get("rel") or "").lower() == "stylesheet":
            self.stylesheets.append(attributes.get("href") or "")
        if tag == "style":
            self.style_blocks += 1
        if tag in _VOID_OR_IGNORED_TEXT_TAGS:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag.lower() in _VOID_OR_IGNORED_TEXT_TAGS and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.text_parts.append(data)


def scan_html(markup: str) -> _Scan:
    scan = _Scan()
    scan.feed(markup or "")
    scan.close()
    return scan


def visible_text(markup: str) -> str:
    """The words a reader would see (no tags, no style or script content), with runs of white space collapsed."""
    return re.sub(r"\s+", " ", " ".join(scan_html(markup).text_parts)).strip()


# ---------------------------------------------------------------- what must never be sent

def unsafe_problems(markup: str) -> List[str]:
    """Constructs an email must never contain: scripts, frames, forms and form controls, embedded objects, event-handler attributes (onclick and the like), javascript: links, automatic
    redirects (meta refresh) and outside stylesheets. Returns one plain sentence per kind found (an empty list means safe). The sending code refuses to send anything that has any of these."""
    scan = scan_html(markup)
    problems = []
    found = sorted({tag for tag in scan.tags if tag in _DANGEROUS_TAGS})
    if found:
        problems.append("It contains " + ", ".join(f"<{tag}>" for tag in found) + ", which email must not have (scripts, frames and forms are blocked by mail apps and look like phishing).")
    if scan.event_handlers:
        problems.append("It contains event attributes such as " + ", ".join(sorted(set(scan.event_handlers))[:3]) + ", which are not allowed.")
    if scan.js_urls:
        problems.append("It contains a javascript: link, which is not allowed.")
    if scan.meta_refresh:
        problems.append("It contains a meta refresh (an automatic redirect), which is not allowed.")
    if scan.stylesheets:
        problems.append("It loads an outside stylesheet; use inline styles instead (most mail apps ignore outside stylesheets).")
    return problems


# ---------------------------------------------------------------- the checks shown in the editor and used before launching

def placeholders_in(text: str) -> List[str]:
    """Every distinct placeholder name used in the text, in order of first use."""
    seen, ordered = set(), []
    for name in PLACEHOLDER_RE.findall(text or ""):
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def has_opt_out(markup: str) -> bool:
    """Whether the email gives a way to opt out: it uses the campaign footer, or says unsubscribe / opt out somewhere in what a reader sees."""
    if "footer" in placeholders_in(markup):
        return True
    text = visible_text(markup).lower()
    return "unsubscribe" in text or "opt out" in text or "opt-out" in text


def html_checks(markup: str, plain_text: str, known_variables: Optional[Iterable[str]], footer: Optional[Dict] = None) -> Dict[str, List[str]]:
    """The checks for one HTML email. `known_variables` are the placeholder names that mean something (the template variables and the Sheet's own columns); None means the
    Sheet's columns are not known yet (a campaign with no leads), so placeholders are not checked rather than wrongly reported. Returns {"errors": [...], "warnings": [...]}:
    errors stop a campaign launching, warnings only advise."""
    errors: List[str] = []
    warnings: List[str] = []
    markup = markup or ""
    if not markup.strip():
        return {"errors": ["The HTML is empty."], "warnings": []}
    scan = scan_html(markup)

    errors.extend(unsafe_problems(markup))

    for image in scan.images:
        src = (image["src"] or "").strip()
        if image["alt"] is None:
            errors.append(f"An image has no alt text ({src[:60] or 'no address'}). Add alt=\"...\" (an empty alt=\"\" is fine for a purely decorative image).")
        if not src.lower().startswith("https://"):
            errors.append(f"An image is not a web address starting with https:// ({src[:60] or 'no address'}). Upload the picture somewhere public and paste its https link.")

    allowed = None if known_variables is None else set(known_variables) | set(RESERVED_PLACEHOLDERS)
    unknown = [] if allowed is None else [name for name in placeholders_in(markup) if name not in allowed]
    if unknown:
        errors.append("These placeholders match no Sheet column: " + ", ".join("{{ " + n + " }}" for n in unknown) + ".")

    if not (plain_text or "").strip():
        errors.append("The plain-text copy is empty. Every HTML email needs one (it is sent together with the design).")

    if not has_opt_out(markup):
        errors.append("There is no way to opt out. Add {{ footer }} where the footer should go, or write your own unsubscribe line.")
    if "footer" in placeholders_in(markup):
        missing = footer_problems(footer)
        if missing:
            errors.append("The email uses {{ footer }}, but the campaign's footer is incomplete: " + "; ".join(missing) + ".")

    size = len(markup.encode("utf-8"))
    if size > GMAIL_CLIP_BYTES:
        errors.append(f"The HTML is {size // 1024} KB. Gmail cuts messages off after about 102 KB and hides the rest (including the footer).")
    elif size > SIZE_WARNING_BYTES:
        warnings.append(f"The HTML is {size // 1024} KB, close to the point (about 102 KB) where Gmail cuts a message off.")
    if len(scan.links) > MANY_LINKS:
        warnings.append(f"The email has {len(scan.links)} links. Many links make a message look like spam.")
    if len(visible_text(markup)) < MIN_VISIBLE_TEXT:
        warnings.append("There is very little text in the email. Messages that are mostly pictures are more often marked as spam.")
    if scan.style_blocks:
        warnings.append("The email uses a <style> block. Some mail apps (older Outlook in particular) ignore it, so inline styles are safer.")
    if plain_text and "footer" not in placeholders_in(plain_text) and not re.search(r"unsubscribe|opt[ -]?out", plain_text, re.I):
        warnings.append("The plain-text copy has no opt-out line. Add {{ footer }} to it as well.")
    return {"errors": errors, "warnings": warnings}


# ---------------------------------------------------------------- link tagging (UTM)

DEFAULT_UTM_SOURCE = "email"
DEFAULT_UTM_MEDIUM = "outreach"
_A_HREF_RE = re.compile(r'(<a\b[^>]*?\bhref\s*=\s*)(["\'])(.*?)\2', re.I | re.S)


def utm_params(campaign_name: str, tracking: Optional[Dict], content: str = "") -> Optional[Dict[str, str]]:
    """The labels to add to links, or None when link tagging is off for the campaign. `tracking` is the campaign's settings mapping, e.g. {"utm": True}; source and medium may be
    set there, otherwise "email" and "outreach". The campaign name and the email (stage and variant, e.g. intro_A) say which campaign and which email a click came from."""
    if not isinstance(tracking, dict) or not tracking.get("utm"):
        return None
    params = {"utm_source": str(tracking.get("source") or DEFAULT_UTM_SOURCE).strip() or DEFAULT_UTM_SOURCE,
              "utm_medium": str(tracking.get("medium") or DEFAULT_UTM_MEDIUM).strip() or DEFAULT_UTM_MEDIUM}
    if campaign_name:
        params["utm_campaign"] = campaign_name
    if content:
        params["utm_content"] = content
    return params


def tag_links(markup: str, params: Optional[Dict[str, str]]) -> str:
    """Adds `params` to every web link (http or https) in an <a href="..."> of the email. A label the link already has is never overwritten or repeated, so tagging twice is the
    same as tagging once, and a link the designer already tagged is left as designed. Links that are not web links (mailto:, tel:, #anchor, relative) are never touched, and the
    rest of the markup is unchanged byte for byte. The address is written back safely escaped for an attribute."""
    if not params or not markup:
        return markup

    def _one(match):
        prefix, quote, raw = match.group(1), match.group(2), match.group(3)
        try:
            parts = urllib.parse.urlsplit(html.unescape(raw).strip())
            if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
                return match.group(0)
            present = {key.lower() for key, _ in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)}
            extra = [(key, value) for key, value in params.items() if key.lower() not in present]
            if not extra:
                return match.group(0)
            query = parts.query + ("&" if parts.query else "") + urllib.parse.urlencode(extra)
            tagged = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path or "/", query, parts.fragment))
        except ValueError:
            return match.group(0)                    # a malformed address (for example an unclosed [ ) is left exactly as the designer wrote it, never a failed send
        return f"{prefix}{quote}{html.escape(tagged, quote=True)}{quote}"

    return _A_HREF_RE.sub(_one, markup)


# ---------------------------------------------------------------- the footer

FOOTER_FIELDS = ("business_name", "address", "opt_out")


def footer_problems(footer: Optional[Dict]) -> List[str]:
    """What is missing from a campaign's footer settings (the opt-out wording has a default, so only the name and the postal address are required)."""
    footer = footer or {}
    problems = []
    if not str(footer.get("business_name") or "").strip():
        problems.append("the business name is missing")
    if not str(footer.get("address") or "").strip():
        problems.append("the postal address is missing")
    return problems


def _footer_values(footer: Dict) -> Tuple[str, str, str]:
    name = str(footer.get("business_name") or "").strip()
    address = str(footer.get("address") or "").strip()
    opt_out = str(footer.get("opt_out") or "").strip() or DEFAULT_OPT_OUT
    return name, address, opt_out


def build_footer_html(footer: Dict) -> str:
    """The footer block for an HTML email. Every value is escaped, so nothing typed in the settings can become markup."""
    name, address, opt_out = _footer_values(footer or {})
    return ('<div style="font-family:Arial,Helvetica,sans-serif;font-size:12px;line-height:1.5;color:#6b7280;text-align:center;padding:16px 8px;">'
            f'{html.escape(name)} &middot; {html.escape(address)}<br>{html.escape(opt_out)}</div>')


def build_footer_text(footer: Dict) -> str:
    """The same footer for the plain-text copy."""
    name, address, opt_out = _footer_values(footer or {})
    return f"--\n{name} · {address}\n{opt_out}"


# ---------------------------------------------------------------- adding and removing the footer in a design

_FOOTER_ROW_RE = re.compile(r"<tr\b[^>]*>\s*<td\b[^>]*>\s*\{\{\s*footer\s*\}\}\s*</td>\s*</tr>[ \t]*\n?", re.I)
_FOOTER_LINE_RE = re.compile(r"^[ \t]*\{\{\s*footer\s*\}\}[ \t]*\n", re.I | re.M)
_FOOTER_TOKEN_RE = re.compile(r"\{\{\s*footer\s*\}\}")


def footer_in_design(markup: str) -> bool:
    """Whether a design uses the campaign footer (has {{ footer }} anywhere)."""
    return "footer" in placeholders_in(markup)


def add_footer_placeholder(markup: str) -> str:
    """The design with {{ footer }} added at the very end of the page (just before </body> when it has one). A design that already has it is returned unchanged."""
    if footer_in_design(markup):
        return markup
    text = markup or ""
    closing = text.lower().rfind("</body>")
    if closing < 0:
        return text.rstrip("\n") + "\n{{ footer }}\n"
    return text[:closing] + "{{ footer }}\n" + text[closing:]


def remove_footer_placeholder(markup: str) -> str:
    """The design without {{ footer }}. A table row that holds nothing else but the placeholder (the shape the starter layouts use) goes with it; any other empty row, such
    as a spacer the designer made on purpose, is never touched."""
    text = _FOOTER_ROW_RE.sub("", markup or "")
    text = _FOOTER_LINE_RE.sub("", text)                  # a placeholder alone on its line takes the line with it
    return _FOOTER_TOKEN_RE.sub("", text)


# ---------------------------------------------------------------- a plain-text copy of an HTML email

_BLOCK_BREAK_TAGS = ("br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table")


def html_to_text(markup: str) -> str:
    """A readable plain-text version of an HTML email, to start the plain-text copy from. Links are kept as 'text (address)'. Placeholders are left as they are."""
    text = markup or ""
    text = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", "", text)
    text = re.sub(r'(?is)<a\b[^>]*?href\s*=\s*["\']([^"\']+)["\'][^>]*>(.*?)</a>', lambda m: f"{re.sub(r'<[^>]+>', '', m.group(2)).strip()} ({m.group(1)})" if m.group(2).strip() else m.group(1), text)
    for tag in _BLOCK_BREAK_TAGS:
        text = re.sub(rf"(?i)</?{tag}\b[^>]*>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    out, blank = [], 0
    for line in lines:
        if line:
            out.append(line)
            blank = 0
        else:
            blank += 1
            if blank == 1 and out:
                out.append("")
    return "\n".join(out).strip()


# ---------------------------------------------------------------- starter layouts

def _letter(inner: str, accent: str = "#2563eb") -> str:
    return ('<!DOCTYPE html>\n<html lang="en">\n<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Email</title></head>\n'
            '<body style="margin:0;padding:0;background:#f3f4f6;">\n'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;"><tr><td align="center" style="padding:24px 12px;">\n'
            '<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:100%;max-width:600px;background:#ffffff;border-radius:8px;">\n'
            + inner +
            '<tr><td>{{ footer }}</td></tr>\n</table>\n</td></tr></table>\n</body>\n</html>\n')


STARTER_LAYOUTS: Dict[str, Dict[str, str]] = {
    "simple_letter": {
        "label": "Simple letter",
        "description": "A clean white page that reads like a letter. Good for updates and personal-feeling announcements.",
        "html": _letter(
            '<tr><td style="padding:32px 32px 8px 32px;font-family:Arial,Helvetica,sans-serif;font-size:16px;line-height:1.6;color:#111827;">\n'
            '<p style="margin:0 0 16px 0;">Hi {{ FirstName }},</p>\n'
            '<p style="margin:0 0 16px 0;">Write the message here. Keep paragraphs short, and say clearly what you would like the reader to do.</p>\n'
            '<p style="margin:0 0 16px 0;">Thanks,<br>Your name</p>\n</td></tr>\n'),
    },
    "announcement": {
        "label": "Announcement",
        "description": "A coloured header, a headline, a short message and a button. Good for news and programme openings.",
        "html": _letter(
            '<tr><td style="background:#111827;padding:18px 32px;border-radius:8px 8px 0 0;font-family:Arial,Helvetica,sans-serif;font-size:18px;font-weight:bold;letter-spacing:1px;color:#ffffff;">YOUR BRAND</td></tr>\n'
            '<tr><td style="padding:32px;font-family:Arial,Helvetica,sans-serif;color:#111827;">\n'
            '<h1 style="margin:0 0 12px 0;font-size:26px;line-height:1.25;">Big news, {{ FirstName }}</h1>\n'
            '<p style="margin:0 0 20px 0;font-size:16px;line-height:1.6;">Say what is happening in a sentence or two, and why it matters to the reader.</p>\n'
            '<a href="https://example.com" style="display:inline-block;background:#2563eb;color:#ffffff;text-decoration:none;font-weight:bold;font-size:16px;padding:12px 24px;border-radius:6px;">See the details</a>\n'
            '</td></tr>\n'),
    },
    "product_spotlight": {
        "label": "Product spotlight",
        "description": "A highlighted feature block with a headline, a few lines and a button. Good for showing one product or offer.",
        "html": _letter(
            '<tr><td style="padding:32px 32px 8px 32px;font-family:Arial,Helvetica,sans-serif;font-size:16px;line-height:1.6;color:#111827;">\n'
            '<p style="margin:0 0 16px 0;">Hi {{ FirstName }},</p>\n</td></tr>\n'
            '<tr><td style="padding:0 32px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#eef2ff;border-radius:8px;"><tr><td style="padding:24px;font-family:Arial,Helvetica,sans-serif;color:#111827;">\n'
            '<div style="font-size:12px;font-weight:bold;letter-spacing:1px;color:#2563eb;margin-bottom:8px;">NEW</div>\n'
            '<h2 style="margin:0 0 10px 0;font-size:22px;line-height:1.3;">Product or offer name</h2>\n'
            '<p style="margin:0 0 16px 0;font-size:15px;line-height:1.6;">Two or three lines about what it is, who it is for and what makes it worth a look.</p>\n'
            '<a href="https://example.com" style="display:inline-block;background:#2563eb;color:#ffffff;text-decoration:none;font-weight:bold;font-size:15px;padding:11px 22px;border-radius:6px;">Take a look</a>\n'
            '</td></tr></table></td></tr>\n'
            '<tr><td style="padding:20px 32px 8px 32px;font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.6;color:#374151;">Questions? Just reply to this email.</td></tr>\n'),
    },
}
DEFAULT_LAYOUT = "simple_letter"


def starter_html(layout_key: str) -> str:
    """The HTML of a starter layout. An unknown key gives the default layout rather than an error."""
    return STARTER_LAYOUTS.get(layout_key, STARTER_LAYOUTS[DEFAULT_LAYOUT])["html"]


def starter_plain_body() -> str:
    """The message part of the starter plain-text copy (no Subject line): the same words as the designed starter, in plain form, ending with the footer placeholder."""
    return "Hi {{ FirstName }},\n\nWrite the same message here in plain words. This copy is sent together with the designed email.\n\nThanks,\nYour name\n\n{{ footer }}"


def starter_plain_text(subject: str = "Your subject here") -> str:
    """The starter plain-text copy that goes with every starter layout: a Subject line, a blank line, the message and the footer placeholder."""
    return f"Subject: {subject.strip() or 'Your subject here'}\n\n{starter_plain_body()}\n"
