import argparse
import hashlib
import json
import os
import platform
import queue
import re
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

UV_VERSION = "0.11.30"
ENVIRONMENT_SCHEMA = 1
INSTALL_PHASES = 3
BASE_PACKAGES = (
    "fastapi==0.139.2",
    "uvicorn==0.51.0",
)
UV_ARTIFACTS = {
    "x86_64-pc-windows-msvc": (
        "uv-x86_64-pc-windows-msvc.zip",
        "be8d78c992312212e5cc05e9f9de3fa996db73b7c86a186dfb9231eb9f91d33e",
    ),
    "aarch64-apple-darwin": (
        "uv-aarch64-apple-darwin.tar.gz",
        "9bed3567d496d8dab84ecf7a1247551ac94ef1baaebb7b65df008dd93e9dc357",
    ),
    "x86_64-unknown-linux-gnu": (
        "uv-x86_64-unknown-linux-gnu.tar.gz",
        "04bc7d180d6138bf6dc08387acf507a823f397a98fea55da36b0ccc7fbce3b68",
    ),
}
UV_SOURCES = (
    f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}",
    f"https://cnb.cool/astral-sh/uv/-/releases/download/{UV_VERSION}",
)
PYTHON_SOURCES = (
    "https://github.com/astral-sh/python-build-standalone/releases/download",
    "https://cnb.cool/astral-sh/python-build-standalone/-/releases/download",
)
PYPI_SOURCES = (
    "https://pypi.org/simple",
    "https://pypi.tuna.tsinghua.edu.cn/simple",
)
INDEX_ENVIRONMENT_VARIABLES = (
    "UV_DEFAULT_INDEX",
    "UV_INDEX_URL",
    "UV_INDEX",
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


def write_status(path, progress, message, *, phase=None, error=None):
    message = str(message)
    if phase is not None:
        message = f"{int(phase)}/{INSTALL_PHASES} {message}"
    payload = {
        "progress": min(max(float(progress), 0.0), 1.0),
        "message": message,
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


def _format_bytes(value):
    amount = float(value)
    units = ("B", "KiB", "MiB", "GiB")
    unit = units[0]
    for unit in units:
        if amount < 1024.0 or unit == units[-1]:
            break
        amount /= 1024.0
    if unit == "B":
        return f"{int(amount)} {unit}"
    return f"{amount:.1f} {unit}"


def _download(
    url,
    destination,
    status,
    phase,
    start,
    end,
    mirror=False,
    clock=time.monotonic,
):
    request = urllib.request.Request(url, headers={"User-Agent": "BlendJob"})
    with urllib.request.urlopen(request) as response, destination.open("wb") as output:
        total = int(response.headers.get("Content-Length") or 0)
        received = 0
        started = clock()
        while True:
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            output.write(chunk)
            received += len(chunk)
            fraction = received / total if total else 0.5
            progress = start + (end - start) * min(fraction, 1.0)
            elapsed = max(clock() - started, 0.001)
            label = "Downloading uv (mirror)" if mirror else "Downloading uv"
            write_status(
                status,
                progress,
                f"{label} {_format_bytes(received / elapsed)}/s",
                phase=phase,
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


def install_uv(
    storage_root,
    status,
    phase=1,
    start=0.01,
    end=0.20,
    mirror=False,
):
    destination = uv_path(storage_root)
    if destination.is_file():
        write_status(status, end, "Installing uv ...", phase=phase)
        return destination
    target = uv_target()
    filename, checksum = UV_ARTIFACTS[target]
    destination.parent.mkdir(parents=True, exist_ok=True)
    span = end - start
    download_start = start + span / 19.0
    download_end = start + span * 17.0 / 19.0
    source = UV_SOURCES[1 if mirror else 0]
    write_status(
        status,
        start,
        "Starting uv download ...",
        phase=phase,
    )
    descriptor, archive_name = tempfile.mkstemp(suffix=f"-{filename}")
    os.close(descriptor)
    archive = Path(archive_name)
    try:
        _download(
            f"{source}/{filename}",
            archive,
            status,
            phase,
            download_start,
            download_end,
            mirror=mirror,
        )
        write_status(
            status,
            download_end,
            "Installing uv ...",
            phase=phase,
        )
        _verify(archive, checksum)
        write_status(
            status,
            end - span / 19.0,
            "Installing uv ...",
            phase=phase,
        )
        _extract_uv(archive, destination)
        write_status(status, end, "Installing uv ...", phase=phase)
        return destination
    except Exception as error:
        raise RuntimeError(f"Unable to install uv from {source}: {error}") from error
    finally:
        archive.unlink(missing_ok=True)


def _run_output_command(command, on_line, on_tick, environment=None):
    process = subprocess.Popen(
        [str(value) for value in command],
        env=environment if environment is not None else child_environment(),
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
    while True:
        try:
            line = output.get(timeout=0.2)
        except queue.Empty:
            on_tick()
            continue
        if line is None:
            break
        normalized = line.strip()
        if normalized:
            print(normalized)
            on_line(normalized)
        on_tick()
    return_code = process.wait()
    if return_code:
        raise subprocess.CalledProcessError(return_code, command)


def _elapsed_progress(start, end, elapsed, horizon=30.0):
    factor = min(elapsed / (elapsed + horizon), 0.97)
    return start + (end - start) * factor


def _rate_from_output(line):
    match = re.search(r"(\d+(?:\.\d+)?\s*[KMGT]?i?B/s)", line, re.IGNORECASE)
    return match.group(1) if match else ""


def install_python_environment(
    command,
    status,
    clock=time.monotonic,
    environment=None,
    mirror=False,
):
    phase = "checking"
    phase_started = clock()
    message = "Checking Python ..."
    write_status(status, 0.20, message, phase=2)

    def set_phase(value, text):
        nonlocal phase, phase_started, message
        if phase != value:
            phase = value
            phase_started = clock()
        message = text

    def on_line(line):
        lowered = line.lower()
        if "download" in lowered and ("python" in lowered or "cpython" in lowered):
            rate = _rate_from_output(line)
            text = "Downloading Python (mirror)" if mirror else "Downloading Python"
            if rate:
                text = f"{text} {rate}"
            set_phase("downloading", text)
        elif any(
            token in lowered
            for token in ("installing", "using cpython", "creating virtual environment")
        ):
            set_phase("installing", "Installing Python ...")

    def on_tick():
        elapsed = clock() - phase_started
        ranges = {
            "checking": (0.20, 0.21),
            "downloading": (0.21, 0.47),
            "installing": (0.47, 0.50),
        }
        start, end = ranges[phase]
        write_status(
            status,
            _elapsed_progress(start, end, elapsed),
            message,
            phase=2,
        )

    _run_output_command(command, on_line, on_tick, environment=environment)
    write_status(status, 0.50, "Installing Python ...", phase=2)


def install_packages(
    command,
    status,
    clock=time.monotonic,
    environment=None,
    mirror=False,
):
    phase = "resolving"
    phase_started = clock()
    total = 0
    downloads = set()
    suffix = " (mirror)" if mirror else ""
    message = f"Resolving packages{suffix} ..."
    write_status(status, 0.50, message, phase=3)

    def set_phase(value, text):
        nonlocal phase, phase_started, message
        if phase != value:
            phase = value
            phase_started = clock()
        message = text

    def on_line(line):
        nonlocal total
        resolved = re.search(r"Resolved\s+(\d+)\s+packages?", line, re.IGNORECASE)
        if resolved:
            total = int(resolved.group(1))
        downloading = re.search(r"Downloading\s+([^\s(]+)", line, re.IGNORECASE)
        if downloading:
            downloads.add(downloading.group(1))
            denominator = max(total, len(downloads))
            set_phase(
                "downloading",
                f"Downloading packages{suffix} {len(downloads)}/{denominator}",
            )
        lowered = line.lower()
        if lowered.startswith(("prepared ", "installed ")):
            set_phase("installing", "Installing packages ...")

    def on_tick():
        elapsed = clock() - phase_started
        if phase == "resolving":
            progress = _elapsed_progress(0.50, 0.53, elapsed)
        elif phase == "downloading":
            if total:
                progress = 0.53 + 0.41 * min(len(downloads) / total, 1.0)
            else:
                progress = _elapsed_progress(0.53, 0.94, elapsed)
        else:
            progress = _elapsed_progress(0.94, 0.98, elapsed)
        write_status(
            status,
            progress,
            message,
            phase=3,
        )

    _run_output_command(
        command,
        on_line,
        on_tick,
        environment=environment,
    )
    write_status(status, 0.98, "Installing packages ...", phase=3)


def install_environment_variables(mirror=False):
    environment = child_environment()
    for name in (*INDEX_ENVIRONMENT_VARIABLES, "UV_PYTHON_INSTALL_MIRROR"):
        environment.pop(name, None)
    source = 1 if mirror else 0
    environment["UV_PYTHON_INSTALL_MIRROR"] = PYTHON_SOURCES[source]
    environment["UV_INDEX_URL"] = PYPI_SOURCES[source]
    return environment


def install(storage_root, environment, status, source="official"):
    if source not in {"official", "mirror"}:
        raise ValueError(f"Unknown install source: {source}")
    mirror = source == "mirror"
    environment = normalized_environment(environment)
    storage_root = Path(storage_root).resolve()
    storage_root.mkdir(parents=True, exist_ok=True)
    venv = storage_root / ".venv"
    manifest = storage_root / "manifest.json"
    expected_digest = environment_digest(environment)
    write_status(status, 0.0, "Starting installation ...")
    uv = install_uv(storage_root, status, mirror=mirror)
    manifest.unlink(missing_ok=True)
    command_environment = install_environment_variables(mirror)
    install_python_environment(
        [uv, "venv", "--clear", "--python", environment["python"], venv],
        status,
        environment=command_environment,
        mirror=mirror,
    )
    packages = resolved_packages(environment)
    install_packages(
        [uv, "pip", "install", "--python", environment_python(storage_root), *packages],
        status,
        environment=command_environment,
        mirror=mirror,
    )
    write_status(status, 0.98, "Verifying installation ...")
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
    write_status(status, 1.0, "Verifying installation ...")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--storage-root", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--status", required=True)
    parser.add_argument(
        "--source",
        choices=("official", "mirror"),
        default="official",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        environment = json.loads(Path(args.config).read_text(encoding="utf-8"))
        install(args.storage_root, environment, args.status, source=args.source)
    except Exception as error:
        write_status(
            args.status,
            1.0,
            "Environment installation failed",
            error=error,
        )
        raise


if __name__ == "__main__":
    main()
