import os
from pathlib import Path

from .storage import read_json, write_json

import socket
import subprocess
import time
import uuid
from dataclasses import dataclass

from .client import JobClient
from .processes import RegisteredServerProcess, terminate_process_tree

SERVER_DIRECTORY_LOCKS = {}


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
        directory_lock=None,
    ):
        self.command_factory = command_factory
        self.cwd_factory = cwd_factory
        self.log_path_factory = log_path_factory
        self.host = host
        self.creationflags = creationflags
        self.directory_lock = directory_lock
        self.connection = None
        self.process = None
        self.log_file = None
        self.auto_start = False
        self.snapshot = {"state": "STOPPED", "message": "Stopped"}

    def start(self):
        if self.process is None:
            self._recover_connection()
        if self.directory_lock is not None:
            self.directory_lock.acquire(self.cwd_factory())
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
            if self.directory_lock is not None:
                self.directory_lock.release()
            raise
        root = Path(self.cwd_factory()).resolve()
        write_json(root / "server-process.json", {
            "root": str(root), "pid": self.process.pid, "parent_pid": os.getpid(),
            "port": port, "instance_id": instance_id,
        })
        if self.directory_lock is not None:
            SERVER_DIRECTORY_LOCKS[instance_id] = self.directory_lock
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

    def ensure(self, timeout=60.0, *, cancel_check=None):
        self.auto_start = True
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cancel_check is not None and cancel_check():
                raise RuntimeError("Operation cancelled")
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
        if self.connection is None:
            self._recover_connection()
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
        root = Path(self.log_path_factory()).parent.resolve()
        record = read_json(root / "server-process.json")
        if record.get("parent_pid") == os.getpid() and record.get("root") == str(root):
            (root / "server-process.json").unlink(missing_ok=True)
            registered_lock = SERVER_DIRECTORY_LOCKS.pop(record.get("instance_id"), None)
            if registered_lock is not None:
                registered_lock.release()
        if self.directory_lock is not None:
            self.directory_lock.release()
        self.snapshot = {"state": "STOPPED", "message": "Stopped"}

    def _recover_connection(self):
        root = Path(self.log_path_factory()).parent.resolve()
        record = read_json(root / "server-process.json")
        if record.get("root") != str(root):
            return
        process = RegisteredServerProcess(record["pid"])
        if process.poll() is not None:
            return
        if record.get("parent_pid") != os.getpid():
            raise RuntimeError("Another Blender process is using this Server folder")
        client = JobClient(self.host, record["port"])
        try:
            health = client.request("GET", "/health", timeout=0.5)
        except RuntimeError as error:
            raise RuntimeError(
                f"Registered Server process {process.pid} is still running but cannot be verified; "
                "restart Blender before changing the Server location"
            ) from error
        if health.get("instance_id") != record.get("instance_id"):
            raise RuntimeError("The registered Server instance does not match")
        if health.get("busy"):
            raise RuntimeError("The registered Server is busy")
        self.connection = ServerConnection(record["port"], record["pid"], record["instance_id"], client)
        self.process = process

    def restart(self):
        self.stop()
        self.auto_start = True
        return self.start()

    def run_job(self, job_type, parameters, *, job_id=None, progress=None, cancel_check=None):
        """Run one job with a known identity and bounded cancellation."""
        client = self.ensure(cancel_check=cancel_check).client
        job_id = job_id or uuid.uuid4().hex
        submitted = {"job_id": job_id, "directory": str(Path(self.cwd_factory()) / "jobs" / job_id)}
        finished = False
        cancellation_deadline = None
        try:
            if cancel_check is not None and cancel_check():
                raise RuntimeError("Operation cancelled")
            client.submit(job_type, parameters, job_id=job_id)
            while True:
                if cancellation_deadline is None and cancel_check is not None and cancel_check():
                    cancellation_deadline = time.monotonic() + 5
                    client.cancel(job_id)
                if cancellation_deadline is not None and time.monotonic() >= cancellation_deadline:
                    self.stop()
                    status = {**submitted, "state": "cancelled", "message": "Job cancelled"}
                else:
                    status = {**submitted, **client.status(job_id)}
                    self.snapshot = {**self.snapshot, "state": "BUSY", "busy": True,
                                     "active_job": status}
                finished = status.get("state") in {"succeeded", "failed", "cancelled"}
                if progress is not None:
                    progress(status)
                if finished:
                    return status
                time.sleep(0.1)
        finally:
            try:
                if not finished:
                    try:
                        client.cancel(job_id)
                    except RuntimeError:
                        # Acceptance may be unknown if the submission response was lost.
                        self.stop()
            finally:
                self.mark_job_complete(job_id)

    def clear_resource(self, name):
        return self.ensure().client.clear_resource(name)

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
                if self.directory_lock is not None:
                    self.directory_lock.release()
                return 2.0
            try:
                self.start()
            except (OSError, RuntimeError) as start_error:
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
            terminate_process_tree(process)
            try:
                process.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3.0)
        if self.log_file is not None:
            self.log_file.close()
            self.log_file = None
