"""A minimal, self-contained Asana API client — deliberately NOT
importing anything from outreach.py, since this whole sync is meant
to stay fully separate from the email outreach automation. Handles
pagination and basic retry on transient network failures; nothing
fancier than that is needed.

Almost entirely READ-ONLY: a once-per-run task list. The single
exception is update_task, used only by the Rights Secured assignee swap
(see ugc_tracker_sync.reassign_rights_secured_tasks), which changes one
task field — the assignee — and nothing else."""
import time
from typing import Dict, List
from urllib.parse import quote

import requests

ASANA_API_BASE = "https://app.asana.com/api/1.0"
_RETRY_DELAYS_SECONDS = [2, 5, 10]
_MAX_RETRY_AFTER_SECONDS = 60  # never sleep longer than this on one Asana "slow down" reply
# assignee.email (its gid always comes back with it) and completed are only
# there for the Rights Secured assignee swap; the Sheet sync ignores them.
_OPT_FIELDS = ("name,memberships.section.name,custom_fields.name,custom_fields.display_value,"
               "permalink_url,completed,assignee.email")


class AsanaWriteError(RuntimeError):
    """A write Asana rejected outright, or one that still failed after
    every retry."""


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


def get_user(identifier: str, token: str, label: str = "that user") -> Dict:
    """One Asana user, by email address or by gid. `label` is what the
    error message calls them — deliberately not the address itself,
    because this repository's Actions logs are public.

    Raises RuntimeError (never returns None) so a misconfigured user is a
    loud, specific failure instead of a swap that silently does nothing."""
    url = f"{ASANA_API_BASE}/users/{quote(identifier, safe='@')}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = _get_with_retries(url, headers=headers, params={"opt_fields": "name,email"})
    except requests.exceptions.HTTPError as exc:
        status = getattr(exc.response, "status_code", "?")
        raise RuntimeError(
            f"Asana could not find or read {label} (HTTP {status}). Check the email address, and that "
            f"the account belongs to the same Asana workspace as the token's owner."
        ) from exc
    user = response.json().get("data") or {}
    if not user.get("gid"):
        raise RuntimeError(f"Asana returned no user for {label}.")
    return user


def update_task(task_gid: str, patch: Dict, token: str) -> None:
    """Applies `patch` (a dict of task fields, e.g. {"assignee": "<gid>"})
    to one task. Safe to repeat: setting a field to the value it already
    has changes nothing, which is what makes retrying after a timeout
    harmless even if the first attempt actually got through.

    Retries timeouts, connection errors, 5xx, and 429 (honouring Asana's
    Retry-After, capped). Any other HTTP error — a 403 for no permission,
    a 404 for a deleted task — is raised at once as AsanaWriteError,
    because repeating it cannot help."""
    url = f"{ASANA_API_BASE}/tasks/{task_gid}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    last_problem = None
    for delay in [0] + _RETRY_DELAYS_SECONDS:
        if delay:
            time.sleep(delay)
        try:
            response = requests.put(url, headers=headers, json={"data": patch}, timeout=30)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            last_problem = exc
            continue
        if response.status_code == 200:
            return
        if response.status_code in (429, 500, 502, 503, 504):
            last_problem = f"Asana API returned {response.status_code}"
            if response.status_code == 429:
                time.sleep(_retry_after_seconds(response))
            continue
        raise AsanaWriteError(
            f"Asana rejected the update to task {task_gid} (HTTP {response.status_code}): {response.text[:200]}")
    raise AsanaWriteError(f"Asana update to task {task_gid} still failing after retries: {last_problem}")


def _retry_after_seconds(response) -> float:
    try:
        return min(max(float(response.headers.get("Retry-After", 0)), 0.0), _MAX_RETRY_AFTER_SECONDS)
    except (TypeError, ValueError):
        return 0.0
