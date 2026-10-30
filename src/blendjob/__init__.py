"""Public API for Blender runtimes and local job services."""

from .result import JobResult
from .runtime import JobRuntime


__all__ = (
    "JobResult",
    "JobRuntime",
    "JobServer",
)


def __getattr__(name):
    if name == "JobServer":
        from .server import JobServer

        globals()[name] = JobServer
        return JobServer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
