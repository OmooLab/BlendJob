# BlendJob 开发者指南

## 目标与边界

`blendjob` 连接 Blender Operator 与独立 Python Job Server。开发者新增任务时只需要一个 Blender Operator 和一个 Server Handler，不需要编写提交线程、HTTP 轮询、取消、进度条、Server 进程或 Environment 安装 Operator。

- `JobRuntime`：Blender 侧唯一运行时，持有 Storage Root、Environment、Server 进程和当前 Job
- `JobOperatorBase`：绑定 Runtime 后供业务 Operator 直接继承的 Modal Mixin
- `JobServer`：独立 Python 进程中的 Job 注册、状态、取消、工作目录和 Resource
- `JobContext`：Handler 的工作目录、Storage Root、进度、取消与 Resource 接口
- `JobResult`：Server 成功结果及其 Job 文件
- `ServerController`：Runtime 内部的专属 Server 进程控制器

## 项目配置

Server 实例直接定义在项目的 `server` package：

```python
# snapform/server/__init__.py

from blendjob import JobServer


server = JobServer("SnapForm Job Server")
```

Blender 侧把这个实例交给 Runtime。实例不会跨进程传输；Runtime 从导出该实例的 package 自动找到独立 Python 入口。

```python
# snapform/job_runtime.py

from pathlib import Path

from blendjob import JobRuntime


environment = {
    "python": "3.10",
    "packages": [
        "numpy==2.4.2",
        "pillow==12.1.1",
        "av==17.0.0",
        "trimesh==4.11.2",
        "huggingface-hub==1.4.1",
    ],
    "platform_packages": {
        "windows": ["onnxruntime-directml==1.24.4"],
        "default": ["onnxruntime==1.24.4"],
    },
}


def post_install(runtime):
    runtime.request(
        "download-model",
        {"model": "MOGE2_VITS_NORMAL"},
    )


runtime = JobRuntime(
    "server:server",
    entrypoint_root=Path(__file__).parent,
    storage_root=storage_root,
    environment=environment,
    post_install=post_install,
    namespace="snapform",
)

JobOperatorBase = runtime.JobOperatorBase
```

Runtime 接收 `<python-file-or-package>:<attribute>` 格式的 Server 入口字符串；相对入口必须同时提供 `entrypoint_root`，package 目录会解析为其中的 `__init__.py`。Blender 主进程不导入 Server package，独立进程启动后才从该文件取得导出的 `JobServer`。Runtime 按约定管理 `.venv`、Manifest、安装状态与日志路径。BlendJob 根据 `environment` 字典安装固定 Python、通用的 FastAPI/Uvicorn Server 依赖、项目 packages 以及当前平台 packages。配置会生成稳定 Hash；Python 或依赖声明变化后 Environment 自动判定为需要重装，不再需要项目维护 `install_env.py` 或手工 Revision。

BlendJob wheel 必须出现在 Extension Manifest 中，供 Blender 主进程导入 Runtime。独立 Server Environment 会自动从 PyPI 安装与 Blender 侧相同版本的 BlendJob，从而执行 `python -m blendjob.runner`；调用方不应在 `environment["packages"]` 中重复声明 BlendJob。示例使用 Python 3.10，也是当前最低支持版本。

`packages` 只声明 Job 自身的额外依赖，没有额外依赖时可以省略。`platform_packages` 优先选择 `windows`、`macos` 或 `linux`，没有对应项时使用 `default`。BlendJob Runtime packages、业务 packages 与平台 packages 会合并安装，并共同参与 Environment Hash。

`post_install` 是可选的普通 callable，在 Environment 可用且 Server 能启动后执行。它不限定为 Job 列表，可以执行任意 Python；SnapForm 用它发起默认模型下载。连续 request 会分别重置进度，Status Bar 不为未知数量的 post-install 操作显示 `2/2` 等阶段总数。post-install 失败不会撤销已经安装成功的 Environment，但 Environment 安装 Operator 会返回失败并报告原始错误。

`runtime.register()` 自动注册 Environment 安装、Job 取消、Server 启动、停止、重启和日志 Operator，并挂载内建 Status Bar。Status Bar 使用可由 Blender 4.5 标记所有权的稳定函数回调，Runtime 卸载时移除同一个回调对象。业务 `CLASSES` 只包含业务类型。

```python
CLASSES = (
    GenerateMoGe2Depth,
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

启用 Runtime 不会立即启动 Server。第一次提交 Job 或显式调用 Start Server 时才启动当前 Blender 专属进程。

## Blender Operator

最小 Operator 只声明 `job_type` 与自己的 Blender Property：

```python
class GenerateMoGe2Depth(JobOperatorBase, bpy.types.Operator):
    bl_idname = "snapform.generate_moge2_depth"
    bl_label = "Generate MoGe-2 Depth"
    job_type = "generate-moge2-depth"

    input_path: bpy.props.StringProperty()
    resolution_level: bpy.props.IntProperty(
        default=5,
        min=0,
        max=9,
    )
