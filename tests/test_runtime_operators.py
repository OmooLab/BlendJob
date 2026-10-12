"""Exercise runtime-provided operators through Blender registration and dispatch."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

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
            assert bpy.ops.blendjob_test.migrate_server(directory=directory) == {"FINISHED"}
            migrate.assert_called_once_with(tmp_path / "target" / "storage")
    runtime.active_job = SimpleNamespace()
    assert not bpy.ops.blendjob_test.setup_server.poll()
    assert not bpy.ops.blendjob_test.migrate_server.poll()


def test_cleanup_confirms_preview_and_rejects_changed_server(runtime):
    result = {"jobs": ["a" * 32], "bytes": 12, "skipped": 0, "failed": []}
    client = Mock()
    client.preview_job_files.return_value = result
    client.clear_job_files.return_value = result
    connection = SimpleNamespace(client=client)
    runtime.server.connection = connection
    cls = next(cls for cls in runtime.operator_classes() if cls.bl_idname.endswith(".clear_job_files"))
    operator = SimpleNamespace(poll=cls.poll, report=Mock())
    context = SimpleNamespace(window_manager=SimpleNamespace(invoke_props_dialog=Mock(return_value={"RUNNING_MODAL"})))
    with patch.object(runtime, "environment_ready", return_value=True), patch.object(runtime.server, "ensure", return_value=connection):
        assert cls.invoke(operator, context, None) == {"RUNNING_MODAL"}
        context.window_manager.invoke_props_dialog.assert_called_once()
        client.preview_job_files.assert_called_once_with()
        client.clear_job_files.assert_not_called()
        assert cls.execute(operator, context) == {"FINISHED"}
        client.clear_job_files.assert_called_once_with(result["jobs"])
        runtime.server.connection = SimpleNamespace(client=client)
        assert cls.execute(operator, context) == {"CANCELLED"}
        with pytest.raises(RuntimeError, match="Preview and confirm"):
            bpy.ops.blendjob_test.clear_job_files()
        assert client.clear_job_files.call_count == 1
        runtime.active_job = SimpleNamespace()
        assert not bpy.ops.blendjob_test.clear_job_files.poll()


def test_migration_reports_cleanup_warning_without_failing(runtime):
    cls = next(cls for cls in runtime.operator_classes() if cls.bl_idname.endswith(".migrate_server"))
    owner = SimpleNamespace(report=Mock())
    message = "Migration completed; old folder remains at C:/old"
    assert cls.response(owner, None, SimpleNamespace(value={"warning": message})) == message
    owner.report.assert_called_once_with({"WARNING"}, message)


@pytest.mark.parametrize("selection", ["target", "target/storage", "target/storage/jobs"])
def test_migration_preserves_storage_folder(runtime, tmp_path, selection):
    cls = next(cls for cls in runtime.operator_classes() if cls.bl_idname.endswith(".migrate_server"))
    with patch.object(runtime, "migrate_controller") as migrate:
        cls.controller(SimpleNamespace(directory=str(tmp_path / selection)), runtime)
    migrate.assert_called_once_with(tmp_path / "target" / "storage")


def test_migration_requires_a_selected_directory(runtime):
    cls = next(cls for cls in runtime.operator_classes() if cls.bl_idname.endswith(".migrate_server"))
    with pytest.raises(ValueError, match="Choose a migration target"):
        cls.controller(SimpleNamespace(directory=""), runtime)


def test_migration_opens_folder_selection_at_current_parent(runtime):
    cls = next(cls for cls in runtime.operator_classes() if cls.bl_idname.endswith(".migrate_server"))
    operator = SimpleNamespace(directory="")
    context = SimpleNamespace(window_manager=SimpleNamespace(fileselect_add=Mock()))
    assert cls.invoke(operator, context, None) == {"RUNNING_MODAL"}
    assert operator.directory == str(runtime.storage_root().parent)
    context.window_manager.fileselect_add.assert_called_once_with(operator)
