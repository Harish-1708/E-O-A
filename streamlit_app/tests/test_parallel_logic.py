import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from parallel_logic import run_in_parallel


def test_results_come_back_in_input_order_not_completion_order():
    """Callers pair results to inputs by position, so a task that
    finishes LAST must still be reported FIRST if it was first."""
    def slow():
        time.sleep(0.15)
        return "slow-first"
    results = run_in_parallel([slow, lambda: "fast-second", lambda: "fast-third"], max_workers=3)
    assert results == [(True, "slow-first"), (True, "fast-second"), (True, "fast-third")]


def test_an_exception_is_captured_and_does_not_stop_the_other_tasks():
    def boom():
        raise ValueError("this one failed")
    results = run_in_parallel([lambda: "ok-1", boom, lambda: "ok-3"], max_workers=3)
    assert results[0] == (True, "ok-1")
    assert results[1][0] is False and isinstance(results[1][1], ValueError)
    assert results[2] == (True, "ok-3")


def test_tasks_genuinely_run_at_the_same_time():
    """Proof of real concurrency, not just a pool existing: three tasks
    that each wait for the other two can only ALL finish if they're
    running simultaneously — run one after another, the first would
    wait forever (here: until the barrier times out)."""
    barrier = threading.Barrier(3, timeout=5)
    def meet():
        barrier.wait()
        return "met"
    results = run_in_parallel([meet, meet, meet], max_workers=3)
    assert results == [(True, "met")] * 3


def test_max_workers_of_one_is_truly_sequential():
    """The documented default behavior every pre-existing code path
    relied on: one at a time, in order, on the calling thread."""
    barrier = threading.Barrier(2, timeout=0.3)
    def meet():
        barrier.wait()
    results = run_in_parallel([meet, meet], max_workers=1)
    # Run sequentially, neither can ever meet the other — both time out.
    assert all(ok is False for ok, _ in results)


def test_a_single_task_runs_on_the_calling_thread_with_no_pool():
    seen = []
    run_in_parallel([lambda: seen.append(threading.get_ident())], max_workers=6)
    assert seen == [threading.get_ident()]


def test_empty_input_returns_empty():
    assert run_in_parallel([]) == []


def test_max_workers_actually_caps_concurrency():
    """The cap exists to keep a burst of reads inside Google's
    per-minute quota — it must really limit how many run at once."""
    lock = threading.Lock()
    state = {"active": 0, "peak": 0}
    def task():
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        time.sleep(0.05)
        with lock:
            state["active"] -= 1
    run_in_parallel([task] * 8, max_workers=2)
    assert state["peak"] == 2
