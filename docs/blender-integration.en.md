# Blender Integration

`JobRuntime` is the Blender-side integration point. Application Operators inherit its bound `JobOperatorBase` to gain asynchronous submission, progress UI, cancellation, and result callbacks.

## Create a Runtime

```python
from pathlib import Path

from blendjob import JobRuntime


runtime = JobRuntime(
    "server:server",
    entrypoint_root=Path(__file__).parent,
    storage_root=Path.home() / ".my-addon",
    environment={"python": "3.10"},
    namespace="my_addon",
)

JobOperatorBase = runtime.JobOperatorBase
```

A minimal extension can keep the Runtime, Operator, Panel, and registration code in its root `__init__.py`. As the project grows, split them into modules such as `job_runtime.py`, `operators.py`, and `panel.py` according to their responsibilities.

The Server entrypoint uses `<python-file-or-package>:<attribute>`. `entrypoint_root` resolves relative entries, and a package directory resolves to its `__init__.py`.

## Connect an Operator to a job

A minimal Operator declares a `job_type` and application properties:

```python
class ResizeImage(JobOperatorBase, bpy.types.Operator):
    bl_idname = "my_addon.resize_image"
    bl_label = "Resize Image"
    job_type = "resize-image"

    input_path: bpy.props.StringProperty(subtype="FILE_PATH")
    width: bpy.props.IntProperty(default=1024, min=1)
```

The default `request()` turns declared Operator properties into a parameter dictionary. In this example, the Server receives `input_path` and `width`.

Both `request()` and `response()` are optional. The default request works when declared properties are the complete parameter set, and `response()` can be omitted when a successful result does not need to update Blender data.

## Customize the request

Override `request()` when parameters come from Blender Context, require a data conversion, or use a selected set of properties. Return the complete dictionary:

```python
def request(self, context):
    input_path = export_active_image(context)
    self._temporary_input = input_path
    return {
        "input": str(input_path),
        "width": self.width,
    }
```

The return value must be a JSON-compatible `dict`. Save large values to files and pass their paths.

## Apply a successful result

Override `response()` to handle `JobResult` on Blender's main thread:

```python
def response(self, context, result):
    image_path = result.file("image")
    image = bpy.data.images.load(str(image_path))
    context.scene.render.engine = "BLENDER_EEVEE_NEXT"
    self.report({"INFO"}, f"Loaded {image.name}")
```

`result.value` is the Handler's return value. When the Handler returns output filenames, `result.file("image")` resolves and validates a file relative to the current job directory.

## Clean up invocation resources

`cleanup()` runs after success, failure, and cancellation. Use it for temporary files created by this Operator invocation:

```python
def cleanup(self):
    path = getattr(self, "_temporary_input", None)
    if path is not None:
        path.unlink(missing_ok=True)
```

## Register the Runtime

Register application types before the Runtime and unregister them in reverse order:

```python
def register():
    for class_type in CLASSES:
        bpy.utils.register_class(class_type)
    runtime.register()


def unregister():
    runtime.unregister()
    for class_type in reversed(CLASSES):
        bpy.utils.unregister_class(class_type)
```

With `namespace="my_addon"`, the Runtime registers these built-in Operators:

| Action | `bl_idname` |
| --- | --- |
| Install environment | `my_addon.install_environment` |
| Install from mirrors | `my_addon.install_environment_mirror` |
| Cancel the active job | `my_addon.cancel_job` |
| Start the Server | `my_addon.start_server` |
| Stop the Server | `my_addon.stop_server` |
| Restart the Server | `my_addon.restart_server` |
| Open the Server log | `my_addon.open_server_log` |

## Make a direct synchronous request

Scripts, background threads, and `post_install` can use the synchronous API:

```python
result = runtime.request(
    "download-model",
    {"model": "depth-v1"},
)
print(result.value)
```

Use `JobOperatorBase` for interactive Blender features; its modal workflow keeps the interface responsive. `runtime.request()` waits for completion and fits call paths that already run in the background.
