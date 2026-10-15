"""The accounts a reply can be sent from, with their health, for the composer's From list. Shared by the campaign's Responses tab and the main Responses page so both
show the same list: the legacy Streamlit-secrets directory and the live slot-mapping file merged (an account added on the Email Accounts page appears at once), plus each
account's last health check.

Nothing here can break the Inbox: a health lookup that fails just means no health is shown.
"""
from typing import Callable, Dict, Optional

from accounts_logic import merge_account_directories
from email_account_slots_logic import read_slot_mapping_live


def build_sender_options(secret_directory: Optional[Dict[str, str]], github_client, slot_mapping_path: str, health_reader: Callable[[], Dict]) -> Dict:
    """{'directory': {name: address}, 'health': {name: {status, detail, checked_at}}}."""
    slot_mapping = read_slot_mapping_live(github_client, slot_mapping_path)
    directory = merge_account_directories(dict(secret_directory or {}), slot_mapping)
    try:
        health = dict(health_reader() or {})
    except Exception:  # noqa: BLE001 - no health data just means no health is shown beside the accounts
        health = {}
    return {"directory": directory, "health": health}
