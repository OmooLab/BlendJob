# Getting Started

This guide creates a minimal Blender extension: install an isolated Python environment, send an integer to a local Job Server, and display the result in Blender.

## Minimal layout

```text
example_job/
├── __init__.py
├── blender_manifest.toml
├── server/
│   └── __init__.py
└── wheels/
    └── blendjob-0.1.13-py3-none-any.whl
```

Modules under `server/` run in the dedicated process, while Blender loads the root `__init__.py`. To make the complete call flow easy to follow, this guide keeps all Blender-side code in `__init__.py`; a larger project can split it into focused modules later.

## 1. Bundle the BlendJob wheel

Copy the BlendJob wheel into `wheels/` and declare it in `blender_manifest.toml`:

The snippet below shows only the field added for BlendJob. Keep the extension's existing name, version, Blender version, license, and other manifest fields.

```toml
wheels = ["./wheels/blendjob-0.1.13-py3-none-any.whl"]
```

Blender can now import `blendjob` when it enables the extension.

## 2. Register a Server Handler

Create `server/__init__.py`:

```python
from blendjob import JobServer


server = JobServer("Example Job Server")


@server.job("double")
def double(context, parameters):
    context.progress(0.5, "Calculating")
    context.check_cancelled()
    return {"value": parameters["value"] * 2}
```

`@server.job("double")` registers a public job type. The Handler receives a `JobContext` and a plain parameter dictionary. Its return value becomes a Blender-side `JobResult`.

## 3. Configure the Runtime

Create the `JobRuntime` instance in the root `__init__.py`:

```python
from pathlib import Path

from blendjob import JobRuntime


runtime = JobRuntime(
    "server:server",
    entrypoint_root=Path(__file__).parent,
    storage_root=Path.home() / ".example-job",
    environment={
        "python": "3.10",
        "packages": ["numpy==2.4.2"],
    },
    namespace="example_job",
)
```

The configuration means:

- `server:server`: load the `server` object from `server/__init__.py`
- `entrypoint_root`: resolve the relative Server entrypoint from this directory
- `storage_root`: persist the environment, job files, models, and logs here
- `environment`: declare the backend Python version and packages
- `namespace`: prefix the `bl_idname` of BlendJob's built-in Operators

NumPy is included to demonstrate application dependency declaration. The `double` Handler itself does not use NumPy, so the example also runs after removing `packages`.

## 4. Define the Blender Operator

Continue in `__init__.py` and define the application Operator:

```python
import bpy

JobOperatorBase = runtime.JobOperatorBase


class DoubleValue(JobOperatorBase, bpy.types.Operator):
    bl_idname = "example_job.double_value"
    bl_label = "Double Value"
    job_type = "double"

    value: bpy.props.IntProperty(default=21)

    def request(self, _context):
        return {"value": self.value}

    def response(self, _context, result):
        self.report({"INFO"}, f"Result: {result.value['value']}")
```

This example submits `{"value": 21}`. After success, `response()` receives the result on Blender's main thread.

The explicit `request()` makes the data crossing from Blender to the Server easy to see. If omitted, `JobOperatorBase` automatically turns declared Operator properties into the parameter dictionary. `response()` is optional as well; without it, the Operator simply finishes with `FINISHED` after a successful job.

## 5. Install the environment on first use

Continue in `__init__.py` and add a minimal panel:

```python
class ExampleJobPanel(bpy.types.Panel):
    bl_idname = "EXAMPLE_JOB_PT_main"
    bl_label = "Example Job"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Example"

    def draw(self, _context):
        layout = self.layout
        if runtime.environment_ready():
            layout.operator("example_job.double_value")
        else:
            layout.operator("example_job.install_environment")
```

The panel offers environment installation on first use and shows the application action once the environment is ready. Projects serving users in mainland China can also expose `example_job.install_environment_mirror`.

## 6. Register the extension

Finally, register the application types and Runtime in the same `__init__.py`:

```python
CLASSES = (
    DoubleValue,
    ExampleJobPanel,
)


def register():
    for class_type in CLASSES:
        bpy.utils.register_class(class_type)
    runtime.register()


def unregister():
    runtime.unregister()
    for class_type in reversed(CLASSES):
        bpy.utils.unregister_class(class_type)
```

`runtime.register()` registers the environment installer, job cancellation, server controls, and log Operator. It also displays active-job progress in Blender's status bar.

## 7. Install and run

1. Build and install the extension.
2. Allow Online Access in Blender Preferences.
3. Open the **Example** tab in the 3D View sidebar.
4. Click **Install Environment**.
5. Click **Double Value** when installation completes.
6. Blender reports `Result: 42`.

The first job submission starts a local Server dedicated to the current Blender instance. Later jobs reuse the same process and environment.

## Next steps

- Learn custom requests, result import, and cleanup in [Blender Integration](blender-integration.md).
- Learn output files, progress, and cancellation in [Server and Jobs](server-jobs.md).
- Extend this example with model downloads and inference in [AI Workflows](ai-workflows.md).
