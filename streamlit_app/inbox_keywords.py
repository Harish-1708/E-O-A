"""Reads what a reply SAYS, by keywords — for the many replies the AI left without a definite reading.

Used only to decide how a conversation is labelled in the Inbox (Interested / Not interested / Lead), and only when
the AI gave no definite answer: a definite AI reading always wins. Nothing here writes anything, and nothing is
stored — the label is worked out afresh each time from the words.

How it reads, and why:
* Only what the person JUST wrote. Quoted history ("> Are you interested?", "On Fri … wrote:", "Sent from my
  iPhone", an Outlook "From: … Sent: …" block) is cut off first, or a question WE asked would be read as their answer.
* Refusals are looked for first, so "not interested" can never be mistaken for "interested".
* A refusal that comes with a counter-offer ("no at that rate, but I'd do it for $400") is a negotiation, so a Lead.
* A plain question or a mention of money, with no yes or no, is only a WEAK Lead reading (see `is_weak`): it never
  overrides a clear yes or no found anywhere else in the conversation.
"""
import re
from typing import Optional, Tuple

INTERESTED = "Interested"
NOT_INTERESTED = "Not Interested"
LEAD = "Lead / Needs Follow-up"

_MAX_CHARS = 1500          # a reply's decision is in its first words
_MAX_INPUT = 20000         # and no more than this is even searched, so a huge email cannot slow the page

# ---- cutting off quoted history
_QUOTE_LINES = re.compile(r"^[ \t]*>.*$", re.MULTILINE)
_HISTORY_STARTS = [
    re.compile(r"(?:^|\s)On\b[^\n]{3,200}?\bwrote\s*:", re.IGNORECASE),                       # "On Fri, Sep 4 … wrote:" (also inline in a snippet)
    re.compile(r"(?:^|\n)\s*On\b[^\n]{0,200}\n[^\n]{0,200}\bwrote\s*:", re.IGNORECASE),        # the same wrapped over two lines
    re.compile(r"-{2,}\s*Original Message\s*-{2,}", re.IGNORECASE),
    re.compile(r"(?:^|\n)\s*From:[^\n]{0,200}\n\s*(?:Sent|Date):", re.IGNORECASE),            # an Outlook header block
    re.compile(r"_{10,}"),                                                                      # Outlook's divider
    re.compile(r"\bSent from my (?:iPhone|iPad|Android|Galaxy|Samsung|phone|mobile|BlackBerry)", re.IGNORECASE),
    re.compile(r"\bSent from Yahoo Mail", re.IGNORECASE),
    re.compile(r"\bGet Outlook for ", re.IGNORECASE),
]


def new_text(body) -> str:
    """The part of an email the person actually wrote, with quoted history and signatures-from-an-app removed."""
    text = str(body or "")[:_MAX_INPUT].replace("\r\n", "\n").replace("\r", "\n").replace("’", "'").replace("‘", "'")
    text = _QUOTE_LINES.sub("", text)
    for pattern in _HISTORY_STARTS:
        match = pattern.search(text)
        if match:
            text = text[:match.start()]
    return re.sub(r"\s+", " ", text).strip()[:_MAX_CHARS]


# ---- the phrases (all matched on lower-case text with straight apostrophes)
def _any(*patterns):
    return re.compile("|".join(f"(?:{p})" for p in patterns))


_NEGATIVE = _any(
    r"\bnot (?:at all |really |very |that |currently |particularly |super |too )?interested\b",
    r"\bno (?:real )?interest\b", r"\bno longer interested\b",
    r"\bno,? thank(?:s| you)\b", r"\bthanks,? but no\b", r"\bthank you,? but no\b", r"\bno thanks\b",
    r"\bnot for me\b", r"\bnot (?:a )?(?:good |great |right )?fit\b",
    r"\bi(?:'ll| will| am going to|'m going to)? pass\b", r"\bpass on (?:this|it|that)\b", r"\bgoing to pass\b",
    r"\bi(?:'ll| will)? have to decline\b", r"\bdecline\b", r"\bwill (?:have to )?decline\b",
    r"\bnot (?:taking|accepting|doing|open to|looking (?:for|to))\b[^.!?]{0,50}\b(?:work|collab\w*|brand|partnership\w*|deals?|offers?|licens\w+|sponsor\w*|projects?|campaigns?)\b",
    r"\bnot (?:available|able to (?:do|take|participate))\b",
    r"\bunsubscribe\b", r"\bremove me\b", r"\btake me off\b", r"\bstop (?:emailing|contacting|messaging|sending)\b",
    r"\bplease (?:don't|do not) (?:contact|email|message|reach out)\b", r"\bdo not (?:contact|email) me\b",
    r"\bnot (?:at this time|right now|this time|currently)\b", r"\bmaybe (?:another|some other|a different) time\b",
    r"\bi(?:'m| am) (?:going to )?(?:say )?no\b", r"\bmy answer is no\b", r"\bwill not be (?:doing|participating|licensing)\b",
    r"\bnot willing\b", r"\bwon't be (?:able|doing|licensing)\b", r"\bnot comfortable\b", r"\bnot licensing\b",
)

