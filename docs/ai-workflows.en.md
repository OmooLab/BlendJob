# AI Workflows

An AI feature commonly has four stages: prepare isolated dependencies, download models, reuse inference objects, and import outputs into Blender. BlendJob organizes these stages around one Runtime and Storage Root.

## 1. Declare inference dependencies

```python
environment = {
    "python": "3.10",
    "packages": [
        "numpy==2.4.2",
        "huggingface-hub==1.4.1",
    ],
    "platform_packages": {
        "windows": ["onnxruntime-directml==1.24.4"],
        "default": ["onnxruntime==1.24.4"],
    },
}
```

The Blender side stays lightweight. These packages are installed in `storage_root/.venv` and imported by Server Handlers.

## 2. Manage models with a Resource

```python
class ModelManager:
    def __init__(self, root):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.sessions = {}

    def download(self, name, progress):
        path = self.root / name
        download_model(name, path, progress=progress)
        return path

    def get(self, name):
        if name not in self.sessions:
            self.sessions[name] = load_session(self.root / name)
        return self.sessions[name]

    def snapshot(self):
        return {
            "downloaded": sorted(path.name for path in self.root.iterdir()),
            "loaded": sorted(self.sessions),
        }

    def clear(self):
        self.sessions.clear()


@server.resource("models")
def create_models(job_server):
    return ModelManager(job_server.storage_root / "models")
```

Model files live in persistent storage, while sessions live in Server memory. The model directory remains available after rebuilding the environment.

## 3. Register a model download job

```python
@server.job("download-model")
def download_model_job(context, parameters):
    manager = context.resource("models")

    def progress(value, message):
        context.progress(value, message)
        context.check_cancelled()

    path = manager.download(parameters["model"], progress)
    return {"model": path.name}
```

Modeling downloads as jobs gives Blender consistent progress, cancellation, error handling, and logs.

## 4. Prepare a default model after installation

Provide `post_install` when the extension should be ready with a default model:

```python
def post_install(runtime):
    runtime.request(
        "download-model",
        {"model": "depth-v1"},
    )


runtime = JobRuntime(
    "server:server",
    entrypoint_root=Path(__file__).parent,
    storage_root=Path.home() / ".my-addon",
    environment=environment,
    post_install=post_install,
    namespace="my_addon",
)
```

After the environment is ready and the Server can start, BlendJob runs `post_install(runtime)` in the background. Progress from the model-download job continues in the status bar. The installation Operator finishes after default-model preparation.

For user-selected, on-demand models, omit `post_install` and invoke the same `download-model` job from a model-management panel.

## 5. Register an inference job

```python
@server.job("estimate-depth")
def estimate_depth(context, parameters):
    models = context.resource("models")
    context.progress(0.1, "Loading model")
    session = models.get(parameters["model"])
    context.check_cancelled()

    context.progress(0.3, "Running inference")
    depth = run_depth(session, parameters["input"])

    output = context.directory / "depth.npz"
    save_depth(output, depth)
    return {"depth": output.name}
```

The model session loads on first use and is reused by later jobs.

## 6. Submit from Blender and import the result

```python
class EstimateDepth(JobOperatorBase, bpy.types.Operator):
    bl_idname = "my_addon.estimate_depth"
    bl_label = "Estimate Depth"
    job_type = "estimate-depth"

    input_path: bpy.props.StringProperty(subtype="FILE_PATH")
    model: bpy.props.StringProperty(default="depth-v1")

    def response(self, context, result):
        build_depth_object(context, result.file("depth"))
```

Blender data handling stays in `response()`, while inference dependencies and model lifecycle stay in the Server. UI, computation, and persistent resources each have a clear role.

## Production guidance

- Pin Python package versions so the environment hash is reproducible.
- Add an application-level file lock when multiple processes can download into the same model directory.
- Expose model state through `snapshot()` so the UI reflects backend facts.
- Call `check_cancelled()` at natural inference checkpoints.
- Keep rebuildable sessions in memory and model weights under `storage_root/models`.
- Offer a `clear()` action to release memory or GPU memory.
