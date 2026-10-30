# Server and Jobs

`JobServer` runs in the dedicated Python process. The project registers Handlers with decorators, and each Handler uses `JobContext` for storage, progress, cancellation, and Resources.

## Register a Handler

```python
from blendjob import JobServer


server = JobServer("Image Tools")


@server.job("resize_image")
def resize_image(context, parameters):
    source = parameters["input_path"]
    width = int(parameters["width"])
    context.progress(0.1, "Opening image")

    image = open_image(source)
    context.check_cancelled()

    context.progress(0.5, "Resizing image")
    resized = image.resize(width)

    output = context.directory / "resized.png"
    resized.save(output)
    return {"image": output.name, "width": width}
```

The `job_type` is the stable name shared by the Blender Operator and Handler, written in lowercase `snake_case`. `parameters` is the dictionary submitted by the Operator.

## JobContext

Handlers commonly use these context members:

| Member | Purpose |
| --- | --- |
| `job_id` | Unique ID of the current job |
| `job_type` | Type of the current job |
| `storage_root` | Persistent root configured by the Runtime |
| `directory` | Dedicated directory for the current job |
| `progress(value, message, **message_args)` | Publish progress from 0.0 to 1.0, a message template, and its parameters |
| `check_cancelled()` | Respond to cancellation at a safe checkpoint |
| `resource(name)` | Get a Server Resource |
| `log(message, level="INFO")` | Write Handler details to the current Job's `job.log` |

BlendJob maintains the `queued`, `running`, `cancelling`, `succeeded`, `failed`, and `cancelled` states from Handler execution.

## Progress and translation

Handlers use English message templates and pass dynamic values as keyword arguments:

```python
context.progress(0.1, "Preparing image")
context.progress(
    0.5,
    "Processing image {index}/{total}",
    index=3,
    total=6,
)
context.progress(1.0, "Processing complete")
```

Keep the messages in the add-on's regular Blender translation table in `translations.py`:

```python
TRANSLATIONS = {
    "zh_HANS": {
        ("*", "Preparing image"): "正在准备图像",
        ("*", "Processing image {index}/{total}"): "正在处理图像 {index}/{total}",
        ("*", "Processing complete"): "处理完成",
    },
}
```

`"*"` is Blender's default translation context. Keep the same parameter names in each translation, such as `{index}` and `{total}`.

Merge translation registration into the add-on's existing `register()` and `unregister()` functions:

```python
import bpy

from .translations import TRANSLATIONS


def register():
    bpy.app.translations.register(__package__, TRANSLATIONS)
    # Register the add-on's classes and runtime here.


def unregister():
    # Unregister the runtime and classes here.
    bpy.app.translations.unregister(__package__)
```

Add-ons with an existing translation table only need to add the entries. `JobRuntime` uses Blender's `pgettext_iface()` to translate the template before filling in parameters at display time, following the current interface language and interface translation setting.

Pass fixed text directly. Keep parameterized templates intact and let the Runtime format them. The first running job appears in the status bar; `runtime.draw_jobs(layout)` displays the full list, and UI progress remains monotonic for one job.

## Respond to cancellation

Check between natural units of long-running work:

```python
for index, batch in enumerate(batches):
    context.check_cancelled()
    process(batch)
    context.progress((index + 1) / len(batches), "Processing batches")
```

`check_cancelled()` exits the Handler and moves the job to the cancelled state. Checks between batches, model stages, or files give prompt feedback to the user.

## Return regular values

A Handler can return any value that FastAPI can encode as JSON:

```python
return {
    "vertices": 1280,
    "duration": 1.42,
}
```

Read it from `result.value` on the Blender side.

## Return output files

Write outputs into `context.directory` and return relative filenames:

```python
output = context.directory / "surface.npz"
save_surface(output, vertices, faces)
return {"surface": output.name}
```

Resolve the file in Blender with:

```python
surface_path = result.file("surface")
```

This keeps invocation files separate and lets Blender verify that a result belongs to the current job directory before reading it.

## Queue behavior

The Job Server uses a single-worker FIFO queue. Submission immediately returns a job ID and directory. An idle job begins running, and later jobs wait in submission order. Both queued and running jobs can receive cancellation requests.

One Runtime manages multiple interactive invocations, each with independent progress, results, and cancellation. Successful results are applied on the main thread in submission order.

## Failures and logs

An exception from a Handler marks the job as failed and returns its error message to the Runtime. Each `jobs/<job-id>/job.log` stores that Job's lifecycle, `context.log()` details, and complete traceback. `server.log` keeps only the timestamped queue, start, success, cancellation, and failure mainline with Job ID and Job Type, plus output from the Server process itself.
