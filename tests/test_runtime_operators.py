"""Exercise runtime-provided operators through Blender registration and dispatch."""
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

bpy = pytest.importorskip("bpy")

from blendjob.runtime import JobRuntime


@pytest.fixture
def runtime(tmp_path):
    entrypoint = tmp_path / "server.py"
    entrypoint.touch()
    runtime = JobRuntime("blendjob_test", server=f"{entrypoint}:server", storage_root=tmp_path / "storage", environment={"python": "3.12"})
    runtime._config.environment_ready = Mock(return_value=True)
    classes = runtime.operator_classes()
    for cls in classes:
        bpy.utils.register_class(cls)
    try:
        yield runtime
    finally:
        for cls in reversed(classes):
            bpy.utils.unregister_class(cls)


def test_setup_and_migration_dispatch_parameters_to_runtime(runtime, tmp_path):
    directory = str(tmp_path / "target")
    requests = []

    def execute(operator, context):
        requests.append((operator.operation, operator.request(context)))
        return {"FINISHED"}

    with patch.object(runtime.operator_classes()[0].__bases__[0], "execute", execute):
        with patch.object(runtime._config, "environment_ready", return_value=False):
            assert bpy.ops.blendjob_test.setup_server(directory=directory, use_mirror=True) == {"FINISHED"}
        assert bpy.ops.blendjob_test.migrate_storage(directory=directory) == {"FINISHED"}
    assert requests == [
        ("setup", {"target": directory, "mirror": True, "online": bpy.app.online_access}),
        ("migrate", {"target": str(tmp_path / "target" / "storage")}),
    ]
    runtime.jobs.append(SimpleNamespace(name="work"))
    assert not bpy.ops.blendjob_test.setup_server.poll()
    assert bpy.ops.blendjob_test.migrate_storage.poll()


def test_cleanup_confirmation_uses_the_preview_snapshot(runtime):
    cls = next(cls for cls in runtime.operator_classes() if cls.bl_idname.endswith(".confirm_clear_job_files"))
    result = {"jobs": ["a" * 32], "bytes": 12, "instance_id": "server"}
    owner = SimpleNamespace(preview=json.dumps(result))
    assert cls.request(owner, None) == {"jobs": result["jobs"], "instance_id": "server"}
    with pytest.raises(ValueError, match="Preview and confirm"):
        cls.request(SimpleNamespace(preview="{}"), None)


def test_migration_reports_cleanup_warning_without_failing(runtime):
    cls = next(cls for cls in runtime.operator_classes() if cls.bl_idname.endswith(".migrate_storage"))
    owner = SimpleNamespace(report=Mock())
    message = "Migration completed; old folder remains at C:/old"
    cls.response(owner, None, {"warning": message})
    owner.report.assert_called_once_with({"WARNING"}, message)


@pytest.mark.parametrize("selection", ["target", "target/storage", "target/storage/jobs"])
def test_migration_preserves_storage_folder(runtime, tmp_path, selection):
    cls = next(cls for cls in runtime.operator_classes() if cls.bl_idname.endswith(".migrate_storage"))
    parameters = cls.request(SimpleNamespace(directory=str(tmp_path / selection)), None)
    assert parameters == {"target": str(tmp_path / "target" / "storage")}


def test_migration_requires_a_selected_directory(runtime):
    cls = next(cls for cls in runtime.operator_classes() if cls.bl_idname.endswith(".migrate_storage"))
    with pytest.raises(ValueError, match="Choose a migration target"):
        cls.request(SimpleNamespace(directory=""), None)


def test_migration_opens_folder_selection_at_current_parent(runtime):
    cls = next(cls for cls in runtime.operator_classes() if cls.bl_idname.endswith(".migrate_storage"))
    operator = SimpleNamespace(directory="")
    context = SimpleNamespace(window_manager=SimpleNamespace(fileselect_add=Mock()))
    assert cls.invoke(operator, context, None) == {"RUNNING_MODAL"}
    assert operator.directory == str(runtime.storage_root().parent)
    context.window_manager.fileselect_add.assert_called_once_with(operator)


@pytest.mark.parametrize("operation", [None, "start", "stop", "clear_resource"])
def test_registered_controls_respect_active_server_operations(runtime, operation):
    runtime.server.snapshot = {"state": "IDLE"}
    runtime.jobs.append(SimpleNamespace(name="work"))
    runtime._operation = SimpleNamespace(name=operation) if operation else None

    assert bpy.ops.blendjob_test.migrate_storage.poll() is (operation is None)
    assert bpy.ops.blendjob_test.clear_job_files.poll() is (operation is None)
    assert bpy.ops.blendjob_test.stop_server.poll() is (operation in {None, "start"})
    assert bpy.ops.blendjob_test.restart_server.poll() is (operation is None)


