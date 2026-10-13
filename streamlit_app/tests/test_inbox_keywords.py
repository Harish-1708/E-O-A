"""Reading a reply's words by keyword: which phrases count, what is ignored, and what must never be misread."""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import inbox_keywords as K  # noqa: E402
from inbox_keywords import INTERESTED, NOT_INTERESTED, LEAD  # noqa: E402


def _intent(body):
    reading = K.keyword_reading(body)
    return reading[0] if reading else None


# ================================================================ refusals

@pytest.mark.parametrize("body", [
    "Not interested", "Not interested.", "not interested", "NOT INTERESTED", "I'm not interested, thanks", "I’m not interested",
    "Not really interested", "Not at all interested", "I am no longer interested", "No interest on my end",
    "No thank you", "No thanks!", "No, thank you.", "no thanks", "Thanks, but no thanks", "Thank you, but no",
    "Not for me, thanks.", "Not a good fit for me", "I'll pass", "I'm going to pass on this one", "Going to pass", "I have to decline",
    "I will decline", "Thanks for reaching out but I'm not taking on brand work right now, best of luck with the campaign!",
    "I'm not accepting collabs at the moment", "Not doing paid partnerships anymore", "Not at this time", "Not right now, thanks",
    "Maybe another time", "I'm not available for this", "I'm not comfortable with this", "I won't be licensing this video",
    "Please remove me from your list", "Unsubscribe", "Please don't contact me again", "Stop emailing me", "Take me off your list",
    "Hi Harish,\n\nThank you so much for reaching out, however I am not interested at this time.\n\nBest,\nAndrea",
])
def test_a_refusal_is_not_interested(body):
    assert _intent(body) == NOT_INTERESTED, body


# ================================================================ yes

@pytest.mark.parametrize("body", [
    "Interested", "I'm interested!", "Definitely interested", "Yes!", "Yes please", "Yes, I'd love to do this", "yeah sure", "Sure, go ahead",
    "I would love to", "I'd love to work with DudeRobe", "We'd be happy to", "Sounds good", "Sounds great, send me the details", "Sounds like a plan",
    "Happy to!", "Glad to", "Count me in", "I'm in", "I'm on board", "Let's do it", "Let's work together", "Tell me more", "Please send the agreement",
    "Send me over the contract", "Absolutely", "OK, sounds good", "I'm open to it", "I'm excited to work together", "That would be great",
    "No problem, sounds good", "We have a deal", "Please go ahead and send it",
    "Hi Harish! Thank you so much for reaching out. I'd be happy to license the video.",
    "Interested. Sent from my iPhone > On Sep 10, 2026, at 9:22 AM, harish@kelson.agency wrote…",
])
def test_a_yes_is_interested(body):
    assert _intent(body) == INTERESTED, body


# ================================================================ negotiating / needs a follow-up

@pytest.mark.parametrize("body", [
    "I can't accept that offer. It's extremely low.", "That's too low for me", "What are your rates?", "How much is the budget?",
    "Would you be able to do $400?", "My rate for this is $400", "Let me check with my manager", "I'll get back to you", "I need some time to think",
    "I can definitely take a look, but this video was filmed several months ago",
    "Do you cover shipping?", "Does that include exclusivity?", "What does usage cover?",
    "Hi Harish, this is what I kept getting since last night. Not sure why it won't go through… could you resend the link?",
    "No at that rate, but I would do it for $400", "Not interested at $150, but I would do it for $400", "No thanks unless you can go higher",
    "Interested, but the rate is too low", "Yes but that is lower than my usual rate",
])
def test_a_negotiation_or_a_question_needs_a_follow_up(body):
    assert _intent(body) == LEAD, body


def test_money_beside_a_firm_refusal_is_still_a_refusal_when_there_is_no_counter():
    assert _intent("Not interested. I don't do paid posts.") == NOT_INTERESTED


@pytest.mark.parametrize("body", ["Please unsubscribe me, I charge $5,000 minimum", "Stop emailing me. Your offer of $150 is an insult, don't contact me again."])
def test_a_hard_stop_is_never_a_negotiation(body):
    assert _intent(body) == NOT_INTERESTED


# ================================================================ nothing to read

@pytest.mark.parametrize("body", [
    "", "   ", None, "Thanks!", "Received and confirmed. Thanks again Harish.", "Hello", "Thank you for your email.", "Got it.",
    "I am disinterested in everything", "uninterested",
])
def test_words_with_no_reading_give_none(body):
    assert K.keyword_reading(body) is None, body


# ================================================================ quoted history is never read as the answer

def test_a_question_we_asked_in_quoted_history_is_not_their_answer():
    assert K.keyword_reading("Thanks!\n\n> Are you interested in a paid collab?\n> We'd love to work with you") is None


