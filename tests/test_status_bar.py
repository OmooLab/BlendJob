"""Cover automatic progress independently of the optional jobs layout."""
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock
import sys

import pytest


@pytest.fixture
def blender(monkeypatch):
    draws, timers = [], set()
    bpy = SimpleNamespace(
        types=SimpleNamespace(STATUSBAR_HT_header=SimpleNamespace(append=draws.append, remove=draws.remove)),
        app=SimpleNamespace(online_access=True,
            translations=SimpleNamespace(register=Mock(), unregister=Mock(),
                pgettext_iface=lambda text: {"Working {count}": "处理中 {count}", "Cancelling job": "取消中"}.get(text, text)),
            timers=SimpleNamespace(is_registered=timers.__contains__,
                register=lambda callback, **kw: timers.add(callback), unregister=timers.remove)),
        context=SimpleNamespace(window_manager=SimpleNamespace(windows=[]), temp_override=lambda **kw: nullcontext()),
        utils=SimpleNamespace(register_class=Mock(), unregister_class=Mock()),
    )
    monkeypatch.setitem(sys.modules, "bpy", bpy)
    return bpy, draws, timers


def layout():
    result = Mock()
    result.row.return_value = result
    return result


def test_status_bar_follows_first_running_job(job_runtime, blender):
    runtime = job_runtime
    queued = runtime.submit("work")
    first = runtime.submit("work")
    second = runtime.submit("work")
    runtime.server.states[queued._job_id] = {"state": "queued"}
    runtime.server.states[first._job_id] = {"state": "running", "progress": .4,
        "message": "Working {count}", "message_args": {"count": 3}}
    runtime._tick()
    bar = layout()
    runtime._status_bar_draw(SimpleNamespace(layout=bar), None)
    bar.progress.assert_called_once_with(factor=.4, type="BAR", text="处理中 3")
    assert bar.operator.return_value.job_id == first.id

    first.cancel()
    bar.reset_mock()
    runtime._status_bar_draw(SimpleNamespace(layout=bar), None)
    assert bar.progress.call_args.kwargs["text"] == "取消中"
    assert not bar.enabled
    runtime.server.states[first._job_id] = {"state": "cancelled"}
    runtime._tick()
    bar.reset_mock()
    runtime._status_bar_draw(SimpleNamespace(layout=bar), None)
    assert bar.operator.return_value.job_id == second.id

    all_jobs = layout()
    runtime.draw_jobs(all_jobs)
    assert all_jobs.progress.call_count == 2
    runtime.server.states[second._job_id] = {"state": "cancelled"}
    runtime._tick()
    bar.reset_mock()
    runtime._status_bar_draw(SimpleNamespace(layout=bar), None)
    bar.row.assert_not_called()


def test_register_unregister_owns_one_header_callback(job_runtime, blender, monkeypatch):
    runtime = job_runtime
    bpy, draws, timers = blender
    monkeypatch.setattr(runtime, "operator_classes", lambda: ())
    runtime.register()
    runtime.register()
    assert draws == [runtime._status_bar_draw]
    timers.add(runtime._redraw_status_bars)
    runtime.unregister()
    runtime.unregister()
    assert draws == [] and timers == set()
    runtime.register()
    assert draws == [runtime._status_bar_draw]
    runtime.unregister()


def test_refresh_supports_any_editor_and_defers_global_header(job_runtime, blender):
    runtime = job_runtime
    bpy, _draws, timers = blender
    area, workspace = Mock(type="IMAGE_EDITOR"), Mock()
    area.regions = [Mock()]
    window = SimpleNamespace(screen=SimpleNamespace(areas=[area], is_temporary=False), workspace=workspace)
    bpy.context.window_manager.windows = [window]
    job = runtime.submit("work")
    runtime._tick()
    runtime._registered = True
    type(runtime).redraw_ui(runtime)
    area.tag_redraw.assert_called_once()
    assert timers == {runtime._redraw_status_bars}
    workspace.status_text_set_internal.assert_not_called()
    runtime._redraw_status_bars()
    workspace.status_text_set_internal.assert_called_once_with(None)
    timers.clear()
    type(runtime).redraw_ui(runtime)
    assert not timers
    runtime.server.states[job._job_id] = {"state": "succeeded"}
    runtime._tick()
    type(runtime).redraw_ui(runtime)
    assert timers == {runtime._redraw_status_bars}


