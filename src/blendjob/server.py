import os
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path


TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled"})


class JobCancelled(RuntimeError):
    """Raised by a handler when its job has been cancelled."""


class JobContext:
    """Mutable state and cancellation API passed to a registered job handler."""

    def __init__(
        self,
        job_id,
        job_type,
        parameters,
        resources,
        storage_root,
        directory,
    ):
        self.job_id = job_id
        self.job_type = job_type
        self.parameters = parameters
        self.storage_root = Path(storage_root)
        self.directory = Path(directory)
        self._resources = resources
        self._lock = threading.Lock()
        self._cancel_requested = False
        self._status = {
            "job_id": job_id,
            "job_type": job_type,
            "state": "queued",
            "progress": 0.0,
            "message": "Task queued",
            "directory": str(self.directory),
        }

    def _update(self, progress, message, error=None, state=None, **details):
        if state is None:
            if error:
                state = "failed"
            elif progress >= 1.0:
                state = "succeeded"
            else:
                state = "running"
        snapshot = {
            "job_id": self.job_id,
            "job_type": self.job_type,
            "state": state,
            "progress": min(max(float(progress), 0.0), 1.0),
            "message": str(message),
            "directory": str(self.directory),
            **{key: value for key, value in details.items() if value is not None},
        }
        if error:
            snapshot["error"] = str(error)
        with self._lock:
            self._status = snapshot

    def progress(self, progress, message, **details):
        """Publish running progress from a Job handler."""
        self._update(progress, message, state="running", **details)

    def succeed(self, result=None, message="Task complete"):
        """Publish a successful terminal result."""
        details = {"result": result} if result is not None else {}
        self._update(1.0, message, state="succeeded", **details)

    def snapshot(self):
        with self._lock:
            return dict(self._status)

    def request_cancel(self):
        with self._lock:
            if self._status["state"] in TERMINAL_STATES:
                return False
            self._cancel_requested = True
            self._status = {
                **self._status,
                "state": "cancelling",
                "message": "Cancelling task",
            }
            return True

    def is_cancelled(self):
        with self._lock:
            return self._cancel_requested

    def check_cancelled(self):
        if self.is_cancelled():
            raise JobCancelled("Task cancelled")

    def resource(self, name):
        """Return a shared resource registered on the owning JobServer."""
        try:
            return self._resources[name]
        except KeyError:
            raise KeyError(f"Unknown Server Resource: {name}") from None


