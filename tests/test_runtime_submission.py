"""Exercise submission and completion without a Blender Operator."""
import sys
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from blendjob import JobServer


def test_direct_submission_snapshots_parameters_and_completes_once(job_runtime):
    runtime = job_runtime
    callback = Mock()
    parameters = {"nested": [1]}
    job = runtime.submit("generate_depth", parameters, on_done=callback)
    parameters["nested"].append(2)
    runtime.server.submit.assert_called_once_with("generate_depth", {"nested": [1]})
    runtime.enable.assert_called_once()
    runtime.server.states[job._job_id] = {"state": "succeeded", "result": {"depth": 42}}
    runtime._poll_server()
    assert job.done and job.result.value == {"depth": 42}
    callback.assert_called_once_with(job)
    runtime._poll_server()
    callback.assert_called_once()
    runtime.server.mark_job_complete.assert_called_once_with(job._job_id)


@pytest.mark.parametrize("name,parameters", [
    ("generate-depth", {}), ("", {}), ("clear_resource", {}),
    ("clear_resource", {"name": "models", "typo": True}),
    ("migrate", {"target": ""}), ("work", {"value": object()}),
])
def test_invalid_submission_has_no_execution(job_runtime, name, parameters):
    with pytest.raises((TypeError, ValueError)):
        job_runtime.submit(name, parameters)
    assert not job_runtime.jobs
    job_runtime.server.submit.assert_not_called()
    job_runtime.server.action.assert_not_called()


def test_service_registration_validates_names():
    server = JobServer("Test")
    try:
        for name in ("setup", "clear_resource", "generate-depth"):
            with pytest.raises(ValueError):
                server.job(name)
        handler = lambda context, parameters: parameters
        server.job("generate_depth")(handler)
        assert server.handlers["generate_depth"] is handler
    finally:
        server.close()


def test_wait_uses_runtime_lifecycle(job_runtime, monkeypatch):
    monkeypatch.setitem(sys.modules, "bpy", None)
    job = job_runtime.submit("work")
    job_runtime.server.states[job._job_id] = {"state": "succeeded", "result": 3}
    assert job.wait().value == 3
    assert job.done and not job_runtime.jobs


def test_interactive_wait_is_rejected(job_runtime, monkeypatch):
    monkeypatch.setitem(sys.modules, "bpy", SimpleNamespace(app=SimpleNamespace(background=False)))
    job = job_runtime.submit("work")
    with pytest.raises(RuntimeError, match="on_done"):
        job.wait()


def test_clear_resource_routes_internally(job_runtime):
    job = job_runtime.clear_resource("models")
    job_runtime._tick()
    job_runtime.server.action.assert_called_once_with("clear_resource", name="models")
    job_runtime.server.states[job.identifier] = {"state": "succeeded", "result": True}
    job_runtime._tick()
    assert job.result is True


def test_controller_is_not_a_public_extension(job_runtime):
    assert not hasattr(job_runtime.JobOperator, "controller")
    for name in ("setup_controller", "migrate_controller", "action_controller", "request_blocking"):
        assert not hasattr(job_runtime, name)
    with pytest.raises(TypeError):
        job_runtime.submit("work", controller=Mock())


@pytest.mark.parametrize("fails", [False, True])
def test_post_setup_runs_after_release_and_can_submit(job_runtime, monkeypatch, fails):
    runtime = job_runtime
    runtime._config.environment_ready.return_value = False
    monkeypatch.setitem(sys.modules, "bpy", None)
    observed = []

    def prepare(owner):
        assert owner is runtime and not runtime.jobs
        runtime.server.mark_job_complete.assert_called_once_with(job.identifier)
        if fails:
            raise RuntimeError("Download failed")
        owner.submit("download_models")

    runtime.post_setup = prepare
    job = runtime.setup(on_done=lambda job: observed.append((job.state, job.error)))
    runtime._tick()
    runtime.server.states[job.identifier] = {"state": "succeeded"}
    runtime._config.environment_ready.return_value = True
    runtime._tick()
    assert job.done
    expected = ("failed", "Setup completed, but post_setup failed: Download failed") if fails else ("succeeded", "")
    assert observed == [expected]
    assert len(runtime.jobs) == (0 if fails else 1)


