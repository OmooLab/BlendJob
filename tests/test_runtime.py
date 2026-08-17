import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import blendjob
from blendjob.runtime import EnvironmentController, JobRuntime


class EnvironmentControllerTest(unittest.TestCase):
    def test_job_runtime_is_the_public_runtime_class(self):
        self.assertIs(blendjob.JobRuntime, JobRuntime)
        self.assertFalse(hasattr(blendjob, "BlenderJobRuntime"))

    def test_post_install_failure_is_reported_as_failure(self):
        runtime = SimpleNamespace()

        def post_install(_runtime):
            raise RuntimeError("model download failed")

        runtime.post_install = post_install
        controller = EnvironmentController(runtime)
        controller.process = SimpleNamespace(poll=lambda: 0, returncode=0)
        with tempfile.TemporaryDirectory() as directory, patch.object(
            runtime,
            "install_status_path",
            return_value=Path(directory) / "missing.json",
            create=True,
        ):
            controller.status("environment")
            controller.post_thread.join(1.0)
            status = controller.status("environment")

        self.assertEqual(status["state"], "failed")
        self.assertEqual(status["error"], "model download failed")
        self.assertEqual(status["post_install_error"], "model download failed")

    def test_completed_install_removes_transient_status(self):
        with tempfile.TemporaryDirectory() as directory:
            status_path = Path(directory) / "install-status.json"
            status_path.write_text("{}", encoding="utf-8")
            runtime = SimpleNamespace(install_status_path=lambda: status_path)
            controller = EnvironmentController(runtime)

            controller.mark_job_complete("environment")

            self.assertFalse(status_path.exists())

    def test_server_runner_uses_bundled_launcher(self):
        runtime = SimpleNamespace(
            environment_python=lambda: Path("environment/python"),
            server_entrypoint="addon/server/__init__.py:server",
            storage_root=lambda: Path("storage"),
        )

        command = JobRuntime._server_command(runtime, 8123, "instance")

        self.assertEqual(command[:2], [str(Path("environment/python")), "-u"])
        self.assertEqual(Path(command[2]).name, "runner.py")
        self.assertEqual(Path(command[2]).parent.name, "launcher")
        self.assertEqual(command[3], "--entrypoint")
        self.assertNotIn("blendjob.runner", command)
        self.assertIn("addon/server/__init__.py:server", command)


if __name__ == "__main__":
    unittest.main()
