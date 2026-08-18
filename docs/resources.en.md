# Server Resources

Server Resources hold objects reused across jobs, such as AI models, ONNX sessions, media decoders, database connections, or memory caches. They are created with the Server and released when it closes.

## Register a lazy Resource

Register a factory with `@server.resource()`:

```python
@server.resource("model_manager")
def create_model_manager(job_server):
    return ModelManager(job_server.storage_root / "models")
```

The factory runs after the Runtime binds the Storage Root, so it can create project directories from `job_server.storage_root`.

## Use it in a Handler

```python
@server.job("run-inference")
def run_inference(context, parameters):
    manager = context.resource("model_manager")
    model = manager.get(parameters["model"])
    return model.predict(parameters["input"])
```

One Resource instance is reused across jobs served by the same process. For large models, this keeps loaded weights or sessions available and avoids repeated initialization.

## Expose state to Blender

Implement `snapshot()` to provide JSON-compatible state to the Runtime:

```python
class ModelManager:
    def snapshot(self):
        return {
            "downloaded": sorted(self.downloaded_models()),
            "loaded": sorted(self.loaded_models),
        }
```

Query it synchronously from Blender:

```python
status = runtime.resource("model_manager")
is_ready = "depth-v1" in status["downloaded"]
```

This pattern works well for panels that show downloaded models, currently loaded models, or cache size.

## Clear in-memory state

After a Resource implements `clear()`, Blender can call it while the Server is idle:

```python
class ModelManager:
    def clear(self):
        self.loaded_models.clear()


runtime.clear_resource("model_manager")
```

`clear()` commonly releases memory or GPU memory while preserving models on disk. The Server coordinates clearing with job submission so it happens in an idle state.

## Close a Resource

A Resource can implement `close()`:

```python
class ModelManager:
    def close(self):
        self.loaded_models.clear()
        self.executor.shutdown()
```

When the Server closes, it waits for the active Handler to finish and calls Resource `close()` methods in reverse registration order. Use this for thread pools, file handles, and native runtime sessions.

## Add an existing instance

Regular Python tests and Servers with an already bound Storage Root can add an existing instance:

```python
server.add_resource("cache", Cache())
```

Extension projects generally prefer lazy factories so directory configuration and Resource creation share the same Server lifecycle.
