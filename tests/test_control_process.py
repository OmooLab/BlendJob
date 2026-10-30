"""Exercise the real process boundary without creating parent-side threads."""
import os
import shutil
import queue
import subprocess
import sys
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
    ("stop", "STOPPING", "OFFLINE"),
    ("start", "STARTING", "IDLE"),
    ("restart", "STARTING", "IDLE"),
])
def test_lifecycle_transition_ignores_old_snapshots(tmp_path, action, transition, finished):
    from blendjob.process import ProcessServer
    server = ProcessServer(Mock(storage_root=lambda: tmp_path))
    server.control = Mock(directory=tmp_path, sequence=2)
    server.control.process.poll.return_value = None
    server.control.send.return_value = "operation"
    write_json(tmp_path / "server.json", {"state": "IDLE", "command_sequence": 1})
    server.action(action)
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
    server.action("stop")
    write_json(tmp_path / "server.json", {"state": "ERROR", "message": "Stop failed", "command_sequence": 1})
    server.poll()
    assert server.snapshot["message"] == "Stop failed"
    server.action("stop")
    server.control.process.poll.return_value = 1
    server.poll()
    assert server.snapshot["state"] == "ERROR"


def test_stop_stays_stopping_until_service_exits_and_can_restart(runtime):
    job = runtime.server.submit("wait", {})
    wait_for(runtime.server, job["job_id"], lambda state: state.get("state") == "running" and "directory" in state)
    stopping = runtime.stop()
    wait_until(lambda: (runtime._tick() or stopping.done))
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        runtime.server.poll()
        state = runtime.server.snapshot["state"]
        assert state in {"STOPPING", "OFFLINE"}, runtime.server.snapshot
        if state == "OFFLINE":
            break
        time.sleep(0.025)
    assert state == "OFFLINE"
    assert wait_for(runtime.server, job["job_id"])["state"] == "cancelled"
    assert not (runtime.storage_root() / "server-process.json").exists()
    # Allow a health poll: stopping must not enable automatic restart.
    time.sleep(1.2)
    runtime.server.poll()
    assert runtime.server.snapshot["state"] == "OFFLINE"
    runtime.server.action("restart")
    job = runtime.server.submit("echo", {"restarted": True})
    assert wait_for(runtime.server, job["job_id"])["result"] == {"restarted": True}


