"""Verify job completion precedes Blender callbacks and resource cleanup follows them."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from blendjob import JobRuntime


@pytest.fixture
def invocation(tmp_path, monkeypatch):
    entrypoint = tmp_path / "server.py"
    entrypoint.touch()
    runtime = JobRuntime(f"{entrypoint}:server", storage_root=tmp_path,
                         environment={"python": "3.12"}, namespace="test")
    monkeypatch.setattr(runtime, "redraw_ui", Mock())
    controller = Mock()
    controller.submit.return_value = {"job_id": "first", "directory": str(tmp_path)}
    controller.status.return_value = {
        "state": "succeeded", "progress": 1.0, "message": "Server complete", "result": {},
    }

    class TestOperator(runtime.JobOperatorBase):
        job_type = "example"

        def controller(self, _runtime):
            return controller

    operator = TestOperator()
    operator.cleanup = Mock()
    operator.report = Mock()
    context = SimpleNamespace(window_manager=Mock(), window=None)
    return runtime, controller, operator, context


@pytest.mark.parametrize("error_type", [None, KeyError, AttributeError, IndexError, RuntimeError])
def test_response_runs_after_completion_and_before_cleanup(invocation, tmp_path, error_type):
    runtime, controller, operator, context = invocation
    resource = tmp_path / "temporary.txt"
    resource.write_text("needed by response")
    operator.cleanup.side_effect = resource.unlink

    def response(_context, _result):
        assert runtime.active_job is None
        assert operator._timer is None
        assert runtime.message == "Server complete"
        assert resource.read_text() == "needed by response"
        operator.cleanup.assert_not_called()
        controller.mark_job_complete.assert_called_once_with("first")
        context.window_manager.progress_end.assert_called_once_with()
        if error_type:
            raise error_type("Missing interface")
        return "Blender result applied"

    operator.response = response
    assert operator.execute(context) == {"RUNNING_MODAL"}
    timer = operator._timer
    event = SimpleNamespace(type="TIMER")
    if error_type:
        with pytest.raises(error_type, match="Missing interface"):
            operator.modal(context, event)
    else:
        assert operator.modal(context, event) == {"FINISHED"}
    assert runtime.active_job is None
    assert runtime.message == "Server complete"
    context.window_manager.progress_update.assert_called_once_with(0)
    assert runtime.redraw_ui.call_count == 2
    assert runtime.progress == 1.0
    assert not resource.exists()
    operator.cleanup.assert_called_once_with()
    operator.report.assert_not_called()
    context.window_manager.event_timer_remove.assert_called_once_with(timer)

    next_operator = type(operator)()
    assert next_operator.execute(context) == {"RUNNING_MODAL"}
    assert next_operator.modal(context, event) == {"FINISHED"}
    assert runtime.active_job is None


@pytest.mark.parametrize("callback_fails", [False, True])
def test_response_can_start_next_job_without_old_operator_interference(invocation, callback_fails):
    runtime, controller, operator, context = invocation
    next_operator = type(operator)()
    next_operator.cleanup = Mock()

    def response(_context, _result):
        controller.submit.return_value = {"job_id": "second", "directory": "."}
        assert next_operator.execute(context) == {"RUNNING_MODAL"}
        if callback_fails:
            raise KeyError("Missing interface")
        return "Old job's message"

    operator.response = response
    assert operator.execute(context) == {"RUNNING_MODAL"}
    event = SimpleNamespace(type="TIMER")
    if callback_fails:
        with pytest.raises(KeyError, match="Missing interface"):
            operator.modal(context, event)
    else:
        assert operator.modal(context, event) == {"FINISHED"}
    next_job = runtime.active_job
    assert next_job.operator is next_operator
    assert runtime.message == next_operator.starting_message
    assert runtime.progress == 0.0
    operator.cleanup.assert_called_once_with()
    next_operator.cleanup.assert_not_called()
    # Blender may still dispatch cancellation or queued events to the old operator.
    operator.cancel(context)
    assert operator.modal(context, SimpleNamespace(type="ESC")) == {"CANCELLED"}
    assert operator.modal(context, event) == {"CANCELLED"}
    assert runtime.active_job is next_job
    controller.cancel.assert_not_called()
    context.window_manager.progress_end.assert_called_once_with()
    controller.mark_job_complete.assert_called_once_with("first")
    assert next_operator.modal(context, event) == {"FINISHED"}
    assert runtime.active_job is None
    next_operator.cleanup.assert_called_once_with()


@pytest.mark.parametrize("outcome", ["failed", "cancelled", "submit_error"])
def test_unsuccessful_jobs_release_resources_without_response(invocation, outcome):
    runtime, controller, operator, context = invocation
    operator.response = Mock()
    if outcome == "submit_error":
        controller.submit.side_effect = RuntimeError("Submit failed")
    else:
        controller.status.return_value = {"state": outcome, "error": "Server failed"}
    if outcome != "cancelled":
        operator.report.side_effect = RuntimeError("Report failed")
        with pytest.raises(RuntimeError, match="Report failed"):
            if operator.execute(context) == {"RUNNING_MODAL"}:
                operator.modal(context, SimpleNamespace(type="TIMER"))
    else:
        assert operator.execute(context) == {"RUNNING_MODAL"}
        assert operator.modal(context, SimpleNamespace(type="TIMER")) == {"CANCELLED"}
    assert runtime.active_job is None
    assert operator._timer is None
    operator.cleanup.assert_called_once_with()
    operator.response.assert_not_called()
    controller.mark_job_complete.assert_called_once()
    context.window_manager.progress_end.assert_called_once_with()


@pytest.mark.parametrize("outcome", ["succeeded", "failed", "cancelled"])
@pytest.mark.parametrize("failing_step", ["event_timer_remove", "progress_end", "redraw_ui"])
def test_ui_teardown_errors_release_job_and_resources(invocation, outcome, failing_step):
    runtime, controller, operator, context = invocation
    controller.status.return_value = {"state": outcome, "message": "Server complete"}
    operator.response = Mock()
    assert operator.execute(context) == {"RUNNING_MODAL"}
    failing_call = (runtime.redraw_ui if failing_step == "redraw_ui"
                    else getattr(context.window_manager, failing_step))
    failing_call.side_effect = ReferenceError("Window is gone")
    with pytest.raises(ReferenceError, match="Window is gone"):
        operator.modal(context, SimpleNamespace(type="TIMER"))
    assert runtime.active_job is None
    assert operator._timer is None
    controller.mark_job_complete.assert_called_once_with("first")
    context.window_manager.progress_end.assert_called_once_with()
    operator.cleanup.assert_called_once_with()
    operator.response.assert_not_called()
    failing_call.side_effect = None
    controller.status.return_value = {"state": "succeeded", "message": "Next job complete"}
    next_operator = type(operator)()
    assert next_operator.execute(context) == {"RUNNING_MODAL"}
    next_job = runtime.active_job
    operator.cancel(context)
    assert runtime.active_job is next_job
    controller.cancel.assert_not_called()
    next_operator.modal(context, SimpleNamespace(type="TIMER"))
    assert runtime.active_job is None
