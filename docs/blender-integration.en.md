# Blender Integration

`JobRuntime` is the Blender-side integration point. Application Operators inherit its bound `JobOperator` to gain asynchronous submission, progress UI, cancellation, and result callbacks.

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

JobOperator = runtime.JobOperator
```

A minimal extension can keep the Runtime, Operator, Panel, and registration code in its root `__init__.py`. As the project grows, split them into modules such as `job_runtime.py`, `operators.py`, and `panel.py` according to their responsibilities.

The Server entrypoint uses `<python-file-or-package>:<attribute>`. `entrypoint_root` resolves relative entries, and a package directory resolves to its `__init__.py`.

## Connect an Operator to a job

A minimal Operator declares a `job_type` and application properties:

```python
class ResizeImage(JobOperator, bpy.types.Operator):
    bl_idname = "my_addon.resize_image"
    bl_label = "Resize Image"
    job_type = "resize_image"

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

## State and default controls

`runtime.state()` returns one state string:

| State | Meaning |
| --- | --- |
| `UNAVAILABLE` | The environment needs installation or repair |
| `INSTALLING` | Installing or validating the environment |
| `MIGRATING` | Moving the storage directory |
| `OFFLINE` | The environment is ready and the server is stopped |
| `STARTING` | Starting the server |
| `IDLE` | The server is idle and can run jobs |
| `PROCESSING` | Executing a job; completion releases the state |
| `STOPPING` | Stop requested; waiting for confirmation |
| `ERROR` | Server startup or operation failed |

Installation and migration take priority. Ordinary operation first checks environment availability, then startup, shutdown, or error states; other active jobs appear as `PROCESSING`. Reading state uses local environment checks and the automatically polled server snapshot.

Common checks derive from `state()`:

| Method | Meaning |
| --- | --- |
| `can_submit()` | Whether a business job can be submitted; the server starts as needed and accepts more jobs while processing |

Use `state() == "UNAVAILABLE"` for setup and `state() == "IDLE"` for resource unloading. A failed job normally returns to `IDLE`; `ERROR` describes server control failures.

Migration cancels all jobs, waits for completion and cleanup, then stops the server before moving its files. Installation or migration already in progress prevents a duplicate migration. The add-on only draws the migration operator.

```python
job_row.enabled = runtime.can_submit()
layout.operator("my_addon.migrate_storage")
```

Use the default server controls:

```python
def draw(self, _context):
    runtime.draw_server_controls(self.layout)
    runtime.draw_jobs(self.layout)
```

Pass an Operator ID to use the add-on's own setup entry:

```python
runtime.draw_server_controls(layout, setup_operator=SetupAIServer.bl_idname)
```

Read state and resources for application-specific UI:

```python
models = runtime.resource("model_manager")
loaded = models.get("loaded", {})

row.enabled = runtime.state() == "IDLE"
```

Resources come from the latest server snapshot and return an empty dictionary when absent. The add-on draws its own model lists and other business content.

## Server control and maintenance

Call the Runtime methods directly:

| Method | Action |
| --- | --- |
| `runtime.start()` | Start the server |
| `runtime.stop()` | Cancel jobs and stop the server |
| `runtime.restart()` | Cancel jobs and restart the server |
| `runtime.setup(target=None, mirror=False, online=True)` | Install the environment; defaults to the configured storage directory |
| `runtime.migrate(target)` | Cancel jobs, stop the server, and move storage |
| `runtime.clear_resource("models")` | Release a server resource |
| `runtime.preview_files()` | Preview job files available for cleanup |
| `runtime.clear_files(jobs, instance_id)` | Clear files selected from the preview |

These asynchronous methods return an `Operation` with `state`, `done`, `progress`, `text`, `result`, and `error`. Pass `on_done(operation)` to receive completion. Server controls and maintenance run independently of the business job list. Installation and migration progress appears in `draw_server_controls()`.

## Register the Runtime

Business progress uses the add-on's Blender translation table; see [Progress and translation](server-jobs.en.md#progress-and-translation).

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
| Set up the environment | `my_addon.setup_server` |
| Migrate storage | `my_addon.migrate_storage` |
| Cancel the active job | `my_addon.cancel_job` |
| Start the Server | `my_addon.start_server` |
| Stop the Server | `my_addon.stop_server` |
| Restart the Server | `my_addon.restart_server` |
| Open the Server log | `my_addon.open_server_log` |

Stop, restart, and migration cancel all jobs and wait for terminal states and cleanup before proceeding. Each row’s × cancels only that job. Its row and input files remain until cancellation is acknowledged.

## Submit a job directly

Use `submit()` to start a business job without an Operator:

```python
job = runtime.submit("download_model", {"model": "depth-v1"})
```

In interactive Blender, pass `on_done(job)` for completion notification. Standalone scripts and Blender background mode can wait for the result:

```python
result = runtime.submit("download_model", {"model": "depth-v1"}).wait()
print(result.value)
```

`wait()` returns a `JobResult` and raises on failure or cancellation. Use a callback or `JobOperator` to receive results in interactive Blender.

## Multiple jobs

Each invocation owns its parameters, progress, result callback, and cleanup. Capture the target and independent input files in `request()`. The server executes jobs FIFO and applies successful results in submission order. Closing the sidebar does not stop processing.

Draw the list with `runtime.draw_jobs(layout)`. Each row contains only a progress bar with translated text and a cancel button. Queued jobs show “Job queued”; finished jobs leave the list. Use `runtime.can_submit()` for job buttons so more jobs can be added while processing.

Registering the Runtime automatically shows the first running job in the status bar, switching to the next when it finishes and hiding when none is running. Use `runtime.draw_jobs(layout)` to place the complete list in any layout. Both displays share progress, translations, and per-job cancellation.
