"""Exercise the real process boundary without creating parent-side threads."""
import os
import shutil
import queue
import subprocess
import sys
import sysconfig
import threading
import time
import venv
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from blendjob.runtime import JobRuntime
from blendjob.process import TERMINAL_STATES
from blendjob.storage import read_json, write_json


@pytest.mark.parametrize("action,transition,finished", [
    ("stop", "STOPPING", "STOPPED"),
    ("enable", "STARTING", "READY"),
    ("restart", "STARTING", "READY"),
])
def test_lifecycle_transition_ignores_old_snapshots(tmp_path, action, transition, finished):
    from blendjob.process import ProcessServer
    server = ProcessServer(Mock(storage_root=lambda: tmp_path))
    server.control = Mock(directory=tmp_path, sequence=2)
    server.control.process.poll.return_value = None
    server.control.send.return_value = "operation"
    write_json(tmp_path / "server.json", {"state": "READY", "command_sequence": 1})
    getattr(server, action)()
    server.poll()
    assert server.snapshot["state"] == transition
    # A subsequent queued command can finish before Blender polls again.
    write_json(tmp_path / "server.json", {"state": finished, "command_sequence": 3})
    server.poll()
    assert server.snapshot["state"] == finished


def test_stop_failure_and_control_crash_end_transition(tmp_path):
    from blendjob.process import ProcessServer
    server = ProcessServer(Mock(storage_root=lambda: tmp_path))
    server.control = Mock(directory=tmp_path, sequence=1)
    server.control.send.return_value = "operation"
    server.control.process.poll.return_value = None
    server.stop()
    write_json(tmp_path / "server.json", {"state": "ERROR", "message": "Stop failed", "command_sequence": 1})
    server.poll()
    assert server.snapshot["message"] == "Stop failed"
    server.stop()
    server.control.process.poll.return_value = 1
    server.poll()
    assert server.snapshot["state"] == "ERROR"


def test_stop_stays_stopping_until_service_exits_and_can_restart(runtime):
    job = runtime.server.submit("wait", {})
    wait_for(runtime.server, job["job_id"], lambda state: state.get("state") == "running" and "directory" in state)
    runtime.server.stop()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        runtime.server.poll()
        state = runtime.server.snapshot["state"]
        assert state in {"STOPPING", "STOPPED"}, runtime.server.snapshot
        if state == "STOPPED":
            break
        time.sleep(0.025)
    assert state == "STOPPED"
    assert wait_for(runtime.server, job["job_id"])["state"] == "cancelled"
    assert not (runtime.storage_root() / "server-process.json").exists()
    # Allow a health poll: stopping must not enable automatic restart.
    time.sleep(1.2)
    runtime.server.poll()
    assert runtime.server.snapshot["state"] == "STOPPED"
    runtime.server.restart()
    job = runtime.server.submit("echo", {"restarted": True})
    assert wait_for(runtime.server, job["job_id"])["result"] == {"restarted": True}


