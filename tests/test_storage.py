import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from blendjob import storage
from blendjob.runtime import JobRuntime


class StorageMigrationTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        state = patch.object(storage, "storage_state_directory", return_value=self.base / "state")
        state.start()
        self.addCleanup(state.stop)
        self.source = self.base / "old" / "server"
        self.target = self.base / "new" / "server"
        self.source.mkdir(parents=True)
        (self.source / "data").mkdir()
        (self.source / "data/sample.bin").write_bytes(b"sample contents")
        (self.source / "empty").mkdir()
        entrypoint = self.base / "app.py"
        entrypoint.touch()
        self.runtime = JobRuntime(f"{entrypoint}:server", storage_root=self.source,
                                  environment={"python": "3.12"}, namespace="test")

    def migration(self, cancel=lambda: False):
        return storage.StorageMigration(self.source, self.target, Mock(), cancel)

    def run_job(self, job):
        job.submit("migrate", {})
        deadline = time.monotonic() + 5
        while job.status("")["state"] not in {"succeeded", "failed", "cancelled"}:
            if time.monotonic() >= deadline:
                job.cancel("")
                self.fail("Maintenance did not finish")
            time.sleep(0.005)
        job.mark_job_complete("")
        return job.status("")

    def test_copy_verifies_contents_and_preserves_empty_directories(self):
        migration = self.migration()
        migration.copy()
        self.assertTrue(self.source.exists())
        self.assertEqual((self.target / "data/sample.bin").read_bytes(), b"sample contents")
        self.assertTrue((self.target / "empty").is_dir())
        self.assertEqual(storage.migration_record(self.target),
                         {"source": str(self.source), "target": str(self.target)})
        self.assertFalse(storage.migration_record(self.source))
        migration.cleanup_source()
        self.assertFalse(self.source.exists())
        self.assertFalse(storage.migration_record(self.target))

    def test_conflicts_and_nested_targets_preserve_existing_data(self):
        self.target.mkdir(parents=True)
        (self.target / "keep").write_text("keep")
        migration = self.migration()
        with self.assertRaisesRegex(ValueError, "empty"):
            migration.copy()
        migration.discard_copy()
        self.assertEqual((self.target / "keep").read_text(), "keep")
        for target in (self.source, self.source.parent, self.source / "nested"):
            with self.assertRaises(ValueError):
                storage.StorageMigration(self.source, target, Mock(), lambda: False)

    def test_restart_before_save_recopies_new_and_changed_source_files(self):
        self.migration().copy()
        (self.source / "data/sample.bin").write_bytes(b"changed")
        (self.source / "new-job.txt").write_text("new")
        (self.target / "stale.txt").write_text("stale")
        job = self.runtime.migrate_controller(self.target)
        with patch.object(job, "validate"):
            status = self.run_job(job)
        self.assertEqual(status["state"], "succeeded")
        self.assertEqual((self.target / "data/sample.bin").read_bytes(), b"changed")
        self.assertEqual((self.target / "new-job.txt").read_text(), "new")
        self.assertFalse((self.target / "stale.txt").exists())
        self.assertFalse(self.source.exists())

    def test_save_runs_on_polling_thread_before_source_removal(self):
        main_thread = threading.get_ident()
        current = self.source
        def location(target=None):
            nonlocal current
            if target is None:
                return current
            self.assertEqual(threading.get_ident(), main_thread)
            self.assertTrue(self.source.exists())
            self.assertTrue((target / "data/sample.bin").is_file())
            current = target
            return current
        self.runtime.storage_root_setting = location
        job = self.runtime.migrate_controller(self.target)
        with patch.object(job, "validate"):
            status = self.run_job(job)
        self.assertEqual(status["state"], "succeeded")
        self.assertEqual(self.runtime.storage_root(), self.target)
        self.assertFalse(self.source.exists())

    def test_save_failure_discards_copy_and_preserves_source(self):
        def location(target=None):
            if target is not None:
                raise RuntimeError("save failed")
            return self.source
        self.runtime.storage_root_setting = location
        job = self.runtime.migrate_controller(self.target)
        with patch.object(job, "validate"):
            status = self.run_job(job)
        self.assertEqual(status["state"], "failed")
        self.assertIn("save failed", status["error"])
        self.assertTrue(self.source.exists())
        self.assertFalse(self.target.exists())
        self.assertEqual(self.runtime.storage_root(), self.source)

    def test_cancel_and_validation_failure_preserve_source(self):
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                job = self.runtime.migrate_controller(self.target)
                def validate(*_args):
                    if cancelled:
                        job.cancel("")
                    else:
                        raise RuntimeError("invalid environment")
                with patch.object(job, "validate", side_effect=validate):
                    status = self.run_job(job)
                self.assertEqual(status["state"], "cancelled" if cancelled else "failed")
                self.assertTrue(self.source.exists())
                self.assertFalse(self.target.exists())

    def test_cleanup_retry_uses_saved_target_without_copying(self):
        job = self.runtime.migrate_controller(self.target)
        with patch.object(job, "validate"), patch.object(storage, "remove_server_directory", side_effect=PermissionError("busy")):
            status = self.run_job(job)
        self.assertEqual(status["state"], "failed")
        self.assertIn("cleanup", status["error"])
        self.assertEqual(self.runtime.storage_root(), self.target)
        self.assertTrue(self.runtime.pending_migration())
        resumed = self.runtime.migrate_controller()
        self.assertFalse(resumed.cancellable)
        with patch.object(resumed, "validate"), patch.object(storage.StorageMigration, "copy", side_effect=AssertionError("must not recopy")):
            status = self.run_job(resumed)
        self.assertEqual(status["state"], "succeeded")
        self.assertFalse(self.source.exists())
        self.assertFalse(self.runtime.pending_migration())

    def test_low_space_does_not_remove_source(self):
        job = self.runtime.migrate_controller(self.target)
        with patch.object(storage.shutil, "disk_usage", return_value=Mock(free=0)):
            status = self.run_job(job)
        self.assertEqual(status["state"], "failed")
        self.assertTrue(self.source.exists())
        self.assertFalse(self.target.exists())

    def test_lock_is_external_and_blocks_another_process(self):
        lease = storage.StorageLock()
        lease.acquire(self.source)
        self.addCleanup(lease.release)
        competing = storage.StorageLock()
        with self.assertRaises(RuntimeError):
            competing.acquire(self.source)
        if os.name == "nt":
            code = "import msvcrt,sys; f=open(sys.argv[1],'r+b'); msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)"
            result = subprocess.run([sys.executable, "-c", code, str(storage.storage_state_path(self.source, "lock"))], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
        lease.release()
        competing.acquire(self.source)
        competing.release()

    def test_setup_hook_receives_online_policy_and_controls_failure(self):
        from blendjob.maintenance import SetupController
        self.runtime.post_setup = Mock(side_effect=RuntimeError("setup hook failed"))
        job = self.runtime.setup_controller(self.target, online=False)
        with patch.object(SetupController, "install"), patch("blendjob.maintenance.validate_environment"), patch.object(job.server, "ensure"):
            status = self.run_job(job)
        self.assertEqual(status["state"], "failed")
        self.assertIn("setup hook failed", status["error"])
        self.runtime.post_setup.assert_called_once_with(job)
        self.assertFalse(job.online)
        self.assertEqual(self.runtime.storage_root(), self.source)

    def test_offline_setup_reuses_environment_and_saves_after_hook(self):
        self.target.mkdir(parents=True)
        storage.write_json(self.target / "manifest.json", {"environment_hash": self.runtime.environment_hash})
        from blendjob.environment import environment_python
        python = environment_python(self.target)
        python.parent.mkdir(parents=True)
        python.touch()
        seen = []
        self.runtime.post_setup = lambda setup: seen.append(self.runtime.storage_root())
        job = self.runtime.setup_controller(self.target, online=False)
        with patch("blendjob.maintenance.validate_environment"), patch.object(job.server, "ensure"), patch.object(job, "install", side_effect=AssertionError("already installed")):
            status = self.run_job(job)
        self.assertEqual(status["state"], "succeeded")
        self.assertEqual(seen, [self.source])
        self.assertEqual(self.runtime.storage_root(), self.target)

    def test_same_location_migration_is_a_noop(self):
        job = self.runtime.migrate_controller(self.source)
        status = self.run_job(job)
        self.assertEqual(status["state"], "succeeded")
        self.assertTrue((self.source / "data/sample.bin").is_file())

    def test_cancellation_cannot_interrupt_cleanup_after_save(self):
        from types import SimpleNamespace
        job = self.runtime.migrate_controller(self.target)
        self.runtime.active_job = SimpleNamespace(controller=job, cancelled=False, started=True, job_id="maintenance")
        def finish():
            self.runtime.cancel_active()
            self.assertFalse(self.runtime.active_job.cancelled)
            self.assertFalse(job.cancellable)
        with patch.object(job, "validate"), patch.object(storage.StorageMigration, "cleanup_source", side_effect=finish):
            status = self.run_job(job)
        self.assertEqual(status["state"], "succeeded")

    def test_setup_request_forwards_progress_and_cancellation(self):
        job = self.runtime.setup_controller(self.target)
        job.server.request = Mock(return_value="result")
        self.assertEqual(job.request("prepare", {"key": "value"}), "result")
        call = job.server.request.call_args
        self.assertEqual(call.args, ("prepare", {"key": "value"}))
        call.kwargs["progress"]({"progress": 0.5, "message": "Preparing resources"})
        self.assertEqual(job.state["message"], "Preparing resources")
        job.cancel("")
        self.assertTrue(call.kwargs["cancel_check"]())
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            job.request("prepare", {})
