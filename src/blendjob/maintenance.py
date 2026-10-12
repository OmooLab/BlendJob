"""Install environments and migrate storage through the regular job protocol."""
import os
import subprocess
import threading
from pathlib import Path

from .environment import validate_environment
from .storage import StorageLock, StorageMigration, read_json


class MaintenanceController:
    """Run maintenance in a worker and save the location during main-thread polling."""

    def __init__(self, runtime, target):
        self.runtime = runtime
        self.source = runtime.storage_root()
        self.target = target
        runtime.server.stop()
        self.server = runtime.server_controller(target)
        self._cancelled = threading.Event()
        self._save_finished = threading.Event()
        self._save_error = None
        self._committed = False
        self._thread = None
        self.state = {"state": "running", "progress": 0.0, "message": "Preparing Server"}

    @property
    def cancellable(self):
        return not self._committed

    def submit(self, _job_type, _parameters):
        self._thread = threading.Thread(target=self.run, name="BlendJobMaintenance", daemon=True)
        self._thread.start()
        return {"job_id": "maintenance", "directory": str(self.target)}

    def status(self, _job_id):
        if self.state["state"] == "awaiting_save" and not self._save_finished.is_set():
            try:
                self.check_cancelled()
                self.runtime.storage_root(self.target)
                self._committed = True
            except Exception as error:
                self._save_error = error
            finally:
                self._save_finished.set()
        return dict(self.state)

    def cancel(self, _job_id):
        if self.cancellable:
            self._cancelled.set()

    def mark_job_complete(self, _job_id):
        self.cancel("maintenance")
        if self._thread is not None:
            self._thread.join()

    def progress(self, value, message):
        self.state = {"state": "running", "progress": value, "message": message}

    def check_cancelled(self):
        if self._cancelled.is_set():
            raise RuntimeError("Operation cancelled")

    def save_location(self):
        self.check_cancelled()
        self.state = {"state": "awaiting_save", "progress": 0.9, "message": "Saving Server location"}
        while not self._save_finished.wait(0.1):
            self.check_cancelled()
        if self._save_error is not None:
            raise self._save_error

    def validate(self, source=None):
        self.check_cancelled()
        validate_environment(self.target, self.runtime.environment, source)
        try:
            self.server.ensure()
        finally:
            self.server.stop()

    def run(self):
        source_lock, target_lock = StorageLock(), StorageLock()
        try:
            source_lock.acquire(self.source)
            if self.source != self.target:
                target_lock.acquire(self.target)
            self.check_cancelled()
            self.perform()
            self.state = {"state": "succeeded", "progress": 1.0, "message": "Server is ready"}
        except Exception as error:
            state = "cancelled" if self._cancelled.is_set() else "failed"
            self.state = {"state": state, "error": str(error), "message": str(error)}
        finally:
            try:
                if self.server.connection is not None:
                    self.server.stop()
            finally:
                target_lock.release()
                source_lock.release()


class SetupController(MaintenanceController):
    """Install an environment and invoke the add-on's post-setup hook."""

    def __init__(self, runtime, target, *, mirror=False, online=True):
        super().__init__(runtime, target)
        self.mirror = mirror
        self.online = online

    def perform(self):
        self.target.mkdir(parents=True, exist_ok=True)
        try:
            if not self.runtime.environment_ready(self.target):
                raise RuntimeError("Environment needs installation")
            validate_environment(self.target, self.runtime.environment)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            self.install()
            validate_environment(self.target, self.runtime.environment)
        self.check_cancelled()
        self.server.ensure()
        if self.runtime.post_setup is not None:
            self.runtime.post_setup(self)
        self.server.stop()
        self.save_location()

    def install(self):
        if not self.online:
            raise RuntimeError("Online Access is disabled")
        self.progress(0.05, "Installing environment")
        status_path = self.target / "install-status.json"
        status_path.unlink(missing_ok=True)
        environment = dict(os.environ, PYTHONIOENCODING="utf-8:backslashreplace", PYTHONUTF8="1")
        try:
            with (self.target / "install.log").open("w", encoding="utf-8") as log:
                process = subprocess.Popen(
                    self.runtime.install_command("mirror" if self.mirror else "official", root=self.target),
                    stdout=log, stderr=subprocess.STDOUT, cwd=self.target, env=environment,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                try:
                    while process.poll() is None:
                        self._cancelled.wait(0.2)
                        self.check_cancelled()
                        status = read_json(status_path)
                        self.progress(0.05 + 0.55 * status.get("progress", 0), status.get("message", "Installing environment"))
                    if process.returncode:
                        raise RuntimeError(f"Environment installation failed; see {self.target / 'install.log'}")
                finally:
                    if process.poll() is None:
                        process.terminate()
                    process.wait()
        finally:
            status_path.unlink(missing_ok=True)

    def request(self, job_type, parameters):
        """Run an add-on setup job with maintenance progress and cancellation."""
        self.check_cancelled()
        return self.server.request(
            job_type, parameters, cancel_check=self._cancelled.is_set,
            progress=lambda status: self.progress(
                0.65 + 0.2 * status.get("progress", 0), status.get("message", "Preparing Server"),
            ),
        )


class MigrateController(MaintenanceController):
    """Copy and validate before saving the target and removing the source."""

    def __init__(self, runtime, target):
        super().__init__(runtime, target)
        record = runtime.pending_migration()
        self._committed = bool(record)
        if record:
            self.source = Path(record["source"])

    def perform(self):
        if self.source == self.target:
            return
        migration = StorageMigration(self.source, self.target, self.progress, self._cancelled.is_set)
        if self._committed:
            self.validate(self.source)
            migration.cleanup_source()
            return
        try:
            migration.copy()
            self.validate(self.source)
            self.save_location()
        except Exception:
            migration.discard_copy()
            raise
        migration.cleanup_source()