@pytest.fixture
def runtime(tmp_path):
    entrypoint = tmp_path / "app.py"
    entrypoint.write_text('''from blendjob.server import JobServer
import time
server = JobServer("Process test")
@server.job("echo")
def echo(context, parameters):
    return parameters
@server.job("wait")
def wait(context, parameters):
    while True:
        context.check_cancelled()
        time.sleep(0.01)
@server.job("fail")
def fail(context, parameters):
    raise ValueError("Expected job failure")
@server.job("ignore-cancel")
def ignore_cancel(context, parameters):
    context.progress(0.1, "Ignoring cancellation")
    while True:
        time.sleep(0.01)
''', encoding="utf-8")
    root = tmp_path / "storage"
    environment = root / ".venv"
    venv.EnvBuilder(with_pip=False).create(environment)
    site = environment / ("Lib/site-packages" if os.name == "nt" else f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages")
    (site / "test-dependencies.pth").write_text(sysconfig.get_path("purelib") + "\n", encoding="utf-8")
    from blendjob.environment import environment_python
    probe = subprocess.run(
        [str(environment_python(root)), "-I", "-c",
         "import sys; print(sys.path); import fastapi, uvicorn"],
        capture_output=True, text=True,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    runtime = JobRuntime(f"{entrypoint}:server", storage_root=root,
                         environment={"python": f"{sys.version_info.major}.{sys.version_info.minor}"}, namespace="process_test")
    write_json(root / "manifest.json", {"environment_hash": runtime.config.environment_hash})
    with (
        patch.object(threading.Thread, "start", side_effect=AssertionError("Parent thread")),
        patch.object(queue, "Queue", side_effect=AssertionError("Parent queue")),
        patch.object(queue, "SimpleQueue", side_effect=AssertionError("Parent queue")),
    ):
        try:
            yield runtime
        finally:
            runtime.server.detach()


def wait_for(controller, job_id, predicate=lambda state: state.get("state") in TERMINAL_STATES):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        state = controller.status(job_id)
        if predicate(state):
            return state
        time.sleep(0.025)
    raise AssertionError(f"Task did not finish: {state}")


def wait_until(predicate):
    deadline = time.monotonic() + 10
    while not predicate():
        assert time.monotonic() < deadline, "Condition did not become true"
        time.sleep(0.025)


@pytest.mark.parametrize("new_location", [False, True])
def test_setup_transfers_running_server_and_directory_lock(runtime, tmp_path, new_location):
    from blendjob.storage import StorageLock
    source = runtime.storage_root()
    target = tmp_path / "prepared" if new_location else source
    if new_location:
        shutil.copytree(source, target)
    current = source
    validated = []

    def location(path=None):
        nonlocal current
        if path is not None:
            validated.append(read_json(path / "server-process.json")["pid"])
            current = path
        return current

    runtime.storage_root_setting = location
    setup = runtime.setup_controller(target, online=False)
    submitted = setup.submit("setup", {})
    assert wait_for(setup, submitted["job_id"])["state"] == "succeeded"
    setup.mark_job_complete(submitted["job_id"])
    assert runtime.request_blocking("echo", {"ready": True}).value == {"ready": True}
    assert read_json(target / "server-process.json")["pid"] == validated[0]
    with pytest.raises(RuntimeError):
        StorageLock().acquire(target)
    if new_location:
        lease = StorageLock()
        lease.acquire(source)
        lease.release()
    runtime.server.stop()
    wait_until(lambda: runtime.server.poll() and runtime.server.snapshot["state"] == "STOPPED")
    lease = StorageLock()
    lease.acquire(target)
    lease.release()
    wait_until(lambda: not list(runtime.server.control.directory.glob("status-*.json")))
    assert not list(runtime.server.control.directory.glob("commit-*"))
    assert not list(runtime.server.control.directory.glob("saving-*"))


def test_consumed_results_and_early_cancel_are_reclaimed_without_deleting_outputs(runtime):
    assert not hasattr(runtime, "request")
    results = [runtime.request_blocking("echo", {"n": n}) for n in range(3)]
    with pytest.raises(RuntimeError, match="Expected job failure"):
        runtime.request_blocking("fail", {})
    active = runtime.server.submit("wait", {})
    wait_for(runtime.server, active["job_id"], lambda state: "directory" in state)
    runtime.server.mark_job_complete(active["job_id"])
    assert runtime.request_blocking("echo", {"recovered": True}).value == {"recovered": True}
    directory = runtime.server.control.directory
    wait_until(lambda: not list(directory.glob("command-*.json")))
    wait_until(lambda: not list(directory.glob("status-*.json")))
    assert not list(directory.glob("cancel-*"))
    assert all(result.directory.is_dir() for result in results)


def test_submit_poll_result_and_cancel_without_parent_threads(runtime):
    start = time.monotonic()
    first = runtime.server.submit("echo", {"value": 42})
    assert time.monotonic() - start < 2
    result = wait_for(runtime.server, first["job_id"])
    assert result["state"] == "succeeded", result
    assert result["result"] == {"value": 42}
    assert Path(result["directory"]).is_dir()
    waiting = runtime.server.submit("wait", {})
    wait_for(runtime.server, waiting["job_id"], lambda state: state.get("state") == "running" and "directory" in state)
    runtime.server.cancel(waiting["job_id"])
    assert wait_for(runtime.server, waiting["job_id"])["state"] == "cancelled"
    again = runtime.server.submit("echo", {"value": "still alive"})
    assert wait_for(runtime.server, again["job_id"])["result"]["value"] == "still alive"


def test_cancel_during_cold_start_and_failure_recovery(runtime):
    job = runtime.server.submit("wait", {})
    runtime.server.cancel(job["job_id"])
    assert wait_for(runtime.server, job["job_id"])["state"] == "cancelled"
    failed = runtime.server.submit("fail", {})
    state = wait_for(runtime.server, failed["job_id"])
    assert state["state"] == "failed"
    assert "Expected job failure" in state["error"]
    completed = runtime.server.submit("echo", {})
    assert wait_for(runtime.server, completed["job_id"])["state"] == "succeeded"


def test_control_process_crash_is_reported(runtime):
    job = runtime.server.submit("echo", {})
    assert wait_for(runtime.server, job["job_id"])["state"] == "succeeded"
    runtime.server.control.process.kill()
    runtime.server.control.process.wait(timeout=5)
    with pytest.raises(RuntimeError, match="exited"):
        runtime.server.submit("echo", {})


def test_detach_resets_state_and_runtime_can_start_again(runtime):
    job = runtime.server.submit("echo", {})
    assert wait_for(runtime.server, job["job_id"])["state"] == "succeeded"
    previous_process = runtime.server.control.process
    runtime.server.snapshot = {"state": "READY", "resources": {"model": "loaded"}}
    runtime.server.detach()
    assert previous_process.poll() is not None
    assert runtime.server.snapshot == {"state": "STOPPED", "message": "Stopped"}
    assert runtime.server.pending_sequence == 0
    runtime.server.poll(available=False)
    assert runtime.server.snapshot["state"] == "UNAVAILABLE"
    runtime.server.poll(available=True)
    assert runtime.server.snapshot["state"] == "STOPPED"
    job = runtime.server.submit("echo", {"reopened": True})
    assert wait_for(runtime.server, job["job_id"])["result"] == {"reopened": True}


def test_queued_cancellation_does_not_submit_to_service(runtime):
    active = runtime.server.submit("wait", {})
    wait_for(runtime.server, active["job_id"], lambda state: state.get("state") == "running" and "directory" in state)
    queued = runtime.server.submit("echo", {"never": "executed"})
    runtime.server.cancel(queued["job_id"])
    runtime.server.cancel(active["job_id"])
    assert wait_for(runtime.server, queued["job_id"])["state"] == "cancelled"
    assert not Path(queued["directory"]).exists()


@pytest.mark.parametrize("save_fails", [False, True])
def test_offline_setup_saves_on_parent_and_propagates_failure(runtime, save_fails):
    source = runtime.storage_root()
    parent = threading.get_ident()
    saved = []

    def location(target=None):
        if target is not None:
            assert threading.get_ident() == parent
            if save_fails:
                raise RuntimeError("Settings save failed")
            saved.append(target)
        return source

    runtime.storage_root_setting = location
    controller = runtime.setup_controller(online=False)
    job = controller.submit("setup", {})
    result = wait_for(controller, job["job_id"])
    assert result["state"] == ("failed" if save_fails else "succeeded"), result
    assert saved == ([] if save_fails else [source])
    if save_fails:
        assert "Settings save failed" in result["error"]


def test_cancellation_stops_unresponsive_job_and_allows_restart(runtime):
    controller = runtime.server
    job = controller.submit("ignore-cancel", {})
    wait_for(controller, job["job_id"], lambda state: state.get("message") == "Ignoring cancellation")
    controller.cancel(job["job_id"])
    result = wait_for(controller, job["job_id"])
    assert result["state"] == "cancelled", result
    assert not (runtime.storage_root() / "server-process.json").exists()
    completed = runtime.server.submit("echo", {"after_cancel": True})
    assert wait_for(runtime.server, completed["job_id"])["result"] == {"after_cancel": True}


def test_shutdown_reaps_control_and_service(runtime):
    job = runtime.server.submit("wait", {})
    wait_for(runtime.server, job["job_id"], lambda state: state.get("state") == "running" and "directory" in state)
    process = runtime.server.control.process
    root = runtime.storage_root()
    record = read_json(root / "server-process.json")
    runtime.server.detach()
    assert process.poll() is not None
    from blendjob.processes import RegisteredServerProcess
    RegisteredServerProcess(record["pid"]).wait(timeout=5)


def test_job_file_cleanup_rejects_a_stale_service_instance(runtime):
    job = runtime.server.submit("echo", {})
    assert wait_for(runtime.server, job["job_id"])["state"] == "succeeded"
    preview = runtime.server.action("preview_files")
    result = wait_for(runtime.server, preview["job_id"])["result"]
    assert job["job_id"] in result["jobs"]
    invalid = runtime.server.action("clear_files", jobs=result["jobs"], instance_id="stale")
    assert wait_for(runtime.server, invalid["job_id"])["state"] == "failed"
    assert Path(job["directory"]).exists()
    clear = runtime.server.action("clear_files", jobs=result["jobs"], instance_id=result["instance_id"])
    assert wait_for(runtime.server, clear["job_id"])["state"] == "succeeded"
    assert not Path(job["directory"]).exists()


@pytest.mark.parametrize("save_fails", [False, True])
def test_migration_commits_before_removing_source(runtime, tmp_path, save_fails):
    source = runtime.storage_root()
    target = tmp_path / "moved"
    (source / "model.bin").write_bytes(b"model cache")
    current = source

    def location(path=None):
        nonlocal current
        if path is not None:
            assert source.is_dir()
            assert (target / "model.bin").read_bytes() == b"model cache"
            if save_fails:
                raise RuntimeError("Cannot save preferences")
            current = path
        return current

    runtime.storage_root_setting = location
    controller = runtime.migrate_controller(target)
    job = controller.submit("migrate", {})
    result = wait_for(controller, job["job_id"])
    assert result["state"] == ("failed" if save_fails else "succeeded"), result
    assert source.exists() == save_fails
    assert target.exists() != save_fails
    assert current == (source if save_fails else target)
    completed = runtime.server.submit("echo", {"migrated": not save_fails})
    assert wait_for(runtime.server, completed["job_id"])["state"] == "succeeded"
