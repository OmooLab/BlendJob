"""Verify the Blender adapter delegates execution and always releases inputs."""
from types import SimpleNamespace
from unittest.mock import Mock
import sys
import pytest


@pytest.fixture
def invocation(job_runtime):
    class Work(job_runtime.JobOperator):
        job_type = "work"
    operator = Work()
    operator.report = Mock()
    operator.response = Mock(return_value=None)
    operator.cleanup = Mock()
    context = SimpleNamespace(window_manager=Mock(), window=None)
    return operator, context


@pytest.mark.parametrize("outcome", ["succeeded", "failed", "cancelled", "submit_error", "callback_error"])
def test_runtime_completes_without_modal_events(job_runtime, invocation, outcome):
    operator, context = invocation
    if outcome == "submit_error":
        job_runtime.server.submit.side_effect = RuntimeError("Submit failed")
    assert operator.execute(context) == {"RUNNING_MODAL"}
    job = operator._job
    if job._job_id:
        job_runtime.server.states[job._job_id] = {
            "state": "succeeded" if outcome == "callback_error" else outcome,
            "result": {}, "error": "Server failed" if outcome == "failed" else "",
        }
    if outcome == "callback_error":
        operator.response.side_effect = ValueError("Cannot apply")
    job_runtime._poll_server()
    assert job.done and not job_runtime.jobs
    operator.cleanup.assert_called_once()
    context.window_manager.event_timer_remove.assert_called_once()
    if outcome in {"succeeded", "callback_error"}:
        operator.response.assert_called_once()
    else:
        operator.response.assert_not_called()
    assert operator.modal(context, SimpleNamespace(type="TIMER")) == ({"FINISHED"} if outcome == "succeeded" else {"CANCELLED"})
    operator.cancel(context)
    operator.cleanup.assert_called_once()


def test_request_failure_cleans_before_submission(job_runtime, invocation):
    operator, context = invocation
    operator.request = Mock(side_effect=ValueError("Invalid image"))
    assert operator.execute(context) == {"CANCELLED"}
    operator.cleanup.assert_called_once()
    job_runtime.server.submit.assert_not_called()


def test_modal_only_requests_cancellation(job_runtime, invocation):
    operator, context = invocation
    operator.execute(context)
    assert operator.modal(context, SimpleNamespace(type="TIMER")) == {"PASS_THROUGH"}
    job_runtime.server.status.assert_not_called()
    assert operator.modal(context, SimpleNamespace(type="ESC")) == {"RUNNING_MODAL"}
    job_runtime.server.cancel.assert_called_once_with(operator._job._job_id)
    operator.cleanup.assert_not_called()


def test_missing_original_window_fails_without_applying(job_runtime, invocation, monkeypatch):
    operator, context = invocation
    context.window = object()
    monkeypatch.setitem(sys.modules, "bpy", SimpleNamespace(context=SimpleNamespace(window_manager=SimpleNamespace(windows=[]))))
    operator.execute(context)
    job_runtime.server.states[operator._job._job_id] = {"state": "succeeded"}
    job_runtime._tick()
    assert operator._job.state == "failed"
    operator.response.assert_not_called()
    operator.cleanup.assert_called_once()


@pytest.mark.parametrize("failure", ["cleanup", "report", "event_timer_remove"])
def test_ui_callback_errors_do_not_leak_jobs(job_runtime, invocation, failure):
    operator, context = invocation
    operator.response.return_value = "Done"
    failing = getattr(context.window_manager, failure) if failure == "event_timer_remove" else getattr(operator, failure)
    failing.side_effect = RuntimeError("UI unavailable")
    operator.execute(context)
    job_runtime.server.states[operator._job._job_id] = {"state": "succeeded"}
    job_runtime._tick()
    assert operator._job.done and not job_runtime.jobs
    operator.cleanup.assert_called_once()
    job_runtime.server.mark_job_complete.assert_called_once()
