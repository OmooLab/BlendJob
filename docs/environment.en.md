# Environment and Storage

BlendJob manages an isolated Python environment for the Server. The Blender process stays lightweight, while task dependencies live in the Runtime configuration.

## Declare dependencies

```python
environment = {
    "python": "3.10",
    "packages": [
        "numpy==2.4.2",
        "pillow==12.1.1",
    ],
    "platform_packages": {
        "windows": ["onnxruntime-directml==1.24.4"],
        "default": ["onnxruntime==1.24.4"],
    },
}
```

- `python`: required Python version for the isolated environment
- `packages`: application dependencies installed on every platform
- `platform_packages`: dependencies selected for `windows`, `macos`, or `linux`; `default` is used when a platform entry is absent

BlendJob adds FastAPI and Uvicorn for the Job Server. The bundled BlendJob wheel supplies both the Blender Client and Server Runtime, so the application dependency list stays focused on project packages.

## Install the environment

`runtime.register()` provides two installation Operators:

- `<namespace>.install_environment`: use the official uv, Python, and PyPI sources
- `<namespace>.install_environment_mirror`: use the CNB uv release, CNB Python mirror, and Tsinghua PyPI mirror

Installation prepares uv, Python, and packages in order and displays live messages and progress in Blender's status bar. Source configuration applies only to the current installer subprocess.

Online Access must be allowed in Blender Preferences. An extension UI can expose either or both installation actions according to its users' regions.

## Configuration changes

The Runtime normalizes the environment declaration and calculates a stable hash. After installation, the manifest stores that hash. When Python, common packages, or platform packages change, `runtime.environment_ready()` returns `False`, allowing the UI to offer installation again.

## Storage Root

`storage_root` can be a path or a callable returning a path:

```python
runtime = JobRuntime(
    ...,
    storage_root=Path.home() / ".my-addon",
)
```

BlendJob uses this layout:

```text
<storage_root>/
├── .venv/
├── environment.json
├── install.log
├── install-status.json
├── jobs/
├── manifest.json
├── server.log
└── tools/
```

- `.venv/`: isolated Python environment for the Server
- `environment.json`: requested installation declaration
- `manifest.json`: installed environment version and hash
- `jobs/<job-id>/`: work and output directory for each job
- `install.log`: environment installation output
- `server.log`: Server and Handler output
- `tools/`: uv managed by BlendJob

`install-status.json` carries UI state during installation and is removed when the install task closes. Your project can add domain directories such as `models/` and `cache/` under the same root.

## Logs and recovery

Read `install.log` after an environment installation failure and `server.log` after a startup or Handler failure. The Runtime provides an `open_server_log` Operator, and your settings panel can also display these paths.

The environment directory is rebuildable data. Store models, user downloads, and other durable content outside `.venv/`.
