import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

import blendjob
from blendjob.runtime import EnvironmentController, JobRuntime
from blendjob.operator import JobOperatorBase, JobOperatorState


class EnvironmentControllerTest(unittest.TestCase):
    def test_install_commands_include_explicit_source(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "example.py").touch()
            runtime = JobRuntime(
                "example.py:server",
                entrypoint_root=directory,
                storage_root=directory,
                environment={"python": "3.12"},
                namespace="example",
            )

            official = runtime.install_command()
            mirror = runtime.install_command(source="mirror")

        self.assertEqual(official[-2:], ["--source", "official"])
        self.assertEqual(mirror[-2:], ["--source", "mirror"])

    def test_progress_is_monotonic_for_one_job(self):
        runtime = SimpleNamespace(update_ui=Mock(), redraw_ui=Mock())
        job = SimpleNamespace(
            job_id="job",
            progress_job_id="job",
            progress=0.8,
            runtime=runtime,
        )

        JobOperatorBase._update_from_job_status(
            SimpleNamespace(starting_message="Starting task"),
            None,
            job,
            {"job_id": "job", "progress": 0.2, "message": "Working"},
        )

        self.assertEqual(job.progress, 0.8)
        runtime.update_ui.assert_called_once_with(None, 0.8, "Working")

    def test_progress_resets_when_post_install_job_changes(self):
        runtime = SimpleNamespace(update_ui=Mock(), redraw_ui=Mock())
        job = SimpleNamespace(
            job_id="environment",
            progress_job_id="environment",
            progress=1.0,
            runtime=runtime,
        )

        JobOperatorBase._update_from_job_status(
            SimpleNamespace(starting_message="Starting task"),
            None,
            job,
            {"job_id": "model", "progress": 0.2, "message": "Downloading model"},
        )

        self.assertEqual(job.progress_job_id, "model")
        self.assertEqual(job.progress, 0.2)
        runtime.update_ui.assert_called_once_with(
            None,
            0.2,
            "Downloading model",
        )

    def test_job_runtime_is_the_public_runtime_class(self):
        self.assertIs(blendjob.JobRuntime, JobRuntime)
        self.assertFalse(hasattr(blendjob, "BlenderJobRuntime"))
        self.assertEqual(
            set(blendjob.__all__),
            {"JobContext", "JobResult", "JobRuntime", "JobServer"},
        )
        self.assertFalse(hasattr(blendjob, "JobOperatorState"))
        self.assertFalse(hasattr(blendjob, "ServerController"))

    def test_post_install_failure_is_reported_as_failure(self):
        runtime = SimpleNamespace()

        def post_install(_runtime):
            raise RuntimeError("model download failed")

        runtime.post_install = post_install
        controller = EnvironmentController(runtime)
        controller.process = SimpleNamespace(poll=lambda: 0, returncode=0)
        with tempfile.TemporaryDirectory() as directory, patch.object(
            runtime,
            "install_status_path",
            return_value=Path(directory) / "missing.json",
            create=True,
        ):
            controller.status("environment")
            controller.post_thread.join(1.0)
            status = controller.status("environment")

        self.assertEqual(status["state"], "failed")
        self.assertEqual(status["error"], "model download failed")
        self.assertNotIn("post_install_error", status)

    def test_completed_install_removes_transient_status(self):
        with tempfile.TemporaryDirectory() as directory:
            status_path = Path(directory) / "install-status.json"
            status_path.write_text("{}", encoding="utf-8")
            runtime = SimpleNamespace(install_status_path=lambda: status_path)
            controller = EnvironmentController(runtime)

            controller.mark_job_complete("environment")

            self.assertFalse(status_path.exists())

    def test_server_runner_uses_bundled_launcher(self):
        runtime = SimpleNamespace(
            environment_python=lambda: Path("environment/python"),
            server_entrypoint="addon/server/__init__.py:server",
            storage_root=lambda: Path("storage"),
        )

        command = JobRuntime._server_command(runtime, 8123, "instance")

        self.assertEqual(command[:2], [str(Path("environment/python")), "-u"])
        self.assertEqual(Path(command[2]).name, "runner.py")
        self.assertEqual(Path(command[2]).parent.name, "launcher")
        self.assertEqual(command[3], "--entrypoint")
        self.assertNotIn("blendjob.runner", command)
        self.assertIn("addon/server/__init__.py:server", command)


