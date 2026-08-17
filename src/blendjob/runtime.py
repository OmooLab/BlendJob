import json
import os
import subprocess
import sys
import threading
from pathlib import Path

from .controller import ServerController
from .entrypoint import normalized_entrypoint
from .environment import environment_digest, normalized_environment
from .operator import JobOperatorBase


class EnvironmentController:
    """Install the declared Environment, then run arbitrary project setup."""

    def __init__(self, runtime):
        self.runtime = runtime
        self.process = None
        self.log_file = None
        self.post_thread = None
        self.post_status = {}
        self.post_error = ""
        self.cancelled = False
        self._post_job_id = ""
        self._post_stage = 1
        self._lock = threading.Lock()

    def submit(self, _job_type, _parameters):
        runtime = self.runtime
        runtime.storage_root()
        runtime.install_status_path().unlink(missing_ok=True)
        self.post_thread = None
        self.post_status = {}
        self.post_error = ""
        self.cancelled = False
        self._post_job_id = ""
        self._post_stage = 1
        self.log_file = runtime.install_log_path().open(
            "w", encoding="utf-8"
        )
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8:backslashreplace"
        environment["PYTHONUTF8"] = "1"
        self.process = subprocess.Popen(
            runtime.install_command(),
            stdout=self.log_file,
            stderr=subprocess.STDOUT,
            cwd=runtime.storage_root(),
            creationflags=windows_creation_flags(),
            env=environment,
        )
        return {
            "job_id": "environment",
            "directory": str(runtime.storage_root()),
        }

    def status(self, _job_id):
        status = self._read_status()
        if self.process is None or self.process.poll() is None:
            return {
                **status,
                "state": "running",
                "progress": status.get("progress", 0.0),
            }
        if self.process.returncode != 0:
            return {
                "message": "Environment installation failed",
                **status,
                "state": "failed",
                "error": status.get("error", "Environment installer failed"),
            }
        if self.runtime.post_install is None:
            return self._success_status()
        if self.post_thread is None:
            self.post_thread = threading.Thread(
                target=self._run_post_install,
                name="BlendJobPostInstall",
                daemon=True,
            )
            self.post_thread.start()
        if self.post_thread.is_alive():
            with self._lock:
                post_status = dict(self.post_status)
            return {
                "progress": post_status.get("progress", 0.0),
                "message": post_status.get(
                    "message",
                    "Running post-install setup",
                ),
                "stage": post_status.get("stage", 2),
                "stage_label": post_status.get("stage_label", "Post Install"),
                "state": "running",
            }
        return self._success_status()

    def cancel(self, _job_id):
        self.cancelled = True
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
        if self._post_job_id:
            try:
                self.runtime.server.cancel(self._post_job_id)
            except RuntimeError:
                pass
        return {"state": "cancelled"}

    def mark_job_complete(self, _job_id):
        if self.log_file is not None:
            self.log_file.close()
            self.log_file = None
        try:
            self.runtime.install_status_path().unlink(missing_ok=True)
        except OSError:
            pass

    def _run_post_install(self):
        runtime = self.runtime
        runtime._request_progress = self._update_post_progress
        runtime._request_cancel_check = lambda: self.cancelled
        try:
            runtime.post_install(runtime)
        except Exception as error:
            self.post_error = str(error)
        finally:
            runtime._request_progress = None
            runtime._request_cancel_check = None

    def _update_post_progress(self, status):
        job_id = str(status.get("job_id", ""))
        with self._lock:
            if job_id and job_id != self._post_job_id:
                self._post_job_id = job_id
                self._post_stage += 1
            self.post_status = {
                **status,
                "stage": self._post_stage,
                "stage_label": str(status.get("job_type", "Post Install")),
            }

    def _success_status(self):
        if self.cancelled:
            return {
                "progress": 1.0,
                "message": "Environment installation cancelled",
                "state": "cancelled",
            }
        if self.post_error:
            return {
                "progress": 1.0,
                "message": f"Environment is ready; post-install failed: {self.post_error}",
                "state": "failed",
                "error": self.post_error,
                "post_install_error": self.post_error,
            }
        return {
            "progress": 1.0,
            "message": "Environment is ready",
            "state": "succeeded",
        }

    def _read_status(self):
        try:
            return json.loads(
                self.runtime.install_status_path().read_text(
                    encoding="utf-8"
                )
            )
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}


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
        post_install=None,
    ):
        self.server_entrypoint = normalized_entrypoint(
            server_entrypoint,
            root=entrypoint_root,
        )
        self.storage_root_factory = storage_root
        self.environment = normalized_environment(environment)
        self.environment_hash = environment_digest(self.environment)
        if post_install is not None and not callable(post_install):
            raise TypeError("post_install must be callable")
        self.post_install = post_install
        if not isinstance(namespace, str) or not namespace.strip():
            raise ValueError("namespace must be a non-empty string")
        self.namespace = namespace.strip()
        self.active_job = None
        self.progress = 0.0
        self.message = "Ready"
        self._operator_base = None
        self._operator_classes = None
        self._timer_registered = False
        self._registered = False
        self._status_bar_draw = self._create_status_bar_draw()
        self._request_progress = None
        self._request_cancel_check = None
        self._environment_controller = EnvironmentController(self)
        self.server = ServerController(
            self._server_command,
            cwd_factory=self.storage_root,
            log_path_factory=self.server_log_path,
            creationflags=windows_creation_flags(),
        )
        self.JobOperatorBase = self.operator_base()

    def storage_root(self):
        configured = (
            self.storage_root_factory()
            if callable(self.storage_root_factory)
            else self.storage_root_factory
        )
        root = Path(configured).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root

    def environment_directory(self):
        return self.storage_root() / ".venv"

    def environment_python(self):
        directory = self.environment_directory()
        if os.name == "nt":
            return directory / "Scripts" / "python.exe"
        return directory / "bin" / "python"

    def environment_manifest_path(self):
        return self.storage_root() / "manifest.json"

    def environment_config_path(self):
        return self.storage_root() / "environment.json"

    def install_status_path(self):
        return self.storage_root() / "install-status.json"

    def install_log_path(self):
        return self.storage_root() / "install.log"

    def server_log_path(self):
        return self.storage_root() / "server.log"

    def environment_manifest(self):
        try:
            value = json.loads(
                self.environment_manifest_path().read_text(encoding="utf-8")
            )
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}
        return value if isinstance(value, dict) else {}

    def environment_ready(self):
        return (
            self.environment_python().is_file()
            and self.environment_manifest().get("environment_hash")
            == self.environment_hash
        )

    def write_environment_config(self):
        path = self.environment_config_path()
        path.write_text(
            json.dumps(self.environment, indent=2),
            encoding="utf-8",
        )
        return path

    def install_command(self):
        installer = Path(__file__).with_name("installer") / "environment.py"
        config = self.write_environment_config()
        return [
            sys.executable,
            str(installer),
            "--storage-root",
            str(self.storage_root()),
            "--config",
            str(config),
            "--status",
            str(self.install_status_path()),
            "--stages",
            "1",
        ]

    def request(self, job_type, parameters, response=None):
        """Directly request one Server Job and return its JobResult."""
        if self._request_cancel_check is not None and self._request_cancel_check():
            raise RuntimeError("Server request was cancelled")
        return self.server.request(
            job_type,
            parameters,
            response=response,
            progress=self._request_progress,
            cancel_check=self._request_cancel_check,
        )

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

        class InstallEnvironment(operator_base, bpy.types.Operator):
            bl_idname = f"{runtime.namespace}.install_environment"
            bl_label = "Install Environment"
            job_type = "install-environment"
            starting_message = "Starting Environment installation"

            def request(self, _context):
                if not bpy.app.online_access:
                    raise RuntimeError(
                        "Online Access is disabled in Blender Preferences"
                    )
                return {}

            def controller(self, _runtime):
                runtime.server.stop()
                return runtime._environment_controller

        class CancelJob(bpy.types.Operator):
            bl_idname = f"{runtime.namespace}.cancel_job"
            bl_label = "Cancel Job"

            @classmethod
            def poll(cls, _context):
                return runtime.active_job is not None

            def execute(self, _context):
                runtime.cancel_active()
                return {"FINISHED"}

        class StartServer(bpy.types.Operator):
            bl_idname = f"{runtime.namespace}.start_server"
            bl_label = "Start Server"

            def execute(self, _context):
                try:
                    runtime.server.enable()
                except (OSError, RuntimeError) as error:
                    self.report({"ERROR"}, str(error))
                    return {"CANCELLED"}
                return {"FINISHED"}

        class StopServer(bpy.types.Operator):
            bl_idname = f"{runtime.namespace}.stop_server"
            bl_label = "Stop Server"

            def execute(self, _context):
                runtime.server.stop()
                runtime.redraw_ui()
                return {"FINISHED"}

        class RestartServer(bpy.types.Operator):
            bl_idname = f"{runtime.namespace}.restart_server"
            bl_label = "Restart Server"

            def execute(self, _context):
                try:
                    runtime.server.restart()
                except (OSError, RuntimeError) as error:
                    self.report({"ERROR"}, str(error))
                    return {"CANCELLED"}
                return {"FINISHED"}

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
            InstallEnvironment,
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
        bpy.types.STATUSBAR_HT_header.append(self._status_bar_draw)
        self._registered = True
        self.enable()

    def unregister(self):
        if not self._registered:
            return
        import bpy

        self.disable()
        bpy.types.STATUSBAR_HT_header.remove(self._status_bar_draw)
        for class_type in reversed(self.operator_classes()):
            bpy.utils.unregister_class(class_type)
        self._registered = False

    def begin_job(self, job):
        if self.active_job is not None:
            raise RuntimeError("Another task is running")
        self.active_job = job

    def finish_job(self, job):
        if job.job_id:
            job.controller.mark_job_complete(job.job_id)
        try:
            job.operator.cleanup()
        finally:
            if self.active_job is job:
                self.active_job = None

    def cancel_active(self):
        job = self.active_job
        if job is None:
            return
        job.cancelled = True
        if not job.started or not job.job_id:
            return
        try:
            job.controller.cancel(job.job_id)
        except RuntimeError:
            pass

    def close_active(self):
        job = self.active_job
        if job is not None:
            self.finish_job(job)

    def update_ui(self, context, progress, message):
        self.progress = min(max(float(progress), 0.0), 1.0)
        self.message = str(message)
        window_manager = getattr(context, "window_manager", None)
        if window_manager is not None and hasattr(
            window_manager, "progress_update"
        ):
            window_manager.progress_update(int(self.progress * 100))
        self.redraw_ui(context)

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
                if area.type in {"STATUSBAR", "VIEW_3D", "PREFERENCES"}:
                    area.tag_redraw()
                    for region in getattr(area, "regions", ()):
                        region.tag_redraw()
        if not force:
            return
        workspace = getattr(context, "workspace", None)
        if workspace is not None:
            try:
                workspace.status_text_set_internal(None)
            except (AttributeError, RuntimeError, TypeError):
                pass

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

    def enable(self):
        try:
            import bpy
        except ModuleNotFoundError:
            return
        timers = getattr(bpy.app, "timers", None)
        if timers is None or timers.is_registered(self._poll_server):
            return
        timers.register(self._poll_server, first_interval=1.0, persistent=True)
        self._timer_registered = True

    def disable(self):
        self.cancel_active()
        self.close_active()
        try:
            import bpy
        except ModuleNotFoundError:
            bpy = None
        timers = getattr(getattr(bpy, "app", None), "timers", None)
        if timers is not None and timers.is_registered(self._poll_server):
            timers.unregister(self._poll_server)
        self._timer_registered = False
        self.server.detach()

    def _poll_server(self):
        interval = self.server.poll(available=self.environment_ready())
        self.redraw_ui()
        return interval

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

    def _server_command(self, port, instance_id):
        launcher = Path(__file__).with_name("launcher") / "runner.py"
        return [
            str(self.environment_python()),
            "-u",
            str(launcher),
            "--entrypoint",
            self.server_entrypoint,
            "--storage-root",
            str(self.storage_root()),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--instance-id",
            instance_id,
            "--parent-pid",
            str(os.getpid()),
        ]

def windows_creation_flags():
    if os.name != "nt":
        return 0
    return subprocess.CREATE_NO_WINDOW
