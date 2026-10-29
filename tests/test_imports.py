"""Keep Blender imports independent from service and installer implementations."""
import subprocess
import sys
from pathlib import Path


def test_blender_runtime_does_not_load_backend_implementations(tmp_path):
    source = Path(__file__).resolve().parents[1] / "src"
    entrypoint = tmp_path / "app.py"
    entrypoint.touch()
    script = '''import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from blendjob import JobRuntime, JobResult
runtime = JobRuntime(sys.argv[2] + ':server', storage_root=Path(sys.argv[3]),
                     environment={'python': '3.12'}, namespace='test')
assert not runtime.environment_ready()
runtime.setup_controller()
runtime.server.poll(available=False)
for name in ('blendjob.client', 'blendjob.server', 'blendjob.controller',
             'blendjob.control', 'blendjob.runner', 'blendjob.installer.environment'):
    assert name not in sys.modules, name
'''
    result = subprocess.run([sys.executable, "-I", "-S", "-c", script, str(source),
                             str(entrypoint), str(tmp_path / "storage")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