def test_setup_respects_disabled_network(job_runtime, monkeypatch):
    runtime = job_runtime
    runtime._config.environment_ready.return_value = False
    monkeypatch.setitem(sys.modules, "bpy", SimpleNamespace(app=SimpleNamespace(online_access=False)))
    runtime.setup(online=True)
    runtime._tick()
    assert runtime.server.action.call_args.kwargs["online"] is False


def test_migration_saves_location_once_before_finishing(job_runtime, tmp_path):
    runtime = job_runtime
    job = runtime.migrate(tmp_path / "moved")
    runtime._tick()
    runtime.server.states[job.identifier] = {"state": "awaiting_save"}
    runtime._tick()
    assert runtime.storage_root() == tmp_path / "moved"
    assert job._location_acknowledged
    runtime._tick()
    assert not job.done
    assert (runtime.server.control.directory / f"saving-{job.identifier}").is_file()
    runtime.server.cancel.assert_not_called()
    runtime.server.control.acknowledge.assert_called_once_with(job.identifier, None)


def test_migration_save_failure_is_acknowledged(job_runtime, tmp_path):
    runtime = job_runtime
    source = runtime.storage_root()

    def location(target=None):
        if target is not None:
            raise RuntimeError("Cannot save")
        return source

    runtime.storage_root_setting = location
    job = runtime.migrate(tmp_path / "moved")
    runtime._tick()
    runtime.server.states[job.identifier] = {"state": "awaiting_save"}
    runtime._tick()
    runtime.server.control.acknowledge.assert_called_once_with(job.identifier, "Cannot save")
    assert runtime.storage_root() == source
    runtime.server.states[job.identifier] = {"state": "failed", "error": "Cannot save"}
    runtime._tick()
    assert job.done and job.state == "failed" and not runtime.jobs


@pytest.mark.parametrize("name", ["start", "stop", "restart", "setup", "migrate"])
def test_control_names_are_not_jobs(job_runtime, name):
    with pytest.raises(ValueError, match="Use runtime"):
        job_runtime.submit(name)
    assert not job_runtime.jobs


def test_stop_during_start_cancels_queued_business_jobs(job_runtime):
    runtime = job_runtime
    runtime.server.snapshot = {"state": "OFFLINE"}
    starting = runtime.start()
    job = runtime.submit("work")
    assert not job._job_id
    stopping = runtime.stop()
    assert starting.done and starting.state == "cancelled"
    runtime._tick()
    assert job.done and job.state == "cancelled"
    runtime.server.submit.assert_not_called()
    runtime.server.states[stopping.identifier] = {"state": "succeeded"}
    runtime._tick()
    assert stopping.done and not runtime.jobs and runtime._operation is None


def test_operation_release_failure_still_completes_and_calls_callback(job_runtime):
    runtime = job_runtime
    callback = Mock()
    operation = runtime.clear_resource("models", on_done=callback)
    runtime._tick()
    runtime.server.states[operation.identifier] = {"state": "succeeded", "result": True}
    runtime.server.mark_job_complete.side_effect = RuntimeError("Control process exited")
    runtime._tick()
    assert operation.done and runtime._operation is None
    callback.assert_called_once_with(operation)
    assert operation.on_done is None
    runtime._tick()
    callback.assert_called_once()


def test_invalid_stop_callback_preserves_start_in_progress(job_runtime):
    runtime = job_runtime
    runtime.server.snapshot = {"state": "OFFLINE"}
    starting = runtime.start()
    runtime._tick()
    with pytest.raises(TypeError, match="on_done"):
        runtime.stop(on_done=42)
    assert runtime._operation is starting and not starting.done
    runtime.server.cancel.assert_not_called()
    runtime.server.mark_job_complete.assert_not_called()
