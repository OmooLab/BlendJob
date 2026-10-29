"""Normalize, inspect and validate Python environments."""
import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path


ENVIRONMENT_SCHEMA = 1


BASE_PACKAGES = (
    "fastapi==0.139.2",
    "uvicorn==0.51.0",
)


def normalized_environment(environment):
    """Validate and normalize a public Environment dictionary."""
    if not isinstance(environment, dict):
        raise TypeError("environment must be a dict")
    python = str(environment.get("python", "")).strip()
    if not python:
        raise ValueError("environment['python'] is required")
    packages = environment.get("packages", ())
    platform_packages = environment.get("platform_packages", {})
    if not isinstance(packages, (list, tuple)):
        raise TypeError("environment['packages'] must be a list")
    if not isinstance(platform_packages, dict):
        raise TypeError("environment['platform_packages'] must be a dict")
    normalized_platforms = {}
    for name, values in platform_packages.items():
        if not isinstance(values, (list, tuple)):
            raise TypeError(f"platform_packages[{name!r}] must be a list")
        normalized_platforms[str(name)] = [str(value) for value in values]
    return {
        "python": python,
        "packages": [str(value) for value in packages],
        "platform_packages": normalized_platforms,
    }


def environment_digest(environment):
    payload = json.dumps(
        {
            "schema": ENVIRONMENT_SCHEMA,
            "base_packages": BASE_PACKAGES,
            "environment": normalized_environment(environment),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def platform_name():
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def resolved_packages(environment):
    environment = normalized_environment(environment)
    platform_packages = environment["platform_packages"]
    selected = platform_packages.get(
        platform_name(),
        platform_packages.get("default", ()),
    )
    return [*BASE_PACKAGES, *environment["packages"], *selected]


def environment_python(storage_root):
    directory = Path(storage_root) / ".venv"
    if os.name == "nt":
        return directory / "Scripts" / "python.exe"
    return directory / "bin" / "python"


def validate_environment(root, environment, source=None):
    script = (
        "import json,sys,platform,importlib.metadata as m; "
        "print(json.dumps({'version':list(sys.version_info[:2]),"
        "'platform':sys.platform,'machine':platform.machine(),"
        "'packages':{name:m.version(name) for name in json.loads(sys.argv[1])},"
        "'paths':[sys.prefix,sys.base_prefix,*sys.path]}))"
    )
    packages = dict(package.split("==", 1) for package in resolved_packages(environment))
    process = subprocess.run(
        [str(environment_python(root)), "-I", "-c", script, json.dumps(list(packages))],
        capture_output=True, text=True, timeout=60,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        result = json.loads(process.stdout)
    except ValueError as error:
        raise RuntimeError("The Python environment cannot run; complete installation first") from error
    expected = [int(part) for part in environment["python"].split(".")[:2]]
    if (
        process.returncode or result["version"] != expected
        or result["platform"] != sys.platform
        or result["machine"].lower() != platform.machine().lower()
        or result["packages"] != packages
    ):
        raise RuntimeError("The Python environment does not match this platform or its dependencies")
    if source is not None:
        original = Path(source).resolve()
        for value in result["paths"]:
            path = Path(value).resolve()
            if path == original or original in path.parents:
                raise RuntimeError("The relocated Python still references the old Server folder")
