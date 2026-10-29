"""Exchange commands and snapshots with an owned control process."""
import atexit
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from .storage import read_json, write_json


TERMINAL_STATES = {"succeeded", "failed", "cancelled"}


class ControlProcess:
    def __init__(self, runtime):
        self.runtime = runtime
        self.process = None
        self.directory = None
        self.log = None
        self.sequence = 0
        self._temporary = None

    def start(self):
        if self.process is not None:
            if self.process.poll() is not None:
                raise RuntimeError(f"BlendJob control process exited; log: {self.directory / 'control.log'}")
            return
        self._temporary = tempfile.TemporaryDirectory(prefix="blendjob-control-")
        self.sequence = 0
        self.directory = Path(self._temporary.name)
        config = {
            "server_entrypoint": self.runtime.config.server_entrypoint,
            "storage_root": str(self.runtime.storage_root()),
            "environment": self.runtime.config.environment,
            "parent_pid": os.getpid(),
        }
        write_json(self.directory / "config.json", config)
        self.log = (self.directory / "control.log").open("w", encoding="utf-8")
        launcher = Path(__file__).with_name("launcher") / "control.py"
        try:
            self.process = subprocess.Popen(
                [sys.executable, "-u", str(launcher), str(self.directory)],
                stdin=subprocess.DEVNULL, stdout=self.log, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                start_new_session=os.name != "nt",
            )
        except Exception:
            self.log.close()
            self._temporary.cleanup()
            raise
        self.process._blendjob_session = os.name != "nt"
        atexit.register(self.close)

    def send(self, action, **parameters):
        json.dumps(parameters)
        self.start()
        sequence = self.sequence + 1
        identifier = uuid.uuid4().hex
        write_json(self.directory / f"command-{sequence:08d}.json",
                   {"id": identifier, "sequence": sequence, "action": action, "parameters": parameters})
        self.sequence = sequence
        return identifier

    def status(self, identifier):
        state = read_json(self.directory / f"status-{identifier}.json")
        if self.process.poll() is not None and state.get("state") not in TERMINAL_STATES:
            raise RuntimeError(f"BlendJob control process exited; log: {self.directory / 'control.log'}")
        return state or {"state": "running", "progress": 0.0, "message": "Starting task"}

    def cancel(self, identifier):
        if self.directory is not None:
            (self.directory / f"cancel-{identifier}").touch()

    def acknowledge(self, identifier, error=None):
        write_json(self.directory / f"commit-{identifier}.json", {"error": error})

    def close(self):
        process = self.process
        if process is None:
            return
        (self.directory / "shutdown").touch()
        # Teardown is bounded; normal UI operations only poll.
        try:
            process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            from .processes import terminate_process_tree
            terminate_process_tree(process)
            process.wait(timeout=4)
        finally:
            if process.poll() is not None:
                self.process = None
                self.log.close()
                self._temporary.cleanup()
                atexit.unregister(self.close)


class ProcessServer:
    """Expose nonblocking Blender commands and cached service state."""

    def __init__(self, runtime):
        self.runtime = runtime
        self.control = ControlProcess(runtime)
        self.snapshot = {"state": "STOPPED", "message": "Stopped"}
        self.pending_sequence = 0

    def action(self, action, **parameters):
        identifier = self.control.send(action, **parameters)
        return {"job_id": identifier, "directory": str(self.runtime.storage_root() / "jobs" / identifier)}

    def submit(self, job_type, parameters):
        return self.action("job", job_type=job_type, parameters=parameters)

    def status(self, job_id):
        return self.control.status(job_id)

    def cancel(self, job_id):
        self.control.cancel(job_id)

    def mark_job_complete(self, job_id):
        if job_id and self.control.process is not None:
            state = self.status(job_id)
            if state.get("state") not in TERMINAL_STATES:
                self.cancel(job_id)
            self.control.send("release", job_id=job_id)

    def enable(self):
        self.action("start")
        self.pending_sequence = self.control.sequence
        self.snapshot = {"state": "STARTING", "message": "Starting"}

    def stop(self):
        if self.control.process is not None:
            self.action("stop")
            self.pending_sequence = self.control.sequence
            self.snapshot = {"state": "STOPPING", "message": "Stopping"}
        else:
            self.snapshot = {"state": "STOPPED", "message": "Stopped"}

    def restart(self):
        self.action("restart")
        self.pending_sequence = self.control.sequence
        self.snapshot = {"state": "STARTING", "message": "Starting"}

    def poll(self, *, available=True):
        if self.control.process is not None:
            if self.control.process.poll() is not None:
                self.snapshot = {"state": "ERROR", "message": "Control process exited"}
            else:
                snapshot = read_json(self.control.directory / "server.json")
                if snapshot and snapshot.get("command_sequence", 0) >= self.pending_sequence:
                    self.snapshot = snapshot
        elif not available:
            self.snapshot = {"state": "UNAVAILABLE", "message": "Environment is not installed"}
        else:
            self.snapshot = {"state": "STOPPED", "message": "Stopped"}
        return 0.25 if self.snapshot.get("state") in {"STARTING", "STOPPING"} else 1.0

    def resource(self, name):
        return self.snapshot.get("resources", {}).get(name, {})

    def clear_resource(self, name):
        return self.action("clear_resource", name=name)

    def request_blocking(self, job_type, parameters, response=None):
        """Run a blocking request for standalone scripts."""
        submitted = self.submit(job_type, parameters)
        try:
            while True:
                state = self.status(submitted["job_id"])
                if state.get("state") in TERMINAL_STATES:
                    if state["state"] != "succeeded":
                        raise RuntimeError(state.get("error") or state.get("message"))
                    from .result import JobResult
                    result = JobResult._from_status({**submitted, **state})
                    if response:
                        response(result)
                    return result
                time.sleep(0.05)
        finally:
            self.mark_job_complete(submitted["job_id"])

    def detach(self):
        self.control.close()
        self.pending_sequence = 0
        self.snapshot = {"state": "STOPPED", "message": "Stopped"}


class ActionController:
    def __init__(self, server, action):
        self.server = server
        self.action = action

    def submit(self, _job_type, parameters):
        return self.server.action(self.action, **parameters)

    def status(self, job_id):
        return self.server.status(job_id)

    def cancel(self, job_id):
        self.server.cancel(job_id)

    def mark_job_complete(self, job_id):
        self.server.mark_job_complete(job_id)
