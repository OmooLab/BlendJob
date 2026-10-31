import sys
import tempfile
import unittest
import runpy
import pytest
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

import blendjob
from blendjob.runtime import JobRuntime
from blendjob.operator import JobOperator


class JobRuntimeEnvironmentTest(unittest.TestCase):
    def test_install_commands_include_explicit_source(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "example.py").touch()
            runtime = JobRuntime("example", server=str(Path(directory) / "example.py") + ":server", storage_root=directory, environment={"python": "3.12"})

            official = runtime._config.install_command(runtime.storage_root())
            mirror = runtime._config.install_command(runtime.storage_root(), source="mirror")

        self.assertEqual(official[-2:], ["--source", "official"])
        self.assertEqual(mirror[-2:], ["--source", "mirror"])

    def test_storage_reads_do_not_create_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            entrypoint = Path(directory) / "example.py"
            entrypoint.touch()
            target = Path(directory) / "server"
            runtime = JobRuntime("example", server=f"{entrypoint}:server", storage_root=target, environment={"python": "3.12"})
            self.assertEqual(runtime.storage_root(), target)
            self.assertEqual(runtime.state(), "UNAVAILABLE")
            self.assertFalse(target.exists())
            runtime._config.install_command(runtime.storage_root())
            self.assertTrue((target / "environment.json").is_file())

    def test_setup_target_inspection_and_execution_reject_unrelated_data(self):
        from blendjob.storage import write_json
        from blendjob.environment import environment_python
        with tempfile.TemporaryDirectory() as directory:
            entrypoint = Path(directory) / "app.py"
            entrypoint.touch()
            runtime = JobRuntime("example", server=f"{entrypoint}:server", storage_root=Path(directory) / "current", environment={"python": "3.12"})
            target = Path(directory) / "selected"
            self.assertFalse(runtime.inspect_installation(target)["environment_ready"])
            self.assertFalse(target.exists())
            target.mkdir()
            unrelated = target / "keep.txt"
            unrelated.write_text("keep")
            with self.assertRaisesRegex(ValueError, "not a BlendJob"):
                runtime.inspect_installation(target)
            with self.assertRaisesRegex(ValueError, "not a BlendJob"):
                runtime.setup(target)
            self.assertEqual(unrelated.read_text(), "keep")
            write_json(target / "environment.json", {"python": "3.12"})
            self.assertFalse(runtime.inspect_installation(target)["environment_ready"])
            python = environment_python(target)
            python.parent.mkdir(parents=True)
            python.touch()
            write_json(target / "manifest.json", {"environment_hash": runtime._config.environment_hash})
            self.assertTrue(runtime.inspect_installation(target)["environment_ready"])
            self.assertEqual(runtime.state(), "UNAVAILABLE")



    def test_job_runtime_is_the_public_runtime_class(self):
        self.assertIs(blendjob.JobRuntime, JobRuntime)
        self.assertFalse(hasattr(blendjob, "BlenderJobRuntime"))
        self.assertEqual(
            set(blendjob.__all__),
            {"JobResult", "JobRuntime", "JobServer"},
        )
        self.assertFalse(hasattr(blendjob, "JobOperatorState"))
        self.assertFalse(hasattr(blendjob, "ServerController"))
        from blendjob.server import JobServer
        self.assertIs(blendjob.JobServer, JobServer)
        self.assertFalse(hasattr(blendjob, "JobContext"))

    def test_server_runner_uses_bundled_launcher(self):
        from blendjob.configuration import ServerConfiguration
        config = SimpleNamespace(server_entrypoint="addon/server/__init__.py:server")
        with patch("blendjob.configuration.environment_python", return_value=Path("environment/python")):
            command = ServerConfiguration.server_command(config, Path("storage"), 8123, "instance")

        self.assertEqual(command[:2], [str(Path("environment/python")), "-u"])
        self.assertEqual(Path(command[2]).name, "runner.py")
        self.assertEqual(Path(command[2]).parent.name, "launcher")
        self.assertEqual(command[3], "--entrypoint")
        self.assertNotIn("blendjob.runner", command)
        self.assertIn("addon/server/__init__.py:server", command)



def test_defaults_follow_factory_file_without_importing_server(tmp_path, monkeypatch):
    addon = tmp_path / "addon"
    (addon / "server").mkdir(parents=True)
    entry = addon / "server" / "__init__.py"
    entry.write_text("raise AssertionError('Do not import the backend in Blender')")
    factory = addon / "runtime.py"
    factory.write_text("from blendjob import JobRuntime\ndef create():\n    return JobRuntime('example', environment={'python': '3.12'})\n")
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    create = runpy.run_path(str(factory))["create"]
    monkeypatch.chdir(tmp_path)
    runtime = create()
    assert runtime._config.server_entrypoint == f"{entry.resolve()}:server"
    assert runtime.storage_root() == home / ".example"
    assert not home.exists()
    assert not runtime._registered
    assert runtime.jobs == []


@pytest.mark.parametrize("server", ["custom", "custom/__init__.py", "custom:service"])
def test_relative_server_override_and_dynamic_storage(tmp_path, server):
    (tmp_path / "custom").mkdir()
    entry = tmp_path / "custom" / "__init__.py"
    entry.touch()
    location = [tmp_path / "first"]
    callback = Mock()
    scope = {"__file__": str(tmp_path / "runtime.py"), "JobRuntime": JobRuntime,
             "server": server, "storage": lambda: location[0], "callback": callback}
    exec("runtime = JobRuntime('example', server=server, environment={'python': '3.12'}, storage_root=storage, post_setup=callback)", scope)
    runtime = scope["runtime"]
    attribute = "service" if server.endswith(":service") else "server"
    assert runtime._config.server_entrypoint == f"{entry}:" + attribute
    assert runtime.storage_root() == location[0]
    location[0] = tmp_path / "second"
    assert runtime.storage_root() == location[0]
    assert runtime.post_setup is callback
    callback.assert_not_called()


def test_absolute_server_without_caller_file(tmp_path):
    entry = tmp_path / "worker.py"
    entry.touch()
    scope = {"JobRuntime": JobRuntime, "server": entry}
    exec("runtime = JobRuntime('example', server=server, environment={'python': '3.12'})", scope)
    assert scope["runtime"]._config.server_entrypoint == f"{entry}:server"
    with pytest.raises(ValueError, match="caller file"):
        exec("JobRuntime('example', environment={'python': '3.12'})", scope)


@pytest.mark.parametrize("namespace", ["", "Example", "../other", "a/b", "a.b", "a-b", " a", "1test", None])
def test_invalid_namespace_is_rejected(namespace):
    with pytest.raises(ValueError, match="namespace"):
        JobRuntime(namespace, environment={"python": "3.12"})
