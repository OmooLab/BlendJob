"""One server control or maintenance operation, separate from business jobs."""
from dataclasses import dataclass, field
from .translations import translate_progress

MAINTENANCE_MESSAGES = {
    "setup": "Installing environment",
    "migrate": "Migrating Folder",
    "preview_files": "Reading job files",
    "clear_files": "Deleting job files",
}


@dataclass
class Operation:
    name: str
    parameters: dict
    on_done: object = None
    identifier: str = ""
    state: str = "pending"
    progress: float = 0.0
    message: str = ""
    message_args: dict = field(default_factory=dict)
    phase: dict | None = None
    result: object = None
    error: str = ""
    done: bool = False
    _location_acknowledged: bool = False

    @property
    def text(self):
        message = translate_progress(self.message or MAINTENANCE_MESSAGES.get(self.name, ""), self.message_args)
        if self.phase is not None:
            message = f"{self.phase['index']}/{self.phase['total']} {message}"
        return message

    def save_location(self, runtime):
        """Commit the verified location on Blender's main thread exactly once."""
        if self._location_acknowledged:
            return
        control = runtime.server.control
        error = None
        try:
            if (control.directory / f"cancel-{self.identifier}").exists():
                raise RuntimeError("Operation cancelled")
            (control.directory / f"saving-{self.identifier}").touch()
            runtime.storage_root(self.parameters["target"])
        except Exception as exception:
            error = str(exception)
        control.acknowledge(self.identifier, error)
        self._location_acknowledged = True
