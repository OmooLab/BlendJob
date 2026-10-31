"""Run cleanup through a real Blender window; background bpy cannot test popup ownership."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


BLENDER = os.environ.get("BLENDJOB_TEST_BLENDER")


@pytest.mark.skipif(not BLENDER, reason="Set BLENDJOB_TEST_BLENDER to run the real window regression")
@pytest.mark.parametrize("mode", ["empty", "confirm", "cancel"])
def test_cleanup_popup_lifetime(tmp_path, mode):
    script = tmp_path / "cleanup_ui.py"
    source = Path(__file__).resolve().parents[1] / "src"
    script.write_text(SCRIPT, encoding="utf-8")
    options = {}
    if sys.platform == "win32":
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        options["startupinfo"] = startup
    completed = subprocess.run(
        [BLENDER, "--factory-startup", "--enable-event-simulate", "--python", str(script),
         "--", str(source), str(tmp_path), mode],
        capture_output=True, text=True, timeout=30,
        env={**os.environ, "TEMP": str(tmp_path), "TMP": str(tmp_path)}, **options,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "CLEANUP_UI_PASSED" in output, output


SCRIPT = r"""
import sys
from pathlib import Path
from types import SimpleNamespace
import bpy

source, directory, mode = sys.argv[-3:]
sys.path.insert(0, source)
from blendjob import JobRuntime

root = Path(directory)
(root / 'server.py').touch()
runtime = JobRuntime('cleanup_test', server=f"{root / 'server.py'}:server", storage_root=root / 'storage', environment={'python': '3.11'})
runtime._config.environment_ready = lambda root: True
states, actions = {}, []

def action(name, **parameters):
    actions.append(name)
    identifier = str(len(states))
    result = {'jobs': [] if mode == 'empty' else ['a' * 32], 'bytes': 12,
              'instance_id': 'test', 'skipped': 0, 'failed': []}
    states[identifier] = {'state': 'succeeded', 'result': result}
    return identifier

runtime.server = SimpleNamespace(snapshot={'state': 'IDLE'}, action=action,
    status=lambda identifier: states[identifier], poll=lambda **kwargs: None,
    mark_job_complete=lambda identifier: None, detach=lambda: None)
runtime.register()

def begin():
    assert bpy.ops.cleanup_test.clear_job_files('INVOKE_DEFAULT') == {'RUNNING_MODAL'}

def respond():
    if mode != 'empty':
        window = bpy.context.window_manager.windows[0]
        key = 'RET' if mode == 'confirm' else 'ESC'
        window.event_simulate(type=key, value='PRESS')
        window.event_simulate(type=key, value='RELEASE')

def finish():
    try:
        expected = ['preview_files', 'clear_files'] if mode == 'confirm' else ['preview_files']
        assert actions == expected, actions
        assert not runtime.jobs and runtime._operation is None
        runtime.unregister()
        print('CLEANUP_UI_PASSED', flush=True)
    finally:
        bpy.ops.wm.quit_blender()

bpy.app.timers.register(begin, first_interval=1)
bpy.app.timers.register(respond, first_interval=4)
bpy.app.timers.register(finish, first_interval=7)
"""
