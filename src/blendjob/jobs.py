"""Task names and handles owned by a runtime."""
import re
import time
from dataclasses import dataclass, field
from uuid import uuid4

from .translations import translate_progress


BUILTIN_OPERATIONS = frozenset({
    "setup", "migrate", "clear_resource", "preview_files", "clear_files",
    "start", "stop", "restart",
})
TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled"})


def validate_job_name(name):
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", name):
        raise ValueError("Job name must use lowercase snake_case")
    return name


@dataclass(eq=False)
class Job:
    """Observe or cancel an accepted task owned by the runtime."""

    _runtime: object
    name: str
    parameters: dict
    _on_done: object = None
    id: str = field(default_factory=lambda: uuid4().hex)
    state: str = "queued"
    progress: float = 0.0
    message: str = "Job queued"
    message_args: dict = field(default_factory=dict)
    phase: dict | None = None
    done: bool = False
    result: object = None
    error: str = ""
    cancelled: bool = False
    _job_id: str = ""
    _directory: str = ""
    _status: dict = field(default_factory=dict)
    _finishing: bool = False

    @property
    def cancellable(self):
        return not self.done and not self._finishing

    @property
    def text(self):
        message = translate_progress("Cancelling job" if self.cancelled else self.message,
                                     {} if self.cancelled else self.message_args)
        if self.phase is not None and not self.cancelled:
            message = f"{self.phase['index']}/{self.phase['total']} {message}"
        return message

    def _update(self, status):
        progress = min(max(float(status.get("progress", self.progress)), 0.0), 1.0)
        self.progress = max(self.progress, progress)
        self.message = str(status.get("message") or self.message)
        self.message_args = dict(status.get("message_args") or {})
        self.phase = status.get("phase")

    def cancel(self):
        self._runtime.cancel_job(self)

    def wait(self):
        """Drive the runtime in a standalone script or Blender background process."""
        try:
            import bpy
        except ModuleNotFoundError:
            bpy = None
        if bpy is not None and not bpy.app.background:
            raise RuntimeError("Use on_done instead of wait() in Blender's interactive UI")
        if self._runtime._ticking:
            raise RuntimeError("Cannot wait inside a runtime callback")
        while not self.done:
            self._runtime._tick()
            if not self.done:
                time.sleep(0.05)
        if self.state != "succeeded":
            raise RuntimeError(self.error or "Job cancelled")
        return self.result