```

执行 `bpy.ops.snapform.generate_moge2_depth()` 调用的就是这个 Operator。`JobOperatorBase` 提供它的 `execute()` 和 `modal()`，不会再创建或调用隐藏的 Job Operator。

没有覆盖 `request()` 时，Operator 声明的全部 Property 会作为参数提交：

```json
{
  "input_path": "D:/image.png",
  "resolution_level": 5
}
```

后端可直接忽略不使用的字段。BlendJob 不提供 Property 排除表。

### request

需要读取 Blender Context 或生成临时输入时覆盖：

```python
def request(self, context):
    input_path, temporary = prepare_image_input(
        context.object.data,
        context.scene,
    )
    self._temporary_input = input_path if temporary else None
    return {
        "input": str(input_path),
        "resolution_level": self.resolution_level,
    }
```

返回值是完整参数，纯替换默认 Property 参数，不做合并。它必须是可由 JSON 表示的 `dict`。

### response

Server 成功后，Runtime 在 Blender 主线程调用：

```python
def response(self, context, result):
    build_moge2_depth_plane(
        context,
        source_object=bpy.data.objects[self._source_object_name],
        npz_path=result.file("depth"),
    )
```

`response()` 是可选的。未覆盖时 Job 成功即返回 `FINISHED`。开发者可以直接调用 Python 函数，也可以调用其它 Blender Operator；BlendJob 不限制后处理方式。

`cleanup()` 同样可选，在成功、失败和取消后都会执行，适合删除临时输入。

业务代码直接返回 `dict`。BlendJob 没有公开 `JobRequest` 包装类型；HTTP Payload 由 Runtime 内部构造。

## 直接 Request

不需要 Blender Operator 时，可以直接向 Runtime 发起一个同步 request：

```python
def downloaded(result):
    print(result.directory)


result = runtime.request(
    "download-model",
    {"model": "MOGE2_VITS_NORMAL"},
    response=downloaded,
)
```

`runtime.request(job_type, parameters, response=None)` 负责启动 Server、提交、轮询、失败处理与构造 `JobResult`，返回值就是 Server response。可选的 `response(result)` 在发起 request 的线程执行。它是同步 API，适合 `post_install`、后台线程或脚本；Blender 主线程中的交互功能仍应使用 `JobOperatorBase`，避免阻塞界面。

## Server Handler

Handler 直接注册为 `handler(context, parameters)`：

```python
@server.job("generate-moge2-depth")
def generate_moge2_depth(context, parameters):
    model = context.resource("model_manager").get(
        parameters["model"],
        parameters["device"],
    )
    context.progress(0.1, "Loading MoGe-2")
    context.check_cancelled()

    depth = model.infer(
        parameters["input"],
        resolution_level=parameters["resolution_level"],
    )
    output = context.directory / "results.npz"
    save_depth(output, depth)
    return {"depth": output.name}
```

返回值保存在终态的 `result` 字段。Blender 端的 `JobResult.file("depth")` 会把相对路径限制在当前 Job Directory 内并验证文件存在。

`JobContext` 提供：

- `storage_root`：Runtime 传入的唯一共享根目录
- `directory`：`storage_root/jobs/<job-id>`
- `progress(value, message, **details)`：发布运行进度
- `check_cancelled()`：在安全检查点响应取消
- `resource(name)`：访问 Server 生命周期 Resource

Handler 不需要也不应管理 `queued`、`succeeded`、`failed` 等状态；这些状态由 `JobServer` 内部更新，因此公开 API 不再同时提供语义重复的 `update()`。Environment 安装发生在 Server 启动前，仍通过 Runtime 的 Bootstrap 状态文件回传进度，不属于 `JobContext`。

## Storage Root

Runtime 是 Storage Root 的唯一配置者。`JobServer` 不需要在项目代码中声明 `jobs_directory`，启动后固定使用：

```text
<storage_root>/
├── .venv/
├── environment.json
├── jobs/
├── models/
├── tools/
├── install.log
├── install-status.json  # 仅在安装期间存在
├── server.log
└── manifest.json
```

`environment.json` 保存期望安装的 Environment 声明，`install-status.json` 只在安装期间回传进度并在任务收尾后删除，`install.log` 持久保留安装过程输出。

需要 Storage Root 的 Resource 使用延迟工厂：

```python
@server.resource("model_manager")
def create_model_manager(job_server):
    return ModelManager(job_server.storage_root / "models")
```

Resource 在独立进程绑定 Storage Root 后创建，关闭 Server 时按注册逆序关闭。

模型等领域数据应由 Server Resource 管理。Blender 可以通过 `runtime.resource("model_manager")` 查询 Resource Snapshot，不应自行拼接模型目录或检查模型文件。

## Server 生命周期

每个 Blender 实例为当前插件持有一个专属 Server。Runtime 使用 `python -m blendjob.runner` 从 Environment 启动入口，避免 package 内模块名遮蔽 Python 标准库；并自动处理随机端口、Instance ID、父 Blender PID、启动日志、连接验证、重启和停止。

Server 只监听 `127.0.0.1`。当前本地专属进程协议不使用一次性 Token，不发现或复用其它 Blender 启动的 Server。

`JobServer` 内建一个标准库单 Worker FIFO Queue。新 Job 会立即得到 ID 和目录，空闲时执行，忙碌时保持 `queued`；同一时刻只运行一个 Handler。排队任务可以取消，调用方不需要自己等待 Server Idle，`runtime.request()` 也不使用额外的 Idle 轮询补丁。

模型和 Environment 可以共享同一 Storage Root；共享磁盘目录不表示共享 Server 进程。多个进程可能同时下载同一模型时，业务层仍需要文件锁。
