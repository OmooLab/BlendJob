# Server and Jobs

`JobServer` runs in the dedicated Python process. The project registers Handlers with decorators, and each Handler uses `JobContext` for storage, progress, cancellation, and Resources.

## Register a Handler

```python
from blendjob import JobServer


server = JobServer("Image Tools")


@server.job("resize-image")
def resize_image(context, parameters):
    source = parameters["input"]
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

The `job_type` is the stable name shared by the Blender Operator and Handler. `parameters` is the dictionary submitted by the Operator.

## JobContext

Handlers commonly use these context members:

| Member | Purpose |
| --- | --- |
| `job_id` | Unique ID of the current job |
| `job_type` | Type of the current job |
| `storage_root` | Persistent root configured by the Runtime |
| `directory` | Dedicated directory for the current job |
| `progress(value, message)` | Publish progress from 0.0 to 1.0 with display text |
| `check_cancelled()` | Respond to cancellation at a safe checkpoint |
| `resource(name)` | Get a Server Resource |

BlendJob maintains the `queued`, `running`, `cancelling`, `succeeded`, `failed`, and `cancelled` states from Handler execution.

## Report progress

```python
context.progress(0.05, "Loading input")
context.progress(0.40, "Running inference")
context.progress(0.90, "Saving output")
```

The Runtime displays the latest progress and message in Blender's status bar. UI progress remains monotonic for one job.

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

One Runtime manages one active interactive Operator invocation at a time. The Server queue can also accept jobs from other call paths, including synchronous `runtime.request()` calls.

## Failures and logs

An exception from a Handler marks the job as failed and returns its error message to the Runtime. The complete traceback is written to `server.log`, which the `<namespace>.open_server_log` Operator opens.