class JobRuntimeStopTest(unittest.TestCase):
    def runtime(self, server):
        runtime = JobRuntime.__new__(JobRuntime)
        runtime.active_job = None
        runtime.server = server
        runtime.redraw_ui = Mock()
        return runtime

    def job(self, events, controller=None):
        controller = controller or SimpleNamespace(
            cancel=lambda _job_id: events.append("cancel"),
            mark_job_complete=lambda _job_id: events.append("mark"),
        )
        operator = SimpleNamespace(
            cleanup=lambda: events.append("cleanup"),
        )
        return JobOperatorState(
            runtime=None,
            operator=operator,
            controller=controller,
            job_id="job",
            started=True,
        )

    def test_stop_cancels_active_job_before_server_and_cleans_up_after(self):
        events = []

        def stop_server():
            events.append("stop")
            server.auto_start = False

        server = SimpleNamespace(auto_start=True, stop=stop_server)
        runtime = self.runtime(server)
        job = self.job(events)
        job.runtime = runtime
        runtime.active_job = job

        runtime.stop()

        self.assertEqual(events, ["cancel", "stop", "mark", "cleanup"])
        self.assertTrue(job.cancelled)
        self.assertIsNone(runtime.active_job)
        self.assertFalse(server.auto_start)
        runtime.redraw_ui.assert_called_once_with(force=True)

    def test_stop_idle_server_without_job_cleanup(self):
        events = []

        def stop_server():
            events.append("stop")
            server.auto_start = False

        server = SimpleNamespace(auto_start=True, stop=stop_server)
        runtime = self.runtime(server)

        runtime.stop()

        self.assertEqual(events, ["stop"])
        self.assertFalse(server.auto_start)
        runtime.redraw_ui.assert_called_once_with(force=True)

    def test_stop_failure_still_closes_active_job_and_redraws(self):
        events = []

        def stop_server():
            events.append("stop")
            raise RuntimeError("stop failed")

        runtime = self.runtime(SimpleNamespace(stop=stop_server))
        job = self.job(events)
        job.runtime = runtime
        runtime.active_job = job

        with self.assertRaisesRegex(RuntimeError, "stop failed"):
            runtime.stop()

        self.assertEqual(events, ["cancel", "stop", "mark", "cleanup"])
        self.assertIsNone(runtime.active_job)
        runtime.redraw_ui.assert_called_once_with(force=True)

    def test_stop_cancels_job_while_submission_is_in_flight(self):
        events = []
        submit_started = threading.Event()
        release_submit = threading.Event()

        def submit(_job_type, _parameters):
            submit_started.set()
            release_submit.wait(1.0)
            return {"job_id": "job", "directory": "."}

        controller = SimpleNamespace(
            submit=submit,
            cancel=lambda _job_id: events.append("cancel"),
            mark_job_complete=lambda _job_id: events.append("mark"),
        )
        job = JobOperatorState(
            runtime=None,
            operator=SimpleNamespace(
                job_type="example",
                cleanup=lambda: events.append("cleanup"),
            ),
            controller=controller,
        )
        submit_thread = threading.Thread(
            target=JobOperatorBase._submit_job,
            args=(job.operator, job, {}),
        )

        def stop_server():
            events.append("stop-start")
            release_submit.set()
            submit_thread.join(1.0)
            events.append("stop-end")

        runtime = self.runtime(SimpleNamespace(stop=stop_server))
        job.runtime = runtime
        runtime.active_job = job
        submit_thread.start()
        self.assertTrue(submit_started.wait(1.0))

        runtime.stop()

        self.assertEqual(
            events,
            ["stop-start", "cancel", "stop-end", "mark", "cleanup"],
        )
        self.assertTrue(job.cancelled)
        self.assertIsNone(runtime.active_job)

    def test_modal_after_stop_cancels_without_duplicate_cleanup(self):
        cleanup = Mock()
        runtime = SimpleNamespace(active_job=None)
        operator = JobOperatorBase()
        operator.job_runtime = runtime
        operator.cleanup = cleanup
        operator._timer = object()
        window_manager = SimpleNamespace(event_timer_remove=Mock())
        context = SimpleNamespace(window_manager=window_manager)

        result = operator.modal(context, SimpleNamespace(type="TIMER"))

        self.assertEqual(result, {"CANCELLED"})
        cleanup.assert_not_called()
        window_manager.event_timer_remove.assert_called_once()