_HARD_STOP = _any(
    r"\bunsubscribe\b", r"\bremove me\b", r"\btake me off\b", r"\bstop (?:emailing|contacting|messaging|sending)\b",
    r"\bplease (?:don't|do not) (?:contact|email|message|reach out)\b", r"\bdo not (?:contact|email) me\b",
)

_POSITIVE = _any(
    r"\binterested\b",
    r"\b(?:i'?d|i would|we'?d|we would) (?:really )?(?:love|like|be (?:happy|glad|delighted|excited)) to\b",
    r"\b(?:would|will) love to\b", r"\bhappy to\b", r"\bglad to\b", r"\bexcited (?:to|about|for)\b",
    r"\bsounds (?:good|great|fantastic|awesome|perfect|fine|amazing)\b", r"\bsounds like a plan\b",
    r"\bcount me in\b", r"\blet'?s (?:do (?:it|this)|go|work together|move forward|make (?:it|this) happen)\b",
    r"\bi(?:'m| am) (?:in|down|on board|game)\b", r"\bon board\b", r"\bwe have a deal\b", r"\bdeal\b(?=[!. ,]|$)",
    r"\btell me more\b", r"\bsend (?:me )?(?:over )?(?:the |your |more |all )?(?:details|info|information|agreement|contract|link|terms|paperwork|license|licence|usage)",
    r"\bplease (?:send|share|forward)\b", r"\bgo(?:ing)? ahead\b", r"\bfeel free to (?:send|proceed|go)\b",
    r"\bmove forward\b", r"\bwould be (?:great|happy|glad|open)\b", r"\bi(?:'m| am) open to\b", r"\bopen to (?:it|this|working|that)\b",
    r"^(?:yes|yeah|yep|yup|sure|absolutely|definitely|okay|ok)\b",
)

# a negotiation: money, rates, questions about terms, "let me check", asking for time
_MONEY_OR_TERMS = _any(
    r"\$\s?\d", r"\b\d[\d,.]*\s?(?:usd|dollars|bucks)\b", r"\b(?:rate|rates|price|pricing|budget|fee|fees|compensation|payment|pay|paid|offer)\b",
    r"\bhow much\b", r"\busage\b", r"\bexclusiv\w*\b", r"\bnegotiat\w*\b", r"\bcounter\w*\b",
    r"\b(?:too|extremely|very|way|really) low\b", r"\blower than\b", r"\bhigher\b", r"\bmore (?:than|money|compensation)\b",
)
_CHECKING = _any(
    r"\b(?:let me|i'?ll|i will|i'?m going to) (?:check|think|look|get back|review|talk|discuss|see|consult)\b", r"\bget back to you\b",
    r"\btake a look\b", r"\bcheck (?:with|on)\b", r"\bneed (?:some )?time\b", r"\bmy (?:manager|agent|team|partner)\b",
)
_QUESTION = re.compile(r"\?")
_COUNTER_TO_A_REFUSAL = _any(
    r"\$\s?\d", r"\b(?:rate|rates|price|budget|fee|compensation|offer)\b", r"\b(?:too|extremely|very|way|really) low\b", r"\blower than\b",
    r"\b(?:but|however|unless)\b[^.!?]{0,80}\b(?:would|could|can|if|for)\b", r"\bif you (?:can|could|would|were)\b",
)
_PRICE_COMPLAINT = _any(r"\b(?:too|extremely|very|way|really) low\b", r"\blower than\b", r"\bcan(?:'t| not)? accept\b", r"\bnot (?:enough|worth)\b")


def keyword_reading(body) -> Optional[Tuple[str, str]]:
    """(intent, the phrase that decided it) for the words the person just wrote, or None when the words give no
    reading. The intent is one of INTERESTED, NOT_INTERESTED, LEAD."""
    text = new_text(body).lower()
    if not text:
        return None
    negative = _NEGATIVE.search(text)
    positive = _POSITIVE.search(text)
    if negative:
        if _HARD_STOP.search(text):
            return NOT_INTERESTED, _HARD_STOP.search(text).group(0).strip()          # never a negotiation, whatever else it says
        counter = _COUNTER_TO_A_REFUSAL.search(text)
        if counter and not _is_plain_decline(text, negative):
            return LEAD, counter.group(0).strip()
        return NOT_INTERESTED, negative.group(0).strip()
    if positive:
        complaint = _PRICE_COMPLAINT.search(text)
        if complaint:
            return LEAD, complaint.group(0).strip()
        return INTERESTED, positive.group(0).strip()
    lead = _MONEY_OR_TERMS.search(text) or _CHECKING.search(text) or _QUESTION.search(text)
    if lead:
        return LEAD, lead.group(0).strip()
    return None


def _is_plain_decline(text: str, negative) -> bool:
    """'No thanks, not interested in $$$ stuff' is still a decline; a counter-offer needs an actual 'but I would'
    or an 'if you' — a bare mention of money beside a firm refusal ('not interested, I don't do paid posts') is not."""
    return not re.search(r"\b(?:but|however|unless|if you)\b", text) and not re.search(r"\$\s?\d", text) \
        and not re.search(r"\b(?:too|extremely|very|way|really) low\b|\blower than\b", text)


def is_weak(intent: str) -> bool:
    """A Lead reading from keywords is only a hint (a question, a mention of money): it must never override a clear
    yes or no found elsewhere in the conversation."""
    return intent == LEAD
