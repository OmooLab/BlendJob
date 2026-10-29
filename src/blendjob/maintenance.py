"""Dispatch maintenance and commit Blender settings on the polling thread."""
from .process import TERMINAL_STATES


class MaintenanceController:
    def __init__(self, runtime, target):
        self.runtime = runtime
        self.source = runtime.storage_root()
        self.target = target
        self._committed = False
        self._acknowledged = False
        self.job_id = None
        self.state = {"state": "running", "progress": 0.0, "message": "Preparing Server"}

    @property
    def cancellable(self):
        return not self._committed

    def submit(self, _job_type, _parameters):
        parameters = {"target": str(self.target), "source": str(self.source)}
        if self.action == "setup":
            parameters.update(mirror=self.mirror, online=self.online)
        submitted = self.runtime.server.action(self.action, **parameters)
        self.job_id = submitted["job_id"]
        return submitted

    def status(self, _job_id):
        self.state = self.runtime.server.status(self.job_id)
        if self.state.get("state") == "awaiting_save" and not self._acknowledged:
            error = None
            control = self.runtime.server.control
            try:
                if (control.directory / f"cancel-{self.job_id}").exists():
                    raise RuntimeError("Operation cancelled")
                (control.directory / f"saving-{self.job_id}").touch()
                self.runtime.storage_root(self.target)
                self._committed = True
            except Exception as exception:
                error = str(exception)
            control.acknowledge(self.job_id, error)
            self._acknowledged = True
        return dict(self.state)

    def cancel(self, _job_id):
        if self.cancellable and self.job_id:
            self.runtime.server.cancel(self.job_id)

    def mark_job_complete(self, _job_id):
        if self.state.get("state") not in TERMINAL_STATES:
            self.cancel(self.job_id)
        if self.job_id:
            self.runtime.server.control.send("release", job_id=self.job_id)


class SetupController(MaintenanceController):
    action = "setup"

    def __init__(self, runtime, target, *, mirror=False, online=True):
        super().__init__(runtime, target)
        self.mirror = mirror
        self.online = online


class MigrateController(MaintenanceController):
    action = "migrate"