class JobRuntimeRedrawTest(unittest.TestCase):
    def setUp(self):
        self.runtime = JobRuntime.__new__(JobRuntime)
        self.runtime.active_job = None
        self.notifications = []
        self.window = None
        self.context = SimpleNamespace(
            window_manager=SimpleNamespace(windows=[], progress_update=Mock()),
            temp_override=self.override,
        )
        bpy = ModuleType("bpy")
        bpy.context = self.context
        self.pending_redraws = []
        bpy.app = SimpleNamespace(timers=SimpleNamespace(
            is_registered=lambda callback: callback in self.pending_redraws,
            register=self.pending_redraws.append,
        ))
        patcher = patch.dict(sys.modules, {"bpy": bpy})
        patcher.start()
        self.addCleanup(patcher.stop)

    def flush_redraws(self):
        callbacks = self.pending_redraws[:]
        self.pending_redraws.clear()
        for callback in callbacks:
            self.assertIsNone(callback())

    @contextmanager
    def override(self, *, window):
        previous = self.window
        self.window = window
        try:
            yield
        finally:
            self.window = previous

    def add_window(self, workspace=None):
        if workspace is None:
            workspace = SimpleNamespace(
                status_text_set_internal=lambda _text: self.notifications.append(
                    (self.window, self.runtime.active_job)
                ),
            )
        area = SimpleNamespace(
            type="VIEW_3D", tag_redraw=Mock(),
            regions=[SimpleNamespace(tag_redraw=Mock())],
        )
        window = SimpleNamespace(
            workspace=workspace, screen=SimpleNamespace(areas=[area]),
        )
        self.context.window_manager.windows.append(window)
        return window

    def test_progress_reaches_windows_with_distinct_and_shared_workspaces(self):
        first = self.add_window()
        second = self.add_window()
        third = self.add_window(first.workspace)
        self.context.workspace = SimpleNamespace(status_text_set_internal=Mock())
        self.runtime.update_ui(self.context, 0.4, "Downloading")
        self.flush_redraws()
        self.assertEqual(self.runtime.progress, 0.4)
        self.assertEqual(self.runtime.message, "Downloading")
        self.assertEqual([id(w) for w, _ in self.notifications],
                         [id(first), id(second), id(third)])
        self.context.workspace.status_text_set_internal.assert_not_called()
        self.assertIsNone(self.window)
        for window in (first, second, third):
            window.screen.areas[0].tag_redraw.assert_called_once()
            window.screen.areas[0].regions[0].tag_redraw.assert_called_once()

    def test_unavailable_windows_do_not_prevent_later_notifications(self):
        self.context.window_manager.windows.extend([
            SimpleNamespace(screen=None, workspace=object()),
            SimpleNamespace(screen=SimpleNamespace(areas=[]), workspace=None),
        ])
        self.add_window(SimpleNamespace(
            status_text_set_internal=Mock(side_effect=ReferenceError("closed")),
        ))
        valid = self.add_window()
        self.runtime.redraw_ui(force=True)
        self.flush_redraws()
        self.assertEqual(len(self.notifications), 1)
        self.assertIs(self.notifications[0][0], valid)
        self.assertIsNone(self.window)

    def test_redraw_is_coalesced_and_uses_windows_after_closure(self):
        closing = self.add_window()
        self.runtime.update_ui(self.context, 0.1, "Working")
        self.runtime.update_ui(self.context, 0.2, "Working")
        self.assertEqual(len(self.pending_redraws), 1)
        self.assertEqual(self.notifications, [])
        self.context.window_manager.windows.remove(closing)
        surviving = self.add_window()
        self.flush_redraws()
        self.assertEqual(len(self.notifications), 1)
        self.assertIs(self.notifications[0][0], surviving)

    def test_temporary_window_only_redraws_its_editor(self):
        window = self.add_window()
        window.screen.is_temporary = True
        window.screen.areas[0].type = "PREFERENCES"
        self.runtime.redraw_ui(self.context, force=True)
        self.flush_redraws()
        self.assertEqual(self.notifications, [])
        window.screen.areas[0].tag_redraw.assert_called_once()

    def test_ordinary_poll_redraw_preserves_status_text(self):
        window = self.add_window()
        self.runtime.redraw_ui()
        self.assertEqual(self.notifications, [])
        window.screen.areas[0].tag_redraw.assert_called_once()

    def test_empty_window_collection_is_safe(self):
        self.runtime.redraw_ui(force=True)
        self.flush_redraws()
        self.assertEqual(self.notifications, [])

    def test_terminal_operator_cleanup_redraws_each_window_once(self):
        self.add_window()
        self.add_window()
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                self.notifications.clear()
                operator = JobOperatorBase()
                operator.job_runtime = self.runtime
                job = JobOperatorState(
                    self.runtime, operator, Mock(), job_id="job", started=True,
                )
                self.runtime.active_job = job
                result = operator._close(
                    self.context, job, "Finished", cancelled=cancelled,
                )
                self.flush_redraws()
                self.assertEqual(result, {"CANCELLED"} if cancelled else {"FINISHED"})
                self.assertEqual(len(self.notifications), 2)
                self.assertTrue(all(active is None for _, active in self.notifications))

    def test_server_stop_redraws_after_active_job_cleanup(self):
        self.add_window()
        self.runtime.server = Mock()
        self.runtime.active_job = JobOperatorState(
            self.runtime, SimpleNamespace(cleanup=Mock()), Mock(),
            job_id="job", started=True,
        )
        self.runtime.stop()
        self.flush_redraws()
        self.assertEqual(len(self.notifications), 1)
        self.assertIsNone(self.notifications[0][1])


