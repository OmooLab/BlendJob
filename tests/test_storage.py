import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from blendjob import storage
from blendjob.configuration import ServerConfiguration
from blendjob.control_maintenance import SetupOperation, MigrateOperation


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
        self.config = ServerConfiguration(f"{entrypoint}:server", {"python": "3.12"})
        self.server = self.config.server_controller(self.source)
        self.current = self.source

    def save_location(self, target):
        self.current = target

    def operation(self, kind, target, **options):
        return kind(self.config, self.source, target, stop_server=self.server.stop,
                    save_location=self.save_location, **options)

    def migration(self, cancel=lambda: False):
        return storage.StorageMigration(self.source, self.target, Mock(), cancel)

    def test_copy_verifies_contents_and_preserves_empty_directories(self):
        migration = self.migration()
        migration.copy()
        self.assertTrue(self.source.exists())
        self.assertEqual((self.target / "data/sample.bin").read_bytes(), b"sample contents")
        self.assertTrue((self.target / "empty").is_dir())
        migration.cleanup_source()
        self.assertFalse(self.source.exists())

    @unittest.skipIf(os.name == "nt", "POSIX virtual environment directory links")
    def test_migration_preserves_internal_directory_links(self):
        (self.source / "lib64").symlink_to("data", target_is_directory=True)
        (self.source / "root").symlink_to(".", target_is_directory=True)
        migration = self.migration()
        migration.copy()
        migration.cleanup_source()
        self.assertTrue((self.target / "lib64").is_symlink())
        self.assertEqual((self.target / "lib64/sample.bin").read_bytes(), b"sample contents")
        self.assertEqual((self.target / "root").resolve(), self.target)

    @unittest.skipIf(os.name == "nt", "POSIX directory links")
    def test_migration_rejects_external_directory_links(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "keep").write_text("keep")
        (self.source / "external").symlink_to(outside, target_is_directory=True)
        migration = self.migration()
        with self.assertRaisesRegex(ValueError, "External directory links"):
            migration.copy()
        migration.discard_copy()
        self.assertEqual((outside / "keep").read_text(), "keep")
        self.assertTrue(self.source.exists())

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

    def test_save_completes_before_source_removal(self):
        current = self.source
        def location(target=None):
            nonlocal current
            if target is None:
                return current
            self.assertTrue(self.source.exists())
            self.assertTrue((target / "data/sample.bin").is_file())
            current = target
            self.current = target
            return current
        self.save_location = location
        job = self.operation(MigrateOperation, self.target)
        with patch.object(job, "validate"):
            status = job.run()
        self.assertEqual(status["state"], "succeeded")
        self.assertEqual(self.current, self.target)
        self.assertFalse(self.source.exists())

    def test_save_failure_discards_copy_and_preserves_source(self):
        def location(target=None):
            if target is not None:
                raise RuntimeError("save failed")
            return self.source
        self.save_location = location
        job = self.operation(MigrateOperation, self.target)
        with patch.object(job, "validate"):
            status = job.run()
        self.assertEqual(status["state"], "failed")
        self.assertIn("save failed", status["error"])
        self.assertTrue(self.source.exists())
        self.assertFalse(self.target.exists())
        self.assertEqual(self.current, self.source)

    def test_cancel_and_validation_failure_preserve_source(self):
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                job = self.operation(MigrateOperation, self.target)
                def validate(*_args):
                    if cancelled:
                        job.cancel_check = lambda: True
                    else:
                        raise RuntimeError("invalid environment")
                with patch.object(job, "validate", side_effect=validate):
                    status = job.run()
                self.assertEqual(status["state"], "cancelled" if cancelled else "failed")
                self.assertTrue(self.source.exists())
                self.assertFalse(self.target.exists())

    def test_cleanup_failure_warns_without_failing_saved_migration(self):
        job = self.operation(MigrateOperation, self.target)
        with patch.object(job, "validate"), patch.object(storage, "remove_server_directory", side_effect=PermissionError("busy")):
            status = job.run()
        self.assertEqual(status["state"], "succeeded")
        self.assertIn(str(self.source), status["result"]["warning"])
        self.assertIn("old folder remains", status["result"]["warning"])
        self.assertEqual(self.current, self.target)
        self.assertTrue(self.source.exists())
        self.assertTrue((self.target / "data/sample.bin").is_file())
        self.assertFalse(list((self.base / "state").glob("*.json")))

    def test_low_space_does_not_remove_source(self):
        job = self.operation(MigrateOperation, self.target)
        with patch.object(storage.shutil, "disk_usage", return_value=Mock(free=0)):
            status = job.run()
        self.assertEqual(status["state"], "failed")
        self.assertTrue(self.source.exists())
        self.assertFalse(self.target.exists())

    def test_interrupted_commit_preserves_both_locations(self):
        from blendjob.control_maintenance import CommitUncertain
        job = self.operation(MigrateOperation, self.target)
        with patch.object(job, "validate"), patch.object(job, "save_location", side_effect=CommitUncertain("Owner exited")):
            status = job.run()
        self.assertEqual(status["state"], "failed")
        self.assertTrue((self.source / "data/sample.bin").is_file())
        self.assertTrue((self.target / "data/sample.bin").is_file())

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

    def test_setup_validation_failure_does_not_save_location(self):
        job = self.operation(SetupOperation, self.target, online=False)
        with patch.object(job, "install"), patch("blendjob.control_maintenance.validate_environment"), patch.object(job.server, "ensure", side_effect=RuntimeError("Invalid server")):
            status = job.run()
        self.assertEqual(status["state"], "failed")
        self.assertIn("Invalid server", status["error"])
        self.assertFalse(job.online)
        self.assertEqual(self.current, self.source)

    def test_offline_setup_reuses_environment_and_saves_after_validation(self):
        self.target.mkdir(parents=True)
        storage.write_json(self.target / "manifest.json", {"environment_hash": self.config.environment_hash})
        from blendjob.environment import environment_python
        python = environment_python(self.target)
        python.parent.mkdir(parents=True)
        python.touch()
        seen = []
        job = self.operation(SetupOperation, self.target, online=False)
        with patch("blendjob.control_maintenance.validate_environment"), patch.object(job.server, "ensure", side_effect=lambda **kwargs: seen.append(self.current)), patch.object(job, "install", side_effect=AssertionError("already installed")):
            status = job.run()
        self.assertEqual(status["state"], "succeeded")
        self.assertEqual(seen, [self.source])
        self.assertEqual(self.current, self.target)

    def test_same_location_migration_is_a_noop(self):
        job = self.operation(MigrateOperation, self.source)
        status = job.run()
        self.assertEqual(status["state"], "succeeded")
        self.assertTrue((self.source / "data/sample.bin").is_file())

    def test_cancellation_cannot_interrupt_cleanup_after_save(self):
        job = self.operation(MigrateOperation, self.target)
        def finish():
            job.cancel_check = lambda: True
            job.check_cancelled()
            self.assertFalse(job.cancellable)
        with patch.object(job, "validate"), patch.object(storage.StorageMigration, "cleanup_source", side_effect=finish):
            status = job.run()
        self.assertEqual(status["state"], "succeeded")
