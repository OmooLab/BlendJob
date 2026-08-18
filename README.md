# BlendJob

[中文文档](https://docs.omoolab.xyz/blendjob/latest/)

BlendJob is a local Job Server runtime for Blender extensions. It runs long tasks in an isolated Python environment and provides a reusable Blender Operator workflow with progress, cancellation, FIFO scheduling, result delivery, and server lifecycle management.

It is designed for AI inference, model downloads, media processing, geometry computation, and other work that benefits from running outside Blender's main process.

## How it works

A BlendJob feature has three parts:

1. A Server Handler defines the Python task.
2. `JobRuntime` configures the Server entrypoint, environment, and storage.
3. A Blender Operator inherits `runtime.JobOperatorBase`, submits parameters, and applies the result on Blender's main thread.

## Quick example

Register a backend task:

```python
# example_job/server/__init__.py
from blendjob import JobServer


server = JobServer("Example Job Server")


@server.job("double")
def double(context, parameters):
    context.progress(0.5, "Calculating")
    return {"value": parameters["value"] * 2}
```

Configure the Blender runtime:

```python
# example_job/__init__.py
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

Define the Blender Operator:

```python
# example_job/__init__.py (continued)
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

The [Getting Started guide](https://docs.omoolab.xyz/blendjob/latest/en/getting-started/) provides a complete, ready-to-run Blender extension layout with registration and UI code.

## Installation

Install BlendJob in a regular Python project with pip or uv:

```bash
python -m pip install blendjob
```

```bash
uv add blendjob
```

The default package has no dependencies, which keeps the Blender-side runtime lightweight. Install the `server` extra when creating a FastAPI app or running the server directly in a regular Python environment:

```bash
python -m pip install "blendjob[server]"
```

For a Blender extension, bundle the BlendJob wheel in `wheels/` and declare it in `blender_manifest.toml`:

```toml
wheels = ["./wheels/blendjob-0.1.13-py3-none-any.whl"]
```

Supported runtime targets are Python 3.10+, Windows x64, macOS arm64, and Linux x64.

## Documentation

- [Getting Started](https://docs.omoolab.xyz/blendjob/latest/en/getting-started/)
- [How BlendJob Works](https://docs.omoolab.xyz/blendjob/latest/en/how-it-works/)
- [Blender Integration](https://docs.omoolab.xyz/blendjob/latest/en/blender-integration/)
- [Server and Jobs](https://docs.omoolab.xyz/blendjob/latest/en/server-jobs/)
- [AI Workflows](https://docs.omoolab.xyz/blendjob/latest/en/ai-workflows/)
- [API Reference](https://docs.omoolab.xyz/blendjob/latest/en/api/)

## Development

```bash
uv sync
uv run pytest
uv build
uv run mkdocs build --strict
```

Versioned documentation uses Mike:

```bash
uv run mike deploy --update-aliases 0.1 latest
uv run mike set-default latest
```

## License

BlendJob is available under the [MIT License](https://github.com/OmooLab/BlendJob/blob/main/LICENSE).
