import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from blendjob.controller import RegisteredServerProcess, terminate_process_tree
from blendjob.runtime import JobRuntime
from blendjob.storage import StorageLock, read_json


class ServerProcessLifecycleTest(unittest.TestCase):
    def test_reloaded_runtime_stops_registered_server_and_releases_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entrypoint = root / "entrypoint.py"
            entrypoint.write_text("from blendjob import JobServer\nserver=JobServer('Recovery Test')\n", encoding="utf-8")
            runtime = JobRuntime(f"{entrypoint}:server", storage_root=root,
                                 environment={"python": f"{sys.version_info.major}.{sys.version_info.minor}"}, namespace="test")
            with patch.object(runtime, "environment_python", return_value=Path(sys.executable)):
                runtime.server.ensure(timeout=10)
            process = runtime.server.process
            record = read_json(root / "server-process.json")
            self.assertEqual(record["parent_pid"], os.getpid())
            replacement = runtime.server_controller(root)
            try:
                replacement.stop(timeout=5)
                process.wait(timeout=5)
                self.assertIsNotNone(process.poll())
                self.assertFalse((root / "server-process.json").exists())
                lease = StorageLock()
                lease.acquire(root)
                lease.release()
            finally:
                runtime.server.stop()

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
