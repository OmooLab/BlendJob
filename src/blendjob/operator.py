import json
from dataclasses import dataclass
from pathlib import Path

from .result import JobResult


@dataclass
class JobOperatorState:
    """Internal state for one modal Job Operator invocation."""

    runtime: object
    operator: object
    controller: object
    job_id: str = ""
    directory: Path | None = None
    cancelled: bool = False
    progress_job_id: str = ""
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
    starting_message = "Starting job"
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
            self.report({"WARNING"}, "Another job is running")
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
        try:
            submitted = controller.submit(self.job_type, parameters)
            job.job_id = submitted["job_id"]
            job.directory = Path(submitted["directory"])
        except (KeyError, OSError, RuntimeError, TypeError) as error:
            return self._finish_failure(context, job, error)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        runtime = self._runtime()
        job = runtime.active_job
        if job is None or job.operator is not self:
            self._remove_job_timer(context)
            return {"CANCELLED"}
        if event.type == "ESC":
            runtime.cancel_active()
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        if job.cancelled and not job.job_id:
            return self._finish_cancelled(context, job)

        try:
            status = job.controller.status(job.job_id)
        except RuntimeError as error:
            return self._finish_failure(context, job, error)
        state = status.get("state")
        if state == "succeeded":
            return self._finish_success(context, job, status)
        if state in {"failed", "cancelled"}:
            if job.cancelled or state == "cancelled":
                return self._finish_cancelled(context, job)
            return self._finish_failure(
                context,
                job,
                status.get("error") or "Server job failed",
            )
        self._update_from_job_status(context, job, status)
        return {"RUNNING_MODAL"}

    def _finish_success(self, context, job, status):
        try:
            self._end_job(context, job, status.get("message") or "Job complete", cancelled=False)
            result = JobResult._from_status({
                "job_id": job.job_id,
                "directory": str(job.directory),
                **status,
            })
            self.response(context, result)
            return {"FINISHED"}
        finally:
            self.cleanup()

    def _finish_failure(self, context, job, error):
        try:
            return self._finish_cancelled(context, job, "Job failed")
        finally:
            self.report({"ERROR"}, str(error))

    def _finish_cancelled(self, context, job, message="Job cancelled"):
        try:
            self._end_job(context, job, message, cancelled=True)
            return {"CANCELLED"}
        finally:
            self.cleanup()

    def _end_job(self, context, job, message, *, cancelled):
        job.runtime.finish_job(job)
        try:
            self._remove_job_timer(context)
        finally:
            if hasattr(context.window_manager, "progress_end"):
                context.window_manager.progress_end()
        job.runtime.update_ui(context, 0.0 if cancelled else 1.0, message)

    def _update_from_job_status(self, context, job, status):
        reported = min(max(float(status.get("progress", job.progress)), 0.0), 1.0)
        progress_job_id = str(status.get("job_id") or job.job_id)
        if progress_job_id != job.progress_job_id:
            job.progress_job_id = progress_job_id
            job.progress = reported
        else:
            job.progress = max(job.progress, reported)
        message = str(status.get("message") or self.starting_message).strip()
        job.runtime.update_ui(context, job.progress, message)

    def _remove_job_timer(self, context):
        timer = getattr(self, "_timer", None)
        if timer is None:
            return
        self._timer = None
        context.window_manager.event_timer_remove(timer)

    def cancel(self, context):
        runtime = self._runtime()
        job = runtime.active_job
        if job is None or job.operator is not self:
            self._remove_job_timer(context)
            return
        runtime.cancel_active()
        self._finish_cancelled(context, job)

    def _runtime(self):
        if self.job_runtime is None:
            raise RuntimeError("Use JobRuntime.JobOperatorBase")
        return self.job_runtime
