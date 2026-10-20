"""What the Sequences tab needs so that an HTML campaign keeps BOTH of an email's files together: the .txt (its subject) and the .html (the designed version). Deleting an
email in an HTML campaign must always delete the pair, and the launch check lists what stops a campaign launching. Pure functions, so the rules can be tested without Streamlit
or GitHub."""
import os
import sys
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import html_campaign_logic as hc  # noqa: E402


def is_html_campaign(campaign_cfg: Optional[Dict]) -> bool:
    return bool(campaign_cfg) and campaign_cfg.get("format") == hc.FORMAT_HTML


def html_path_for(txt_path: str) -> str:
    """templates/Camp/intro_A.txt -> templates/Camp/intro_A.html"""
    return txt_path[:-4] + ".html" if txt_path.endswith(".txt") else txt_path


def with_html_deletions(paths: List[str]) -> List[str]:
    """The given .txt deletion paths followed by the matching .html paths. The text files go FIRST: if a deletion fails part way, what is left is a stray design file (harmless),
    never an email with a design but no text, and never a text file with no design."""
    ordered = list(paths)
    for path in paths:
        companion = html_path_for(path)
        if path.endswith(".txt") and companion not in ordered:
            ordered.append(companion)
    return ordered


def launch_blockers(emails: List[Dict], known_variables, footer: Optional[Dict]) -> List[str]:
    """What stops an HTML campaign launching: one plain line per problem, each starting with the email it is about. `emails` is a list of
    {"label": "Intro · Variant A", "html": the design or None when the file does not exist, "subject": the email's subject line, "first": True for the first stage,
    "problem": why it could not be read or None}. Only the checks' ERRORS block (and an empty subject on the first email); their warnings never do. An empty list means
    nothing stops the launch."""
    blockers: List[str] = []
    for email in emails:
        label = email.get("label") or "An email"
        if email.get("problem"):
            blockers.append(f"{label}: could not be read ({email['problem']}).")
            continue
        if email.get("first") and not (email.get("subject") or "").strip():
            blockers.append(f"{label}: the subject is empty. The first email of a sequence needs one.")
        if email.get("html") is None:
            blockers.append(f"{label}: has no designed (.html) file yet.")
        else:
            for error in hc.html_checks(email["html"], "", known_variables, footer)["errors"]:
                blockers.append(f"{label}: {error}")
    return blockers
