import json
import threading
from dataclasses import dataclass
from pathlib import Path

from .client import JobResult


@dataclass
class JobOperatorState:
    """Internal state for one modal Job Operator invocation."""

    runtime: object
    operator: object
    controller: object
    job_id: str = ""
    directory: Path | None = None
    started: bool = False
    cancelled: bool = False
    start_error: str = ""
    start_thread: threading.Thread | None = None
    progress_stage: int | None = None
    progress: float = 0.0


def operator_properties(operator):
    """Return every custom Blender Property declared by an Operator class."""
    names = []
    for class_type in reversed(type(operator).__mro__):
        for name in getattr(class_type, "__annotations__", {}):
            if name not in names:
                names.append(name)
    parameters = {
        name: _json_value(getattr(operator, name))
        for name in names
        if hasattr(operator, name)
    }
    json.dumps(parameters)
    return parameters


def _json_value(value):
    if value is None or isinstance(value, (bool, float, int, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    try:
        return [_json_value(item) for item in value]
    except TypeError:
        return value


class JobOperatorBase:
    """Blender mixin that turns one Operator into a complete remote Job."""

    bl_options = {"INTERNAL"}
    poll_interval = 0.25
    starting_message = "Starting task"
    job_runtime = None
    job_type = ""

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        annotations = {}
        for base in reversed(cls.__mro__[1:]):
            annotations.update(getattr(base, "__annotations__", {}))
        annotations.update(cls.__dict__.get("__annotations__", {}))
        if annotations:
            cls.__annotations__ = annotations

    def request(self, _context):
        """Return the complete JSON parameters submitted to the Server."""
        return operator_properties(self)

    def response(self, _context, _result):
        """Apply an optional successful result on Blender's main thread."""

    def cleanup(self):
        """Release invocation resources after every terminal outcome."""

    def controller(self, runtime):
        """Return the operation controller used by this invocation."""
        return runtime.server

    def execute(self, context):
        runtime = self._runtime()
        if runtime.active_job is not None:
            self.report({"WARNING"}, "Another task is running")
            return {"CANCELLED"}
        if not self.job_type:
            self.report({"ERROR"}, "Set job_type on the Job Operator")
            return {"CANCELLED"}
        try:
            parameters = self.request(context)
            if not isinstance(parameters, dict):
                raise TypeError("request(context) must return a dict")
            json.dumps(parameters)
            controller = self.controller(runtime)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}

        job = JobOperatorState(runtime, self, controller)
        runtime.begin_job(job)
        if hasattr(context.window_manager, "progress_begin"):
            context.window_manager.progress_begin(0, 100)
        runtime.update_ui(context, 0.0, self.starting_message)
        self._timer = context.window_manager.event_timer_add(
            self.poll_interval,
            window=context.window,
        )
        context.window_manager.modal_handler_add(self)
        runtime.redraw_ui(context, force=True)
        job.start_thread = threading.Thread(
            target=self._submit_job,
            args=(job, parameters),
            name="BlendJobSubmit",
            daemon=True,
        )
        job.start_thread.start()
        return {"RUNNING_MODAL"}

    def _submit_job(self, job, parameters):
        if job.cancelled:
            job.started = True
            return
        try:
            submitted = job.controller.submit(self.job_type, parameters)
            job.job_id = submitted["job_id"]
            job.directory = Path(submitted["directory"])
            job.started = True
            if job.cancelled:
                job.controller.cancel(job.job_id)
        except (KeyError, OSError, RuntimeError, TypeError) as error:
            job.start_error = str(error)

    def modal(self, context, event):
        runtime = self._runtime()
        if event.type == "ESC":
            runtime.cancel_active()
        if event.type != "TIMER":
            return {"PASS_THROUGH"}

        job = runtime.active_job
        if job is None:
            self.remove_job_timer(context)
            return {"CANCELLED"}
        if job.start_error:
            self.report({"ERROR"}, f"Unable to start task: {job.start_error}")
            return self._close(context, job, "Task failed to start", cancelled=True)
        if not job.started:
            return {"RUNNING_MODAL"}
        if job.cancelled and not job.job_id:
            return self._close(context, job, "Task cancelled", cancelled=True)

        try:
            status = job.controller.status(job.job_id)
        except RuntimeError as error:
            return self._finish_failure(context, job, error)
        self.update_from_job_status(context, job, status)
        state = status.get("state")
        if state == "succeeded":
            return self._finish_success(context, job, status)
        if state in {"failed", "cancelled"}:
            if job.cancelled or state == "cancelled":
                return self._close(context, job, "Task cancelled", cancelled=True)
            return self._finish_failure(
                context,
                job,
                status.get("error") or "Server task failed",
            )
        return {"RUNNING_MODAL"}

    def _finish_success(self, context, job, status):
        try:
            complete_status = {
                "job_id": job.job_id,
                "directory": str(job.directory),
                **status,
            }
            message = self.response(
                context,
                JobResult.from_status(complete_status),
            )
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            return self._finish_failure(context, job, error)
        return self._close(
            context,
            job,
            message or status.get("message") or "Task complete",
            cancelled=False,
        )

    def _finish_failure(self, context, job, error):
        self.report({"ERROR"}, str(error))
        return self._close(context, job, "Task failed", cancelled=True)

    def _close(self, context, job, message, *, cancelled):
        self.remove_job_timer(context)
        if hasattr(context.window_manager, "progress_end"):
            context.window_manager.progress_end()
        job.runtime.finish_job(job)
        job.runtime.update_ui(context, 0.0 if cancelled else 1.0, message)
        job.runtime.redraw_ui(context, force=True)
        return {"CANCELLED"} if cancelled else {"FINISHED"}

    def update_from_job_status(self, context, job, status):
        reported = min(max(float(status.get("progress", job.progress)), 0.0), 1.0)
        stage = status.get("stage")
        if stage is not None and stage != job.progress_stage:
            job.progress_stage = stage
            job.progress = reported
        else:
            job.progress = max(job.progress, reported)
        message = self.format_job_status(status, self.starting_message)
        job.runtime.update_ui(context, job.progress, message)
        job.runtime.redraw_ui(context, force=True)

    def format_job_status(self, status, fallback):
        message = status.get("message")
        stage = status.get("stage")
        stages = status.get("stages")
        stage_label = status.get("stage_label")
        if stage and stages:
            prefix = f"{stage}/{stages}"
            normalized = str(message or "").strip()
            if normalized and normalized.lower() != str(stage_label or "").lower():
                return f"{prefix} {normalized}"
            return f"{prefix} {stage_label}" if stage_label else prefix
        return str(message or stage_label or fallback).strip()

    def remove_job_timer(self, context):
        timer = getattr(self, "_timer", None)
        if timer is None:
            return
        context.window_manager.event_timer_remove(timer)
        self._timer = None

    def cancel(self, context):
        runtime = self._runtime()
        job = runtime.active_job
        runtime.cancel_active()
        self.remove_job_timer(context)
        if job is None:
            return
        if hasattr(context.window_manager, "progress_end"):
            context.window_manager.progress_end()
        runtime.finish_job(job)
        runtime.update_ui(context, 0.0, "Task cancelled")
        runtime.redraw_ui(context, force=True)

    def _runtime(self):
        if self.job_runtime is None:
            raise RuntimeError("Use JobRuntime.JobOperatorBase")
        return self.job_runtime
