"""Keep cleanup bounded and serialized with active jobs."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from blendjob.server import JobServer


@pytest.fixture
def server(tmp_path):
    instance = JobServer("Test", storage_root=tmp_path)
    yield instance
    instance.close()


def create_job(server, name="a" * 32):
    directory = server.storage_root / "jobs" / name
    directory.mkdir(parents=True)
    (directory / "output.bin").write_bytes(b"output")
    server.jobs[name] = SimpleNamespace(_snapshot=lambda: {"state": "succeeded"})
    return directory


def test_preview_clear_and_history_preserve_resources(server):
    job = create_job(server)
    shared_file = server.storage_root / "shared.bin"
    shared_file.write_bytes(b"shared")
    log = server.storage_root / "server.log"
    log.write_text("log")
    resource = Mock()
    server.resources["shared_resource"] = resource
    preview = server.preview_job_files()
    assert preview["jobs"] == [job.name] and preview["bytes"] == 6
    assert job.exists() and job.name in server.jobs
    result = server.clear_job_files(preview["jobs"])
    assert result["jobs"] == [job.name] and not job.exists()
    assert job.name not in server.jobs
    assert shared_file.read_bytes() == b"shared" and log.read_text() == "log"
    resource.clear.assert_not_called()


@pytest.mark.parametrize("state", ["active", "queued"])
def test_busy_server_refuses_preview_and_clear(server, state):
    job = create_job(server)
    if state == "active":
        server.active_job_id = job.name
    else:
        server.jobs[job.name] = SimpleNamespace(_snapshot=lambda: {"state": "queued"})
    for selected in (None, {job.name}):
        with pytest.raises(RuntimeError, match="busy"):
            if selected is None:
                server.preview_job_files()
            else:
                server.clear_job_files(selected)
    assert job.exists()


def test_clear_only_removes_previewed_jobs(server):
    job = create_job(server)
    selected = set(server.preview_job_files()["jobs"])
    new = create_job(server, "b" * 32)
    result = server.clear_job_files(selected)
    assert result["jobs"] == [job.name]
    assert not job.exists() and new.exists()



def test_failed_delete_keeps_history_and_reports_partial_progress(server, monkeypatch):
    from blendjob import server as job_files

    job = create_job(server)
    monkeypatch.setattr(job_files.shutil, "rmtree", Mock(side_effect=PermissionError("locked")))
    result = server.clear_job_files([job.name])
    assert result["failed"][0]["job"] == job.name
    assert job.name in server.jobs and job.exists()


@pytest.mark.parametrize("location", ["root", "job", "nested"])
def test_redirected_paths_are_never_deleted(server, monkeypatch, location):
    from blendjob import server as job_files

    job = create_job(server)
    redirected = {"root": job.parent, "job": job, "nested": job / "output.bin"}[location]
    monkeypatch.setattr(job_files, "is_redirect", lambda path: path == redirected)
    if location == "root":
        with pytest.raises(ValueError, match="local storage"):
            server.clear_job_files([job.name])
    else:
        assert server.clear_job_files([job.name])["skipped"] == 1
    assert (job / "output.bin").read_bytes() == b"output"


def test_only_uuid_job_directories_are_candidates(server):
    root = server.storage_root / "jobs"
    (root / "user-files").mkdir(parents=True)
    (root / ("a" * 32)).write_text("not a directory")
    assert server.preview_job_files()["jobs"] == []
    assert (root / "user-files").exists()


def test_http_route_validates_requests_and_busy_state(server):
    from fastapi import HTTPException

    app = server.create_app("test")
    route = next(route.endpoint for route in app.routes if route.path == "/job-files/{action}")
    for action, payload in (("clear", {}), ("clear", {"jobs": ["../data"]}),
                            ("unknown", {})):
        with pytest.raises(HTTPException) as caught:
            route(action, payload)
        assert caught.value.status_code == 422
    job = create_job(server)
    assert route("preview", {})["jobs"] == [job.name]
    server.active_job_id = job.name
    with pytest.raises(HTTPException) as caught:
        route("clear", {"jobs": [job.name]})
    assert caught.value.status_code == 409


def test_client_previews_and_clears_files_over_http(server):
    import socket
    import threading
    import time

    import uvicorn
    from blendjob.client import JobClient

    job = create_job(server)
    unselected = create_job(server, "b" * 32)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        client = JobClient("127.0.0.1", listener.getsockname()[1])
        host = uvicorn.Server(uvicorn.Config(server.create_app("test"), log_level="error"))
        thread = threading.Thread(target=host.run, kwargs={"sockets": [listener]})
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not host.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert host.started
            preview = client.preview_job_files()
            assert set(preview["jobs"]) == {job.name, unselected.name}
            result = client.clear_job_files([job.name])
            assert result["jobs"] == [job.name]
            assert result["skipped"] == 0
            assert not job.exists() and unselected.exists()
            assert job.name not in server.jobs
            assert client.clear_job_files([])["jobs"] == []
            server.active_job_id = unselected.name
            with pytest.raises(RuntimeError, match="busy"):
                client.clear_job_files([unselected.name])
        finally:
            host.should_exit = True
            thread.join(timeout=5)
            assert not thread.is_alive()
