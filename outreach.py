#!/usr/bin/env python3
"""
Outreach Automation — single-file version, SMTP/IMAP edition.

Runs entirely on GitHub Actions. Authentication is Gmail App Passwords
(generated once on a Google webpage, pasted into a GitHub secret) — no
OAuth, no client_secret.json, no browser code flow, no local execution.

Usage:
    python outreach.py preview        --campaign NAME --stage NAME --batch-size N [--variant A]
    python outreach.py send           --campaign NAME --stage NAME --batch-size N [--variant A]
    python outreach.py check-replies  --campaign NAME
    python outreach.py dashboard      --campaign NAME | --all

See README.md for full setup (Google Sheet + service account + App Passwords).
"""

import argparse
import base64
import concurrent.futures
import copy
import email
import html as html_lib
import imaplib
import json
import os
import random
import re
import smtplib
import ssl
import sys
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from email.header import decode_header
from html.parser import HTMLParser
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from email.utils import make_msgid, formatdate, parsedate_to_datetime, getaddresses
from typing import Dict, List, Optional, Tuple

import yaml
from dateutil import parser as dateparser
import gspread
import requests

import html_campaign_logic as hc
import unsubscribe_logic as ul


# =============================================================================
# SECTION 1: Constants — sheet column names and tab-name conventions
# =============================================================================

MASTER_COLUMNS = [
    "LeadID",
    "FirstName",          # Optional — blank renders as "there" in templates
    "LastName",             # Optional
    "Email",                 # MANDATORY — the only required field per lead
    "Company",                # Optional — blank renders as "your team"
    "Campaign",
    "Approval",               # Informational only — no longer gates sending. Pending | Yes | No | Paused
    "SenderAccount",          # Optional — which account to send from. Locked in
                                # after first send so later stages match.
    "RequestedAction",        # Free-text, NOT read by the system.
    "CurrentStage",
    "ScheduledAt",
    "IntroSentAt",
    "IntroVariant",
    "FollowUp1SentAt",
    "FollowUp1Variant",
    "FollowUp2SentAt",
    "FollowUp2Variant",
    "FollowUp3SentAt",
    "FollowUp3Variant",
    "FollowUp4SentAt",
    "FollowUp4Variant",
    "FollowUp5SentAt",
    "FollowUp5Variant",
    "FollowUp6SentAt",
    "FollowUp6Variant",
    "FollowUp7SentAt",
    "FollowUp7Variant",
    "FollowUp8SentAt",
    "FollowUp8Variant",
    "FollowUp9SentAt",
    "FollowUp9Variant",
    "FollowUp10SentAt",
    "FollowUp10Variant",
    "NextEligibleAt",
    "ReplyStatus",             # "" | Replied
    "ReplyAt",
    "LastInboundClassification",
    "LastInboundAt",
    "Status",                   # Doubles as "Last Action"
    "LastActionAt",
    "Error",
    "MessageID",
    "ThreadReferences",
    "ThreadSubject",           # Set automatically whenever a stage sends with a
                                # non-blank Subject. Leave a template's Subject
                                # line blank to continue this same thread
                                # ("Re: <ThreadSubject>") instead of starting a
                                # new one — see render_email / Section 5.
    "AsanaTaskGID",            # Set automatically the first time a lead is synced
                                # to Asana — checked before every future sync so a
                                # lead never gets a second task created for it.
                                # Never edit this by hand.
    "ManualAsanaStage",        # Optional human override for this ONE lead's Asana
                                # pipeline stage — e.g. "Rights Secured" or
                                # "Declined / Dead". When set, a sync uses this
                                # value instead of auto-deriving the stage from
                                # send/reply history, and — unlike the "never move
                                # a task already sitting in Rights Secured/Declined"
                                # protection, which only looks at Asana's current
                                # state — this can also apply on a brand-new task's
                                # very first creation. Leave blank for normal,
                                # fully-automatic stage tracking.
    "LastSyncedAsanaStage",    # Set automatically after every sync that
                                # successfully processes this lead — the Asana
                                # section the sync itself left the task at,
                                # whether that's a fresh move or just confirming
                                # where it already was. Never edit this by hand;
                                # it exists purely so a LATER sync can tell a
                                # direct, manual Asana edit (the live section no
                                # longer matches what THIS field says) apart from
                                # a ManualAsanaStage override that's simply never
                                # been applied yet — see decide_asana_sync_action.
]
# NOTE: this is the REQUIRED prefix of the Master header row. You may add
# extra columns of your own AFTER these (e.g. "Industry", "JobTitle") and
# reference them directly in templates as {{Industry}} — see Section 4.

RESPONSES_COLUMNS = [
    "ResponseID",
    "LeadID",
    "Campaign",
    "ReceivedAt",
    "From",
    "Subject",
    "Snippet",
    "Classification",
    "MatchMethod",
    "MessageID",
    "InReplyTo",
    "ActionTaken",       # Stopped Sequence | Logged Only |
                         # Logged Only (Unverified Match) | Logged Only (Predates Contact)
                         # — only a Header-matched message can produce "Stopped Sequence"
    "FullBody",          # the complete inbound message text, untruncated — Snippet stays a
                         # short preview for existing displays; FullBody exists so a real
                         # conversation view can be reconstructed without a live IMAP fetch.
                         # An existing Response Sheet auto-migrates to include this column
                         # the next time check_replies runs — see _get_or_create_ws.
    "IsRead",            # "Yes" once marked read from the Responses page; blank/anything
                         # else counts as unread. Written only via mark_responses_read,
                         # triggered through the mark_responses_read.yml workflow — Streamlit
                         # itself never writes to this or any other Sheet column directly.
    "Intent",            # one of INTENT_OPTIONS — a SEPARATE layer from Classification,
                         # only meaningful for a Genuine Reply; blank for anything else
                         # (a bounce/auto-reply/OOO has no "sales intent" to speak of).
    "IntentConfidence",  # "High" / "Medium" / "Low" — a Low confidence result always has
                         # Intent="Unclear" too; never trust a low-confidence guess at face
                         # value in either field.
    "IntentClassifiedAt",  # when the intent classification actually ran — blank means it
                           # hasn't been classified yet (including every reply logged
                           # before this feature existed).
    "MessageDate",       # when the email was really SENT (its own Date header, in UTC), unlike
                         # ReceivedAt which is when this system LOGGED it — up to an hour or more
                         # later. The conversation view orders by this so a quick answer from you
                         # never appears before the question it answers. Blank on replies logged
                         # before this column existed; the view then falls back to ReceivedAt.
]

# The emails WE sent that the system did not send by itself: replies typed by hand in the mailbox
# (found by reading the Sent folder) and replies sent from the control panel. Kept in its OWN tab
# and read by nothing but the conversation view, so no count, rate or dashboard can change.
SENT_MAIL_COLUMNS = [
    "MessageID",
    "LeadID",
    "SentAt",            # when it was really sent, UTC (its own Date header)
    "SenderAccount",
    "To",
    "Cc",
    "Subject",
    "Body",              # plain text, capped to fit a cell
    "InReplyTo",
    "Source",            # "app" (sent from the control panel) | "mailbox" (found in the Sent folder)
]
SENT_SOURCE_APP = "app"
SENT_SOURCE_MAILBOX = "mailbox"

SEND_LOG_COLUMNS = [
    "BatchID",
    "Timestamp",
    "LeadID",
    "Email",
    "Campaign",
    "Stage",
    "Variant",
    "SenderAccount",
    "Status",            # sent | error | skipped
    "MessageID",
    "Error",
]

ERROR_LOG_COLUMNS = [
    "Timestamp",
    "Campaign",
    "ErrorType",
    "LeadID",
    "Email",
    "Stage",
    "BatchID",
    "Message",
]

DASHBOARD_COLUMNS = ["Section", "Metric", "Value"]

ACCOUNT_HEALTH_TAB = "Email Accounts Health"
ACCOUNT_HEALTH_COLUMNS = ["AccountName", "Address", "Status", "Detail", "CheckedAt"]

ALL_CAMPAIGNS_DASHBOARD_COLUMNS = [
    "Campaign", "Total Leads", "Unique Contacted", "Total Sent",
    "Delivered (est.)", "Bounced (Hard)", "Bounced (Soft)", "Replies",
    "Reply Rate", "Sequence Completion",
]

APPROVAL_YES = "Yes"

STATUS_STOPPED_REPLIED = "Stopped - Replied"
STATUS_STOPPED_BOUNCED = "Stopped - Bounced"
STATUS_STOPPED_REJECTED = "Stopped - Rejected"
STATUS_STOPPED_MANUAL = "Stopped - Manual"  # a human stopped this ONE lead
                            # on purpose — never sent automatically, distinct
                            # from Removed: this lead stays fully visible in
                            # the Data tab, it's just no longer eligible to
                            # be sent to.
STATUS_PAUSED = "Paused"
STATUS_COMPLETED = "Completed"
STATUS_REMOVED = "Removed"  # soft-remove from the Data tab — never a hard delete,
                            # see import_leads/update_lead_statuses docstrings

STATUS_STOPPED_UNSUBSCRIBED = "Stopped - Unsubscribed"   # the person asked to stop (an HTML campaign's unsubscribe email); never reply-rate "Replied"

FORMAT_TEXT, FORMAT_HTML, VALID_FORMATS = hc.FORMAT_TEXT, hc.FORMAT_HTML, hc.VALID_FORMATS   # a campaign is plain text (the default) or HTML, for its whole life

TERMINAL_STATUSES = {
    STATUS_STOPPED_UNSUBSCRIBED,
    STATUS_STOPPED_REPLIED,
    STATUS_STOPPED_BOUNCED,
    STATUS_STOPPED_REJECTED,
    STATUS_STOPPED_MANUAL,
    STATUS_PAUSED,
    STATUS_COMPLETED,
    STATUS_REMOVED,
}

# An unsubscribe marks a lead "Stopped - Unsubscribed" unless its outcome was already decided. Completed and Paused are not decided outcomes (the person still asked to stop), so they ARE overwritten.
# The Google page applies the very same rule, straight away, to the campaign's master tab (apps_script/Code.gs UNSUBSCRIBE_KEEPS_STATUS: a test keeps the two in step).
UNSUBSCRIBE_KEEPS_STATUS = TERMINAL_STATUSES - {STATUS_COMPLETED, STATUS_PAUSED}

CLASSIFICATION_GENUINE = "Genuine Reply"
CLASSIFICATION_AUTOREPLY = "Auto-Reply"
CLASSIFICATION_OOO = "Out of Office"
CLASSIFICATION_BOUNCE_HARD = "Bounce (Hard)"
CLASSIFICATION_BOUNCE_SOFT = "Bounce (Soft)"

# A SEPARATE, independent layer from Classification above — Classification
# says WHAT KIND of message this mechanically is (a real reply vs. a bounce
# vs. an auto-reply); Intent says what a Genuine Reply's SENDER actually
# wants, which Classification alone can't tell you. Named "Intent," not
# "Sentiment" — someone can write an annoyed-sounding reply that's still a
# strong lead ("I'm frustrated with our current provider, what does yours
# cost?"), so sentiment would be the wrong axis to classify on.
INTENT_INTERESTED = "Interested"
INTENT_NOT_INTERESTED = "Not Interested"
INTENT_LEAD_FOLLOWUP = "Lead / Needs Follow-up"
INTENT_UNCLEAR = "Unclear"
INTENT_OPTIONS = [INTENT_INTERESTED, INTENT_NOT_INTERESTED, INTENT_LEAD_FOLLOWUP, INTENT_UNCLEAR]

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_INTENT_MODEL = "claude-haiku-4-5-20251001"

# Error monitoring categories.
ERR_SEND_FAILURE = "Send Failure"
ERR_AUTH_FAILURE = "Authentication Failure"
ERR_INVALID_EMAIL = "Invalid Email Address"
ERR_SHEETS_API = "Sheets API Error"
ERR_RATE_LIMIT = "Rate-Limit Error"
ERR_MISSING_VARIABLE = "Missing Template Variable"
ERR_MISSING_SENDER_ACCOUNT = "Missing Sender Account"
ERR_REPLY_CHECK = "Reply Check Failure"
ERR_SENDER_CAPACITY = "Sender Capacity Reached"

DATETIME_FMT = "%Y-%m-%d %H:%M:%S"


def stage_field_names(index: int) -> dict:
    """index 0 -> Intro fields, index 1 -> FollowUp1 fields, etc."""
    prefix = "Intro" if index == 0 else f"FollowUp{index}"
    return {"sent_at": f"{prefix}SentAt", "variant": f"{prefix}Variant"}


class MissingSenderAccountError(ValueError):
    pass


class InvalidEmailFormatError(ValueError):
    pass


class SenderCapacityReachedError(ValueError):
    """Raised when every account eligible to send to a given lead (whether
    chosen manually, via rotation, or via the single default) has already
    hit its per-account daily limit. Not a fault — the lead is deferred to
    a later run, not treated as failed."""
    pass


class AttachmentTooLargeError(ValueError):
    """Raised by send_manual_reply when the combined size of every
    attachment exceeds MAX_TOTAL_ATTACHMENT_BYTES — a safety cap well
    under what mainstream mail providers reject outright, chosen partly
    to keep the committed base64-encoded payload file (roughly 1.33x the
    raw size) a reasonable size for a single git commit."""
    pass


# =============================================================================
# SECTION 2: Config loading — template-folder discovery, no central list
#
# A campaign "exists" the moment templates/<name>/ exists — nothing needs
# to be registered anywhere just to make a campaign name recognized. Global
# settings (shared sheet, default account, and the DEFAULT campaign
# settings every campaign inherits) live in config/settings.yaml. A
# campaign only needs its own config/campaigns/<name>.yaml if it wants to
# override something from the defaults — most campaigns need nothing there
# at all.
# =============================================================================

class ConfigError(Exception):
    pass


class CampaignPausedError(RuntimeError):
    """Raised by send_batch when campaign_cfg["status"] == "paused" —
    never raised by build_batch/preview, which stay usable regardless of
    pause state so a paused campaign can still be reviewed."""
    pass


class UnsubscribeListUnreadableError(RuntimeError):
    """Raised by send_batch for an HTML campaign when the shared Unsubscribes tab cannot be read: sending without it could email someone who has opted out, so that campaign
    does not send in that run (the next run tries again). Never raised for a plain-text campaign, which does not use the list."""
    pass


class OutsideSendingWindowError(RuntimeError):
    """Raised by send_batch when campaign_cfg["schedule"] restricts
    sending to specific days/hours and right now doesn't qualify — like
    CampaignPausedError, never raised by build_batch/preview, so a
    schedule-restricted campaign can still be reviewed any time."""
    pass


def load_settings(path: str = "config/settings.yaml") -> dict:
    if not os.path.exists(path):
        raise ConfigError(f"Settings file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if "shared_sheet_id" not in data:
        raise ConfigError(f"{path} is missing 'shared_sheet_id'.")
    if "default_campaign_settings" not in data:
        raise ConfigError(f"{path} is missing 'default_campaign_settings'.")
    return data


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merges override into a COPY of base. Nested dicts merge
    key by key (so e.g. an override can set just sending.daily_limit
    without redefining the whole sending block); anything else — including
    lists like stages/variants — is replaced wholesale by the override,
    since merging a list element-by-element isn't meaningful here."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def discover_campaign_names(templates_root: str = "templates") -> List[str]:
    """Every campaign that currently exists, full stop — determined purely
    by having a subfolder under templates/. This is what dashboard --all
    iterates over; there is no other list of "configured campaigns"."""
    if not os.path.isdir(templates_root):
        return []
    return sorted(
        name for name in os.listdir(templates_root)
        if os.path.isdir(os.path.join(templates_root, name)) and not name.startswith(".")
    )


# The only fixed, canonical stage order the system knows about. A campaign
# never needs all five — auto-discovery below stops at the first stage
# with no template files at all, so 1 stage is just as valid as 5.
CANONICAL_STAGE_ORDER = [
    "intro", "followup1", "followup2", "followup3", "followup4",
    "followup5", "followup6", "followup7", "followup8", "followup9", "followup10",
]  # Up to 10 follow-ups (11 stages total, intro included) — the actual cap on how many
   # a campaign can have; extending this list is the ONLY change needed to raise it further,
   # since discover_stages_and_variants, stage_field_names, and every UI stage picker all
   # derive from this list's length rather than a separately hardcoded number.
ALL_VARIANT_LETTERS = ["A", "B", "C", "D"]


def parse_stages_and_variants_from_filenames(filenames: List[str], stage_wait_days: Dict[str, int],
                                              source_description: str = "") -> Tuple[List[Dict], List[str]]:
    """The actual validation logic, decoupled from HOW the filenames were
    obtained — local disk, a GitHub API directory listing, anything.
    discover_stages_and_variants below is the local-filesystem
    convenience wrapper most callers use; this exists so a caller that
    already has an authoritative filename list can validate against
    THAT directly — e.g. one just fetched fresh from GitHub's API to
    sidestep Streamlit's own local checkout potentially lagging behind
    a very recent commit, which is exactly the gap that let a
    duplicated campaign — and separately, a destructive Delete Stage /
    Delete Variant action performed shortly after one — see a stale,
    inconsistent view of which files actually exist.

    source_description: an optional string appended to error messages
    for context (e.g. " in templates/Foo"), matching what
    discover_stages_and_variants has always produced."""
    filename_set = set(filenames)
    stages: List[Dict] = []
    canonical_variants: Optional[List[str]] = None

    for prefix in CANONICAL_STAGE_ORDER:
        found_variants = [v for v in ALL_VARIANT_LETTERS if f"{prefix}_{v}.txt" in filename_set]
        if not found_variants:
            break  # first gap — stages must be contiguous, stop here

        if canonical_variants is None:
            canonical_variants = found_variants
        elif found_variants != canonical_variants:
            missing = sorted(set(canonical_variants) - set(found_variants))
            extra = sorted(set(found_variants) - set(canonical_variants))
            problems = []
            if missing:
                problems.append(f"missing variant(s) {missing} (present in '{stages[0]['name']}')")
            if extra:
                problems.append(f"has extra variant(s) {extra} not present in '{stages[0]['name']}'")
            raise ConfigError(
                f"Inconsistent variants for stage '{prefix}'{source_description}: {'; '.join(problems)}. "
                "Every stage must offer the same variant letters — or specify 'stages' and 'variants' "
                "explicitly together in this campaign's override file if that's genuinely intentional."
            )

        stages.append({
            "name": prefix, "template_prefix": prefix,
            "wait_days_after_previous": stage_wait_days.get(prefix, 0),
        })

    if not stages:
        raise ConfigError(
            f"No template files found{source_description}. Expected at least 'intro_A.txt' "
            "(or another variant letter)."
        )

    return stages, canonical_variants


def discover_stages_and_variants(templates_dir: str, stage_wait_days: Dict[str, int]) -> Tuple[List[Dict], List[str]]:
    """Auto-detects a campaign's stage sequence and variant set purely from
    which template files exist — no YAML declaration required. Minimum is
    1 stage + 1 variant (just intro_A.txt); maximum is 5 stages x A-D.

    Two things keep this safe rather than silently permissive:
    - Stages must be CONTIGUOUS from Intro — a gap (e.g. intro + followup2
      but no followup1 files) is a configuration error, not "skip a stage".
    - Every included stage must offer the EXACT SAME variant letters as
      Intro. A campaign with fewer variants entirely (e.g. just A) is
      fine; a LATER stage quietly missing ONE variant that an earlier
      stage has is almost always an accidental missing file, not an
      intentional design, so it's rejected with a clear message instead
      of silently shrinking just that stage.

    Reads the local filesystem — see parse_stages_and_variants_from_filenames
    for validating against an already-fetched filename list instead (e.g.
    from GitHub's API directly, when the local checkout's own freshness
    can't be trusted for a destructive or data-integrity-critical action).
    """
    if not os.path.isdir(templates_dir):
        raise ConfigError(f"Templates directory not found: {templates_dir}")
    filenames = os.listdir(templates_dir)
    return parse_stages_and_variants_from_filenames(filenames, stage_wait_days, f" in {templates_dir}")


MAX_STAGE_WAIT_DAYS = 90      # the longest wait between two emails a campaign may set for itself


def parse_stage_wait_overrides(campaign_name: str, raw) -> Dict[str, int]:
    """A campaign's OWN follow-up waits ('stage_wait_days' in its override file): {stage name: whole days, 0 to MAX_STAGE_WAIT_DAYS}.

    Strict on purpose. A wait that is silently ignored or rounded would send emails on a different schedule from the one that was set, and nothing would say
    so — so anything that is not a whole number of days in range stops the campaign with a message naming the stage, exactly like every other bad setting.
    (Waits for stages the campaign does not have are accepted and simply unused: a deleted follow-up leaves its entry behind, which must not break sending.)"""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"Campaign '{campaign_name}': 'stage_wait_days' must be a list of 'stage: days' entries, got {raw!r}")
    waits: Dict[str, int] = {}
    for stage, days in raw.items():
        if not isinstance(stage, str) or not stage.strip():
            raise ConfigError(f"Campaign '{campaign_name}': 'stage_wait_days' has an entry whose stage name is not text: {stage!r}")
        if isinstance(days, bool) or not isinstance(days, int) or not 0 <= days <= MAX_STAGE_WAIT_DAYS:
            raise ConfigError(f"Campaign '{campaign_name}': the wait for '{stage}' must be a whole number of days from 0 to {MAX_STAGE_WAIT_DAYS}, got {days!r}")
        waits[stage] = days
    return waits


def apply_stage_wait_overrides(stages, waits: Dict[str, int]):
    """`stages` with each stage's wait replaced by this campaign's own, where it has one (the stage's template prefix is the key). Returns a new list and
    never changes the stages passed in; anything that is not a stage dict, or has no override, passes through untouched."""
    if not waits:
        return stages
    updated = []
    for stage in stages:
        key = (stage.get("template_prefix") or stage.get("name")) if isinstance(stage, dict) else None
        updated.append({**stage, "wait_days_after_previous": waits[key]} if key in waits else stage)
    return updated


def get_campaign(campaign_name: str, settings_path: str = "config/settings.yaml",
                  campaigns_dir: str = "config/campaigns", templates_root: str = "templates",
                  template_filenames: Optional[List[str]] = None) -> dict:
    """`template_filenames`: an authoritative list of this campaign's template file names obtained elsewhere (the Streamlit app passes a fresh GitHub listing when its own
    checked-out copy of the templates folder is behind a very recent commit, e.g. a duplicated campaign). When given, the local templates folder is not consulted at all,
    and nothing else about how the campaign is built changes."""
    settings = load_settings(settings_path)
    shared_sheet_id = settings.get("shared_sheet_id", "")

    # The actual safety gate: no templates folder, no campaign — regardless
    # of what name was typed into a workflow input.
    templates_dir = os.path.join(templates_root, campaign_name)
    if template_filenames is None and not os.path.isdir(templates_dir):
        raise ConfigError(
            f"No templates found for campaign '{campaign_name}' — expected a folder at "
            f"'{templates_dir}'. Create it with your template files before running this campaign. "
            f"Currently available campaigns: {', '.join(discover_campaign_names(templates_root)) or '(none)'}"
        )

    default_settings = dict(settings.get("default_campaign_settings", {}))
    stage_wait_days = default_settings.pop("stage_wait_days", {})
    cfg = default_settings  # sending, reply_monitor — genuinely shared defaults

    override = {}
    override_path = os.path.join(campaigns_dir, f"{campaign_name}.yaml")
    if os.path.exists(override_path):
        with open(override_path, "r", encoding="utf-8") as f:
            override = yaml.safe_load(f) or {}
        non_shape_override = {k: v for k, v in override.items() if k not in ("stages", "variants")}
        cfg = _deep_merge(cfg, non_shape_override)

    # This campaign's own follow-up waits win over the shared table in settings.yaml, stage by stage; a stage it does not mention keeps the shared value.
    override_waits = parse_stage_wait_overrides(campaign_name, override.get("stage_wait_days"))
    stage_wait_days = {**stage_wait_days, **override_waits}

    has_stages = "stages" in override
    has_variants = "variants" in override
    if has_stages != has_variants:
        raise ConfigError(
            f"Campaign '{campaign_name}' override specifies only one of 'stages'/'variants' — "
            "specify both together explicitly, or neither to auto-discover from template files."
        )

    if has_stages and has_variants:
        # Explicit declaration — the strict path: every implied file MUST exist.
        cfg["stages"] = apply_stage_wait_overrides(override["stages"], override_waits)
        cfg["variants"] = override["variants"]
        cfg["templates_dir"] = cfg.get("templates_dir") or templates_dir
        if template_filenames is None:
            _validate_templates_exist(campaign_name, cfg)
        else:
            _validate_templates_in_listing(campaign_name, cfg, template_filenames)
    else:
        # No explicit shape — auto-discover from whatever's actually there.
        if template_filenames is None:
            discovered_stages, discovered_variants = discover_stages_and_variants(templates_dir, stage_wait_days)
        else:
            discovered_stages, discovered_variants = parse_stages_and_variants_from_filenames(
                template_filenames, stage_wait_days, source_description=f" in templates/{campaign_name} (read from GitHub)")
        cfg["stages"] = discovered_stages
        cfg["variants"] = discovered_variants
        cfg["templates_dir"] = cfg.get("templates_dir") or templates_dir

    cfg["sheet_id"] = cfg.get("sheet_id") or shared_sheet_id
    cfg["master_tab"] = cfg.get("master_tab") or f"{campaign_name} Master Sheet"
    cfg["responses_tab"] = cfg.get("responses_tab") or f"{campaign_name} Response Sheet"
    cfg["send_log_tab"] = cfg.get("send_log_tab") or f"{campaign_name} Custom Log Sheet"
    cfg["error_log_tab"] = cfg.get("error_log_tab") or f"{campaign_name} Error Log"
    cfg["sent_mail_tab"] = cfg.get("sent_mail_tab") or f"{campaign_name} Sent Mail"
    cfg["dashboard_tab"] = cfg.get("dashboard_tab") or f"{campaign_name} Dashboard"
    # "active" preserves every campaign's actual current behavior before
    # this field existed — only a NEW campaign explicitly created as
    # "draft" (via the Streamlit New Campaign flow) should ever start
    # anywhere other than active. Never default to "draft" here; that
    # would silently pause every pre-existing campaign the moment this
    # ships.
    cfg["status"] = cfg.get("status") or "active"
    cfg["_campaign_name"] = campaign_name
    cfg["_global_default_account"] = (settings.get("email_accounts") or {}).get("default_account", "")
    cfg["format"] = cfg.get("format") or FORMAT_TEXT          # every campaign made before HTML existed is plain text

    _validate_campaign(campaign_name, cfg)
    if cfg["format"] == FORMAT_HTML:
        _validate_html_templates(campaign_name, cfg, template_filenames)
    return cfg


def _validate_campaign(name: str, cfg: dict) -> None:
    required = ["templates_dir", "stages", "variants", "sending"]
    for key in required:
        if key not in cfg:
            raise ConfigError(f"Campaign '{name}' is missing required key '{key}'")

    if not cfg.get("sheet_id") or str(cfg["sheet_id"]).startswith("PUT_YOUR"):
        raise ConfigError(
            f"Campaign '{name}': no sheet_id resolved. Set 'shared_sheet_id' in "
            "config/settings.yaml (or 'sheet_id' in this campaign's override file)."
        )
    if len(cfg["stages"]) == 0:
        raise ConfigError(f"Campaign '{name}' has no stages defined.")
    if len(cfg["stages"]) > len(CANONICAL_STAGE_ORDER):
        raise ConfigError(
            f"Campaign '{name}' defines {len(cfg['stages'])} stages, but the Master "
            f"sheet schema only reserves columns for Intro + {len(CANONICAL_STAGE_ORDER) - 1} "
            f"follow-ups ({len(CANONICAL_STAGE_ORDER)} max)."
        )
    if len(cfg["variants"]) < 1:
        raise ConfigError(f"Campaign '{name}' must define at least one variant.")

    sending = cfg.get("sending", {})
    for key in ["timezone", "window_start", "window_end", "delay_min_minutes", "delay_max_minutes", "daily_limit"]:
        if key not in sending:
            raise ConfigError(f"Campaign '{name}' sending config missing '{key}'")

    # The wait between sending rounds is chosen per campaign (Settings > Time between sends). Refuse a value that would only fail later, mid-send: time.sleep() raises on a
    # negative number, and a reversed range makes the wait meaningless.
    for key in ("delay_min_minutes", "delay_max_minutes"):
        value = sending[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value < 0:
            raise ConfigError(f"Campaign '{name}': sending.{key} must be a number of minutes, zero or more, got {value!r}.")
    if sending["delay_min_minutes"] > sending["delay_max_minutes"]:
        raise ConfigError(f"Campaign '{name}': sending.delay_min_minutes ({sending['delay_min_minutes']}) cannot be more than "
                          f"sending.delay_max_minutes ({sending['delay_max_minutes']}).")

    if "per_account_daily_limit" in sending and sending["per_account_daily_limit"] is not None:
        limit = sending["per_account_daily_limit"]
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise ConfigError(
                f"Campaign '{name}': sending.per_account_daily_limit must be a positive integer, "
                f"got {limit!r}."
            )
    if "sender_rotation" in sending and not isinstance(sending["sender_rotation"], bool):
        raise ConfigError(f"Campaign '{name}': sending.sender_rotation must be true or false.")
    if "rotation_accounts" in sending and sending["rotation_accounts"] is not None:
        if not isinstance(sending["rotation_accounts"], list) or not sending["rotation_accounts"]:
            raise ConfigError(
                f"Campaign '{name}': sending.rotation_accounts must be a non-empty list of account "
                "names, or omitted entirely to rotate across all EMAIL_ACCOUNTS_JSON accounts."
            )

    if cfg.get("format") not in VALID_FORMATS:
        raise ConfigError(f"Campaign '{name}': format must be 'text' or 'html', got {cfg.get('format')!r}.")
    tracking = cfg.get("tracking")
    if tracking is not None:
        if not isinstance(tracking, dict):
            raise ConfigError(f"Campaign '{name}': tracking must be a mapping, e.g. utm: true.")
        if tracking.get("utm") is not None and not isinstance(tracking["utm"], bool):
            raise ConfigError(f"Campaign '{name}': tracking.utm must be true or false.")
        for key in ("source", "medium"):
            if tracking.get(key) is not None and not isinstance(tracking[key], str):
                raise ConfigError(f"Campaign '{name}': tracking.{key} must be text.")
    footer = cfg.get("footer")
    if footer is not None:
        if not isinstance(footer, dict):
            raise ConfigError(f"Campaign '{name}': footer must be a mapping with business_name, address and opt_out.")
        for key in hc.FOOTER_FIELDS:
            if footer.get(key) is not None and not isinstance(footer[key], str):
                raise ConfigError(f"Campaign '{name}': footer.{key} must be text.")


def _validate_html_templates(name: str, cfg: dict, template_filenames: Optional[List[str]] = None) -> None:
    """An HTML campaign keeps TWO files per email: the .txt (subject and plain-text copy, found exactly as for any campaign) and a .html beside it. Every .txt must have its
    .html, checked against the local folder, or against a supplied file list (what the app passes when its own copy of the templates is behind GitHub)."""
    present = set(template_filenames) if template_filenames is not None else None
    missing = []
    for stage in cfg["stages"]:
        for variant in cfg["variants"]:
            filename = f"{stage['template_prefix']}_{variant}.html"
            exists = (filename in present) if present is not None else os.path.exists(os.path.join(cfg["templates_dir"], filename))
            if not exists:
                missing.append(filename)
    if missing:
        listing = "\n".join(f"  - {m}" for m in missing)
        raise ConfigError(f"Campaign '{name}' is an HTML campaign but is missing {len(missing)} HTML file(s):\n{listing}\nEvery email needs its .html beside its .txt.")


def _validate_templates_exist(name: str, cfg: dict) -> None:
    """Deliberately does NOT infer stages/variants from whatever template
    files happen to exist — that would let a silently-missing file quietly
    shrink a campaign's sequence. Instead: the configured stages/variants
    are the source of truth, and every file they imply must actually be
    present, or this fails loudly before any send is attempted."""
    missing = []
    for stage in cfg["stages"]:
        for variant in cfg["variants"]:
            path = os.path.join(cfg["templates_dir"], f"{stage['template_prefix']}_{variant}.txt")
            if not os.path.exists(path):
                missing.append(path)
    if missing:
        listing = "\n".join(f"  - {p}" for p in missing)
        raise ConfigError(
            f"Campaign '{name}' is missing {len(missing)} template file(s):\n{listing}\n"
            "Add these files, or adjust this campaign's stages/variants."
        )


def _validate_templates_in_listing(name: str, cfg: dict, filenames: List[str]) -> None:
    """The same strict check as _validate_templates_exist, against a list of file names instead of the local disk."""
    present = set(filenames)
    missing = [f"{stage['template_prefix']}_{variant}.txt" for stage in cfg["stages"] for variant in cfg["variants"] if f"{stage['template_prefix']}_{variant}.txt" not in present]
    if missing:
        listing = "\n".join(f"  - {p}" for p in missing)
        raise ConfigError(
            f"Campaign '{name}' is missing {len(missing)} template file(s):\n{listing}\n"
            "Add these files, or adjust this campaign's stages/variants."
        )


def _stage_index(stages: List[Dict], stage_name: str) -> int:
    for i, s in enumerate(stages):
        if s["name"] == stage_name:
            return i
    raise ValueError(f"Stage '{stage_name}' not found in campaign config.")


# =============================================================================
# SECTION 3: Google Sheets connector
#
# Each campaign gets 5 tabs: Master Sheet, Response Sheet, Custom Log Sheet,
# Error Log, Dashboard — all auto-created on first connection. Header
# validation only requires the REQUIRED columns to be present as a prefix,
# in order; you're free to add extra trailing columns of your own (e.g. for
# custom template variables) without breaking anything.
# =============================================================================

SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def _build_gspread_client():
    from google.oauth2.service_account import Credentials

    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_JSON env var is not set. "
            "Put the full service account key JSON there (as a GitHub secret)."
        )
    info = json.loads(raw)
    creds = Credentials.from_service_account_info(info, scopes=SHEETS_SCOPES)
    return gspread.authorize(creds), gspread


GSPREAD_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
GSPREAD_MAX_RETRIES = 3
GSPREAD_RETRY_BASE_DELAY_SECONDS = 2  # doubles each retry: 2s, 4s, 8s — fine for a
                                       # transient 5xx, Google's own service having a
                                       # momentary issue, which typically clears in
                                       # seconds, not minutes.
GSPREAD_QUOTA_RETRY_DELAYS_SECONDS = [15, 30, 60, 90]  # for 429 quota errors specifically —
                                       # Google's Sheets API quota (e.g. "Read requests
                                       # per minute per user") resets on a per-MINUTE
                                       # window, so the short, generic backoff above was
                                       # never actually long enough to reliably wait one
                                       # out: 3 retries totaling ~14 seconds could easily
                                       # exhaust before the very same 60-second window
                                       # that caused the 429 had even finished. This
                                       # totals up to ~105 seconds across 3 retries,
                                       # comfortably spanning at least one full window.


def _is_retryable_gspread_error(exc) -> bool:
    """429 (rate limit) and 5xx (Google's own service having a transient
    issue, e.g. the 503 'service is currently unavailable' this exists
    to fix) are worth waiting out. Anything else — 403 Forbidden (a real
    permissions problem), 404 Not Found (wrong sheet ID) — means retrying
    would just waste time on something that will never succeed, so those
    are raised immediately instead."""
    status_code = getattr(getattr(exc, "response", None), "status_code", None)
    return status_code in GSPREAD_RETRYABLE_STATUS_CODES


def _is_gspread_quota_error(exc) -> bool:
    """Specifically a 429 — as opposed to a 5xx, which the generic,
    shorter retry schedule already handles appropriately. Distinguished
    so a quota exhaustion gets the much longer backoff it actually
    needs, without also making every transient 5xx wait unnecessarily
    long."""
    status_code = getattr(getattr(exc, "response", None), "status_code", None)
    return status_code == 429


GSPREAD_QUOTA_RETRY_JITTER_FRACTION = 0.5  # +/-50% of each delay


def _quota_retry_delay(base_delay_seconds: float) -> float:
    """Applies random jitter to a quota-retry delay.

    Without this, every runner that collides on the same per-minute
    quota waits the SAME fixed interval and therefore retries at the
    same instant — colliding again, waiting the same next interval, and
    colliding again. Lockstep retry converts one unlucky overlap into a
    near-guaranteed repeated failure, which is exactly the intermittent
    "1 in 5 runs" pattern this was reported as.

    Several scheduled workflows share ONE service account and therefore
    ONE per-minute read quota, so overlaps are routine rather than
    exceptional: spreading retries randomly across the window lets the
    colliding runs de-synchronise instead of marching in step.
    """
    jitter = base_delay_seconds * GSPREAD_QUOTA_RETRY_JITTER_FRACTION
    return max(1.0, base_delay_seconds + random.uniform(-jitter, jitter))


def _call_with_gspread_retries(func, *args, **kwargs):
    """Retries a gspread call with exponential backoff, but only for
    errors classified retryable by _is_retryable_gspread_error above.
    A 429 quota error specifically gets its own, much longer backoff
    schedule (GSPREAD_QUOTA_RETRY_DELAYS_SECONDS) — see that constant's
    own comment for why the generic short schedule was never long
    enough for this specific error. This quota-retry loop runs on its
    own, separate counter — it is NOT a sub-case of the generic
    for-loop's GSPREAD_MAX_RETRIES bound, since coupling the two would
    silently truncate the quota schedule if either constant ever
    changed independently of the other."""
    for attempt in range(GSPREAD_MAX_RETRIES + 1):
        try:
            return func(*args, **kwargs)
        except gspread.exceptions.APIError as exc:
            if _is_gspread_quota_error(exc):
                last_quota_exc = exc
                for quota_delay in GSPREAD_QUOTA_RETRY_DELAYS_SECONDS:
                    time.sleep(_quota_retry_delay(quota_delay))
                    try:
                        return func(*args, **kwargs)
                    except gspread.exceptions.APIError as quota_exc:
                        if not _is_gspread_quota_error(quota_exc):
                            raise
                        last_quota_exc = quota_exc
                        continue
                raise last_quota_exc
            if not _is_retryable_gspread_error(exc) or attempt == GSPREAD_MAX_RETRIES:
                raise
            time.sleep(GSPREAD_RETRY_BASE_DELAY_SECONDS * (2 ** attempt))


class _RetryingWorksheet:
    """Thin proxy around a real gspread Worksheet — every METHOD call
    (get_all_records, append_row, batch_update, ...) is automatically
    retried on a transient error; every plain attribute (.id, .title, ...)
    passes straight through untouched. Wrapping here, once, at the point
    every worksheet gets created, means every read/write in this whole
    file gets the same protection with no per-call-site changes needed."""
    def __init__(self, real_worksheet):
        self._real = real_worksheet

    def __getattr__(self, name):
        attr = getattr(self._real, name)
        if callable(attr):
            def _retrying_call(*args, **kwargs):
                def one_attempt():
                    # A FRESH copy of the arguments for every attempt. gspread's Worksheet.batch_update
                    # REWRITES the ranges inside the list it is given (it prefixes each with the tab's
                    # name), so re-sending the same list after a 429 gave "'Tab'!'Tab'!R112" — a quota
                    # error that a simple retry should have waited out became a permanent 400.
                    try:
                        fresh_args, fresh_kwargs = copy.deepcopy(args), copy.deepcopy(kwargs)
                    except Exception:  # noqa: BLE001 - an argument that cannot be copied is passed as it is
                        fresh_args, fresh_kwargs = args, kwargs
                    return attr(*fresh_args, **fresh_kwargs)
                return _call_with_gspread_retries(one_attempt)
            return _retrying_call
        return attr


def _open_spreadsheet_with_retries(client, sheet_id: str):
    return _call_with_gspread_retries(client.open_by_key, sheet_id)


def _list_worksheets_by_title(spreadsheet) -> Optional[Dict[str, object]]:
    """Every tab in the spreadsheet, from ONE Google request, keyed by
    title.

    Why this exists: in the gspread version this runs on, each
    spreadsheet.worksheet(title) call is its own full request (it
    re-fetches the whole spreadsheet's metadata). Connecting one
    campaign looks up five tabs, so that was five requests before a
    single lead was even read — and Google allows only 60 reads a
    minute per account, shared by every workflow. One listing replaces
    all five lookups.

    Returns None on ANY failure — including a spreadsheet object that
    has no worksheets() at all — so the caller simply falls back to the
    original one-lookup-per-tab behaviour. This can only ever remove
    requests, never change what gets found or created."""
    try:
        worksheets = _call_with_gspread_retries(spreadsheet.worksheets)
        return {ws.title: ws for ws in worksheets}
    except Exception:  # noqa: BLE001 - deliberate: any failure means "use the old path"
        return None


def _get_or_create_ws(spreadsheet, gspread_module, title: str, required_header: List[str],
                       known_worksheets: Optional[Dict[str, object]] = None):
    raw_ws = known_worksheets.get(title) if known_worksheets else None
    try:
        if raw_ws is None:
            # Wrapped in the same retry as every other Google call: this
            # was the one lookup that was NOT, so a single 429 here
            # failed the whole campaign connection on the spot, however
            # generous the retry schedule was everywhere else.
            raw_ws = _call_with_gspread_retries(spreadsheet.worksheet, title)
    except gspread_module.exceptions.WorksheetNotFound:
        raw_ws = spreadsheet.add_worksheet(title=title, rows=2000, cols=max(len(required_header) + 5, 10))
        ws = _RetryingWorksheet(raw_ws)
        ws.append_row(required_header)
        return ws

    # Wrapped immediately, before any further calls — so even the very
    # first read/write below (filling in a blank header) is retried too,
    # not just everything a caller does with the returned object later.
    ws = _RetryingWorksheet(raw_ws)
    existing_header = ws.row_values(1)
    if not existing_header:
        ws.append_row(required_header)
        return ws

    # Every column this tab needs, checked by NAME — not position. Every
    # read/write anywhere in this system goes by column name (a
    # row_values(1) index lookup, or get_all_records()'s dict keys),
    # never a fixed positional offset — so a required column simply
    # needing to EXIST somewhere in the header is the real requirement.
    # An earlier version of this check required the required columns to
    # appear as an exact ordered PREFIX, which is stricter than
    # necessary and was actively wrong once any custom column (from a
    # CSV import, via ensure_master_header_includes) had already been
    # appended after an earlier required column: a LATER code update
    # adding still another new required column would then find that
    # custom column sitting exactly where the new one was expected, and
    # fail the whole tab outright — exactly the bug a real user hit in
    # production, the first time a genuinely new required column and an
    # already-imported custom column collided in position.
    missing_columns = [c for c in required_header if c not in existing_header]
    if missing_columns:
        if len(missing_columns) == len(required_header):
            # Every required column is missing — this isn't "a few new
            # columns were added since this tab was created", it shares
            # NOTHING with what this tab is supposed to look like. Fail
            # loudly rather than silently bolting the entire expected
            # schema onto what's probably an unrelated sheet/tab.
            raise RuntimeError(
                f"Header row in tab '{title}' shares none of the expected columns.\n"
                f"Expected: {required_header}\n"
                f"Found: {existing_header}\n"
                "This doesn't look like the right tab for this campaign — check its config."
            )
        # Widen the sheet's own GRID first if needed, not just the header
        # ROW's content — a real, reported production failure: a Sheet
        # tab has a fixed column count set whenever it was first created,
        # and writing a cell beyond that is rejected outright by a real
        # 400 ("Range exceeds grid limits") error, regardless of how
        # correct the header content itself is. This is the exact same
        # protection ensure_master_header_includes already has for
        # CUSTOM columns (added via CSV import) — MASTER_COLUMNS growing
        # over time (e.g. adding more follow-up stages) hits the
        # identical failure mode for the STANDARD schema, and this
        # function had never had the same guard. Resizes with headroom
        # beyond what's immediately needed, so the next small addition
        # doesn't require another resize right away.
        needed_col_count = len(existing_header) + len(missing_columns)
        if needed_col_count > ws.col_count:
            ws.resize(cols=needed_col_count + 10)
        start_col = len(existing_header) + 1
        for i, col_name in enumerate(missing_columns):
            ws.update_cell(1, start_col + i, col_name)

    return ws


class SheetsConnector:
    def __init__(self, sheet_id: str, master_tab: str, responses_tab: str, send_log_tab: str,
                 error_log_tab: str, dashboard_tab: str, sent_mail_tab: Optional[str] = None):
        self.sheet_id = sheet_id
        self._client, self._gspread = _build_gspread_client()
        self._spreadsheet = _open_spreadsheet_with_retries(self._client, sheet_id)

        known = _list_worksheets_by_title(self._spreadsheet)  # one request instead of five
        self.master_ws = _get_or_create_ws(self._spreadsheet, self._gspread, master_tab, MASTER_COLUMNS, known)
        self.responses_ws = _get_or_create_ws(self._spreadsheet, self._gspread, responses_tab, RESPONSES_COLUMNS, known)
        self.send_log_ws = _get_or_create_ws(self._spreadsheet, self._gspread, send_log_tab, SEND_LOG_COLUMNS, known)
        self.error_log_ws = _get_or_create_ws(self._spreadsheet, self._gspread, error_log_tab, ERROR_LOG_COLUMNS, known)
        self.dashboard_ws = _get_or_create_ws(self._spreadsheet, self._gspread, dashboard_tab, DASHBOARD_COLUMNS, known)

        # The Sent Mail tab is NOT created here. It is made the first time there is something to put
        # in it, so a campaign that never needs it never gets an extra tab, and connecting costs
        # nothing extra. `known` (already fetched above) tells us whether it exists yet.
        self._known_tabs = known or {}
        self._sent_mail_tab = sent_mail_tab
        existing = self._known_tabs.get(sent_mail_tab) if sent_mail_tab else None
        self._sent_mail_ws = _RetryingWorksheet(existing) if existing is not None else None

    # ---------- Master ----------

    def get_all_leads(self) -> List[Dict]:
        # No expected_headers override: reads the REAL header row, so any
        # extra custom columns you've added come through as dict keys too.
        records = self.master_ws.get_all_records()
        leads = []
        for i, record in enumerate(records, start=2):  # row 1 is header
            record["_row"] = i
            leads.append(record)
        return leads

    def read_unsubscribes(self) -> List[Dict]:
        """Every row of the shared "Unsubscribes" tab (written by the hosted unsubscribe page). An empty list when the tab does not exist yet: nobody has opted out. Only HTML
        campaigns ever call this."""
        ws = self._known_tabs.get(ul.UNSUBSCRIBE_TAB)
        if ws is None:
            return []
        return _RetryingWorksheet(ws).get_all_records()

    def ensure_campaign_codes(self, wanted: Dict[str, str]) -> int:
        """Makes sure the shared "Campaign codes" tab lists each {code: campaign name}, so the Google page can write the NAME (not the code) into the Unsubscribes tab. The tab is made the
        first time. Rows already there are left alone; a code listed against a different name is reported, never overwritten. Returns how many rows were added."""
        raw = self._known_tabs.get(ul.CODES_TAB)
        if raw is None:
            try:
                raw = _call_with_gspread_retries(self._spreadsheet.add_worksheet, ul.CODES_TAB, 500, len(ul.CODES_COLUMNS) + 3)
                _RetryingWorksheet(raw).append_row(ul.CODES_COLUMNS)
            except Exception:  # noqa: BLE001 - it may have been made by another run a moment ago: use that one
                raw = _call_with_gspread_retries(self._spreadsheet.worksheet, ul.CODES_TAB)
            self._known_tabs[ul.CODES_TAB] = raw
        worksheet = _RetryingWorksheet(raw)
        to_add, conflicts = ul.plan_code_rows(worksheet.get_all_records(), wanted)
        for code in conflicts:
            print(f"WARNING: the code {code} is already listed in the '{ul.CODES_TAB}' tab against a different campaign name; it was not changed.")
        for code, name in to_add:
            worksheet.append_row([code, name])
        return len(to_add)

    def update_lead_fields(self, row_number: int, fields: Dict[str, str]) -> None:
        """Writes each field to its column position on the REAL sheet's
        OWN, current header — never MASTER_COLUMNS' own Python list
        position. This is the actual fix for a severe, confirmed
        production bug: _get_or_create_ws appends any missing required
        column to the END of whatever header a sheet already has,
        deliberately never reordering existing columns to match
        MASTER_COLUMNS' own internal order. So the moment MASTER_COLUMNS
        itself gains a new entry INSERTED before an existing one in the
        Python list (exactly what extending follow-ups from 4 to 10
        did, inserting 12 new columns before AsanaTaskGID, Status, and
        LastActionAt), every column after the insertion point silently
        computed the WRONG real-sheet position for every existing
        campaign's sheet — writing into whatever column actually sits
        there instead. Confirmed directly against a live Asana project:
        the write meant for AsanaTaskGID landed somewhere else entirely,
        so the Sheet never recorded a task's GID, and every subsequent
        sync treated every lead as brand new — creating a fresh
        duplicate task for literally every single lead, every run."""
        header = self.master_ws.row_values(1)
        gspread = self._gspread
        updates = []
        for col_name, value in fields.items():
            if col_name not in header:
                raise ValueError(f"Column '{col_name}' not found in this sheet's actual header: {header}")
            col_index = header.index(col_name) + 1  # 1-indexed
            updates.append({
                "range": gspread.utils.rowcol_to_a1(row_number, col_index),
                "values": [[value]],
            })
        if updates:
            self.master_ws.batch_update(updates)

    def append_lead(self, fields: Dict[str, str]) -> None:
        """Appends ONE new row. Unlike update_lead_fields, this is NOT
        restricted to MASTER_COLUMNS — it builds the row against whatever
        the sheet's ACTUAL header row currently is, so any custom trailing
        columns (Title, Website, LinkedIn, ...) get filled in correctly
        too. Any field not present in `fields` is left blank in that
        column, not an error — a CSV import commonly won't map every
        column. IMPORTANT: a field name that ISN'T already in the header
        is silently dropped here, not an error — see
        ensure_master_header_includes, which import_leads calls first
        specifically so a brand-new custom field name actually gets a
        real column instead of being lost this way."""
        header = self.master_ws.row_values(1)
        row = [str(fields.get(col, "")) for col in header]
        self.master_ws.append_row(row, value_input_option="RAW")

    def ensure_master_header_includes(self, column_names: List[str]) -> None:
        """Widens the Master Sheet's header to include any of the given
        column names it doesn't already have — appended at the end,
        every existing column and its position left untouched. Called
        by import_leads before appending any rows, so a brand-new
        custom field name (one this campaign's Sheet has never seen
        before) actually gets a real column instead of being silently
        dropped by append_lead, which can only ever fill in columns the
        header already has.

        Also widens the sheet's own GRID first if needed, not just the
        header ROW's content — a Sheet tab has a fixed column count set
        whenever it was first created (this one had been sitting at 38
        for a long time), and writing a cell beyond that is rejected
        outright by a real 400 error, regardless of how correct the
        header content itself is. Resizes with some headroom beyond
        what's immediately needed, so a future single new custom field
        doesn't require another resize right away."""
        current_header = self.master_ws.row_values(1)
        missing = [c for c in column_names if c and c not in current_header]
        if not missing:
            return
        needed_col_count = len(current_header) + len(missing)
        if needed_col_count > self.master_ws.col_count:
            self.master_ws.resize(cols=needed_col_count + 10)
        start_col = len(current_header) + 1
        for i, col_name in enumerate(missing):
            self.master_ws.update_cell(1, start_col + i, col_name)

    def update_lead_statuses(self, row_numbers_to_status: Dict[int, str]) -> None:
        """Bulk status update (e.g. soft-remove) — one batch_update call
        covering every row, not one API call per row. Always stamps
        LastActionAt alongside Status, matching every other status write
        in this file."""
        # Same fix, same reason, as update_lead_fields above — looked up
        # against the sheet's own live header, never MASTER_COLUMNS'
        # Python list position.
        header = self.master_ws.row_values(1)
        gspread = self._gspread
        now_str = datetime.now().strftime(DATETIME_FMT)
        status_col = header.index("Status") + 1
        last_action_col = header.index("LastActionAt") + 1
        updates = []
        for row_number, status in row_numbers_to_status.items():
            updates.append({"range": gspread.utils.rowcol_to_a1(row_number, status_col), "values": [[status]]})
            updates.append({"range": gspread.utils.rowcol_to_a1(row_number, last_action_col), "values": [[now_str]]})
        if updates:
            self.master_ws.batch_update(updates)

    # ---------- Responses ----------

    def get_logged_message_ids(self) -> set:
        # Looked up against the sheet's own live header, never
        # RESPONSES_COLUMNS' Python list position — same fix, same
        # reason, as update_lead_fields on the Master sheet: the two
        # orders are not guaranteed to match once a sheet has been
        # incrementally widened, and a latent version of the exact bug
        # that corrupted Master sheet data was found sitting here too.
        header = self.responses_ws.row_values(1)
        ids = self.responses_ws.col_values(header.index("MessageID") + 1)
        return set(ids[1:])  # skip header

    def get_logged_message_info(self) -> Dict[str, Dict]:
        """{message_id: {"row": row_number, "match_method": str}} for
        every already-logged response with a non-blank MessageID (a
        response can have a blank one — see append_response's own
        ResponseID fallback — and those can never be re-matched against
        anyway, so they're correctly excluded here).

        This exists so check_replies can distinguish a message that was
        already DEFINITIVELY resolved (Header or Subject match — see
        that constant's own docstring) from one that was only ever
        logged as an unverified, sender-only match: the latter is
        eligible to be safely re-evaluated on a later run, since the
        matching LOGIC itself may have improved since it was first
        logged (a real, confirmed case: a subject-matching upgrade
        landing after a reply was already logged as unverified under
        the old logic, permanently unable to ever be reconsidered
        without this distinction — get_logged_message_ids alone,
        a bare set with no match-method information, cannot tell the
        two situations apart at all)."""
        header = self.responses_ws.row_values(1)
        match_method_col = header.index("MatchMethod") + 1
        message_id_col = header.index("MessageID") + 1
        message_ids = self.responses_ws.col_values(message_id_col)[1:]  # skip header
        match_methods = self.responses_ws.col_values(match_method_col)[1:]
        info = {}
        for i, message_id in enumerate(message_ids):
            if not message_id:
                continue
            match_method = match_methods[i] if i < len(match_methods) else ""
            info[message_id] = {"row": i + 2, "match_method": match_method}  # row 1 is header
        return info

    def update_response_match(self, row_number: int, match_method: str, action_taken: str) -> None:
        """Updates JUST the MatchMethod and ActionTaken cells of an
        already-logged response row — used when a message previously
        logged as an unverified, sender-only match gets re-evaluated on
        a later run and now resolves definitively (see
        get_logged_message_info's own docstring). Every other column
        on that row (Classification, Subject, FullBody, etc.) is left
        exactly as it was originally logged, since none of that
        actually changed — only which match method the CURRENT
        matching logic now assigns to the same, unchanged message."""
        header = self.responses_ws.row_values(1)
        gspread = self._gspread
        updates = [
            {"range": gspread.utils.rowcol_to_a1(row_number, header.index("MatchMethod") + 1),
             "values": [[match_method]]},
            {"range": gspread.utils.rowcol_to_a1(row_number, header.index("ActionTaken") + 1),
             "values": [[action_taken]]},
        ]
        self.responses_ws.batch_update(updates)

    # ---------- Sent Mail (hand-typed and control-panel replies; see SENT_MAIL_COLUMNS) ----------

    @property
    def sent_mail_enabled(self) -> bool:
        return bool(self._sent_mail_tab)

    def get_sent_mail_ids(self) -> set:
        """Message-IDs already stored. An empty set — with no Google request at all —
        when the tab has not been created yet."""
        if self._sent_mail_ws is None:
            return set()
        header = self._sent_mail_ws.row_values(1)
        if "MessageID" not in header:
            return set()
        return {value for value in self._sent_mail_ws.col_values(header.index("MessageID") + 1)[1:] if value}

    def append_sent_mail_rows(self, rows: List[Dict]) -> int:
        """Stores rows, creating the tab on first use. Written RAW (email text must never be
        interpreted by Sheets as a formula). Returns how many rows were written."""
        if not self._sent_mail_tab or not rows:
            return 0
        if self._sent_mail_ws is None:
            self._sent_mail_ws = _get_or_create_ws(self._spreadsheet, self._gspread, self._sent_mail_tab,
                                                    SENT_MAIL_COLUMNS, self._known_tabs)
        values = [[str(row.get(column, "") or "") for column in SENT_MAIL_COLUMNS] for row in rows]
        self._sent_mail_ws.append_rows(values, value_input_option="RAW")
        return len(values)

    def update_response_rows(self, updates: List[Dict]) -> int:
        """Repairs many already-logged response rows in ONE Google request, however many rows and
        cells that is — Google allows only 60 write requests a minute, so a request per row (as the
        first version of the repair did) hits the limit after a minute's work.

        Each update is {"row": n, "full_body": text-or-None, "snippet": text, "intent_fields": {...},
        "message_date": "..."}. `full_body=None` leaves the text cells alone (a date-only fix). Written
        RAW — email text must never be interpreted by Sheets, or a body that starts with '=' would run
        as a formula. Returns how many rows were included."""
        header = self.responses_ws.row_values(1)
        gspread = self._gspread
        cells = []
        for update in updates:
            values = dict(update.get("intent_fields") or {})
            if update.get("full_body") is not None:
                values.update({"FullBody": update["full_body"], "Snippet": update.get("snippet") or ""})
            if update.get("message_date"):
                values["MessageDate"] = update["message_date"]
            for column, value in values.items():
                cells.append({"range": gspread.utils.rowcol_to_a1(update["row"], header.index(column) + 1),
                              "values": [[value]]})
        if not cells:
            return 0
        self.responses_ws.batch_update(cells, value_input_option="RAW")
        return len(updates)

    def update_response_body(self, row_number: int, full_body: Optional[str], snippet: Optional[str],
                             intent_fields: Optional[Dict[str, str]] = None,
                             message_date: Optional[str] = None) -> None:
        """One row of update_response_rows: the text of a reply that was stored empty (see
        backfill_response_bodies), optionally its intent reading and/or its real sent time."""
        self.update_response_rows([{"row": row_number, "full_body": full_body, "snippet": snippet,
                                    "intent_fields": intent_fields, "message_date": message_date}])

    def get_all_responses(self) -> List[Dict]:
        # _row tracked the same way get_all_leads does — needed so
        # mark_responses_read can update a SPECIFIC row's IsRead cell
        # without re-deriving row numbers from scratch.
        records = self.responses_ws.get_all_records()
        responses = []
        for i, record in enumerate(records, start=2):  # row 1 is header
            record["_row"] = i
            responses.append(record)
        return responses

    def mark_responses_read(self, response_id_to_row: Dict[str, int]) -> int:
        """Sets IsRead=Yes for the given {ResponseID: row_number} pairs,
        in one batched Sheets update — the caller (cmd_mark_responses_read)
        resolves which rows to touch by matching ResponseID against a
        fresh get_all_responses() call; this method trusts the row
        numbers it's given rather than re-looking anything up itself.
        Returns how many rows were actually included in the update."""
        gspread = self._gspread
        col_index = self.responses_ws.row_values(1).index("IsRead") + 1
        updates = [
            {"range": gspread.utils.rowcol_to_a1(row_number, col_index), "values": [["Yes"]]}
            for row_number in response_id_to_row.values()
        ]
        if updates:
            self.responses_ws.batch_update(updates)
        return len(updates)

    def append_response(self, fields: Dict[str, str]) -> None:
        row = [fields.get(col, "") for col in RESPONSES_COLUMNS]
        self.responses_ws.append_row(row, value_input_option="RAW")

    # ---------- Send Log ----------

    def get_all_send_log(self) -> List[Dict]:
        return self.send_log_ws.get_all_records()

    def append_send_log(self, fields: Dict[str, str]) -> None:
        row = [fields.get(col, "") for col in SEND_LOG_COLUMNS]
        self.send_log_ws.append_row(row, value_input_option="RAW")

    # ---------- Error Log ----------

    def get_all_error_log(self) -> List[Dict]:
        return self.error_log_ws.get_all_records()

    def append_error_log(self, fields: Dict[str, str]) -> None:
        row = [fields.get(col, "") for col in ERROR_LOG_COLUMNS]
        self.error_log_ws.append_row(row, value_input_option="RAW")


def get_all_campaigns_dashboard_ws(sheet_id: str):
    """The combined cross-campaign dashboard lives in one shared tab, not
    scoped to any single campaign's connector."""
    client, gspread_module = _build_gspread_client()
    spreadsheet = _open_spreadsheet_with_retries(client, sheet_id)
    return _get_or_create_ws(spreadsheet, gspread_module, "All Campaigns Dashboard", ALL_CAMPAIGNS_DASHBOARD_COLUMNS)


# =============================================================================
# SECTION 4: Error logging helper
#
# Used everywhere an error needs recording. Never lets a logging failure
# crash the run it's trying to report on — falls back to stderr.
# =============================================================================

def log_error(sheets: "SheetsConnector", campaign_name: str, error_type: str, message: str,
              lead_id: str = "", email_addr: str = "", stage: str = "", batch_id: str = "") -> None:
    try:
        sheets.append_error_log({
            "Timestamp": datetime.now().strftime(DATETIME_FMT),
            "Campaign": campaign_name, "ErrorType": error_type, "LeadID": lead_id,
            "Email": email_addr, "Stage": stage, "BatchID": batch_id, "Message": str(message)[:500],
        })
    except Exception as log_exc:  # noqa: BLE001 - never let error logging crash the run
        print(f"WARNING: failed to write to error log ({error_type}: {message}): {log_exc}", file=sys.stderr)


# =============================================================================
# SECTION 5: Template engine
#
# Only Email is mandatory per lead. Known variables (FirstName, LastName,
# CompanyName) get graceful defaults when blank. Any OTHER {{Variable}} is
# resolved directly against a matching Master column of the same name —
# this is how custom variables (e.g. {{Industry}}) work, with no code
# changes needed, as long as you've added that column to Master yourself.
# A blank value (known or custom) renders as nothing, never as literal
# "{{...}}" syntax. A variable that matches NO column at all (a likely
# typo) also renders as nothing, but gets tracked and flagged — see
# render_email's missing_variables return value.
# =============================================================================

PLACEHOLDER_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")

TEMPLATE_VARIABLE_MAP = {
    "FirstName": "FirstName",
    "LastName": "LastName",
    "CompanyName": "Company",
    "Email": "Email",
}

DEFAULT_VALUES = {
    "FirstName": "there",
    "LastName": "",
    "CompanyName": "your team",
}


class TemplateError(Exception):
    pass


def parse_template_content(content: str, source_description: str = "") -> Dict[str, str]:
    """The actual parsing logic, decoupled from HOW the raw text was
    obtained — local disk, GitHub's API, anything. Template format:
    first line = 'Subject: ...', blank line, then the body. See
    load_template for the local-filesystem convenience wrapper most
    callers use; this exists so a caller that already has freshly-
    fetched content (e.g. from GitHub's API directly, to sidestep
    Streamlit's local checkout potentially lagging behind a very recent
    edit) can validate and parse it without going through disk at all.

    source_description: an optional string appended after "Template"
    in the error message for context (e.g. " templates/Foo/intro_A.txt"
    — note the leading space), matching what load_template has always
    produced."""
    if not content.startswith("Subject:"):
        raise TemplateError(
            f"Template{source_description} must start with a 'Subject: ...' line, followed by a "
            "blank line and then the email body."
        )
    lines = content.split("\n")
    subject = lines[0][len("Subject:"):].strip()
    body = "\n".join(lines[1:]).lstrip("\n")
    return {"subject": subject, "body": body}


def load_template(templates_dir: str, template_prefix: str, variant: str) -> Dict[str, str]:
    """Reads a template file from local disk. See parse_template_content
    for validating already-fetched content directly instead — the
    exact same rules, decoupled from where the text came from."""
    path = os.path.join(templates_dir, f"{template_prefix}_{variant}.txt")
    if not os.path.exists(path):
        raise TemplateError(f"Template file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    return parse_template_content(content, f" {path}")


def render_text(text: str, lead: Dict[str, str], missing_out: Optional[List[str]] = None,
                escape=None, reserved: Optional[Dict[str, str]] = None) -> str:
    """escape / reserved are used only for an HTML campaign's emails and are None everywhere else, so plain-text rendering is exactly what it always was.
    escape: a function applied to every VALUE taken from the lead (so a name containing symbols can never become markup). reserved: names that stand for ready-made text
    (the campaign footer) and are inserted as they are, not looked up in the lead."""
    _e = escape or (lambda value: value)

    def _replace(match):
        var_name = match.group(1)
        if reserved is not None and var_name in reserved:
            return reserved[var_name]
        if var_name in TEMPLATE_VARIABLE_MAP:
            sheet_col = TEMPLATE_VARIABLE_MAP[var_name]
            value = (lead.get(sheet_col) or "").strip()
            return _e(value) if value else _e(DEFAULT_VALUES.get(var_name, ""))
        if var_name in lead:
            # A real custom column — just blank for this lead. Normal, not an error.
            return _e((lead.get(var_name) or "").strip())
        # No column anywhere matches this variable name — almost certainly a
        # typo or a template referencing a field that was never added.
        if missing_out is not None:
            missing_out.append(var_name)
        return ""

    return PLACEHOLDER_RE.sub(_replace, text)


def render_email(templates_dir: str, template_prefix: str, variant: str, lead: Dict[str, str],
                  is_first_stage: bool = False, reserved_text: Optional[Dict[str, str]] = None, ignore_body: bool = False) -> Dict:
    """Renders subject + body (ignore_body=True renders only the subject: an HTML campaign's plain-text half is made from its design instead). A template's Subject line can be left BLANK
    to mean "continue the same thread" instead of starting a new one —
    mirrors the same convention other outreach tools use. When blank:

    - The outgoing subject becomes "Re: " + the lead's stored
      ThreadSubject (the most recent NON-blank subject actually sent to
      this lead), so Gmail/Outlook thread it together with the earlier
      message instead of starting a new conversation. Already-"Re:"
      subjects aren't double-prefixed.
    - ThreadSubject itself is left unchanged (still continuing the same
      original subject, not resetting it).

    A non-blank Subject always "resets" ThreadSubject going forward to
    whatever was actually rendered, so a later stage can deliberately
    start a fresh subject/thread, and any stage after THAT can continue
    from it with a blank Subject again.

    is_first_stage=True (the very first stage in the sequence) can never
    use a blank Subject — there's no previous thread to continue from a
    first message, so this raises immediately rather than sending a blank
    or guessed subject line.
    """
    tmpl = load_template(templates_dir, template_prefix, variant)
    missing: List[str] = []
    rendered_subject = render_text(tmpl["subject"], lead, missing_out=missing, reserved=reserved_text)
    body = "" if ignore_body else render_text(tmpl["body"], lead, missing_out=missing, reserved=reserved_text)

    is_continuation = False
    if rendered_subject.strip():
        subject = rendered_subject
        thread_subject = rendered_subject
    else:
        if is_first_stage:
            raise TemplateError(
                f"{template_prefix}_{variant}.txt has a blank Subject line, but this is the FIRST stage "
                "in the sequence — there's no previous thread to continue. Every first-stage template "
                "needs a non-blank Subject."
            )
        existing_thread_subject = (lead.get("ThreadSubject") or "").strip()
        if not existing_thread_subject:
            raise TemplateError(
                f"{template_prefix}_{variant}.txt has a blank Subject line (continue-the-thread), but "
                f"lead '{lead.get('LeadID', '?')}' has no ThreadSubject recorded to continue from — this "
                "usually means the lead was sent to before this feature existed. Either put a Subject in "
                "this template, or fill in ThreadSubject manually in the Master Sheet for this lead."
            )
        subject = existing_thread_subject if existing_thread_subject.lower().startswith("re:") \
            else f"Re: {existing_thread_subject}"
        thread_subject = existing_thread_subject
        is_continuation = True

    seen = set()
    deduped_missing = []
    for name in missing:
        if name not in seen:
            seen.add(name)
            deduped_missing.append(name)

    return {"subject": subject, "body": body, "missing_variables": deduped_missing,
            "thread_subject": thread_subject, "is_continuation": is_continuation}


def html_template_path(templates_dir: str, template_prefix: str, variant: str) -> str:
    """Where an HTML campaign keeps the designed version of an email: next to the .txt file, with the same name and the .html ending."""
    return os.path.join(templates_dir, f"{template_prefix}_{variant}.html")


def load_html_template(templates_dir: str, template_prefix: str, variant: str) -> str:
    path = html_template_path(templates_dir, template_prefix, variant)
    if not os.path.exists(path):
        raise TemplateError(f"HTML template file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def footer_reserved(campaign_cfg: Dict, template_texts: List[str], as_html: bool) -> Dict[str, str]:
    """The reserved {{ footer }} for an HTML campaign's email, built from the campaign's footer settings. If one of the given template texts uses {{ footer }} but the footer
    settings are incomplete, this raises TemplateError rather than ever sending a message with a half-empty footer."""
    footer = campaign_cfg.get("footer") or {}
    if any("footer" in hc.placeholders_in(text) for text in template_texts):
        problems = hc.footer_problems(footer)
        if problems:
            raise TemplateError("This email uses {{ footer }}, but the campaign's footer is incomplete: " + "; ".join(problems) + ". Fill it in under the campaign's Settings.")
    return {"footer": hc.build_footer_html(footer) if as_html else hc.build_footer_text(footer)}


def render_email_html(templates_dir: str, template_prefix: str, variant: str, lead: Dict[str, str], campaign_cfg: Dict) -> Dict:
    """The designed (HTML) version of an email for one lead. Refuses to render anything unsafe (scripts, forms, event attributes...), escapes every value taken from the
    lead, and fills {{ footer }} from the campaign's footer settings. Returns {"html": ..., "missing_variables": [...]}."""
    raw = load_html_template(templates_dir, template_prefix, variant)
    return render_html_text(raw, lead, campaign_cfg, label=f"{template_prefix}_{variant}.html", utm_content=f"{template_prefix}_{variant}")


def render_html_text(raw: str, lead: Dict[str, str], campaign_cfg: Dict, label: str = "The HTML", utm_content: str = "") -> Dict:
    """The one place an HTML design becomes an email for one lead: used both by the real send (through render_email_html) and by the app's preview, so what is previewed
    is exactly what would be sent. Refuses anything unsafe, escapes every value taken from the lead, and fills {{ footer }}. When the campaign has link tagging on
    (tracking: {utm: true}), every web link is given its utm_ labels (utm_content says which email, e.g. intro_A)."""
    problems = hc.unsafe_problems(raw)
    if problems:
        raise TemplateError(f"{label} is not safe to send: " + " ".join(problems))
    reserved = footer_reserved(campaign_cfg, [raw], as_html=True)
    missing: List[str] = []
    rendered = render_text(raw, lead, missing_out=missing, escape=html_lib.escape, reserved=reserved)
    rendered = hc.tag_links(rendered, hc.utm_params(campaign_cfg.get("_campaign_name", ""), campaign_cfg.get("tracking"), utm_content))
    seen, ordered = set(), []
    for name in missing:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    # The hosted unsubscribe page (when it is set up): the footer's Unsubscribe link becomes this lead's own signed link. Done AFTER link tagging so the page address is never tagged.
    links = unsubscribe_links(campaign_cfg, lead)
    unsubscribe_url, footer_url = links if links else (None, None)
    if footer_url:
        rendered = rendered.replace(hc.UNSUBSCRIBE_TOKEN, html_lib.escape(footer_url, quote=True))
    return {"html": rendered, "missing_variables": ordered, "unsubscribe_url": unsubscribe_url, "unsubscribe_footer_url": footer_url}


def campaign_brand_key(campaign_cfg: Dict) -> str:
    """Which brand this campaign's opt-outs belong to: its footer's business name (every HTML campaign with the same name is one brand), or the campaign itself when it has none."""
    return ul.brand_key((campaign_cfg.get("footer") or {}).get("business_name", ""), campaign_cfg.get("_campaign_name", ""))


def unsubscribe_links(campaign_cfg: Dict, lead: Dict) -> Optional[Tuple[str, str]]:
    """(header link, footer link) for this lead, both carrying the SAME signed token, or None when the page is not set up (UNSUBSCRIBE_URL / UNSUBSCRIBE_SECRET) or the campaign is not
    an HTML campaign. The header link goes to the Google script (a mail app's one-tap unsubscribe is a request made by the mail provider's servers). The footer link is what a person
    CLICKS: the static page on GitHub Pages when UNSUBSCRIBE_PAGE_URL is set (so Google never sees their browser), otherwise the Google page itself."""
    if campaign_cfg.get("format") != FORMAT_HTML:
        return None
    config = ul.configuration(os.environ)
    if config is None:
        return None
    footer = campaign_cfg.get("footer") or {}
    campaign_name = str(campaign_cfg.get("_campaign_name") or "").strip()
    token = ul.make_token(config.secret, campaign=ul.campaign_code(config.secret, campaign_name) if campaign_name else "", lead_id=str(lead.get("LeadID") or ""), email=str(lead.get("Email") or ""),
                          brand=str(footer.get("business_name") or ""), key=campaign_brand_key(campaign_cfg), address=str(footer.get("address") or ""), test=bool(lead.get("_unsubscribe_test")))
    header_link = ul.page_url(config.url, token)
    return header_link, (ul.page_url(config.page, token) if config.page else header_link)


def unsubscribe_page_link(campaign_cfg: Dict, lead: Dict) -> Optional[str]:
    """This lead's signed link for the mail app's unsubscribe header (the Google script address), or None when the page is not set up or the campaign is not an HTML campaign."""
    links = unsubscribe_links(campaign_cfg, lead)
    return links[0] if links else None


def probe_static_page(url: str, timeout: float = 20) -> Tuple[str, str]:
    """Opens the static page's address as a visitor would (no cookies) and says whether GitHub Pages is serving the unsubscribe page: (state, message)."""
    import urllib.error
    import urllib.request
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (unsubscribe page check)"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return ul.classify_page(response.status, response.read(300000).decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return ul.classify_page(exc.code, "")
    except Exception as exc:  # noqa: BLE001
        return ul.classify_page(None, "", error=str(exc) or exc.__class__.__name__)


def probe_page_config(page_url: str, expected_api: str, timeout: float = 20) -> Tuple[str, str]:
    """Reads the static page's config.js and says whether it names the same Google script as UNSUBSCRIBE_URL: (state, message)."""
    import urllib.error
    import urllib.parse
    import urllib.request
    request = urllib.request.Request(urllib.parse.urljoin(page_url, "config.js"), headers={"User-Agent": "Mozilla/5.0 (unsubscribe page check)"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return ul.classify_config(ul.config_js_address(response.read(100000).decode("utf-8", "replace")), expected_api, response.status)
    except urllib.error.HTTPError as exc:
        return ul.classify_config("", expected_api, exc.code)
    except Exception as exc:  # noqa: BLE001
        return ul.classify_config("", expected_api, None, error=str(exc) or exc.__class__.__name__)


def _timed_request(url: str, data: Optional[bytes], origin: str, timeout: float) -> Dict:
    """One request as the page would make it (no cookies; redirects followed), timed: {wall: seconds, took: milliseconds the script says it spent, or None} or {error}."""
    import urllib.request
    headers = {"User-Agent": "Mozilla/5.0 (unsubscribe page check)", "Origin": origin}
    if data is not None:
        headers["Content-Type"] = "text/plain;charset=utf-8"
    started = time.monotonic()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, method="POST" if data is not None else "GET", headers=headers), timeout=timeout) as response:
            body = response.read(200000).decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc) or exc.__class__.__name__}
    wall = time.monotonic() - started
    took = None
    try:
        value = json.loads(body).get("took")
        took = int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    except (ValueError, AttributeError):
        pass
    return {"wall": wall, "took": took}


def probe_unsubscribe_timings(api_url: str, token: str, origin: str, timeout: float = 30, repeats: int = 2) -> List[Dict]:
    """Times the page's three requests to the Google script, each `repeats` times (the first run shows a cold start, the later ones a warm one): a request that does nothing (ping), opening the link,
    and confirming with a TEST link (which records nothing). Only ever used to REPORT. Returns [{label, runs: [{wall, took} | {error}]}]."""
    import urllib.parse
    confirm = json.dumps({"api": "confirm", "t": token, "reason": "", "details": "", "defer": True, "timing": True}).encode("utf-8")
    plan = [("ping (a request that does nothing at all)", f"{api_url}?api=ping&timing=1", None),
            ("open the link (the page's data request)", f"{api_url}?api=info&t={urllib.parse.quote(token)}&timing=1", None),
            ("confirm with a TEST link (records nothing)", api_url, confirm)]
    return [{"label": label, "runs": [_timed_request(url, data, origin, timeout) for _ in range(max(1, repeats))]} for label, url, data in plan]


def probe_unsubscribe_api(api_url: str, token: str, origin: str, post: bool = False, timeout: float = 20, max_hops: int = 6) -> Tuple[str, str]:
    """Makes the static page's own request to the Google script (GET ?api=info, or the confirm POST with a TEST link, which records nothing) exactly as a browser on `origin` would: no
    cookies, a plain-text POST, redirects followed one step at a time, with every answer on the way checked for permission for `origin`. Returns (state, message), see classify_api."""
    import urllib.error
    import urllib.parse
    import urllib.request

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = urllib.request.build_opener(_NoRedirect())                      # no cookie handling at all: the page's requests carry none
    hops: List[Dict] = []
    target = api_url if post else f"{api_url}?api=info&t={urllib.parse.quote(token)}"
    method = "POST" if post else "GET"
    data = json.dumps({"api": "confirm", "t": token, "reason": "", "details": ""}).encode("utf-8") if post else None
    for _ in range(max_hops):
        headers = {"User-Agent": "Mozilla/5.0 (unsubscribe page check)", "Origin": origin}
        if data is not None:
            headers["Content-Type"] = "text/plain;charset=utf-8"
        try:
            response = opener.open(urllib.request.Request(target, data=data, method=method, headers=headers), timeout=timeout)
            status, answer_headers, body = response.status, response.headers, response.read(200000).decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            status, answer_headers = exc.code, exc.headers
            try:
                body = exc.read(200000).decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                body = ""
        except Exception as exc:  # noqa: BLE001
            hops.append({"where": ul._where(target), "error": str(exc) or exc.__class__.__name__})
            break
        hops.append({"where": ul._where(target), "status": status, "acao": answer_headers.get("Access-Control-Allow-Origin", ""), "body": body})
        location = urllib.parse.urljoin(target, answer_headers.get("Location", "")) if 300 <= status < 400 and answer_headers.get("Location") else ""
        if not location:
            break
        target = location
        if status in (301, 302, 303):
            method, data = "GET", None                                           # a browser follows these with a GET and no body
    return ul.classify_api(hops, origin, expect_state="" if post else "test")


def probe_unsubscribe_page(url: str, timeout: float = 20, expect: Tuple[str, ...] = ()) -> Tuple[str, str]:
    """Opens an unsubscribe address from here WITHOUT signing in to anything and with no cookies, and says whether Google served it: (state, message), see
    unsubscribe_logic.classify_probe. It requests EXACTLY the string it is given: the test email command gives it the bare address and then the exact link that is in the email
    (the address plus its token). Best effort and only ever used to REPORT; it never blocks a send."""
    import urllib.error
    import urllib.request
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (unsubscribe page check)"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:                      # redirects are followed, as a browser does
            return ul.classify_probe(response.status, response.geturl(), response.read(300000).decode("utf-8", "replace"), expect=expect)
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(300000).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            body = ""
        return ul.classify_probe(exc.code, exc.geturl() or url, body, expect=expect)
    except Exception as exc:  # noqa: BLE001 - a network problem on our side is "could not check", never a verdict on the page
        return ul.classify_probe(None, url, "", error=str(exc) or exc.__class__.__name__)


def trace_unsubscribe_link(url: str, timeout: float = 20, expect: Tuple[str, ...] = (), frame_hosts: Tuple[str, ...] = ul.FRAME_HOSTS, max_hops: int = 6) -> List[Dict]:
    """What a BROWSER would do with this link, as far as it can be done without a Google sign-in: keep cookies, follow each redirect ONE STEP AT A TIME (so every hop is seen), then
    request the page's own content frame (the second request a web app makes, on googleusercontent.com). Returns one dict per request (see unsubscribe_logic.format_trace and
    trace_verdict). Only ever used to REPORT. Never prints or returns a link's token (only host and path)."""
    import http.cookiejar
    import urllib.error
    import urllib.parse
    import urllib.request

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None                                                          # the redirect itself is the answer we want to see

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), _NoRedirect())
    steps: List[Dict] = []

    def fetch(target: str, kind: str, referer: Optional[str]):
        headers = {"User-Agent": "Mozilla/5.0 (unsubscribe page check)"}
        if referer:
            pieces = urllib.parse.urlsplit(referer)
            headers["Referer"] = f"{pieces.scheme}://{pieces.netloc}/"           # a browser sends only the origin across sites
        known = {cookie.name for cookie in jar}
        try:
            response = opener.open(urllib.request.Request(target, headers=headers), timeout=timeout)
            status, answer_headers, body = response.status, response.headers, response.read(400000).decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            status, answer_headers = exc.code, exc.headers
            try:
                body = exc.read(400000).decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                body = ""
        except Exception as exc:  # noqa: BLE001 - one step that cannot be made is reported as such, never as a verdict on the page
            steps.append({"kind": kind, "where": ul._where(target), "error": str(exc) or exc.__class__.__name__, "state": "unknown"})
            return None, "", ""
        full_location = urllib.parse.urljoin(target, answer_headers.get("Location", "")) if 300 <= status < 400 and answer_headers.get("Location") else ""
        if full_location:
            state = "signin" if "accounts.google.com" in full_location.lower() else "ok"
        else:
            state = ul.classify_probe(status, target, body, expect=expect if kind == "hop" else ())[0]
        step = {"kind": kind, "where": ul._where(target), "status": status, "state": state, "size": len(body), "content_type": answer_headers.get("Content-Type", ""),
                "location": ul._where(full_location) if full_location else "", "cookies": sorted({cookie.name for cookie in jar} - known), "frame_options": answer_headers.get("X-Frame-Options", "")}
        steps.append(step)
        return step, body, full_location

    current, referer = url, None
    step, body, location = fetch(current, "hop", referer)
    hops = 1
    while step and location and step["state"] == "ok" and hops < max_hops:               # a redirect to a sign-in page is the answer: it is reported, not followed
        referer, current = current, location
        step, body, location = fetch(current, "hop", referer)
        hops += 1
    if step and step["state"] == "ok" and not location:
        for frame_url in ul.find_frame_urls(body, frame_hosts)[:3]:
            fetch(frame_url, "frame", current)
    return steps


def load_unsubscribe_pairs(sheets) -> set:
    """{(address, brand key)} for everyone who has opted out. If the list cannot be read this RAISES: the caller must not send without it."""
    try:
        rows = sheets.read_unsubscribes()
    except Exception as exc:  # noqa: BLE001 - any failure to read the list means "do not send", whatever its cause
        raise UnsubscribeListUnreadableError(
            f"The Unsubscribes list could not be read ({exc}), so nothing was sent for this campaign: sending without it could email someone who has opted out. "
            "The next run tries again.") from exc
    return ul.load_pairs(rows)


def publish_campaign_code(campaign_cfg: Dict, sheets) -> None:
    """Before an HTML campaign sends with the unsubscribe page on: lists this campaign's code next to its name in the shared 'Campaign codes' tab, so the Unsubscribes tab can show the name.
    The links carry only the code. If it cannot be listed, the emails still go out and the Campaign column shows the code until it is listed: nobody is affected."""
    name = str(campaign_cfg.get("_campaign_name") or "").strip()
    config = ul.configuration(os.environ)
    if campaign_cfg.get("format") != FORMAT_HTML or config is None or not name:
        return
    publish = getattr(sheets, "ensure_campaign_codes", None)
    if publish is None:
        return
    code = ul.campaign_code(config.secret, name)
    try:
        publish({code: name})
    except Exception as exc:  # noqa: BLE001
        print(f"WARNING: could not list the campaign code {code} in the '{ul.CODES_TAB}' tab ({exc}). The emails are sent as usual; an opt-out will show this code in the Campaign column "
              "until the code is listed there. Nobody is affected.")


def reconcile_unsubscribes(campaign_cfg: Dict, sheets, leads: List[Dict], pairs: set) -> List[Dict]:
    """Marks the leads of this HTML campaign whose address has opted out of its brand as "Stopped - Unsubscribed" in their own tab, so the app shows it. A lead whose outcome is
    already decided (replied, bounced, rejected, stopped by hand, removed, or already unsubscribed) is left as it is; one that is only Completed or Paused is marked. Changes the lead dicts in place too. One failed write is reported and never stops the others."""
    changed = []
    for lead in ul.suppressed_leads(leads, campaign_brand_key(campaign_cfg), pairs):
        if str(lead.get("Status") or "").strip() in UNSUBSCRIBE_KEEPS_STATUS or not lead.get("_row"):
            continue
        now_str = datetime.now().strftime(DATETIME_FMT)
        try:
            sheets.update_lead_fields(lead["_row"], {"Status": STATUS_STOPPED_UNSUBSCRIBED, "LastActionAt": now_str})
        except Exception as exc:  # noqa: BLE001
            print(f"WARNING: could not mark {lead.get('Email', '?')} as unsubscribed in the sheet ({exc}); they are still never emailed.")
            continue
        lead["Status"], lead["LastActionAt"] = STATUS_STOPPED_UNSUBSCRIBED, now_str
        changed.append(lead)
    return changed


def _mirror_unsubscribes_quietly(campaign_cfg: Dict, sheets) -> None:
    """After a reply check: copy the Unsubscribes list into this HTML campaign's lead tab, so even a campaign that is not sending shows who has left. Never raises."""
    if campaign_cfg.get("format") != FORMAT_HTML:
        return
    try:
        changed = reconcile_unsubscribes(campaign_cfg, sheets, sheets.get_all_leads(), load_unsubscribe_pairs(sheets))
        if changed:
            print(f"{campaign_cfg.get('_campaign_name', '')}: marked {len(changed)} lead(s) as unsubscribed from the Unsubscribes list.")
    except Exception as exc:  # noqa: BLE001 - housekeeping must never fail a reply check
        print(f"{campaign_cfg.get('_campaign_name', '')}: could not copy the Unsubscribes list into the sheet ({exc}).")


# =============================================================================
# SECTION 6: Variant selector
# =============================================================================

def pick_variant(leads: List[Dict], variant_field: str, variants: List[str],
                  already_assigned_in_batch: Optional[Dict[str, int]] = None) -> str:
    counts = {v: 0 for v in variants}
    for lead in leads:
        v = lead.get(variant_field, "")
        if v in counts:
            counts[v] += 1

    if already_assigned_in_batch:
        for v, n in already_assigned_in_batch.items():
            if v in counts:
                counts[v] += n

    min_count = min(counts.values())
    least_used = [v for v, c in counts.items() if c == min_count]
    return random.choice(least_used)


# =============================================================================
# SECTION 7: Eligibility
# =============================================================================

def _parse_dt(value: str):
    if not value:
        return None
    try:
        return dateparser.parse(value)
    except (ValueError, TypeError):
        return None


EMAIL_FORMAT_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_valid_email_format(value: str) -> bool:
    return bool(EMAIL_FORMAT_RE.match((value or "").strip()))


def find_duplicate_email_leads(leads: List[Dict]) -> Dict[str, List[Dict]]:
    """{email: [lead, lead, ...]} for every email address appearing in MORE
    THAN ONE Master Sheet row — always a data-entry mistake within a single
    campaign (a copy-paste, or re-adding a lead under a new LeadID), never
    a legitimate case. get_eligible_leads already protects against
    actually sending to more than one of them regardless of whether this
    warning is surfaced — this is purely for visibility, so the mistake
    gets noticed and cleaned up rather than silently persisting."""
    by_email: Dict[str, List[Dict]] = {}
    for lead in leads:
        email = (lead.get("Email") or "").strip().lower()
        if not email:
            continue
        by_email.setdefault(email, []).append(lead)
    return {email: rows for email, rows in by_email.items() if len(rows) > 1}


VALID_SEND_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def is_within_sending_window(schedule: Dict, now_utc: Optional[datetime] = None) -> Tuple[bool, str]:
    """schedule: {timezone, window_start, window_end, send_days} — all
    optional, and a missing/empty schedule (or one with no timezone) means
    "always allowed", exactly matching every campaign's behavior before
    this feature existed. Returns (is_within, reason) — reason is only
    ever non-empty when is_within is False.

    Deliberately takes now_utc as a parameter (defaulting to the real
    current time) rather than always calling datetime.now() internally —
    that's what makes this testable across specific moments, including
    DST transitions, without mocking the clock.

    Uses a real IANA timezone via the stdlib zoneinfo — never a fixed
    offset — so DST is handled correctly automatically; this is exactly
    the category of bug (silently sending at the wrong local time, or
    silently never sending at all) that's easy to get subtly wrong with a
    naive UTC-offset calculation.
    """
    if not schedule:
        return True, ""

    tz_name = schedule.get("timezone")
    if not tz_name:
        return True, ""

    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(
            f"Invalid timezone '{tz_name}' in schedule config — use a real IANA name "
            f"(e.g. 'America/Los_Angeles', not 'PST'). Error: {exc}"
        )

    now_utc = now_utc or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    local_now = now_utc.astimezone(tz)

    send_days = schedule.get("send_days")
    if send_days:
        normalized_days = [d.strip().lower()[:3] for d in send_days]
        today = VALID_SEND_DAYS[local_now.weekday()]
        if today not in normalized_days:
            return False, (
                f"Today ({today}) is not one of this campaign's send_days ({', '.join(normalized_days)}) "
                f"in {tz_name}"
            )

    window_start = schedule.get("window_start")
    window_end = schedule.get("window_end")
    if window_start and window_end:
        try:
            start_t = datetime.strptime(window_start, "%H:%M").time()
            end_t = datetime.strptime(window_end, "%H:%M").time()
        except ValueError as exc:
            raise ConfigError(f"Invalid window_start/window_end in schedule config (expected HH:MM): {exc}")

        current_t = local_now.time()
        if start_t <= end_t:
            in_window = start_t <= current_t <= end_t
        else:
            # A window that crosses midnight (e.g. 22:00-06:00) — "in
            # window" means AFTER start OR BEFORE end, not between them.
            in_window = current_t >= start_t or current_t <= end_t

        if not in_window:
            return False, (
                f"Current time {current_t.strftime('%H:%M')} {tz_name} is outside the sending "
                f"window ({window_start}-{window_end})"
            )

    return True, ""


def _campaign_timezone_name(campaign_cfg: Dict) -> Optional[str]:
    """The IANA timezone of a campaign's sending schedule, or None if it
    has none. This is the timezone its follow-up due DATES are counted in."""
    return ((campaign_cfg or {}).get("schedule") or {}).get("timezone") or None


def _local_calendar_date(moment: datetime, timezone_name: Optional[str]) -> date:
    """The calendar date `moment` falls on, in the campaign's timezone.

    Every timestamp in the Sheet is a plain "2026-10-07 19:04:20" written
    by datetime.now() on whatever machine ran the engine — UTC, on GitHub
    Actions. So a naive `moment` is read as THIS machine's own clock and
    converted to the campaign's timezone; that is what turns 19:04 UTC
    into Wednesday 3:04 PM New York, i.e. Wednesday. An unknown or missing
    timezone just uses the machine's own date, never an error: the
    sending-window check is what reports a bad timezone."""
    if not timezone_name:
        return moment.date()
    try:
        tz = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        return moment.date()
    aware = moment.astimezone() if moment.tzinfo is None else moment
    return aware.astimezone(tz).date()


def get_eligible_leads(leads: List[Dict], stages: List[Dict], stage_index: int,
                        ignore_wait_days: bool = False, timezone_name: Optional[str] = None) -> List[Dict]:
    this_sent_field = stage_field_names(stage_index)["sent_at"]

    prev_sent_field = None
    wait_days = 0
    if stage_index > 0:
        prev_sent_field = stage_field_names(stage_index - 1)["sent_at"]
        wait_days = stages[stage_index].get("wait_days_after_previous", 0)

    eligible = []
    # The wait is counted in CALENDAR DAYS in the campaign's timezone, not
    # in hours and minutes: a follow-up that is 2 days after an intro sent
    # at 10:00 AM becomes due at the start of that day, not at 10:00 AM —
    # so a run at 9:00 AM that day can send it. The sending window and send
    # days still decide WHEN a due lead actually goes out; this only decides
    # whether it is due. A lead that is due on a day with no sending (a
    # weekend) simply stays due and goes out when sending next opens.
    today = _local_calendar_date(datetime.now(), timezone_name)

    for lead in leads:
        if not (lead.get("Email") or "").strip():
            continue  # Email is mandatory
        if lead.get("Status", "") in TERMINAL_STATUSES:
            continue
        if lead.get("ReplyStatus", "") == "Replied":
            continue
        if lead.get(this_sent_field, ""):
            continue  # already sent this stage — duplicate protection

        if stage_index == 0:
            eligible.append(lead)
            continue

        prev_sent_raw = lead.get(prev_sent_field, "")
        if not prev_sent_raw:
            continue  # the PREVIOUS stage must actually have been sent — this
                       # is stage ORDER, never skippable, unaffected by
                       # ignore_wait_days (that only overrides the WAIT, not
                       # the requirement that the previous stage happened)

        if ignore_wait_days:
            eligible.append(lead)
            continue

        prev_sent_dt = _parse_dt(prev_sent_raw)
        if prev_sent_dt is None:
            continue

        if today >= _local_calendar_date(prev_sent_dt, timezone_name) + timedelta(days=wait_days):
            eligible.append(lead)

    # De-duplicate by EMAIL ADDRESS, not row — the checks above only ever
    # protect a single row against being sent to twice. If the same email
    # exists as two separate rows (a copy-paste mistake, or re-adding a
    # lead under a new LeadID), both rows independently pass every check
    # and this would otherwise send the same person the same stage twice
    # in the same run. Keeps the first (lowest row number) occurrence —
    # see find_duplicate_email_leads for surfacing this as a warning
    # rather than silently dropping it.
    deduped = []
    seen_emails = set()
    for lead in eligible:
        email = (lead.get("Email") or "").strip().lower()
        if email in seen_emails:
            continue
        seen_emails.add(email)
        deduped.append(lead)
    return deduped


# =============================================================================
# SECTION 8: Email accounts — multi-account SMTP/IMAP with App Passwords
# =============================================================================

EMAIL_ACCOUNT_SLOT_COUNT = 10  # start small — see check_single_account_health's docstring in the
                                # Campaigns Hub plan for why this isn't jumped straight to 20

DEFAULT_SMTP_HOST = "smtp.gmail.com"
DEFAULT_SMTP_PORT = 465
DEFAULT_IMAP_HOST = "imap.gmail.com"
DEFAULT_IMAP_PORT = 993


def smtp_connection_settings(account: Dict) -> Tuple[str, int, str]:
    """(host, port, username) for SMTP — defaults to Gmail's own values
    and the account's login address when a custom provider's fields
    aren't set, so an existing Gmail-only account dict keeps working
    completely unchanged. A third-party provider (Hostinger, etc.) sets
    smtp_host/smtp_port/smtp_username explicitly instead."""
    host = account.get("smtp_host") or DEFAULT_SMTP_HOST
    port = int(account.get("smtp_port") or DEFAULT_SMTP_PORT)
    username = account.get("smtp_username") or account["address"]
    return host, port, username


def imap_connection_settings(account: Dict) -> Tuple[str, int, str]:
    """Same defaulting principle as smtp_connection_settings, for IMAP."""
    host = account.get("imap_host") or DEFAULT_IMAP_HOST
    port = int(account.get("imap_port") or DEFAULT_IMAP_PORT)
    username = account.get("imap_username") or account["address"]
    return host, port, username


def get_imap_password(account: Dict) -> str:
    """Most providers (Gmail included) use one password for both SMTP and
    IMAP — app_password already covers that case. A few (some Hostinger
    setups) issue a separate IMAP-specific password; when an account has
    imap_password set, IMAP calls use that instead of app_password.
    smtp_send never calls this — SMTP always uses app_password."""
    return account.get("imap_password") or account["app_password"]


def _load_email_accounts_from_slots() -> Dict[str, Dict[str, str]]:
    """Reads EMAIL_ACCOUNT_SLOT_1..N env vars, each holding ONE account's
    JSON ({"name": ..., "address": ..., "app_password": ..., and
    optionally smtp_host/smtp_port/smtp_username/imap_host/imap_port/
    imap_username for a non-Gmail provider}), empty/unset if that slot
    isn't in use. Returns {} — NOT an error — if none of the slots are
    populated at all, since a repo mid-migration (or one that hasn't
    started migrating) legitimately has zero slots set and relies
    entirely on the legacy EMAIL_ACCOUNTS_JSON blob instead.

    Keeps every field from the slot's JSON except "name" (which becomes
    the dict key) — not just address/app_password — so a custom
    provider's smtp_host etc. actually survives instead of silently
    being dropped."""
    accounts: Dict[str, Dict[str, str]] = {}
    for i in range(1, EMAIL_ACCOUNT_SLOT_COUNT + 1):
        raw = os.environ.get(f"EMAIL_ACCOUNT_SLOT_{i}")
        if not raw or not raw.strip():
            continue
        try:
            info = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"EMAIL_ACCOUNT_SLOT_{i} is not valid JSON: {exc}")
        for field in ("name", "address", "app_password"):
            if field not in info:
                raise RuntimeError(f"EMAIL_ACCOUNT_SLOT_{i} is missing '{field}'.")
        accounts[info["name"]] = {k: v for k, v in info.items() if k != "name"}
    return accounts


def load_email_accounts() -> Dict[str, Dict[str, str]]:
    """Reads from the new per-account EMAIL_ACCOUNT_SLOT_1..N secrets if
    any are populated, merged with the legacy EMAIL_ACCOUNTS_JSON blob if
    THAT'S also still set — a slot always wins over a same-named
    EMAIL_ACCOUNTS_JSON entry, so an in-app edit (which only ever touches
    a slot) takes effect immediately even before the old blob is
    eventually retired. Once every account has been migrated into a slot
    and EMAIL_ACCOUNTS_JSON is removed, this naturally becomes
    "slots only" with no code change needed here."""
    slot_accounts = _load_email_accounts_from_slots()

    raw_json = os.environ.get("EMAIL_ACCOUNTS_JSON")
    json_accounts: Dict[str, Dict[str, str]] = {}
    if raw_json:
        json_accounts = json.loads(raw_json)
        for name, info in json_accounts.items():
            if "address" not in info or "app_password" not in info:
                raise RuntimeError(f"Account '{name}' in EMAIL_ACCOUNTS_JSON is missing 'address' or 'app_password'.")

    if not slot_accounts and not json_accounts:
        raise RuntimeError(
            "No email accounts configured — set EMAIL_ACCOUNT_SLOT_1 (etc.), the legacy "
            "EMAIL_ACCOUNTS_JSON, or both during migration."
        )

    merged = dict(json_accounts)
    merged.update(slot_accounts)  # slots win on a name collision
    return merged


def resolve_sender_account(lead: Dict, campaign_cfg: Dict, accounts: Dict[str, Dict[str, str]]) -> str:
    """Original single-default resolution (no rotation): lead override >
    campaign default > global default. Still used directly whenever
    sender_rotation is off. Kept unchanged so nothing that depended on it
    before breaks."""
    requested = (lead.get("SenderAccount") or "").strip()
    if requested:
        if requested not in accounts:
            raise MissingSenderAccountError(f"Unknown SenderAccount '{requested}' — not in EMAIL_ACCOUNTS_JSON.")
        return requested

    campaign_default = campaign_cfg.get("default_sender_account", "")
    if campaign_default:
        if campaign_default not in accounts:
            raise MissingSenderAccountError(
                f"Campaign default_sender_account '{campaign_default}' not in EMAIL_ACCOUNTS_JSON.")
        return campaign_default

    global_default = campaign_cfg.get("_global_default_account", "")
    if not global_default:
        raise MissingSenderAccountError("No SenderAccount on the lead, and no default account is configured.")
    if global_default not in accounts:
        raise MissingSenderAccountError(f"Default account '{global_default}' not in EMAIL_ACCOUNTS_JSON.")
    return global_default


def normalize_account_name(name: str) -> str:
    """Reduces an account name to a comparison key that ignores every
    difference a human would consider meaningless: letter case,
    surrounding and internal whitespace, and the choice of separator
    between words (space, hyphen, underscore, dot).

    So "Sales 2", "sales-2", "SALES_2", "sales.2" and "sales2" all
    reduce to the same key. This exists because account names are typed
    by hand in three places that cannot validate each other — the
    EMAIL_ACCOUNTS_JSON secret, a campaign's sending.rotation_accounts,
    and the per-lead SenderAccount column — and any difference between
    them used to fork one account into two identities, which in
    practice meant either an empty rotation pool or a lead silently
    switching sender mid-thread.

    Deliberately conservative: it does NOT strip digits, collapse
    distinct words, or drop other characters, so "sales2" and "sales3"
    stay firmly distinct. Only separators and case are treated as
    noise."""
    if not name:
        return ""
    lowered = str(name).strip().lower()
    for sep in ("-", "_", ".", "\t"):
        lowered = lowered.replace(sep, " ")
    return re.sub(r"\s+", "", lowered)


class AmbiguousAccountNameError(Exception):
    """Two or more DIFFERENT account names in EMAIL_ACCOUNTS_JSON reduce
    to the same normalized key (e.g. "Sales 2" and "sales-2" both
    present as separate slots). Rather than silently picking one — which
    would send from an account the config never intended — this is
    raised so the ambiguity is fixed at the source."""


def canonical_account_name(name: str, accounts: Dict[str, Dict[str, str]]) -> Optional[str]:
    """Resolves an account name to the EXACT key used in
    EMAIL_ACCOUNTS_JSON, ignoring case, whitespace and separator style
    (see normalize_account_name), or None if there is no such account.

    An exact match always wins outright, so nothing changes for configs
    that already agree. Everything downstream keys off the canonical
    name returned here, so a formatting variant can never fork into a
    second identity.

    Raises AmbiguousAccountNameError if the name matches more than one
    DISTINCT account key — better a loud, fixable error than silently
    sending from the wrong address."""
    if not name:
        return None
    if name in accounts:
        return name
    target = normalize_account_name(name)
    if not target:
        return None
    matches = [key for key in accounts if normalize_account_name(key) == target]
    if len(matches) > 1:
        raise AmbiguousAccountNameError(
            f"Account name {name!r} matches more than one account in EMAIL_ACCOUNTS_JSON: "
            f"{sorted(matches)}. These differ only by case, spacing or separator — rename them "
            f"so each account has a distinctly different name."
        )
    return matches[0] if matches else None


def get_rotation_accounts(campaign_cfg: Dict, accounts: Dict[str, Dict[str, str]]) -> List[str]:
    """Which accounts are in the rotation pool. Defaults to every account in
    EMAIL_ACCOUNTS_JSON; narrow it with sending.rotation_accounts.

    Configured names are matched case-insensitively and normalized to
    their canonical EMAIL_ACCOUNTS_JSON key (see canonical_account_name):
    without that, a config saying "sales2" against a secret keyed
    "Sales2" silently produced an EMPTY rotation pool — every send in
    the campaign failing for what looks like a capitalization typo.
    Duplicates that collapse to the same canonical name are de-duped
    while preserving the configured order."""
    configured = campaign_cfg.get("sending", {}).get("rotation_accounts")
    if configured:
        resolved = []
        for a in configured:
            canonical = canonical_account_name(a, accounts)
            if canonical and canonical not in resolved:
                resolved.append(canonical)
        return resolved
    return list(accounts.keys())


def pick_rotation_account(rotation_accounts: List[str], per_account_daily_limit: Optional[int],
                           sent_today_by_account: Dict[str, int],
                           batch_assigned_counts: Dict[str, int]) -> Optional[str]:
    """Picks the least-used-today account from the rotation pool that still
    has capacity. Returns None if every account is at its cap."""
    eligible = []
    for acct in rotation_accounts:
        used = sent_today_by_account.get(acct, 0) + batch_assigned_counts.get(acct, 0)
        if per_account_daily_limit is not None and used >= per_account_daily_limit:
            continue
        eligible.append((acct, used))
    if not eligible:
        return None
    min_used = min(u for _, u in eligible)
    least_used = [a for a, u in eligible if u == min_used]
    return random.choice(least_used)


def _check_account_capacity(account_name: str, per_account_daily_limit: Optional[int],
                             sent_today_by_account: Dict[str, int],
                             batch_assigned_counts: Dict[str, int]) -> None:
    if per_account_daily_limit is None:
        return
    used = sent_today_by_account.get(account_name, 0) + batch_assigned_counts.get(account_name, 0)
    if used >= per_account_daily_limit:
        raise SenderCapacityReachedError(
            f"Account '{account_name}' has reached its per-account daily limit ({per_account_daily_limit}).")


def current_valid_senders_for_campaign(campaign_cfg: Dict, accounts: Dict[str, Dict[str, str]]) -> List[str]:
    """The set of accounts this campaign would actually use for a lead
    with NO pin at all, right now — used to decide whether an EXISTING
    pin has gone stale.

    The real, reported bug this exists to fix: a campaign launched with
    5 rotation accounts, later reduced to 1. If sender_rotation stayed
    checked, the earlier stale-pin fix already caught this (the pin is
    checked against get_rotation_accounts). But reducing to a single
    account is exactly when a person naturally also UNCHECKS "Rotate
    across multiple sender accounts" — there is only one account left,
    so rotating among it makes no sense. The old fix was conditioned on
    `sending.sender_rotation` being True, so unchecking it silently
    turned the stale-pin protection off entirely, and every lead
    pinned to one of the 4 removed accounts stayed stuck on a sender
    that no longer exists in this campaign's configuration — forever,
    since nothing about turning rotation off should have disabled this
    safety check at all.

    This generalizes across both configurations:
    - sender_rotation ON  -> the current rotation pool.
    - sender_rotation OFF -> the single account resolve_sender_account
      would pick for an UNPINNED lead (campaign default, else global
      default) — i.e. the one account "no rotation" mode actually sends
      from right now.

    Returns [] when nothing can be determined (nothing configured
    anywhere) — an empty result means "not enough information to judge
    staleness", so the caller must leave an existing pin untouched
    rather than treating silence as "nothing is valid, clear it".

    Order of precedence, and why:
    1. An explicit sending.rotation_accounts list, if present — this is
       authoritative for "which accounts this campaign uses" REGARDLESS
       of whether the sender_rotation FEATURE (round-robin selection)
       is currently toggled on. This is the actual fix for the reported
       bug: the Settings tab's account picker writes ONLY
       rotation_accounts, never sender_rotation's sibling
       default_sender_account — so unchecking "Rotate across multiple
       sender accounts" after narrowing to 1 account leaves
       rotation_accounts = ["sales1"] as the ONLY record of that
       decision anywhere. Ignoring it (as the first version of this fix
       did, falling through to a global default that was never touched
       by this flow at all) meant the staleness check had no evidence
       to act on and left the stale pin in place indefinitely.
    2. sender_rotation on with no explicit list — rotates across every
       globally configured account.
    3. Single-default resolution for an UNPINNED lead (campaign
       default, else global default)."""
    sending_cfg = campaign_cfg.get("sending", {})
    if sending_cfg.get("rotation_accounts"):
        return get_rotation_accounts(campaign_cfg, accounts)
    if sending_cfg.get("sender_rotation"):
        return list(accounts.keys())
    try:
        return [resolve_sender_account({}, campaign_cfg, accounts)]
    except MissingSenderAccountError:
        return []


def resolve_sender_account_for_send(lead: Dict, campaign_cfg: Dict, accounts: Dict[str, Dict[str, str]],
                                     sent_today_by_account: Dict[str, int],
                                     batch_assigned_counts: Dict[str, int]) -> str:
    """The resolution actually used at send time. Priority, per the design
    goal of keeping manual assignment as an override rather than replacing
    it:

    1. The lead's own SenderAccount cell, if set — ALWAYS wins, and is
       checked against the per-account daily limit exactly like every other
       path (a manual pin doesn't bypass the safety cap; if that account is
       full, this lead is deferred rather than silently rerouted to a
       different account than the sheet says).
    2. If sending.sender_rotation is enabled — auto-pick the least-used
       eligible account from the rotation pool.
    3. Otherwise, the original single-default behavior (resolve_sender_account).

    Raises MissingSenderAccountError or SenderCapacityReachedError, both
    caught and classified by send_batch's per-lead error isolation.
    """
    sending_cfg = campaign_cfg.get("sending", {})
    per_account_limit = sending_cfg.get("per_account_daily_limit")

    requested = (lead.get("SenderAccount") or "").strip()
    if requested:
        canonical_requested = canonical_account_name(requested, accounts)
        if canonical_requested is None:
            raise MissingSenderAccountError(f"Unknown SenderAccount '{requested}' — not in EMAIL_ACCOUNTS_JSON.")
        requested = canonical_requested
        # Same generalized staleness check as _resolve_account_for_round —
        # kept in sync so this function (currently unused, but its own
        # tests keep it live) can never silently regress to the version
        # of this bug where a pin to a since-removed account was honored
        # forever regardless of rotation being on or off.
        current_valid = current_valid_senders_for_campaign(campaign_cfg, accounts)
        if not current_valid or requested in current_valid:
            _check_account_capacity(requested, per_account_limit, sent_today_by_account, batch_assigned_counts)
            return requested
        # else: pin is stale — fall through to rotation/default below.

    # Same unified condition as _resolve_account_for_round — see its
    # comment for why rotation_accounts alone must trigger this branch
    # even with sender_rotation toggled off.
    if sending_cfg.get("sender_rotation") or sending_cfg.get("rotation_accounts"):
        rotation_accounts = get_rotation_accounts(campaign_cfg, accounts)
        if not rotation_accounts:
            raise MissingSenderAccountError(
                "sender_rotation is enabled but no valid accounts are configured "
                "(check sending.rotation_accounts and EMAIL_ACCOUNTS_JSON)."
            )
        chosen = pick_rotation_account(rotation_accounts, per_account_limit,
                                        sent_today_by_account, batch_assigned_counts)
        if chosen is None:
            raise SenderCapacityReachedError(
                f"All rotation accounts are at their per-account daily limit ({per_account_limit}).")
        return chosen

    chosen = resolve_sender_account(lead, campaign_cfg, accounts)
    _check_account_capacity(chosen, per_account_limit, sent_today_by_account, batch_assigned_counts)
    return chosen


# =============================================================================
# SECTION 9: SMTP sending
# =============================================================================

def _build_outbound_message(sender_address: str, to: str, subject: str, body_text: str,
                             in_reply_to: Optional[str] = None, references: Optional[str] = None,
                             cc: Optional[List[str]] = None, attachments: Optional[List[Dict]] = None,
                             html_body: Optional[str] = None, unsubscribe_url: Optional[str] = None):
    """attachments: [{"filename": str, "content": bytes}, ...] — sent as
    regular email attachments (not inline/embedded HTML images; that
    would need a whole HTML-body path this codebase doesn't have). When
    empty/None, builds the exact same plain MIMEText message as before
    this feature existed — every existing caller (every automated
    send) never passes attachments, so their message shape is completely
    unchanged."""
    if html_body is not None:
        # An HTML campaign's email: ONE message holding the plain-text copy first and the designed version second (the mail app shows the better one it can).
        if attachments:
            raise ValueError("An HTML email cannot also carry attachments.")
        # The footer's Unsubscribe link points at the mailbox this email is sent from, which is only known here.
        body_text = hc.fill_unsubscribe_text(body_text, sender_address)
        html_body = hc.fill_unsubscribe_html(html_body, sender_address)
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(body_text, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))
    elif attachments:
        msg = MIMEMultipart()
        msg.attach(MIMEText(body_text))
        for att in attachments:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(att["content"])
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f'attachment; filename="{att["filename"]}"')
            msg.attach(part)
    else:
        msg = MIMEText(body_text)
    msg["Subject"] = subject
    msg["From"] = sender_address
    msg["To"] = to
    if cc:
        msg["Cc"] = ", ".join(cc)
    # Bcc is deliberately NEVER added as a header — that's what makes it
    # blind. It only ever affects the SMTP envelope recipient list, in
    # smtp_send below.
    if html_body is not None:
        # Mail apps show an "Unsubscribe" link from this header; pressing it sends an email to the sending mailbox, which the reply check recognises (see check_replies).
        if unsubscribe_url:
            # The hosted page is set up: the address that unsubscribes with ONE tap (RFC 8058) goes first, the email-based one stays as the fallback.
            msg["List-Unsubscribe"] = f"<{unsubscribe_url}>, <{hc.unsubscribe_mailto(sender_address)}>"
            msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
        else:
            msg["List-Unsubscribe"] = f"<{hc.unsubscribe_mailto(sender_address)}>"
    msg["Date"] = formatdate(localtime=True)
    # An HTML email's Message-ID is made on the sender's own domain (not the machine's name, which looks like "@localhost" to spam filters). Plain-text mail is unchanged.
    sender_domain = sender_address.rsplit("@", 1)[-1] if "@" in sender_address else ""
    message_id = make_msgid(domain=sender_domain) if (html_body is not None and sender_domain) else make_msgid()
    msg["Message-ID"] = message_id
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    return msg, message_id


def smtp_send(address: str, app_password: str, to: str, subject: str, body_text: str,
              in_reply_to: Optional[str] = None, references: Optional[str] = None,
              cc: Optional[List[str]] = None, bcc: Optional[List[str]] = None,
              attachments: Optional[List[Dict]] = None,
              smtp_host: str = DEFAULT_SMTP_HOST, smtp_port: int = DEFAULT_SMTP_PORT,
              smtp_username: Optional[str] = None, html_body: Optional[str] = None, unsubscribe_url: Optional[str] = None) -> Dict[str, str]:
    """smtp_host/smtp_port/smtp_username default to Gmail's own values
    and `address` itself — every existing call site (which never passes
    them) keeps sending through Gmail exactly as before. A third-party
    provider (Hostinger, etc.) passes its own host/port, and a login
    username that can differ from the visible From address — see
    smtp_connection_settings, which every real call site should build
    these three arguments from rather than reading account fields here
    directly."""
    html_extra = {"html_body": html_body} if html_body is not None else {}
    if html_body is not None and unsubscribe_url:
        html_extra["unsubscribe_url"] = unsubscribe_url
    msg, message_id = _build_outbound_message(address, to, subject, body_text, in_reply_to, references,
                                               cc=cc, attachments=attachments, **html_extra)
    envelope_recipients = [to] + list(cc or []) + list(bcc or [])
    login_username = smtp_username or address
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context) as server:
        server.login(login_username, app_password)
        server.sendmail(address, envelope_recipients, msg.as_string())
    return {"message_id": message_id}


MAX_TOTAL_ATTACHMENT_BYTES = 10 * 1024 * 1024  # 10 MB, see AttachmentTooLargeError's docstring


def send_manual_reply(accounts: Dict[str, Dict[str, str]], sender_account: str, to_email: str,
                       subject: str, body: str, in_reply_to: Optional[str] = None,
                       references: Optional[str] = None, cc: Optional[List[str]] = None,
                       bcc: Optional[List[str]] = None, attachments: Optional[List[Dict]] = None) -> Dict[str, str]:
    """Sends a one-off manual reply from inside the app — NOT part of the
    automated batch sequence, and never called with data Streamlit
    computed insecurely: the caller (Streamlit's Responses tab) is
    expected to have already resolved in_reply_to/references from the
    specific inbound message being replied to, exactly the same
    header-based threading every automated follow-up already relies on —
    this function just sends what it's given, it doesn't look anything up
    itself.

    attachments (optional): [{"filename": str, "content": bytes}, ...].
    Raises AttachmentTooLargeError if their combined size exceeds
    MAX_TOTAL_ATTACHMENT_BYTES — checked BEFORE anything is sent.

    Raises MissingSenderAccountError / InvalidEmailFormatError the same
    way the rest of this file does, so classify_send_exception and the
    existing error-logging path both already handle it correctly with no
    special-casing needed.
    """
    if sender_account not in accounts:
        raise MissingSenderAccountError(f"Unknown SenderAccount '{sender_account}' — not in EMAIL_ACCOUNTS_JSON.")
    if not is_valid_email_format(to_email):
        raise InvalidEmailFormatError(f"'{to_email}' is not a valid email address format")
    for extra in list(cc or []) + list(bcc or []):
        if not is_valid_email_format(extra):
            raise InvalidEmailFormatError(f"'{extra}' (Cc/Bcc) is not a valid email address format")
    if attachments:
        total_size = sum(len(att["content"]) for att in attachments)
        if total_size > MAX_TOTAL_ATTACHMENT_BYTES:
            raise AttachmentTooLargeError(
                f"Attachments total {total_size / (1024*1024):.1f} MB, over the "
                f"{MAX_TOTAL_ATTACHMENT_BYTES / (1024*1024):.0f} MB limit."
            )

    account = accounts[sender_account]
    smtp_host, smtp_port, smtp_username = smtp_connection_settings(account)
    return smtp_send(account["address"], account["app_password"], to=to_email, subject=subject,
                      body_text=body, in_reply_to=in_reply_to, references=references, cc=cc, bcc=bcc,
                      attachments=attachments, smtp_host=smtp_host, smtp_port=smtp_port,
                      smtp_username=smtp_username)


TEST_EMAIL_PREFIX = "[TEST] "


def send_test_email(campaign_cfg: Dict, accounts: Dict[str, Dict[str, str]], stage_prefix: str, variant: str, to_email: str,
                    sender_account: Optional[str] = None) -> Dict:
    """Sends ONE email of a campaign to the given address, built exactly the way the real send builds it (an HTML campaign's as the two-version message with its unsubscribe
    header), with a made-up lead and a [TEST] subject, so the team can see what arrives. Touches no Sheet and no lead, and records nothing: it is not part of any sequence.
    Raises the same errors as every other send path (unknown sender, bad address) and TemplateError for a broken template."""
    if not is_valid_email_format(to_email or ""):
        raise InvalidEmailFormatError(f"'{to_email}' is not a valid email address format")
    stage_names = [stage["template_prefix"] for stage in campaign_cfg["stages"]]
    if stage_prefix not in stage_names:
        raise ValueError(f"'{stage_prefix}' is not a stage of this campaign (stages: {', '.join(stage_names)}).")
    if variant not in campaign_cfg["variants"]:
        raise ValueError(f"'{variant}' is not a variant of this campaign (variants: {', '.join(campaign_cfg['variants'])}).")
    account_name = sender_account or campaign_cfg.get("default_sender_account") or campaign_cfg.get("_global_default_account") or next(iter(accounts), "")
    if not account_name or account_name not in accounts:
        raise MissingSenderAccountError(f"Unknown SenderAccount '{account_name}' — not in EMAIL_ACCOUNTS_JSON.")
    lead = {"FirstName": "Test", "LastName": "Recipient", "Company": "Your Company", "Email": to_email, "LeadID": "TEST", "ThreadSubject": "Test of this campaign", "_unsubscribe_test": True}
    is_html = campaign_cfg.get("format") == FORMAT_HTML
    rendered = render_email(campaign_cfg["templates_dir"], stage_prefix, variant, lead, is_first_stage=(stage_prefix == stage_names[0]), ignore_body=is_html)
    html_extra = {}
    if is_html:
        html_result = render_email_html(campaign_cfg["templates_dir"], stage_prefix, variant, lead, campaign_cfg)
        html_extra = {"html_body": html_result["html"]}
        footer_link = html_result.get("unsubscribe_footer_url") or ""
        if html_result.get("unsubscribe_url"):
            html_extra["unsubscribe_url"] = html_result["unsubscribe_url"]
        rendered["body"] = hc.html_to_text(html_result["html"])
        rendered["missing_variables"] = list(dict.fromkeys(list(rendered["missing_variables"]) + html_result["missing_variables"]))
    account = accounts[account_name]
    smtp_host, smtp_port, smtp_username = smtp_connection_settings(account)
    subject = TEST_EMAIL_PREFIX + rendered["subject"]
    sent = smtp_send(account["address"], account["app_password"], to=to_email, subject=subject, body_text=rendered["body"],
                     smtp_host=smtp_host, smtp_port=smtp_port, smtp_username=smtp_username, **html_extra)
    return {"message_id": sent["message_id"], "to": to_email, "subject": subject, "format": campaign_cfg.get("format") or FORMAT_TEXT, "account": account_name,
            "missing_variables": rendered["missing_variables"], "unsubscribe_header": ((f"<{html_extra['unsubscribe_url']}>, " if html_extra.get("unsubscribe_url") else "") + f"<{hc.unsubscribe_mailto(account['address'])}>") if is_html else "",
            "unsubscribe_page": bool(html_extra.get("unsubscribe_url")), "unsubscribe_url": html_extra.get("unsubscribe_url") or "",
            "unsubscribe_footer_url": footer_link if is_html else "",
            "link_in_every_copy": bool(html_extra.get("unsubscribe_url")) and html_lib.escape(footer_link, quote=True) in html_extra["html_body"] and footer_link in rendered["body"]}


def classify_send_exception(exc: Exception) -> str:
    """Maps any exception raised during the send attempt to one of the
    error-monitoring categories."""
    if isinstance(exc, MissingSenderAccountError):
        return ERR_MISSING_SENDER_ACCOUNT
    if isinstance(exc, SenderCapacityReachedError):
        return ERR_SENDER_CAPACITY
    if isinstance(exc, InvalidEmailFormatError):
        return ERR_INVALID_EMAIL
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return ERR_AUTH_FAILURE
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return ERR_INVALID_EMAIL
    if isinstance(exc, smtplib.SMTPResponseException):
        code = getattr(exc, "smtp_code", None)
        raw_error = getattr(exc, "smtp_error", b"")
        text = f"{raw_error} {exc}".lower()
        if code in (421, 450, 451, 452, 454) or "rate" in text or "too many" in text or "try again later" in text:
            return ERR_RATE_LIMIT
        return ERR_SEND_FAILURE
    if isinstance(exc, (smtplib.SMTPException, OSError, TimeoutError)):
        return ERR_SEND_FAILURE
    if type(exc).__name__ == "APIError" or "gspread" in type(exc).__module__:
        return ERR_SHEETS_API
    return ERR_SEND_FAILURE


# =============================================================================
# SECTION 10: IMAP reading
# =============================================================================

def _decode_header_value(raw: str) -> str:
    if not raw:
        return ""
    parts = decode_header(raw)
    decoded = []
    for text, encoding in parts:
        if isinstance(text, bytes):
            decoded.append(text.decode(encoding or "utf-8", errors="replace"))
        else:
            decoded.append(text)
    return "".join(decoded)


_MAX_HTML_CHARS = 1_000_000   # more than any real email; bounds the work a hostile one can cause


class _HTMLToText(HTMLParser):
    """Turns an HTML email body into readable plain text, standard library only.
    Paragraphs get a blank line around them, list items / divs / table rows a
    line break, scripts and styles are dropped, entities are decoded, and a link
    whose address is not already visible in its own text keeps that address in
    brackets after it. Quoted history (a <blockquote>, which is how Apple Mail,
    Gmail and Outlook all wrap the earlier messages) keeps the '> ' markers a
    plain-text reply would have, so what a person just wrote is never mistaken
    for what they are quoting."""
    _SKIP = {"script", "style", "head", "title"}
    _PARAGRAPH = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "table", "ul", "ol"}
    _LINE = {"div", "tr", "li", "hr", "section", "article", "header", "footer"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._parts: List[Tuple[str, int]] = []       # (text, how deeply quoted it was when written)
        self._trailing_newlines = 2                    # start of text counts as already "on a fresh paragraph"
        self._quote_depth = 0
        self._skip_depth = 0
        self._href: Optional[str] = None
        self._link_text: List[str] = []

    def _add(self, text: str) -> None:
        if not text:
            return
        self._parts.append((text, self._quote_depth))
        self._trailing_newlines = len(text) - len(text.rstrip("\n"))

    def _ensure_newlines(self, count: int) -> None:
        if self._trailing_newlines < count:
            self._add("\n" * (count - self._trailing_newlines))

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "br":
            self._add("\n")                  # an explicit break always counts, so <br><br> is a blank line
        elif tag in self._PARAGRAPH:
            self._ensure_newlines(2)
        elif tag in self._LINE:
            self._ensure_newlines(1)
        if tag == "blockquote":
            self._quote_depth += 1
        if tag == "li":
            self._add("- ")
        if tag == "a":
            self._href = (dict(attrs).get("href") or "").strip() or None
            self._link_text = []

    def handle_endtag(self, tag):
        if tag in self._SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag == "a" and self._href:
            visible = "".join(self._link_text)
            if self._href.lower().startswith(("http://", "https://")) and self._href.rstrip("/") not in visible:
                self._add(f" ({self._href})")
            self._href = None
        if tag in ("td", "th") and not self._trailing_newlines:
            self._add(" ")
        if tag == "blockquote":
            self._quote_depth = max(0, self._quote_depth - 1)
        if tag in self._PARAGRAPH:
            self._ensure_newlines(2)
        elif tag in self._LINE:
            self._ensure_newlines(1)

    def handle_data(self, data):
        if self._skip_depth:
            return
        collapsed = re.sub(r"\s+", " ", data)
        if not collapsed.strip() and self._trailing_newlines:
            return                            # whitespace between tags, right after a line break: nothing to show
        self._add(collapsed)
        if self._href is not None:
            self._link_text.append(collapsed)

    def text(self) -> str:
        lines: List[Tuple[int, str]] = []
        buffer, buffer_depth = "", None
        for chunk, depth in self._parts:
            for index, piece in enumerate(chunk.split("\n")):
                if index:                      # a line break: close the line, remembering how deeply quoted it was
                    lines.append((buffer_depth if buffer_depth is not None else depth, buffer))
                    buffer, buffer_depth = "", None
                if piece:
                    if buffer_depth is None:
                        buffer_depth = depth
                    buffer += piece
        lines.append((buffer_depth or 0, buffer))
        out: List[str] = []
        for depth, line in lines:
            rendered = ("> " * depth + line.strip()).rstrip()
            if not line.strip() and out and out[-1] == rendered:
                continue                       # one blank (or blank-quoted) line is enough
            out.append(rendered)
        while out and re.fullmatch(r"(>\s*)*", out[-1]):
            out.pop()                          # nothing but blank or empty-quote lines left at the end
        return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def _html_to_text(markup: str) -> str:
    markup = markup[:_MAX_HTML_CHARS]
    try:
        parser = _HTMLToText()
        parser.feed(markup)
        parser.close()
        return parser.text()
    except Exception:  # noqa: BLE001 - malformed markup must never cost the reply: fall back to crude tag stripping
        stripped = html_lib.unescape(re.sub(r"<[^>]*>", " ", markup))
        return re.sub(r"[ \t]+", " ", stripped).strip()


def _decode_part(part) -> str:
    """A MIME part's text. An unknown or misspelt charset name (some clients
    send values Python has never heard of) falls back to UTF-8 rather than
    raising — a raise here used to make the whole reply vanish unlogged."""
    payload = part.get_payload(decode=True)
    if not payload:
        return ""
    try:
        return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _describe_structure(msg) -> str:
    """The email's parts by content type only — e.g. 'text/plain (empty), text/html' — never
    any of its content. Lets an email that still shows no text be diagnosed from a public log."""
    labels = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        label = part.get_content_type()
        if "attachment" in str(part.get("Content-Disposition", "")).lower():
            label += " (attachment)"
        elif label.startswith("text/") and not _decode_part(part).strip():
            label += " (empty)"
        labels.append(label)
    return ", ".join(labels) or "(no parts)"


def _extract_plain_text_body(msg) -> str:
    """The readable text of an email. The first text/plain part that actually
    contains something wins, exactly as before. Many clients (Outlook, mobile
    apps, rich-text mail) send HTML only, or an empty plain part beside the
    real HTML one; those used to be logged with an empty body, which also meant
    the intent reader and the auto-reply/bounce detector saw nothing. When there
    is no usable plain text, the HTML part is converted to text instead. An
    email with neither (a lone image or attachment) still gives ''."""
    html_text = ""
    for part in msg.walk():
        if part.is_multipart() or "attachment" in str(part.get("Content-Disposition", "")).lower():
            continue
        content_type = part.get_content_type()
        if content_type == "text/plain":
            text = _decode_part(part)
            if text.strip():
                return text
        elif content_type == "text/html" and not html_text:
            html_text = _html_to_text(_decode_part(part))
    return html_text


def _message_to_dict(msg) -> Dict:
    subject = _decode_header_value(msg.get("Subject", ""))
    from_ = _decode_header_value(msg.get("From", ""))
    message_id = (msg.get("Message-ID", "") or "").strip()
    in_reply_to = (msg.get("In-Reply-To", "") or "").strip()
    references = (msg.get("References", "") or "").strip()
    body = _extract_plain_text_body(msg)
    headers = {k.lower(): v for k, v in msg.items()}

    parsed_date = None
    date_header = headers.get("date", "")
    if date_header:
        try:
            parsed_date = parsedate_to_datetime(date_header)
            if parsed_date.tzinfo is not None:
                # Convert to UTC FIRST, then strip tzinfo — every other
                # timestamp in this system (IntroSentAt, FollowUp*SentAt,
                # LastActionAt, etc.) comes from datetime.now() on the
                # GitHub Actions runner, which is UTC and naive. Simply
                # discarding the offset here without converting silently
                # kept the sender's own LOCAL wall-clock time instead —
                # a real, confirmed production case: a reply genuinely
                # sent AFTER the most recent outreach email, from a
                # sender in US Eastern time (UTC-4), had its "13:58" logged
                # as if it were "13:58 UTC" rather than converted to the
                # correct "17:58 UTC" — a large enough gap to make an
                # actual, current reply look like it arrived BEFORE the
                # send it was replying to, misclassifying a genuine
                # "Stopped Sequence" reply as "Logged Only (Predates
                # Contact)" and never stopping the sequence.
                parsed_date = parsed_date.astimezone(timezone.utc).replace(tzinfo=None)
        except (TypeError, ValueError):
            parsed_date = None

    return {
        "message_id": message_id, "in_reply_to": in_reply_to, "references": references,
        "subject": subject, "from": from_, "headers": headers, "body": body,
        "snippet": body[:500], "date": parsed_date, "structure": _describe_structure(msg),
    }


def _parse_email_message(raw_bytes: bytes) -> Dict:
    msg = email.message_from_bytes(raw_bytes)
    return _message_to_dict(msg)


def imap_fetch_recent(address: str, app_password: str, since_dt: datetime,
                       imap_host: str = DEFAULT_IMAP_HOST, imap_port: int = DEFAULT_IMAP_PORT,
                       imap_username: Optional[str] = None) -> List[Dict]:
    """imap_host/imap_port/imap_username default to Gmail's own values
    and `address` itself — every existing call site keeps working
    unchanged. See imap_connection_settings, which every real call site
    should build these three arguments from."""
    login_username = imap_username or address
    imap = imaplib.IMAP4_SSL(imap_host, imap_port)
    try:
        imap.login(login_username, app_password)
        imap.select("INBOX", readonly=True)
        date_str = since_dt.strftime("%d-%b-%Y")
        status, data = imap.search(None, f'(SINCE "{date_str}")')
        if status != "OK":
            return []
        ids = data[0].split()
        messages = []
        for num in ids:
            status, msg_data = imap.fetch(num, "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            try:
                parsed = _parse_email_message(raw)
            except Exception:  # noqa: BLE001 - one malformed message must never block the rest of the mailbox
                # A real, severe fragility this closes: nothing here was
                # ever wrapped per-message before, so a single message
                # this parser can't handle — any unusual MIME structure,
                # encoding, or header shape, not only a date header —
                # would raise straight out of this loop and abort
                # fetching for the WHOLE mailbox. Every message after the
                # bad one, from every sender, would silently never be
                # seen at all, with no error visible anywhere except a
                # failed IMAP account-health check that doesn't name
                # which message caused it. One bad message must only
                # cost that one message, never the rest of the inbox.
                continue

            msg_date = parsed["date"]
            if msg_date is not None and msg_date < since_dt:
                continue

            messages.append(parsed)
        return messages
    finally:
        try:
            imap.logout()
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass


_SAFE_MESSAGE_ID = re.compile(r"^<[^<>\s\"\\\x00-\x1f]+>$")


def imap_fetch_by_message_ids(address: str, app_password: str, message_ids: List[str],
                               imap_host: str = DEFAULT_IMAP_HOST, imap_port: int = DEFAULT_IMAP_PORT,
                               imap_username: Optional[str] = None,
                               folders: Tuple[str, ...] = ("INBOX", '"[Gmail]/All Mail"')) -> Dict[str, Dict]:
    """{message_id: parsed message} for each requested Message-ID found in this
    mailbox — a targeted lookup of specific, already-logged messages, never a
    sweep of the inbox. Read-only (it cannot change a flag), one login, and it
    also looks in Gmail's All Mail for anything archived out of the inbox.
    A Message-ID that is not in the usual <...> shape is skipped rather than
    sent to the server, since these values come from other people's email."""
    wanted = [m for m in dict.fromkeys(message_ids) if m and _SAFE_MESSAGE_ID.match(m)]
    found: Dict[str, Dict] = {}
    if not wanted:
        return found
    imap = imaplib.IMAP4_SSL(imap_host, imap_port)
    try:
        imap.login(imap_username or address, app_password)
        for folder in folders:
            remaining = [m for m in wanted if m not in found]
            if not remaining:
                break
            try:
                status, _ = imap.select(folder, readonly=True)
            except Exception:  # noqa: BLE001 - a provider without this folder (any non-Gmail mailbox)
                continue
            if status != "OK":
                continue
            for message_id in remaining:
                try:
                    status, data = imap.search(None, "HEADER", "Message-ID", f'"{message_id}"')
                    ids = data[0].split() if status == "OK" and data and data[0] else []
                    if not ids:
                        continue
                    status, msg_data = imap.fetch(ids[-1], "(RFC822)")
                    if status != "OK" or not msg_data or not msg_data[0]:
                        continue
                    found[message_id] = _parse_email_message(msg_data[0][1])
                except Exception:  # noqa: BLE001 - one unreadable message must not stop the others
                    continue
        return found
    finally:
        try:
            imap.logout()
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass


# ----------------------------------------------------------------------------
# Emails WE sent that the system did not send by itself (see SENT_MAIL_COLUMNS)
# ----------------------------------------------------------------------------

_LIST_LINE = re.compile(r'^\((?P<flags>[^)]*)\)\s+(?:"(?:[^"\\]|\\.)*"|NIL)\s+(?P<name>.+)$')


def _message_ids_in(text) -> List[str]:
    return re.findall(r"<[^<>\s]+>", str(text or ""))


def _quote_imap_folder(name: str) -> str:
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _sent_folder_candidates(imap) -> List[str]:
    """Folder names to try for 'Sent', best first: whatever the server itself flags as \\Sent (so
    Gmail in any language, and most other providers, are found without guessing), then the common names."""
    names: List[str] = []
    try:
        status, data = imap.list()
        if status == "OK":
            for raw in data or []:
                line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
                match = _LIST_LINE.match(line.strip())
                if match and "\\sent" in match.group("flags").lower():
                    name = match.group("name").strip()
                    if len(name) >= 2 and name.startswith('"') and name.endswith('"'):
                        name = name[1:-1].replace('\\"', '"').replace("\\\\", "\\")
                    names.append(name)
    except Exception:  # noqa: BLE001 - fall back to the usual names below
        pass
    for fallback in ("[Gmail]/Sent Mail", "Sent", "Sent Items", "Sent Messages", "INBOX.Sent"):
        if fallback not in names:
            names.append(fallback)
    return names


def imap_fetch_sent(address: str, app_password: str, since_dt: datetime, accept,
                    imap_host: str = DEFAULT_IMAP_HOST, imap_port: int = DEFAULT_IMAP_PORT,
                    imap_username: Optional[str] = None) -> List[Dict]:
    """Parsed copies of what this mailbox SENT since `since_dt`, read-only, from its Sent folder.

    Two steps so a busy Sent folder (every automated outreach email lands there) stays cheap: first
    only each message's headers are fetched, and `accept(header_message)` decides whether the message
    is worth downloading in full; only those are. Raises if no Sent folder can be opened — the caller
    records that against the account and carries on."""
    imap = imaplib.IMAP4_SSL(imap_host, imap_port)
    try:
        imap.login(imap_username or address, app_password)
        opened = False
        for folder in _sent_folder_candidates(imap):
            try:
                status, _ = imap.select(_quote_imap_folder(folder), readonly=True)
            except Exception:  # noqa: BLE001 - not this provider's name for it
                continue
            if status == "OK":
                opened = True
                break
        if not opened:
            raise RuntimeError("could not find this mailbox's Sent folder")
        status, data = imap.search(None, f'(SINCE "{since_dt.strftime("%d-%b-%Y")}")')
        if status != "OK" or not data or not data[0]:
            return []
        found: List[Dict] = []
        for num in data[0].split():
            try:
                status, head = imap.fetch(num, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID TO CC IN-REPLY-TO REFERENCES SUBJECT DATE)])")
                if status != "OK" or not head or not head[0]:
                    continue
                if not accept(email.message_from_bytes(head[0][1])):
                    continue
                status, msg_data = imap.fetch(num, "(RFC822)")
                if status != "OK" or not msg_data or not msg_data[0]:
                    continue
                parsed = _parse_email_message(msg_data[0][1])
            except Exception:  # noqa: BLE001 - one unreadable message must not stop the others
                continue
            if parsed["date"] is not None and parsed["date"] < since_dt:
                continue
            found.append(parsed)
        return found
    finally:
        try:
            imap.logout()
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass


def find_sent_mail_to_log(sheets, accounts: Dict[str, Dict[str, str]], lookback_hours: int,
                          fetch_sent=None, now: Optional[datetime] = None) -> Dict:
    """Finds emails this campaign's mailboxes SENT that belong to one of its leads' conversations but
    are not stored anywhere yet — what you typed by hand, or replied from your phone. Reads only; see
    sync_sent_mail to store them.

    A sent message belongs to a lead when it replies to a message already known to be in that lead's
    thread (its threading headers name one — the same strongest test used for incoming replies), or,
    failing that, when it is addressed to the lead and its subject is a reply to the lead's thread
    subject. Anything else — your mail to the same person about something unrelated — is ignored and
    never stored. What the system sent by itself (the Send Log) is skipped, and so is anything already
    stored, so running this again changes nothing. Messages are matched oldest first, so a later reply
    in a chain is recognised by the earlier one."""
    fetch_sent = fetch_sent or imap_fetch_sent
    now = now or datetime.now()
    result: Dict = {"rows": [], "account_errors": []}
    leads = sheets.get_all_leads()
    if not leads:
        return result

    owner_of_id: Dict[str, str] = {}               # a message id already in a lead's thread -> that lead

    def claim(message_ids, lead_id):
        if lead_id:
            for message_id in message_ids:
                owner_of_id.setdefault(message_id, lead_id)

    by_email: Dict[str, Dict] = {}
    for lead in leads:
        lead_id = str(lead.get("LeadID", ""))
        claim(_message_ids_in(lead.get("MessageID")), lead_id)
        claim(_message_ids_in(lead.get("ThreadReferences")), lead_id)
        address = (lead.get("Email") or "").strip().lower()
        if address:
            by_email.setdefault(address, lead)
    for response in sheets.get_all_responses():
        claim(_message_ids_in(response.get("MessageID")), str(response.get("LeadID", "")))
    sent_by_system = set()
    for entry in sheets.get_all_send_log():
        message_id = (entry.get("MessageID") or "").strip()
        if message_id:
            if entry.get("Stage") != "manual_reply":     # a control-panel reply is wanted here, if its own copy was lost
                sent_by_system.add(message_id)
            claim([message_id], str(entry.get("LeadID", "")))
    skip = sent_by_system | set(sheets.get_sent_mail_ids())

    def addresses(*headers) -> List[str]:
        return [a.lower() for _, a in getaddresses([str(h or "") for h in headers]) if a]

    def accept(header) -> bool:
        message_id = str(header.get("Message-ID") or "").strip()
        if not message_id or message_id in skip:
            return False
        if any(r in owner_of_id for r in _message_ids_in(f'{header.get("In-Reply-To", "")} {header.get("References", "")}')):
            return True
        return any(a in by_email for a in addresses(header.get("To"), header.get("Cc")))

    used = {(lead.get("SenderAccount") or "").strip() for lead in leads} & set(accounts)
    since_dt = now - timedelta(hours=lookback_hours)
    candidates = []
    for account_name in sorted(used or set(accounts)):
        account = accounts[account_name]
        try:
            host, port, username = imap_connection_settings(account)
            fetched = fetch_sent(account["address"], get_imap_password(account), since_dt, accept,
                                 imap_host=host, imap_port=port, imap_username=username)
        except Exception as exc:  # noqa: BLE001 - one mailbox being unreachable must not stop the others
            result["account_errors"].append(f"{account_name}: {classify_imap_exception(exc)} — {exc}")
            continue
        candidates.extend((account_name, message) for message in fetched)
    candidates.sort(key=lambda pair: pair[1].get("date") or datetime.min)

    for account_name, message in candidates:
        message_id = message.get("message_id") or ""
        if not message_id or message_id in skip:
            continue
        lead_id = next((owner_of_id[r] for r in _message_ids_in(f'{message.get("in_reply_to", "")} {message.get("references", "")}')
                        if r in owner_of_id), None)
        if lead_id is None:
            headers = message.get("headers", {})
            for address in addresses(headers.get("to"), headers.get("cc")):
                lead = by_email.get(address)
                subject = (lead.get("ThreadSubject") or "").strip() if lead else ""
                if subject and _is_reply_subject_match(message.get("subject", ""), subject):
                    lead_id = str(lead.get("LeadID", ""))
                    break
        if lead_id is None:
            continue
        headers = message.get("headers", {})
        result["rows"].append({
            "MessageID": message_id, "LeadID": lead_id,
            "SentAt": (message.get("date") or now).strftime(DATETIME_FMT), "SenderAccount": account_name,
            "To": str(headers.get("to", "")), "Cc": str(headers.get("cc", "")), "Subject": message.get("subject", ""),
            "Body": (message.get("body") or "")[:49000], "InReplyTo": message.get("in_reply_to", ""),
            "Source": SENT_SOURCE_MAILBOX,
        })
        skip.add(message_id)
        claim([message_id], lead_id)                    # so the NEXT message in this chain is recognised by this one
    return result


def sync_sent_mail(sheets, accounts: Dict[str, Dict[str, str]], lookback_hours: int, fetch_sent=None,
                   now: Optional[datetime] = None) -> Dict:
    """find_sent_mail_to_log, then store what it found."""
    found = find_sent_mail_to_log(sheets, accounts, lookback_hours, fetch_sent=fetch_sent, now=now)
    if found["rows"]:
        sheets.append_sent_mail_rows(found["rows"])
    return found


def _sync_sent_mail_quietly(sheets, accounts, lookback_hours: int, campaign_name: str) -> None:
    """Runs after every reply check. NEVER lets a problem here touch the reply check itself: any
    failure is printed and swallowed. Prints counts only — this repository's logs are public."""
    if not getattr(sheets, "sent_mail_enabled", False):
        return
    try:
        found = sync_sent_mail(sheets, accounts, max(lookback_hours, 24))
        if found["rows"]:
            print(f"{campaign_name}: stored {len(found['rows'])} message(s) you sent yourself.")
        for error in found["account_errors"]:
            print(f"{campaign_name}: could not read a Sent folder ({error}) — replies were not affected.")
    except Exception as exc:  # noqa: BLE001 - see the docstring
        print(f"{campaign_name}: reading Sent folders failed ({exc}) — replies were not affected.")


def classify_imap_exception(exc: Exception) -> str:
    text = str(exc).lower()
    if isinstance(exc, imaplib.IMAP4.error) or "authenticationfailed" in text or "invalid credentials" in text \
            or "login failed" in text:
        return ERR_AUTH_FAILURE
    return ERR_REPLY_CHECK


def check_single_account_health(address: str, app_password: str, imap_host: str = DEFAULT_IMAP_HOST,
                                 imap_port: int = DEFAULT_IMAP_PORT,
                                 imap_username: Optional[str] = None) -> Tuple[str, str]:
    """Cheap connectivity check — logs in via IMAP and immediately logs
    out, never fetches or sends anything. Returns (status, detail):
    status is 'Connected' or 'Disconnected'; detail is a short,
    password-free reason on failure, empty on success. The password
    itself never appears in the return value — only ever used locally,
    in-process, for this one login attempt. imap_host/imap_port/
    imap_username default to Gmail's own values — see
    imap_connection_settings, which check_account_health builds these
    from per-account."""
    login_username = imap_username or address
    imap = imaplib.IMAP4_SSL(imap_host, imap_port)
    try:
        imap.login(login_username, app_password)
        return "Connected", ""
    except Exception as exc:  # noqa: BLE001 - any failure here means "can't currently connect", full stop
        return "Disconnected", str(exc)[:200]
    finally:
        try:
            imap.logout()
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass


def check_account_health(accounts: Dict[str, Dict[str, str]]) -> List[Dict]:
    """Checks every configured account's connectivity, one IMAP login
    attempt each, isolated so one broken account can't block the rest.
    Never includes app_password anywhere in the returned rows — only
    AccountName, Address, Status, Detail, CheckedAt, matching
    ACCOUNT_HEALTH_COLUMNS exactly, since this is written straight to a
    Sheet Streamlit reads."""
    now_str = datetime.now().strftime(DATETIME_FMT)
    results = []
    for name, account in accounts.items():
        imap_host, imap_port, imap_username = imap_connection_settings(account)
        status, detail = check_single_account_health(account["address"], get_imap_password(account),
                                                       imap_host=imap_host, imap_port=imap_port,
                                                       imap_username=imap_username)
        results.append({
            "AccountName": name, "Address": account["address"], "Status": status,
            "Detail": detail, "CheckedAt": now_str,
        })
    return results


def write_account_health(sheet_id: str, results: List[Dict]) -> None:
    """Overwrites the whole Email Accounts Health tab's data rows every
    run — this is a current-status SNAPSHOT, not an accumulating log, so
    a since-removed account's stale row disappears on the next check
    rather than lingering forever."""
    client, gspread_module = _build_gspread_client()
    spreadsheet = _open_spreadsheet_with_retries(client, sheet_id)
    ws = _get_or_create_ws(spreadsheet, gspread_module, ACCOUNT_HEALTH_TAB, ACCOUNT_HEALTH_COLUMNS)
    existing_row_count = len(ws.get_all_values())
    if existing_row_count > 1:
        ws.batch_clear([f"A2:E{existing_row_count}"])
    rows = [[r["AccountName"], r["Address"], r["Status"], r["Detail"], r["CheckedAt"]] for r in results]
    if rows:
        ws.append_rows(rows)


# =============================================================================
# SECTION 11: Batch building + sending
# =============================================================================

def make_batch_id() -> str:
    return f"BATCH-{datetime.now().strftime('%Y%m%d-%H%M%S')}"


def _compute_next_eligible_at(stages: List[Dict], idx: int, sent_at: datetime,
                               timezone_name: Optional[str] = None) -> str:
    """The calendar DATE (YYYY-MM-DD, campaign timezone) from which the next
    stage is due — the same rule get_eligible_leads applies. Informational
    only: nothing reads this column to decide anything."""
    if idx + 1 >= len(stages):
        return ""
    next_wait_days = stages[idx + 1].get("wait_days_after_previous", 0)
    due = _local_calendar_date(sent_at, timezone_name) + timedelta(days=next_wait_days)
    return due.strftime("%Y-%m-%d")


def _count_sent_today(leads: List[Dict], stages: List[Dict]) -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    count = 0
    for lead in leads:
        for i in range(len(stages)):
            field = stage_field_names(i)["sent_at"]
            if lead.get(field, "").startswith(today):
                count += 1
    return count


def _count_sent_today_by_account(send_log: List[Dict]) -> Dict[str, int]:
    """Per-account send counts for today, sourced from SendLog (the only
    place that records which account each individual send used, with a
    timestamp — Master only keeps the most recent account per lead)."""
    today = datetime.now().strftime("%Y-%m-%d")
    counts: Dict[str, int] = {}
    for row in send_log:
        if row.get("Status") == "sent" and str(row.get("Timestamp", "")).startswith(today):
            acct = row.get("SenderAccount", "")
            if acct:
                counts[acct] = counts.get(acct, 0) + 1
    return counts


def backfill_thread_subjects(campaign_cfg: Dict, leads: List[Dict]) -> List[Dict]:
    """One-time migration helper: for every lead whose ThreadSubject is
    blank, reconstructs it by re-rendering the subject of the most
    recently sent stage that actually HAD a non-blank subject — walking
    backward through sent stages (last to first) and skipping over any
    that themselves used the blank-means-continue convention, since
    there's nothing to extract from those. This handles both:

    - Leads sent entirely before ThreadSubject existed (every stage had a
      real subject — the very first one checked always works).
    - Leads with a MIX of old real-subject stages and newer
      blank-subject-continuation stages (e.g. Intro had a real subject,
      but a later followup already used a blank Subject to continue it) —
      those newer blank stages are skipped over rather than causing the
      whole lookup to give up, since the real subject is still recoverable
      from the stage before them.

    Never overwrites an already-set ThreadSubject, and never writes
    anything itself — returns a plan the caller writes back (same
    "compute first, act separately" shape as build_batch), so this is
    naturally safe to dry-run and to re-run repeatedly.

    IMPORTANT CAVEAT: this assumes the stage it ultimately finds hasn't
    had its template Subject edited since it was actually sent. If you've
    since changed that wording, the backfilled value will be wrong for
    leads sent under the old one. There's no historical record of the
    exact subject actually sent (SendLog doesn't store it) — re-rendering
    the current template is a practical best effort, not a guarantee. For
    leads where getting this exactly right matters, set ThreadSubject
    manually from your actual sent mail instead — carefully: a
    copy-pasted subject from the WRONG lead/campaign will "work" (no
    error) but silently produce a mis-threaded reply, since nothing here
    can verify a manually-entered value against what was really sent.
    """
    stages = campaign_cfg["stages"]
    results = []
    for lead in leads:
        lead_id = lead.get("LeadID", "")
        if (lead.get("ThreadSubject") or "").strip():
            results.append({"lead_id": lead_id, "status": "skipped_already_set"})
            continue

        sent_indices = [i for i in range(len(stages) - 1, -1, -1)
                         if (lead.get(stage_field_names(i)["sent_at"]) or "").strip()]

        if not sent_indices:
            results.append({"lead_id": lead_id, "status": "skipped_not_sent_yet"})
            continue

        found = None
        skipped_blank_stages = []
        last_error = None
        last_error_stage = None
        for idx in sent_indices:
            fields = stage_field_names(idx)
            variant = (lead.get(fields["variant"]) or "").strip()
            stage_label = stages[idx]["name"]
            if not variant:
                continue  # try an earlier stage rather than giving up entirely
            try:
                tmpl = load_template(campaign_cfg["templates_dir"], stages[idx]["template_prefix"], variant)
                subject = render_text(tmpl["subject"], lead)
            except Exception as exc:  # noqa: BLE001 - isolate per-lead template issues, keep trying earlier stages
                last_error, last_error_stage = str(exc), stage_label
                continue
            if not subject.strip():
                skipped_blank_stages.append(stage_label)  # this one also used blank-continuation — try earlier
                continue
            found = {"subject": subject, "stage": stage_label, "row": lead["_row"]}
            break

        if found is not None:
            results.append({"lead_id": lead_id, "status": "backfilled", "thread_subject": found["subject"],
                             "row": found["row"], "stage": found["stage"]})
        elif last_error is not None:
            results.append({"lead_id": lead_id, "status": "error", "stage": last_error_stage, "error": last_error})
        elif skipped_blank_stages:
            results.append({"lead_id": lead_id, "status": "skipped_template_now_blank",
                             "stage": skipped_blank_stages[0]})
        else:
            results.append({"lead_id": lead_id, "status": "skipped_unknown_variant",
                             "stage": stages[sent_indices[0]]["name"]})
    return results


def import_leads(sheets: SheetsConnector, campaign_name: str, new_leads: List[Dict[str, str]],
                  allow_duplicate_emails: bool = False) -> Dict[str, int]:
    """Appends new_leads as new Master Sheet rows. Skips any row with no
    Email (mandatory everywhere else in this system) and, by default,
    any row whose Email already exists among current leads
    (case-insensitive) — the same "first row wins" assumption
    find_duplicate_email_leads already enforces elsewhere, just applied
    at import time instead of left for that check to flag later.

    allow_duplicate_emails=True deliberately turns that second check
    off — for a real, recurring case: contacting the same creator again
    for a genuinely different video (its own Video File / Refunnel Link
    / etc.), tracked as its own separate Asana task, without the
    automated sender ever emailing that person a second time. This is
    safe to allow because sending itself already has its own,
    independent protection: find_duplicate_email_leads / get_eligible_
    leads only ever consider the FIRST (lowest row number) lead for a
    given email eligible to actually be emailed — every later row
    sharing that email is automatically excluded from sending, always,
    regardless of this flag. Asana sync has no such restriction at
    all — it operates strictly per ROW (via each row's own
    AsanaTaskGID), so a second row for the same email correctly gets
    its own separate task.

    Every imported lead's Approval is left BLANK (Pending) unless the
    caller explicitly set it in that row's dict — a bulk import should
    never default new leads straight to "Yes" and eligible to send; a
    human needs to approve them first, same as any manually-added row.
    """
    existing = sheets.get_all_leads()
    existing_ids = [int(l["LeadID"]) for l in existing if str(l.get("LeadID", "")).strip().isdigit()]
    next_id = (max(existing_ids) + 1) if existing_ids else 1
    existing_emails = {(l.get("Email") or "").strip().lower() for l in existing if (l.get("Email") or "").strip()}

    # Widen the header ONCE, before appending anything — otherwise a
    # brand-new custom field name (never seen before on this campaign's
    # Sheet) gets silently dropped by append_lead, which only fills in
    # columns the header already has. Every incoming field name across
    # every lead is considered, not just the first row's, in case
    # different rows map different custom columns.
    all_field_names = set()
    for lead in new_leads:
        all_field_names.update(lead.keys())
    sheets.ensure_master_header_includes(sorted(all_field_names))

    imported = 0
    skipped_duplicate = 0
    skipped_no_email = 0
    for lead in new_leads:
        email = (lead.get("Email") or "").strip()
        if not email:
            skipped_no_email += 1
            continue
        if not allow_duplicate_emails and email.lower() in existing_emails:
            skipped_duplicate += 1
            continue
        row = dict(lead)
        row["LeadID"] = str(next_id)
        row["Campaign"] = campaign_name
        row.setdefault("Approval", "")
        sheets.append_lead(row)
        existing_emails.add(email.lower())
        next_id += 1
        imported += 1

    return {"imported": imported, "skipped_duplicate": skipped_duplicate, "skipped_no_email": skipped_no_email}


def remove_leads(sheets: SheetsConnector, lead_ids: List[str]) -> Dict[str, int]:
    """Soft-remove: sets Status=Removed for every matching LeadID — NEVER
    a hard delete. The row and everything already sent to that lead stays
    exactly as it was; get_eligible_leads already excludes any
    TERMINAL_STATUSES status, which Removed is now part of, so a removed
    lead simply stops being picked up for anything further."""
    existing = sheets.get_all_leads()
    lead_id_set = {str(lid) for lid in lead_ids}
    row_updates = {l["_row"]: STATUS_REMOVED for l in existing if str(l.get("LeadID", "")) in lead_id_set}
    sheets.update_lead_statuses(row_updates)
    found_ids = {str(l.get("LeadID", "")) for l in existing if l["_row"] in row_updates}
    return {"removed": len(row_updates), "not_found": len(lead_id_set - found_ids)}


def build_batch(campaign_cfg: Dict, leads: List[Dict], stage_name: str, batch_size: int,
                 forced_variant: Optional[str] = None, ignore_wait_days: bool = False, suppressed_pairs: Optional[set] = None) -> List[Dict]:
    """Computes eligible leads + assigns variants + renders emails WITHOUT
    sending or writing anything. Safe to call repeatedly for preview.

    ignore_wait_days=True skips ONLY the wait_days_after_previous timing
    check for stages after the first — every other eligibility rule still
    applies unchanged: Approval must be Yes, the lead can't be in a
    terminal/replied state, this stage can't already be sent, and the
    PREVIOUS stage must actually have been sent (stage order is never
    skippable, only the wait between stages is overridable). This is the
    "send this follow-up now regardless of schedule" override.
    """
    stages = campaign_cfg["stages"]
    variants = campaign_cfg["variants"]
    idx = _stage_index(stages, stage_name)
    fields = stage_field_names(idx)

    if forced_variant is not None and forced_variant not in variants:
        raise ValueError(f"Variant '{forced_variant}' is not in campaign variants: {variants}")

    eligible = get_eligible_leads(leads, stages, idx, ignore_wait_days=ignore_wait_days,
                                   timezone_name=_campaign_timezone_name(campaign_cfg))
    if suppressed_pairs and campaign_cfg.get("format") == FORMAT_HTML:
        # An address that has opted out of this campaign's brand is never emailed (removed BEFORE the batch is cut to size, so it never takes a place from someone who can be sent).
        brand = campaign_brand_key(campaign_cfg)
        eligible = [lead for lead in eligible if not ul.is_suppressed(lead.get("Email", ""), brand, suppressed_pairs)]
    eligible = eligible[:batch_size]

    batch_counts = {v: 0 for v in variants}
    plan = []
    for lead in eligible:
        if forced_variant is not None:
            variant = forced_variant
        else:
            variant = pick_variant(leads, fields["variant"], variants, batch_counts)
            batch_counts[variant] += 1
        is_html = campaign_cfg.get("format") == FORMAT_HTML
        # An HTML campaign's .txt file holds only the Subject line: its plain-text half is made from the rendered design (below), never written by hand.
        rendered = render_email(campaign_cfg["templates_dir"], stages[idx]["template_prefix"], variant, lead,
                                 is_first_stage=(idx == 0), ignore_body=is_html)
        html_body = None
        unsubscribe_url = None
        if is_html:
            html_result = render_email_html(campaign_cfg["templates_dir"], stages[idx]["template_prefix"], variant, lead, campaign_cfg)
            html_body = html_result["html"]
            unsubscribe_url = html_result.get("unsubscribe_url")
            rendered["body"] = hc.html_to_text(html_body)
            rendered["missing_variables"] = list(dict.fromkeys(list(rendered["missing_variables"]) + html_result["missing_variables"]))

        prior_message_id = lead.get("MessageID", "") if idx > 0 else ""
        prior_references = lead.get("ThreadReferences", "") if idx > 0 else ""
        in_reply_to = prior_message_id or None
        if prior_message_id:
            references = f"{prior_references} {prior_message_id}".strip()
        else:
            references = prior_references or None

        plan.append({
            "lead": lead, "variant": variant,
            "subject": rendered["subject"], "body": rendered["body"], "html_body": html_body, "unsubscribe_url": unsubscribe_url,
            "missing_variables": rendered["missing_variables"],
            "in_reply_to": in_reply_to, "references": references,
            "thread_subject": rendered["thread_subject"], "is_continuation": rendered["is_continuation"],
        })
    return plan


def _resolve_account_for_round(lead: Dict, campaign_cfg: Dict, accounts: Dict[str, Dict[str, str]],
                                confirmed_batch_counts: Dict[str, int], sent_today_by_account: Dict[str, int],
                                used_accounts_this_round: set) -> Tuple[str, object]:
    """Resolution used when building one CONCURRENT sending round (see
    send_batch). Returns one of:

      ("account", account_name) — ready to send this round.
      ("defer", None)           — this lead's account is already busy this
                                   round (e.g. two leads both pinned to the
                                   same SenderAccount, or every rotation
                                   account is already taken this round).
                                   Not an error — retried in the next round.
      ("error", exception)      — permanently unsendable this run (unknown
                                   account, or genuinely out of capacity
                                   across every eligible account).

    Capacity itself (sending.per_account_daily_limit) is always checked
    against confirmed_batch_counts — successes actually confirmed from
    completed rounds so far — never against same-round reservations, so a
    lead is never falsely told "at capacity" just because another lead
    happens to be using that account in the same round.
    """
    per_account_limit = campaign_cfg.get("sending", {}).get("per_account_daily_limit")
    requested = (lead.get("SenderAccount") or "").strip()
    sending_cfg = campaign_cfg.get("sending", {})

    # A lead's SenderAccount is normally "locked in" on first send so every
    # follow-up in a thread comes from the same address. But that pin is
    # only meaningful while the account is still actually part of THIS
    # campaign's sender configuration. The real, reported bug this fixes: a
    # campaign whose sender accounts were changed (old account removed, new
    # ones added) kept sending follow-ups from the REMOVED account forever,
    # because the pin was validated only against the global
    # EMAIL_ACCOUNTS_JSON — where the old account still exists — and never
    # against the campaign's own current rotation_accounts list. Removing
    # an account from a campaign silently did nothing for every lead
    # already contacted. Treating the pin as stale here lets rotation
    # reassign the lead to a currently-configured account instead; an
    # explicit config change is a deliberate decision and outranks a pin
    # that was only ever an automatic side effect of an earlier send.
    # Normalize the pinned name to its canonical EMAIL_ACCOUNTS_JSON key
    # BEFORE any membership test. A lead row saying "sales2" and a secret
    # keyed "Sales2" are the same account; without this the stale-pin
    # check below would see a case variant as "no longer in the rotation"
    # and SILENTLY reassign the lead to a different sender mid-thread,
    # which is worse than the loud error it used to produce.
    if requested:
        canonical_requested = canonical_account_name(requested, accounts)
        if canonical_requested is None:
            return ("error", MissingSenderAccountError(
                f"Unknown SenderAccount '{requested}' — not in EMAIL_ACCOUNTS_JSON."))
        requested = canonical_requested

    if requested:
        # Generalized — this check must run regardless of whether rotation
        # is currently on or off. See current_valid_senders_for_campaign's
        # own docstring for the exact reported bug this closes: turning
        # rotation off (the natural move when reduced to one account) used
        # to silently disable this staleness check entirely.
        current_valid = current_valid_senders_for_campaign(campaign_cfg, accounts)
        if current_valid and requested not in current_valid:
            requested = ""

    if requested:
        try:
            _check_account_capacity(requested, per_account_limit, sent_today_by_account, confirmed_batch_counts)
        except SenderCapacityReachedError as exc:
            return ("error", exc)
        if requested in used_accounts_this_round:
            return ("defer", None)
        return ("account", requested)

    # Triggers whenever EITHER sender_rotation is on, OR an explicit
    # rotation_accounts list exists — matching
    # current_valid_senders_for_campaign's own condition exactly. This
    # closes the second half of the reported bug: with rotation TOGGLED
    # OFF but rotation_accounts still holding the campaign's real,
    # narrowed-down account list (e.g. ["sales1"] after removing 4 of 5
    # accounts), that list is still the deliberate, current
    # configuration — routing it through this same, already-correct
    # rotation-pool logic (which handles a pool of exactly one account
    # perfectly normally) is what actually resolves it, rather than
    # falling through to single-default resolution, which only ever
    # knows about default_sender_account / the global default and has
    # never heard of rotation_accounts at all.
    if sending_cfg.get("sender_rotation") or sending_cfg.get("rotation_accounts"):
        rotation_accounts = get_rotation_accounts(campaign_cfg, accounts)
        if not rotation_accounts:
            return ("error", MissingSenderAccountError(
                "sender_rotation is enabled but no valid accounts are configured "
                "(check sending.rotation_accounts and EMAIL_ACCOUNTS_JSON)."))

        def has_capacity(acct: str) -> bool:
            if per_account_limit is None:
                return True
            used = sent_today_by_account.get(acct, 0) + confirmed_batch_counts.get(acct, 0)
            return used < per_account_limit

        capacity_ok = [a for a in rotation_accounts if has_capacity(a)]
        if not capacity_ok:
            return ("error", SenderCapacityReachedError(
                f"All rotation accounts are at their per-account daily limit ({per_account_limit})."))

        # Prefer an account not already claimed by another lead THIS round —
        # this is what makes concurrent sending safe: every job fired at the
        # same time in one round uses a different account.
        available_now = [a for a in capacity_ok if a not in used_accounts_this_round]
        if not available_now:
            return ("defer", None)  # every currently-usable account is busy this round only

        chosen = pick_rotation_account(available_now, per_account_limit, sent_today_by_account,
                                        confirmed_batch_counts)
        return ("account", chosen)

    # No manual pin, no rotation — the single campaign/global default
    # account. Deliberately resolves against an UNPINNED lead ({}), not
    # the original `lead` — the actual bug this closes: `requested` above
    # gets cleared to "" once a pin is judged stale, but the original
    # `lead` dict still carries its own stale SenderAccount value.
    # resolve_sender_account(lead, ...) would re-derive "requested" from
    # that same stale field internally and hand back the very account
    # this whole function just decided to move away from — silently
    # undoing the staleness check one branch later. This is what let a
    # pin to a removed account survive every fix up to this one.
    try:
        chosen = resolve_sender_account({}, campaign_cfg, accounts)
        _check_account_capacity(chosen, per_account_limit, sent_today_by_account, confirmed_batch_counts)
    except (MissingSenderAccountError, SenderCapacityReachedError) as exc:
        return ("error", exc)
    if chosen in used_accounts_this_round:
        return ("defer", None)
    return ("account", chosen)


def _record_send_failure(sheets: "SheetsConnector", campaign_name: str, batch_id: str, stage_name: str,
                          item: Dict, exc: Exception, account_name: str = "") -> Dict:
    lead = item["lead"]
    row = lead["_row"]
    lead_id = lead.get("LeadID", "")
    lead_email = lead.get("Email", "")
    now_str = datetime.now().strftime(DATETIME_FMT)
    error_type = classify_send_exception(exc)
    is_skip = isinstance(exc, SenderCapacityReachedError)
    outcome_status = "skipped" if is_skip else "error"
    try:
        sheets.update_lead_fields(row, {"Error": str(exc)[:500]})
        sheets.append_send_log({
            "BatchID": batch_id, "Timestamp": now_str, "LeadID": lead_id, "Email": lead_email,
            "Campaign": campaign_name, "Stage": stage_name, "Variant": item["variant"],
            "SenderAccount": account_name, "Status": outcome_status, "MessageID": "",
            "Error": str(exc)[:500],
        })
    except Exception:  # noqa: BLE001 - the error log entry below is the durable record either way
        pass
    log_error(sheets, campaign_name, error_type, str(exc), lead_id=lead_id, email_addr=lead_email,
              stage=stage_name, batch_id=batch_id)
    return {"lead_id": lead_id, "email": lead_email, "status": outcome_status,
            "error": str(exc), "error_type": error_type, "batch_id": batch_id}


def _record_send_success(sheets: "SheetsConnector", campaign_name: str, batch_id: str, stage_name: str,
                          fields: Dict[str, str], idx: int, stages: List[Dict], item: Dict,
                          account_name: str, sent: Dict[str, str],
                          timezone_name: Optional[str] = None) -> Dict:
    lead = item["lead"]
    row = lead["_row"]
    lead_id = lead.get("LeadID", "")
    lead_email = lead.get("Email", "")
    now = datetime.now()
    now_str = now.strftime(DATETIME_FMT)
    try:
        sheets.update_lead_fields(row, {
            fields["sent_at"]: now_str,
            fields["variant"]: item["variant"],
            "CurrentStage": stage_name,
            "NextEligibleAt": _compute_next_eligible_at(stages, idx, now, timezone_name),
            "Status": f"{stage_name} Sent",
            "LastActionAt": now_str,
            "MessageID": sent["message_id"],
            "ThreadReferences": item["references"] or "",
            "ThreadSubject": item["thread_subject"],
            "SenderAccount": account_name,
            "Error": "",
        })
        sheets.append_send_log({
            "BatchID": batch_id, "Timestamp": now_str, "LeadID": lead_id, "Email": lead_email,
            "Campaign": campaign_name, "Stage": stage_name, "Variant": item["variant"],
            "SenderAccount": account_name, "Status": "sent", "MessageID": sent["message_id"], "Error": "",
        })
        for var_name in item["missing_variables"]:
            log_error(sheets, campaign_name, ERR_MISSING_VARIABLE,
                      f"Template variable '{{{{{var_name}}}}}' has no matching Master column — "
                      "rendered blank. Check for a typo, or add that column.",
                      lead_id=lead_id, email_addr=lead_email, stage=stage_name, batch_id=batch_id)
        return {"lead_id": lead_id, "email": lead_email, "status": "sent",
                "variant": item["variant"], "batch_id": batch_id, "account": account_name}
    except Exception as sheets_exc:  # noqa: BLE001
        # The send genuinely happened, so this account WAS used, and the
        # caller still needs to count it against confirmed_batch_counts —
        # flagged distinctly so it's not confused with an ordinary send
        # failure (the email is already in the recipient's inbox).
        log_error(sheets, campaign_name, ERR_SHEETS_API,
                  f"Email sent successfully (Message-ID {sent['message_id']}) but failed to update "
                  f"the sheet: {sheets_exc}. Check manually to avoid a duplicate resend.",
                  lead_id=lead_id, email_addr=lead_email, stage=stage_name, batch_id=batch_id)
        return {"lead_id": lead_id, "email": lead_email, "status": "sent_but_sheet_error",
                "error": str(sheets_exc), "batch_id": batch_id, "account": account_name}


def send_batch(campaign_cfg: Dict, sheets: SheetsConnector, accounts: Dict[str, Dict[str, str]],
               stage_name: str, batch_size: int, forced_variant: Optional[str] = None,
               ignore_wait_days: bool = False) -> List[Dict]:
    """Sends the batch in CONCURRENT ROUNDS instead of one email at a time.

    Each round is built greedily so that no two jobs in it share a sender
    account — meaning every job in a round can genuinely fire at the same
    moment (real threads, real simultaneous SMTP connections), one send per
    account per round. A lead whose resolved account is already taken by
    another lead in the current round isn't an error — it's simply carried
    over to the next round. With N distinct sender accounts, this produces
    exactly the "N accounts to N leads at once, then wait, then the next N"
    behavior. With a single account (or nothing but manually-pinned leads
    all sharing one account), every round naturally has exactly one job —
    identical in effect to the old fully-sequential behavior, so nothing
    changes for single-account setups.

    sending.delay_min_minutes / delay_max_minutes are the wait BETWEEN
    ROUNDS now, not between individual emails — the pacing that used to
    apply per-send now applies per-account (each account only sends its
    next email one round-delay later).

    Every attempt (sent, error, or skipped) is logged to SendLog under one
    BatchID, and every error is also classified and logged to the Error
    Log. Failures are isolated per lead and never abort the batch. A lead
    that can never be sent this run (unknown account, genuinely at
    capacity) is recorded immediately, without waiting for a round.

    ignore_wait_days=True overrides ONLY the scheduled wait between stages
    (e.g. send followup1 today even though it's not due for 3 more days) —
    see build_batch's docstring for exactly what is and isn't skipped.

    Raises CampaignPausedError immediately, before touching anything, if
    campaign_cfg["status"] in ("paused", "draft"), and OutsideSendingWindowError
    if campaign_cfg["schedule"] restricts sending to specific days/hours
    and right now doesn't qualify — Preview/build_batch are deliberately
    NOT gated by either, so a paused, still-draft, or schedule-restricted
    campaign can still be reviewed any time, just not sent.
    """
    status = campaign_cfg.get("status") or "active"
    if status == "deleted":
        raise CampaignPausedError(
            f"Campaign '{campaign_cfg.get('_campaign_name', '')}' has been (temporarily) deleted — "
            "no batch will be sent. Restore it first if this was unintentional."
        )
    if status == "paused":
        raise CampaignPausedError(
            f"Campaign '{campaign_cfg.get('_campaign_name', '')}' is paused — no batch will be sent. "
            "Resume it (set status back to 'active') first if this was unintentional."
        )
    if status == "draft":
        raise CampaignPausedError(
            f"Campaign '{campaign_cfg.get('_campaign_name', '')}' is still a draft and hasn't been "
            "launched — no batch will be sent. Launch it first if this was unintentional."
        )

    within_window, window_reason = is_within_sending_window(campaign_cfg.get("schedule") or {})
    if not within_window:
        raise OutsideSendingWindowError(
            f"Campaign '{campaign_cfg.get('_campaign_name', '')}' is outside its configured sending "
            f"window — no batch will be sent. {window_reason}."
        )

    stages = campaign_cfg["stages"]
    sending_cfg = campaign_cfg["sending"]
    daily_limit = sending_cfg["daily_limit"]
    delay_min = sending_cfg["delay_min_minutes"]
    delay_max = sending_cfg["delay_max_minutes"]
    campaign_name = campaign_cfg.get("_campaign_name", "")

    leads = sheets.get_all_leads()
    already_today = _count_sent_today(leads, stages)
    remaining_today = max(daily_limit - already_today, 0)
    effective_batch_size = min(batch_size, remaining_today)

    if effective_batch_size <= 0:
        return []

    suppressed = None
    if campaign_cfg.get("format") == FORMAT_HTML:
        # HTML campaigns only: read the shared Unsubscribes list ONCE for this run (raises if it cannot be read), show who has left in their own tab, and never email them.
        suppressed = load_unsubscribe_pairs(sheets)
        publish_campaign_code(campaign_cfg, sheets)
        reconcile_unsubscribes(campaign_cfg, sheets, leads, suppressed)

    plan = build_batch(campaign_cfg, leads, stage_name, effective_batch_size, forced_variant=forced_variant,
                        ignore_wait_days=ignore_wait_days, suppressed_pairs=suppressed)
    if not plan:
        return []

    idx = _stage_index(stages, stage_name)
    fields = stage_field_names(idx)
    batch_id = make_batch_id()

    send_log = sheets.get_all_send_log()
    sent_today_by_account = _count_sent_today_by_account(send_log)
    confirmed_batch_counts: Dict[str, int] = {}  # confirmed successes from ROUNDS ALREADY COMPLETED this run

    results: List[Optional[Dict]] = [None] * len(plan)
    pending: List[Tuple[int, Dict]] = list(enumerate(plan))

    while pending:
        round_jobs: List[Tuple[int, Dict, str]] = []
        used_accounts_this_round: set = set()
        still_pending: List[Tuple[int, Dict]] = []

        for orig_idx, item in pending:
            lead = item["lead"]
            lead_email = lead.get("Email", "")

            if not is_valid_email_format(lead_email):
                exc = InvalidEmailFormatError(f"'{lead_email}' is not a valid email address format")
                results[orig_idx] = _record_send_failure(sheets, campaign_name, batch_id, stage_name, item, exc)
                continue

            status, value = _resolve_account_for_round(lead, campaign_cfg, accounts, confirmed_batch_counts,
                                                         sent_today_by_account, used_accounts_this_round)
            if status == "error":
                results[orig_idx] = _record_send_failure(sheets, campaign_name, batch_id, stage_name, item, value)
                continue
            if status == "defer":
                still_pending.append((orig_idx, item))
                continue

            account_name = value
            used_accounts_this_round.add(account_name)
            round_jobs.append((orig_idx, item, account_name))

        if round_jobs:
            # Fire every job in this round AT THE SAME TIME — real threads,
            # real concurrent SMTP connections, one per account. Sheets
            # writes are deliberately NOT done from these threads (gspread's
            # Worksheet object isn't meant to be hit concurrently) — only
            # the network send itself is parallelized.
            sent_outcomes: Dict[int, Tuple[str, object]] = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(round_jobs)) as executor:
                future_to_job = {}
                for orig_idx, job_item, acct in round_jobs:
                    smtp_host, smtp_port, smtp_username = smtp_connection_settings(accounts[acct])
                    # Only an HTML campaign's email carries html_body: a plain-text email is sent with exactly the arguments it always was.
                    html_extra = {"html_body": job_item["html_body"]} if job_item.get("html_body") is not None else {}
                    if html_extra and job_item.get("unsubscribe_url"):
                        html_extra["unsubscribe_url"] = job_item["unsubscribe_url"]
                    future = executor.submit(
                        smtp_send, accounts[acct]["address"], accounts[acct]["app_password"],
                        to=job_item["lead"].get("Email", ""), subject=job_item["subject"],
                        body_text=job_item["body"], in_reply_to=job_item["in_reply_to"],
                        references=job_item["references"],
                        smtp_host=smtp_host, smtp_port=smtp_port, smtp_username=smtp_username,
                        **html_extra,
                    )
                    future_to_job[future] = orig_idx
                for future in concurrent.futures.as_completed(future_to_job):
                    orig_idx = future_to_job[future]
                    try:
                        sent_outcomes[orig_idx] = ("ok", future.result())
                    except Exception as exc:  # noqa: BLE001 - isolate per-lead SMTP failures within the round
                        sent_outcomes[orig_idx] = ("error", exc)

            # Persist results sequentially, back on the main thread, in the
            # round's original order.
            for orig_idx, job_item, acct in round_jobs:
                outcome, payload = sent_outcomes[orig_idx]
                if outcome == "ok":
                    result = _record_send_success(sheets, campaign_name, batch_id, stage_name, fields, idx,
                                                   stages, job_item, acct, payload,
                                                   timezone_name=_campaign_timezone_name(campaign_cfg))
                    if result["status"] in ("sent", "sent_but_sheet_error"):
                        confirmed_batch_counts[acct] = confirmed_batch_counts.get(acct, 0) + 1
                else:
                    result = _record_send_failure(sheets, campaign_name, batch_id, stage_name, job_item,
                                                   payload, account_name=acct)
                results[orig_idx] = result

        pending = still_pending
        if pending:
            time.sleep(random.uniform(delay_min * 60, delay_max * 60))

    return results


# =============================================================================
# SECTION 12: Reply monitor
# =============================================================================

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def is_unsubscribe_request(subject: str) -> bool:
    """Whether a message's subject is an unsubscribe request, the way a mail app's Unsubscribe link writes it (the List-Unsubscribe address carries ?subject=unsubscribe), after
    removing any Re:/Fwd: prefixes. Deliberately only the subject: a body that merely mentions the word is not treated as a request."""
    text = (subject or "").strip().lower()
    while True:
        stripped = re.sub(r"^(re|fwd?)\s*:\s*", "", text)
        if stripped == text:
            break
        text = stripped
    return text.startswith("unsubscribe") and len(text) <= 40


def _extract_email(from_header: str) -> str:
    match = EMAIL_RE.search(from_header or "")
    return match.group(0).lower() if match else ""


def _normalize_subject_for_match(subject: str) -> str:
    """Normalizes a subject line for comparison: unicode dashes folded to
    a plain hyphen, non-breaking and other exotic whitespace folded to a
    normal space, runs of whitespace collapsed to one, then lowercased
    and stripped.

    This exists because comparing raw subject strings is far too
    brittle in practice: a subject travels from the outbound template,
    into the Sheet's ThreadSubject cell, out through SMTP, through the
    recipient's mail client (which re-encodes it), and back in through
    MIME-decoded IMAP — and routinely comes back with an en-dash
    turned into a hyphen, a normal space turned into a non-breaking
    space, or extra whitespace introduced. Every one of those is the
    same subject to a human and silently not-a-match to ==, which
    meant a genuine reply could fail subject-matching for reasons
    having nothing to do with whether it was genuinely a reply."""
    folded = subject
    for dash in ("\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2015", "\u2212"):
        folded = folded.replace(dash, "-")
    for space in ("\u00a0", "\u2007", "\u202f", "\u200b", "\t", "\r", "\n"):
        folded = folded.replace(space, " ")
    return re.sub(r"\s+", " ", folded).strip().lower()


def _is_reply_subject_match(msg_subject: str, original_subject: str) -> bool:
    """True if msg_subject is a reply to original_subject: one or more
    leading "Re:" prefixes (case-insensitive, each optionally followed
    by whitespace — handles the real-world "Re: Re: <subject>" double-
    accumulation some mail clients produce) followed by the original
    subject line, compared after normalization (see
    _normalize_subject_for_match for why raw == is not usable here).

    This is a genuinely strong secondary signal — not as strong as a
    real Message-ID thread reference, but far stronger than "same
    sender email, unrelated subject" — used specifically to safely
    treat a reply as verified even when its threading headers
    (In-Reply-To / References) were stripped entirely by the sender's
    own mail client. This is a real, confirmed case, not a
    hypothetical: iOS Mail is known to drop these headers on short
    replies. Without this, a reply with an unmistakably matching "Re:
    <original subject>" from the exact lead's own email address
    would be permanently unable to ever stop a sequence, solely
    because of a mail client quirk unrelated to whether the reply
    itself is genuine.

    Returns False if original_subject is blank (an older lead from
    before ThreadSubject was tracked, or one where it was never
    populated) — correctly falling back to the existing sender-only,
    never-stops-a-sequence behavior in that case."""
    if not original_subject or not original_subject.strip():
        return False
    stripped = msg_subject.strip()
    had_reply_prefix = False
    while True:
        without_prefix = re.sub(r"^re\s*:\s*", "", stripped, flags=re.IGNORECASE)
        if without_prefix == stripped:
            break
        stripped = without_prefix
        had_reply_prefix = True
    if not had_reply_prefix:
        return False
    return _normalize_subject_for_match(stripped) == _normalize_subject_for_match(original_subject)


def _most_recent_send_at(lead: Dict) -> Optional[datetime]:
    """Latest send timestamp across every stage for this lead. Used to
    sanity-check whether an inbound message could plausibly be a reply to
    THIS campaign's own outreach — it can't be a reply to something that
    hadn't been sent to this lead yet.

    Derived from CANONICAL_STAGE_ORDER's own length rather than a
    separately hardcoded list of field names — the earlier version
    hardcoded exactly Intro + FollowUp1-4, which silently stopped being
    correct the moment a lead progressed past FollowUp4: for any lead
    further along than that, this would report a stale, too-early
    "most recent send" (the true most recent send lives in a
    FollowUp5+ field this never looked at), undermining the exact
    sanity check this function exists to provide."""
    latest = None
    for index in range(len(CANONICAL_STAGE_ORDER)):
        field = stage_field_names(index)["sent_at"]
        dt = _parse_dt(lead.get(field, ""))
        if dt is not None and (latest is None or dt > latest):
            latest = dt
    return latest


# Reply-matching outcomes. A Header match (inbound In-Reply-To/References
# contains a Message-ID this system actually sent) is the only signal
# trusted enough to stop a live sequence. An Email-only match (same sender
# address, no header link) is real signal worth logging, but NEVER trusted
# on its own to stop anything — the same address can legitimately appear
# across more than one campaign (or a previous, since-replaced campaign),
# and sender-address alone can't tell those apart. See README "Reply
# matching safety" for the full rationale and the production incident that
# prompted this.
ACTION_STOPPED = "Stopped Sequence"
ACTION_UNSUBSCRIBED = "Stopped Sequence (Unsubscribed)"
ACTION_LOGGED_ONLY = "Logged Only"
ACTION_LOGGED_UNVERIFIED = "Logged Only (Unverified Match)"
ACTION_LOGGED_UNRELATED = "Logged Only (Predates Contact)"


OOO_KEYWORDS = [
    "out of office", "automatic reply", "auto-reply", "auto reply",
    "away from my desk", "on leave", "on vacation", "currently unavailable",
    "will be back on", "annual leave",
]
BOUNCE_HARD_KEYWORDS = [
    "undeliverable", "delivery has failed", "delivery failed",
    "address not found", "recipient address rejected",
    "mailbox unavailable", "550", "does not exist",
]
BOUNCE_SOFT_KEYWORDS = [
    "mailbox full", "quota exceeded", "temporarily deferred",
    "try again later", "451", "452",
]
BOUNCE_SENDER_PATTERNS = ["mailer-daemon", "postmaster", "mail delivery subsystem"]


def _status_code_severity(text: str) -> str:
    match = re.search(r"\b([245])\.\d\.\d\b", text)
    if not match:
        return ""
    code = match.group(1)
    if code == "5":
        return CLASSIFICATION_BOUNCE_HARD
    if code == "4":
        return CLASSIFICATION_BOUNCE_SOFT
    return ""


def classify_message(headers: Dict[str, str], subject: str, body: str, from_addr: str) -> str:
    headers = {k.lower(): v for k, v in (headers or {}).items()}
    subject_l = (subject or "").lower()
    body_l = (body or "").lower()
    from_l = (from_addr or "").lower()

    auto_submitted = headers.get("auto-submitted", "").lower()
    if auto_submitted and auto_submitted != "no":
        return CLASSIFICATION_AUTOREPLY
    if "x-autoreply" in headers or "x-autorespond" in headers:
        return CLASSIFICATION_AUTOREPLY
    if headers.get("precedence", "").lower() in ("bulk", "auto_reply", "junk"):
        return CLASSIFICATION_AUTOREPLY

    content_type = headers.get("content-type", "").lower()
    is_bounce_sender = any(p in from_l for p in BOUNCE_SENDER_PATTERNS)
    is_dsn = "report-type=delivery-status" in content_type or "multipart/report" in content_type

    if is_bounce_sender or is_dsn:
        severity = _status_code_severity(body_l) or _status_code_severity(subject_l)
        if severity:
            return severity
        if any(k in body_l or k in subject_l for k in BOUNCE_HARD_KEYWORDS):
            return CLASSIFICATION_BOUNCE_HARD
        if any(k in body_l or k in subject_l for k in BOUNCE_SOFT_KEYWORDS):
            return CLASSIFICATION_BOUNCE_SOFT
        return CLASSIFICATION_BOUNCE_HARD

    if any(k in subject_l or k in body_l for k in OOO_KEYWORDS):
        return CLASSIFICATION_OOO

    return CLASSIFICATION_GENUINE


def classify_reply_intent(body: str, api_key: str) -> Dict[str, str]:
    """Classifies a Genuine Reply's actual sales intent — a SEPARATE call
    from the mechanical classify_message above, only ever made for
    replies that are already known to be genuine (never for a bounce or
    an auto-reply, which have no "intent" to speak of). Returns
    {"intent": one of INTENT_OPTIONS, "confidence": "High"/"Medium"/"Low"}.

    A low-confidence result is downgraded to Unclear rather than trusted
    at face value — an ambiguous reply should read as "needs a human to
    look at it," not risk a wrong business decision (e.g. silently
    treating a real prospect as Not Interested). The same downgrade
    applies to any failure — a network error, a rate limit, a malformed
    response — since a classification hiccup should never block the
    whole check_replies run, and "we don't know" is always the honest,
    safe fallback.
    """
    fallback = {"intent": INTENT_UNCLEAR, "confidence": "Low"}
    if not api_key:
        return fallback

    prompt = (
        "You are classifying a single email reply from a cold outreach recipient into their sales "
        "intent. Categories:\n"
        "- Interested: they want to move forward or learn more now.\n"
        "- Not Interested: a clear decline, no interest at all, right now or in general.\n"
        "- Lead / Needs Follow-up: not a flat no — asking questions, deferring to a later time "
        '(e.g. "not right now, check back in a few months"), or otherwise worth following up on.\n'
        "- Unclear: genuinely ambiguous, or you are not confident.\n\n"
        'Reply with ONLY a JSON object, nothing else: {"intent": "<one of the four category names '
        'above, exactly as written>", "confidence": "High" or "Medium" or "Low"}.\n\n'
        f"Email reply:\n{(body or '')[:3000]}"
    )
    try:
        resp = requests.post(
            ANTHROPIC_API_URL,
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": ANTHROPIC_INTENT_MODEL, "max_tokens": 100,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=20,
        )
        resp.raise_for_status()
        raw_text = resp.json()["content"][0]["text"].strip()
        parsed = json.loads(raw_text)
        intent = parsed.get("intent", "")
        confidence = parsed.get("confidence", "Low")
        if intent not in INTENT_OPTIONS or confidence == "Low":
            return fallback
        return {"intent": intent, "confidence": confidence}
    except Exception:  # noqa: BLE001 - any failure here falls back to the safe "Unclear" default
        return fallback


def check_replies(sheets: SheetsConnector, accounts: Dict[str, Dict[str, str]], lookback_hours: int,
                   campaign_name: str = "", anthropic_api_key: Optional[str] = None,
                   account_errors: Optional[List[str]] = None, handle_unsubscribe: bool = True) -> List[Dict]:
    """handle_unsubscribe (on by default, for every campaign): a message whose subject is an unsubscribe request, sent FROM a lead's own address, stops that lead
    (Status "Stopped - Unsubscribed", which is not counted as a reply). Because every campaign's reply check reads the same mailboxes, one unsubscribe email stops that
    address in EVERY campaign it is in, plain-text or HTML. False turns it off.

    anthropic_api_key: when set, every newly-logged Genuine Reply gets
    a one-time intent classification (see classify_reply_intent) in the
    same run it's first discovered — never re-classified on a later run,
    since a message is only ever logged once at all (message_id dedup,
    below, already guarantees that). When not set, Intent/IntentConfidence/
    IntentClassifiedAt are simply left blank — this whole feature is
    opt-in, not required for check_replies to work exactly as it always
    has.

    account_errors: pass a list here to have it appended to whenever an
    account's IMAP check fails outright — this is what lets a caller
    (see cmd_check_replies) tell "genuinely zero new messages this run"
    apart from "an account was silently never actually checked", which
    otherwise look identical from the returned actions list alone (both
    are just an empty list) despite being very different situations.
    Optional and non-breaking — every existing caller that doesn't pass
    this gets exactly the same behavior as before."""
    leads = sheets.get_all_leads()

    by_message_id = {}
    by_email = {}
    for lead in leads:
        mid = (lead.get("MessageID") or "").strip()
        if mid:
            by_message_id.setdefault(mid, lead)
        email_addr = (lead.get("Email") or "").strip().lower()
        if email_addr:
            by_email.setdefault(email_addr, lead)

    logged_info = sheets.get_logged_message_info()
    since_dt = datetime.now() - timedelta(hours=lookback_hours)

    actions = []
    for account_name, account in accounts.items():
        try:
            imap_host, imap_port, imap_username = imap_connection_settings(account)
            messages = imap_fetch_recent(account["address"], get_imap_password(account), since_dt,
                                          imap_host=imap_host, imap_port=imap_port, imap_username=imap_username)
        except Exception as exc:  # noqa: BLE001 - one account's IMAP outage shouldn't block the rest
            error_type = classify_imap_exception(exc)
            message = f"IMAP check failed for account '{account_name}': {exc}"
            print(f"WARNING: {message}", file=sys.stderr)
            log_error(sheets, campaign_name, error_type, message)
            if account_errors is not None:
                account_errors.append(f"{account_name}: {error_type} — {exc}")
            continue

        for msg in messages:
            previously_logged = logged_info.get(msg["message_id"]) if msg["message_id"] else None
            if previously_logged and previously_logged["match_method"] in ("Header", "Subject"):
                continue  # already definitively resolved — never re-processed

            combined_refs = f'{msg.get("in_reply_to", "")} {msg.get("references", "")}'
            matched_lead = None
            match_method = None
            for mid, lead in by_message_id.items():
                if mid and mid in combined_refs:
                    matched_lead = lead
                    match_method = "Header"
                    break
            if matched_lead is None:
                sender_email = _extract_email(msg["from"])
                matched_lead = by_email.get(sender_email)
                if matched_lead is not None:
                    match_method = "Email"
                    if _is_reply_subject_match(msg["subject"], matched_lead.get("ThreadSubject", "")):
                        match_method = "Subject"
            if matched_lead is None:
                continue

            classification = classify_message(msg["headers"], msg["subject"], msg["body"], msg["from"])
            now_str = datetime.now().strftime(DATETIME_FMT)

            master_updates = {"LastInboundClassification": classification, "LastInboundAt": now_str,
                               "LastActionAt": now_str}

            unsubscribe_request = handle_unsubscribe and match_method == "Email" and is_unsubscribe_request(msg["subject"])
            if unsubscribe_request:
                # The sender is exactly this lead's own address and the subject is an unsubscribe request: safe to act on, and the person has asked us to stop.
                master_updates["Status"] = STATUS_STOPPED_UNSUBSCRIBED
                action_taken = ACTION_UNSUBSCRIBED
            elif match_method in ("Header", "Subject"):
                # Both are strong enough to safely act on. Header is a
                # provable Message-ID thread reference. Subject is a
                # same-sender-email reply whose subject line exactly
                # matches (after stripping "Re:" prefixes) this lead's
                # own original outreach subject — see
                # _is_reply_subject_match's own docstring for the real,
                # confirmed case this covers (a mail client stripping
                # threading headers entirely on an otherwise
                # unmistakably genuine reply).
                action_taken = ACTION_LOGGED_ONLY
                if classification == CLASSIFICATION_GENUINE:
                    master_updates.update({"ReplyStatus": "Replied", "ReplyAt": now_str,
                                            "Status": STATUS_STOPPED_REPLIED})
                    action_taken = ACTION_STOPPED
                elif classification == CLASSIFICATION_BOUNCE_HARD:
                    master_updates["Status"] = STATUS_STOPPED_BOUNCED
                    action_taken = ACTION_STOPPED
            else:
                # Sender-only match, subject doesn't confirm it either —
                # logged for visibility, but NEVER allowed to stop a
                # sequence (ReplyStatus/Status are deliberately left
                # untouched below in both branches).
                last_sent = _most_recent_send_at(matched_lead)
                msg_date = msg.get("date")
                if last_sent is not None and msg_date is not None and msg_date < last_sent:
                    # Chronologically impossible to be a reply to this
                    # campaign's outreach — almost certainly a stale message
                    # tied to a different (possibly deleted) campaign that
                    # happens to share this lead's email address.
                    action_taken = ACTION_LOGGED_UNRELATED
                else:
                    action_taken = ACTION_LOGGED_UNVERIFIED

            if previously_logged is not None:
                if match_method not in ("Header", "Subject"):
                    # Re-evaluated on this run since it wasn't
                    # definitively resolved last time, but still isn't
                    # now either — nothing actually changed, so don't
                    # re-append (would duplicate the existing response
                    # row) or re-touch the lead's own fields (the
                    # "else" branch above deliberately never touches
                    # ReplyStatus/Status for a non-definitive match
                    # anyway, so there's genuinely nothing new to do).
                    continue
                # Upgraded from a previously-logged, non-definitive
                # match to a definitive one (see get_logged_message_info's
                # own docstring for the real, confirmed case this
                # fixes) — update the EXISTING response row's
                # MatchMethod/ActionTaken in place, rather than
                # appending a second, duplicate row for the same
                # message. Note: intent classification (see
                # classify_reply_intent below) intentionally does NOT
                # run for this upgrade path — Intent/IntentConfidence
                # stay however they were (blank) when first logged;
                # re-running it here would add meaningful complexity
                # for a small, secondary feature, on top of an already
                # substantial fix.
                try:
                    sheets.update_lead_fields(matched_lead["_row"], master_updates)
                    sheets.update_response_match(previously_logged["row"], match_method, action_taken)
                except Exception as sheets_exc:  # noqa: BLE001
                    log_error(sheets, campaign_name, ERR_SHEETS_API,
                              f"Reply re-evaluation succeeded but failed to update the sheet: {sheets_exc}",
                              lead_id=matched_lead.get("LeadID", ""), email_addr=matched_lead.get("Email", ""))
                    continue
                actions.append({"lead_id": matched_lead.get("LeadID", ""), "email": matched_lead.get("Email", ""),
                                 "classification": classification, "action": action_taken,
                                 "match_method": match_method, "account": account_name})
                continue

            intent_fields = {"Intent": "", "IntentConfidence": "", "IntentClassifiedAt": ""}
            if classification == CLASSIFICATION_GENUINE and anthropic_api_key:
                intent_result = classify_reply_intent(msg.get("body", ""), anthropic_api_key)
                intent_fields = {
                    "Intent": intent_result["intent"], "IntentConfidence": intent_result["confidence"],
                    "IntentClassifiedAt": now_str,
                }

            try:
                sheets.update_lead_fields(matched_lead["_row"], master_updates)
                sheets.append_response({
                    "ResponseID": msg["message_id"] or f"noid-{now_str}", "LeadID": matched_lead.get("LeadID", ""),
                    "Campaign": campaign_name, "ReceivedAt": now_str, "From": msg["from"], "Subject": msg["subject"],
                    "Snippet": msg["snippet"], "Classification": classification, "MatchMethod": match_method,
                    "MessageID": msg["message_id"], "InReplyTo": msg.get("in_reply_to", ""),
                    "ActionTaken": action_taken, "FullBody": msg.get("body", "")[:49000],
                    "MessageDate": msg["date"].strftime(DATETIME_FMT) if msg.get("date") else "",
                    # Sheets caps a single cell at 50,000 characters — 49000 leaves headroom
                    # rather than sending a request Google would reject outright for the
                    # rare very long email.
                    **intent_fields,
                })
            except Exception as sheets_exc:  # noqa: BLE001
                log_error(sheets, campaign_name, ERR_SHEETS_API,
                          f"Reply detected but failed to update the sheet: {sheets_exc}",
                          lead_id=matched_lead.get("LeadID", ""), email_addr=matched_lead.get("Email", ""))
                continue

            actions.append({"lead_id": matched_lead.get("LeadID", ""), "email": matched_lead.get("Email", ""),
                             "classification": classification, "action": action_taken,
                             "match_method": match_method, "account": account_name})

    return actions


# =============================================================================
# SECTION 13: Dashboards
#
# Fully recomputed and rewritten every run (not appended to) — a snapshot
# of current state, not a log. "Delivered" is an ESTIMATE (Sent minus
# confirmed hard bounces) since SMTP provides no real delivery receipt;
# labeled as such rather than overclaiming precision.
# "Variant Performance" reply attribution is approximate: a reply is
# attributed to whichever stage/variant was the lead's most recent send
# (CurrentStage) at the time they replied.
# =============================================================================

def _pct(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        return "0.0%"
    return f"{(numerator / denominator) * 100:.1f}%"


def compute_campaign_dashboard(campaign_cfg: Dict, leads: List[Dict], responses: List[Dict],
                                send_log: List[Dict], error_log: List[Dict]) -> List[Tuple[str, str, str]]:
    stages = campaign_cfg["stages"]

    total_leads = sum(1 for l in leads if (l.get("Email") or "").strip())
    contacted = [l for l in leads if any((l.get(stage_field_names(i)["sent_at"]) or "").strip()
                                          for i in range(len(stages)))]
    unique_contacted = len(contacted)

    total_sent = sum(1 for r in send_log if r.get("Status") == "sent")
    bounced_hard = sum(1 for r in responses if r.get("Classification") == CLASSIFICATION_BOUNCE_HARD)
    bounced_soft = sum(1 for r in responses if r.get("Classification") == CLASSIFICATION_BOUNCE_SOFT)
    # Unique LEADS who have replied, not a count of response MESSAGES —
    # the actual reported bug: a lead with several back-and-forth
    # messages in one conversation had every message counted separately
    # (33 leads, 51 "replies", a mathematically impossible 154.5% reply
    # rate). ReplyStatus lives on the lead itself, is set exactly once
    # per lead, and is set identically whether the reply was a real,
    # DEFINITIVELY matched one (Header or Subject match — see where
    # ReplyStatus gets set during check_replies) or a manual override
    # via set-lead-override — which is also the second half of what was
    # reported: a manual update must count here too, and this field is
    # exactly where a manual override already writes.
    genuine_replies = sum(1 for l in leads if (l.get("ReplyStatus") or "").strip() == "Replied")
    delivered_est = max(total_sent - bounced_hard, 0)

    last_stage_field = stage_field_names(len(stages) - 1)["sent_at"] if stages else None
    completed = sum(1 for l in leads if last_stage_field and (l.get(last_stage_field) or "").strip()) \
        if last_stage_field else 0

    rows: List[Tuple[str, str, str]] = []
    rows.append(("Overview", "Last Updated", datetime.now().strftime(DATETIME_FMT)))
    rows.append(("Overview", "Total Leads (with Email)", str(total_leads)))
    rows.append(("Overview", "Unique Leads Contacted", str(unique_contacted)))
    rows.append(("Overview", "Total Emails Sent", str(total_sent)))
    rows.append(("Overview", "Delivered (est. = Sent minus Hard Bounces)", str(delivered_est)))
    rows.append(("Overview", "Bounced (Hard)", str(bounced_hard)))
    rows.append(("Overview", "Bounced (Soft)", str(bounced_soft)))
    rows.append(("Overview", "Genuine Replies", str(genuine_replies)))
    rows.append(("Overview", "Reply Rate (Replies / Unique Contacted)", _pct(genuine_replies, unique_contacted)))
    rows.append(("Overview", "Sequence Completion (Reached Final Stage / Unique Contacted)",
                  _pct(completed, unique_contacted)))

    for i, stage in enumerate(stages):
        sent_field = stage_field_names(i)["sent_at"]
        sent_count = sum(1 for l in leads if (l.get(sent_field) or "").strip())
        rows.append(("Per-Stage", f"{stage['name']} - Sent", str(sent_count)))

    accounts_seen = sorted({(l.get("SenderAccount") or "").strip() for l in contacted
                             if (l.get("SenderAccount") or "").strip()})
    for acct in accounts_seen:
        acct_leads = [l for l in contacted if (l.get("SenderAccount") or "").strip() == acct]
        acct_sent = sum(1 for r in send_log if r.get("Status") == "sent" and r.get("SenderAccount") == acct)
        acct_replies = sum(1 for l in acct_leads if l.get("Status") == STATUS_STOPPED_REPLIED)
        rows.append(("Sender Performance", f"{acct} - Sent", str(acct_sent)))
        rows.append(("Sender Performance", f"{acct} - Replies", str(acct_replies)))
        rows.append(("Sender Performance", f"{acct} - Reply Rate", _pct(acct_replies, len(acct_leads))))

    sent_today_by_account = _count_sent_today_by_account(send_log)
    per_account_limit = campaign_cfg.get("sending", {}).get("per_account_daily_limit")
    rotation_on = bool(campaign_cfg.get("sending", {}).get("sender_rotation"))
    if sent_today_by_account or rotation_on:
        rows.append(("Sender Usage Today", "Sender Rotation Enabled", "Yes" if rotation_on else "No"))
        usage_accounts = sorted(set(sent_today_by_account.keys()) | set(accounts_seen))
        for acct in usage_accounts:
            used_today = sent_today_by_account.get(acct, 0)
            if per_account_limit is not None:
                rows.append(("Sender Usage Today", f"{acct} - Sent Today", f"{used_today} / {per_account_limit}"))
            else:
                rows.append(("Sender Usage Today", f"{acct} - Sent Today", f"{used_today} (no per-account cap set)"))

    variant_sent_counts: Dict[str, int] = {}
    for i, stage in enumerate(stages):
        sent_field = stage_field_names(i)["sent_at"]
        variant_field = stage_field_names(i)["variant"]
        for l in leads:
            if (l.get(sent_field) or "").strip():
                v = (l.get(variant_field) or "").strip()
                if v:
                    key = f"{stage['name']}-{v}"
                    variant_sent_counts[key] = variant_sent_counts.get(key, 0) + 1

    variant_reply_counts: Dict[str, int] = {}
    stage_name_to_index = {s["name"]: i for i, s in enumerate(stages)}
    for l in leads:
        if l.get("Status") == STATUS_STOPPED_REPLIED:
            current_stage = l.get("CurrentStage", "")
            idx = stage_name_to_index.get(current_stage)
            if idx is not None:
                v = (l.get(stage_field_names(idx)["variant"]) or "").strip()
                if v:
                    key = f"{current_stage}-{v}"
                    variant_reply_counts[key] = variant_reply_counts.get(key, 0) + 1

    for key in sorted(variant_sent_counts.keys()):
        sent_n = variant_sent_counts[key]
        reply_n = variant_reply_counts.get(key, 0)
        rows.append(("Variant Performance", f"{key} - Sent", str(sent_n)))
        rows.append(("Variant Performance", f"{key} - Replies (approx.)", str(reply_n)))
        rows.append(("Variant Performance", f"{key} - Reply Rate (approx.)", _pct(reply_n, sent_n)))

    error_counts: Dict[str, int] = {}
    for e in error_log:
        et = e.get("ErrorType", "Unknown")
        error_counts[et] = error_counts.get(et, 0) + 1
    for et in sorted(error_counts.keys()):
        rows.append(("Errors (All Time)", et, str(error_counts[et])))

    recent_errors = error_log[-10:] if error_log else []
    for e in recent_errors:
        label = f"{e.get('Timestamp', '')} - {e.get('ErrorType', '')}"
        rows.append(("Recent Errors", label, str(e.get("Message", ""))[:200]))

    return rows


def compute_all_campaigns_row(campaign_name: str, leads: List[Dict], responses: List[Dict],
                               send_log: List[Dict], stages: List[Dict]) -> List[str]:
    total_leads = sum(1 for l in leads if (l.get("Email") or "").strip())
    contacted = sum(1 for l in leads if any((l.get(stage_field_names(i)["sent_at"]) or "").strip()
                                             for i in range(len(stages))))
    total_sent = sum(1 for r in send_log if r.get("Status") == "sent")
    bounced_hard = sum(1 for r in responses if r.get("Classification") == CLASSIFICATION_BOUNCE_HARD)
    bounced_soft = sum(1 for r in responses if r.get("Classification") == CLASSIFICATION_BOUNCE_SOFT)
    # Unique LEADS who have replied, not a count of response MESSAGES —
    # the same fix as compute_campaign_dashboard's own genuine_replies,
    # applied here because this is a SEPARATE function with its OWN,
    # previously unfixed copy of the identical bug: a lead with several
    # back-and-forth messages in one conversation had every message
    # counted separately. This is what the Campaigns Hub list reads for
    # its "Replies" column — the earlier fix to compute_campaign_dashboard
    # only reached the in-page Analytics tab, not this one, producing two
    # different reply counts for the exact same campaign (one correct, one
    # still inflated) depending on which screen you looked at.
    replies = sum(1 for l in leads if (l.get("ReplyStatus") or "").strip() == "Replied")
    delivered_est = max(total_sent - bounced_hard, 0)
    last_stage_field = stage_field_names(len(stages) - 1)["sent_at"] if stages else None
    completed = sum(1 for l in leads if last_stage_field and (l.get(last_stage_field) or "").strip()) \
        if last_stage_field else 0

    return [
        campaign_name, str(total_leads), str(contacted), str(total_sent), str(delivered_est),
        str(bounced_hard), str(bounced_soft), str(replies), _pct(replies, contacted), _pct(completed, contacted),
    ]


def write_dashboard(dashboard_ws, rows: List[Tuple[str, str, str]]) -> None:
    dashboard_ws.clear()
    values = [DASHBOARD_COLUMNS] + [list(r) for r in rows]
    dashboard_ws.update(values, "A1")


def write_all_campaigns_dashboard(ws, campaign_rows: List[List[str]]) -> None:
    ws.clear()
    values = [ALL_CAMPAIGNS_DASHBOARD_COLUMNS] + campaign_rows
    ws.update(values, "A1")


# =============================================================================
# SECTION 14: Main CLI commands
# =============================================================================

def _connect_sheets(campaign_cfg) -> SheetsConnector:
    return SheetsConnector(
        sheet_id=campaign_cfg["sheet_id"],
        master_tab=campaign_cfg["master_tab"],
        responses_tab=campaign_cfg["responses_tab"],
        send_log_tab=campaign_cfg["send_log_tab"],
        error_log_tab=campaign_cfg["error_log_tab"],
        dashboard_tab=campaign_cfg["dashboard_tab"],
        sent_mail_tab=campaign_cfg.get("sent_mail_tab"),
    )


def cmd_preview(args):
    campaign_cfg = get_campaign(args.campaign)
    sheets = _connect_sheets(campaign_cfg)
    leads = sheets.get_all_leads()

    duplicates = find_duplicate_email_leads(leads)
    if duplicates:
        print(f"WARNING: {len(duplicates)} email address(es) appear on more than one Master Sheet row — "
              "only the first (lowest row number) of each is ever eligible; the rest are silently excluded "
              "from sending, but should be cleaned up:")
        for email_addr, rows in duplicates.items():
            lead_ids = ", ".join(str(r.get("LeadID", "?")) for r in rows)
            print(f"  {email_addr} — rows {[r['_row'] for r in rows]} (LeadIDs: {lead_ids})")
        print()

    forced_variant = None if args.variant in (None, "Auto") else args.variant
    suppressed = None
    if campaign_cfg.get("format") == FORMAT_HTML:
        try:
            suppressed = load_unsubscribe_pairs(sheets)
        except UnsubscribeListUnreadableError as exc:
            print(f"WARNING: {exc} This preview does not leave out anyone who has opted out.\n")
    plan = build_batch(campaign_cfg, leads, args.stage, args.batch_size, forced_variant=forced_variant,
                        ignore_wait_days=args.ignore_wait_days, suppressed_pairs=suppressed)

    if not plan:
        print(f"No eligible leads found for stage '{args.stage}'.")
        return

    if args.ignore_wait_days:
        print(f"NOTE: --ignore-wait-days is set — the scheduled wait for stage '{args.stage}' was skipped "
              "for this preview. Every other eligibility rule (Approval, not already sent, previous stage "
              "actually sent, no reply) still applied normally.\n")
    print(f"{len(plan)} eligible lead(s) for stage '{args.stage}':\n")
    for item in plan:
        lead = item["lead"]
        print("=" * 70)
        print(f"Lead ID:  {lead.get('LeadID')}")
        print(f"To:       {lead.get('FirstName')} {lead.get('LastName')} <{lead.get('Email')}>")
        if not is_valid_email_format(lead.get("Email", "")):
            print("          WARNING: this email address doesn't look correctly formatted.")
        print(f"Variant:  {item['variant']}")
        print(f"Subject:  {item['subject']}"
              + ("  (continuing existing thread)" if item["is_continuation"] else ""))
        print("-" * 70)
        print(item["body"])
        if item["missing_variables"]:
            print(f"\nWARNING: unrecognized template variable(s), rendered blank: "
                  f"{', '.join('{{' + v + '}}' for v in item['missing_variables'])}")
    print("=" * 70)
    print("\nNothing has been sent. Re-run with the 'send' command to actually send this batch.")


def apply_sending_overrides(campaign_cfg: Dict, daily_limit: Optional[int] = None,
                             per_account_daily_limit: Optional[int] = None,
                             sender_rotation: Optional[str] = None) -> List[str]:
    """Applies CLI-provided overrides for a single run — never touches
    campaigns.yaml. Replaces campaign_cfg['sending'] with a fresh copy
    before mutating it, so nothing shared/cached elsewhere is affected.
    Returns the list of keys that were actually overridden (for reporting).
    Raises ValueError for an invalid override value."""
    campaign_cfg["sending"] = dict(campaign_cfg["sending"])
    sending = campaign_cfg["sending"]
    overridden = []

    if daily_limit is not None:
        if daily_limit <= 0:
            raise ValueError("--daily-limit must be a positive integer.")
        sending["daily_limit"] = daily_limit
        overridden.append("daily_limit")

    if per_account_daily_limit is not None:
        if per_account_daily_limit <= 0:
            raise ValueError("--per-account-daily-limit must be a positive integer.")
        sending["per_account_daily_limit"] = per_account_daily_limit
        overridden.append("per_account_daily_limit")

    if sender_rotation is not None:
        sending["sender_rotation"] = (sender_rotation == "true")
        overridden.append("sender_rotation")

    return overridden


def cmd_send(args):
    campaign_cfg = get_campaign(args.campaign)
    sheets = _connect_sheets(campaign_cfg)
    accounts = load_email_accounts()

    duplicates = find_duplicate_email_leads(sheets.get_all_leads())
    if duplicates:
        print(f"WARNING: {len(duplicates)} email address(es) appear on more than one Master Sheet row — "
              "only the first (lowest row number) of each is ever eligible; the rest are silently excluded "
              "from sending, but should be cleaned up:")
        for email_addr, rows in duplicates.items():
            lead_ids = ", ".join(str(r.get("LeadID", "?")) for r in rows)
            print(f"  {email_addr} — rows {[r['_row'] for r in rows]} (LeadIDs: {lead_ids})")
        print()

    try:
        overridden = apply_sending_overrides(campaign_cfg, args.daily_limit,
                                              args.per_account_daily_limit, args.sender_rotation)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    sending = campaign_cfg["sending"]
    print(
        f"Sending config for this run: daily_limit={sending.get('daily_limit')}"
        f"{' [overridden]' if 'daily_limit' in overridden else ' [from config]'}, "
        f"per_account_daily_limit={sending.get('per_account_daily_limit')}"
        f"{' [overridden]' if 'per_account_daily_limit' in overridden else ' [from config]'}, "
        f"sender_rotation={sending.get('sender_rotation', False)}"
        f"{' [overridden]' if 'sender_rotation' in overridden else ' [from config]'}\n"
    )

    forced_variant = None if args.variant in (None, "Auto") else args.variant
    if args.ignore_wait_days:
        print(f"NOTE: --ignore-wait-days is set — the scheduled wait for stage '{args.stage}' will be "
              "skipped for this run. Every other eligibility rule (Approval, not already sent, previous "
              "stage actually sent, no reply) still applies normally.\n")
    try:
        results = send_batch(campaign_cfg, sheets, accounts, args.stage, args.batch_size,
                              forced_variant=forced_variant, ignore_wait_days=args.ignore_wait_days)
    except (CampaignPausedError, OutsideSendingWindowError) as exc:
        print(f"SKIPPED: {exc}")
        return

    if not results:
        print(f"No eligible leads to send for stage '{args.stage}' "
              "(none eligible, or today's sending limit already reached).")
        return

    batch_id = results[0].get("batch_id", "")
    sent = [r for r in results if r["status"] == "sent"]
    sent_but_sheet_error = [r for r in results if r["status"] == "sent_but_sheet_error"]
    skipped = [r for r in results if r["status"] == "skipped"]
    errors = [r for r in results if r["status"] == "error"]

    print(f"Batch ID: {batch_id}")
    print(f"Sent {len(sent)} email(s), {len(sent_but_sheet_error)} sent-but-sheet-error, "
          f"{len(skipped)} skipped (sender capacity), {len(errors)} error(s).\n")
    for r in sent:
        print(f"  OK    {r['email']} (variant {r['variant']}, account {r['account']})")
    for r in sent_but_sheet_error:
        print(f"  WARN  {r['email']}: email sent, but sheet update failed ({r['error']}) — check manually")
    for r in skipped:
        print(f"  SKIP  {r['email']}: {r['error']}")
    for r in errors:
        print(f"  ERROR {r['email']}: [{r['error_type']}] {r['error']}")


# How long Auto Send waits before retrying a campaign whose connection hit
# Google's per-minute read quota. Google's quota window is one minute, so
# the first wait is a bit under a minute and the second a bit over: by the
# second retry the window has certainly rolled over. Jittered (see
# _quota_retry_delay) so colliding runs don't retry in lockstep.
AUTO_SEND_CONNECT_RETRY_DELAYS_SECONDS = [45, 75]


def _campaign_window_closed_reason(campaign_cfg: Dict) -> Optional[str]:
    """Decides from CONFIG ALONE — no Google request at all — whether a
    campaign is outside its sending window right now. Returns the reason
    if it is, None if it's open.

    Any problem evaluating the window (a bad timezone, a malformed
    schedule) returns None on purpose: send_batch has its own window
    check and error reporting, and a campaign must never be silently
    skipped over a config mistake that send_batch would have surfaced."""
    try:
        within_window, reason = is_within_sending_window(campaign_cfg.get("schedule") or {})
    except Exception:  # noqa: BLE001
        return None
    return None if within_window else reason


def _campaign_is_complete(stages: List[Dict], leads: List[Dict]) -> bool:
    """True only if at least one lead has an email AND every such lead is
    finished (stopped, or already sent the last stage). Deliberately the
    same rule as the control panel's "Completed" label
    (streamlit_app/campaign_status_logic.py), so a campaign this log
    calls completed is one the app shows as Completed. Used only to
    word a log line — it never decides what gets sent."""
    sendable = [lead for lead in leads if (lead.get("Email") or "").strip()]
    if not sendable or not stages:
        return False
    last_sent_field = stage_field_names(len(stages) - 1)["sent_at"]
    return all(
        (lead.get("Status") or "").startswith("Stopped") or bool((lead.get(last_sent_field) or "").strip())
        for lead in sendable
    )


def _connect_sheets_with_quota_retry(campaign_cfg: Dict, label: str) -> "SheetsConnector":
    """_connect_sheets, retried — only when Google's per-minute read quota
    was hit. Every scheduled workflow shares one service account and so
    one quota; a collision at connect time is routine, and giving up on
    the campaign for the whole run (the old behaviour) just meant its due
    emails waited for the next run. Any other error is raised at once."""
    attempts = len(AUTO_SEND_CONNECT_RETRY_DELAYS_SECONDS) + 1
    for attempt, base_delay in enumerate(AUTO_SEND_CONNECT_RETRY_DELAYS_SECONDS + [None], start=1):
        try:
            return _connect_sheets(campaign_cfg)
        except Exception as exc:  # noqa: BLE001
            is_quota = isinstance(exc, gspread.exceptions.APIError) and _is_gspread_quota_error(exc)
            if base_delay is None or not is_quota:
                raise
            wait = _quota_retry_delay(base_delay)
            print(f"{label}: Google's per-minute read limit was hit while connecting — "
                  f"waiting {wait:.0f}s, then retrying (attempt {attempt + 1} of {attempts}).")
            time.sleep(wait)


def cmd_auto_send_all(args):
    """The scheduled trigger's entry point for automated sending.

    ONLY campaigns that are in progress are ever worked on:
      * Paused, Draft and Deleted campaigns are skipped outright, never
        even connected to.
      * A campaign outside its sending window (wrong hours, or not one
        of its send days) is skipped from its CONFIG alone — no Google
        request at all.
      * A campaign with nothing left to do — Completed, or every lead
        still waiting out a follow-up delay — costs ONE read of its
        leads, and then no further requests.

    For a campaign that does pass those checks, the leads are read once
    and each stage is checked IN MEMORY for whether any lead is
    actually due; only stages with a due lead go on to send_batch
    (which still enforces the sending window, the daily limit and every
    per-lead eligibility rule itself — nothing about WHAT may be sent
    changed). After a stage that really sent something, the leads are
    re-read so a later stage that the send just made due (a follow-up
    with a zero-day wait) is still handled in the same run.

    Why: the old version connected to every campaign and called
    send_batch for every stage, each re-reading the Sheet — roughly 55
    Google reads per run against a limit of 60 a minute shared by every
    workflow, so runs routinely failed with 429s.

    One campaign's failure, or one stage being outside the sending
    window, is reported but never blocks any other campaign or stage in
    the same run."""
    accounts = load_email_accounts()
    any_running = False
    any_errors = False
    outside_window = idle = worked = 0

    for campaign_name in discover_campaign_names():
        try:
            campaign_cfg = get_campaign(campaign_name)
        except Exception as exc:  # noqa: BLE001 - a broken campaign config shouldn't sink the whole run
            print(f"{campaign_name}: couldn't load config ({exc}) — skipped.")
            any_errors = True
            continue

        current_status = campaign_cfg.get("status") or "active"
        if current_status != "active":
            # Deliberately printed, not silent: a campaign that is
            # Paused/Draft/Deleted vanishing from this output with zero
            # mention once looked exactly like "auto-send only ever
            # processes one hardcoded campaign".
            print(f"{campaign_name}: skipped (status is '{current_status}', not active).")
            continue
        any_running = True
        stages = campaign_cfg.get("stages") or []

        closed_reason = _campaign_window_closed_reason(campaign_cfg)
        if closed_reason:
            print(f"{campaign_name}: skipped — outside its sending window, so nothing can be sent "
                  f"(no Sheet access needed). {closed_reason}")
            outside_window += 1
            continue

        try:
            sheets = _connect_sheets_with_quota_retry(campaign_cfg, campaign_name)
        except Exception as exc:  # noqa: BLE001 - one campaign's connection failure shouldn't block others
            print(f"{campaign_name}: couldn't connect to its Sheet ({exc}).")
            any_errors = True
            continue

        try:
            leads = sheets.get_all_leads() if stages else []
        except Exception as exc:  # noqa: BLE001
            print(f"{campaign_name}: couldn't read its leads ({exc}).")
            any_errors = True
            continue

        idle_stages = []
        for stage_index, stage in enumerate(stages):
            stage_name = stage["name"]
            if not get_eligible_leads(leads, stages, stage_index,
                                       timezone_name=_campaign_timezone_name(campaign_cfg)):
                idle_stages.append(stage_name)
                continue
            try:
                results = send_batch(campaign_cfg, sheets, accounts, stage_name,
                                      batch_size=campaign_cfg["sending"]["daily_limit"])
                if results:
                    sent = sum(1 for r in results if r.get("status") == "sent")
                    print(f"{campaign_name} / {stage_name}: sent {sent} of {len(results)} attempted.")
                    if sent and stage_index + 1 < len(stages):  # only a LATER stage can benefit from a re-read
                        try:
                            leads = sheets.get_all_leads()  # a send can make a later stage due
                        except Exception as exc:  # noqa: BLE001
                            print(f"{campaign_name}: couldn't re-read leads after sending ({exc}) — later "
                                  f"stages are judged on the earlier data; anything due waits for the next run.")
                else:
                    # An empty list (no exception) means: in the window,
                    # but the daily limit was already met, or the
                    # eligible leads were all dropped at send time.
                    print(f"{campaign_name} / {stage_name}: nothing to send right now "
                          f"(daily limit already met, or no lead currently eligible).")
            except (CampaignPausedError, OutsideSendingWindowError) as exc:
                # Expected, routine — e.g. the window closed mid-run.
                print(f"{campaign_name} / {stage_name}: skipped ({exc}).")
            except Exception as exc:  # noqa: BLE001 - one stage's failure shouldn't block the rest
                print(f"{campaign_name} / {stage_name}: FAILED ({exc}).")
                any_errors = True

        if len(idle_stages) == len(stages):
            idle += 1
            if _campaign_is_complete(stages, leads):
                print(f"{campaign_name}: completed — every lead has finished or stopped; nothing to send.")
            else:
                print(f"{campaign_name}: nothing due on any of its {len(stages)} stages right now "
                      f"(leads are waiting out follow-up delays, or none are eligible).")
        else:
            worked += 1
            if idle_stages:
                print(f"{campaign_name}: no lead due yet for {', '.join(idle_stages)}.")

    if not any_running:
        print("No campaign is currently Running — nothing to do.")
    else:
        print(f"Auto Send summary: {worked} campaign(s) with something due, {idle} with nothing due, "
              f"{outside_window} outside their sending window.")
    if any_errors:
        sys.exit(1)


def backfill_response_bodies(sheets, accounts: Dict[str, Dict[str, str]], anthropic_api_key: Optional[str] = None,
                              fetch=None, include_dates: bool = False) -> List[Dict]:
    """Repairs replies that were logged with NO text — HTML-only emails, which
    the reader used to store empty (see _extract_plain_text_body). The text is
    not lost: the email is still in the mailbox, so each such row is looked up
    there by its Message-ID (its own sender account's mailbox first, then the
    others) and, if it now yields readable text, FullBody and Snippet are filled
    in. A row that already has any text is never touched, so this only fills
    gaps and is safe to run again.

    The email's REAL sent time (MessageDate) is filled in too, for every row it
    repairs. With `include_dates=True` it also looks up rows that have text but no
    real sent time — which is every reply logged before that column existed — and
    fills in just that date. (The conversation view orders by it.)

    With `anthropic_api_key`, a recovered Genuine Reply also gets its intent
    read again — the earlier reading was made from an empty message, so it could
    only ever say Unclear. Classification, match method and action taken are
    deliberately NOT re-decided: changing those could change what happened to a
    lead's sequence, and this is only about showing the words.

    Returns one dict per candidate row with a `status`: recovered, dated (only the
    date was missing), still_empty (the email truly has no text — probably only an
    image; `structure` lists its content types), no_date, not_found, or
    no_message_id. A row's `message_date` is only present when it is missing from
    the Sheet and was found. `fetch` is injectable for tests."""
    fetch = fetch or imap_fetch_by_message_ids
    lead_account = {str(l.get("LeadID", "")): (l.get("SenderAccount") or "").strip() for l in sheets.get_all_leads()}

    results: List[Dict] = []
    rows_by_message_id: Dict[str, List[Dict]] = {}
    for row in sheets.get_all_responses():
        needs_text = not ((row.get("FullBody") or "").strip() or (row.get("Snippet") or "").strip())
        has_date = bool((row.get("MessageDate") or "").strip())
        if not needs_text and not (include_dates and not has_date):
            continue
        entry = {"row": row["_row"], "lead_id": str(row.get("LeadID", "")),
                 "classification": row.get("Classification", ""), "message_id": (row.get("MessageID") or "").strip(),
                 "needs_text": needs_text, "has_date": has_date}
        if not entry["message_id"]:
            results.append({**entry, "status": "no_message_id"})
        else:
            rows_by_message_id.setdefault(entry["message_id"], []).append(entry)

    found: Dict[str, Dict] = {}
    account_errors: Dict[str, str] = {}
    asked = set()

    def ask(account_name: str, message_ids: List[str]) -> None:
        todo = [m for m in message_ids if m not in found and (account_name, m) not in asked]
        if not todo or account_name in account_errors:
            return
        asked.update((account_name, m) for m in todo)
        account = accounts[account_name]
        try:
            host, port, username = imap_connection_settings(account)
            found.update(fetch(account["address"], get_imap_password(account), todo,
                               imap_host=host, imap_port=port, imap_username=username))
        except Exception as exc:  # noqa: BLE001 - one mailbox being unreachable must not stop the others
            account_errors[account_name] = classify_imap_exception(exc)

    own_mailbox: Dict[str, List[str]] = {}
    for message_id, entries in rows_by_message_id.items():
        name = lead_account.get(entries[0]["lead_id"], "")
        if name in accounts:
            own_mailbox.setdefault(name, []).append(message_id)
    for name, message_ids in own_mailbox.items():
        ask(name, message_ids)
    for name in accounts:                       # whatever is still missing: try every other mailbox too
        ask(name, list(rows_by_message_id))

    now_str = datetime.now().strftime(DATETIME_FMT)
    for message_id, entries in rows_by_message_id.items():
        parsed = found.get(message_id)
        for entry in entries:
            if parsed is None:
                results.append({**entry, "status": "not_found", "account_errors": dict(account_errors)})
                continue
            sent_at = parsed.get("date")
            message_date = sent_at.strftime(DATETIME_FMT) if sent_at is not None and not entry["has_date"] else ""
            body = parsed.get("body", "")
            if not entry["needs_text"]:                      # only the date was missing
                results.append({**entry, "status": "dated" if message_date else "no_date", "message_date": message_date})
                continue
            if not body.strip():
                results.append({**entry, "status": "still_empty", "message_date": message_date,
                                "structure": parsed.get("structure", "")})
                continue
            intent_fields: Dict[str, str] = {}
            if anthropic_api_key and entry["classification"] == CLASSIFICATION_GENUINE:
                reading = classify_reply_intent(body, anthropic_api_key)
                intent_fields = {"Intent": reading["intent"], "IntentConfidence": reading["confidence"],
                                 "IntentClassifiedAt": now_str}
            results.append({**entry, "status": "recovered", "full_body": body[:49000], "snippet": body[:500],
                            "chars": len(body), "intent_fields": intent_fields, "message_date": message_date})
    return sorted(results, key=lambda r: r["row"])


def cmd_backfill_thread_subject(args):
    """One-time migration command — see backfill_thread_subjects' docstring
    for exactly what this does and its accuracy caveat. Defaults to
    --dry-run in the GitHub Actions workflow (not here in the raw CLI, to
    match every other command's behavior of doing what you asked)."""
    campaign_cfg = get_campaign(args.campaign)
    sheets = _connect_sheets(campaign_cfg)
    leads = sheets.get_all_leads()

    results = backfill_thread_subjects(campaign_cfg, leads)
    to_backfill = [r for r in results if r["status"] == "backfilled"]
    skipped = [r for r in results if r["status"] not in ("backfilled",)]

    action_word = "Would backfill" if args.dry_run else "Backfilling"
    print(f"{action_word} ThreadSubject for {len(to_backfill)} lead(s).\n")
    for r in to_backfill:
        print(f"  Lead {r['lead_id']} (from {r['stage']}): {r['thread_subject']!r}")

    if skipped:
        print(f"\n{len(skipped)} lead(s) skipped (no action needed/possible):")
        for r in skipped:
            detail = f" ({r['stage']})" if r.get("stage") else ""
            extra = f" — {r['error']}" if r.get("error") else ""
            print(f"  Lead {r['lead_id']}: {r['status']}{detail}{extra}")

    if args.dry_run:
        print("\nDRY RUN — nothing was written. Re-run without --dry-run to actually write these.")
        return

    for r in to_backfill:
        sheets.update_lead_fields(r["row"], {"ThreadSubject": r["thread_subject"]})
    print(f"\nWrote ThreadSubject for {len(to_backfill)} lead(s).")


BACKFILL_WRITE_CHUNK_ROWS = 100
BACKFILL_WRITE_CHUNK_CHARS = 1_500_000     # keeps one request well inside Google's size limit when the rows carry long text
BACKFILL_WRITE_PAUSE_SECONDS = 2.0


def _chunk_updates(updates: List[Dict], max_rows: int = BACKFILL_WRITE_CHUNK_ROWS,
                   max_chars: int = BACKFILL_WRITE_CHUNK_CHARS) -> List[List[Dict]]:
    """Splits repairs into requests of at most `max_rows` rows and about `max_chars` characters, in order."""
    chunks: List[List[Dict]] = []
    current: List[Dict] = []
    size = 0
    for update in updates:
        weight = len(update.get("full_body") or "") + len(update.get("snippet") or "") + 200
        if current and (len(current) >= max_rows or size + weight > max_chars):
            chunks.append(current)
            current, size = [], 0
        current.append(update)
        size += weight
    if current:
        chunks.append(current)
    return chunks


def cmd_backfill_response_bodies(args):
    """See backfill_response_bodies. Prints only row numbers, lead IDs, counts, reasons and content
    TYPES — never a name, an address, a subject or any message text — because this repository's
    Actions logs are public."""
    campaign_cfg = get_campaign(args.campaign)
    sheets = _connect_sheets(campaign_cfg)
    accounts = load_email_accounts()
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    include_dates = bool(getattr(args, "include_dates", False))

    results = backfill_response_bodies(sheets, accounts, None if args.dry_run else api_key, include_dates=include_dates)
    recovered = [r for r in results if r["status"] == "recovered"]
    date_only = [r for r in results if r["status"] == "dated"]
    others = [r for r in results if r["status"] not in ("recovered", "dated")]
    plural = lambda n: "reply" if n == 1 else "replies"      # noqa: E731

    action_word = "Would fill in" if args.dry_run else "Filling in"
    print(f"{action_word} the text of {len(recovered)} logged {plural(len(recovered))} that had no text.\n")
    for r in recovered:
        print(f"  Row {r['row']} (lead {r['lead_id']}): {r['chars']} characters")
    if include_dates:
        print(f"\n{action_word} the real sent time of {len(date_only)} more {plural(len(date_only))} that already had text.")
    if others:
        print(f"\n{len(others)} could not be fully filled in:")
        for r in others:
            if r["status"] == "still_empty":
                reason = "the email itself has no readable text (probably only an image or attachment)"
                if r.get("structure"):
                    reason += f" — its parts: {r['structure']}"
                if r.get("message_date"):
                    reason += "; its sent time is still recorded"
            elif r["status"] == "no_message_id":
                reason = "no Message-ID was logged for it, so it cannot be looked up"
            elif r["status"] == "no_date":
                reason = "found, but the email carries no date"
            else:                                                    # not_found
                unreachable = r.get("account_errors") or {}
                reason = "not found in any mailbox that could be checked"
                if unreachable:
                    reason += f" ({len(unreachable)} mailbox(es) could not be reached: " + ", ".join(
                        f"{name} - {kind}" for name, kind in unreachable.items()) + ")"
            print(f"  Row {r['row']} (lead {r['lead_id']}): {reason}")

    genuine = sum(1 for r in recovered if r["classification"] == CLASSIFICATION_GENUINE)
    if args.dry_run:
        print("\nDRY RUN — nothing was written. Re-run without --dry-run to write these."
              + (f" {genuine} would also have their intent read again." if api_key and genuine else
                 (" Their intent will not be read again: no ANTHROPIC_API_KEY is set." if genuine else "")))
        return

    only_dates = date_only + [r for r in others if r["status"] == "still_empty" and r.get("message_date")]
    updates = [{"row": r["row"], "full_body": r["full_body"], "snippet": r["snippet"],
                "intent_fields": r["intent_fields"] or None, "message_date": r.get("message_date")} for r in recovered]
    updates += [{"row": r["row"], "full_body": None, "snippet": None, "intent_fields": None,
                 "message_date": r["message_date"]} for r in only_dates]
    # A few big requests, a short pause between them: one request per row used up Google's 60-writes-a-minute
    # limit. Each request is complete in itself and the repair only ever fills gaps, so if Google still refuses
    # part-way, running it again simply carries on from where it stopped.
    written = 0
    for index, chunk in enumerate(_chunk_updates(updates)):
        if index:
            time.sleep(BACKFILL_WRITE_PAUSE_SECONDS)
        try:
            sheets.update_response_rows(chunk)
        except Exception as exc:  # noqa: BLE001 - report how far it got, then fail so the run is marked failed
            print(f"\nSTOPPED: wrote {written} of {len(updates)} row(s) before Google refused the next request "
                  f"({type(exc).__name__}). Nothing is half-written; run it again to carry on — rows already done are skipped.")
            raise SystemExit(1)
        written += len(chunk)
    reread = sum(1 for r in recovered if r["intent_fields"])
    print(f"\nWrote the text for {len(recovered)} {plural(len(recovered))}; read the intent again for {reread}; "
          f"recorded the real sent time of {len(only_dates) + sum(1 for r in recovered if r.get('message_date'))}.")


def cmd_sync_sent_mail(args):
    """Finds and stores what you sent yourself (see find_sent_mail_to_log) over the last --days days —
    for history older than the hourly check looks back over. Prints only lead IDs and counts: this
    repository's Actions logs are public."""
    campaign_cfg = get_campaign(args.campaign)
    sheets = _connect_sheets(campaign_cfg)
    accounts = load_email_accounts()
    days = max(1, int(args.days))
    found = find_sent_mail_to_log(sheets, accounts, days * 24)
    rows = found["rows"]

    per_lead: Dict[str, int] = {}
    for row in rows:
        per_lead[row["LeadID"]] = per_lead.get(row["LeadID"], 0) + 1
    action_word = "Would store" if args.dry_run else "Storing"
    print(f"{action_word} {len(rows)} message(s) you sent yourself, across {len(per_lead)} lead(s), "
          f"from the last {days} day(s).\n")
    for lead_id in sorted(per_lead, key=lambda x: (not x.isdigit(), int(x) if x.isdigit() else 0, x)):
        print(f"  Lead {lead_id}: {per_lead[lead_id]} message(s)")
    if found["account_errors"]:
        print(f"\n{len(found['account_errors'])} mailbox(es) could not be read:")
        for error in found["account_errors"]:
            print(f"  {error}")

    if args.dry_run:
        print("\nDRY RUN — nothing was written. Re-run without --dry-run to store these.")
        return
    stored = sheets.append_sent_mail_rows(rows) if rows else 0
    print(f"\nStored {stored} message(s).")


def cmd_import_leads(args):
    """Reads {"leads": [{...}, ...], "allow_duplicate_emails": bool} from
    --file and appends them to the Master Sheet. This is the ONLY thing
    that ever writes to the Master Sheet from a bulk-import path —
    invoked by import_leads.yml after Streamlit commits the mapped
    payload file, never called with Streamlit-supplied data any other
    way. "allow_duplicate_emails" defaults to False if absent from the
    payload — see import_leads' own docstring for exactly what turning
    it on does and doesn't affect."""
    campaign_cfg = get_campaign(args.campaign)
    sheets = _connect_sheets(campaign_cfg)
    with open(args.file, "r", encoding="utf-8") as f:
        payload = json.load(f)
    new_leads = payload.get("leads", [])
    if not new_leads:
        print("No leads in payload file — nothing to do.")
        return
    summary = import_leads(sheets, args.campaign, new_leads,
                            allow_duplicate_emails=bool(payload.get("allow_duplicate_emails", False)))
    print(f"Imported {summary['imported']} lead(s).")
    if summary["skipped_duplicate"]:
        print(f"Skipped {summary['skipped_duplicate']} duplicate email(s) (already in the Master Sheet).")
    if summary["skipped_no_email"]:
        print(f"Skipped {summary['skipped_no_email']} row(s) with no email address.")


def cmd_remove_leads(args):
    """Reads {"lead_ids": ["5", "8", ...]} from --file and sets their
    Status to Removed — never a hard delete, see remove_leads' docstring."""
    campaign_cfg = get_campaign(args.campaign)
    sheets = _connect_sheets(campaign_cfg)
    with open(args.file, "r", encoding="utf-8") as f:
        payload = json.load(f)
    lead_ids = payload.get("lead_ids", [])
    if not lead_ids:
        print("No lead_ids in payload file — nothing to do.")
        return
    summary = remove_leads(sheets, lead_ids)
    print(f"Removed {summary['removed']} lead(s) (Status set to '{STATUS_REMOVED}').")
    if summary["not_found"]:
        print(f"{summary['not_found']} LeadID(s) in the payload weren't found in the Master Sheet.")


def cmd_set_lead_override(args):
    """Sets a manual, per-lead override — sending status ("stop this
    ONE lead without removing it"), Asana pipeline stage ("Rights
    Secured" / "Declined / Dead" for a lead handled outside the
    automated pipeline entirely), and/or reply tracking (ReplyStatus /
    LastInboundClassification) — found by email, not LeadID, since
    that's what a human actually has on hand when looking at a creator
    in the Data tab.

    The reply-tracking fields exist for a real, recurring gap: a
    genuine reply that lands on a NEW email thread with a different
    subject (rather than a reply within the original thread) is never
    matched back to the lead's own row by the automated reply-checker,
    which only ever links a reply to a lead via that original thread's
    own Message-ID references. The conversation is still genuine and
    visible in the Response tab, but the lead's own ReplyStatus never
    gets set — meaning compute_lead_asana_stage has no way to know a
    reply happened, and a sync can revert the task back to Outreach
    Sent or Follow-up even after being manually moved to Negotiating,
    since Negotiating itself is derived from ReplyStatus, not a
    protected manual-only stage. Setting ReplyStatus to "Replied"
    directly fixes this at the source — once set, Negotiating is
    reached the normal way and never reverts, exactly like a reply
    that was correctly thread-matched.

    An empty string for any of these four flags explicitly CLEARS that
    override (resumes normal automated behavior) — distinct from not
    passing the flag at all, which leaves that field untouched. Setting
    ReplyStatus to "Replied" also sets ReplyAt to now; setting
    LastInboundClassification to a non-blank value also sets
    LastInboundAt to now — matching what the normal, automatic
    reply-detection flow already does when it sets these fields
    together, so a manually-set reply looks the same as a normally-
    detected one everywhere else in the system that reads them."""
    campaign_cfg = get_campaign(args.campaign)
    sheets = _connect_sheets(campaign_cfg)
    leads = sheets.get_all_leads()
    email_lower = args.email.strip().lower()
    matching = [l for l in leads if (l.get("Email") or "").strip().lower() == email_lower]
    if not matching:
        print(f"No lead found with email '{args.email}' in campaign '{args.campaign}'.")
        sys.exit(1)
    if len(matching) > 1:
        print(f"NOTE: {len(matching)} leads share this email — updating only the first "
              f"(row {matching[0]['_row']}).")
    lead = matching[0]

    updates = {}
    if args.status is not None:
        if args.status not in ("", STATUS_STOPPED_MANUAL):
            print(f"ERROR: --status must be '' (resume normal sending) or '{STATUS_STOPPED_MANUAL}' "
                  f"(stop sending to this lead only), got {args.status!r}.", file=sys.stderr)
            sys.exit(1)
        updates["Status"] = args.status
    if args.asana_stage is not None:
        if args.asana_stage and args.asana_stage.lower() not in _ASANA_STAGE_NAMES_BY_LOWER:
            valid = ", ".join(sorted(_ASANA_STAGE_NAMES_BY_LOWER.values()))
            print(f"ERROR: --asana-stage must be '' (resume automatic stage tracking) or one of: "
                  f"{valid}. Got {args.asana_stage!r}.", file=sys.stderr)
            sys.exit(1)
        updates["ManualAsanaStage"] = args.asana_stage
    if args.reply_status is not None:
        if args.reply_status not in ("", "Replied"):
            print(f"ERROR: --reply-status must be '' (clear) or 'Replied', got {args.reply_status!r}.",
                  file=sys.stderr)
            sys.exit(1)
        updates["ReplyStatus"] = args.reply_status
        if args.reply_status == "Replied":
            updates["ReplyAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if args.last_inbound_classification is not None:
        valid_classifications = (CLASSIFICATION_GENUINE, CLASSIFICATION_AUTOREPLY, CLASSIFICATION_OOO,
                                  CLASSIFICATION_BOUNCE_HARD, CLASSIFICATION_BOUNCE_SOFT)
        if args.last_inbound_classification and args.last_inbound_classification not in valid_classifications:
            valid = ", ".join(valid_classifications)
            print(f"ERROR: --last-inbound-classification must be '' (clear) or one of: {valid}. "
                  f"Got {args.last_inbound_classification!r}.", file=sys.stderr)
            sys.exit(1)
        updates["LastInboundClassification"] = args.last_inbound_classification
        if args.last_inbound_classification:
            updates["LastInboundAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if not updates:
        print("Nothing to update — pass --status, --asana-stage, --reply-status, and/or "
              "--last-inbound-classification.")
        return

    sheets.update_lead_fields(lead["_row"], updates)
    print(f"Updated {args.email}: {updates}")


ASANA_API_BASE = "https://app.asana.com/api/1.0"
ASANA_MAX_RETRIES = 3
ASANA_RETRY_BASE_DELAY_SECONDS = 2

# The Asana pipeline stage a lead sits in. The first four are auto-derived
# from data the email system already tracks (see compute_lead_asana_stage);
# the last two are real human business decisions — a sync run must NEVER
# set or move a task into or out of either one on its own. See
# decide_asana_sync_action for how that's actually enforced.
ASANA_STAGE_SOURCED = "Sourced"
ASANA_STAGE_OUTREACH_SENT = "Outreach Sent"
ASANA_STAGE_FOLLOWUP = "Follow-up"
ASANA_STAGE_NEGOTIATING = "Negotiating"
ASANA_STAGE_RIGHTS_SECURED = "Rights Secured"
ASANA_STAGE_DECLINED_DEAD = "Declined / Dead"
ASANA_MANUAL_ONLY_STAGES = {ASANA_STAGE_RIGHTS_SECURED, ASANA_STAGE_DECLINED_DEAD}

# Once a task's LIVE section is one of these, custom-field sync switches
# from "always push the Sheet's value" to "fill blanks only" — see
# _filter_custom_fields_to_blanks_only for the actual mechanism, and the
# real reported case this exists for: a human manually sets/adjusts a
# negotiated field directly in Asana (a Rights Expiration date being the
# concrete example reported) once a deal is actually being worked, and
# the very next scheduled sync was overwriting that manual edit back to
# the Sheet's own, older value — every run, indefinitely, since the
# Sheet was never also updated to match. Negotiating is included, not
# just the two terminal ASANA_MANUAL_ONLY_STAGES above, because
# negotiation is exactly when a human starts entering real, specific
# terms by hand — waiting until Rights Secured would protect the data
# only after the negotiation that produced it is already over.
ASANA_FILL_BLANKS_ONLY_STAGES = {ASANA_STAGE_NEGOTIATING} | ASANA_MANUAL_ONLY_STAGES

# Columns the email system itself owns — never treated as a custom field to
# push to Asana, and never used as the "does this look like extra creator
# data" signal in build_asana_custom_fields_payload.
_ASANA_RESERVED_LEAD_FIELDS = set(MASTER_COLUMNS) | {"_row", "AsanaTaskGID"}

# A handful of Asana field names have an obvious, exact equivalent already
# tracked by the email system under a different name — falling back to that
# rather than requiring the SAME data typed twice under a second column.
# Checked only when the lead has no column of its own with a matching name.
_ASANA_FIELD_NAME_FALLBACKS = {
    "creator email": "Email",
}


def _safe_lead_str(value) -> str:
    """Coerces a raw Sheet cell value to a stripped string, safely,
    regardless of its actual Python type. gspread returns a cell's
    value typed by what it LOOKS like, not what column it's in — a
    long numeric-looking string (an Asana task GID is a perfect
    example: a long run of digits) comes back as a Python int, not a
    str, and calling .strip() on that directly raises AttributeError.
    None becomes an empty string, matching the (x or "") pattern used
    everywhere else in this file for fields that are reliably text."""
    if value is None:
        return ""
    return str(value).strip()


_ASANA_STAGE_NAMES_BY_LOWER = {
    name.lower(): name for name in
    (ASANA_STAGE_SOURCED, ASANA_STAGE_OUTREACH_SENT, ASANA_STAGE_FOLLOWUP,
     ASANA_STAGE_NEGOTIATING, ASANA_STAGE_RIGHTS_SECURED, ASANA_STAGE_DECLINED_DEAD)
}

# Forward progression order of the pipeline. Rights Secured and
# Declined / Dead are deliberately absent — they're terminal,
# manual-only stages already protected separately by
# ASANA_MANUAL_ONLY_STAGES, and "Declined" isn't meaningfully
# "further along" than "Negotiating" anyway.
_ASANA_STAGE_PROGRESSION = (
    ASANA_STAGE_SOURCED, ASANA_STAGE_OUTREACH_SENT, ASANA_STAGE_FOLLOWUP, ASANA_STAGE_NEGOTIATING,
)


def _asana_stage_rank(stage_name: str) -> Optional[int]:
    """Position in the forward pipeline progression, or None for a stage
    that isn't part of it (the terminal manual-only stages, or an
    unrecognized section name a human added to the project)."""
    try:
        return _ASANA_STAGE_PROGRESSION.index(stage_name)
    except ValueError:
        return None


def compute_lead_asana_stage(lead: Dict) -> str:
    """Derives where a lead sits in the Asana creator-outreach pipeline
    from data the email system already tracks — never guesses Rights
    Secured or Declined / Dead on its own, since those are real human
    decisions, not something inferable from send/reply timestamps.
    Checked most-advanced-state-first, so a lead with both a follow-up
    sent AND a reply logged correctly lands on Negotiating, not
    Follow-up.

    A "ManualAsanaStage" value on the lead (any of the six real stage
    names, case-insensitive — e.g. set from the Data tab for a lead
    handled outside the automated pipeline entirely) takes priority
    over every rule below it. This is the one legitimate way to reach
    Rights Secured or Declined / Dead from the Sheet side — including
    for a task that doesn't exist in Asana yet, so a lead you've
    already finalized manually can be created directly into the right
    stage on its very first sync. An unrecognized value is ignored
    (falls through to normal auto-derivation) rather than raising."""
    manual_override = _safe_lead_str(lead.get("ManualAsanaStage"))
    if manual_override:
        matched_stage = _ASANA_STAGE_NAMES_BY_LOWER.get(manual_override.lower())
        if matched_stage:
            return matched_stage
    if (lead.get("ReplyStatus") or "").strip() == "Replied":
        return ASANA_STAGE_NEGOTIATING
    for index in range(1, 5):
        if (lead.get(f"FollowUp{index}SentAt") or "").strip():
            return ASANA_STAGE_FOLLOWUP
    if (lead.get("IntroSentAt") or "").strip():
        return ASANA_STAGE_OUTREACH_SENT
    return ASANA_STAGE_SOURCED


def decide_asana_sync_action(lead: Dict, current_asana_section_name: Optional[str],
                              existing_task_gid: Optional[str] = None) -> Dict:
    """Pure decision logic — no network calls here, which is exactly why
    this is tested exhaustively on its own. Returns
    {"action": "create" | "update", "target_stage": str or None}.

    "create" only ever happens when there's no existing task GID — this,
    plus the caller always writing that GID back immediately after
    creating, is the entire no-duplicates guarantee: re-running sync any
    number of times against the same lead is a no-op for creation,
    every time.

    existing_task_gid: pass this explicitly when the caller has already
    determined the real, current answer (e.g., the lead's own
    AsanaTaskGID turned out to be stale — a 404 from Asana — and the
    caller wants this treated as "no task" for self-healing purposes).
    Defaults to reading straight from the lead's own AsanaTaskGID field
    when not given, for every normal call site.

    target_stage is None in two distinct situations, both meaning
    "leave this task exactly where it is":

    1. The task's current section is a manual-only stage (Rights
       Secured / Declined / Dead) and the lead has NO explicit
       ManualAsanaStage override at all — this is what stops an
       automatic, send/reply-derived recomputation from silently
       reverting a decision a human made directly in Asana.

    2. The task's current section is a manual-only stage, the lead
       DOES have an override, but LastSyncedAsanaStage (what the last
       successful sync actually left the task at) no longer matches
       the live section — meaning someone changed it directly in
       Asana AFTER that last sync, which is demonstrably more recent
       than whenever the override itself was set. An override naming
       some OTHER stage (e.g. Negotiating, set before that later
       direct edit) is treated as stale in this case and does not
       apply.

    An explicit override still always wins everywhere else — including
    moving a task OUT of Rights Secured or Declined/Dead the first
    time it's applied (LastSyncedAsanaStage blank or already matching
    the live section, so there's no evidence of a later edit), and
    including when the override ITSELF names a manual-only stage (a
    deliberate correction between the two terminal states, e.g.
    override=Rights Secured while the task currently sits at
    Declined / Dead) — both cases work exactly as they always have.

    Without the LastSyncedAsanaStage distinction, these two situations
    are indistinguishable from the lead's own fields alone — the same
    (override, current-section) values arise both when the override
    is fresh and should correct a stale Asana state, and when the
    override is the stale one and a later direct Asana edit should
    win. Only knowing what the sync itself last did resolves this
    without breaking one case to fix the other."""
    if existing_task_gid is None:
        existing_task_gid = _safe_lead_str(lead.get("AsanaTaskGID"))
    has_existing_task = bool(existing_task_gid)
    computed_stage = compute_lead_asana_stage(lead)
    if not has_existing_task:
        return {"action": "create", "target_stage": computed_stage}
    manual_override = _safe_lead_str(lead.get("ManualAsanaStage"))
    has_explicit_override = bool(manual_override) and manual_override.lower() in _ASANA_STAGE_NAMES_BY_LOWER
    if not has_explicit_override and current_asana_section_name in ASANA_MANUAL_ONLY_STAGES:
        return {"action": "update", "target_stage": None}

    # The actual reported production case, resolved without breaking
    # the override's own, equally real job of correcting a task that's
    # ALREADY sitting in Rights Secured / Declined: an override was
    # set to Negotiating through the app, successfully applied, and
    # the task was LATER moved directly in Asana to Declined / Dead —
    # a real, deliberate human decision the app has no way to know
    # happened on its own. Both "the override is fresh and should
    # correct the live state" and "the override is stale and the live
    # state is the more recent, correct one" look IDENTICAL from the
    # override and current-section values alone — telling them apart
    # needs something that records what the LAST sync actually did,
    # not just what the lead's own fields currently say.
    # LastSyncedAsanaStage is exactly that: set after every successful
    # sync to whatever stage the task was left at. If the live section
    # now disagrees with it, something changed Asana directly since
    # the last time this ran — and that's demonstrably the more recent
    # action, so it wins over a same-named-but-older override. If
    # LastSyncedAsanaStage is blank (never synced before) or matches
    # the live section (nothing's changed since), there's no evidence
    # of a later direct edit, so the override is trusted exactly as it
    # always has been.
    if has_explicit_override and current_asana_section_name in ASANA_MANUAL_ONLY_STAGES:
        override_is_also_manual_only = (
            _ASANA_STAGE_NAMES_BY_LOWER[manual_override.lower()] in ASANA_MANUAL_ONLY_STAGES
        )
        last_synced = _safe_lead_str(lead.get("LastSyncedAsanaStage"))
        drifted_since_last_sync = bool(last_synced) and last_synced != current_asana_section_name
        if drifted_since_last_sync and not override_is_also_manual_only:
            return {"action": "update", "target_stage": None}

    # Never let an AUTOMATIC computation drag a task BACKWARD through the
    # pipeline. The real, reported case this fixes: the same creator
    # email existing in more than one campaign (e.g. an older completed
    # campaign plus the current active one) with the SAME AsanaTaskGID
    # on both leads' rows — meaning both campaigns' syncs drive the same
    # Asana task, and whichever ran last won. The active campaign would
    # correctly advance the task to Negotiating on a reply, then the
    # older campaign's sync — which knows nothing about that reply, so
    # computes only "Outreach Sent" from its own send data — would drag
    # it straight back. That loop repeats forever, which is exactly why
    # a task kept reverting no matter how many times it was fixed by
    # hand or how many other bugs were fixed upstream.
    #
    # Forward moves and an explicit ManualAsanaStage override are both
    # still fully honored — this only blocks automatic REGRESSION, which
    # is never legitimate: a lead that has genuinely replied does not
    # stop having replied because a different campaign's sheet has no
    # record of it.
    if not has_explicit_override:
        current_rank = _asana_stage_rank(current_asana_section_name)
        computed_rank = _asana_stage_rank(computed_stage)
        if current_rank is not None and computed_rank is not None and computed_rank < current_rank:
            return {"action": "update", "target_stage": None}
    return {"action": "update", "target_stage": computed_stage}


def _match_asana_option(value: str, options: Dict[str, str]) -> Optional[str]:
    """Case-insensitive exact match of a raw Sheet value against an
    Asana enum/multi_enum field's registered option names. No fuzzy
    matching — a value that doesn't cleanly match an existing option is
    skipped rather than guessed at, since silently picking the "closest"
    option could misrepresent a real business fact (e.g. usage rights)."""
    value_lower = value.strip().lower()
    for name, gid in options.items():
        if name.strip().lower() == value_lower:
            return gid
    return None


def _parse_date_to_iso(value_str: str) -> Optional[str]:
    """Converts a date string in any of several common Sheet-typed
    formats to Asana's required ISO 8601 "YYYY-MM-DD". A raw Sheet cell
    holding a date is commonly displayed/exported in the spreadsheet's
    own locale format (e.g. "09/03/26", US-style MM/DD/YY) — Asana's
    API silently rejects (or misinterprets) anything that isn't ISO,
    and the previous approach of just taking the first 10 characters
    only worked for a value that already happened to be ISO-formatted.
    Returns None (field skipped, not guessed at) if nothing matches."""
    value_str = value_str.strip()
    candidate_formats = [
        "%Y-%m-%d",    # already ISO — the common case for a value this
                        # system itself wrote, left as a fast path
        "%m/%d/%Y",    # 09/03/2026 — US-style, 4-digit year
        "%m/%d/%y",    # 09/03/26 — US-style, 2-digit year (the reported case)
        "%d/%m/%Y",    # 03/09/2026 — day-first, 4-digit year
        "%d/%m/%y",    # 03/09/26 — day-first, 2-digit year
        "%B %d, %Y",   # September 3, 2026
        "%b %d, %Y",   # Sep 3, 2026
    ]
    for fmt in candidate_formats:
        try:
            return datetime.strptime(value_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _apply_value_to_asana_field(payload: Dict[str, object], defn: Dict, value_str: str) -> None:
    field_type = defn["type"]
    if field_type == "text":
        payload[defn["gid"]] = value_str
    elif field_type == "number":
        try:
            payload[defn["gid"]] = float(value_str)
        except ValueError:
            pass
    elif field_type == "date":
        iso_date = _parse_date_to_iso(value_str)
        if iso_date:
            payload[defn["gid"]] = {"date": iso_date}
    elif field_type == "enum":
        option_gid = _match_asana_option(value_str, defn["options"])
        if option_gid:
            payload[defn["gid"]] = option_gid
    elif field_type == "multi_enum":
        tokens = [t.strip() for t in value_str.split(",") if t.strip()]
        option_gids = [g for g in (_match_asana_option(t, defn["options"]) for t in tokens) if g]
        if option_gids:
            payload[defn["gid"]] = option_gids


def build_asana_custom_fields_payload(lead: Dict, custom_field_defs: Dict[str, Dict]) -> Dict[str, object]:
    """Maps whatever EXTRA columns a lead has (anything beyond the
    standard outreach-tracking ones in MASTER_COLUMNS) onto the target
    Asana project's ACTUALLY REGISTERED custom fields, matched by name
    (case-insensitive). This is deliberately generic — it works
    unmodified for any campaign's own custom columns and any Asana
    project's own field set, rather than hardcoding one fixed "creator
    outreach" schema into the core engine. A lead column with no
    matching Asana field is silently skipped (not every campaign needs
    every field); an enum/multi_enum value with no matching option is
    also skipped rather than guessed at.

    A small set of Asana field names (_ASANA_FIELD_NAME_FALLBACKS) fall
    back to an equivalent column the email system already has under a
    different name (e.g. Asana's "Creator Email" <- the lead's own
    "Email") — but only when the lead has no column of its own that
    directly matches that Asana field name; an explicit column always
    wins over a fallback.

    custom_field_defs: {field_name: {"gid": str, "type": str,
    "options": {option_name: option_gid}}} — see
    asana_get_project_structure, which builds this from the live
    project."""
    field_defs_by_lower_name = {name.lower(): defn for name, defn in custom_field_defs.items()}
    payload: Dict[str, object] = {}
    matched_field_names_lower = set()

    for key, raw_value in lead.items():
        if key in _ASANA_RESERVED_LEAD_FIELDS:
            continue
        value_str = str(raw_value).strip() if raw_value is not None else ""
        if not value_str:
            continue
        defn = field_defs_by_lower_name.get(key.lower())
        if not defn:
            continue
        _apply_value_to_asana_field(payload, defn, value_str)
        matched_field_names_lower.add(key.lower())

    for target_field_lower, source_column in _ASANA_FIELD_NAME_FALLBACKS.items():
        if target_field_lower in matched_field_names_lower:
            continue
        defn = field_defs_by_lower_name.get(target_field_lower)
        if not defn:
            continue
        raw_value = lead.get(source_column)
        value_str = str(raw_value).strip() if raw_value is not None else ""
        if not value_str:
            continue
        _apply_value_to_asana_field(payload, defn, value_str)

    # "Last Contact Date" specifically also considers the lead's own
    # LastActionAt — the timestamp this system itself updates every
    # time an email actually goes out — taking whichever of that and
    # the Sheet's own manually-typed "Last Contact Date" value is more
    # recent. Without this, an actual send (e.g. a follow-up sent
    # today) would never move this field forward on its own; it would
    # sit frozen at whatever a human last typed into the Sheet,
    # however stale that becomes. This deliberately overrides whatever
    # the generic name-matching above already set for this one field —
    # this logic is authoritative for it specifically.
    last_contact_defn = field_defs_by_lower_name.get("last contact date")
    if last_contact_defn:
        candidate_dates = []
        sheet_value = _safe_lead_str(lead.get("Last Contact Date"))
        if sheet_value:
            parsed = _parse_date_to_iso(sheet_value)
            if parsed:
                candidate_dates.append(parsed)
        last_action_at = _safe_lead_str(lead.get("LastActionAt"))
        if last_action_at:
            candidate_dates.append(last_action_at[:10])
        if candidate_dates:
            _apply_value_to_asana_field(payload, last_contact_defn, max(candidate_dates))

    return payload


def build_asana_task_name(lead: Dict) -> str:
    """[Client] | [CreatorHandle] – [Product], matching the naming
    convention already used in the Creator Outreach Asana project.
    Reserved lead column names for this: "Client", "CreatorHandle",
    "Product" — a campaign wanting a well-formatted task title should
    include those columns; anything present still gets joined, so a
    lead missing one or two of them still gets a reasonable name rather
    than an error. A lead with none of the three falls back to its
    email address, so a task is never created with a blank name.

    Falls back to the "Creator" column for the handle when there's no
    separate "CreatorHandle" column — a real campaign's sheet commonly
    uses "Creator" itself to hold the @handle (the same value that also
    flows into Asana's own "Creator" custom field via the generic
    name-matching in build_asana_custom_fields_payload), rather than a
    second, differently-named column just for the task title."""
    client = _safe_lead_str(lead.get("Client"))
    handle = _safe_lead_str(lead.get("CreatorHandle")) or _safe_lead_str(lead.get("Creator"))
    product = _safe_lead_str(lead.get("Product"))
    if client and handle and product:
        return f"{client} | {handle} \u2013 {product}"
    parts = [p for p in (client, handle, product) if p]
    if parts:
        return " | ".join(parts)
    return _safe_lead_str(lead.get("Email")) or "Unnamed creator"


def _call_with_asana_retries(func, *args, **kwargs):
    """Same principle as _call_with_gspread_retries: a 429 (rate limit)
    or 5xx (Asana's own service hiccup) is worth waiting out; any other
    HTTP error (403, 404, a real auth problem) is raised immediately
    rather than wasting time retrying something that will never
    succeed.

    Also retries network-level failures — a read timeout or connection
    error — which is a REAL, REPORTED gap this closes: those never even
    reach the status-code check below, since the request never got a
    response at all. func() raises requests.exceptions.Timeout /
    ConnectionError directly rather than returning an object with a
    .status_code, so the original version of this wrapper never caught
    them at all — a single transient ~20s read timeout, ordinary
    network jitter across the ~150+ Asana calls one sync run makes
    (several calls per lead, times 30-40 leads), permanently failed
    that one lead for the entire run with zero retry, even though the
    exact same request would very likely succeed a moment later."""
    for attempt in range(ASANA_MAX_RETRIES + 1):
        try:
            resp = func(*args, **kwargs)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            if attempt == ASANA_MAX_RETRIES:
                raise
            time.sleep(ASANA_RETRY_BASE_DELAY_SECONDS * (2 ** attempt))
            continue
        if resp.status_code not in (429, 500, 502, 503, 504):
            return resp
        if attempt == ASANA_MAX_RETRIES:
            return resp
        time.sleep(ASANA_RETRY_BASE_DELAY_SECONDS * (2 ** attempt))
    raise AssertionError("unreachable")  # pragma: no cover - every branch above returns or raises


class AsanaTaskNotFoundError(Exception):
    """Raised specifically for a 404 on a task lookup — Asana's own
    unambiguous signal that a given task GID doesn't exist at all, as
    opposed to any other failure (403, a network error, a real auth
    problem). sync_campaign_to_asana treats this one specific case as
    "the stored AsanaTaskGID is stale — this lead needs a fresh task",
    since a 404 can't mean "exists but access was lost" the way a 403
    could — that ambiguity is exactly why a 403 is NOT treated the same
    way and stays a hard, visible error instead."""
    pass


def _asana_request(method: str, path: str, api_key: str, **kwargs) -> Dict:
    url = f"{ASANA_API_BASE}{path}"
    headers = {"Authorization": f"Bearer {api_key}"}
    resp = _call_with_asana_retries(requests.request, method, url, headers=headers, timeout=20, **kwargs)
    if resp.status_code == 404:
        raise AsanaTaskNotFoundError(f"Asana returned 404 for {method} {path}")
    resp.raise_for_status()
    return resp.json()


def asana_find_project_gid(project_name: str, api_key: str) -> Optional[str]:
    """Asana's own API requires an explicit workspace to list projects —
    there's no "search across everything this token can see" option, and
    calling /projects without one is rejected outright with a 400. This
    searches every workspace the token's owner belongs to (not just the
    first), in case the account spans more than one, and returns the
    first name match found."""
    project_name_lower = project_name.strip().lower()
    workspaces_data = _asana_request("GET", "/workspaces", api_key)
    workspaces = workspaces_data.get("data", [])
    if not workspaces:
        raise RuntimeError(
            "The Asana token has no accessible workspaces — double-check the Personal Access Token "
            "was generated from the correct Asana account."
        )
    for workspace in workspaces:
        data = _asana_request("GET", f"/projects?workspace={workspace['gid']}&opt_fields=name&limit=100", api_key)
        for project in data.get("data", []):
            if project.get("name", "").strip().lower() == project_name_lower:
                return project["gid"]
    return None


def asana_get_project_structure(project_gid: str, api_key: str) -> Dict:
    """Returns {"sections": {name: gid}, "custom_fields": {name: {"gid":
    ..., "type": ..., "options": {option_name: option_gid}}}} — read
    fresh from the live project on every sync run, never cached across
    runs, so a field or section Paula renames or adds in Asana is
    picked up automatically next time."""
    opt_fields = (
        "sections.name,sections.gid,"
        "custom_field_settings.custom_field.name,custom_field_settings.custom_field.gid,"
        "custom_field_settings.custom_field.resource_subtype,"
        "custom_field_settings.custom_field.enum_options.name,"
        "custom_field_settings.custom_field.enum_options.gid"
    )
    data = _asana_request("GET", f"/projects/{project_gid}?opt_fields={opt_fields}", api_key)
    project = data["data"]
    sections = {s["name"]: s["gid"] for s in project.get("sections", [])}
    custom_fields = {}
    for setting in project.get("custom_field_settings", []):
        cf = setting["custom_field"]
        options = {o["name"]: o["gid"] for o in (cf.get("enum_options") or [])}
        custom_fields[cf["name"]] = {"gid": cf["gid"], "type": cf["resource_subtype"], "options": options}
    return {"sections": sections, "custom_fields": custom_fields}


def asana_create_task(project_gid: str, section_gid: str, name: str, custom_fields: Dict, api_key: str,
                       assignee_email: Optional[str] = None) -> str:
    body_data = {"projects": [project_gid], "name": name, "custom_fields": custom_fields}
    if assignee_email:
        body_data["assignee"] = assignee_email
    body = {"data": body_data}
    result = _asana_request("POST", "/tasks", api_key, json=body)
    task_gid = result["data"]["gid"]
    _asana_request("POST", f"/sections/{section_gid}/addTask", api_key, json={"data": {"task": task_gid}})
    return task_gid


def asana_get_task_assignee_email(task_gid: str, api_key: str) -> Optional[str]:
    """Fetches just a task's current assignee email, if any — used to
    decide whether an existing task needs its assignee set on this
    sync (only when currently unassigned; see sync_campaign_to_asana),
    never to overwrite a different assignee a human deliberately set."""
    data = _asana_request("GET", f"/tasks/{task_gid}?opt_fields=assignee.email", api_key)
    return (data["data"].get("assignee") or {}).get("email")


def asana_update_task(task_gid: str, name: str, custom_fields: Dict, api_key: str,
                       assignee_email: Optional[str] = None) -> None:
    """Updates an existing task's name and/or custom fields, and
    optionally its assignee. The name is kept in sync on every update,
    not just set once at creation — the naming convention is derived
    entirely from the Sheet's own data, so a task's title should never
    permanently drift from what that data now says, the same way its
    custom fields don't. This is also what self-heals any task created
    before build_asana_task_name correctly picked up the "Creator"
    column as a fallback for the handle — the next sync corrects the
    name automatically, with no manual renaming needed in Asana.

    assignee_email, when given, is only ever passed by the caller when
    the task is confirmed to currently have NO assignee at all — this
    function itself doesn't check that; see sync_campaign_to_asana."""
    data = {"name": name}
    if custom_fields:
        data["custom_fields"] = custom_fields
    if assignee_email:
        data["assignee"] = assignee_email
    _asana_request("PUT", f"/tasks/{task_gid}", api_key, json={"data": data})


def asana_move_task_to_section(task_gid: str, section_gid: str, api_key: str) -> None:
    _asana_request("POST", f"/sections/{section_gid}/addTask", api_key, json={"data": {"task": task_gid}})


def asana_get_task_current_section(task_gid: str, project_gid: str, api_key: str) -> Optional[str]:
    data = _asana_request("GET", f"/tasks/{task_gid}?opt_fields=memberships.section.name,memberships.project.gid",
                           api_key)
    for membership in data["data"].get("memberships", []):
        if (membership.get("project") or {}).get("gid") == project_gid:
            return (membership.get("section") or {}).get("name")
    return None


def asana_get_task_manual_state(task_gid: str, project_gid: str, api_key: str) -> Dict[str, Optional[str]]:
    """Fetches a task's current LIVE state relevant to reverse-syncing a
    decision made directly in Asana (moving a task to Rights Secured /
    Declined, or setting its Rights Expiration) to the Creator Tracker
    sheet — its current section within the given project, and its
    "Rights Expiration" custom field value as an ISO date string, if
    set. This is a SEPARATE, dedicated call from
    asana_get_task_current_section (used for the ordinary create/update
    sync decision) — called only when Creator Tracker sync also needs
    to reflect a purely-Asana-side decision that was never also typed
    into ManualAsanaStage on the Sheet.

    Returns {"section": str or None, "rights_expiration": str or None}."""
    data = _asana_request(
        "GET",
        f"/tasks/{task_gid}?opt_fields=memberships.section.name,memberships.project.gid,"
        f"custom_fields.name,custom_fields.date_value",
        api_key,
    )
    task = data["data"]
    section = None
    for membership in task.get("memberships", []):
        if (membership.get("project") or {}).get("gid") == project_gid:
            section = (membership.get("section") or {}).get("name")
            break
    rights_expiration = None
    for field in task.get("custom_fields", []):
        if (field.get("name") or "").strip().lower() == "rights expiration":
            date_value = field.get("date_value")
            if date_value and date_value.get("date"):
                rights_expiration = date_value["date"]
            break
    return {"section": section, "rights_expiration": rights_expiration}


def asana_get_task_blank_custom_field_gids(task_gid: str, api_key: str) -> set:
    """The set of custom field GIDs that are CURRENTLY BLANK (unset) on
    this task right now, read live from Asana.

    display_value is used rather than any type-specific representation
    (text_value, number_value, enum value, a date's own value dict,
    etc.) because it is the one field Asana returns for every custom
    field type alike, and it comes back empty/None exactly when that
    field has no value set — regardless of whether the field is text,
    number, date, enum, or multi_enum. That uniformity is what lets the
    caller filter a payload down to "fields currently blank" without
    needing type-specific logic of its own.

    Deliberately a separate, on-demand call rather than always fetched
    — see ASANA_FILL_BLANKS_ONLY_STAGES's own comment: only a lead
    whose LIVE section is already Negotiating or later ever needs this,
    which is a small minority of leads on any given sync run, so this
    avoids adding an extra Asana API call for every other lead."""
    data = _asana_request(
        "GET", f"/tasks/{task_gid}?opt_fields=custom_fields.gid,custom_fields.display_value", api_key,
    )
    task = data["data"]
    blank_gids = set()
    for field in task.get("custom_fields", []):
        gid = field.get("gid")
        if gid and not field.get("display_value"):
            blank_gids.add(gid)
    return blank_gids


def filter_custom_fields_to_blanks_only(custom_fields: Dict[str, object], blank_gids: set) -> Dict[str, object]:
    """Keeps only the entries of an outgoing custom-fields payload whose
    Asana field is CURRENTLY blank — see
    asana_get_task_blank_custom_field_gids for how blank_gids is
    determined, and ASANA_FILL_BLANKS_ONLY_STAGES for when this applies
    at all. A field the task already has some value for (set by an
    earlier sync, or typed directly into Asana by a human) is left
    completely untouched: dropped from the payload before it's sent,
    not overwritten with the Sheet's version and not cleared."""
    return {gid: value for gid, value in custom_fields.items() if gid in blank_gids}


def _lead_content_key(lead: Dict) -> Optional[str]:
    """A normalized key identifying WHICH creator video a lead is
    actually about, from whichever of Video File / Refunnel Link the
    lead has. Prefers the extracted TikTok video ID (shared reliably by
    both fields, per _extract_tiktok_video_id's own docstring) and
    falls back to the raw, lowercased, stripped URL string when no ID
    can be extracted from either — still useful as an exact-match
    safety net even without an ID. Returns None when the lead has
    neither field populated at all, which must never be treated as a
    "match" against anything."""
    video_file = (lead.get(TRACKER_COLUMN_VIDEO_FILE) or "").strip()
    refunnel_link = (lead.get(TRACKER_COLUMN_REFUNNEL_LINK) or "").strip()
    for raw in (video_file, refunnel_link):
        if raw:
            video_id = _extract_tiktok_video_id(raw)
            if video_id:
                return video_id
    for raw in (video_file, refunnel_link):
        if raw:
            return raw.lower()
    return None


def asana_build_existing_task_content_index(project_gid: str, structure: Dict, api_key: str) -> Dict[str, str]:
    """{content_key: task_gid} for every task already in the project
    that has a Video File or Refunnel Link value set — the actual,
    concrete safeguard requested after repeated duplicate-task
    incidents: even if a task's GID is somehow never recorded back to
    the Sheet again in the future (whatever the reason turns out to be
    next time), a lead whose video content matches a task that already
    exists gets reattached to that existing task instead of spawning
    another duplicate. This is a second, independent layer — it works
    even when the Sheet's own AsanaTaskGID tracking has failed, which
    is exactly the scenario that made every existing safeguard useless
    in the incident this was built for.

    Only fetched when the project actually has one of these two custom
    fields configured at all — skipped entirely otherwise, so a project
    that doesn't use them pays no extra cost on every sync run.

    Paginated: a project can hold far more tasks than one page returns,
    and every one of them needs checking, not just the first page.

    When more than one existing task shares the same content key
    (shouldn't normally happen, but a prior duplication incident could
    easily have produced exactly this), the OLDEST (first created) task
    wins — that's the original, and the more likely one already carries
    real progress/history worth keeping."""
    video_field_gid = structure["custom_fields"].get(TRACKER_COLUMN_VIDEO_FILE, {}).get("gid")
    refunnel_field_gid = structure["custom_fields"].get(TRACKER_COLUMN_REFUNNEL_LINK, {}).get("gid")
    if not video_field_gid and not refunnel_field_gid:
        return {}

    opt_fields = "created_at,custom_fields.gid,custom_fields.display_value"
    index: Dict[str, str] = {}
    index_created_at: Dict[str, str] = {}
    offset = None
    while True:
        path = f"/tasks?project={project_gid}&opt_fields={opt_fields}&limit=100"
        if offset:
            path += f"&offset={offset}"
        data = _asana_request("GET", path, api_key)
        for task in data.get("data", []):
            content_value = None
            for field in task.get("custom_fields", []):
                if field.get("gid") in (video_field_gid, refunnel_field_gid) and field.get("display_value"):
                    content_value = field["display_value"]
                    break
            if not content_value:
                continue
            key = _extract_tiktok_video_id(content_value) or content_value.strip().lower()
            created_at = task.get("created_at", "")
            if key not in index or created_at < index_created_at.get(key, ""):
                index[key] = task["gid"]
                index_created_at[key] = created_at
        next_page = data.get("next_page")
        if not next_page:
            break
        offset = next_page.get("offset")
        if not offset:
            break
    return index


def sync_campaign_to_asana(sheets: SheetsConnector, campaign_cfg: Dict, api_key: str,
                            default_assignee_email: Optional[str] = None,
                            leads: Optional[List[Dict]] = None) -> Dict:
    """The one entry point. Reads every lead, creates or updates its
    Asana task, and writes the resulting AsanaTaskGID back to the Sheet
    for any newly-created task — that write-back, checked before every
    future sync, is what makes re-running this any number of times safe
    rather than creating duplicate tasks. Never moves a task out of
    Rights Secured or Declined / Dead once a human has put it there
    (see decide_asana_sync_action). One lead's failure never blocks the
    rest of the sync — collected in errors instead.

    default_assignee_email, when given, is set on every new task at
    creation, and backfilled onto any existing task that currently has
    NO assignee at all — never overwriting a different assignee a
    human deliberately set, on either a new or an existing task, since
    that would be reversing a real decision rather than filling in a
    blank one.

    leads, when given, is used directly instead of calling
    sheets.get_all_leads() internally — this exists so a single caller
    that also runs Creator Tracker sync and/or
    collect_asana_manual_state_for_leads for the SAME campaign in the
    SAME pass (see cmd_sync_asana / cmd_sync_asana_all) can fetch the
    Sheet once and reuse it across all three, rather than each one
    independently re-reading the exact same data — a real, reported
    contributor to hitting Google's per-minute Sheets API read quota.
    Omit it (the default) and this reads fresh, exactly as before.

    campaign_cfg["asana"]: {"enabled": bool, "project_name": str}."""
    asana_settings = campaign_cfg.get("asana") or {}
    if not asana_settings.get("enabled"):
        return {"skipped_disabled": True, "created": 0, "updated": 0, "reused_existing": 0, "skipped_no_email": 0, "errors": []}

    project_name = (asana_settings.get("project_name") or "").strip()
    if not project_name:
        raise RuntimeError("Asana sync is enabled for this campaign but no project_name is configured.")

    project_gid = asana_find_project_gid(project_name, api_key)
    if not project_gid:
        raise RuntimeError(f"No Asana project named '{project_name}' was found in this workspace.")
    structure = asana_get_project_structure(project_gid, api_key)
    # Second, independent safeguard against duplicate tasks — see its own
    # docstring. Built once per sync run, not per lead.
    existing_task_by_content = asana_build_existing_task_content_index(project_gid, structure, api_key)

    created = 0
    updated = 0
    reused_existing = 0
    skipped_no_email = 0
    errors = []

    for lead in (leads if leads is not None else sheets.get_all_leads()):
        email = (lead.get("Email") or "").strip()
        if not email:
            skipped_no_email += 1
            continue
        try:
            existing_gid = _safe_lead_str(lead.get("AsanaTaskGID"))
            current_section_name = None
            if existing_gid:
                try:
                    current_section_name = asana_get_task_current_section(existing_gid, project_gid, api_key)
                except AsanaTaskNotFoundError:
                    # The stored GID points at a task Asana no longer has
                    # any record of — self-heal by treating this lead as
                    # if it never had a task, rather than failing forever.
                    # A 403 here is deliberately NOT caught the same way:
                    # it could mean the task still exists but access was
                    # lost, and blindly creating a second one in that case
                    # would be a real, visible duplicate in Asana.
                    existing_gid = ""

            decision = decide_asana_sync_action(lead, current_section_name, existing_task_gid=existing_gid)
            custom_fields = build_asana_custom_fields_payload(lead, structure["custom_fields"])

            # Transparency for a real, reported point of confusion: an
            # explicit ManualAsanaStage override always wins over
            # whatever a task's LIVE Asana section currently is — by
            # design, since this is also what stops the multi-campaign
            # regression bug (the same creator's email in an older
            # campaign's sheet dragging a task backward with no
            # awareness of a reply the ACTIVE campaign already saw).
            # But that means a task moved directly in Asana — dragged
            # to Rights Secured by hand, say — silently reverts on the
            # very next sync if this lead's own sheet row still has a
            # stale override left over from earlier, with nothing
            # anywhere explaining why. Logged only when the override is
            # actually the reason the task is about to move away from
            # where it currently, live, sits — not on every sync of
            # every overridden lead.
            manual_override_value = _safe_lead_str(lead.get("ManualAsanaStage"))
            if (decision["action"] == "update" and manual_override_value
                    and decision["target_stage"] and current_section_name
                    and decision["target_stage"] != current_section_name):
                print(f"  {lead.get('Email', '?')}: Asana currently shows '{current_section_name}', "
                      f"but this lead's own ManualAsanaStage override is still set to "
                      f"'{manual_override_value}' — moving it back. If '{current_section_name}' "
                      f"was set on purpose (e.g. a direct Asana edit), update or clear this lead's "
                      f"override to match via \"Manage a lead\" so it stops being overridden.")

            if decision["action"] == "create":
                # Before creating anything, check whether a task for this
                # EXACT video already exists — the actual second-layer
                # safeguard: even if this lead's own AsanaTaskGID was
                # never correctly recorded (whatever the reason), reusing
                # a task that already covers the same content stops a
                # fresh duplicate from being created for it.
                content_key = _lead_content_key(lead)
                reused_gid = existing_task_by_content.get(content_key) if content_key else None
                if reused_gid:
                    sheets.update_lead_fields(lead["_row"], {"AsanaTaskGID": reused_gid})
                    reused_existing += 1
                else:
                    target_section_gid = structure["sections"].get(decision["target_stage"])
                    if not target_section_gid:
                        raise RuntimeError(f"Asana project has no section named '{decision['target_stage']}'.")
                    new_gid = asana_create_task(project_gid, target_section_gid, build_asana_task_name(lead),
                                                 custom_fields, api_key, assignee_email=default_assignee_email)
                    # LastSyncedAsanaStage written in the same call —
                    # see its own comment in MASTER_COLUMNS and
                    # decide_asana_sync_action for why this matters.
                    sheets.update_lead_fields(lead["_row"], {"AsanaTaskGID": new_gid,
                                                              "LastSyncedAsanaStage": decision["target_stage"]})
                    created += 1
            else:
                assignee_to_set = None
                if default_assignee_email:
                    current_assignee = asana_get_task_assignee_email(existing_gid, api_key)
                    if not current_assignee:
                        assignee_to_set = default_assignee_email
                # Once a task's LIVE section is Negotiating or later, a
                # human is expected to be entering real, specific terms
                # directly in Asana — see ASANA_FILL_BLANKS_ONLY_STAGES's
                # own comment for the exact reported case (a manually-set
                # Rights Expiration date being silently overwritten back
                # to the Sheet's older value on every sync). From this
                # point on, sync can still fill in a field that's
                # genuinely still blank, but never overwrites one that
                # already has a value, whether that value came from an
                # earlier sync or was typed by a human.
                if current_section_name in ASANA_FILL_BLANKS_ONLY_STAGES and custom_fields:
                    blank_gids = asana_get_task_blank_custom_field_gids(existing_gid, api_key)
                    custom_fields = filter_custom_fields_to_blanks_only(custom_fields, blank_gids)
                asana_update_task(existing_gid, build_asana_task_name(lead), custom_fields, api_key,
                                   assignee_email=assignee_to_set)
                target_stage = decision["target_stage"]
                if target_stage and target_stage != current_section_name:
                    target_section_gid = structure["sections"].get(target_stage)
                    if not target_section_gid:
                        # The actual reported bug: this used to silently
                        # skip the move with no error at all if the
                        # project had no section by this exact name —
                        # the sync would report "updated" and nothing
                        # would visibly change, indistinguishable from a
                        # genuine no-op. Now it's a real, visible error,
                        # matching what the create path already does.
                        raise RuntimeError(
                            f"Asana project has no section named '{target_stage}' — cannot move task "
                            f"there. Available sections: {sorted(structure['sections'].keys())}"
                        )
                    asana_move_task_to_section(existing_gid, target_section_gid, api_key)
                # The stage this sync actually leaves the task at —
                # the newly-moved-to one, or current_section_name
                # unchanged when target_stage was None (frozen).
                # Written only when it's actually different from what's
                # already recorded, to avoid a write on every single
                # steady-state run where nothing changed. See
                # LastSyncedAsanaStage's own comment in MASTER_COLUMNS
                # and decide_asana_sync_action for why this exists.
                final_stage = target_stage or current_section_name
                if final_stage and _safe_lead_str(lead.get("LastSyncedAsanaStage")) != final_stage:
                    sheets.update_lead_fields(lead["_row"], {"LastSyncedAsanaStage": final_stage})
                updated += 1
        except Exception as exc:  # noqa: BLE001 - one bad lead shouldn't sink the whole sync
            errors.append({"email": email, "error": str(exc)})

    return {"created": created, "updated": updated, "reused_existing": reused_existing, "skipped_no_email": skipped_no_email, "errors": errors}


# =============================================================================
# Creator Tracker sheet sync — a SEPARATE, much larger (3000+ row) external
# Google Sheet shared across every campaign, maintained by a different daily
# process. This system's ONLY job here is to keep one column
# (TRACKER_COLUMN_CONTACT_STATUS) in sync with each lead's own pipeline
# stage — never creating rows, never touching any other column, and never
# reversing a human decision (Rights Secured / Declined / Dead) back to an
# earlier stage, exactly like the same guarantee for Asana sync.
# =============================================================================

# Column names as they appear in the Creator Tracker sheet's own header —
# these are ASSUMPTIONS based on how the sheet was described, not confirmed
# against the real sheet directly. If a sync run reports every lead as "no
# matching row found", start by checking these against the sheet's actual
# column headers and correcting them here.
TRACKER_COLUMN_EMAIL = "Creator Email"
TRACKER_COLUMN_CREATOR_NAME = "Creator"
TRACKER_COLUMN_CONTACT_STATUS = "Contact Status"
TRACKER_COLUMN_LAST_CONTACTED_DATE = "Last Contacted Date"
TRACKER_COLUMN_VIDEO_FILE = "Video File"
TRACKER_COLUMN_REFUNNEL_LINK = "Refunnel Link"
TRACKER_COLUMN_RIGHTS_DURATION = "Rights Duration"

TRACKER_STATUS_SOURCED = "Sourced"
TRACKER_STATUS_OUTREACHED = "Outreached (Intro Sent)"
TRACKER_STATUS_FOLLOWUP = "Follow up"
TRACKER_STATUS_NEGOTIATING = "Negotiating"
TRACKER_STATUS_RIGHTS_SECURED = "Rights Secured"
TRACKER_STATUS_DECLINED_DEAD = "Declined / Dead"
# Same idea as ASANA_MANUAL_ONLY_STAGES — these two are always a human
# decision; a sync run must never set OR move a row into or out of either
# one on its own.
TRACKER_MANUAL_ONLY_STATUSES = {TRACKER_STATUS_RIGHTS_SECURED, TRACKER_STATUS_DECLINED_DEAD}

# Forward progression of tracker statuses — the tracker-vocabulary
# equivalent of _ASANA_STAGE_PROGRESSION, and it exists for exactly the
# same reason. See _asana_stage_rank and decide_asana_sync_action's own
# no-regression comment: the same creator email living in more than one
# campaign means several campaigns' syncs write to the SAME tracker row,
# and a campaign with no record of a reply would otherwise drag a row
# that correctly reached Negotiating straight back to "Outreached
# (Intro Sent)" on its next run, forever.
_TRACKER_STATUS_PROGRESSION = (
    TRACKER_STATUS_SOURCED, TRACKER_STATUS_OUTREACHED, TRACKER_STATUS_FOLLOWUP, TRACKER_STATUS_NEGOTIATING,
)


def _tracker_status_rank(status_name: str) -> Optional[int]:
    """Position in the forward tracker-status progression, or None for a
    status outside it (the terminal manual-only statuses, a blank cell,
    or any other label a human typed into the column by hand)."""
    try:
        return _TRACKER_STATUS_PROGRESSION.index(status_name)
    except ValueError:
        return None


# Translates the shared internal Asana-stage vocabulary to this tracker
# sheet's own label set — same underlying pipeline, computed once by
# compute_lead_asana_stage, shown under two different downstream label
# vocabularies (Asana's and this sheet's) rather than two independent
# stage-derivation implementations that could drift apart from each other.
_ASANA_STAGE_TO_TRACKER_STATUS = {
    ASANA_STAGE_SOURCED: TRACKER_STATUS_SOURCED,
    ASANA_STAGE_OUTREACH_SENT: TRACKER_STATUS_OUTREACHED,
    ASANA_STAGE_FOLLOWUP: TRACKER_STATUS_FOLLOWUP,
    ASANA_STAGE_NEGOTIATING: TRACKER_STATUS_NEGOTIATING,
    ASANA_STAGE_RIGHTS_SECURED: TRACKER_STATUS_RIGHTS_SECURED,
    ASANA_STAGE_DECLINED_DEAD: TRACKER_STATUS_DECLINED_DEAD,
}


def compute_lead_tracker_status(lead: Dict) -> str:
    """Same underlying pipeline as compute_lead_asana_stage — including
    the SAME ManualAsanaStage override for Rights Secured / Declined /
    Dead, so a single human decision is reflected consistently in both
    Asana and the Creator Tracker sheet, rather than needing two
    independent overrides that could disagree with each other —
    translated to this tracker sheet's own label vocabulary."""
    stage = compute_lead_asana_stage(lead)
    return _ASANA_STAGE_TO_TRACKER_STATUS[stage]


def build_tracker_row_index(tracker_rows: List[Dict]) -> Tuple[Dict[str, List[int]], Dict[str, List[int]]]:
    """Builds {email_lower: [row_numbers]} and {name_lower: [row_numbers]}
    lookups from the tracker sheet's own current rows (each dict must
    include '_row'). BOTH map to a LIST, not a single row — a creator
    can legitimately appear more than once (contacted separately for
    different videos), and email collisions are just as real as name
    collisions across a sheet spanning thousands of rows and many
    campaigns. An earlier version of this function let a later row
    silently overwrite an earlier one in the email index, meaning an
    email match against more than one row was never even detected,
    just silently resolved to whichever row happened to be read last —
    see find_tracker_row_for_lead, which now disambiguates or reports
    ambiguity for BOTH email and name matches rather than guessing."""
    email_index: Dict[str, List[int]] = {}
    name_index: Dict[str, List[int]] = {}
    for row in tracker_rows:
        email = (row.get(TRACKER_COLUMN_EMAIL) or "").strip().lower()
        if email:
            email_index.setdefault(email, []).append(row["_row"])
        name = (row.get(TRACKER_COLUMN_CREATOR_NAME) or "").strip().lower()
        if name:
            name_index.setdefault(name, []).append(row["_row"])
    return email_index, name_index


def _extract_tiktok_video_id(value: str) -> Optional[str]:
    """Extracts the numeric TikTok video ID from either a long-form
    Video File URL (tiktok.com/@handle/video/1234567890123456789) or a
    Refunnel Link, which embeds the same ID as tk_1234567890123456789
    somewhere in its own URL regardless of the exact query-param
    format around it. This is the one thing a long-form Video File and
    a Refunnel Link reliably share even when the Video File itself is
    stored as TikTok's short, opaque share link
    (tiktok.com/t/AbCdEfG/) — that format doesn't contain the numeric
    ID at all, which is exactly the real, confirmed case that broke
    exact-string disambiguation for several leads whose Video File
    happened to be recorded as a short link while their matching
    tracker row (or vice versa) used the long form. Returns None if no
    ID-shaped substring is found in either known pattern."""
    match = re.search(r"/video/(\d{6,})", value) or re.search(r"tk_(\d{6,})", value)
    return match.group(1) if match else None


def _disambiguate_tracker_candidates(lead: Dict, candidate_rows: List[int],
                                      tracker_records_by_row: Dict[int, Dict]) -> Optional[int]:
    """When an email or name match finds MORE than one tracker row —
    the same creator appearing more than once, e.g. contacted
    separately for different videos — narrows down using Video File /
    Refunnel Link: whichever of the two the lead itself has, checked
    against each candidate row's own value for that same column.

    Tries an exact string match first; if that doesn't resolve it,
    falls back to comparing the underlying TikTok video ID extracted
    from either field (see _extract_tiktok_video_id) — this is what
    makes disambiguation robust to the two sheets recording the exact
    same video under a different link format from each other (a short
    share link on one side, the long-form URL on the other), which a
    raw string comparison alone can never resolve no matter how
    correct the underlying data actually is.

    Exactly one candidate matching either way is accepted as the
    correct row; zero matches, or more than one, stays genuinely
    ambiguous — returns None rather than guessing."""
    lead_video_file = _safe_lead_str(lead.get(TRACKER_COLUMN_VIDEO_FILE)).strip().lower()
    lead_refunnel_link = _safe_lead_str(lead.get(TRACKER_COLUMN_REFUNNEL_LINK)).strip().lower()
    if not lead_video_file and not lead_refunnel_link:
        return None  # nothing on the lead side to disambiguate with

    lead_video_id = _extract_tiktok_video_id(lead_video_file) or _extract_tiktok_video_id(lead_refunnel_link)

    matching_rows = []
    for row_number in candidate_rows:
        row = tracker_records_by_row[row_number]
        row_video_file = _safe_lead_str(row.get(TRACKER_COLUMN_VIDEO_FILE)).strip().lower()
        row_refunnel_link = _safe_lead_str(row.get(TRACKER_COLUMN_REFUNNEL_LINK)).strip().lower()
        if (lead_video_file and lead_video_file == row_video_file) or \
           (lead_refunnel_link and lead_refunnel_link == row_refunnel_link):
            matching_rows.append(row_number)
            continue
        if lead_video_id:
            row_video_id = _extract_tiktok_video_id(row_video_file) or _extract_tiktok_video_id(row_refunnel_link)
            if row_video_id and row_video_id == lead_video_id:
                matching_rows.append(row_number)

    if len(matching_rows) == 1:
        return matching_rows[0]
    return None


def find_tracker_row_for_lead(lead: Dict, email_index: Dict[str, List[int]],
                               name_index: Dict[str, List[int]],
                               tracker_records_by_row: Dict[int, Dict]) -> Dict:
    """Tries EMAIL first (reliable when present), falls back to NAME —
    matching this tracker sheet's own mixed reliability, where either
    column can be blank for a given row. Tries the lead's own
    Creator/CreatorHandle column first (the tracker's own "Creator"
    column holds the @handle, the same vocabulary this system already
    uses elsewhere), then its full name (FirstName + LastName) as a
    second name-based attempt.

    Either an email or a name match can legitimately find MORE than
    one tracker row — the same creator contacted separately for
    different videos is a real, expected case, not an error. When that
    happens, Video File / Refunnel Link (whichever the lead itself
    has) is checked against each candidate row's own values to narrow
    down to the one exact row — see _disambiguate_tracker_candidates.
    Only if that STILL can't narrow to exactly one row is it reported
    as genuinely ambiguous and never guessed at — silently picking one
    could update the wrong creator's status in a sheet spanning
    thousands of unrelated rows across every other campaign too.

    Returns {"row": int or None, "matched_by": str or None,
    "ambiguous": bool}."""
    email = _safe_lead_str(lead.get("Email")).lower()
    if email:
        candidates = email_index.get(email, [])
        if len(candidates) == 1:
            return {"row": candidates[0], "matched_by": "email", "ambiguous": False}
        if len(candidates) > 1:
            disambiguated = _disambiguate_tracker_candidates(lead, candidates, tracker_records_by_row)
            if disambiguated is not None:
                return {"row": disambiguated, "matched_by": "email+video/refunnel", "ambiguous": False}
            return {"row": None, "matched_by": None, "ambiguous": True}

    candidate_names = []
    handle = (_safe_lead_str(lead.get("Creator")) or _safe_lead_str(lead.get("CreatorHandle"))).lower()
    if handle:
        candidate_names.append(handle)
    full_name = f"{lead.get('FirstName', '')} {lead.get('LastName', '')}".strip().lower()
    if full_name:
        candidate_names.append(full_name)

    for candidate in candidate_names:
        matches = name_index.get(candidate, [])
        if len(matches) == 1:
            return {"row": matches[0], "matched_by": "name", "ambiguous": False}
        if len(matches) > 1:
            disambiguated = _disambiguate_tracker_candidates(lead, matches, tracker_records_by_row)
            if disambiguated is not None:
                return {"row": disambiguated, "matched_by": "name+video/refunnel", "ambiguous": False}
            return {"row": None, "matched_by": None, "ambiguous": True}

    return {"row": None, "matched_by": None, "ambiguous": False}


def _build_tracker_records_from_values(all_values: List[List[str]]) -> Tuple[List[str], List[Dict]]:
    """Builds (header, records) from raw worksheet values instead of
    gspread's get_all_records().

    get_all_records() raises outright if the header row contains any
    duplicate column name ("the header row in the worksheet is not
    unique"), which is a real, confirmed production failure: the
    Creator Tracker is a large, long-lived sheet maintained by hand and
    by a separate daily process, and it genuinely does have repeated
    headers (commonly a blank trailing column repeated several times,
    or a column added twice). That aborted the ENTIRE tracker sync for
    every lead, even though every column this sync actually reads or
    writes was present and unambiguous.

    Duplicates are resolved by taking the FIRST occurrence of each
    column name — matching the column index this module already
    computes via header.index(), so record lookups and cell writes
    always refer to the same physical column. Blank header cells are
    skipped entirely rather than becoming a "" key."""
    if not all_values:
        return [], []
    header = all_values[0]
    seen = {}
    for i, name in enumerate(header):
        cleaned = (name or "").strip()
        if cleaned and cleaned not in seen:
            seen[cleaned] = i
    records = []
    for row in all_values[1:]:
        record = {}
        for name, i in seen.items():
            record[name] = row[i] if i < len(row) else ""
        records.append(record)
    return header, records


def sync_campaign_to_tracker_sheet(sheets: SheetsConnector, campaign_cfg: Dict, tracker_ws,
                                    asana_manual_state_by_email: Optional[Dict[str, Dict]] = None,
                                    leads: Optional[List[Dict]] = None) -> Dict:
    """The Creator Tracker sheet equivalent of sync_campaign_to_asana —
    same stage-derivation, same "never reverse a manual decision"
    guarantee — applied to a completely separate, much larger sheet
    shared across every campaign.

    Never creates a row (unlike Asana task creation), and never
    touches any column other than Contact Status, Last Contacted Date,
    and Rights Duration — this sheet's rows and every other column are
    owned and populated by a different daily process. A lead with no
    matching row, or an ambiguous name match, is reported as a warning
    for review — never silently skipped, and never guessed at.

    asana_manual_state_by_email, when given, is
    {email: {"section": str or None, "rights_expiration": str or
    None}} — collected separately by collect_asana_manual_state_for_leads
    (only possible when Asana sync also ran for this campaign, since it
    requires reading Asana's own live state). When a lead's own
    ManualAsanaStage column on the Sheet is BLANK, but Asana's live
    section for that lead's task is Rights Secured or Declined / Dead,
    that live section is used instead — this is what lets a decision
    made directly in Asana (dragging a card to a different section)
    reach the Tracker sheet without ALSO requiring the same decision to
    be typed into ManualAsanaStage. A lead's own ManualAsanaStage,
    when set, always takes precedence over Asana's live state — an
    explicit Sheet-side decision is never silently overridden by
    whatever a task's section in Asana happens to say. Rights
    Expiration, when present in this dict, populates Rights Duration
    the same way Last Contacted Date is populated from LastActionAt:
    never overwritten with something older or blank.

    leads, when given, is used directly instead of calling
    sheets.get_all_leads() internally — see sync_campaign_to_asana's
    own docstring for why: this avoids a single sync pass reading the
    exact same Sheet data 2-3 times over, once per function, which was
    a real, reported contributor to hitting Google's Sheets API read
    quota. Omit it (the default) and this reads fresh, exactly as
    before.

    campaign_cfg["tracker_sync"]: {"enabled": bool}."""
    tracker_settings = campaign_cfg.get("tracker_sync") or {}
    if not tracker_settings.get("enabled"):
        return {"skipped_disabled": True, "updated": 0, "unchanged": 0, "warnings": []}
    asana_manual_state_by_email = asana_manual_state_by_email or {}

    # Read raw values once and build both the header and the records
    # from them — get_all_records() cannot handle a header row with
    # duplicate column names (see _build_tracker_records_from_values),
    # which this sheet genuinely has. This also replaces what used to be
    # a separate row_values(1) call, so it's one API read instead of two.
    all_values = tracker_ws.get_all_values()
    header, tracker_records = _build_tracker_records_from_values(all_values)
    missing_columns = [c for c in (TRACKER_COLUMN_CONTACT_STATUS, TRACKER_COLUMN_LAST_CONTACTED_DATE,
                                    TRACKER_COLUMN_RIGHTS_DURATION) if c not in header]
    if missing_columns:
        raise RuntimeError(
            f"Creator Tracker sheet is missing column(s): {missing_columns}. Found: {header}"
        )
    status_col_index = header.index(TRACKER_COLUMN_CONTACT_STATUS) + 1
    last_contacted_col_index = header.index(TRACKER_COLUMN_LAST_CONTACTED_DATE) + 1
    rights_duration_col_index = header.index(TRACKER_COLUMN_RIGHTS_DURATION) + 1

    tracker_rows = []
    for i, record in enumerate(tracker_records, start=2):  # row 1 is header
        record["_row"] = i
        tracker_rows.append(record)
    tracker_records_by_row = {r["_row"]: r for r in tracker_rows}
    email_index, name_index = build_tracker_row_index(tracker_rows)

    updated = 0
    unchanged = 0
    warnings = []

    for lead in (leads if leads is not None else sheets.get_all_leads()):
        identifier = _safe_lead_str(lead.get("Email")) or _safe_lead_str(lead.get("FirstName")) or "(no email/name)"
        match = find_tracker_row_for_lead(lead, email_index, name_index, tracker_records_by_row)

        if match["ambiguous"]:
            warnings.append(f"{identifier}: matched more than one Creator Tracker row by name — "
                             "skipped, needs manual review.")
            continue
        if match["row"] is None:
            warnings.append(f"{identifier}: no matching row found in the Creator Tracker sheet.")
            continue

        row_number = match["row"]
        tracker_row = tracker_records_by_row[row_number]
        current_status = _safe_lead_str(tracker_row.get(TRACKER_COLUMN_CONTACT_STATUS))
        manual_override_on_sheet = _safe_lead_str(lead.get("ManualAsanaStage"))
        if not manual_override_on_sheet and current_status in TRACKER_MANUAL_ONLY_STATUSES:
            # A human decision — the whole row is frozen from this sync's
            # perspective from here on, including Last Contacted Date
            # and Rights Duration. Checked BEFORE freezing, not after: an
            # explicit ManualAsanaStage override must still be able to
            # move a row that's already at a manual-only status (e.g.
            # correcting a decline, or finalizing after more
            # negotiation) — freezing unconditionally on current_status
            # alone, with no override check at all, meant a row could
            # never be moved again once it reached Rights Secured or
            # Declined, contradicting the very design this docstring
            # describes ("An explicit ManualAsanaStage is a deliberate
            # human decision and is never blocked" — true everywhere
            # else in this function except this one early exit).
            unchanged += 1
            continue

        row_changed = False

        new_status = compute_lead_tracker_status(lead)
        lead_email_lower = _safe_lead_str(lead.get("Email")).lower()
        asana_state = asana_manual_state_by_email.get(lead_email_lower) if lead_email_lower else None
        if not manual_override_on_sheet and asana_state:
            asana_section = asana_state.get("section")
            translated = _ASANA_STAGE_TO_TRACKER_STATUS.get(asana_section)
            if asana_section in ASANA_MANUAL_ONLY_STAGES and translated:
                new_status = translated
        if new_status != current_status:
            # Never let an AUTOMATIC computation drag a row BACKWARD
            # through the pipeline — same reasoning, and same real
            # reported cause, as decide_asana_sync_action's own
            # no-regression guard: the same creator email in more than
            # one campaign means several syncs write to this one row,
            # and a campaign with no record of a reply would otherwise
            # undo a row that correctly advanced, on every run, forever.
            # An explicit ManualAsanaStage is a deliberate human
            # decision and is never blocked.
            current_rank = _tracker_status_rank(current_status)
            new_rank = _tracker_status_rank(new_status)
            is_regression = (not manual_override_on_sheet and current_rank is not None
                              and new_rank is not None and new_rank < current_rank)
            if is_regression:
                unchanged += 1
                continue
            tracker_ws.update_cell(row_number, status_col_index, new_status)
            row_changed = True

        # Sourced from the lead's own last-send timestamp — never written
        # blank (a lead that hasn't been contacted yet leaves whatever's
        # already there alone, rather than clobbering it with nothing).
        last_action_at = _safe_lead_str(lead.get("LastActionAt"))
        new_last_contacted = last_action_at[:10] if last_action_at else ""
        current_last_contacted = _safe_lead_str(tracker_row.get(TRACKER_COLUMN_LAST_CONTACTED_DATE))
        if new_last_contacted and new_last_contacted != current_last_contacted:
            # Never move this date BACKWARD. "Last contacted" is a
            # high-water mark — an older campaign syncing this shared
            # row knows only about ITS own (earlier) send, and must not
            # overwrite a more recent contact recorded by another
            # campaign. Both values are ISO yyyy-mm-dd here, so a plain
            # string comparison is a correct chronological one; a
            # non-ISO value already in the cell (typed by hand) fails
            # that shape check and is treated as "no usable existing
            # date", so the fresh value still wins rather than being
            # blocked by something unparseable.
            current_is_iso = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", current_last_contacted))
            if current_is_iso and new_last_contacted < current_last_contacted:
                pass  # older than what's already recorded — leave it alone
            else:
                tracker_ws.update_cell(row_number, last_contacted_col_index, new_last_contacted)
                row_changed = True

        # Rights Duration — sourced only from Asana's own live Rights
        # Expiration field (there's no equivalent tracked automatically
        # by the email system), so this is entirely dependent on
        # asana_manual_state_by_email having an entry for this lead.
        # Same "never write blank" principle as Last Contacted Date.
        new_rights_expiration = (asana_state or {}).get("rights_expiration") or ""
        current_rights_duration = _safe_lead_str(tracker_row.get(TRACKER_COLUMN_RIGHTS_DURATION))
        if new_rights_expiration and new_rights_expiration != current_rights_duration:
            tracker_ws.update_cell(row_number, rights_duration_col_index, new_rights_expiration)
            row_changed = True

        if row_changed:
            updated += 1
        else:
            unchanged += 1

    return {"updated": updated, "unchanged": unchanged, "warnings": warnings}



def collect_asana_manual_state_for_leads(sheets: SheetsConnector, campaign_cfg: Dict, api_key: str,
                                          leads: Optional[List[Dict]] = None) -> Dict[str, Dict]:
    """Fetches every lead-with-an-existing-task's current live Asana
    state (section + Rights Expiration), for use by
    sync_campaign_to_tracker_sheet's reverse-sync of a decision made
    directly in Asana. Only possible when Asana sync is ALSO enabled
    and configured for this campaign — there's no Asana state to read
    otherwise. Returns {} (not an error) if Asana sync isn't enabled,
    or if resolving the project fails for any reason — Creator Tracker
    sync should never be blocked by this being unavailable; it just
    means live-Asana-state reverse-sync doesn't happen for that run,
    same as if no lead had an existing task at all.

    leads, when given, is used directly instead of calling
    sheets.get_all_leads() internally — see sync_campaign_to_asana's
    own docstring for why: a single caller running Asana sync, Tracker
    sync, and this function for the same campaign in the same pass can
    fetch the Sheet once and reuse it, rather than each one
    independently re-reading the exact same data. Omit it (the
    default) and this reads fresh, exactly as before.

    Keyed by lowercased email, matching find_tracker_row_for_lead's own
    lookup convention."""
    asana_settings = campaign_cfg.get("asana") or {}
    if not asana_settings.get("enabled"):
        return {}
    project_name = (asana_settings.get("project_name") or "").strip()
    if not project_name:
        return {}
    try:
        project_gid = asana_find_project_gid(project_name, api_key)
    except Exception:  # noqa: BLE001 - unavailable Asana state should never block Tracker sync
        return {}
    if not project_gid:
        return {}

    state_by_email: Dict[str, Dict] = {}
    for lead in (leads if leads is not None else sheets.get_all_leads()):
        email = _safe_lead_str(lead.get("Email")).lower()
        task_gid = _safe_lead_str(lead.get("AsanaTaskGID"))
        if not email or not task_gid:
            continue
        try:
            state_by_email[email] = asana_get_task_manual_state(task_gid, project_gid, api_key)
        except Exception:  # noqa: BLE001 - one lead's failed lookup shouldn't lose state for the rest
            continue
    return state_by_email


def _connect_tracker_worksheet(spreadsheet_id: str, worksheet_identifier: str):
    """Opens a worksheet in a COMPLETELY SEPARATE spreadsheet from any
    campaign's own Sheet — the shared Creator Tracker sheet — using the
    same service account credential already used everywhere else (it
    must be shared with that service account in Google Sheets' own
    sharing settings, exactly like every campaign's own Sheet is).
    Deliberately does NOT use _get_or_create_ws / auto-widen anything:
    this sheet's structure is owned and maintained by a separate
    process — this system only ever reads its existing rows and
    updates two columns' cells, never touches its header or adds
    columns to it.

    worksheet_identifier accepts EITHER the tab's own name (as shown at
    the bottom of Google Sheets — NOT the spreadsheet document's own
    title, which is a different, easily-confused thing) OR its numeric
    gid (the number after "gid=" in that specific tab's URL). A gid is
    more robust — it never changes even if the tab gets renamed later —
    so it's tried first whenever the value looks purely numeric."""
    client, gspread_module = _build_gspread_client()
    spreadsheet = _open_spreadsheet_with_retries(client, spreadsheet_id)
    identifier = worksheet_identifier.strip()
    try:
        if identifier.isdigit():
            raw_ws = spreadsheet.get_worksheet_by_id(int(identifier))
        else:
            raw_ws = spreadsheet.worksheet(identifier)
    except gspread_module.exceptions.WorksheetNotFound:
        raise RuntimeError(
            f"Creator Tracker worksheet '{worksheet_identifier}' not found in spreadsheet {spreadsheet_id}. "
            "CREATOR_TRACKER_WORKSHEET_NAME should be the TAB's own name (shown at the bottom of "
            "Google Sheets) or its numeric gid (the number after 'gid=' in that tab's URL) — NOT the "
            "spreadsheet document's own title, which is a different thing. Also check the sheet has "
            "been shared with this service account."
        )
    return _RetryingWorksheet(raw_ws)


def cmd_sync_asana(args):
    """Runs Asana sync and Creator Tracker sheet sync independently for
    one campaign — each only actually does anything if BOTH its own
    "enabled" setting is on AND its own credentials are present, so a
    campaign that only wants one of the two never needs the other
    configured at all."""
    campaign_cfg = get_campaign(args.campaign)
    sheets = _connect_sheets(campaign_cfg)
    any_errors = False
    api_key = os.environ.get("ASANA_ACCESS_TOKEN", "")
    # Fetched ONCE and reused across all three functions below, instead
    # of each one independently re-reading the exact same Master Sheet
    # data — a real, reported contributor to hitting Google's Sheets
    # API per-minute read quota when both syncs are enabled for a
    # campaign. The one accepted trade-off: a lead that gets a BRAND
    # NEW Asana task created during the Asana-sync step just below
    # won't have that new AsanaTaskGID reflected in this cached list
    # for collect_asana_manual_state_for_leads right after — harmless,
    # since a task that was only just created this same run is never
    # yet in Rights Secured / Declined / Dead, so there's no manual
    # state on it worth reverse-syncing to the Tracker sheet anyway.
    leads = sheets.get_all_leads()

    asana_enabled = (campaign_cfg.get("asana") or {}).get("enabled")
    if asana_enabled:
        if not api_key:
            print("Asana sync is enabled but ASANA_ACCESS_TOKEN is not set — skipped.")
            any_errors = True
        else:
            default_assignee_email = os.environ.get("ASANA_DEFAULT_ASSIGNEE_EMAIL", "").strip() or None
            result = sync_campaign_to_asana(sheets, campaign_cfg, api_key,
                                             default_assignee_email=default_assignee_email, leads=leads)
            print(f"Asana: created {result['created']}, updated {result['updated']}, "
                  f"reused {result.get('reused_existing', 0)} existing (matched by video), "
                  f"skipped {result['skipped_no_email']} (no email).")
            if result["errors"]:
                print(f"Asana: {len(result['errors'])} lead(s) failed to sync:")
                for err in result["errors"]:
                    print(f"  {err['email']}: {err['error']}")
                any_errors = True

    tracker_enabled = (campaign_cfg.get("tracker_sync") or {}).get("enabled")
    if tracker_enabled:
        tracker_sheet_id = os.environ.get("CREATOR_TRACKER_SHEET_ID", "")
        tracker_worksheet_name = os.environ.get("CREATOR_TRACKER_WORKSHEET_NAME", "")
        if not tracker_sheet_id or not tracker_worksheet_name:
            print("Creator Tracker sync is enabled but CREATOR_TRACKER_SHEET_ID / "
                  "CREATOR_TRACKER_WORKSHEET_NAME is not set — skipped.")
            any_errors = True
        else:
            try:
                tracker_ws = _connect_tracker_worksheet(tracker_sheet_id, tracker_worksheet_name)
                # Only meaningful when Asana sync is also enabled and has
                # a valid api_key — collect_asana_manual_state_for_leads
                # returns {} otherwise, so this is always safe to call.
                asana_manual_state = collect_asana_manual_state_for_leads(sheets, campaign_cfg, api_key,
                                                                           leads=leads)
                result = sync_campaign_to_tracker_sheet(sheets, campaign_cfg, tracker_ws,
                                                         asana_manual_state_by_email=asana_manual_state,
                                                         leads=leads)
                print(f"Creator Tracker: updated {result['updated']}, unchanged {result['unchanged']}.")
                if result["warnings"]:
                    print(f"Creator Tracker: {len(result['warnings'])} lead(s) need review:")
                    for warning in result["warnings"]:
                        print(f"  {warning}")
            except Exception as exc:  # noqa: BLE001 - report clearly rather than crash the whole command
                print(f"Creator Tracker sync failed: {exc}")
                any_errors = True

    if not asana_enabled and not tracker_enabled:
        print(f"Neither Asana sync nor Creator Tracker sync is enabled for '{args.campaign}' — nothing to do.")

    if any_errors:
        sys.exit(1)


def cmd_sync_asana_all(args):
    """The scheduled trigger's entry point — loops over EVERY discovered
    campaign, running Asana sync and Creator Tracker sync independently
    for each one (each only runs if its own "enabled" setting is on AND
    its own credentials are present). One campaign's failure is
    reported but never blocks any other campaign, and Asana failing for
    a campaign never blocks that same campaign's Tracker sync, or vice
    versa. The Creator Tracker connection is made once and reused
    across every campaign that needs it — it's the SAME shared sheet
    for all of them, not a separate one per campaign."""
    asana_api_key = os.environ.get("ASANA_ACCESS_TOKEN", "")
    default_assignee_email = os.environ.get("ASANA_DEFAULT_ASSIGNEE_EMAIL", "").strip() or None
    tracker_sheet_id = os.environ.get("CREATOR_TRACKER_SHEET_ID", "")
    tracker_worksheet_name = os.environ.get("CREATOR_TRACKER_WORKSHEET_NAME", "")

    any_enabled = False
    any_errors = False
    tracker_ws = None
    tracker_connect_failed = False

    for campaign_name in discover_campaign_names():
        try:
            campaign_cfg = get_campaign(campaign_name)
        except Exception as exc:  # noqa: BLE001 - a broken campaign config shouldn't sink the whole run
            print(f"{campaign_name}: couldn't load config ({exc}) — skipped.")
            any_errors = True
            continue

        asana_enabled = (campaign_cfg.get("asana") or {}).get("enabled")
        tracker_enabled = (campaign_cfg.get("tracker_sync") or {}).get("enabled")
        if not asana_enabled and not tracker_enabled:
            continue
        any_enabled = True

        try:
            sheets = _connect_sheets(campaign_cfg)
            # Fetched ONCE per campaign and reused across Asana sync,
            # Tracker sync, and collect_asana_manual_state_for_leads
            # below — see cmd_sync_asana's own comment for why: this
            # was a real, reported contributor to hitting Google's
            # Sheets API per-minute read quota when running for every
            # campaign on a schedule.
            leads = sheets.get_all_leads()
        except Exception as exc:  # noqa: BLE001 - one campaign's connection failure shouldn't block others
            print(f"{campaign_name}: couldn't connect to its Sheet ({exc}).")
            any_errors = True
            continue

        if asana_enabled:
            if not asana_api_key:
                print(f"{campaign_name}: Asana sync enabled but ASANA_ACCESS_TOKEN is not set — skipped.")
                any_errors = True
            else:
                try:
                    result = sync_campaign_to_asana(sheets, campaign_cfg, asana_api_key,
                                                     default_assignee_email=default_assignee_email, leads=leads)
                    print(f"{campaign_name} (Asana): created {result['created']}, updated "
                          f"{result['updated']}, reused {result.get('reused_existing', 0)} existing "
                          f"(matched by video), skipped {result['skipped_no_email']} (no email).")
                    if result["errors"]:
                        any_errors = True
                        for err in result["errors"]:
                            print(f"  {campaign_name} (Asana) — {err['email']}: {err['error']}")
                except Exception as exc:  # noqa: BLE001 - one campaign's Asana failure shouldn't block others
                    print(f"{campaign_name}: Asana sync failed entirely ({exc}).")
                    any_errors = True

        if tracker_enabled:
            if not tracker_sheet_id or not tracker_worksheet_name:
                print(f"{campaign_name}: Creator Tracker sync enabled but CREATOR_TRACKER_SHEET_ID / "
                      "CREATOR_TRACKER_WORKSHEET_NAME is not set — skipped.")
                any_errors = True
            elif tracker_connect_failed:
                pass  # already reported once below; no need to repeat per campaign
            else:
                try:
                    if tracker_ws is None:
                        tracker_ws = _connect_tracker_worksheet(tracker_sheet_id, tracker_worksheet_name)
                    # Only meaningful when Asana sync is also enabled and
                    # has a valid api_key for THIS campaign —
                    # collect_asana_manual_state_for_leads returns {}
                    # otherwise, so this is always safe to call.
                    asana_manual_state = collect_asana_manual_state_for_leads(sheets, campaign_cfg,
                                                                               asana_api_key, leads=leads)
                    result = sync_campaign_to_tracker_sheet(sheets, campaign_cfg, tracker_ws,
                                                             asana_manual_state_by_email=asana_manual_state,
                                                             leads=leads)
                    print(f"{campaign_name} (Creator Tracker): updated {result['updated']}, "
                          f"unchanged {result['unchanged']}.")
                    if result["warnings"]:
                        for warning in result["warnings"]:
                            print(f"  {campaign_name} (Creator Tracker) — {warning}")
                except Exception as exc:  # noqa: BLE001 - one campaign's Tracker failure shouldn't block others
                    print(f"{campaign_name}: Creator Tracker sync failed ({exc}).")
                    any_errors = True
                    if tracker_ws is None:
                        tracker_connect_failed = True

    if not any_enabled:
        print("No campaign has Asana sync or Creator Tracker sync enabled — nothing to do.")
    if any_errors:
        sys.exit(1)


def cmd_mark_responses_read(args):
    """Reads {"response_ids": [...]} from --file and sets IsRead=Yes for
    each one that still exists in this campaign's Response Sheet — a
    ResponseID no longer present (e.g. the sheet was manually edited) is
    silently skipped, not an error; the caller's goal ("these should show
    read") is still satisfied for everything that DID match."""
    campaign_cfg = get_campaign(args.campaign)
    sheets = _connect_sheets(campaign_cfg)
    with open(args.file, "r", encoding="utf-8") as f:
        payload = json.load(f)
    requested_ids = payload.get("response_ids", [])
    if not requested_ids:
        print("No response_ids in payload file — nothing to do.")
        return

    all_responses = sheets.get_all_responses()
    requested_set = set(requested_ids)
    id_to_row = {
        r.get("ResponseID"): r["_row"] for r in all_responses if r.get("ResponseID") in requested_set
    }
    marked_count = sheets.mark_responses_read(id_to_row)
    print(f"Marked {marked_count} of {len(requested_ids)} response(s) as read.")
    missing = requested_set - set(id_to_row.keys())
    if missing:
        print(f"Not found (already gone from the sheet, or never existed): {sorted(missing)}")


def cmd_send_test(args):
    """Sends one test email of a campaign to --to (see send_test_email). Prints what was sent; never touches a Sheet."""
    campaign_cfg = get_campaign(args.campaign)
    accounts = load_email_accounts()
    try:
        sent = send_test_email(campaign_cfg, accounts, args.stage, args.variant, args.to, sender_account=args.account or None)
    except Exception as exc:  # noqa: BLE001 - say plainly what went wrong, and fail the run so it shows red in Actions
        print(f"ERROR: the test email was NOT sent: {exc}")
        sys.exit(1)
    print(f"Test email sent to {sent['to']} from {sent['account']} ({sent['format']} campaign): {sent['subject']}")
    if sent["missing_variables"]:
        print("Note: these placeholders matched no column and came out blank: " + ", ".join(sent["missing_variables"]))
    if sent["unsubscribe_header"]:
        print(f"Unsubscribe header sent with this email: List-Unsubscribe: {sent['unsubscribe_header']}")
        print("Note: Gmail often does not show its own Unsubscribe button next to the sender for a test sent to yourself or from a new or low-volume sender. "
              "The footer's Unsubscribe link is in the email itself either way.")
        config, note = ul.configuration_status(os.environ)
        if sent.get("unsubscribe_page"):
            print("Unsubscribe page: ON. The footer link and the header carry a signed TEST link: opening it shows a banner and records nothing, so a real opt-out can only be proved "
                  "with a real campaign email.")
            print(f"Page address in use: {config.url}")
            if note:
                print(f"WARNING: {note}")
            link = sent.get("unsubscribe_url", "")
            token = link[len(config.url) + 3:] if link.startswith(config.url + ("&" if "?" in config.url else "?") + "t=") else ""
            print(f"Link in this test email (exact): {link}")
            print(f"Link check: starts with the address in use: {'YES' if token else 'NO'} | length {len(link)} characters | only safe characters in the token: "
                  f"{'YES' if token and re.fullmatch(r'[A-Za-z0-9._-]+', token) else 'NO'} | the same link in the footer, the header and the plain-text copy: {'YES' if sent.get('link_in_every_copy') else 'NO'}")
            campaign_name = str(campaign_cfg.get("_campaign_name") or "").strip()
            record = ul.verify_token(config.secret, ul.token_from_url(link)) or {}
            hidden = bool(campaign_name) and campaign_name not in link and record.get("c") == ul.campaign_code(config.secret, campaign_name)
            print(f"Campaign name in the link: {'HIDDEN, the link carries only the code ' + record.get('c', '') if hidden else 'NOT hidden'}. Anyone can read the rest of the link's contents (the recipient's own address, "
                  "the brand, the lead ID); only the campaign name is replaced.")
            bare_state, bare_message = probe_unsubscribe_page(config.url)
            exact_state, exact_message = probe_unsubscribe_page(link, expect=ul.LINK_MARKERS)
            print(f"Check 1, the page address on its own (opened from GitHub without signing in): {bare_state.upper()}. {bare_message}")
            print(f"Check 2, the exact link in this email (opened the same way): {exact_state.upper()}. {exact_message}")
            print(ul.compare_probes(bare_state, exact_state))
            trace = trace_unsubscribe_link(link, expect=ul.LINK_MARKERS)
            print("Browser-style trace of the exact link (cookies kept, each redirect followed one step at a time, then the page's own content frame requested):")
            for line in ul.format_trace(trace):
                print(line)
            print(ul.trace_verdict(trace))
            pages_bad = False
            if config.page:
                footer_link = sent.get("unsubscribe_footer_url", "")
                origin = ul.origin_of(config.page)
                page_token = ul.token_from_url(footer_link)
                print(f"Footer link (what recipients click, on GitHub Pages): {footer_link}")
                print("Static page checks (recipients open a page on GitHub Pages; it asks the Google script for its data with plain requests: no Google sign-in, no cookies):")
                results = [("A, the page is being served", probe_static_page(footer_link)),
                           ("B, its config.js names the same Google script", probe_page_config(config.page, config.url)),
                           (f"C, the script answers the page's data request (GET) and allows a page on {origin}", probe_unsubscribe_api(config.url, page_token, origin)),
                           ("D, the script accepts the page's confirm request (POST, with this TEST link: records nothing)", probe_unsubscribe_api(config.url, page_token, origin, post=True))]
                for label, (state, message) in results:
                    print(f"  {label}: {state.upper()}. {message}")
                pages_bad = any(state == "blocked" for _, (state, _message) in results)
                if pages_bad:
                    print("PAGE VERDICT: at least one thing the page needs is not working: fix the lines marked BLOCKED first.")
                elif any(state == "unknown" for _, (state, _message) in results):
                    print("PAGE VERDICT: not conclusive: at least one check could not be made from here.")
                else:
                    print("PAGE VERDICT: everything the page needs works from here, including the permission for a page on another site to read the Google script's answers. "
                          "NOT TESTED HERE: a real browser. Open the footer link yourself, in your normal browser, to see it.")
                timings = probe_unsubscribe_timings(config.url, page_token, origin)
                print("Speed check, from GitHub's network (a recipient's browser will differ). Each request is made twice: the first shows a cold start, the second a warm one:")
                for line in ul.format_timings(timings) + ul.speed_reading(timings):
                    print(line)
            else:
                print("Note: UNSUBSCRIBE_PAGE_URL is not set, so recipients open the Google page itself. That page is refused for people signed in to several Google accounts "
                      "(Google adds /u/N/ to the address). Set UNSUBSCRIBE_PAGE_URL to the GitHub Pages address of docs/unsubscribe.html to avoid that.")
            if pages_bad or "blocked" in (bare_state, exact_state) or "signin" in (bare_state, exact_state) or any(step.get("state") in ("blocked", "signin") for step in trace):
                print("PROBLEM: recipients would not be able to open the unsubscribe page. This run is marked failed so you notice; the test email itself was sent.")
                sys.exit(1)
        else:
            print("Unsubscribe page: OFF" + (f". {note}" if note else " (not set up yet): the email-based unsubscribe is used."))


def cmd_send_reply(args):
    """Reads a fully-resolved reply payload from --file and sends it —
    this command never looks anything up itself (which response, which
    thread); Streamlit's Responses tab is expected to have already
    resolved to/subject/in_reply_to/references from the specific inbound
    message being replied to before ever committing this payload. See
    send_manual_reply's docstring for exactly why that split exists.

    Expected payload shape:
        {"sender_account": "sales1", "to": "lead@abc.com",
         "subject": "Re: ...", "body": "...", "in_reply_to": "<...>",
         "references": "<...> <...>", "cc": [...], "bcc": [...],
         "lead_id": "5",
         "attachments": [{"filename": "photo.png", "content_base64": "..."}]}
    cc/bcc/lead_id/attachments are optional. Attachment content travels
    as base64 in the JSON payload (the payload file itself is already the
    "big binary data over a size-limited channel" workaround — see
    campaigns.py's Responses tab for how it's built).
    """
    campaign_cfg = get_campaign(args.campaign)
    sheets = _connect_sheets(campaign_cfg)
    accounts = load_email_accounts()
    with open(args.file, "r", encoding="utf-8") as f:
        payload = json.load(f)

    lead_id = payload.get("lead_id", "")
    to_email = payload.get("to", "")
    attachments = None
    if payload.get("attachments"):
        attachments = [
            {"filename": att["filename"], "content": base64.b64decode(att["content_base64"])}
            for att in payload["attachments"]
        ]

    try:
        sent = send_manual_reply(
            accounts, payload["sender_account"], to_email, payload["subject"], payload["body"],
            in_reply_to=payload.get("in_reply_to"), references=payload.get("references"),
            cc=payload.get("cc") or None, bcc=payload.get("bcc") or None, attachments=attachments,
        )
    except Exception as exc:  # noqa: BLE001 - classify and log exactly like every other send path
        error_type = classify_send_exception(exc)
        now_str = datetime.now().strftime(DATETIME_FMT)
        try:
            sheets.append_send_log({
                "BatchID": "manual-reply", "Timestamp": now_str, "LeadID": lead_id, "Email": to_email,
                "Campaign": args.campaign, "Stage": "manual_reply", "Variant": "",
                "SenderAccount": payload.get("sender_account", ""), "Status": "error",
                "MessageID": "", "Error": str(exc)[:500],
            })
        except Exception:  # noqa: BLE001 - the error log entry below is the durable record either way
            pass
        log_error(sheets, args.campaign, error_type, str(exc), lead_id=lead_id, email_addr=to_email,
                  stage="manual_reply", batch_id="manual-reply")
        print(f"FAILED: {exc}")
        raise SystemExit(1)

    now_str = datetime.now().strftime(DATETIME_FMT)
    sheets.append_send_log({
        "BatchID": "manual-reply", "Timestamp": now_str, "LeadID": lead_id, "Email": to_email,
        "Campaign": args.campaign, "Stage": "manual_reply", "Variant": "",
        "SenderAccount": payload["sender_account"], "Status": "sent",
        "MessageID": sent["message_id"], "Error": "",
    })
    if getattr(sheets, "sent_mail_enabled", False):
        # Keep what was actually said, so the conversation view can show it: the Send Log only
        # records THAT a reply went out, and this payload file is deleted once it has been sent.
        try:
            sheets.append_sent_mail_rows([{
                "MessageID": sent["message_id"], "LeadID": lead_id, "SentAt": now_str,
                "SenderAccount": payload["sender_account"], "To": to_email, "Cc": ", ".join(payload.get("cc") or []),
                "Subject": payload["subject"], "Body": (payload["body"] or "")[:49000],
                "InReplyTo": payload.get("in_reply_to") or "", "Source": SENT_SOURCE_APP,
            }])
        except Exception as exc:  # noqa: BLE001 - the email HAS been sent; failing to keep a copy must not look like it failed
            log_error(sheets, args.campaign, ERR_SHEETS_API, f"Reply sent but could not be stored for the conversation view: {exc}",
                      lead_id=lead_id, email_addr=to_email, stage="manual_reply", batch_id="manual-reply")
    print(f"Sent. Message-ID: {sent['message_id']}")


def cmd_check_account_health(args):
    """Logs into every configured account via IMAP (a cheap connectivity
    check, not a real mailbox scan) and writes the result to the shared
    'Email Accounts Health' Sheet tab — never the password itself, only
    connection status and a short failure reason. Reads shared_sheet_id
    from settings.yaml directly since this isn't tied to any one
    campaign's own Sheet configuration."""
    accounts = load_email_accounts()
    settings = load_settings()
    results = check_account_health(accounts)
    write_account_health(settings["shared_sheet_id"], results)
    for r in results:
        detail_suffix = f" — {r['Detail']}" if r["Detail"] else ""
        print(f"{r['AccountName']} ({r['Address']}): {r['Status']}{detail_suffix}")


def cmd_check_replies(args):
    campaign_cfg = get_campaign(args.campaign)
    sheets = _connect_sheets(campaign_cfg)
    accounts = load_email_accounts()
    lookback_hours = campaign_cfg.get("reply_monitor", {}).get("lookback_hours", 24)
    anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY")  # optional — blank Intent fields if unset

    account_errors = []
    actions = check_replies(sheets, accounts, lookback_hours, campaign_name=args.campaign,
                             anthropic_api_key=anthropic_api_key, account_errors=account_errors)
    _sync_sent_mail_quietly(sheets, accounts, lookback_hours, args.campaign)
    _mirror_unsubscribes_quietly(campaign_cfg, sheets)

    if account_errors:
        # Printed to STDOUT specifically (not just the WARNING already
        # sent to stderr inside check_replies itself) — the workflow's
        # job summary only captures stdout via `tee`, so a failure here
        # would otherwise be invisible in the run summary a person
        # actually looks at, even though the run itself still reports
        # "success" and "no new messages", which looks identical to a
        # run that genuinely found nothing.
        print(f"WARNING: {len(account_errors)} account(s) could NOT be checked this run — any "
              f"replies sent to those addresses were NOT looked for, not genuinely absent:")
        for err in account_errors:
            print(f"  {err}")
        print()

    if not actions:
        print("No new inbound messages matched to a lead.")
        return

    print(f"Processed {len(actions)} inbound message(s):\n")
    for a in actions:
        print(f"  {a['email']:<35} {a['classification']:<18} ({a['match_method']:<6}, {a['account']}) -> {a['action']}")


def cmd_check_replies_all(args):
    """The scheduled trigger's entry point — loops over EVERY discovered
    campaign, regardless of status (Running, Paused, Draft, Completed),
    since even a paused or finished campaign can still receive a
    genuine reply that needs to be logged and its lead's sequence
    correctly stopped.

    This is the actual fix for a real reported production bug: the
    scheduled workflow was calling check-replies for a single,
    hardcoded campaign name — meaning any OTHER campaign (including
    whatever the current actual campaign is, if it was created after
    that name was hardcoded, e.g. Kelson_Creators_Licensing_03SEP
    superseding the older Kelson_Creators_Licensing) never had its
    replies checked automatically at all, while a manual run — which
    lets a human specify any campaign by name — worked fine every
    time. One campaign's failure never blocks any other campaign's
    check."""
    accounts = load_email_accounts()
    anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY")
    any_campaigns = False
    any_errors = False

    for campaign_name in discover_campaign_names():
        any_campaigns = True
        try:
            campaign_cfg = get_campaign(campaign_name)
            sheets = _connect_sheets(campaign_cfg)
            lookback_hours = campaign_cfg.get("reply_monitor", {}).get("lookback_hours", 24)

            account_errors = []
            actions = check_replies(sheets, accounts, lookback_hours, campaign_name=campaign_name,
                                     anthropic_api_key=anthropic_api_key, account_errors=account_errors)
            _sync_sent_mail_quietly(sheets, accounts, lookback_hours, campaign_name)
            _mirror_unsubscribes_quietly(campaign_cfg, sheets)

            if account_errors:
                print(f"{campaign_name}: WARNING — {len(account_errors)} account(s) could NOT be "
                      f"checked this run — any replies sent to those addresses were NOT looked for:")
                for err in account_errors:
                    print(f"  {err}")
                any_errors = True

            if actions:
                print(f"{campaign_name}: processed {len(actions)} inbound message(s):")
                for a in actions:
                    print(f"  {a['email']:<35} {a['classification']:<18} "
                          f"({a['match_method']:<6}, {a['account']}) -> {a['action']}")
            else:
                print(f"{campaign_name}: no new inbound messages matched.")
        except Exception as exc:  # noqa: BLE001 - one campaign's failure shouldn't block the rest
            print(f"{campaign_name}: check-replies failed entirely ({exc}).")
            any_errors = True

    if not any_campaigns:
        print("No campaigns found — nothing to check.")
    if any_errors:
        sys.exit(1)


def cmd_dashboard(args):
    if not args.all and not args.campaign:
        print("Specify --campaign NAME or --all", file=sys.stderr)
        sys.exit(1)

    if args.all:
        settings = load_settings()
        shared_sheet_id = settings.get("shared_sheet_id", "")
        campaign_names = discover_campaign_names()
        if not campaign_names:
            print("No campaigns found — no subfolders under templates/.")
            return
        all_rows = []
        for name in campaign_names:
            campaign_cfg = get_campaign(name)
            sheets = _connect_sheets(campaign_cfg)
            leads = sheets.get_all_leads()
            responses = sheets.get_all_responses()
            send_log = sheets.get_all_send_log()
            error_log = sheets.get_all_error_log()
            rows = compute_campaign_dashboard(campaign_cfg, leads, responses, send_log, error_log)
            write_dashboard(sheets.dashboard_ws, rows)
            all_rows.append(compute_all_campaigns_row(name, leads, responses, send_log, campaign_cfg["stages"]))
            print(f"Updated dashboard for '{name}'")
        if shared_sheet_id and all_rows:
            ws = get_all_campaigns_dashboard_ws(shared_sheet_id)
            write_all_campaigns_dashboard(ws, all_rows)
            print("Updated combined 'All Campaigns Dashboard'")
    else:
        campaign_cfg = get_campaign(args.campaign)
        sheets = _connect_sheets(campaign_cfg)
        leads = sheets.get_all_leads()
        responses = sheets.get_all_responses()
        send_log = sheets.get_all_send_log()
        error_log = sheets.get_all_error_log()
        rows = compute_campaign_dashboard(campaign_cfg, leads, responses, send_log, error_log)
        write_dashboard(sheets.dashboard_ws, rows)
        print(f"Updated dashboard for '{args.campaign}'")


# =============================================================================
# SECTION 15: Argument parsing / entry point
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Outreach automation (SMTP/IMAP, single-file)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_preview = sub.add_parser("preview", help="Show what would be sent, without sending")
    p_preview.add_argument("--campaign", required=True)
    p_preview.add_argument("--stage", required=True)
    p_preview.add_argument("--batch-size", type=int, required=True)
    p_preview.add_argument("--variant", default="Auto",
                            help="Force a specific variant letter for the whole batch. Default: Auto")
    p_preview.add_argument("--ignore-wait-days", action="store_true",
                            help="Skip the scheduled wait for this stage (e.g. followup1's 3-day wait) "
                                 "for THIS RUN ONLY. Every other eligibility rule still applies — the "
                                 "previous stage must actually have been sent, this stage can't already "
                                 "be sent, Approval must be Yes, and no reply must have been received.")
    p_preview.set_defaults(func=cmd_preview)

    p_send = sub.add_parser("send", help="Actually send a batch")
    p_send.add_argument("--campaign", required=True)
    p_send.add_argument("--stage", required=True)
    p_send.add_argument("--batch-size", type=int, required=True)
    p_send.add_argument("--variant", default="Auto",
                         help="Force a specific variant letter for the whole batch. Default: Auto")
    p_send.add_argument("--ignore-wait-days", action="store_true",
                         help="Skip the scheduled wait for this stage (e.g. followup1's 3-day wait) "
                              "for THIS RUN ONLY. Every other eligibility rule still applies — the "
                              "previous stage must actually have been sent, this stage can't already "
                              "be sent, Approval must be Yes, and no reply must have been received.")
    p_send.add_argument("--daily-limit", type=int, default=None,
                         help="Override sending.daily_limit for THIS RUN ONLY — never written to "
                              "campaigns.yaml. Omit to use the value from config.")
    p_send.add_argument("--per-account-daily-limit", type=int, default=None,
                         help="Override sending.per_account_daily_limit for THIS RUN ONLY — never "
                              "written to campaigns.yaml. Omit to use the value from config.")
    p_send.add_argument("--sender-rotation", choices=["true", "false"], default=None,
                         help="Override sending.sender_rotation for THIS RUN ONLY — never written "
                              "to campaigns.yaml. Omit to use the value from config.")
    p_send.set_defaults(func=cmd_send)

    p_replies = sub.add_parser("check-replies", help="Check all configured inboxes for new replies/bounces")
    p_replies.add_argument("--campaign", required=True)
    p_replies.set_defaults(func=cmd_check_replies)

    p_replies_all = sub.add_parser("check-replies-all",
                                    help="Check all configured inboxes for every campaign, regardless of "
                                         "status — the scheduled trigger's entry point")
    p_replies_all.set_defaults(func=cmd_check_replies_all)

    p_backfill = sub.add_parser("backfill-thread-subject",
                                 help="One-time migration: fill in ThreadSubject for leads already mid-sequence "
                                      "before that feature existed (see backfill_thread_subjects docstring for "
                                      "exactly how, and its accuracy caveat)")
    p_backfill.add_argument("--campaign", required=True)
    p_backfill.add_argument("--dry-run", action="store_true",
                             help="Show what would be backfilled without writing anything")
    p_backfill.set_defaults(func=cmd_backfill_thread_subject)

    p_backfill_bodies = sub.add_parser("backfill-response-bodies",
                                        help="Fill in the text of logged replies that were stored with no text "
                                             "(HTML-only emails), by looking each one up in its mailbox. Only fills "
                                             "gaps; see backfill_response_bodies' docstring.")
    p_backfill_bodies.add_argument("--campaign", required=True)
    p_backfill_bodies.add_argument("--dry-run", action="store_true",
                                    help="Show what would be filled in without writing anything")
    p_backfill_bodies.add_argument("--include-dates", action="store_true",
                                    help="Also fill in the real sent time of EVERY logged reply that lacks one, "
                                         "not only of the ones being repaired for missing text")
    p_backfill_bodies.set_defaults(func=cmd_backfill_response_bodies)

    p_sync_sent = sub.add_parser("sync-sent-mail",
                                  help="Store the emails you sent yourself (typed by hand in the mailbox) that belong to a "
                                       "lead's conversation, found by reading each mailbox's Sent folder read-only. "
                                       "The scheduled reply check already does this for the last day; use this for older history.")
    p_sync_sent.add_argument("--campaign", required=True)
    p_sync_sent.add_argument("--days", type=int, default=7, help="How many days back to look (default 7)")
    p_sync_sent.add_argument("--dry-run", action="store_true", help="Show what would be stored without writing anything")
    p_sync_sent.set_defaults(func=cmd_sync_sent_mail)

    p_import = sub.add_parser("import-leads", help="Bulk-import leads from a JSON payload file")
    p_import.add_argument("--campaign", required=True)
    p_import.add_argument("--file", required=True, help='Path to a JSON file: {"leads": [{...}, ...]}')
    p_import.set_defaults(func=cmd_import_leads)

    p_remove = sub.add_parser("remove-leads",
                               help="Soft-remove leads (sets Status=Removed, never a hard delete) "
                                    "from a JSON payload file")
    p_remove.add_argument("--campaign", required=True)
    p_remove.add_argument("--file", required=True, help='Path to a JSON file: {"lead_ids": ["5", "8", ...]}')
    p_remove.set_defaults(func=cmd_remove_leads)

    p_set_override = sub.add_parser("set-lead-override",
                                     help="Set a manual sending-status, Asana-stage, and/or reply-tracking "
                                          "override for one lead, found by email")
    p_set_override.add_argument("--campaign", required=True)
    p_set_override.add_argument("--email", required=True)
    p_set_override.add_argument("--status", default=None,
                                 help="'' to resume normal sending, or 'Stopped - Manual' to stop sending "
                                      "to only this lead. Omit to leave sending status untouched.")
    p_set_override.add_argument("--asana-stage", default=None,
                                 help="'' to resume automatic Asana stage tracking, or one of Sourced / "
                                      "Outreach Sent / Follow-up / Negotiating / Rights Secured / "
                                      "Declined / Dead. Omit to leave the Asana stage untouched.")
    p_set_override.add_argument("--reply-status", default=None,
                                 help="'' to clear, or 'Replied' — for a genuine reply that landed on a "
                                      "new email thread the automated reply-checker couldn't match back "
                                      "to this lead. Also sets ReplyAt to now. Omit to leave untouched.")
    p_set_override.add_argument("--last-inbound-classification", default=None,
                                 help="'' to clear, or one of Genuine Reply / Auto-Reply / Out of Office / "
                                      "Bounce (Hard) / Bounce (Soft). Also sets LastInboundAt to now when "
                                      "non-blank. Omit to leave untouched.")
    p_set_override.set_defaults(func=cmd_set_lead_override)

    p_reply = sub.add_parser("send-reply",
                              help="Send a one-off manual reply from a fully-resolved JSON payload file "
                                   "(never looks anything up itself — see cmd_send_reply's docstring)")
    p_reply.add_argument("--campaign", required=True)
    p_reply.add_argument("--file", required=True,
                          help='Path to a JSON file: {"sender_account": "sales1", "to": "...", '
                               '"subject": "...", "body": "...", "in_reply_to": "<...>", '
                               '"references": "<...>", "cc": [...], "bcc": [...], "lead_id": "5"}')
    p_reply.set_defaults(func=cmd_send_reply)

    p_test = sub.add_parser("send-test", help="Send one test email of a campaign to an address, with a made-up lead (touches no Sheet)")
    p_test.add_argument("--campaign", required=True)
    p_test.add_argument("--stage", required=True, help="The stage's file prefix, e.g. intro or followup1")
    p_test.add_argument("--variant", required=True, help="A, B, C or D")
    p_test.add_argument("--to", required=True, help="The address that receives the test")
    p_test.add_argument("--account", default="", help="Optional: which sender account to send from (default: the campaign's default)")
    p_test.set_defaults(func=cmd_send_test)

    p_mark_read = sub.add_parser("mark-responses-read",
                                  help="Set IsRead=Yes for the response IDs listed in a JSON payload file")
    p_mark_read.add_argument("--campaign", required=True)
    p_mark_read.add_argument("--file", required=True, help='Path to a JSON file: {"response_ids": ["r1", "r2"]}')
    p_mark_read.set_defaults(func=cmd_mark_responses_read)

    p_sync_asana = sub.add_parser("sync-asana", help="Create/update Asana tasks for this campaign's leads")
    p_sync_asana.add_argument("--campaign", required=True)
    p_sync_asana.set_defaults(func=cmd_sync_asana)

    p_sync_asana_all = sub.add_parser("sync-asana-all",
                                       help="Sync every campaign with asana.enabled set to true")
    p_sync_asana_all.set_defaults(func=cmd_sync_asana_all)

    p_auto_send_all = sub.add_parser("auto-send-all",
                                      help="Send every stage due right now, for every Running campaign")
    p_auto_send_all.set_defaults(func=cmd_auto_send_all)

    p_health = sub.add_parser("check-account-health",
                               help="Check every configured account's IMAP connectivity and write results "
                                    "to the shared 'Email Accounts Health' Sheet tab")
    p_health.set_defaults(func=cmd_check_account_health)

    p_dash = sub.add_parser("dashboard", help="Recompute and write the dashboard tab(s)")
    p_dash.add_argument("--campaign", help="Campaign name (omit if using --all)")
    p_dash.add_argument("--all", action="store_true",
                         help="Update dashboards for every configured campaign, plus the combined "
                              "All Campaigns Dashboard")
    p_dash.set_defaults(func=cmd_dashboard)

    args = parser.parse_args()
    try:
        args.func(args)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
