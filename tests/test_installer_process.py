"""Verify cancellation reaps the installer and its child processes."""
import os
import subprocess
import sys
import time
from unittest.mock import Mock, patch

import pytest

from blendjob.configuration import ServerConfiguration
from blendjob.control_maintenance import SetupOperation
from blendjob.processes import RegisteredServerProcess, terminate_process_tree


@pytest.mark.skipif(os.name != "nt", reason="Windows process tree regression")
def test_cancel_installation_terminates_installer_and_child(tmp_path):
    entrypoint = tmp_path / "app.py"
    entrypoint.touch()
    config = ServerConfiguration(f"{entrypoint}:server", {"python": "3.12"})
    operation = SetupOperation(config, tmp_path, tmp_path,
                               stop_server=Mock(), save_location=Mock())
    pid_path = tmp_path / "child.pid"
    script = """import subprocess, sys, time
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
with open(sys.argv[1], 'w') as stream:
    stream.write(str(child.pid))
time.sleep(60)
"""
    command = [sys.executable, "-c", script, str(pid_path)]
    launched = []
    popen = subprocess.Popen

    def launch(*args, **kwargs):
        process = popen(*args, **kwargs)
        launched.append(process)
        return process

    deadline = time.monotonic() + 5

    def cancel():
        if pid_path.exists() and pid_path.read_text():
            raise RuntimeError("Operation cancelled")
        if time.monotonic() >= deadline:
            raise AssertionError("Installer child did not start")

    try:
        with patch.object(config, "install_command", return_value=command), \
             patch.object(operation, "check_cancelled", side_effect=cancel), \
             patch("blendjob.control_maintenance.subprocess.Popen", side_effect=launch):
            with pytest.raises(RuntimeError, match="Operation cancelled"):
                operation.install()
        child = RegisteredServerProcess(int(pid_path.read_text()))
        child.wait(timeout=5)
        assert launched[0].poll() is not None
        assert child.poll() is not None
        assert not (tmp_path / "install-status.json").exists()
    finally:
        for process in launched:
            if process.poll() is None:
                terminate_process_tree(process)
                process.wait(timeout=5)
        if pid_path.exists() and pid_path.read_text():
            child = RegisteredServerProcess(int(pid_path.read_text()))
            if child.poll() is None:
                terminate_process_tree(child)
                child.wait(timeout=5)


def test_posix_termination_targets_the_owned_process_group():
    process = Mock(pid=123, _blendjob_session=True)
    with patch("blendjob.processes.os.name", "posix"), \
         patch("signal.SIGKILL", 9, create=True), \
         patch("os.killpg", create=True) as kill_group:
        terminate_process_tree(process)
    kill_group.assert_called_once_with(123, 9)
    process.terminate.assert_not_called()
