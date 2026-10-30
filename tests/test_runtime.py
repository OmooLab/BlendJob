import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

import blendjob
from blendjob.runtime import JobRuntime
from blendjob.operator import JobOperator


class JobRuntimeEnvironmentTest(unittest.TestCase):
    def test_install_commands_include_explicit_source(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "example.py").touch()
            runtime = JobRuntime(
                "example.py:server",
                entrypoint_root=directory,
                storage_root=directory,
                environment={"python": "3.12"},
                namespace="example",
            )

            official = runtime._config.install_command(runtime.storage_root())
            mirror = runtime._config.install_command(runtime.storage_root(), source="mirror")

        self.assertEqual(official[-2:], ["--source", "official"])
        self.assertEqual(mirror[-2:], ["--source", "mirror"])

    def test_storage_reads_do_not_create_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            entrypoint = Path(directory) / "example.py"
            entrypoint.touch()
            target = Path(directory) / "server"
            runtime = JobRuntime(f"{entrypoint}:server", storage_root=target,
                                 environment={"python": "3.12"}, namespace="example")
            self.assertEqual(runtime.storage_root(), target)
            self.assertEqual(runtime.state(), "UNAVAILABLE")
            self.assertFalse(target.exists())
            runtime._config.install_command(runtime.storage_root())
            self.assertTrue((target / "environment.json").is_file())

    def test_setup_target_inspection_and_execution_reject_unrelated_data(self):
        from blendjob.storage import write_json
        from blendjob.environment import environment_python
        with tempfile.TemporaryDirectory() as directory:
            entrypoint = Path(directory) / "app.py"
            entrypoint.touch()
            runtime = JobRuntime(f"{entrypoint}:server", storage_root=Path(directory) / "current",
                                 environment={"python": "3.12"}, namespace="example")
            target = Path(directory) / "selected"
            self.assertFalse(runtime.inspect_installation(target)["environment_ready"])
            self.assertFalse(target.exists())
            target.mkdir()
            unrelated = target / "keep.txt"
            unrelated.write_text("keep")
            with self.assertRaisesRegex(ValueError, "not a BlendJob"):
                runtime.inspect_installation(target)
            with self.assertRaisesRegex(ValueError, "not a BlendJob"):
                runtime.setup(target)
            self.assertEqual(unrelated.read_text(), "keep")
            write_json(target / "environment.json", {"python": "3.12"})
            self.assertFalse(runtime.inspect_installation(target)["environment_ready"])
            python = environment_python(target)
            python.parent.mkdir(parents=True)
            python.touch()
            write_json(target / "manifest.json", {"environment_hash": runtime._config.environment_hash})
            self.assertTrue(runtime.inspect_installation(target)["environment_ready"])
            self.assertEqual(runtime.state(), "UNAVAILABLE")



    def test_job_runtime_is_the_public_runtime_class(self):
        self.assertIs(blendjob.JobRuntime, JobRuntime)
        self.assertFalse(hasattr(blendjob, "BlenderJobRuntime"))
        self.assertEqual(
            set(blendjob.__all__),
            {"JobResult", "JobRuntime", "JobServer"},
        )
        self.assertFalse(hasattr(blendjob, "JobOperatorState"))
        self.assertFalse(hasattr(blendjob, "ServerController"))
        from blendjob.server import JobServer
        self.assertIs(blendjob.JobServer, JobServer)
        self.assertFalse(hasattr(blendjob, "JobContext"))

    def test_server_runner_uses_bundled_launcher(self):
        from blendjob.configuration import ServerConfiguration
        config = SimpleNamespace(server_entrypoint="addon/server/__init__.py:server")
        with patch("blendjob.configuration.environment_python", return_value=Path("environment/python")):
            command = ServerConfiguration.server_command(config, Path("storage"), 8123, "instance")

        self.assertEqual(command[:2], [str(Path("environment/python")), "-u"])
        self.assertEqual(Path(command[2]).name, "runner.py")
        self.assertEqual(Path(command[2]).parent.name, "launcher")
        self.assertEqual(command[3], "--entrypoint")
        self.assertNotIn("blendjob.runner", command)
        self.assertIn("addon/server/__init__.py:server", command)
