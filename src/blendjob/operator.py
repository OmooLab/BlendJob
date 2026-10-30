import json
from contextlib import contextmanager


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


class JobOperator:
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
        """Apply the result on Blender's main thread and optionally return an INFO message."""

    def cleanup(self):
        """Release invocation resources after every terminal outcome."""

    def execute(self, context):
        self._cleaned = False
        self._job = None
        self._window_manager = getattr(context, "window_manager", None)
        self._window = getattr(context, "window", None)
        self._area = getattr(context, "area", None)
        self._region = getattr(context, "region", None)
        try:
            parameters = self.request(context)
            if not isinstance(parameters, dict):
                raise TypeError("request(context) must return a dict")
            self._job = self._runtime().submit(
                self.job_type, parameters,
                on_done=lambda job: self._complete(context, job),
            )
            self._job.message = self.starting_message
            self._timer = context.window_manager.event_timer_add(self.poll_interval, window=context.window)
            context.window_manager.modal_handler_add(self)
        except Exception as error:
            if self._job is not None:
                self._job.cancel()
            else:
                self._cleanup_once()
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        job = getattr(self, "_job", None)
        if job is None or job.done:
            self._remove_job_timer()
            return {"FINISHED"} if job is not None and job.state == "succeeded" else {"CANCELLED"}
        if event.type == "ESC":
            job.cancel()
            return {"RUNNING_MODAL"}
        return {"PASS_THROUGH"}

    @contextmanager
    def _response_context(self, context):
        if self._window is None:
            yield context
            return
        import bpy
        if self._window not in bpy.context.window_manager.windows[:]:
            raise RuntimeError("The task's original window is no longer available")
        override = {"window": self._window}
        if self._area is not None:
            if self._area not in self._window.screen.areas[:]:
                raise RuntimeError("The task's original area is no longer available")
            override["area"] = self._area
        if self._region is not None:
            if self._area is None or self._region not in self._area.regions[:]:
                raise RuntimeError("The task's original region is no longer available")
            override["region"] = self._region
        with bpy.context.temp_override(**override):
            yield bpy.context

    def _complete(self, context, job):
        try:
            if job.state == "succeeded":
                with self._response_context(context) as response_context:
                    message = self.response(response_context, job.result)
                if message:
                    self.report({"INFO"}, message)
            elif job.state == "failed":
                self.report({"ERROR"}, job.error or "Server job failed")
        except Exception as error:
            self.report({"ERROR"}, str(error))
            raise
        finally:
            try:
                self._cleanup_once()
            finally:
                # A live window needs its next modal event to finish the handler.
                if self._window is None or self._runtime()._closing:
                    self._remove_job_timer()
                else:
                    import bpy
                    if self._window not in bpy.context.window_manager.windows[:]:
                        self._remove_job_timer()

    def _cleanup_once(self):
        if not self._cleaned:
            self._cleaned = True
            self.cleanup()

    def _remove_job_timer(self):
        timer = getattr(self, "_timer", None)
        if timer is not None:
            self._timer = None
            try:
                self._window_manager.event_timer_remove(timer)
            except (ReferenceError, RuntimeError):
                pass

    def cancel(self, context):
        job = getattr(self, "_job", None)
        if job is not None:
            job.cancel()

    def _runtime(self):
        if self.job_runtime is None:
            raise RuntimeError("Use runtime.JobOperator")
        return self.job_runtime
