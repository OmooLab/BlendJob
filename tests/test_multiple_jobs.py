"""Cover queue ordering, cancellation and maintenance through runtime.submit."""
from unittest.mock import Mock
import pytest


def test_results_are_delivered_in_submission_order(job_runtime):
    runtime = job_runtime
    events = []
    a = runtime.submit("work", on_done=lambda job: events.append("a"))
    b = runtime.submit("work", on_done=lambda job: events.append("b"))
    runtime.server.states[b._job_id] = {"state": "succeeded"}
    runtime._tick()
    assert events == []
    runtime.server.states[a._job_id] = {"state": "succeeded"}
    runtime._tick()
    assert events == ["a", "b"] and not runtime.jobs


def test_cancel_one_job_discards_racing_success(job_runtime):
    runtime = job_runtime
    a = runtime.submit("work")
    b = runtime.submit("work")
    b.cancel()
    runtime.server.cancel.assert_called_once_with(b._job_id)
    runtime.server.states[b._job_id] = {"state": "succeeded"}
    runtime._tick()
    assert b.done and b.state == "cancelled" and b.result is None
    assert runtime.jobs == [a]


@pytest.mark.parametrize("action", ["stop", "restart", "migrate"])
def test_barrier_waits_for_callbacks_and_release(job_runtime, tmp_path, action):
    runtime = job_runtime
    events = []
    a = runtime.submit("work", on_done=lambda job: events.append("cleanup"))
    runtime.server.mark_job_complete.side_effect = lambda identifier: events.append("release")
    parameters = {"target": str(tmp_path / "new")} if action == "migrate" else {}
    barrier = getattr(runtime, action)(**parameters)
    assert not runtime.can_submit()
    runtime.server.action.assert_not_called()
    runtime.server.states[a._job_id] = {"state": "cancelled"}
    runtime._tick()
    assert events == ["cleanup", "release"]
    assert runtime.server.action.call_args.args == (action,)
    assert barrier.identifier
    assert barrier not in runtime.jobs


def test_callback_failure_does_not_block_following_job(job_runtime):
    runtime = job_runtime
    a = runtime.submit("work", on_done=Mock(side_effect=ValueError("Bad result")))
    b = runtime.submit("work")
    for job in (a, b):
        runtime.server.states[job._job_id] = {"state": "succeeded"}
    runtime._tick()
    assert a.state == "failed" and a.error == "Bad result"
    assert b.done and b.state == "succeeded" and not runtime.jobs


def test_callback_can_submit_next_job_without_reentrancy(job_runtime):
    runtime = job_runtime
    next_jobs = []
    a = runtime.submit("work", on_done=lambda job: next_jobs.append(runtime.submit("work")))
    runtime.server.states[a._job_id] = {"state": "succeeded"}
    runtime._tick()
    assert a.done and next_jobs[0]._job_id == ""
    runtime._tick()
    assert next_jobs[0]._job_id


def test_process_failure_releases_every_job(job_runtime):
    runtime = job_runtime
    jobs = [runtime.submit("work") for _ in range(2)]
    runtime.server.status.side_effect = RuntimeError("Control process exited")
    runtime._tick()
    assert all(job.done and job.state == "failed" for job in jobs)
    assert not runtime.jobs


def test_disable_stops_worker_before_releasing_inputs(job_runtime):
    runtime = job_runtime
    events = []
    jobs = [runtime.submit("work", on_done=lambda job: events.append("cleanup")) for _ in range(2)]
    runtime.server.detach.side_effect = lambda: events.append("stop")
    runtime.disable()
    assert events == ["stop", "cleanup", "cleanup"]
    assert all(job.done for job in jobs) and not runtime.jobs


def test_progress_and_cancel_rows_are_independent(job_runtime):
    runtime = job_runtime
    a = runtime.submit("work")
    b = runtime.submit("work")
    runtime.server.states[b._job_id] = {"state": "queued", "progress": 0, "message": "Job queued"}
    runtime._tick()
    rows = [Mock(), Mock()]
    layout = Mock()
    layout.row.side_effect = rows
    runtime.draw_jobs(layout)
    assert rows[0].progress.call_args.kwargs["factor"] == .25
    assert rows[1].progress.call_args.kwargs["factor"] == 0
    assert rows[0].row.return_value.operator.return_value.job_id == a.id
    assert rows[1].row.return_value.operator.return_value.job_id == b.id
