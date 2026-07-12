"""Test: concurrent sessions — zero cross-session event leakage."""
import pytest, threading, time, tempfile
from pathlib import Path
from memory.memory_manager import MemoryManager


@pytest.fixture
def mm():
    tmp = tempfile.mkdtemp()
    mgr = MemoryManager(tmp)
    yield mgr
    import shutil; shutil.rmtree(tmp, ignore_errors=True)


def test_concurrent_sessions_no_cross_talk(mm):
    """10 parallel sessions: events from A must not appear in B."""
    errors = []
    results = {}

    def agent_work(sid):
        try:
            mm.start_session(session_id=sid, user_id=sid)
            for i in range(5):
                eid = mm.record_event(
                    "tool", "tool_output", f"data from {sid} step {i}",
                    session_id=sid,
                )
            # Retrieve — must only contain own events
            events = mm.get_recent_events(limit=20)
            own = [e for e in events if f"data from {sid}" in (e.get("content",""))]
            results[sid] = len(own)
        except Exception as e:
            errors.append(f"{sid}: {e}")

    threads = []
    for i in range(10):
        t = threading.Thread(target=agent_work, args=(f"sess-{i}",))
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0, f"Errors: {errors}"
    for sid, count in results.items():
        assert count == 5, f"{sid}: expected 5 own events, got {count}"
