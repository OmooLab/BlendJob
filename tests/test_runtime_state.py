"""Cover unified state precedence and default server controls."""
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from blendjob import JobRuntime


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    entrypoint = tmp_path / "server.py"
    entrypoint.touch()
    runtime = JobRuntime(f"{entrypoint}:server", storage_root=tmp_path,
                         environment={"python": "3.12"}, namespace="test")
    monkeypatch.setattr(runtime._config, "environment_ready", Mock(return_value=True))
    monkeypatch.setattr(runtime, "redraw_ui", Mock())
    monkeypatch.setitem(sys.modules, "bpy", SimpleNamespace(
        types=SimpleNamespace(Operator=type("Operator", (), {})),
        props=SimpleNamespace(StringProperty=Mock(), BoolProperty=Mock()),
    ))
    return runtime


@pytest.mark.parametrize("server_state", ["OFFLINE", "STARTING", "IDLE", "PROCESSING", "STOPPING", "ERROR", "UNAVAILABLE"])
def test_environment_and_maintenance_take_precedence(runtime, server_state):
    runtime.server.snapshot = {"state": server_state}
    runtime._config.environment_ready.return_value = False
    assert runtime.state() == "UNAVAILABLE"
    for name, expected in (("setup", "INSTALLING"), ("migrate", "MIGRATING")):
        runtime._operation = SimpleNamespace(name=name)
        assert runtime.state() == expected
    runtime._operation = None
    runtime._config.environment_ready.return_value = True
    assert runtime.state() == ("OFFLINE" if server_state == "UNAVAILABLE" else server_state)


def test_local_job_is_busy_until_released(runtime):
    runtime.server.snapshot = {"state": "IDLE"}
    runtime.jobs[:] = [SimpleNamespace(name="example")]
    assert runtime.state() == "PROCESSING"
    runtime.server.snapshot["state"] = "STOPPING"
    assert runtime.state() == "STOPPING"
    runtime.server.snapshot["state"] = "IDLE"
    runtime.jobs.clear()
    assert runtime.state() == "IDLE"


@pytest.mark.parametrize("state,actions", [
    ("UNAVAILABLE", {"setup_server"}),
    ("INSTALLING", set()), ("MIGRATING", set()),
    ("OFFLINE", {"start_server"}),
    ("STARTING", {"stop_server"}),
    ("IDLE", {"stop_server", "restart_server"}),
    ("PROCESSING", {"stop_server", "restart_server"}),
    ("STOPPING", set()),
    ("ERROR", {"start_server", "stop_server", "restart_server"}),
])
def test_controls_and_operator_polls_agree(runtime, monkeypatch, state, actions):
    monkeypatch.setattr(runtime, "state", lambda: state)
    layout = Mock()
    layout.row.return_value = layout
    runtime.draw_server_controls(layout)
    drawn = {call.args[0].split(".")[-1] for call in layout.operator.call_args_list}
    assert drawn == actions | (set() if state == "UNAVAILABLE" else {"open_server_log"})
    for cls in runtime.operator_classes():
        if hasattr(cls, "allowed_states"):
            assert cls.poll(None) == (cls.bl_idname.split(".")[-1] in actions)
    if state == "UNAVAILABLE":
        layout.reset_mock()
        runtime.draw_server_controls(layout, setup_operator="addon.setup_ai")
        assert "addon.setup_ai" in [call.args[0] for call in layout.operator.call_args_list]


def test_model_resource_uses_existing_snapshot(runtime):
    runtime.server.snapshot = {"state": "IDLE", "resources": {"models": {"loaded": ["depth"]}}}
    assert runtime.resource("models") == {"loaded": ["depth"]}
    assert runtime.resource("missing") == {}


@pytest.mark.parametrize("state,submit", [
    ("UNAVAILABLE", False), ("INSTALLING", False), ("MIGRATING", False),
    ("OFFLINE", True), ("STARTING", True), ("IDLE", True),
    ("PROCESSING", True), ("STOPPING", False), ("ERROR", False),
])
def test_submission_states(runtime, monkeypatch, state, submit):
    monkeypatch.setattr(runtime, "state", lambda: state)
    assert runtime.can_submit() is submit


@pytest.mark.parametrize("operation,actions", [
    ("start", {"stop_server"}),
    ("stop", set()),
    ("clear_resource", set()),
    ("preview_files", set()),
])
def test_controls_draw_only_actions_accepted_during_an_operation(runtime, operation, actions):
    runtime.server.snapshot = {"state": "IDLE"}
    runtime._operation = SimpleNamespace(name=operation)
    layout = Mock()
    layout.row.return_value = layout

    runtime.draw_server_controls(layout)

    drawn = {call.args[0].split(".")[-1] for call in layout.operator.call_args_list}
    assert drawn == actions | {"open_server_log"}
