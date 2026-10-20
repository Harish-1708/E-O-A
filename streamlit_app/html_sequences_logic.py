"""What the Sequences tab needs so that an HTML campaign keeps BOTH of an email's files together: the .txt (subject and plain-text copy) and the .html (the designed version).
Adding or deleting an email in an HTML campaign must always add or delete the pair, or the campaign would no longer load (the engine refuses an HTML campaign with a missing
.html). Pure functions, so the rules can be tested without Streamlit or GitHub."""
import os
import sys
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import html_campaign_logic as hc  # noqa: E402


VIEW_TEXT = "Subject & plain text"
VIEW_DESIGN = "Design (HTML)"


def is_html_campaign(campaign_cfg: Optional[Dict]) -> bool:
    return bool(campaign_cfg) and campaign_cfg.get("format") == hc.FORMAT_HTML


def html_path_for(txt_path: str) -> str:
    """templates/Camp/intro_A.txt -> templates/Camp/intro_A.html"""
    return txt_path[:-4] + ".html" if txt_path.endswith(".txt") else txt_path


def with_html_companions(files: List[Dict], layout: Optional[str] = None) -> List[Dict]:
    """The given new template files plus an .html companion (the starter layout) for every .txt among them, to be committed together in one save. Files already present as
    .html in the list are kept as they are and not duplicated."""
    present = {f["path"] for f in files}
    extra = []
    for f in files:
        companion = html_path_for(f["path"])
        if f["path"].endswith(".txt") and companion not in present:
            extra.append({"path": companion, "content": hc.starter_html(layout or hc.DEFAULT_LAYOUT).encode("utf-8")})
            present.add(companion)
    return list(files) + extra


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
    {"label": "Intro · Variant A", "html": the design or None when the file does not exist, "body": the plain-text copy's body, "problem": why it could not be read or None}.
    Only the checks' ERRORS block; their warnings never do. An empty list means nothing stops the launch."""
    blockers: List[str] = []
    for email in emails:
        label = email.get("label") or "An email"
        if email.get("problem"):
            blockers.append(f"{label}: could not be read ({email['problem']}).")
        elif email.get("html") is None:
            blockers.append(f"{label}: has no designed (.html) file yet.")
        else:
            for error in hc.html_checks(email["html"], email.get("body") or "", known_variables, footer)["errors"]:
                blockers.append(f"{label}: {error}")
    return blockers