@pytest.fixture
def runtime(tmp_path):
    entrypoint = tmp_path / "app.py"
    entrypoint.write_text('''from blendjob import JobServer
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
@server.job("cold")
def cold(context, parameters):
    import ctypes, sys
    count = context.storage_root / "cold-count.txt"
    count.write_text(count.read_text() + "x" if count.exists() else "x")
    if sys.platform == "win32":
        ctypes.PyDLL("kernel32").Sleep(3200)
    else:
        ctypes.PyDLL(None).sleep(3)
    return parameters
@server.job("fail")
def fail(context, parameters):
    raise ValueError("Expected job failure")
@server.job("ignore_cancel")
def ignore_cancel(context, parameters):
    context.progress(0.1, "Ignoring cancellation")
    while True:
        time.sleep(0.01)
''', encoding="utf-8")
    root = tmp_path / "storage"
    environment = root / ".venv"
    venv.EnvBuilder(with_pip=False, symlinks=os.name != "nt").create(environment)
    site = environment / ("Lib/site-packages" if os.name == "nt" else f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages")
    dependencies = [path for path in sys.path if Path(path).name == "site-packages"]
    (site / "test-dependencies.pth").write_text("\n".join(dependencies) + "\n", encoding="utf-8")
    from blendjob.environment import environment_python
    probe = subprocess.run(
        [str(environment_python(root)), "-I", "-c",
         "import sys; print(sys.path); import fastapi, uvicorn"],
        capture_output=True, text=True,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    runtime = JobRuntime(f"{entrypoint}:server", storage_root=root,
                         environment={"python": f"{sys.version_info.major}.{sys.version_info.minor}"}, namespace="process_test")
    write_json(root / "manifest.json", {"environment_hash": runtime._config.environment_hash})
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
    raise AssertionError(f"Job did not finish: {state}")


def wait_until(predicate):
    deadline = time.monotonic() + 10
    while not predicate():
        assert time.monotonic() < deadline, "Condition did not become true"
        time.sleep(0.025)


def test_setup_transfers_running_server_and_directory_lock(runtime, tmp_path):
    from blendjob.storage import StorageLock
    source = runtime.storage_root()
    target = tmp_path / "prepared"
    shutil.copytree(source, target, symlinks=True)
    (source / "manifest.json").unlink()
    current = source
    validated = []

    def location(path=None):
        nonlocal current
        if path is not None:
            validated.append(read_json(path / "server-process.json")["pid"])
            current = path
        return current

    runtime.storage_root_setting = location
    setup = runtime.setup(target, online=False)
    wait_until(lambda: (runtime._tick() or setup.done))
    assert setup.state == "succeeded", setup.error
    assert runtime.submit("echo", {"ready": True}).wait().value == {"ready": True}
    assert read_json(target / "server-process.json")["pid"] == validated[0]
    with pytest.raises(RuntimeError):
        StorageLock().acquire(target)
    lease = StorageLock()
    lease.acquire(source)
    lease.release()
    stopping = runtime.stop()
    wait_until(lambda: (runtime._tick() or stopping.done))
    wait_until(lambda: runtime.server.poll() and runtime.server.snapshot["state"] == "OFFLINE")
    lease = StorageLock()
    lease.acquire(target)
    lease.release()
    wait_until(lambda: not list(runtime.server.control.directory.glob("status-*.json")))
    assert not list(runtime.server.control.directory.glob("commit-*"))
    assert not list(runtime.server.control.directory.glob("saving-*"))


def test_consumed_results_and_early_cancel_are_reclaimed_without_deleting_outputs(runtime):
    assert not hasattr(runtime, "request")
    results = [runtime.submit("echo", {"n": n}).wait() for n in range(3)]
    with pytest.raises(RuntimeError, match="Expected job failure"):
        runtime.submit("fail", {}).wait()
    active = runtime.server.submit("wait", {})
    wait_for(runtime.server, active["job_id"], lambda state: "directory" in state)
    runtime.server.mark_job_complete(active["job_id"])
    assert runtime.submit("echo", {"recovered": True}).wait().value == {"recovered": True}
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
    runtime.server.snapshot = {"state": "IDLE", "resources": {"model": "loaded"}}
    runtime.server.detach()
    assert previous_process.poll() is not None
    assert runtime.server.snapshot == {"state": "OFFLINE", "message": "Offline"}
    assert runtime.server.pending_sequence == 0
    runtime.server.poll(available=False)
    assert runtime.server.snapshot["state"] == "UNAVAILABLE"
    runtime.server.poll(available=True)
    assert runtime.server.snapshot["state"] == "OFFLINE"
    job = runtime.server.submit("echo", {"reopened": True})
    assert wait_for(runtime.server, job["job_id"])["result"] == {"reopened": True}


def test_queued_cancellation_does_not_submit_to_service(runtime):
    active = runtime.server.submit("wait", {})
    wait_for(runtime.server, active["job_id"], lambda state: state.get("state") == "running" and "directory" in state)
    queued = runtime.server.submit("echo", {"never": "executed"})
    runtime.server.cancel(queued["job_id"])
    assert runtime.server.status(active["job_id"])["state"] == "running"
    assert runtime.server.status(queued["job_id"])["state"] == "queued"
    runtime.server.cancel(active["job_id"])
    assert wait_for(runtime.server, queued["job_id"])["state"] == "cancelled"
    assert not Path(queued["directory"]).exists()


@pytest.mark.parametrize("save_fails", [False, True])
def test_offline_setup_saves_on_parent_and_propagates_failure(runtime, save_fails):
    target = runtime.storage_root()
    current = target.with_name("uninstalled")
    current.mkdir()
    parent = threading.get_ident()
    saved = []

    def location(path=None):
        nonlocal current
        if path is not None:
            assert threading.get_ident() == parent
            if save_fails:
                raise RuntimeError("Settings save failed")
            saved.append(path)
            current = path
        return current

    runtime.storage_root_setting = location
    operation = runtime.setup(target, online=False)
    wait_until(lambda: (runtime._tick() or operation.done))
    assert operation.state == ("failed" if save_fails else "succeeded"), operation.error
    assert saved == ([] if save_fails else [target])
    if save_fails:
        assert "Settings save failed" in operation.error


def test_cancellation_stops_unresponsive_job_and_allows_restart(runtime):
    controller = runtime.server
    job = controller.submit("ignore_cancel", {})
    wait_for(controller, job["job_id"], lambda state: state.get("message") == "Ignoring cancellation")
    completed = runtime.server.submit("echo", {"after_cancel": True})
    controller.cancel(job["job_id"])
    result = wait_for(controller, job["job_id"])
    assert result["state"] == "cancelled", result
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
    result = wait_for(runtime.server, preview)["result"]
    assert job["job_id"] in result["jobs"]
    invalid = runtime.server.action("clear_files", jobs=result["jobs"], instance_id="stale")
    assert wait_for(runtime.server, invalid)["state"] == "failed"
    assert Path(job["directory"]).exists()
    clear = runtime.server.action("clear_files", jobs=result["jobs"], instance_id=result["instance_id"])
    assert wait_for(runtime.server, clear)["state"] == "succeeded"
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
    active = runtime.server.submit("wait", {})
    wait_for(runtime.server, active["job_id"], lambda state: "directory" in state)
    operation = runtime.migrate(target)
    wait_until(lambda: (runtime._tick() or operation.done))
    assert operation.state == ("failed" if save_fails else "succeeded"), operation.error
    assert wait_for(runtime.server, active["job_id"])["state"] == "cancelled"
    assert source.exists() == save_fails
    assert target.exists() != save_fails
    assert current == (source if save_fails else target)
    completed = runtime.server.submit("echo", {"migrated": not save_fails})
    assert wait_for(runtime.server, completed["job_id"])["state"] == "succeeded"


@pytest.mark.parametrize("action,finished", [("start", "IDLE"), ("stop", "OFFLINE"), ("restart", "IDLE")])
def test_runtime_observes_control_completion_after_delayed_poll(runtime, action, finished):
    if action != "start":
        runtime.submit("echo", {}).wait()
    job = getattr(runtime, action)()
    runtime._tick()
    wait_until(lambda: runtime.server.poll() and runtime.server.snapshot["state"] == finished)
    # Blender can miss the exact instant a command finishes.
    time.sleep(0.3)
    runtime._tick()
    assert job.done and job.state == "succeeded", (job.state, job.error)
    assert not runtime.jobs
    wait_until(lambda: not (runtime.server.control.directory / f"status-{job.identifier}.json").exists())


def test_stop_cancels_queued_work_and_finishes_control(runtime):
    active = runtime.submit("wait")
    wait_for(runtime.server, active._job_id, lambda state: state.get("state") == "running" and "directory" in state)
    queued = runtime.submit("echo", {"queued": True})
    stopped = runtime.stop()
    wait_until(lambda: (runtime._tick() or (not runtime.jobs and runtime._operation is None)))
    assert active.state == queued.state == "cancelled"
    assert stopped.state == "succeeded"
    assert runtime.state() == "OFFLINE"


def test_cold_first_job_and_second_job_both_complete_once(runtime):
    first = runtime.submit("cold", {"index": 1})
    second = runtime.submit("echo", {"index": 2})
    wait_until(lambda: (runtime._tick() or (first.done and second.done)))
    assert first.state == second.state == "succeeded", (first.error, second.error)
    assert first.result.value == {"index": 1}
    assert second.result.value == {"index": 2}
    assert (runtime.storage_root() / "cold-count.txt").read_text() == "x"
    assert not runtime.jobs
