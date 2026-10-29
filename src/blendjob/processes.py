"""Inspect and terminate owned processes without loading service code."""
import os
import subprocess
import time


def terminate_process_tree(process):
    """Terminate the owned process tree or isolated process group."""
    if os.name == "nt":
        result = subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode and process.poll() is None:
            raise RuntimeError(f"Unable to stop process tree {process.pid}")
    else:
        if getattr(process, "_blendjob_session", False):
            import signal
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.terminate()


class RegisteredServerProcess:
    """Wait for a verified Server recovered after a Runtime reload."""

    def __init__(self, pid):
        self.pid = pid

    def poll(self):
        if os.name == "nt":
            import ctypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.restype = ctypes.c_void_p
            handle = kernel.OpenProcess(0x100000, False, self.pid)
            if not handle:
                return 0
            try:
                kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
                return None if kernel.WaitForSingleObject(handle, 0) == 258 else 0
            finally:
                kernel.CloseHandle.argtypes = [ctypes.c_void_p]
                kernel.CloseHandle(handle)
        try:
            pid, _status = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                return 0
        except ChildProcessError:
            pass
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            return 0
        return None

    def wait(self, timeout):
        deadline = time.monotonic() + timeout
        while self.poll() is None:
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(str(self.pid), timeout)
            time.sleep(0.05)

    def terminate(self):
        import signal
        os.kill(self.pid, signal.SIGTERM)

    def kill(self):
        import signal
        os.kill(self.pid, getattr(signal, "SIGKILL", signal.SIGTERM))
