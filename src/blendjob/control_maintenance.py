"""Install and migrate storage inside the independent control process."""
import os
import subprocess
import time

from .environment import validate_environment
from .storage import StorageLock, StorageMigration, read_json
from .processes import terminate_process_tree


class CommitUncertain(RuntimeError):
    """Keep both locations when the owner disappears during settings commit."""


class MaintenanceOperation:
    """Run maintenance synchronously inside the control process worker."""

    def __init__(self, config, source, target, *, stop_server, save_location,
                 progress=lambda state: None, cancel_check=lambda: False):
        self.config = config
        self.source = source
        self.target = target
        self.server = config.server_controller(target)
        self.stop_server = stop_server
        self.save_location = save_location
        self.publish = progress
        self.cancel_check = cancel_check
        self._committed = False
        self.state = {"state": "running", "progress": 0.0, "message": "Preparing Server"}

    @property
    def cancellable(self):
        return not self._committed

    def progress(self, value, message):
        self.state = {"state": "running", "progress": value, "message": message}
        self.publish(self.state)

    def cancelled(self):
        return self.cancellable and self.cancel_check()

    def check_cancelled(self):
        if self.cancelled():
            raise RuntimeError("Operation cancelled")

    def commit_location(self):
        self.check_cancelled()
        self.save_location(self.target)
        self._committed = True

    def validate(self, source=None):
        self.check_cancelled()
        validate_environment(self.target, self.config.environment, source)
        try:
            self.server.ensure(cancel_check=self.cancelled)
        finally:
            self.server.stop()

    def run(self):
        source_lock, target_lock = StorageLock(), StorageLock()
        try:
            self.check_cancelled()
            self.progress(0.0, "Stopping Server")
            self.stop_server()
            self.check_cancelled()
            source_lock.acquire(self.source)
            if self.source != self.target:
                target_lock.acquire(self.target)
            self.check_cancelled()
            result = self.perform()
            self.state = {"state": "succeeded", "progress": 1.0,
                          "message": "Server is ready", "result": result}
        except Exception as error:
            state = "cancelled" if self.cancelled() else "failed"
            self.state = {"state": state, "error": str(error), "message": str(error)}
        finally:
            try:
                if self.state["state"] == "succeeded" and self.server.connection is not None:
                    # Keep the validated process protected until its new owner stops it.
                    self.server.directory_lock = source_lock if self.source == self.target else target_lock
                elif self.server.connection is not None:
                    self.server.stop()
            finally:
                for lease in (source_lock, target_lock):
                    if self.state["state"] != "succeeded" or lease is not self.server.directory_lock:
                        lease.release()
        return self.state


class SetupOperation(MaintenanceOperation):
    """Install and validate an environment before saving its location."""

    def __init__(self, config, source, target, *, mirror=False, online=True, **callbacks):
        super().__init__(config, source, target, **callbacks)
        self.mirror = mirror
        self.online = online

    def perform(self):
        self.target.mkdir(parents=True, exist_ok=True)
        try:
            if not self.config.environment_ready(self.target):
                raise RuntimeError("Environment needs installation")
            validate_environment(self.target, self.config.environment)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            self.install()
            validate_environment(self.target, self.config.environment)
        self.check_cancelled()
        self.server.ensure(cancel_check=self.cancelled)
        self.commit_location()

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
                    self.config.install_command(self.target, "mirror" if self.mirror else "official"),
                    stdout=log, stderr=subprocess.STDOUT, cwd=self.target, env=environment,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    start_new_session=os.name != "nt",
                )
                process._blendjob_session = os.name != "nt"
                try:
                    while process.poll() is None:
                        time.sleep(0.2)
                        self.check_cancelled()
                        status = read_json(status_path)
                        self.progress(0.05 + 0.55 * status.get("progress", 0), status.get("message", "Installing environment"))
                    if process.returncode:
                        raise RuntimeError(f"Environment installation failed; see {self.target / 'install.log'}")
                finally:
                    if process.poll() is None:
                        terminate_process_tree(process)
                    process.wait(timeout=5)
        finally:
            status_path.unlink(missing_ok=True)

class MigrateOperation(MaintenanceOperation):
    """Copy and validate before saving the target and removing the source."""

    def perform(self):
        if self.source == self.target:
            return
        migration = StorageMigration(self.source, self.target, self.progress, self.cancelled)
        try:
            migration.copy()
            self.validate(self.source)
            self.commit_location()
        except Exception as error:
            if not isinstance(error, CommitUncertain):
                migration.discard_copy()
            raise
        try:
            migration.cleanup_source()
        except OSError as error:
            return {"warning": f"Migration completed; old folder remains at {self.source}: {error}"}
