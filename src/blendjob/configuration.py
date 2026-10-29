"""Describe an environment and build commands for its storage directory."""
import json
import os
import subprocess
import sys
from pathlib import Path

from .entrypoint import normalized_entrypoint
from .environment import environment_digest, normalized_environment, environment_python
from .storage import read_json


class ServerConfiguration:
    def __init__(self, server_entrypoint, environment, *, entrypoint_root=None):
        self.server_entrypoint = normalized_entrypoint(server_entrypoint, root=entrypoint_root)
        self.environment = normalized_environment(environment)
        self.environment_hash = environment_digest(self.environment)

    def environment_ready(self, root):
        return (environment_python(root).is_file()
                and read_json(Path(root) / "manifest.json").get("environment_hash") == self.environment_hash)

    def inspect_installation(self, root):
        root = Path(root)
        recognized = bool(read_json(root / "manifest.json") or read_json(root / "environment.json"))
        if root.exists() and (not root.is_dir() or (any(root.iterdir()) and not recognized)):
            raise ValueError(f"The target is not a BlendJob installation: {root}")
        return {"environment_ready": self.environment_ready(root)}

    def install_command(self, root, source="official"):
        root.mkdir(parents=True, exist_ok=True)
        config = root / "environment.json"
        config.write_text(json.dumps(self.environment, indent=2), encoding="utf-8")
        installer = Path(__file__).with_name("launcher") / "install.py"
        return [sys.executable, str(installer), "--storage-root", str(root),
                "--config", str(config), "--status", str(root / "install-status.json"),
                "--source", source]

    def server_command(self, root, port, instance_id):
        launcher = Path(__file__).with_name("launcher") / "runner.py"
        return [str(environment_python(root)), "-u", str(launcher),
                "--entrypoint", self.server_entrypoint, "--storage-root", str(root),
                "--host", "127.0.0.1", "--port", str(port),
                "--instance-id", instance_id, "--parent-pid", str(os.getpid())]

    def server_controller(self, root, *, directory_lock=None):
        from .controller import ServerController

        return ServerController(
            lambda port, instance_id: self.server_command(root, port, instance_id),
            cwd_factory=lambda: root, log_path_factory=lambda: root / "server.log",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            directory_lock=directory_lock,
        )
