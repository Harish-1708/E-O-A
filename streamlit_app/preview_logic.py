"""Preview never sends anything and never writes anything, so it runs
DIRECTLY inside Streamlit — no GitHub Actions round trip needed. It reuses
outreach.build_batch exactly as the CLI does, against a read-only fetch of
current lead data. This also means SMTP credentials (EMAIL_ACCOUNTS_JSON)
never need to exist anywhere near the Streamlit process — only Send needs
them, and Send always happens via GitHub Actions, which is the only place
that credential lives.
"""
import sys
from typing import Dict, List, Optional

import config

if config.REPO_ROOT not in sys.path:
    sys.path.insert(0, config.REPO_ROOT)

import outreach  # noqa: E402


def get_campaign_cfg(campaign_name: str) -> Dict:
    # Reads config.SETTINGS_PATH/CAMPAIGNS_DIR/TEMPLATES_ROOT at CALL time via
    # the module (not `from config import X` at import time) — this module
    # is imported once and cached by Python for the whole process, so a
    # test's patch("config.CAMPAIGNS_DIR", ...) must still be visible on
    # every call, not just the first one before the patch took effect.
    return outreach.get_campaign(
        campaign_name,
        settings_path=config.SETTINGS_PATH,
        campaigns_dir=config.CAMPAIGNS_DIR,
        templates_root=config.TEMPLATES_ROOT,
    )


def list_campaigns() -> List[str]:
    return outreach.discover_campaign_names(config.TEMPLATES_ROOT)


def list_campaigns_live(github_client) -> List[str]:
    """Campaign names read LIVE from GitHub, falling back to the local
    checkout if GitHub can't be reached.

    A campaign exists because a templates/<name>/ folder exists, so
    creating, duplicating or deleting one changes GitHub immediately
    while the local checkout stays frozen until Streamlit Cloud
    redeploys — the same staleness class as campaign settings and the
    account slot mapping. Sorted for a stable display order regardless
    of the order GitHub returns entries in.

    Same fallback contract as the other live readers: any API failure
    degrades to the previous local behaviour rather than erroring, and
    an empty live result is treated as a real failure signal (a repo
    with zero campaign folders is indistinguishable from a failed
    listing here, and falling back is the safer of the two)."""
    if github_client is not None:
        try:
            names = github_client.list_subdirectories("templates")
            if names:
                return sorted(names)
        except Exception:  # noqa: BLE001 - network/auth/API failure
            pass
    return list_campaigns()


def run_preview(campaign_name: str, stage_name: str, batch_size: int,
                 leads: List[Dict], forced_variant: Optional[str] = None,
                 ignore_wait_days: bool = False) -> List[Dict]:
    """Returns outreach.build_batch's plan list unmodified — same function
    the CLI/GitHub Action's `preview` command calls. No sending, no sheet
    writes; leads must already be fetched (read-only) by the caller."""
    campaign_cfg = get_campaign_cfg(campaign_name)
    variant = None if forced_variant in (None, "Auto") else forced_variant
    return outreach.build_batch(campaign_cfg, leads, stage_name, batch_size, forced_variant=variant,
                                 ignore_wait_days=ignore_wait_days)
