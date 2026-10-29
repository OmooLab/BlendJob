"""Lock, copy and clean up Server storage roots."""
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import time
from pathlib import Path

def read_json(path):
    try:
        value = Path(path).read_text(encoding="utf-8")
        data = json.loads(value)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json(path, data):
    path = Path(path)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    for attempt in range(20):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.005)


def file_checksum(path, cancel_check=lambda: False):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            if cancel_check():
                raise RuntimeError("Operation cancelled")
            digest.update(chunk)
    return digest.hexdigest()


def storage_state_directory():
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
        return base / "BlendJob/storage"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/BlendJob/storage"
    base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
    return base / "blendjob/storage"


def storage_state_path(root, suffix):
    identity = os.path.normcase(str(Path(root).resolve()))
    key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return storage_state_directory() / f"{key}.{suffix}"


def check_storage_location(target):
    target = Path(target)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        container = target if target.is_dir() else target.parent
        with tempfile.TemporaryDirectory(prefix=f"{target.name}-write-test-", dir=container) as directory:
            (Path(directory) / "write-test").write_bytes(b"")
    except PermissionError as error:
        raise RuntimeError(
            f"Cannot write to Server folder: {target}. Choose a folder with write access."
        ) from error


class StorageLock:
    """Hold a process-wide lease outside the movable directory."""

    def __init__(self):
        self.stream = None
        self.root = None

    def acquire(self, root):
        root = Path(root).resolve()
        if self.stream is not None:
            if self.root != root:
                raise RuntimeError("Another Server Location is in use")
            return
        path = storage_state_path(root, "lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        stream = path.open("a+b")
        try:
            stream.seek(0)
            if not stream.read(1):
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            stream.close()
            raise RuntimeError(f"Another Blender process is using {root}") from error
        self.stream, self.root = stream, root

    def release(self):
        if self.stream is not None:
            self.stream.close()
            self.stream, self.root = None, None


def remove_server_directory(path):
    path = Path(path)
    if path.resolve() != path.absolute():
        raise ValueError(f"Cannot remove a linked Server directory: {path}")
    def retry(function, filename, _error):
        item = Path(filename)
        if item.resolve() != path and path not in item.resolve().parents:
            raise ValueError("Server cleanup escaped its directory")
        item.chmod(item.stat().st_mode | stat.S_IWRITE)
        function(filename)
    shutil.rmtree(path, onerror=retry)


class StorageMigration:
    """Copy storage into an empty target and discard it on failure."""

    def __init__(self, source, target, progress, cancel_check):
        self.source = Path(source).resolve()
        self.target = Path(target).resolve()
        self.progress = progress
        self.cancel_check = cancel_check
        self.owns_target = False
        if self.source == self.target or self.source in self.target.parents or self.target in self.source.parents:
            raise ValueError("Choose a different, non-nested Server location")

    def copy(self):
        if self.target.exists() and (not self.target.is_dir() or any(self.target.iterdir())):
            raise ValueError(f"The target must be empty: {self.target}")
        if not self.source.is_dir():
            raise ValueError(f"Server folder does not exist: {self.source}")
        check_storage_location(self.target)
        self.owns_target = True
        files = []
        directories = []
        for directory, dirs, names in os.walk(self.source):
            directories.append(Path(directory).relative_to(self.source))
            for name in dirs[:]:
                entry = Path(directory) / name
                if entry.is_symlink():
                    link = entry.resolve()
                    if link != self.source and self.source not in link.parents:
                        raise ValueError(f"External directory links cannot be migrated: {entry}")
                    dirs.remove(name)
                    files.append(entry)
                elif getattr(entry.stat(), "st_file_attributes", 0) & 0x400:
                    raise ValueError(f"Linked directories cannot be migrated: {entry}")
            files.extend((Path(directory) / name for name in names))
        total = sum(path.stat().st_size for path in files)
        if shutil.disk_usage(self.target.parent).free < total:
            raise RuntimeError("Not enough free space at the Server location")
        for relative in directories:
            (self.target / relative).mkdir(parents=True, exist_ok=True)
        copied = 0
        for path in files:
            if self.cancel_check():
                raise RuntimeError("Migration cancelled")
            relative = path.relative_to(self.source)
            destination = self.target / relative
            if path.is_symlink():
                link = path.resolve()
                if link == self.source or self.source in link.parents:
                    link = self.target / link.relative_to(self.source)
                destination.symlink_to(link, target_is_directory=path.is_dir())
                continue
            digest = hashlib.sha256()
            with path.open("rb") as reader, destination.open("wb") as writer:
                while chunk := reader.read(4 * 1024 * 1024):
                    if self.cancel_check():
                        raise RuntimeError("Migration cancelled")
                    writer.write(chunk)
                    digest.update(chunk)
                    copied += len(chunk)
                    self.progress(0.75 * copied / max(total, 1), "Migrating Server files")
            shutil.copystat(path, destination)
            if digest.hexdigest() != file_checksum(destination, self.cancel_check):
                raise RuntimeError(f"Copied file failed verification: {relative}")

    def cleanup_source(self):
        self.progress(0.95, "Removing the old Server folder")
        if self.source.exists():
            remove_server_directory(self.source)


    def discard_copy(self):
        if self.owns_target:
            if self.target.exists():
                remove_server_directory(self.target)
            self.owns_target = False
