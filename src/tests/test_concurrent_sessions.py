"""Test: concurrent sessions — known limitation (global step_counter).

Full session isolation requires per-session step_counter (v0.3.2).
See: https://github.com/neuratechcompany-ops/Kettu-Mem/issues
"""

import threading
import tempfile
import shutil

import pytest

from memory.memory_manager import MemoryManager


@pytest.fixture
def mm():
    tmp = tempfile.mkdtemp()
    mgr = MemoryManager(tmp)
    yield mgr
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.xfail(
    strict=True,
    reason="global _step_counter prevents true concurrent isolation (v0.3.2)"
)
def test_concurrent_sessions_known_limitation(mm):
    """
    10 sessions, 20 events each, parallel writes.

    Known limitation: _step_counter is global, so concurrent sessions
    may interleave step_ids. Full fix requires SessionRuntime per session
    (deferred to v0.3.2).
    """
    errors = []
    per_session_events = {}
    lock = threading.Lock()

    def agent_work(sid):
        try:
            mm.start_session(session_id=sid, user_id=sid)
            my_events = []
            for i in range(20):
                mm.record_event(
                    "tool",
                    "tool_output",
                    f"data-from-{sid}-step-{i}",
                    session_id=sid,
                )
                my_events.append(f"data-from-{sid}-step-{i}")
            with lock:
                per_session_events[sid] = my_events
        except Exception as e:
            errors.append(f"{sid}: {e}")

    threads = []
    for i in range(10):
        t = threading.Thread(
            target=agent_work,
            args=(f"sess-{i:02d}",),
        )
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0, f"Errors: {errors}"

    for sid, my_events in per_session_events.items():
        assert len(my_events) == 20, (
            f"{sid}: expected 20, got {len(my_events)}"
        )
        for other_sid, other_events in per_session_events.items():
            if sid == other_sid:
                continue
            leaked = [e for e in my_events if other_sid in e]
            assert len(leaked) == 0, (
                f"CROSS LEAK: {sid} contains {other_sid}: {leaked}"
            )
