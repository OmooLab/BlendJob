"""Exercise runtime-provided operators through Blender registration and dispatch."""
from types import SimpleNamespace
from unittest.mock import Mock, patch
import threading

import pytest

bpy = pytest.importorskip("bpy")

from blendjob.operator import JobOperatorBase
from blendjob.runtime import JobRuntime


@pytest.fixture
def runtime(tmp_path):
    entrypoint = tmp_path / "server.py"
    entrypoint.touch()
    runtime = JobRuntime(f"{entrypoint}:server", storage_root=tmp_path / "storage",
                         environment={"python": "3.12"}, namespace="blendjob_test")
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
    controller = Mock()
    def execute(operator, context):
        assert operator.controller(runtime) is controller
        return {"FINISHED"}
    with patch.object(JobOperatorBase, "execute", execute):
        with patch.object(runtime, "setup_controller", return_value=controller) as setup:
            assert bpy.ops.blendjob_test.setup_server(directory=directory, use_mirror=True) == {"FINISHED"}
            setup.assert_called_once_with(directory, mirror=True, online=bpy.app.online_access)
        with patch.object(runtime, "migrate_controller", return_value=controller) as migrate:
            assert bpy.ops.blendjob_test.migrate_folder(directory=directory) == {"FINISHED"}
            migrate.assert_called_once_with(tmp_path / "target" / "storage")
    runtime.active_job = SimpleNamespace()
    assert not bpy.ops.blendjob_test.setup_server.poll()
    assert not bpy.ops.blendjob_test.migrate_folder.poll()


@pytest.mark.parametrize("state,callback_error", [
    ("succeeded", False), ("succeeded", True), ("failed", False), ("cancelled", False),
])
def test_post_setup_runs_on_main_thread_after_cleanup(runtime, tmp_path, state, callback_error):
    cls = runtime.operator_classes()[0]
    invocations = []
    outcomes = []
    controller = Mock()
    controller.submit.return_value = {"job_id": "setup", "directory": str(tmp_path)}
    controller.status.return_value = {"state": state, "message": "Setup complete"}
    context = SimpleNamespace(window_manager=Mock(), window=None)
    next_job = SimpleNamespace()
    calls = []
    parent = threading.get_ident()

    def post_setup(setup):
        operator = invocations[0]
        assert threading.get_ident() == parent
        assert setup is controller
        assert runtime.active_job is None
        assert operator._timer is None
        controller.mark_job_complete.assert_called_once_with("setup")
        calls.append(setup)
        if callback_error:
            raise LookupError("Callback failed")
        # Ordinary Python and bpy access are both valid; a follow-up job can start.
        text = bpy.data.texts.new("BlendJob callback test")
        bpy.data.texts.remove(text)
        runtime.begin_job(next_job)

    def execute(operator, _context):
        invocations.append(operator)
        assert JobOperatorBase.execute(operator, context) == {"RUNNING_MODAL"}
        result = operator.modal(context, SimpleNamespace(type="TIMER"))
        outcomes.append(result)
        return result

    runtime.post_setup = post_setup
    with (
        patch.object(runtime, "setup_controller", return_value=controller),
        patch.object(runtime, "update_ui"),
        patch.object(cls, "execute", execute),
        patch.object(threading.Thread, "start", side_effect=AssertionError("Parent thread")),
    ):
        if callback_error or state == "failed":
            message = "Setup completed, but post_setup failed: Callback failed" if callback_error else "Server task failed"
            with pytest.raises(RuntimeError, match=message):
                bpy.ops.blendjob_test.setup_server()
        else:
            bpy.ops.blendjob_test.setup_server()
    succeeded = state == "succeeded"
    assert calls == ([controller] if succeeded else [])
    assert outcomes == [{"FINISHED"} if succeeded and not callback_error else {"CANCELLED"}]
    assert runtime.active_job is (next_job if succeeded and not callback_error else None)
    context.window_manager.event_timer_remove.assert_called_once()
    context.window_manager.progress_end.assert_called_once()
    controller.mark_job_complete.assert_called_once_with("setup")


def test_cleanup_previews_before_confirmation_and_dispatches_selected_jobs(runtime):
    result = {"jobs": ["a" * 32], "bytes": 12, "skipped": 0, "failed": [], "instance_id": "server"}
    cls = next(cls for cls in runtime.operator_classes() if cls.bl_idname.endswith(".clear_job_files"))
    operator = SimpleNamespace(poll=cls.poll, report=Mock())
    context = SimpleNamespace(window_manager=SimpleNamespace(invoke_props_dialog=Mock(return_value={"RUNNING_MODAL"})))
    with patch.object(runtime, "environment_ready", return_value=True), patch.object(JobOperatorBase, "execute", return_value={"RUNNING_MODAL"}) as execute:
        assert cls.execute(operator, context) == {"CANCELLED"}
        execute.assert_not_called()
        assert cls.invoke(operator, context, None) == {"RUNNING_MODAL"}
        assert operator._action == "preview_files"
        assert cls.request(operator, context) == {}
        context.window_manager.invoke_props_dialog.assert_not_called()
        cls.response(operator, context, SimpleNamespace(value=result))
        context.window_manager.invoke_props_dialog.assert_called_once()
        assert cls.execute(operator, context) == {"RUNNING_MODAL"}
        assert operator._action == "clear_files"
        assert cls.request(operator, context) == {"jobs": result["jobs"], "instance_id": "server"}
        runtime.active_job = SimpleNamespace()
        assert not bpy.ops.blendjob_test.clear_job_files.poll()


def test_migration_reports_cleanup_warning_without_failing(runtime):
    cls = next(cls for cls in runtime.operator_classes() if cls.bl_idname.endswith(".migrate_folder"))
    owner = SimpleNamespace(report=Mock())
    message = "Migration completed; old folder remains at C:/old"
    assert cls.response(owner, None, SimpleNamespace(value={"warning": message})) == message
    owner.report.assert_called_once_with({"WARNING"}, message)


@pytest.mark.parametrize("selection", ["target", "target/storage", "target/storage/jobs"])
def test_migration_preserves_storage_folder(runtime, tmp_path, selection):
    cls = next(cls for cls in runtime.operator_classes() if cls.bl_idname.endswith(".migrate_folder"))
    with patch.object(runtime, "migrate_controller") as migrate:
        cls.controller(SimpleNamespace(directory=str(tmp_path / selection)), runtime)
    migrate.assert_called_once_with(tmp_path / "target" / "storage")


def test_migration_requires_a_selected_directory(runtime):
    cls = next(cls for cls in runtime.operator_classes() if cls.bl_idname.endswith(".migrate_folder"))
    with pytest.raises(ValueError, match="Choose a migration target"):
        cls.controller(SimpleNamespace(directory=""), runtime)


def test_migration_opens_folder_selection_at_current_parent(runtime):
    cls = next(cls for cls in runtime.operator_classes() if cls.bl_idname.endswith(".migrate_folder"))
    operator = SimpleNamespace(directory="")
    context = SimpleNamespace(window_manager=SimpleNamespace(fileselect_add=Mock()))
    assert cls.invoke(operator, context, None) == {"RUNNING_MODAL"}
    assert operator.directory == str(runtime.storage_root().parent)
    context.window_manager.fileselect_add.assert_called_once_with(operator)