class JobRuntimeStatusBarTest(unittest.TestCase):
    @staticmethod
    def original_draw(_owner, _context):
        pass

    def setUp(self):
        class StatusBarHeader:
            draw = self.original_draw

            @classmethod
            def append(cls, draw):
                draw_funcs = getattr(cls.draw, "_draw_funcs", None)
                if draw_funcs is None:
                    original_draw = cls.draw

                    def draw_all(owner, context):
                        for draw_func in draw_all._draw_funcs:
                            draw_func(owner, context)

                    draw_funcs = draw_all._draw_funcs = [original_draw]
                    cls.draw = draw_all
                draw._owner = "example"
                draw_funcs.append(draw)

            @classmethod
            def remove(cls, draw):
                draw_funcs = getattr(cls.draw, "_draw_funcs", ())
                if draw in draw_funcs:
                    draw_funcs.remove(draw)

        class Timers:
            callbacks = []

            @classmethod
            def is_registered(cls, callback):
                return callback in cls.callbacks

            @classmethod
            def register(cls, callback, **_options):
                cls.callbacks.append(callback)

            @classmethod
            def unregister(cls, callback):
                cls.callbacks.remove(callback)

        fake_bpy = ModuleType("bpy")
        fake_bpy.types = SimpleNamespace(
            Operator=object,
            STATUSBAR_HT_header=StatusBarHeader,
        )
        fake_bpy.utils = SimpleNamespace(
            register_class=Mock(),
            unregister_class=Mock(),
        )
        fake_bpy.app = SimpleNamespace(
            online_access=True,
            timers=Timers,
        )
        fake_bpy.context = SimpleNamespace(
            window_manager=SimpleNamespace(windows=()),
            workspace=None,
        )
        self.status_bar = StatusBarHeader
        self.timers = Timers
        self.bpy_patch = patch.dict(sys.modules, {"bpy": fake_bpy})
        self.bpy_patch.start()
        self.addCleanup(self.bpy_patch.stop)
        self.runtime = JobRuntime(
            "test_runtime.py:server",
            entrypoint_root=Path(__file__).parent,
            storage_root=Path(self.id()),
            environment={"python": "3.12"},
            namespace="example",
        )
        self.runtime.environment_ready = Mock(return_value=False)
        self.runtime.server.poll = Mock(return_value=1.0)
        self.runtime.redraw_ui = Mock()

    def callbacks(self):
        return getattr(self.status_bar.draw, "_draw_funcs", ())

    def test_unregister_removes_pending_status_bar_redraw(self):
        self.runtime.register()
        self.timers.register(self.runtime._redraw_status_bars)
        self.runtime.unregister()
        self.assertFalse(self.timers.is_registered(self.runtime._redraw_status_bars))

    def test_poll_restores_status_bar_after_blender_rebuilds_header(self):
        self.runtime.register()
        self.assertIn(self.runtime._status_bar_draw, self.callbacks())

        self.status_bar.draw = self.original_draw
        self.assertEqual(self.callbacks(), ())

        interval = self.runtime._poll_server()

        self.assertEqual(interval, 1.0)
        self.assertIn(self.runtime._status_bar_draw, self.callbacks())
        self.assertEqual(
            getattr(self.runtime._status_bar_draw, "_owner", None),
            "example",
        )

    def test_poll_does_not_duplicate_status_bar_and_unregister_stops_recovery(self):
        self.runtime.register()

        self.runtime._poll_server()
        self.runtime._poll_server()

        self.assertEqual(
            self.callbacks().count(self.runtime._status_bar_draw),
            1,
        )

        self.status_bar.draw = self.original_draw
        self.runtime.unregister()
        self.runtime._poll_server()

        self.assertNotIn(self.runtime._status_bar_draw, self.callbacks())
        self.assertFalse(self.timers.is_registered(self.runtime._poll_server))


if __name__ == "__main__":
    unittest.main()
