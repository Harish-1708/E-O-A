"""Lead statuses in the Response Sheet's intent columns.

Two commands, both of which only ever write the three INTENT columns (Intent, IntentConfidence, IntentClassifiedAt) —
never a reply's text, its classification, or what the system did about it:

  set   A status somebody chose by hand in the Inbox, for one lead. It is written on every reply row of that lead with
        IntentConfidence "Manual", so it beats the AI and the keywords everywhere (the Inbox, the Performance page, …).
        "auto" takes a manual status back off, leaving those cells blank so the automatic reading applies again.
  fill  For every genuine reply the AI left without a definite reading (Intent blank or "Unclear"), reads the words with
        the same keyword rules the Inbox uses and, when they give a CLEAR yes or no, writes it with IntentConfidence
        "Keywords". A question or a mention of money is NOT written (it is only a hint), a manual status is never
        touched, and running it again changes nothing. This is what makes the Performance page follow the Inbox.

Output is only counts and row numbers: this repository's Actions logs are public, so no name, address or reply text is
ever printed.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime
from typing import Dict, List, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "streamlit_app"))

import inbox_keywords as K  # noqa: E402  (pure Python: no Streamlit)

DATETIME_FMT = "%Y-%m-%d %H:%M:%S"
VALID_STATUSES = set(K.STATUS_TO_INTENT) | {"auto"}
PAYLOAD_PREFIX = "status_changes/"
MAX_PAYLOAD_BYTES = 10_000
CHUNK_ROWS = 100                 # one Google request per chunk (Google allows only 60 write requests a minute)
PAUSE_SECONDS = 2.0


# ---------------------------------------------------------------- what to write (pure)

def _is_genuine(row: Dict) -> bool:
    return str(row.get("Classification") or "").lower().startswith("genuine")


def _update(row: Dict, intent: str, confidence: str, classified_at: str) -> Dict:
    return {"row": row["_row"], "full_body": None, "snippet": None, "message_date": None,
            "intent_fields": {"Intent": intent, "IntentConfidence": confidence, "IntentClassifiedAt": classified_at}}


def validate_payload(payload, campaign: str) -> Tuple[str, str]:
    """(lead_id, status) from a status-change payload, or ValueError saying what is wrong."""
    if not isinstance(payload, dict):
        raise ValueError("the payload is not a JSON object")
    lead_id = str(payload.get("lead_id") or "").strip()
    status = str(payload.get("status") or "").strip()
    if not lead_id or lead_id.startswith("_no_lead_"):
        raise ValueError("the payload names no lead")
    if status not in VALID_STATUSES:
        raise ValueError(f"unknown status {status!r}")
    if str(payload.get("campaign") or "") != campaign:
        raise ValueError("the payload is for a different campaign")
    return lead_id, status


def plan_manual_status(rows: List[Dict], lead_id: str, status: str, now: str) -> List[Dict]:
    """Every reply row of the lead gets the chosen status ('auto': the manual ones are blanked instead)."""
    mine = [r for r in rows if str(r.get("LeadID", "")).strip() == str(lead_id)]
    if status == "auto":
        return [_update(r, "", "", "") for r in mine if str(r.get("IntentConfidence") or "").strip() == K.MANUAL_CONFIDENCE]
    return [_update(r, K.STATUS_TO_INTENT[status], K.MANUAL_CONFIDENCE, now) for r in mine]


def plan_keyword_fill(rows: List[Dict], now: str) -> List[Dict]:
    """Rows to write: genuine replies with no definite AI reading whose words say a clear yes or no."""
    updates = []
    for row in rows:
        if not _is_genuine(row):
            continue
        intent = str(row.get("Intent") or "").strip()
        confidence = str(row.get("IntentConfidence") or "").strip()
        if confidence == K.MANUAL_CONFIDENCE or (intent and intent.lower() != "unclear"):
            continue                                                # a manual or definite reading is never replaced
        words = next((v for v in (row.get("FullBody"), row.get("Snippet")) if str(v or "").strip()), "")
        reading = K.keyword_reading(words)
        if reading and not K.is_weak(reading[0]):
            updates.append(_update(row, reading[0], K.KEYWORD_CONFIDENCE, now))
    return updates


def write_updates(sheets, updates: List[Dict], sleep=time.sleep) -> int:
    """Writes in requests of CHUNK_ROWS rows with a short pause between them. Returns how many rows were written."""
    written = 0
    for index in range(0, len(updates), CHUNK_ROWS):
        if index:
            sleep(PAUSE_SECONDS)
        chunk = updates[index:index + CHUNK_ROWS]
        sheets.update_response_rows(chunk)
        written += len(chunk)
    return written


# ---------------------------------------------------------------- the commands

def _connect(campaign: str):
    import outreach                                              # only the real run needs the engine
    return outreach._connect_sheets(outreach.get_campaign(campaign))


def cmd_set(args) -> int:
    path = str(args.file or "")
    normalised = os.path.normpath(path).replace("\\", "/")
    if not normalised.startswith(PAYLOAD_PREFIX) or ".." in path.split("/"):
        print(f"Refusing to read {path!r}: a status payload must be under {PAYLOAD_PREFIX}")
        return 1
    if not os.path.isfile(path) or os.path.getsize(path) > MAX_PAYLOAD_BYTES:
        print("The payload file is missing or too large.")
        return 1
    try:
        with open(path, encoding="utf-8") as handle:
            lead_id, status = validate_payload(json.load(handle), args.campaign)
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"The payload is not valid: {exc}")
        return 1
    sheets = _connect(args.campaign)
    updates = plan_manual_status(sheets.get_all_responses(), lead_id, status, datetime.now().strftime(DATETIME_FMT))
    written = write_updates(sheets, updates)
    print(f"Set lead {lead_id} to '{status}' on {written} reply row(s)." if written else f"Lead {lead_id}: nothing to change.")
    return 0


def cmd_fill(args) -> int:
    if args.all:
        import outreach
        campaigns = list(outreach.discover_campaign_names())
    else:
        campaigns = [args.campaign]
    failed = 0
    total = 0
    for name in campaigns:
        try:
            sheets = _connect(name)
            updates = plan_keyword_fill(sheets.get_all_responses(), datetime.now().strftime(DATETIME_FMT))
            if args.dry_run:
                print(f"{name}: would label {len(updates)} repl{'y' if len(updates) == 1 else 'ies'} from their words.")
            else:
                total += write_updates(sheets, updates)
                print(f"{name}: labelled {len(updates)} repl{'y' if len(updates) == 1 else 'ies'} from their words.")
        except Exception as exc:  # noqa: BLE001 - one campaign failing must not stop the others
            failed += 1
            print(f"{name}: could not be processed ({type(exc).__name__}).")
    if args.dry_run:
        print("\nDRY RUN — nothing was written.")
    elif failed:
        print(f"\n{failed} campaign(s) could not be processed; the others were done.")
    return 1 if failed else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Lead statuses in the Response Sheet's intent columns.")
    sub = parser.add_subparsers(dest="command", required=True)
    p_set = sub.add_parser("set", help="Apply a status chosen by hand in the Inbox (from a committed payload file).")
    p_set.add_argument("--campaign", required=True)
    p_set.add_argument("--file", required=True)
    p_set.set_defaults(func=cmd_set)
    p_fill = sub.add_parser("fill", help="Label replies the AI left unclear from what they say.")
    group = p_fill.add_mutually_exclusive_group(required=True)
    group.add_argument("--campaign")
    group.add_argument("--all", action="store_true")
    p_fill.add_argument("--dry-run", action="store_true")
    p_fill.set_defaults(func=cmd_fill)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