def test_native_blender_header_registration(tmp_path, monkeypatch):
    bpy = pytest.importorskip("bpy")
    from blendjob import JobRuntime
    entry = tmp_path / "server.py"
    entry.touch()
    runtime = JobRuntime(f"{entry}:server", storage_root=tmp_path,
                         environment={"python": "3.12"}, namespace="statusbar_smoke")
    # The bpy package has no interactive event loop; exercise the header lifecycle.
    monkeypatch.setattr(runtime, "enable", Mock())
    try:
        runtime.register()
        runtime.register()
        assert bpy.types.STATUSBAR_HT_header.draw._draw_funcs.count(runtime._status_bar_draw) == 1
    finally:
        runtime.unregister()
    assert runtime._status_bar_draw not in bpy.types.STATUSBAR_HT_header.draw._draw_funcs


@pytest.mark.parametrize("name", ["start", "stop", "restart"])
def test_server_controls_do_not_create_progress_rows(job_runtime, blender, name):
    runtime = job_runtime
    runtime.server.snapshot = {"state": "OFFLINE" if name == "start" else "IDLE"}
    job = getattr(runtime, name)()
    empty = layout()
    runtime.draw_jobs(empty)
    empty.row.assert_not_called()
    runtime._tick()
    assert job.state == "running"
    runtime._status_bar_draw(SimpleNamespace(layout=empty), None)
    runtime.draw_jobs(empty)
    empty.row.assert_not_called()
    assert runtime.state() == ("STARTING" if name == "start" else "STOPPING")
    runtime.server.states[job.identifier] = {"state": "succeeded"}
    runtime._tick()
    assert job.done


@pytest.mark.parametrize("name,parameters,label", [
    ("setup", {}, "Installing environment"),
    ("migrate", {"target": "moved"}, "Migrating Folder"),
    ("preview_files", {}, "Reading job files"),
    ("clear_files", {"jobs": ["a" * 32], "instance_id": "server"}, "Deleting job files"),
])
@pytest.mark.parametrize("terminal", ["succeeded", "failed", "cancelled"])
def test_maintenance_status_bar_lifecycle(job_runtime, blender, name, parameters, label, terminal):
    runtime = job_runtime
    _, _, timers = blender
    runtime._config.environment_ready.return_value = name != "setup"
    operation = getattr(runtime, name)(**parameters)
    runtime._registered = True
    bar = layout()
    runtime._status_bar_draw(SimpleNamespace(layout=bar), None)
    bar.progress.assert_called_once_with(factor=0.0, type="BAR", text=label)
    bar.operator.assert_not_called()
    assert not runtime.jobs
    type(runtime).redraw_ui(runtime)
    assert runtime._redraw_status_bars in timers
    timers.clear()

    runtime._tick()
    runtime.server.states[operation.identifier] = {"state": "running", "progress": .4,
        "message": "Working {count}", "message_args": {"count": 2}, "phase": {"index": 1, "total": 3}}
    runtime._tick()
    bar.reset_mock()
    runtime._status_bar_draw(SimpleNamespace(layout=bar), None)
    bar.progress.assert_called_once_with(factor=.4, type="BAR", text="1/3 处理中 2")
    type(runtime).redraw_ui(runtime)
    assert runtime._redraw_status_bars in timers
    timers.clear()
    type(runtime).redraw_ui(runtime)
    assert not timers

    runtime.server.states[operation.identifier] = {"state": terminal, "result": {}}
    runtime._tick()
    type(runtime).redraw_ui(runtime)
    assert runtime._redraw_status_bars in timers
    bar.reset_mock()
    runtime._status_bar_draw(SimpleNamespace(layout=bar), None)
    bar.row.assert_not_called()
    assert operation.done and not runtime.jobs


def test_running_job_precedes_waiting_maintenance(job_runtime, blender):
    runtime = job_runtime
    job = runtime.submit("work")
    runtime._tick()
    operation = runtime.preview_files()
    assert runtime._status_bar_item() is job
    runtime.server.states[job._job_id] = {"state": "succeeded", "result": {}}
    runtime._tick()
    assert runtime._status_bar_item() is operation
    runtime.server.states[operation.identifier] = {"state": "running", "message": "Reading job files"}
    runtime._tick()
    assert operation.text == "Reading job files"
