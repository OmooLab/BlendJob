"""Read a completed job value and its output files."""
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class JobResult:
    """Successful Job value and files returned to a Blender Operator."""

    job_id: str
    directory: Path
    value: object

    @classmethod
    def _from_status(cls, status):
        return cls(
            job_id=str(status.get("job_id", "")),
            directory=Path(status["directory"]),
            value=status.get("result"),
        )

    def file(self, name):
        if not isinstance(self.value, dict):
            raise RuntimeError("Job result does not contain named files")
        try:
            relative = self.value[name]
        except KeyError:
            raise RuntimeError(f"Job result does not contain file: {name}") from None
        root = self.directory.resolve()
        path = (root / str(relative)).resolve()
        if path != root and root not in path.parents:
            raise RuntimeError(f"Job result file escapes its directory: {relative}")
        if not path.is_file():
            raise RuntimeError(f"Job result file does not exist: {path}")
        return path
