import argparse
import hashlib
import json
import os
import platform
import queue
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

UV_VERSION = "0.11.3"
ENVIRONMENT_SCHEMA = 1
BASE_PACKAGES = (
    "fastapi==0.139.2",
    "uvicorn==0.51.0",
)
UV_ARTIFACTS = {
    "x86_64-pc-windows-msvc": (
        "uv-x86_64-pc-windows-msvc.zip",
        "ae681c0aaec7cc96af184648cb88d73f8393ed60fa5880abdd6bdb910f9b227c",
    ),
    "aarch64-apple-darwin": (
        "uv-aarch64-apple-darwin.tar.gz",
        "2bc3d0c7bf2bd08325b1e170abac6f7e5b3346e1d4eab3370d17cefec934996f",
    ),
    "x86_64-unknown-linux-gnu": (
        "uv-x86_64-unknown-linux-gnu.tar.gz",
        "c0f3236f146e55472663cfbcc9be3042a9f1092275bbe3fe2a56a6cbfd3da5ce",
    ),
}
UV_SOURCES = (
    f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}",
    f"https://mirror.omoolab.xyz/uv/{UV_VERSION}",
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


def uv_path(storage_root):
    filename = "uv.exe" if os.name == "nt" else "uv"
    return Path(storage_root) / "tools" / filename


def uv_target():
    machine = platform.machine().lower()
    if sys.platform == "win32" and machine in {"amd64", "x86_64"}:
        return "x86_64-pc-windows-msvc"
    if sys.platform == "darwin" and machine in {"arm64", "aarch64"}:
        return "aarch64-apple-darwin"
    if sys.platform.startswith("linux") and machine in {"amd64", "x86_64"}:
        return "x86_64-unknown-linux-gnu"
    raise RuntimeError(f"Unsupported uv platform: {sys.platform} {machine}")


def write_status(path, progress, message, *, stage, stages, error=None):
    payload = {
        "progress": min(max(float(progress), 0.0), 1.0),
        "message": str(message),
        "stage": int(stage),
        "stages": int(stages),
        "stage_label": "Environment",
    }
    if error:
        payload["error"] = str(error)
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def child_environment():
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8:backslashreplace"
    environment["PYTHONUTF8"] = "1"
    for name, value in urllib.request.getproxies().items():
        environment.setdefault(f"{name.upper()}_PROXY", value)
    return environment


def _download(url, destination, status, stage, stages, start, end):
    request = urllib.request.Request(url, headers={"User-Agent": "BlendJob"})
    with urllib.request.urlopen(request) as response, destination.open("wb") as output:
        total = int(response.headers.get("Content-Length") or 0)
        received = 0
        while True:
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            output.write(chunk)
            received += len(chunk)
            fraction = received / total if total else 0.5
            progress = start + (end - start) * min(fraction, 1.0)
            write_status(
                status,
                progress,
                "Installing uv",
                stage=stage,
                stages=stages,
            )


def _verify(path, checksum):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest().lower() != checksum.lower():
        raise RuntimeError("uv archive checksum does not match")


def _extract_uv(archive, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        extracted = Path(directory)
        if archive.name.endswith(".zip"):
            with zipfile.ZipFile(archive) as package:
                package.extractall(extracted)
        else:
            with tarfile.open(archive, "r:gz") as package:
                package.extractall(extracted)
        candidates = tuple(extracted.rglob(destination.name))
        if not candidates:
            raise RuntimeError("uv executable was not found in its archive")
        shutil.copy2(candidates[0], destination)
    if os.name != "nt":
        destination.chmod(destination.stat().st_mode | 0o111)


def install_uv(storage_root, status, stage, stages, start=0.0, end=0.15):
    destination = uv_path(storage_root)
    if destination.is_file():
        write_status(status, end, "uv is ready", stage=stage, stages=stages)
        return destination
    target = uv_target()
    filename, checksum = UV_ARTIFACTS[target]
    errors = []
    destination.parent.mkdir(parents=True, exist_ok=True)
    for source in UV_SOURCES:
        descriptor, archive_name = tempfile.mkstemp(suffix=f"-{filename}")
        os.close(descriptor)
        archive = Path(archive_name)
        try:
            _download(
                f"{source}/{filename}",
                archive,
                status,
                stage,
                stages,
                start,
                end * 0.8,
            )
            _verify(archive, checksum)
            _extract_uv(archive, destination)
            write_status(status, end, "uv is ready", stage=stage, stages=stages)
            return destination
        except Exception as error:
            errors.append(f"{source}: {error}")
        finally:
            archive.unlink(missing_ok=True)
    raise RuntimeError("Unable to install uv: " + "; ".join(errors))


def run_command(command, environment=None):
    subprocess.check_call(
        [str(value) for value in command],
        env=environment or child_environment(),
    )


def install_packages(
    command,
    status,
    stage,
    stages,
    start=0.22,
    end=0.98,
    clock=time.monotonic,
):
    process = subprocess.Popen(
        [str(value) for value in command],
        env=child_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = queue.Queue()

    def read_output():
        for line in process.stdout:
            output.put(line)
        output.put(None)

    threading.Thread(target=read_output, daemon=True).start()
    started = clock()
    last_line = "Installing packages"
    while True:
        try:
            line = output.get(timeout=0.2)
        except queue.Empty:
            line = ""
        if line is None:
            break
        if line.strip():
            last_line = line.strip()
            print(last_line)
        elapsed = clock() - started
        fraction = min(elapsed / (elapsed + 30.0), 0.97)
        write_status(
            status,
            start + (end - start) * fraction,
            last_line,
            stage=stage,
            stages=stages,
        )
    return_code = process.wait()
    if return_code:
        raise subprocess.CalledProcessError(return_code, command)


def install(storage_root, environment, status, stages=1):
    environment = normalized_environment(environment)
    storage_root = Path(storage_root).resolve()
    storage_root.mkdir(parents=True, exist_ok=True)
    venv = storage_root / ".venv"
    manifest = storage_root / "manifest.json"
    expected_digest = environment_digest(environment)
    uv = install_uv(storage_root, status, 1, stages)
    manifest.unlink(missing_ok=True)
    write_status(status, 0.16, "Creating Python Environment", stage=1, stages=stages)
    run_command([uv, "venv", "--clear", "--python", environment["python"], venv])
    write_status(status, 0.22, "Installing packages", stage=1, stages=stages)
    packages = resolved_packages(environment)
    install_packages(
        [uv, "pip", "install", "--python", environment_python(storage_root), *packages],
        status,
        1,
        stages,
    )
    manifest.write_text(
        json.dumps(
            {
                "environment_hash": expected_digest,
                "python": environment["python"],
                "packages": packages,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    write_status(status, 1.0, "Environment is ready", stage=1, stages=stages)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--storage-root", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--status", required=True)
    parser.add_argument("--stages", type=int, default=1)
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        environment = json.loads(Path(args.config).read_text(encoding="utf-8"))
        install(args.storage_root, environment, args.status, args.stages)
    except Exception as error:
        write_status(
            args.status,
            1.0,
            "Environment installation failed",
            stage=1,
            stages=args.stages,
            error=error,
        )
        raise


if __name__ == "__main__":
    main()
