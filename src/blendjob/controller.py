import socket
import subprocess
import time
import uuid
from dataclasses import dataclass

from .client import JobClient, JobResult


@dataclass
class ServerConnection:
    port: int
    pid: int
    instance_id: str
    client: JobClient


class ServerController:
    """Own a dedicated local JobServer process and its HTTP connection."""

    def __init__(
        self,
        command_factory,
        *,
        cwd_factory,
        log_path_factory,
        host="127.0.0.1",
        creationflags=0,
    ):
        self.command_factory = command_factory
        self.cwd_factory = cwd_factory
        self.log_path_factory = log_path_factory
        self.host = host
        self.creationflags = creationflags
        self.connection = None
        self.process = None
        self.log_file = None
        self.auto_start = False
        self.snapshot = {"state": "STOPPED", "message": "Stopped"}

    def start(self):
        if self.process is not None and self.process.poll() is None:
            return self.connection
        self._close_process()
        port = self._available_port()
        instance_id = uuid.uuid4().hex
        log_path = self.log_path_factory()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_file = log_path.open("a", encoding="utf-8")
        command = self.command_factory(port, instance_id)
        try:
            self.process = subprocess.Popen(
                command,
                stdout=self.log_file,
                stderr=subprocess.STDOUT,
                cwd=self.cwd_factory(),
                creationflags=self.creationflags,
            )
        except OSError:
            self.log_file.close()
            self.log_file = None
            raise
        client = JobClient(self.host, port)
        self.connection = ServerConnection(
            port,
            self.process.pid,
            instance_id,
            client,
        )
        self.snapshot = {"state": "STARTING", "message": "Starting", "port": port}
        return self.connection

    def health(self, timeout=0.3):
        if self.connection is None:
            raise RuntimeError("Server is not running")
        health = self.connection.client.request("GET", "/health", timeout=timeout)
        if health.get("instance_id") != self.connection.instance_id:
            raise RuntimeError("Server instance does not match")
        state = "BUSY" if health.get("busy") else "READY"
        self.snapshot = {
            **health,
            "state": state,
            "message": "Busy" if state == "BUSY" else "Ready",
            "port": self.connection.port,
        }
        return health

    def ensure(self, timeout=60.0):
        self.auto_start = True
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                self.health(timeout=0.5)
                return self.connection
            except RuntimeError:
                self._close_finished_process()
            if self.process is None:
                self.start()
            time.sleep(0.05)
        raise RuntimeError(f"Server did not become ready; log: {self.log_path_factory()}")

    def enable(self):
        self.auto_start = True
        try:
            self.health(timeout=0.2)
        except RuntimeError:
            self.start()
        return self.connection

    def stop(self, timeout=10.0):
        self.auto_start = False
        if self.connection is not None:
            try:
                self.connection.client.shutdown()
            except RuntimeError:
                pass
        deadline = time.monotonic() + timeout
        while self.process is not None and self.process.poll() is None:
            if time.monotonic() >= deadline:
                self._close_process(terminate=True)
                break
            time.sleep(0.05)
        self._close_process()
        self.connection = None
        self.snapshot = {"state": "STOPPED", "message": "Stopped"}

    def restart(self):
        self.stop()
        self.auto_start = True
        return self.start()

    def submit(self, job_type, parameters):
        return self.ensure().client.submit(job_type, parameters)

    def request(
        self,
        job_type,
        parameters,
        *,
        response=None,
        progress=None,
        cancel_check=None,
        poll_interval=0.25,
    ):
        """Submit one Job and return its response after polling to completion."""
        submitted = self.submit(job_type, parameters)
        job_id = submitted["job_id"]
        directory = submitted["directory"]
        cancel_requested = False
        try:
            while True:
                if (
                    not cancel_requested
                    and cancel_check is not None
                    and cancel_check()
                ):
                    self.cancel(job_id)
                    cancel_requested = True
                status = self.status(job_id)
                if progress is not None:
                    progress({"job_id": job_id, "directory": directory, **status})
                state = status.get("state")
                if state == "succeeded":
                    result = JobResult._from_status(
                        {"job_id": job_id, "directory": directory, **status}
                    )
                    if response is not None:
                        response(result)
                    return result
                if state == "cancelled":
                    raise RuntimeError("Server request was cancelled")
                if state == "failed":
                    raise RuntimeError(status.get("error") or "Server request failed")
                time.sleep(max(float(poll_interval), 0.01))
        finally:
            self.mark_job_complete(job_id)

    def status(self, job_id):
        return self.ensure().client.status(job_id)

    def cancel(self, job_id):
        return self.ensure().client.cancel(job_id)

    def resource(self, name):
        return self.ensure().client.resource(name)

    def clear_resource(self, name):
        return self.ensure().client.clear_resource(name)

    def detach(self):
        self.auto_start = False
        self.connection = None
        self._close_process(terminate=True)
        self.snapshot = {"state": "STOPPED", "message": "Stopped"}

    def poll(self, *, available=True):
        if not available:
            self.snapshot = {"state": "UNAVAILABLE", "message": "Environment is not installed"}
            return 2.0
        try:
            self.health(timeout=0.5)
            return 5.0
        except RuntimeError as error:
            self._close_finished_process()
            if not self.auto_start:
                self.snapshot = {"state": "STOPPED", "message": "Stopped"}
                return 2.0
            try:
                self.start()
            except OSError as start_error:
                self.snapshot = {"state": "ERROR", "message": str(start_error)}
                return 2.0
            self.snapshot = {"state": "STARTING", "message": "Starting"}
            return 1.0

    def mark_job_complete(self, job_id):
        active = self.snapshot.get("active_job")
        if active and active.get("job_id") != job_id:
            return
        if self.snapshot.get("state") == "BUSY":
            self.snapshot = {
                **self.snapshot,
                "busy": False,
                "active_job": None,
                "state": "READY",
                "message": "Ready",
            }

    def _available_port(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind((self.host, 0))
            return listener.getsockname()[1]

    def _close_finished_process(self):
        if self.process is not None and self.process.poll() is not None:
            self._close_process()
            self.connection = None

    def _close_process(self, terminate=False):
        process = self.process
        self.process = None
        if process is not None and terminate and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3.0)
        if self.log_file is not None:
            self.log_file.close()
            self.log_file = None
