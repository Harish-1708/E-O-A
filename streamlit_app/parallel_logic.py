"""A tiny helper for running independent, slow, read-only network calls
(Google Sheets reads) at the same time instead of one after another.

Deliberately minimal and Streamlit-free: callers resolve anything that
needs Streamlit (cached connectors, secrets) on the normal script thread
BEFORE handing plain callables here, so nothing running in a worker
thread ever touches st.* — worker threads have no script-run context and
shouldn't be asked to behave as though they do.
"""
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, List, Tuple

# Google's Sheets API allows roughly 60 read requests per minute per user.
# A modest cap keeps a burst of concurrent reads comfortably inside that,
# while still turning "N sequential round trips" into "about one".
DEFAULT_MAX_WORKERS = 6


def _capture(fn: Callable[[], Any]) -> Tuple[bool, Any]:
    try:
        return True, fn()
    except Exception as exc:  # noqa: BLE001 - captured so one failure can't hide the others' results
        return False, exc


def run_in_parallel(callables: List[Callable[[], Any]],
                    max_workers: int = DEFAULT_MAX_WORKERS) -> List[Tuple[bool, Any]]:
    """Runs every zero-argument callable and returns [(ok, value_or_exception)]
    in the SAME ORDER as the input — never in completion order — so callers
    can pair results back to inputs by position.

    Exceptions are captured and returned, not raised: the callers here
    each need their own error policy (the Campaigns list skips just the
    one broken campaign; the campaign page re-raises the first failure),
    which only works if every task gets to finish and report.

    max_workers <= 1, or a single callable, runs sequentially on the
    calling thread with no pool at all — identical behavior to a plain
    loop, which is exactly what every pre-existing code path was."""
    if not callables:
        return []
    if max_workers <= 1 or len(callables) == 1:
        return [_capture(fn) for fn in callables]
    with ThreadPoolExecutor(max_workers=min(max_workers, len(callables))) as pool:
        futures = [pool.submit(_capture, fn) for fn in callables]
        return [f.result() for f in futures]
