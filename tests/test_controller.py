import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from blendjob.controller import ServerController
from blendjob.processes import RegisteredServerProcess, terminate_process_tree
from blendjob.configuration import ServerConfiguration
from blendjob.storage import StorageLock, read_json


@pytest.mark.parametrize("cancel_fails", [False, True])
def test_lost_submission_response_cancels_known_id_or_stops_service(tmp_path, cancel_fails):
    server = ServerController(Mock(), cwd_factory=lambda: tmp_path, log_path_factory=lambda: tmp_path / "server.log")
    client = Mock()
    client.submit.side_effect = RuntimeError("Response lost")
    if cancel_fails:
        client.cancel.side_effect = RuntimeError("Cancellation not confirmed")
    server.ensure = Mock(return_value=Mock(client=client))
    server.stop = Mock()
    identifier = "a" * 32
    with pytest.raises(RuntimeError, match="Response lost"):
        server.run_job("example", {}, job_id=identifier)
    client.submit.assert_called_once_with("example", {}, job_id=identifier)
    client.cancel.assert_called_once_with(identifier)
    assert server.stop.call_count == int(cancel_fails)


@pytest.mark.parametrize("cooperative", [False, True])
def test_job_cancellation_waits_for_completion_or_stops_after_deadline(tmp_path, cooperative):
    server = ServerController(Mock(), cwd_factory=lambda: tmp_path, log_path_factory=lambda: tmp_path / "server.log")
    client = Mock()
    client.status.side_effect = lambda _job_id: {
        "state": "cancelled" if cooperative and client.cancel.called else "running"}
    server.ensure = Mock(return_value=Mock(client=client))
    server.stop = Mock()
    states = []
    clock = 0.0

    def sleep(_interval):
        nonlocal clock
        clock += 1
        assert clock <= 6, "Cancellation exceeded its deadline"

    with patch("blendjob.controller.time.monotonic", side_effect=lambda: clock), patch("blendjob.controller.time.sleep", sleep):
        status = server.run_job("example", {}, progress=states.append,
                                cancel_check=lambda: client.status.called)
    assert status["state"] == "cancelled"
    assert states[-1]["state"] == "cancelled"
    assert server.stop.call_count == int(not cooperative)
    client.cancel.assert_called_once_with(status["job_id"])


class ServerProcessLifecycleTest(unittest.TestCase):
    def test_unresponsive_registered_process_blocks_start_and_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "server.py").write_text("", encoding="utf-8")
            config = ServerConfiguration("server.py:server", {"python": "3.12"}, entrypoint_root=root)
            server = config.server_controller(root, directory_lock=StorageLock())
            record = {"root": str(root), "pid": os.getpid(), "parent_pid": os.getpid(),
                      "port": 12345, "instance_id": "unresponsive"}
            path = root / "server-process.json"
            path.write_text(json.dumps(record), encoding="utf-8")
            with patch("blendjob.controller.JobClient.request", side_effect=RuntimeError("unreachable")):
                for action in (server.start, server.stop):
                    with self.subTest(action=action.__name__):
                        with self.assertRaisesRegex(RuntimeError, "still running but cannot be verified"):
                            action()
                        self.assertEqual(read_json(path), record)
                        self.assertIsNone(server.process)

    def test_reloaded_runtime_stops_registered_server_and_releases_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entrypoint = root / "entrypoint.py"
            entrypoint.write_text("from blendjob.server import JobServer\nserver=JobServer('Recovery Test')\n", encoding="utf-8")
            config = ServerConfiguration(f"{entrypoint}:server", {"python": f"{sys.version_info.major}.{sys.version_info.minor}"})
            server = config.server_controller(root, directory_lock=StorageLock())
            with patch("blendjob.configuration.environment_python", return_value=Path(sys.executable)):
                server.ensure(timeout=10)
            process = server.process
            record = read_json(root / "server-process.json")
            self.assertEqual(record["parent_pid"], os.getpid())
            replacement = config.server_controller(root)
            try:
                with patch("blendjob.controller.subprocess.Popen", side_effect=AssertionError("Must reuse registered Server")):
                    connection = replacement.start()
                self.assertEqual(connection.instance_id, record["instance_id"])
                replacement.stop(timeout=5)
                process.wait(timeout=5)
                self.assertIsNotNone(process.poll())
                self.assertFalse((root / "server-process.json").exists())
                lease = StorageLock()
                lease.acquire(root)
                lease.release()
            finally:
                server.stop()

    @unittest.skipUnless(os.name == "nt", "Windows process tree")
    def test_forced_shutdown_terminates_child_and_launcher(self):
        with tempfile.TemporaryDirectory() as directory:
            pid_path = Path(directory) / "child.pid"
            code = "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)']); open(sys.argv[1],'w').write(str(p.pid)); time.sleep(120)"
            parent = subprocess.Popen([sys.executable, "-c", code, str(pid_path)], creationflags=subprocess.CREATE_NO_WINDOW)
            import time
            deadline = time.monotonic() + 5
            while not pid_path.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            try:
                child = RegisteredServerProcess(int(pid_path.read_text()))
                terminate_process_tree(parent)
                parent.wait(timeout=5)
                child.wait(timeout=5)
                self.assertIsNotNone(child.poll())
            finally:
                if parent.poll() is None:
                    terminate_process_tree(parent)
