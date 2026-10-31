"""Small runtime fixture with a deterministic process boundary."""
from unittest.mock import Mock
import pytest
from blendjob import JobRuntime
from blendjob.process import ProcessServer


@pytest.fixture
def job_runtime(tmp_path, monkeypatch):
    entrypoint = tmp_path / "app.py"
    entrypoint.touch()
    runtime = JobRuntime("test", server=f"{entrypoint}:server", storage_root=tmp_path / "storage", environment={"python": "3.12"})
    monkeypatch.setattr(runtime._config, "environment_ready", Mock(return_value=True))
    monkeypatch.setattr(runtime, "enable", Mock())
    monkeypatch.setattr(runtime, "redraw_ui", Mock())
    runtime.server = Mock(spec=ProcessServer)
    runtime.server.snapshot = {"state": "IDLE"}
    runtime.server.control = Mock(process=None, directory=tmp_path, sequence=0)
    states = {}

    def submit(name, parameters):
        identifier = str(len(states))
        states[identifier] = {"state": "running", "progress": 0.25, "message": name}
        return {"job_id": identifier, "directory": str(tmp_path / identifier)}

    runtime.server.submit.side_effect = submit
    runtime.server.action.side_effect = lambda action, **parameters: submit(action, parameters)["job_id"]
    runtime.server.status.side_effect = states.__getitem__
    runtime.server.states = states
    return runtime
