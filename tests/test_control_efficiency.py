"""Check idle scheduling, unchanged snapshots and consumed IPC lifetimes."""
from concurrent.futures import Future
import os
from types import SimpleNamespace
from unittest.mock import Mock, patch

from blendjob.control import ControlService
from blendjob.storage import write_json


def service_at(tmp_path):
    entrypoint = tmp_path / "app.py"
    entrypoint.touch()
    write_json(tmp_path / "config.json", {
        "server_entrypoint": f"{entrypoint}:server", "environment": {"python": "3.12"},
        "storage_root": str(tmp_path / "storage"), "parent_pid": os.getpid(),
    })
    return ControlService(tmp_path)


def test_unchanged_progress_is_not_republished_and_terminal_status_is_kept(tmp_path):
    service = service_at(tmp_path)
    service.server = Mock(snapshot={"state": "BUSY"})
    status = {"state": "running", "progress": 0.5, "message": "Working"}
    with patch("blendjob.control.write_json") as write:
        for _ in range(20):
            service.publish("job", dict(status))
            service.publish_server()
        assert write.call_count == 2
        service.publish("job", {**status, "state": "succeeded", "result": 42})
        assert write.call_count == 3
        # Sequence changes must propagate even when the display state is unchanged.
        service.completed_sequence = 2
        service.publish_server()
        assert write.call_count == 4
        service.release("job")
        assert not service.states


def test_idle_health_checks_follow_backend_interval_without_scanning_directory(tmp_path):
    service = service_at(tmp_path)
    now = 0.0
    checks = []

    def sleep(seconds):
        nonlocal now
        now += seconds

    def poll(**kwargs):
        checks.append(now)
        return 5.0

    class Executor:
        def __init__(self, **kwargs):
            pass

        def submit(self, function, *args):
            future = Future()
            future.set_result(function(*args))
            return future

        def shutdown(self, **kwargs):
            pass

    service.parent = SimpleNamespace(poll=lambda: None if now < 11 else 0)
    service.server = Mock(snapshot={"state": "READY"}, poll=poll)
    with (
        patch("blendjob.control.ThreadPoolExecutor", Executor),
        patch("blendjob.control.time.sleep", sleep),
        patch("blendjob.control.time.monotonic", lambda: now),
        patch.object(type(tmp_path), "glob", side_effect=AssertionError("Directory scan")),
        patch("blendjob.control.write_json") as write,
    ):
        service.run()
    assert len(checks) == 3
    assert all(later - earlier >= 5 for earlier, later in zip(checks, checks[1:]))
    assert write.call_count == 1
    service.server.stop.assert_called_once()
