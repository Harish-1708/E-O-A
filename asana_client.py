"""A minimal, self-contained Asana API client — deliberately NOT
importing anything from outreach.py, since this whole sync is meant
to stay fully separate from the email outreach automation. Handles
pagination and basic retry on transient network failures; nothing
fancier than that is needed for a read-only, once-per-run task list."""
import time
from typing import Dict, List

import requests

ASANA_API_BASE = "https://app.asana.com/api/1.0"
_RETRY_DELAYS_SECONDS = [2, 5, 10]
_OPT_FIELDS = "name,memberships.section.name,custom_fields.name,custom_fields.display_value,permalink_url"


def get_all_project_tasks(project_gid: str, token: str) -> List[Dict]:
    """Every task in a project, fully paginated, with section
    membership and custom field values included — exactly what
    ugc_tracker_logic.extract_rights_secured_tasks expects."""
    headers = {"Authorization": f"Bearer {token}"}
    tasks: List[Dict] = []
    url = f"{ASANA_API_BASE}/tasks"
    params = {"project": project_gid, "opt_fields": _OPT_FIELDS, "limit": 100}

    while url:
        response = _get_with_retries(url, headers=headers, params=params)
        data = response.json()
        tasks.extend(data.get("data", []))
        next_page = data.get("next_page")
        if not next_page:
            break
        url = next_page["uri"]
        params = None  # the next_page URI already includes all query params

    return tasks


def _get_with_retries(url: str, headers: Dict, params) -> requests.Response:
    last_exc = None
    for attempt, delay in enumerate([0] + _RETRY_DELAYS_SECONDS):
        if delay:
            time.sleep(delay)
        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)
            if response.status_code == 200:
                return response
            if response.status_code in (429, 500, 502, 503, 504):
                last_exc = RuntimeError(f"Asana API returned {response.status_code}: {response.text[:200]}")
                continue
            response.raise_for_status()
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            last_exc = exc
            continue
    raise RuntimeError(f"Asana API request failed after retries: {last_exc}")
