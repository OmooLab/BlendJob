import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import blendjob
from blendjob.runtime import EnvironmentController, JobRuntime
from blendjob.operator import JobOperatorBase


class EnvironmentControllerTest(unittest.TestCase):
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

            official = runtime.install_command()
            mirror = runtime.install_command(source="mirror")

        self.assertEqual(official[-2:], ["--source", "official"])
        self.assertEqual(mirror[-2:], ["--source", "mirror"])

    def test_progress_is_monotonic_for_one_job(self):
        runtime = SimpleNamespace(update_ui=Mock(), redraw_ui=Mock())
        job = SimpleNamespace(
            job_id="job",
            progress_job_id="job",
            progress=0.8,
            runtime=runtime,
        )

        JobOperatorBase._update_from_job_status(
            SimpleNamespace(starting_message="Starting task"),
            None,
            job,
            {"job_id": "job", "progress": 0.2, "message": "Working"},
        )

        self.assertEqual(job.progress, 0.8)
        runtime.update_ui.assert_called_once_with(None, 0.8, "Working")

    def test_progress_resets_when_post_install_job_changes(self):
        runtime = SimpleNamespace(update_ui=Mock(), redraw_ui=Mock())
        job = SimpleNamespace(
            job_id="environment",
            progress_job_id="environment",
            progress=1.0,
            runtime=runtime,
        )

        JobOperatorBase._update_from_job_status(
            SimpleNamespace(starting_message="Starting task"),
            None,
            job,
            {"job_id": "model", "progress": 0.2, "message": "Downloading model"},
        )

        self.assertEqual(job.progress_job_id, "model")
        self.assertEqual(job.progress, 0.2)
        runtime.update_ui.assert_called_once_with(
            None,
            0.2,
            "Downloading model",
        )

    def test_job_runtime_is_the_public_runtime_class(self):
        self.assertIs(blendjob.JobRuntime, JobRuntime)
        self.assertFalse(hasattr(blendjob, "BlenderJobRuntime"))
        self.assertEqual(
            set(blendjob.__all__),
            {"JobContext", "JobResult", "JobRuntime", "JobServer"},
        )
        self.assertFalse(hasattr(blendjob, "JobOperatorState"))
        self.assertFalse(hasattr(blendjob, "ServerController"))

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
        self.assertNotIn("post_install_error", status)

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
