"""Thin GitHub REST API client. Every network call is isolated to this
module and goes through `requests`, so tests can mock `requests.*` directly
without touching real GitHub.

Token scope needed:
- actions: read, actions: write  -> dispatch_workflow, get_run, find_recent_run
- contents: write -> create_file / commit_campaign_files_directly (New
  Campaign page, template edits, campaign settings)
- secrets: write -> get_repo_public_key / set_secret / delete_secret (Email
  Accounts management ONLY — a materially larger grant than anything
  else in this app needs; see those methods' docstrings for what this
  can and can't do)
"""
import base64
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, Optional

import requests

GITHUB_API = "https://api.github.com"
DEFAULT_TIMEOUT = 20

# ---------------------------------------------------------------------------
# Optional, SHA-keyed read cache (opt-in per client via cache_reads=True).
#
# Every live read this app makes asks GitHub for "main" — a moving target —
# so nothing about it could safely be cached, and a single campaign page
# load (which re-runs on every click) made dozens of sequential API calls.
# The cache below is safe specifically because it is keyed by COMMIT SHA:
# the contents of a file at a given commit can never change, so an entry
# can never be stale for that commit, and a new commit simply has a new
# SHA that misses the cache and reads fresh. Staleness is therefore
# bounded only by how recently the head SHA itself was looked up
# (_HEAD_SHA_TTL_SECONDS — a few seconds), never by how old a cached file
# is — and every write this app makes through GitHubClient invalidates the
# whole thing immediately, so the app never shows its OWN changes late.
#
# Module-level (not per-instance) on purpose: several pages each hold
# their own client, and a write through one must invalidate reads cached
# through another.
# ---------------------------------------------------------------------------
_HEAD_SHA_TTL_SECONDS = 3.0
_READ_CACHE_LOCK = threading.Lock()
_HEAD_SHAS: Dict[tuple, tuple] = {}   # (owner, repo, branch) -> (sha, fetched_at_monotonic)
_READ_ENTRIES: Dict[tuple, object] = {}  # (owner, repo, sha, kind, path) -> value


def _invalidate_read_cache() -> None:
    """Drops every cached head SHA and every cached read. Called after
    any write this app makes, so a change it just committed is always
    visible on the very next read, never hidden behind a cached head."""
    with _READ_CACHE_LOCK:
        _HEAD_SHAS.clear()
        _READ_ENTRIES.clear()


class GitHubActionsError(Exception):
    pass


