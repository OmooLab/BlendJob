"""Surface fatal IPC failures instead of leaving frontend operations running."""
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from blendjob.process import ControlProcess
from blendjob.storage import write_json


@pytest.mark.parametrize("failure", ["task", "poll"])
def test_worker_failure_exits_control_process_and_reports_error(tmp_path, failure):
    entrypoint = tmp_path / "app.py"
    entrypoint.touch()
    write_json(tmp_path / "config.json", {
        "server_entrypoint": f"{entrypoint}:server", "environment": {"python": "3.12"},
        "storage_root": str(tmp_path / "storage"), "parent_pid": os.getpid(),
    })
    identifier = "a" * 32
    if failure == "task":
        write_json(tmp_path / "command-00000001.json", {
            "id": identifier, "sequence": 1, "action": "start", "parameters": {},
        })
    script = '''import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from blendjob.control import ControlService
service = ControlService(sys.argv[2])
def fail(*args):
    raise OSError('Injected IPC write failure')
if sys.argv[3] == 'task':
    service.publish = fail
else:
    service.publish_server = fail
stop = service.server.stop
def stop_and_record():
    stop()
    (service.directory / 'stopped').touch()
service.server.stop = stop_and_record
service.run()
'''
    source = Path(__file__).resolve().parents[1] / "src"
    with (tmp_path / "control.log").open("w") as log:
        process = subprocess.Popen([sys.executable, "-c", script, str(source), str(tmp_path), failure],
                                   stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            process.wait(timeout=10)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
    assert process.returncode != 0
    assert "Injected IPC write failure" in (tmp_path / "control.log").read_text()
    assert (tmp_path / "stopped").exists()
    control = ControlProcess(SimpleNamespace())
    control.directory, control.process = tmp_path, process
    with pytest.raises(RuntimeError, match="control process exited"):
        control.status(identifier)
