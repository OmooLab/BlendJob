import json
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener


class JobClient:
    """Dependency-free HTTP client for a local :class:`JobServer`."""

    def __init__(self, host, port, *, timeout=2.0):
        self.base_url = f"http://{host}:{int(port)}"
        self.timeout = timeout
        self.opener = build_opener(ProxyHandler({}))

    def request(self, method, path, payload=None, timeout=None):
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(
            f"{self.base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with self.opener.open(
                request,
                timeout=self.timeout if timeout is None else timeout,
            ) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            try:
                detail = json.loads(error.read().decode("utf-8")).get("detail")
            except (json.JSONDecodeError, UnicodeDecodeError):
                detail = None
            raise RuntimeError(detail or f"Job Server returned HTTP {error.code}")
        except (OSError, URLError) as error:
            raise RuntimeError(f"Unable to reach Job Server: {error}")

    def health(self):
        return self.request("GET", "/health")

    def submit(self, job_type, parameters):
        return self.request(
            "POST",
            "/jobs",
            {"job_type": str(job_type), "parameters": dict(parameters)},
        )

    def status(self, job_id):
        return self.request("GET", f"/jobs/{job_id}")

    def cancel(self, job_id):
        return self.request("DELETE", f"/jobs/{job_id}")

    def resources(self):
        return self.request("GET", "/resources")

    def resource(self, name):
        return self.request("GET", f"/resources/{name}")

    def clear_resource(self, name):
        return self.request("POST", f"/resources/{name}/clear")

    def shutdown(self):
        return self.request("POST", "/shutdown")


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