class GitHubClient:
    def __init__(self, token: str, owner: str, repo: str, timeout: int = DEFAULT_TIMEOUT,
                 cache_reads: bool = False):
        self.owner = owner
        self.repo = repo
        self.timeout = timeout
        # Off by default — every existing caller and test behaves exactly
        # as before. See the module-level comment above for what turning
        # this on does and why it can't serve stale data.
        self.cache_reads = cache_reads
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    # ---------- Triggering + polling workflow runs ----------

    def dispatch_workflow(self, workflow_file: str, inputs: Dict[str, str],
                           ref: str = "main") -> Optional[Dict]:
        """Triggers workflow_dispatch. Returns {'id':..., 'html_url':...}
        directly when the API's return_run_details feature is available;
        returns None otherwise (caller should fall back to
        find_recent_run)."""
        url = f"{GITHUB_API}/repos/{self.owner}/{self.repo}/actions/workflows/{workflow_file}/dispatches"
        payload = {"ref": ref, "inputs": inputs, "return_run_details": True}
        resp = requests.post(url, json=payload, headers=self._headers, timeout=self.timeout)
        if resp.status_code not in (200, 204):
            raise GitHubActionsError(
                f"Failed to dispatch '{workflow_file}': {resp.status_code} {resp.text[:300]}"
            )
        if resp.status_code == 200 and resp.content:
            return resp.json()
        return None

    def get_run(self, run_id: int) -> Dict:
        url = f"{GITHUB_API}/repos/{self.owner}/{self.repo}/actions/runs/{run_id}"
        resp = requests.get(url, headers=self._headers, timeout=self.timeout)
        if resp.status_code != 200:
            raise GitHubActionsError(f"Failed to fetch run {run_id}: {resp.status_code} {resp.text[:300]}")
        return resp.json()

    def find_recent_run(self, workflow_file: str, branch: str = "main") -> Optional[Dict]:
        """Fallback correlation if dispatch_workflow returned None — most
        recent workflow_dispatch run for this workflow/branch. Best-effort;
        can theoretically race with a second concurrent trigger."""
        url = f"{GITHUB_API}/repos/{self.owner}/{self.repo}/actions/workflows/{workflow_file}/runs"
        resp = requests.get(
            url, headers=self._headers,
            params={"event": "workflow_dispatch", "branch": branch, "per_page": 1},
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            raise GitHubActionsError(f"Failed to list runs for '{workflow_file}': {resp.status_code} {resp.text[:300]}")
        runs = resp.json().get("workflow_runs", [])
        return runs[0] if runs else None

    # ---------- Campaign creation — direct commit, no branch/PR ----------
    #
    # Deliberately a direct commit to `base` (main), not a PR: the goal is
    # for campaign creation to never require a trip to GitHub. The
    # remaining safety net is the in-app confirmation the Streamlit page
    # requires before calling this — see campaign_builder.py.

    # ---------- SHA-keyed read cache helpers (see module comment) ----------

    def _head_sha(self, branch: str = "main") -> Optional[str]:
        """The current head commit SHA of `branch`, from one very small
        request (the sha media type returns just the SHA text), reused
        for a few seconds so a single page render's many reads share one
        lookup. Returns None on ANY failure — callers then fall back to
        the exact same direct read this client always did, so a failed
        lookup can make things no slower or less correct than before."""
        key = (self.owner, self.repo, branch)
        now = time.monotonic()
        with _READ_CACHE_LOCK:
            cached = _HEAD_SHAS.get(key)
            if cached and now - cached[1] < _HEAD_SHA_TTL_SECONDS:
                return cached[0]
        try:
            resp = requests.get(
                f"{GITHUB_API}/repos/{self.owner}/{self.repo}/commits/{branch}",
                headers={**self._headers, "Accept": "application/vnd.github.sha"},
                timeout=self.timeout,
            )
        except requests.RequestException:
            return None
        sha = (resp.text or "").strip() if resp.status_code == 200 else ""
        if len(sha) != 40 or any(ch not in "0123456789abcdef" for ch in sha):
            return None
        with _READ_CACHE_LOCK:
            previous = _HEAD_SHAS.get(key)
            if previous and previous[0] != sha:
                # A new commit landed — everything cached at the old one
                # is now just dead weight, so drop it rather than let
                # entries accumulate forever.
                for entry_key in [k for k in _READ_ENTRIES if k[0] == self.owner and k[1] == self.repo
                                  and k[2] == previous[0]]:
                    del _READ_ENTRIES[entry_key]
            _HEAD_SHAS[key] = (sha, now)
        return sha

    def _cached_read(self, kind: str, path: str, ref: str, fetch: Callable[[str], object]):
        """Runs fetch(ref_to_use), through the SHA-keyed cache when this
        client has caching on and the read is for the moving "main"
        ref; otherwise exactly fetch(ref), unchanged. The actual fetch
        is made AT the resolved SHA, not "main", so what's cached under
        a SHA is always genuinely that commit's content even if main
        moves between the lookup and the read."""
        if not self.cache_reads or ref != "main":
            return fetch(ref)
        sha = self._head_sha(ref)
        if sha is None:
            return fetch(ref)
        key = (self.owner, self.repo, sha, kind, path)
        with _READ_CACHE_LOCK:
            if key in _READ_ENTRIES:
                value = _READ_ENTRIES[key]
                return list(value) if isinstance(value, list) else value
        value = fetch(sha)
        with _READ_CACHE_LOCK:
            _READ_ENTRIES[key] = list(value) if isinstance(value, list) else value
        return value

    def warm_file_cache(self, paths: List[str], ref: str = "main", max_workers: int = 8) -> None:
        """Reads several files at once so the page code that then reads
        them one by one (unchanged) finds every one already cached. A
        no-op when caching is off, and never raises — a path that fails
        to read here just gets read (and fails, or not) normally later."""
        if not self.cache_reads or ref != "main" or not paths:
            return
        if self._head_sha(ref) is None:
            return

        def _one(path: str) -> None:
            try:
                self.get_file_content(path, ref)
            except Exception:  # noqa: BLE001 - warming is best-effort only
                pass

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            list(pool.map(_one, paths))

    def get_file_sha(self, path: str, ref: str = "main") -> Optional[str]:
        """Current SHA of the file at `path` on `ref`, or None if it
        doesn't exist yet. GitHub's contents API requires this SHA when
        updating an existing file — omitting it (as an earlier version of
        create_file did) works fine for brand-new files but is rejected
        with a 422 for anything that already exists, which is exactly
        what editing a template or updating campaign settings does."""
        url = f"{GITHUB_API}/repos/{self.owner}/{self.repo}/contents/{path}"
        resp = requests.get(url, headers=self._headers, params={"ref": ref}, timeout=self.timeout)
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise GitHubActionsError(f"Failed to check existing file '{path}': {resp.status_code} {resp.text[:300]}")
        return resp.json().get("sha")

    def list_directory_files(self, path: str, ref: str = "main") -> List[str]:
        """Filenames (not full paths) directly inside a repo directory,
        read fresh from GitHub's own current state — deliberately NOT the
        local filesystem Streamlit Cloud happens to have checked out,
        which can lag behind a very recent commit until the next
        redeploy finishes. Returns [] if the directory doesn't exist,
        rather than raising — callers that need "must actually have
        files" should check for that themselves (see
        campaign_builder.build_campaign_duplication_files, which refuses
        to proceed on an empty result rather than silently creating a
        duplicate with nothing in it)."""
        def _fetch(read_ref: str) -> List[str]:
            url = f"{GITHUB_API}/repos/{self.owner}/{self.repo}/contents/{path}"
            resp = requests.get(url, headers=self._headers, params={"ref": read_ref}, timeout=self.timeout)
            if resp.status_code == 404:
                return []
            if resp.status_code != 200:
                raise GitHubActionsError(f"Failed to list '{path}': {resp.status_code} {resp.text[:300]}")
            entries = resp.json()
            return [entry["name"] for entry in entries if entry.get("type") == "file"]

        return self._cached_read("files", path, ref, _fetch)

    def list_subdirectories(self, path: str, ref: str = "main") -> List[str]:
        """Directory names directly inside a repo directory, read fresh
        from GitHub. The counterpart to list_directory_files, which
        deliberately filters to type == "file" and so can never see a
        campaign's own folder.

        Needed because the campaign list itself is derived from which
        folders exist under templates/ — so creating, duplicating or
        deleting a campaign changed GitHub immediately while the app
        kept listing the frozen local checkout until a redeploy.

        Returns [] if the directory doesn't exist, matching
        list_directory_files' contract."""
        def _fetch(read_ref: str) -> List[str]:
            url = f"{GITHUB_API}/repos/{self.owner}/{self.repo}/contents/{path}"
            resp = requests.get(url, headers=self._headers, params={"ref": read_ref}, timeout=self.timeout)
            if resp.status_code == 404:
                return []
            if resp.status_code != 200:
                raise GitHubActionsError(f"Failed to list '{path}': {resp.status_code} {resp.text[:300]}")
            entries = resp.json()
            return [entry["name"] for entry in entries if entry.get("type") == "dir"]

        return self._cached_read("dirs", path, ref, _fetch)

    def get_file_content(self, path: str, ref: str = "main") -> bytes:
        """Raw bytes of one file's current content, read fresh from
        GitHub — same "authoritative, never the local checkout" reason
        as list_directory_files. Raises if the file doesn't exist."""
        def _fetch(read_ref: str) -> bytes:
            url = f"{GITHUB_API}/repos/{self.owner}/{self.repo}/contents/{path}"
            resp = requests.get(url, headers=self._headers, params={"ref": read_ref}, timeout=self.timeout)
            if resp.status_code != 200:
                raise GitHubActionsError(f"Failed to read '{path}': {resp.status_code} {resp.text[:300]}")
            return base64.b64decode(resp.json()["content"])

        return self._cached_read("content", path, ref, _fetch)

    def create_file(self, path: str, content_bytes: bytes, message: str, branch: str = "main") -> None:
        """Creates OR updates a file at `path`. Every write in this app —
        new templates, edited templates, campaign settings — goes through
        this one method, so fetching the current SHA here (when the file
        already exists) fixes update-in-place for all of them at once."""
        existing_sha = self.get_file_sha(path, ref=branch)
        url = f"{GITHUB_API}/repos/{self.owner}/{self.repo}/contents/{path}"
        payload = {
            "message": message,
            "content": base64.b64encode(content_bytes).decode("ascii"),
            "branch": branch,
        }
        if existing_sha:
            payload["sha"] = existing_sha
        try:
            resp = requests.put(url, json=payload, headers=self._headers, timeout=self.timeout)
        finally:
            # Invalidated whether or not the write succeeded — a failed
            # attempt may still have partially landed, and the one thing
            # that must never happen is a later read serving a cached
            # copy from before this call.
            _invalidate_read_cache()
        if resp.status_code not in (200, 201):
            raise GitHubActionsError(f"Failed to create/update file '{path}': {resp.status_code} {resp.text[:300]}")

    def delete_file(self, path: str, message: str, branch: str = "main") -> None:
        """Deletes a file at `path`. GitHub's contents API requires the
        current SHA to delete, same as create_file requires for an
        update — fetched fresh here. If the file is already gone, this is
        a no-op, not an error — the caller's goal ('this file shouldn't
        exist') is already satisfied, same philosophy as delete_secret."""
        existing_sha = self.get_file_sha(path, ref=branch)
        if existing_sha is None:
            return
        url = f"{GITHUB_API}/repos/{self.owner}/{self.repo}/contents/{path}"
        payload = {"message": message, "sha": existing_sha, "branch": branch}
        try:
            resp = requests.delete(url, json=payload, headers=self._headers, timeout=self.timeout)
        finally:
            _invalidate_read_cache()
        if resp.status_code not in (200, 204):
            raise GitHubActionsError(f"Failed to delete file '{path}': {resp.status_code} {resp.text[:300]}")

    def commit_campaign_files_directly(self, files: List[Dict[str, bytes]], commit_message: str,
                                        base: str = "main") -> None:
        """files: [{'path': 'templates/Foo/intro_A.txt', 'content': b'...'}].
        Commits every file straight to `base`. Raises on the first failure —
        callers should treat a partial failure as "check the repo", since a
        prior file in the list may have already landed."""
        for f in files:
            self.create_file(f["path"], f["content"], message=commit_message, branch=base)

    # ---------- Repository secrets — set/delete only, NEVER read ----------
    #
    # GitHub Secrets are write-only by design: this API can set or delete a
    # secret's value, but there is no endpoint that returns an existing
    # value, to this token or any other. That's the actual security
    # property everything here relies on — Streamlit briefly holds a
    # plaintext password only for the instant it takes to encrypt and send
    # it below; it's never stored, logged, or displayed anywhere by this
    # client. Requires a token with `secrets: write` — a materially larger
    # grant than anything else in this app needs, used ONLY by the Email
    # Accounts management page.

    def get_repo_public_key(self) -> Dict[str, str]:
        """{'key_id': ..., 'key': <base64>} — GitHub's current public key
        for this repo, used to encrypt every secret value before it's ever
        sent over the wire. Fetched fresh each time rather than cached,
        since GitHub can rotate this key."""
        url = f"{GITHUB_API}/repos/{self.owner}/{self.repo}/actions/secrets/public-key"
        resp = requests.get(url, headers=self._headers, timeout=self.timeout)
        if resp.status_code != 200:
            raise GitHubActionsError(f"Failed to fetch repo public key: {resp.status_code} {resp.text[:300]}")
        return resp.json()

    def encrypt_secret_value(self, plaintext: str, public_key_b64: str) -> str:
        """Libsodium sealed-box encryption, exactly as GitHub's API
        requires (https://docs.github.com/en/rest/actions/secrets) — a
        one-way encryption only GitHub's own private key can open. Kept as
        its own method (no network call) so it's directly unit-testable
        without touching the API."""
        from nacl import encoding, public
        public_key = public.PublicKey(public_key_b64.encode("utf-8"), encoding.Base64Encoder())
        sealed_box = public.SealedBox(public_key)
        encrypted = sealed_box.encrypt(plaintext.encode("utf-8"))
        return base64.b64encode(encrypted).decode("utf-8")

    def set_secret(self, secret_name: str, plaintext_value: str) -> None:
        """Encrypts plaintext_value with the repo's CURRENT public key
        (fetched fresh, never cached — see get_repo_public_key) and sets
        it as a repository secret. Creates the secret if it doesn't exist,
        overwrites it if it does — GitHub's API doesn't distinguish the
        two operations."""
        key_info = self.get_repo_public_key()
        encrypted_value = self.encrypt_secret_value(plaintext_value, key_info["key"])
        url = f"{GITHUB_API}/repos/{self.owner}/{self.repo}/actions/secrets/{secret_name}"
        payload = {"encrypted_value": encrypted_value, "key_id": key_info["key_id"]}
        resp = requests.put(url, json=payload, headers=self._headers, timeout=self.timeout)
        if resp.status_code not in (201, 204):
            raise GitHubActionsError(f"Failed to set secret '{secret_name}': {resp.status_code} {resp.text[:300]}")

    def delete_secret(self, secret_name: str) -> None:
        """204 means deleted; 404 means it was already gone — both count
        as success here, since the caller's goal ("this secret should not
        exist") is satisfied either way."""
        url = f"{GITHUB_API}/repos/{self.owner}/{self.repo}/actions/secrets/{secret_name}"
        resp = requests.delete(url, headers=self._headers, timeout=self.timeout)
        if resp.status_code not in (204, 404):
            raise GitHubActionsError(f"Failed to delete secret '{secret_name}': {resp.status_code} {resp.text[:300]}")