@pytest.mark.parametrize("body, expected", [
    ("Sounds good\n\nOn Fri, Sep 4, 2026 at 11:52 AM Harish Dharan <harish@kelson.agency> wrote:\n> Not interested?", INTERESTED),
    ("Sounds good\n\nOn Fri, Sep 4, 2026 at 11:52 AM Harish Dharan\n<harish@kelson.agency> wrote:\n> Not interested?", INTERESTED),
    ("Sounds good\n\nOn Sep 4, 2026, at 11:52 AM, harish@kelson.agency wrote:\nI'm not interested in less than $200", INTERESTED),
    ("No thanks\n\n-----Original Message-----\nFrom: Harish\nSent: Friday\nWould you be interested?", NOT_INTERESTED),
    ("No thanks\n\nFrom: Harish Dharan <h@k.agency>\nSent: Friday, September 4, 2026\nTo: Andrea\nSubject: Collab\nAre you interested?", NOT_INTERESTED),
    ("Not interested\n\nSent from my iPhone\n\n> On Sep 4, Harish wrote: sounds good?", NOT_INTERESTED),
    ("Interested!\n________________________________\nFrom: Harish\nPlease say if you are not interested", INTERESTED),
    ("Yes please\n\nGet Outlook for iOS\n\nOn Friday someone wrote: no thanks", INTERESTED),
])
def test_only_what_the_person_wrote_is_read(body, expected):
    assert _intent(body) == expected, body


def test_a_reply_that_is_nothing_but_quoted_text_gives_none():
    assert K.keyword_reading("> not interested\n> no thank you") is None


def test_new_text_cuts_quotes_and_app_signatures_and_collapses_whitespace():
    assert K.new_text("Hi  there\n\nfriend\n> quoted\n\nOn Fri, Sep 4, 2026 at 9:00 AM X wrote:\n> more") == "Hi there friend"
    assert K.new_text("Thanks! Sent from my iPhone") == "Thanks!"
    assert K.new_text(None) == "" and K.new_text("") == ""


# ================================================================ which phrase decided

def test_the_phrase_that_decided_is_returned_for_transparency():
    assert K.keyword_reading("Sorry, not interested, thanks")[1] == "not interested"
    assert K.keyword_reading("Sounds great, send details")[1] == "sounds great"
    assert K.keyword_reading("What are your rates?")[1] in ("rates", "?")


def test_a_refusal_is_found_before_a_yes_so_not_interested_is_never_read_as_interested():
    assert _intent("I'm not interested") == NOT_INTERESTED and _intent("Not interested, but thank you, sounds great though") == NOT_INTERESTED


# ================================================================ only a hint, and bounded

def test_only_a_lead_reading_is_weak():
    assert K.is_weak(LEAD) and not K.is_weak(INTERESTED) and not K.is_weak(NOT_INTERESTED) and not K.is_weak("")


def test_a_huge_email_is_read_quickly_and_only_its_start_counts():
    hostile = "word " * 400_000 + " not interested"
    started = time.time()
    assert K.keyword_reading(hostile) is None                                   # the refusal is far beyond what is read
    assert time.time() - started < 1.0
    assert K.keyword_reading("No thanks. " + "x " * 400_000) == (NOT_INTERESTED, "no thanks")


def test_nasty_input_never_raises():
    for body in ["\x00" * 50, "(" * 5000, "On " * 3000 + "wrote:", "> " * 5000, "\u202e not interested", "$" * 1000, "wrote:" * 1000, 12345, ["list"], {"a": 1}]:
        K.keyword_reading(body)


# ================================================================ gaps found by deliberately breaking the rules

@pytest.mark.parametrize("body", ["Not interested, thanks for the offer", "No thank you. I don't discuss rates.", "Not interested in your compensation structure",
                                  "No thanks, your price is your business"])
def test_a_refusal_that_merely_mentions_an_offer_or_a_rate_is_still_a_refusal(body):
    """Words like offer / rate / price are in the counter-offer vocabulary, but without a 'but I would' or an amount nothing is being negotiated."""
    assert _intent(body) == NOT_INTERESTED, body


@pytest.mark.parametrize("body, expected", [
    ("I’ll pass", NOT_INTERESTED), ("Please don’t contact me again", NOT_INTERESTED), ("I’d love to", INTERESTED),
    ("I’m not taking on brand work", NOT_INTERESTED), ("Let’s do it", INTERESTED), ("That’s too low for me", LEAD), ("I can’t accept that, it’s lower than my rate", LEAD),
])
def test_phones_and_word_processors_send_curly_apostrophes_and_they_are_read_the_same(body, expected):
    assert _intent(body) == expected, body


@pytest.mark.parametrize("body, expected", [
    ("Sounds good\n\nFrom: Harish Dharan <h@k.agency>\nSent: Friday, September 4, 2026\nSubject: Collab\nI'm not interested in less than $200", INTERESTED),
    ("No thanks\n\nFrom: Harish\nDate: Fri, Sep 4\nSubject: x\nSounds great, we'd love to work with you", NOT_INTERESTED),
    ("Sounds good\n\nOn Fri, Sep 4, 2026 at 11:52 AM Harish Dharan\n<harish@kelson.agency> wrote:\nI'm not interested in less than $200", INTERESTED),
    ("Sounds good\n\nOn Fri, Sep 4, 2026 at 11:52 AM Harish Dharan <harish@kelson.agency> wrote:\nI'm not interested in less than $200", INTERESTED),
])
def test_history_that_is_not_marked_with_quote_signs_is_cut_off_too(body, expected):
    """Outlook and Apple Mail put the earlier message below a header with no '>' in front of it."""
    assert _intent(body) == expected, body


def test_a_pathological_email_is_read_in_a_fraction_of_a_second():
    started = time.time()
    K.keyword_reading("On " * 300_000)
    assert time.time() - started < 0.6                                           # only the first 20,000 characters are even searched
