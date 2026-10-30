import json
import logging
from pathlib import Path

from .configuration import ServerConfiguration
from .process import ProcessServer
from .operator import JobOperator
from .operations import Operation, MAINTENANCE_MESSAGES
from .translations import TRANSLATIONS
from .jobs import Job, BUILTIN_OPERATIONS, TERMINAL_STATES, validate_job_name
from .result import JobResult


class JobRuntime:
    """Own one add-on's Storage Root, Environment, Server and Blender UI."""

    def __init__(
        self,
        server_entrypoint,
        *,
        entrypoint_root=None,
        storage_root,
        environment,
        namespace,
        post_setup=None,
    ):
        self._config = ServerConfiguration(server_entrypoint, environment, entrypoint_root=entrypoint_root)
        self.storage_root_setting = storage_root
        if post_setup is not None and not callable(post_setup):
            raise TypeError("post_setup must be callable")
        self.post_setup = post_setup
        if not isinstance(namespace, str) or not namespace.strip():
            raise ValueError("namespace must be a non-empty string")
        self.namespace = namespace.strip()
        self._translation_key = f"{__package__}.{self.namespace}"
        self.jobs = []
        self._operation = None
        self._ticking = False
        self._closing = False
        self._operator_classes = None
        self._registered = False
        self._status_bar_state = None

        def draw_status_bar(header, _context):
            item = self._status_bar_item()
            if item is not None:
                row = header.layout.row(align=True)
                row.ui_units_x = 20
                if isinstance(item, Job):
                    self._draw_job(row, item)
                else:
                    row.progress(factor=item.progress, type="BAR", text=item.text)

        self._status_bar_draw = draw_status_bar
        self.server = ProcessServer(self)

        class BoundJobOperator(JobOperator):
            job_runtime = self

        BoundJobOperator.__name__ = "JobOperator"
        self.JobOperator = BoundJobOperator

    def inspect_installation(self, root):
        """Inspect a storage directory against this runtime's environment."""
        return self._config.inspect_installation(root)

    def submit(self, name, parameters=None, *, on_done=None):
        """Accept a task and arrange its complete lifecycle."""
        validate_job_name(name)
        if self._closing:
            raise RuntimeError("The runtime is shutting down")
        if on_done is not None and not callable(on_done):
            raise TypeError("on_done must be callable")
        if parameters is None:
            parameters = {}
        if not isinstance(parameters, dict):
            raise TypeError("Job parameters must be a dict")
        parameters = json.loads(json.dumps(parameters, allow_nan=False))
        if name in BUILTIN_OPERATIONS:
            raise ValueError(f"Use runtime.{name}() for server operations")
        if not self.can_submit():
            raise RuntimeError(f"Cannot submit {name} while runtime is {self.state()}")
        self.enable()
        job = Job(self, name, parameters, on_done)
        self.jobs.append(job)
        if self._operation is None and not self._ticking:
            self._start_job(job)
        self.redraw_ui()
        return job

    def start(self, *, on_done=None):
        return self._begin_operation("start", {}, on_done)

    def stop(self, *, on_done=None):
        return self._begin_operation("stop", {}, on_done)

    def restart(self, *, on_done=None):
        return self._begin_operation("restart", {}, on_done)

    def setup(self, target=None, *, mirror=False, online=True, on_done=None):
        if target is not None and not str(target).strip():
            raise ValueError("Choose a storage target")
        target = self.storage_root() if target is None else Path(target).expanduser().resolve()
        self.inspect_installation(target)
        if not isinstance(mirror, bool) or not isinstance(online, bool):
            raise TypeError("mirror and online must be bools")
        try:
            import bpy
            online = online and bool(bpy.app.online_access)
        except ModuleNotFoundError:
            pass
        return self._begin_operation("setup", {
            "source": str(self.storage_root()), "target": str(target),
            "mirror": mirror, "online": online,
        }, on_done)

    def migrate(self, target, *, on_done=None):
        if target is None or not str(target).strip():
            raise ValueError("Choose a storage target")
        target = Path(target).expanduser().resolve()
        return self._begin_operation("migrate", {
            "source": str(self.storage_root()), "target": str(target),
        }, on_done)

    def clear_resource(self, name, *, on_done=None):
        if not isinstance(name, str) or not name:
            raise ValueError("Choose a resource name")
        return self._begin_operation("clear_resource", {"name": name}, on_done)

    def preview_files(self, *, on_done=None):
        return self._begin_operation("preview_files", {}, on_done)

    def clear_files(self, jobs, instance_id, *, on_done=None):
        if not isinstance(jobs, list) or not isinstance(instance_id, str):
            raise ValueError("File cleanup requires jobs and instance_id from a preview")
        return self._begin_operation("clear_files", {"jobs": jobs, "instance_id": instance_id}, on_done)

    def _begin_operation(self, name, parameters, on_done):
        if self._closing:
            raise RuntimeError("The runtime is shutting down")
        if on_done is not None and not callable(on_done):
            raise TypeError("on_done must be callable")
        parameters = json.loads(json.dumps(parameters, allow_nan=False))
        if not self._can_operate(name):
            raise RuntimeError(f"Cannot {name} while runtime is {self.state()}")
        if self._operation is not None:
            previous = self._operation
            if previous.identifier:
                self.server.cancel(previous.identifier)
            previous.state = "cancelled"
            self._finish_operation(previous)
        if self._operation is not None:
            raise RuntimeError("A server operation is already in progress")
        operation = Operation(name, parameters, on_done)
        self._operation = operation
        if name in {"stop", "restart", "migrate"}:
            self.cancel_jobs()
        self.enable()
        self.redraw_ui()
        return operation

    def _can_operate(self, name):
        if self._closing:
            return False
        if self._operation is not None:
            return self._operation.name == "start" and name == "stop"
        state = self.state()
        if name == "setup":
            return state == "UNAVAILABLE"
        if name == "start":
            return state in {"OFFLINE", "ERROR"}
        return state not in {"UNAVAILABLE", "INSTALLING", "MIGRATING", "STOPPING"}

    def _poll_operation(self):
        operation = self._operation
        if operation is None or (self.jobs and operation.name != "start"):
            return
        try:
            if not operation.identifier:
                operation.identifier = self.server.action(operation.name, **operation.parameters)
            status = self.server.status(operation.identifier)
            if status.get("state") == "awaiting_save":
                operation.save_location(self)
            operation.state = status.get("state", "running")
            operation.progress = status.get("progress", operation.progress)
            if operation.state != "queued":
                operation.message = status.get("message", "")
            operation.message_args = status.get("message_args", {})
            operation.phase = status.get("phase")
            if operation.state not in TERMINAL_STATES:
                return
            operation.result = status.get("result")
            operation.error = status.get("error", "")
        except Exception as error:
            operation.state, operation.error = "failed", str(error)
        self._finish_operation(operation)

    def _finish_operation(self, operation):
        self._release(operation.identifier)
        self._operation = None
        operation.done = True
        try:
            if operation.state == "succeeded" and operation.name == "setup" and self.post_setup and not self._closing:
                try:
                    self.post_setup(self)
                except Exception as error:
                    operation.state = "failed"
                    operation.error = f"Setup completed, but post_setup failed: {error}"
            if operation.on_done:
                operation.on_done(operation)
            elif operation.error:
                logging.getLogger(__name__).error("%s: %s", operation.name, operation.error)
        except Exception as error:
            operation.state, operation.error = "failed", str(error)
            logging.getLogger(__name__).exception("Operation completion failed")
        finally:
            operation.on_done = None

    def _start_job(self, job):
        try:
            submitted = self.server.submit(job.name, job.parameters)
            job._job_id = submitted["job_id"]
            job._directory = str(submitted["directory"])
        except Exception as error:
            job._status = {"state": "failed", "error": str(error)}

    def storage_root(self, path=None):
        """Read or save the configured location without creating its directory."""
        if path is not None:
            path = Path(path).expanduser().resolve()
        if callable(self.storage_root_setting):
            configured = self.storage_root_setting() if path is None else self.storage_root_setting(path)
        else:
            if path is not None:
                self.storage_root_setting = path
            configured = self.storage_root_setting
        return Path(configured).expanduser().resolve()

    def server_log_path(self):
        return self.storage_root() / "server.log"

    def operator_classes(self):
        if self._operator_classes is not None:
            return self._operator_classes
        import bpy

        runtime = self

        class OperationOperator:
            @classmethod
            def poll(cls, _context):
                return runtime._can_operate(cls.operation)

            def execute(self, context):
                self._operation = None
                self._timer = None
                self._window_manager = context.window_manager
                try:
                    parameters = self.request(context)
                    self._timer = context.window_manager.event_timer_add(0.25, window=context.window)
                    self._operation = getattr(runtime, self.operation)(**parameters, on_done=self._operation_finished)
                    if not context.window_manager.modal_handler_add(self):
                        raise RuntimeError("Unable to attach the operation to this window")
                except Exception as error:
                    self.cancel(context)
                    self.report({"ERROR"}, str(error))
                    return {"CANCELLED"}
                return {"RUNNING_MODAL"}

            def cancel(self, _context):
                """Release UI ownership; the runtime owns any accepted operation."""
                operation = getattr(self, "_operation", None)
                if operation is not None:
                    operation.on_done = None
                timer = getattr(self, "_timer", None)
                if timer is not None:
                    self._timer = None
                    try:
                        self._window_manager.event_timer_remove(timer)
                    except (ReferenceError, RuntimeError):
                        pass

            def _operation_finished(self, operation):
                if runtime._closing:
                    self.cancel(None)

            def modal(self, context, event):
                if not self._operation.done:
                    return {"PASS_THROUGH"}
                self.cancel(context)
                try:
                    if self._operation.state == "failed":
                        self.report({"ERROR"}, self._operation.error)
                        return {"CANCELLED"}
                    if self._operation.state == "succeeded":
                        message = self.response(context, self._operation.result or {})
                        if message:
                            self.report({"INFO"}, message)
                        return {"FINISHED"}
                except Exception as error:
                    self.report({"ERROR"}, str(error))
                return {"CANCELLED"}

            def request(self, _context):
                return {}

            def response(self, context, result):
                pass

        class SetupServer(OperationOperator, bpy.types.Operator):
            bl_idname = f"{runtime.namespace}.setup_server"
            bl_label = "Set Up Server"
            operation = "setup"

            directory: bpy.props.StringProperty(name="Server Location", subtype="DIR_PATH", options={"SKIP_SAVE"})
            use_mirror: bpy.props.BoolProperty(name="Use China Mirror", default=False)

            def invoke(self, context, _event):
                if not self.directory:
                    self.directory = str(runtime.storage_root())
                return context.window_manager.invoke_props_dialog(self, width=460)

            def draw(self, _context):
                self.layout.prop(self, "directory")
                self.layout.prop(self, "use_mirror")

            def request(self, _context):
                return {"target": self.directory or str(runtime.storage_root()),
                        "mirror": self.use_mirror, "online": bpy.app.online_access}

        class MigrateStorage(OperationOperator, bpy.types.Operator):
            bl_idname = f"{runtime.namespace}.migrate_storage"
            bl_label = "Migrate Storage"
            operation = "migrate"

            directory: bpy.props.StringProperty(subtype="DIR_PATH", options={"SKIP_SAVE"})
            filter_folder: bpy.props.BoolProperty(default=True, options={"HIDDEN"})

            def invoke(self, context, _event):
                if not self.directory:
                    self.directory = str(runtime.storage_root().parent)
                context.window_manager.fileselect_add(self)
                return {"RUNNING_MODAL"}

            def request(self, _context):
                if not self.directory.strip():
                    raise ValueError("Choose a migration target")
                selected = Path(self.directory).expanduser().resolve()
                name = runtime.storage_root().name
                target = next((path for path in (selected, *selected.parents)
                               if path.name.lower() == name.lower()), selected / name)
                return {"target": str(target)}

            def response(self, _context, result):
                warning = (result or {}).get("warning")
                if warning:
                    self.report({"WARNING"}, warning)
                    return
                return "Folder migration completed"

        class ClearJobFiles(OperationOperator, bpy.types.Operator):
            bl_idname = f"{runtime.namespace}.clear_job_files"
            bl_label = "Clear Job Files"
            bl_options = {"INTERNAL"}
            operation = "preview_files"

            def response(self, _context, result):
                if not result["jobs"]:
                    return "No unused job files to clear"
                # The modal preview finishes here; the dialog owns a separate operator.
                operator = getattr(getattr(bpy.ops, runtime.namespace), "confirm_clear_job_files")
                operator("INVOKE_DEFAULT", preview=json.dumps(result))

        class ConfirmClearJobFiles(OperationOperator, bpy.types.Operator):
            bl_idname = f"{runtime.namespace}.confirm_clear_job_files"
            bl_label = "Clear Job Files"
            bl_options = {"INTERNAL"}
            operation = "clear_files"

            preview: bpy.props.StringProperty(default="{}", options={"HIDDEN", "SKIP_SAVE"})

            def invoke(self, context, _event):
                if not json.loads(self.preview).get("jobs"):
                    return {"CANCELLED"}
                return context.window_manager.invoke_props_dialog(self, width=460, confirm_text="Delete Files")

            def draw(self, _context):
                preview = json.loads(self.preview)
                message = bpy.app.translations.pgettext_iface("Delete {count} jobs ({size} MB)?").format(
                    count=len(preview["jobs"]), size=f"{preview['bytes'] / 1_000_000:.1f}",
                )
                self.layout.label(text=message, translate=False)
                self.layout.label(text="Permanently delete job outputs and logs.")

            def request(self, _context):
                preview = json.loads(self.preview)
                if not preview.get("jobs") or not preview.get("instance_id"):
                    raise ValueError("Preview and confirm job file cleanup first")
                return {"jobs": preview["jobs"], "instance_id": preview["instance_id"]}

            def response(self, _context, result):
                kept = result["skipped"] + len(result["failed"])
                message = bpy.app.translations.pgettext_iface(
                    "Deleted {count} jobs ({size} MB); kept {kept}"
                ).format(count=len(result["jobs"]), size=f"{result['bytes'] / 1_000_000:.1f}", kept=kept)
                if kept:
                    self.report({"WARNING"}, message)
                    return
                return message

        class CancelJob(bpy.types.Operator):
            bl_idname = f"{runtime.namespace}.cancel_job"
            bl_label = "Cancel Job"

            job_id: bpy.props.StringProperty(options={"SKIP_SAVE"})

            def execute(self, _context):
                for job in runtime.jobs:
                    if job.id == self.job_id:
                        runtime.cancel_job(job)
                        break
                return {"FINISHED"}

        class ServerControlOperator(OperationOperator):
            @classmethod
            def poll(cls, _context):
                return super().poll(_context) and runtime.state() in cls.allowed_states

        class StartServer(ServerControlOperator, bpy.types.Operator):
            allowed_states = {"OFFLINE", "ERROR"}
            bl_idname = f"{runtime.namespace}.start_server"
            bl_label = "Start Server"
            operation = "start"

        class StopServer(ServerControlOperator, bpy.types.Operator):
            allowed_states = {"STARTING", "IDLE", "PROCESSING", "ERROR"}
            bl_idname = f"{runtime.namespace}.stop_server"
            bl_label = "Stop Server"
            operation = "stop"

        class RestartServer(ServerControlOperator, bpy.types.Operator):
            allowed_states = {"IDLE", "PROCESSING", "ERROR"}
            bl_idname = f"{runtime.namespace}.restart_server"
            bl_label = "Restart Server"
            operation = "restart"

        class OpenServerLog(bpy.types.Operator):
            bl_idname = f"{runtime.namespace}.open_server_log"
            bl_label = "Open Server Log"

            def execute(self, _context):
                path = runtime.server_log_path()
                target = path if path.is_file() else path.parent
                result = bpy.ops.wm.path_open(filepath=str(target))
                if "FINISHED" not in result:
                    self.report({"ERROR"}, "Unable to open the Server log")
                    return {"CANCELLED"}
                return {"FINISHED"}

        self._operator_classes = (
            SetupServer,
            MigrateStorage,
            ClearJobFiles,
            ConfirmClearJobFiles,
            CancelJob,
            StartServer,
            StopServer,
            RestartServer,
            OpenServerLog,
        )
        return self._operator_classes

    def register(self):
        if self._registered:
            return
        import bpy

        for class_type in self.operator_classes():
            bpy.utils.register_class(class_type)
        bpy.app.translations.register(self._translation_key, TRANSLATIONS)
        bpy.types.STATUSBAR_HT_header.append(self._status_bar_draw)
        self._registered = True
        self.enable()

    def unregister(self):
        if not self._registered:
            return
        import bpy

        self.disable()
        bpy.types.STATUSBAR_HT_header.remove(self._status_bar_draw)
        self._redraw_status_bars()
        for class_type in reversed(self.operator_classes()):
            bpy.utils.unregister_class(class_type)
        bpy.app.translations.unregister(self._translation_key)
        self._registered = False

    def _finish_job(self, job, status):
        if job.done or job._finishing:
            return
        job._finishing = True
        job.state = "cancelled" if job.cancelled else status["state"]
        job.error = str(status.get("error") or "")
        try:
            if job.state == "succeeded":
                job.result = JobResult._from_status({
                    **status, "directory": status.get("directory") or job._directory,
                    "job_id": status.get("job_id") or job._job_id,
                })
                job.progress = 1.0
            if job._on_done is not None:
                job._on_done(job)
            elif job.state == "failed":
                logging.getLogger(__name__).error("%s: %s", job.name, job.error)
        except Exception as error:
            job.state, job.error, job.result = "failed", str(error), None
            logging.getLogger(__name__).exception("Job completion failed: %s", job.name)
        finally:
            self._release(job._job_id)
            job._on_done = None
            if job in self.jobs:
                self.jobs.remove(job)
            job.done = True

    def _release(self, identifier):
        try:
            if identifier:
                self.server.mark_job_complete(identifier)
        except Exception as error:
            logging.getLogger(__name__).warning("Could not release %s: %s", identifier, error)

    def cancel_job(self, job):
        if not job.cancellable or job.cancelled:
            return
        job.cancelled = True
        if job._job_id:
            try:
                self.server.cancel(job._job_id)
            except (OSError, RuntimeError) as error:
                logging.getLogger(__name__).warning("Could not request cancellation of %s: %s", job.name, error)
        self.redraw_ui()

    def cancel_jobs(self):
        for job in tuple(self.jobs):
            self.cancel_job(job)

    def _tick(self):
        if self._ticking or self._closing:
            return
        self._ticking = True
        try:
            try:
                self.server.poll(available=self._config.environment_ready(self.storage_root()))
            except (OSError, RuntimeError) as error:
                self.server.snapshot = {"state": "ERROR", "message": str(error)}
                if self._operation is not None:
                    self._operation.state, self._operation.error = "failed", str(error)
                    self._finish_operation(self._operation)
                for job in tuple(self.jobs):
                    self._finish_job(job, {"state": "failed", "error": str(error)})
                return
            if self._operation is not None and self._operation.name == "start":
                self._poll_operation()
                if self._operation is not None:
                    return
            for job in tuple(self.jobs):
                if job.done:
                    continue
                if not job._job_id and not job._status:
                    if job.cancelled:
                        self._finish_job(job, {"state": "cancelled"})
                        continue
                    self._start_job(job)
                try:
                    status = job._status or self.server.status(job._job_id)
                    state = status.get("state", "queued")
                    job._update(status)
                    if state in TERMINAL_STATES:
                        job._status = status
                        if state == "succeeded" and not job.cancelled and self.jobs[0] is not job:
                            continue
                        self._finish_job(job, status)
                    else:
                        job.state = "queued" if state == "queued" else "running"
                except Exception as error:
                    self._finish_job(job, {"state": "failed", "error": str(error)})
            self._poll_operation()
        finally:
            self._ticking = False
            self.redraw_ui()

    def draw_jobs(self, layout):
        """Draw all unfinished jobs in any developer-provided layout."""
        for job in self.jobs:
            self._draw_job(layout, job)

    def _draw_job(self, layout, job):
        row = layout.row(align=True)
        row.progress(factor=job.progress, type="BAR", text=job.text)
        cancel = row.row(align=True)
        cancel.enabled = not job.cancelled and job.cancellable
        cancel.operator(f"{self.namespace}.cancel_job", text="", icon="X").job_id = job.id

    def _first_running_job(self):
        return next((job for job in self.jobs
                     if job.state == "running" and not job.done), None)

    def _status_bar_item(self):
        job = self._first_running_job()
        if job is not None:
            return job
        operation = self._operation
        if operation is not None and not operation.done and operation.name in MAINTENANCE_MESSAGES:
            return operation
        return None

    def redraw_ui(self, context=None):
        try:
            import bpy
        except ModuleNotFoundError:
            return
        context = context or bpy.context
        window_manager = getattr(context, "window_manager", None)
        for window in getattr(window_manager, "windows", ()):
            try:
                for area in window.screen.areas:
                    area.tag_redraw()
                    for region in area.regions:
                        region.tag_redraw()
            except ReferenceError:
                continue
        if self._registered:
            item = self._status_bar_item()
            state = None if item is None else (item, item.progress, item.text)
            if isinstance(item, Job):
                state += (item.cancelled, item.cancellable)
            if state != self._status_bar_state:
                self._status_bar_state = state
                timers = bpy.app.timers
                if not timers.is_registered(self._redraw_status_bars):
                    timers.register(self._redraw_status_bars)

    def _redraw_status_bars(self):
        import bpy

        context = bpy.context
        for window in context.window_manager.windows:
            screen = getattr(window, "screen", None)
            workspace = getattr(window, "workspace", None)
            if screen is None or workspace is None or getattr(screen, "is_temporary", False):
                continue
            try:
                with context.temp_override(window=window):
                    workspace.status_text_set_internal(None)
            except (ReferenceError, RuntimeError):
                continue

    def state(self):
        """Return the current runtime state without contacting the server."""
        if self._operation is not None:
            return {"setup": "INSTALLING", "migrate": "MIGRATING", "start": "STARTING",
                    "stop": "STOPPING", "restart": "STOPPING"}.get(self._operation.name, "PROCESSING")
        if not self._config.environment_ready(self.storage_root()):
            return "UNAVAILABLE"
        state = self.server.snapshot.get("state", "OFFLINE")
        if state in {"STARTING", "STOPPING", "ERROR"}:
            return state
        if self.jobs:
            return "PROCESSING"
        return "OFFLINE" if state == "UNAVAILABLE" else state

    def can_submit(self):
        """Return whether a job can be submitted, starting the server if needed."""
        return (self._operation is None or self._operation.name == "start") and self.state() in {"OFFLINE", "STARTING", "IDLE", "PROCESSING"}

    def draw_server_controls(self, layout, *, setup_operator=None):
        """Draw server status and controls, with an optional add-on setup operator."""
        state = self.state()
        if self._operation is not None and self._operation.name in {"setup", "migrate"}:
            layout.progress(factor=self._operation.progress, type="BAR", text=self._operation.text)
        labels = {
            "UNAVAILABLE": ("Environment is not installed", "ERROR"),
            "INSTALLING": ("Installing environment", "TIME"),
            "MIGRATING": ("Migrating Folder", "TIME"),
            "OFFLINE": ("Offline", "PAUSE"),
            "STARTING": ("Starting", "TIME"),
            "IDLE": ("Idle", "RADIOBUT_ON"),
            "PROCESSING": ("Processing", "TIME"),
            "STOPPING": ("Stopping", "TIME"),
            "ERROR": ("Server error", "ERROR"),
        }
        text, icon = labels[state]
        row = layout.row(align=True)
        row.label(text=text, text_ctxt="BlendJob", icon=icon)
        operators = {cls.__name__: cls for cls in self.operator_classes()}
        for name, icon in (("StartServer", "PLAY"), ("RestartServer", "FILE_REFRESH"),
                           ("StopServer", "PAUSE")):
            operator = operators[name]
            if operator.poll(None):
                row.operator(operator.bl_idname, text="", icon=icon)
        if state == "ERROR":
            layout.label(text=self.server.snapshot.get("message", "Server error"), icon="ERROR")
        if state != "UNAVAILABLE":
            layout.operator(operators["OpenServerLog"].bl_idname, icon="TEXT")
        if state == "UNAVAILABLE":
            row = layout.row()
            row.operator_context = "INVOKE_DEFAULT"
            row.operator(setup_operator or operators["SetupServer"].bl_idname, icon="IMPORT")

    def resource(self, name):
        return self.server.resource(name)

    def enable(self):
        try:
            import bpy
        except ModuleNotFoundError:
            return
        timers = getattr(bpy.app, "timers", None)
        if timers is None or timers.is_registered(self._poll_server):
            return
        timers.register(self._poll_server, first_interval=1.0, persistent=True)

    def disable(self):
        self._closing = True
        try:
            self.cancel_jobs()
            # Stop workers before callbacks release input files.
            self.server.detach()
            if self._operation is not None:
                self._operation.state = "cancelled"
                self._finish_operation(self._operation)
            for job in tuple(self.jobs):
                self._finish_job(job, {"state": "cancelled"})
        finally:
            try:
                import bpy
            except ModuleNotFoundError:
                bpy = None
            timers = getattr(getattr(bpy, "app", None), "timers", None)
            if timers is not None:
                for callback in (self._poll_server, self._redraw_status_bars):
                    if timers.is_registered(callback):
                        timers.unregister(callback)
            self._status_bar_state = None
            self._closing = False

    def _poll_server(self):
        self._tick()
        return 0.25 if self.jobs or self._operation is not None else 1.0
