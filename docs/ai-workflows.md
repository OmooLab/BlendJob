# AI 工作流

AI 功能通常包含四个阶段：准备独立依赖、下载模型、复用推理对象、把输出导回 Blender。BlendJob 可以把这四个阶段组织在同一 Runtime 与 Storage Root 中。

## 1. 声明推理依赖

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

Blender 侧继续保持轻量；这些 packages 安装在 `storage_root/.venv`，由 Server Handler 导入。

## 2. 用 Resource 管理模型

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

模型文件保存在持久目录，Session 保存在 Server 内存中。Environment 重建后，模型目录仍可继续使用。

## 3. 注册模型下载 Job

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

把模型下载也建模为 Job，Blender 就能获得一致的进度、取消、错误处理与日志。

## 4. 在安装后准备默认模型

需要开箱即用的默认模型时，为 Runtime 提供 `post_install`：

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

Environment 就绪并且 Server 可以启动后，BlendJob 在后台执行 `post_install(runtime)`。模型下载 Job 的进度会继续显示在状态栏。安装 Operator 会在默认模型准备完成后结束。

如果产品希望用户按需选择模型，可以省略 `post_install`，在模型管理面板中调用同一个 `download-model` Job。

## 5. 注册推理 Job

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

模型 Session 第一次使用时加载，后续 Job 直接复用。

## 6. 从 Blender 提交并导入结果

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

Blender 数据处理集中在 `response()`，推理依赖和模型生命周期集中在 Server。这样 UI、计算和持久资源各自拥有清晰职责。

## 生产项目建议

- 固定 Python package 版本，使 Environment Hash 可重复。
- 给下载到同一模型目录的多进程操作增加业务层文件锁。
- 在 `snapshot()` 中暴露模型状态，让 UI 基于后端事实显示操作。
- 在推理自然阶段调用 `check_cancelled()`。
- 把可重建的 Session 放在内存，把模型权重放在 `storage_root/models`。
- 通过 `clear()` 提供释放内存或显存的用户操作。