class JobServer:
    """Small FastAPI wrapper with decorator-based job registration."""

    def __init__(
        self,
        name,
        *,
        storage_root=None,
        max_job_history=100,
    ):
        self.name = name
        self.max_job_history = max(int(max_job_history), 0)
        self.handlers = {}
        self.resources = {}
        self.resource_factories = {}
        self.jobs = {}
        self.futures = {}
        self.active_job_id = None
        self.lock = threading.Lock()
        self.executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="BlendJobWorker",
        )
        self.closed = False
        self.shutdown_event = threading.Event()
        self.started_at = time.time()
        self.storage_root = Path(storage_root) if storage_root is not None else None
        self.jobs_directory = (
            self.storage_root / "jobs" if self.storage_root is not None else None
        )

    def bind(self, storage_root):
        """Bind the Server to the Runtime-owned Storage Root."""
        storage_root = Path(storage_root).resolve()
        if self.storage_root is not None:
            current = self.storage_root.resolve()
            if current != storage_root:
                raise RuntimeError(
                    f"Job Server is already bound to another Storage Root: {current}"
                )
        self.storage_root = storage_root
        self.jobs_directory = storage_root / "jobs"
        for name, factory in tuple(self.resource_factories.items()):
            if name not in self.resources:
                self.resources[name] = factory(self)
        return self

    def add_resource(self, name, resource):
        """Register shared state owned for the full Server lifetime."""
        if not name or "/" in name:
            raise ValueError(f"Invalid resource name: {name}")
        with self.lock:
            if name in self.resources:
                raise ValueError(f"Resource is already registered: {name}")
            self.resources[name] = resource
        return resource

    def resource(self, name):
        """Register a Resource factory initialized after Storage Root binding."""
        def register(factory):
            if not name or "/" in name:
                raise ValueError(f"Invalid resource name: {name}")
            if name in self.resources or name in self.resource_factories:
                raise ValueError(f"Resource is already registered: {name}")
            self.resource_factories[name] = factory
            if self.storage_root is not None:
                self.resources[name] = factory(self)
            return factory

        return register

    def resource_snapshot(self, name):
        try:
            resource = self.resources[name]
        except KeyError:
            raise KeyError(name) from None
        snapshot = getattr(resource, "snapshot", None)
        return snapshot() if snapshot is not None else {}

    def resource_snapshots(self):
        return {
            name: self.resource_snapshot(name)
            for name in tuple(self.resources)
        }

    def clear_resource(self, name):
        """Clear an idle Server Resource without racing a new Job submission."""
        with self.lock:
            if self.active_job_id is not None or self._queued_job_count():
                return False
            try:
                resource = self.resources[name]
            except KeyError:
                raise KeyError(name) from None
            clear = getattr(resource, "clear", None)
            if clear is None:
                raise TypeError(f"Resource cannot be cleared: {name}")
            clear()
            return True

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            self.shutdown_event.set()
        self.executor.shutdown(wait=True, cancel_futures=True)
        for resource in reversed(tuple(self.resources.values())):
            close = getattr(resource, "close", None)
            if close is not None:
                close()

    def job(self, job_type):
        """Register ``handler(context, parameters)`` for a public job type."""
        def register(handler):
            if job_type in self.handlers:
                raise ValueError(f"Job type is already registered: {job_type}")
            self.handlers[job_type] = handler
            return handler

        return register

    def snapshot(self, instance_id):
        with self.lock:
            active = self.jobs.get(self.active_job_id)
            queued_jobs = self._queued_job_count()
            result = {
                "ready": True,
                "server": self.name,
                "instance_id": instance_id,
                "busy": active is not None or queued_jobs > 0,
                "active_job": active.snapshot() if active else None,
                "queued_jobs": queued_jobs,
                "started_at": self.started_at,
                "resources": self.resource_snapshots(),
            }
        return result

    def submit(self, job_type, parameters, *, job_id=None):
        if self.storage_root is None or self.jobs_directory is None:
            raise RuntimeError("Bind the Job Server to a Storage Root before submitting")
        job_id = job_id or uuid.uuid4().hex
        if job_type not in self.handlers:
            raise KeyError(job_type)
        if not job_id or Path(job_id).name != job_id or job_id in {".", ".."}:
            raise ValueError(f"Invalid job id: {job_id}")
        with self.lock:
            if self.closed:
                raise RuntimeError("Job Server is closed")
            if job_id in self.jobs:
                raise ValueError(f"Job id already exists: {job_id}")
            self.jobs_directory.mkdir(parents=True, exist_ok=True)
            directory = self.jobs_directory / job_id
            try:
                directory.mkdir()
            except FileExistsError:
                raise ValueError(f"Job directory already exists: {job_id}") from None
            context = JobContext(
                job_id,
                job_type,
                parameters,
                self.resources,
                self.storage_root,
                directory,
            )
            self.jobs[job_id] = context
            self._prune_jobs()
            self.futures[job_id] = self.executor.submit(self.run, context)
        return context

    def cancel(self, job_id):
        with self.lock:
            context = self.jobs.get(job_id)
            future = self.futures.get(job_id)
            if context is None:
                return None
            if future is not None and future.cancel():
                context.request_cancel()
                context._update(1.0, "Task cancelled", state="cancelled")
                self.futures.pop(job_id, None)
                return context
        return context if context.request_cancel() else None

    def _queued_job_count(self):
        return sum(
            context.snapshot()["state"] == "queued"
            for context in self.jobs.values()
        )

    def _prune_jobs(self):
        completed = [
            identifier
            for identifier, context in self.jobs.items()
            if identifier != self.active_job_id
            and context.snapshot()["state"] in TERMINAL_STATES
        ]
        excess = len(completed) - self.max_job_history
        for identifier in completed[:max(excess, 0)]:
            self.jobs.pop(identifier, None)

    def run(self, context):
        with self.lock:
            if context.snapshot()["state"] in TERMINAL_STATES:
                return
            self.active_job_id = context.job_id
        context._update(0.0, "Server accepted the task", state="running")
        try:
            context.check_cancelled()
            result = self.handlers[context.job_type](
                context,
                context.parameters,
            )
            context.check_cancelled()
            snapshot = context.snapshot()
            if snapshot["state"] not in {"failed", "cancelled"}:
                context.succeed(
                    result,
                    message=snapshot.get("message") or "Task complete",
                )
        except JobCancelled:
            context._update(1.0, "Task cancelled", state="cancelled")
        except Exception as error:
            traceback.print_exc()
            context._update(1.0, "Task failed", error=error, state="failed")
        finally:
            with self.lock:
                if self.active_job_id == context.job_id:
                    self.active_job_id = None
                self.futures.pop(context.job_id, None)

    def create_app(self, instance_id, storage_root=None):
        from fastapi import FastAPI, HTTPException

        if storage_root is not None:
            self.bind(storage_root)
        elif self.storage_root is None:
            raise RuntimeError("Storage Root is required to create the Job Server app")

        @asynccontextmanager
        async def lifespan(_app):
            yield
            self.close()

        app = FastAPI(
            title=self.name,
            docs_url=None,
            redoc_url=None,
            openapi_url=None,
            lifespan=lifespan,
        )
        app.state.job_server = self

        @app.get("/health")
        def health():
            return self.snapshot(instance_id)

        @app.get("/resources")
        def resources():
            return self.resource_snapshots()

        @app.get("/resources/{resource_name}")
        def resource_status(
            resource_name: str,
        ):
            try:
                return self.resource_snapshot(resource_name)
            except KeyError:
                raise HTTPException(status_code=404, detail="Resource was not found")

        @app.post("/resources/{resource_name}/clear")
        def clear_resource(
            resource_name: str,
        ):
            try:
                cleared = self.clear_resource(resource_name)
            except KeyError:
                raise HTTPException(status_code=404, detail="Resource was not found")
            except TypeError as error:
                raise HTTPException(status_code=405, detail=str(error))
            if not cleared:
                raise HTTPException(status_code=409, detail="Server is busy")
            return {"resource": resource_name, "state": "cleared"}

        @app.post("/jobs", status_code=202)
        def submit_job(payload: dict):
            job_type = str(payload.get("job_type", payload.get("command", "")))
            parameters = payload.get("parameters")
            if not job_type or not isinstance(parameters, dict):
                raise HTTPException(status_code=422, detail="Invalid job request")
            try:
                context = self.submit(job_type, parameters)
            except KeyError:
                raise HTTPException(status_code=422, detail="Unknown job type")
            except ValueError as error:
                raise HTTPException(status_code=409, detail=str(error))
            return context.snapshot()

        @app.get("/jobs/{job_id}")
        def job_status(job_id: str):
            context = self.jobs.get(job_id)
            if context is None:
                raise HTTPException(status_code=404, detail="Job was not found")
            return context.snapshot()

        @app.delete("/jobs/{job_id}", status_code=202)
        def cancel_job(job_id: str):
            context = self.cancel(job_id)
            if context is None:
                raise HTTPException(status_code=404, detail="Active job was not found")
            return context.snapshot()

        @app.post("/shutdown", status_code=202)
        def shutdown():
            self.shutdown_event.set()
            return {"state": "stopping"}

        return app


def watch_parent(parent_pid, shutdown_event, interval=1.0):
    """Stop a dedicated Server after its owning Blender process exits."""
    if not parent_pid:
        return
    while not shutdown_event.wait(interval):
        if not _process_exists(parent_pid):
            shutdown_event.set()
            return


def _process_exists(process_id):
    if os.name != "nt":
        try:
            os.kill(process_id, 0)
            return True
        except OSError:
            return False
    import ctypes

    synchronize = 0x00100000
    handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, process_id)
    if not handle:
        return False
    try:
        return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == 0x00000102
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)
