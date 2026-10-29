"""Run service communication and maintenance outside Blender."""
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .configuration import ServerConfiguration
from .control_maintenance import SetupOperation, MigrateOperation, CommitUncertain
from .processes import RegisteredServerProcess
from .storage import StorageLock, read_json, write_json


class ControlService:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.completed_sequence = 0
        self.states = {}
        self.server_state = None
        config = read_json(self.directory / "config.json")
        self.parent = RegisteredServerProcess(config.pop("parent_pid"))
        self.stopping = False
        self.root = Path(config.pop("storage_root"))
        self.config = ServerConfiguration(**config)
        self.server = self.config.server_controller(self.root, directory_lock=StorageLock())

    def cancelled(self, identifier):
        return (self.stopping or (self.directory / f"cancel-{identifier}").exists()
                or (self.directory / "shutdown").exists()
                or self.parent.poll() is not None)

    def publish(self, identifier, state):
        if self.states.get(identifier) == state:
            return
        write_json(self.directory / f"status-{identifier}.json", state)
        self.states[identifier] = state

    def release(self, identifier):
        for name in (f"status-{identifier}.json", f"cancel-{identifier}",
                     f"saving-{identifier}", f"commit-{identifier}.json"):
            (self.directory / name).unlink(missing_ok=True)
        self.states.pop(identifier, None)

    def execute(self, command):
        identifier, action, parameters = command["id"], command["action"], command["parameters"]
        try:
            if self.cancelled(identifier):
                raise RuntimeError("Operation cancelled")
            self.publish(identifier, {"state": "running", "progress": 0.0, "message": "Starting job"})
            if action == "job":
                result = self.job(identifier, parameters)
            elif action in {"setup", "migrate"}:
                result = self.maintenance(identifier, action, parameters)
            elif action == "start":
                self.server.enable()
                result = {}
            elif action == "stop":
                self.server.stop()
                result = {}
            elif action == "restart":
                self.server.restart()
                result = {}
            elif action == "clear_resource":
                result = self.server.clear_resource(parameters["name"])
            elif action in {"preview_files", "clear_files"}:
                connection = self.server.ensure()
                if action == "preview_files":
                    result = {**connection.client.preview_job_files(), "instance_id": connection.instance_id}
                else:
                    if connection.instance_id != parameters["instance_id"]:
                        raise RuntimeError("Job Server changed; preview cleanup again")
                    result = connection.client.clear_job_files(parameters["jobs"])
            else:
                raise ValueError(f"Unknown control action: {action}")
            if result is not None:
                self.publish(identifier, {"state": "succeeded", "progress": 1.0, "result": result})
        except Exception as error:
            self.publish(identifier, {"state": "cancelled" if self.cancelled(identifier) else "failed",
                                      "error": str(error), "message": str(error)})
            if action in {"start", "stop", "restart"}:
                self.server.snapshot = {"state": "ERROR", "message": str(error)}
        finally:
            self.completed_sequence = command["sequence"]
            self.publish_server()

    def publish_server(self):
        state = {**self.server.snapshot, "command_sequence": self.completed_sequence}
        if state != self.server_state:
            write_json(self.directory / "server.json", state)
            self.server_state = state

    def poll_server(self):
        interval = self.server.poll(available=self.config.environment_ready(self.root))
        self.publish_server()
        return interval

    def job(self, identifier, parameters):
        def progress(status):
            self.publish(identifier, status)
            self.publish_server()

        self.server.run_job(parameters["job_type"], parameters["parameters"], job_id=identifier,
                            cancel_check=lambda: self.cancelled(identifier), progress=progress)

    def maintenance(self, identifier, action, parameters):
        source, target = Path(parameters["source"]), Path(parameters["target"])
        if self.root != source:
            raise RuntimeError("Server location changed; start maintenance again")

        def save_location(path):
            self.publish(identifier, {"state": "awaiting_save", "progress": 0.9,
                                      "message": "Saving Server location"})
            reply_path = self.directory / f"commit-{identifier}.json"
            while not reply_path.exists():
                if self.cancelled(identifier):
                    if (self.directory / f"saving-{identifier}").exists():
                        raise CommitUncertain("Settings commit was interrupted; both Server locations were preserved")
                    raise RuntimeError("Operation cancelled")
                time.sleep(0.05)
            reply = read_json(reply_path)
            if reply.get("error"):
                raise RuntimeError(reply["error"])
            self.root = Path(path)

        callbacks = {"progress": lambda state: self.publish(identifier, state),
                     "cancel_check": lambda: self.cancelled(identifier),
                     "stop_server": self.server.stop, "save_location": save_location}
        operation = (SetupOperation(self.config, source, target,
                                    mirror=parameters["mirror"], online=parameters["online"], **callbacks)
                     if action == "setup" else MigrateOperation(self.config, source, target, **callbacks))
        state = operation.run()
        if state["state"] == "succeeded":
            self.server = operation.server
            if self.server.directory_lock is None:
                self.server.directory_lock = StorageLock()
        elif self.root != source:
            self.server = self.config.server_controller(self.root, directory_lock=StorageLock())
        self.publish(identifier, state)

    def run(self):
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="BlendJobControl")
        pending = {}
        released = set()
        poll = None
        next_poll = 0
        sequence = 1
        idle_delay = 0.05
        try:
            while not (self.directory / "shutdown").exists() and self.parent.poll() is None:
                while True:
                    path = self.directory / f"command-{sequence:08d}.json"
                    if not path.is_file():
                        break
                    command = read_json(path)
                    if not command:
                        break
                    path.unlink()
                    sequence += 1
                    idle_delay = 0.05
                    if command["action"] == "release":
                        released.add(command["parameters"]["job_id"])
                        continue
                    if command["action"] in {"start", "stop", "restart"}:
                        released.add(command["id"])
                    if command["action"] == "stop":
                        for identifier in pending:
                            (self.directory / f"cancel-{identifier}").touch()
                    pending[command["id"]] = executor.submit(self.execute, command)
                for identifier, future in tuple(pending.items()):
                    if future.done():
                        future.result()
                        del pending[identifier]
                        next_poll = min(next_poll, time.monotonic() + 1)
                for identifier in released.difference(pending):
                    # A terminal status can be read before the worker finishes publishing.
                    self.release(identifier)
                    released.remove(identifier)
                if poll is not None and poll.done():
                    next_poll = time.monotonic() + poll.result()
                    poll = None
                if not pending and poll is None and time.monotonic() >= next_poll:
                    poll = executor.submit(self.poll_server)
                time.sleep(0.05 if pending else idle_delay)
                idle_delay = 0.05 if pending else min(0.25, idle_delay * 2)
        finally:
            self.stopping = True
            try:
                executor.shutdown(wait=True, cancel_futures=True)
            finally:
                self.server.stop()


def main():
    ControlService(sys.argv[1]).run()