@pytest.mark.parametrize("closing", [False, True])
def test_operation_timer_is_released_on_modal_completion_or_shutdown(runtime, monkeypatch, closing):
    setup = next(cls for cls in runtime.operator_classes() if cls.bl_idname.endswith(".setup_server"))
    operator = setup.__bases__[0]()
    operator.operation = "stop"
    operator.report = Mock()
    operation = SimpleNamespace(done=False, state="running", result=None, error="")
    stop = Mock(return_value=operation)
    monkeypatch.setattr(runtime, "stop", stop)
    context = SimpleNamespace(window_manager=Mock(), window=None)

    assert operator.execute(context) == {"RUNNING_MODAL"}
    assert operator.modal(context, None) == {"PASS_THROUGH"}
    timer = context.window_manager.event_timer_add.return_value
    monkeypatch.setattr(runtime, "_closing", closing)
    operation.done = True
    operation.state = "cancelled" if closing else "succeeded"
    stop.call_args.kwargs["on_done"](operation)

    if closing:
        context.window_manager.event_timer_remove.assert_called_once_with(timer)
    else:
        context.window_manager.event_timer_remove.assert_not_called()
    assert operator.modal(context, None) == ({"CANCELLED"} if closing else {"FINISHED"})
    context.window_manager.event_timer_remove.assert_called_once_with(timer)


@pytest.mark.parametrize("failure", ["request", "timer", "dispatch", "handler", "handler_refused"])
def test_operation_execute_failure_releases_ui_ownership(runtime, monkeypatch, failure):
    operator = runtime.operator_classes()[0].__bases__[0]()
    operator.operation = "stop"
    operator.report = Mock()
    operation = SimpleNamespace(on_done=None, done=False)
    context = SimpleNamespace(window_manager=Mock(), window=None)

    def stop(**kwargs):
        operation.on_done = kwargs["on_done"]
        if failure == "dispatch":
            raise RuntimeError("dispatch failed")
        return operation

    dispatch = Mock(side_effect=stop)
    monkeypatch.setattr(runtime, "stop", dispatch)
    if failure == "request":
        operator.request = Mock(side_effect=ValueError("request failed"))
    if failure == "timer":
        context.window_manager.event_timer_add.side_effect = RuntimeError("timer failed")
    if failure == "handler":
        context.window_manager.modal_handler_add.side_effect = RuntimeError("handler failed")
    if failure == "handler_refused":
        context.window_manager.modal_handler_add.return_value = False
    assert operator.execute(context) == {"CANCELLED"}
    assert operator._timer is None
    if failure in {"request", "timer"}:
        dispatch.assert_not_called()
        context.window_manager.event_timer_remove.assert_not_called()
    else:
        context.window_manager.event_timer_remove.assert_called_once()
    if failure in {"handler", "handler_refused"}:
        assert operation.on_done is None and not operation.done
    message = "Unable to attach the operation to this window" if failure == "handler_refused" else f"{failure} failed"
    operator.report.assert_called_once_with({"ERROR"}, message)


def test_window_cancel_detaches_operation_without_cancelling_work(runtime, monkeypatch):
    operator = runtime.operator_classes()[0].__bases__[0]()
    operator.operation = "stop"
    operator.report = Mock()
    operation = SimpleNamespace(on_done=None, done=False)

    def dispatch(**kwargs):
        operation.on_done = kwargs["on_done"]
        return operation

    monkeypatch.setattr(runtime, "stop", dispatch)
    context = SimpleNamespace(window_manager=Mock(), window=None)
    assert operator.execute(context) == {"RUNNING_MODAL"}
    assert operation.on_done is not None
    operator.cancel(context)
    operator.cancel(context)
    assert operation.on_done is None and not operation.done
    context.window_manager.event_timer_remove.assert_called_once()


def test_response_failure_finishes_modal_and_detaches_callback(runtime, monkeypatch):
    operator = runtime.operator_classes()[0].__bases__[0]()
    operator.operation = "stop"
    operator.report = Mock()
    operator.response = Mock(side_effect=RuntimeError("UI response failed"))
    operation = SimpleNamespace(done=True, state="succeeded", result={}, on_done=None)
    monkeypatch.setattr(runtime, "stop", Mock(return_value=operation))
    context = SimpleNamespace(window_manager=Mock(), window=None)
    assert operator.execute(context) == {"RUNNING_MODAL"}
    assert operator.modal(context, None) == {"CANCELLED"}
    context.window_manager.event_timer_remove.assert_called_once()
    assert operation.on_done is None
    operator.report.assert_called_once_with({"ERROR"}, "UI response failed")


def test_cancel_before_execution_has_no_operation(runtime):
    operator = runtime.operator_classes()[0].__bases__[0]()
    operator.cancel(None)
    assert runtime._operation is None
