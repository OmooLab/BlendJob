from pathlib import Path

from .configuration import ServerConfiguration
from .process import ProcessServer, ActionController
from .operator import JobOperatorBase
from .maintenance import MaintenanceController, SetupController, MigrateController


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
        self.config = ServerConfiguration(server_entrypoint, environment, entrypoint_root=entrypoint_root)
        self.storage_root_setting = storage_root
        if post_setup is not None and not callable(post_setup):
            raise TypeError("post_setup must be callable")
        self.post_setup = post_setup
        if not isinstance(namespace, str) or not namespace.strip():
            raise ValueError("namespace must be a non-empty string")
        self.namespace = namespace.strip()
        self.active_job = None
        self.progress = 0.0
        self.message = "Ready"
        self._ui_state = None
        self._operator_base = None
        self._operator_classes = None
        self._registered = False
        self._status_bar_draw = self._create_status_bar_draw()
        self.server = ProcessServer(self)
        self.JobOperatorBase = self.operator_base()

    def setup_controller(self, target=None, *, mirror=False, online=True):
        """Install and validate an environment, then save its location."""
        target = self.storage_root() if target is None else Path(target).expanduser().resolve()
        self.config.inspect_installation(target)
        return SetupController(self, target, mirror=mirror, online=online)

    def migrate_controller(self, target):
        """Migrate storage to the selected target."""
        if not str(target).strip():
            raise ValueError("Choose a migration target")
        target = Path(target).expanduser().resolve()
        return MigrateController(self, target)

    def maintenance_busy(self):
        return isinstance(getattr(self.active_job, "controller", None), MaintenanceController)

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

    def environment_ready(self):
        return self.config.environment_ready(self.storage_root())

    def request_blocking(self, job_type, parameters, response=None):
        """Wait for one JobResult in a standalone script; use JobOperatorBase for Blender UI."""
        return self.server.request_blocking(job_type, parameters, response=response)

    def operator_base(self):
        if self._operator_base is not None:
            return self._operator_base
        runtime = self

        class BoundJobOperator(JobOperatorBase):
            job_runtime = runtime

        BoundJobOperator.__name__ = "JobOperatorBase"
        self._operator_base = BoundJobOperator
        return BoundJobOperator

    def operator_classes(self):
        if self._operator_classes is not None:
            return self._operator_classes
        import bpy

        runtime = self
        operator_base = self.JobOperatorBase

        class SetupServer(operator_base, bpy.types.Operator):
            bl_idname = f"{runtime.namespace}.setup_server"
            bl_label = "Set Up Server"
            job_type = "setup-server"
            starting_message = "Preparing Server"

            directory: bpy.props.StringProperty(name="Server Location", subtype="DIR_PATH", options={"SKIP_SAVE"})
            use_mirror: bpy.props.BoolProperty(name="Use China Mirror", default=False)

            @classmethod
            def poll(cls, _context):
                return not runtime.server_busy()

            def invoke(self, context, _event):
                if not self.directory:
                    self.directory = str(runtime.storage_root())
                return context.window_manager.invoke_props_dialog(self, width=460)

            def draw(self, _context):
                self.layout.prop(self, "directory")
                self.layout.prop(self, "use_mirror")

            def cancel(self, context):
                if getattr(self, "_timer", None) is not None:
                    super().cancel(context)

            def controller(self, _runtime):
                return runtime.setup_controller(self.directory or None, mirror=self.use_mirror,
                                                online=bpy.app.online_access)

            def _finish_success(self, context, job, status):
                """Call post_setup(controller) on the main thread after releasing setup."""
                result = super()._finish_success(context, job, status)
                if result == {"FINISHED"} and runtime.post_setup is not None:
                    try:
                        runtime.post_setup(job.controller)
                    except Exception as error:
                        self.report({"ERROR"}, f"Setup completed, but post_setup failed: {error}")
                        return {"CANCELLED"}
                return result

        class MigrateFolder(operator_base, bpy.types.Operator):
            bl_idname = f"{runtime.namespace}.migrate_folder"
            bl_label = "Migrate Folder"
            job_type = "migrate-folder"
            starting_message = "Migrating Folder"

            directory: bpy.props.StringProperty(subtype="DIR_PATH", options={"SKIP_SAVE"})
            filter_folder: bpy.props.BoolProperty(default=True, options={"HIDDEN"})

            @classmethod
            def poll(cls, _context):
                return not runtime.server_busy()

            def invoke(self, context, _event):
                if not self.directory:
                    self.directory = str(runtime.storage_root().parent)
                context.window_manager.fileselect_add(self)
                return {"RUNNING_MODAL"}

            def cancel(self, context):
                if getattr(self, "_timer", None) is not None:
                    super().cancel(context)

            def controller(self, _runtime):
                if not self.directory.strip():
                    raise ValueError("Choose a migration target")
                selected = Path(self.directory).expanduser().resolve()
                name = runtime.storage_root().name
                target = next((path for path in (selected, *selected.parents)
                               if path.name.lower() == name.lower()), selected / name)
                return runtime.migrate_controller(target)

            def response(self, _context, result):
                warning = (result.value or {}).get("warning")
                if warning:
                    self.report({"WARNING"}, warning)
                    return warning
                return "Folder migration completed"

        class ClearJobFiles(operator_base, bpy.types.Operator):
            bl_idname = f"{runtime.namespace}.clear_job_files"
            bl_label = "Clear Job Files"
            bl_options = {"INTERNAL"}
            job_type = "maintain-job-files"
            starting_message = "Reading job files"

            @classmethod
            def poll(cls, _context):
                return runtime.environment_ready() and not runtime.server_busy()

            def invoke(self, context, _event):
                self._action = "preview_files"
                return operator_base.execute(self, context)

            def controller(self, _runtime):
                return ActionController(runtime.server, self._action)

            def request(self, _context):
                if self._action == "preview_files":
                    return {}
                return {"jobs": self._preview["jobs"], "instance_id": self._preview["instance_id"]}

            def draw(self, _context):
                preview = self._preview
                self.layout.label(text=f"Delete {len(preview['jobs'])} jobs ({preview['bytes'] / 1_000_000:.1f} MB)?")
                self.layout.label(text="Permanently delete job outputs and logs.")

            def execute(self, context):
                if not self.poll(context) or not getattr(self, "_preview", {}).get("jobs"):
                    self.report({"ERROR"}, "Preview and confirm cleanup while the server is idle")
                    return {"CANCELLED"}
                self._action = "clear_files"
                return operator_base.execute(self, context)

            def response(self, context, result):
                value = result.value
                if self._action == "preview_files":
                    self._preview = value
                    if not value["jobs"]:
                        self.report({"INFO"}, "No unused job files to clear")
                    else:
                        context.window_manager.invoke_props_dialog(self, width=460, confirm_text="Delete Files")
                    return "Job files inspected"
                kept = value["skipped"] + len(value["failed"])
                message = f"Deleted {len(value['jobs'])} jobs ({value['bytes'] / 1_000_000:.1f} MB); kept {kept}"
                self.report({"WARNING"} if kept else {"INFO"}, message)
                return message

        class CancelJob(bpy.types.Operator):
            bl_idname = f"{runtime.namespace}.cancel_job"
            bl_label = "Cancel Job"

            @classmethod
            def poll(cls, _context):
                job = runtime.active_job
                return job is not None and not (
                    isinstance(getattr(job, "controller", None), MaintenanceController)
                    and not job.controller.cancellable
                )

            def execute(self, _context):
                runtime.cancel_active()
                return {"FINISHED"}

        class ServerControlOperator:
            @classmethod
            def poll(cls, _context):
                return not runtime.maintenance_busy()

        class StartServer(ServerControlOperator, bpy.types.Operator):
            bl_idname = f"{runtime.namespace}.start_server"
            bl_label = "Start Server"

            def execute(self, _context):
                try:
                    runtime.server.enable()
                except (OSError, RuntimeError) as error:
                    self.report({"ERROR"}, str(error))
                    return {"CANCELLED"}
                return {"FINISHED"}

        class StopServer(ServerControlOperator, bpy.types.Operator):
            bl_idname = f"{runtime.namespace}.stop_server"
            bl_label = "Stop Server"

            def execute(self, _context):
                runtime.stop()
                return {"FINISHED"}

        class RestartServer(ServerControlOperator, bpy.types.Operator):
            bl_idname = f"{runtime.namespace}.restart_server"
            bl_label = "Restart Server"

            def execute(self, _context):
                try:
                    runtime.server.restart()
                except (OSError, RuntimeError) as error:
                    self.report({"ERROR"}, str(error))
                    return {"CANCELLED"}
                return {"FINISHED"}

        class OpenServerLog(ServerControlOperator, bpy.types.Operator):
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
            MigrateFolder,
            ClearJobFiles,
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
        self._ensure_status_bar_draw()
        self._registered = True
        self.enable()

    def unregister(self):
        if not self._registered:
            return
        import bpy

        self.disable()
        self._remove_status_bar_draw()
        for class_type in reversed(self.operator_classes()):
            bpy.utils.unregister_class(class_type)
        self._registered = False

    def begin_job(self, job):
        if self.active_job is not None:
            raise RuntimeError("Another task is running")
        self.active_job = job

    def finish_job(self, job):
        try:
            job.controller.mark_job_complete(job.job_id)
        except (OSError, RuntimeError):
            pass
        finally:
            try:
                job.operator.cleanup()
            finally:
                if self.active_job is job:
                    self.active_job = None

    def cancel_active(self):
        job = self.active_job
        if job is None or not getattr(job.controller, "cancellable", True):
            return
        job.cancelled = True
        if not job.job_id:
            return
        try:
            job.controller.cancel(job.job_id)
        except RuntimeError:
            pass

    def close_active(self):
        job = self.active_job
        if job is not None:
            self.finish_job(job)

    def stop(self):
        self.cancel_active()
        try:
            self.server.stop()
        finally:
            self.close_active()
            self.redraw_ui(force=True)

    def update_ui(self, context, progress, message):
        progress = min(max(float(progress), 0.0), 1.0)
        message = str(message)
        state = (id(self.active_job), progress, message)
        if self._ui_state == state:
            return
        self._ui_state = state
        self.progress = progress
        self.message = message
        window_manager = getattr(context, "window_manager", None)
        if window_manager is not None and hasattr(
            window_manager, "progress_update"
        ):
            window_manager.progress_update(int(self.progress * 100))
        self.redraw_ui(context, force=True)

    def redraw_ui(self, context=None, force=False):
        try:
            import bpy
        except ModuleNotFoundError:
            return
        context = context or bpy.context
        window_manager = getattr(context, "window_manager", None)
        for window in getattr(window_manager, "windows", ()):
            screen = getattr(window, "screen", None)
            for area in getattr(screen, "areas", ()):
                if area.type in {"VIEW_3D", "PREFERENCES"}:
                    area.tag_redraw()
                    for region in getattr(area, "regions", ()):
                        region.tag_redraw()
        # Window destruction callbacks must finish before switching context.
        if force and not bpy.app.timers.is_registered(self._redraw_status_bars):
            bpy.app.timers.register(self._redraw_status_bars)

    def _redraw_status_bars(self):
        import bpy

        context = bpy.context
        for window in context.window_manager.windows:
            screen = getattr(window, "screen", None)
            workspace = getattr(window, "workspace", None)
            if (
                screen is None
                or workspace is None
                or getattr(screen, "is_temporary", False)
            ):
                continue
            try:
                with context.temp_override(window=window):
                    workspace.status_text_set_internal(None)
            except (ReferenceError, RuntimeError):
                continue

    def server_status(self):
        return dict(self.server.snapshot)

    def server_busy(self):
        return self.active_job is not None or self.server.snapshot.get(
            "state"
        ) == "BUSY"

    def resource(self, name):
        return self.server.resource(name)

    def clear_resource(self, name):
        return self.server.clear_resource(name)

    def action_controller(self, action):
        return ActionController(self.server, action)

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
        self.cancel_active()
        self.close_active()
        try:
            import bpy
        except ModuleNotFoundError:
            bpy = None
        timers = getattr(getattr(bpy, "app", None), "timers", None)
        if timers is not None:
            for callback in (self._poll_server, self._redraw_status_bars):
                if timers.is_registered(callback):
                    timers.unregister(callback)
        self.server.detach()

    def _poll_server(self):
        if self.maintenance_busy():
            return 0.5
        if self._registered:
            self._ensure_status_bar_draw()
        previous = {key: value for key, value in self.server.snapshot.items() if key != "active_job"}
        available = self.server.control.process is not None or self.environment_ready()
        interval = self.server.poll(available=available)
        current = {key: value for key, value in self.server.snapshot.items() if key != "active_job"}
        if current != previous:
            self.redraw_ui()
        return interval

    def _ensure_status_bar_draw(self):
        import bpy

        status_bar = bpy.types.STATUSBAR_HT_header
        draw_funcs = getattr(status_bar.draw, "_draw_funcs", ())
        if self._status_bar_draw not in draw_funcs:
            status_bar.append(self._status_bar_draw)

    def _remove_status_bar_draw(self):
        import bpy

        status_bar = bpy.types.STATUSBAR_HT_header
        draw_funcs = getattr(status_bar.draw, "_draw_funcs", ())
        if self._status_bar_draw in draw_funcs:
            status_bar.remove(self._status_bar_draw)

    def _draw_status_bar(self, owner, _context):
        if self.active_job is None:
            return
        row = owner.layout.row(align=True)
        progress_row = row.row(align=True)
        progress_row.ui_units_x = 18.0
        progress_row.progress(
            factor=self.progress,
            type="BAR",
            text=self.message,
        )
        row.operator(
            f"{self.namespace}.cancel_job",
            text="",
            icon="X",
        )

    def _create_status_bar_draw(self):
        runtime = self

        def draw_status_bar(owner, context):
            runtime._draw_status_bar(owner, context)

        return draw_status_bar
