import tempfile
import threading
import time
import unittest
from pathlib import Path

from blendjob import JobServer


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.storage_root = Path(self.temporary_directory.name)

    def wait_for_job(self, context, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            snapshot = context.snapshot()
            if snapshot["state"] in {"succeeded", "failed", "cancelled"}:
                return snapshot
            time.sleep(0.01)
        self.fail(f"Job did not finish: {context.snapshot()}")

    def test_runs_jobs_in_fifo_order(self):
        server = JobServer("Test", storage_root=self.storage_root)
        first_started = threading.Event()
        release_first = threading.Event()
        order = []

        @server.job("example")
        def example(_context, parameters):
            order.append(parameters["name"])
            if parameters["name"] == "first":
                first_started.set()
                release_first.wait(1.0)

        first = server.submit("example", {"name": "first"})
        self.assertTrue(first_started.wait(1.0))
        second = server.submit("example", {"name": "second"})
        self.assertEqual(server.snapshot("instance")["queued_jobs"], 1)
        release_first.set()
        self.wait_for_job(first)
        self.wait_for_job(second)
        self.assertEqual(order, ["first", "second"])
        server.close()

    def test_queued_job_can_be_cancelled(self):
        server = JobServer("Test", storage_root=self.storage_root)
        first_started = threading.Event()
        release_first = threading.Event()
        executed = []

        @server.job("example")
        def example(_context, parameters):
            executed.append(parameters["name"])
            if parameters["name"] == "first":
                first_started.set()
                release_first.wait(1.0)

        first = server.submit("example", {"name": "first"})
        self.assertTrue(first_started.wait(1.0))
        second = server.submit("example", {"name": "second"})
        server.cancel(second.job_id)
        release_first.set()
        self.wait_for_job(first)
        self.assertEqual(self.wait_for_job(second)["state"], "cancelled")
        self.assertEqual(executed, ["first"])
        server.close()

    def test_job_has_opaque_directory_and_progress(self):
        server = JobServer("Test", storage_root=self.storage_root)

        @server.job("example")
        def example(context, _parameters):
            context.progress(0.5, "Half way")
            return {"value": 42}

        context = server.submit("example", {})
        status = self.wait_for_job(context)
        self.assertRegex(context.job_id, r"^[0-9a-f]{32}$")
        self.assertEqual(context.directory, self.storage_root / "jobs" / context.job_id)
        self.assertEqual(status["result"], {"value": 42})
        server.close()

    def test_resource_lifecycle(self):
        events = []

        class Resource:
            def snapshot(self):
                return {"loaded": True}

            def clear(self):
                events.append("clear")

            def close(self):
                events.append("close")

        server = JobServer("Test", storage_root=self.storage_root)
        server.add_resource("example", Resource())
        self.assertTrue(server.clear_resource("example"))
        server.close()
        self.assertEqual(events, ["clear", "close"])


if __name__ == "__main__":
    unittest.main()

