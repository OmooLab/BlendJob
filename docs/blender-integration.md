# Blender 集成

`JobRuntime` 提供 Blender 侧的统一入口。业务 Operator 继承它绑定的 `JobOperator` 后，就能使用异步提交、进度显示、取消与结果回调。

## 创建 Runtime

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

最小 Extension 可以把 Runtime、Operator、Panel 与注册代码放在根 `__init__.py` 中。项目增长后，再按职责拆分到 `job_runtime.py`、`operators.py`、`panel.py` 等模块。

Server 入口使用 `<python-file-or-package>:<attribute>` 格式。相对入口由 `entrypoint_root` 解析，package 目录会使用其中的 `__init__.py`。

## 把 Operator 接入 Job

最小 Operator 声明 `job_type` 和业务 Property：

```python
class ResizeImage(JobOperator, bpy.types.Operator):
    bl_idname = "my_addon.resize_image"
    bl_label = "Resize Image"
    job_type = "resize_image"

    input_path: bpy.props.StringProperty(subtype="FILE_PATH")
    width: bpy.props.IntProperty(default=1024, min=1)
```

默认 `request()` 会把 Operator 声明的 Property 组成参数字典。在这个例子中，Server 会收到 `input_path` 与 `width`。

`request()` 和 `response()` 都是可选方法。默认请求适合直接提交 Property；成功结果无需导回 Blender 数据时，可以省略 `response()`。

## 自定义请求

当参数需要读取 Blender Context、转换 Blender 数据或选择部分 Property 时，覆盖 `request()` 并返回完整字典：

```python
def request(self, context):
    input_path = export_active_image(context)
    self._temporary_input = input_path
    return {
        "input": str(input_path),
        "width": self.width,
    }
```

返回值需要是可由 JSON 表示的 `dict`。大型数据适合先保存为文件，然后传递路径。

## 应用成功结果

覆盖 `response()`，在 Blender 主线程处理 `JobResult`：

```python
def response(self, context, result):
    image_path = result.file("image")
    image = bpy.data.images.load(str(image_path))
    context.scene.render.engine = "BLENDER_EEVEE_NEXT"
    self.report({"INFO"}, f"Loaded {image.name}")
```

`result.value` 是 Handler 的返回值。Handler 返回输出文件名时，`result.file("image")` 会相对于当前 Job Directory 解析并验证文件。

## 清理调用资源

`cleanup()` 在成功、失败和取消后执行，适合删除 Operator 为这次调用创建的临时文件：

```python
def cleanup(self):
    path = getattr(self, "_temporary_input", None)
    if path is not None:
        path.unlink(missing_ok=True)
```

## 状态与默认控制区

`runtime.state()` 返回一个状态字符串：

| 状态 | 含义 |
| --- | --- |
| `UNAVAILABLE` | 环境未就绪，需要安装或修复 |
| `INSTALLING` | 正在安装或验证环境 |
| `MIGRATING` | 正在迁移存储目录 |
| `OFFLINE` | 环境就绪，服务器未运行 |
| `STARTING` | 正在启动服务器 |
| `IDLE` | 服务器空闲，可以执行任务 |
| `PROCESSING` | 正在执行 Job，完成后即释放状态 |
| `STOPPING` | 已请求停止，等待服务器确认 |
| `ERROR` | 服务器启停或控制过程发生异常 |

安装和迁移优先显示；普通运行先检查环境是否就绪，再显示启动、停止或错误状态，其余活动任务显示为 `PROCESSING`。状态读取使用本地环境检查与自动轮询的服务器快照。

常用判断全部从 `state()` 推导：

| 方法 | 含义 |
| --- | --- |
| `can_submit()` | 是否可以提交业务 Job；服务器按需启动，处理中也可继续提交 |

安装入口判断 `state() == "UNAVAILABLE"`，卸载资源判断 `state() == "IDLE"`。普通 Job 失败后服务器回到空闲；`ERROR` 表示服务器控制异常。

迁移负责取消全部任务，等任务结束并清理后停止服务，再搬移目录。安装或迁移进行中时会阻止重复迁移。插件只需绘制迁移 Operator。

```python
job_row.enabled = runtime.can_submit()
layout.operator("my_addon.migrate_storage")
```

Panel 可以直接使用默认控制区。BlendJob 负责状态文字、启动、停止、重启、安装入口和日志按钮，内建 Operator 根据同一状态决定是否可用：

```python
def draw(self, _context):
    runtime.draw_server_controls(self.layout)
    runtime.draw_jobs(self.layout)
```

插件有自己的安装入口时，传入其 Operator ID：

```python
runtime.draw_server_controls(layout, setup_operator=SetupAIServer.bl_idname)
```

业务界面按需读取状态和资源：

```python
models = runtime.resource("model_manager")
loaded = models.get("loaded", {})

row.enabled = runtime.state() == "IDLE"
```

资源来自最近一次服务器快照，尚无资源时返回空字典。模型列表等业务内容由插件绘制。

## 服务器控制与维护

直接调用 Runtime 的独立方法：

| 方法 | 用途 |
| --- | --- |
| `runtime.start()` | 启动服务器 |
| `runtime.stop()` | 取消任务并停止服务器 |
| `runtime.restart()` | 取消任务并重启服务器 |
| `runtime.setup(target=None, mirror=False, online=True)` | 安装环境，默认使用配置的存储目录 |
| `runtime.migrate(target)` | 取消任务、停止服务器并迁移目录 |
| `runtime.clear_resource("models")` | 释放服务器资源 |
| `runtime.preview_files()` | 预览可清理的任务文件 |
| `runtime.clear_files(jobs, instance_id)` | 清理预览中选定的任务文件 |

这些方法异步执行，返回包含 `state`、`done`、`progress`、`text`、`result`、`error` 的 `Operation`。需要完成通知时传入 `on_done(operation)`。服务器控制和维护独立于业务任务列表，安装和迁移进度由 `draw_server_controls()` 显示。

## 注册 Runtime

业务进度使用插件的 Blender 翻译表，示例见[进度与翻译](server-jobs.md)。

先注册业务类型，再注册 Runtime；注销时按相反顺序：

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

Runtime 注册以下内建 Operator，`namespace="my_addon"` 时对应：

| 用途 | `bl_idname` |
| --- | --- |
| 设置 Environment | `my_addon.setup_server` |
| 迁移目录 | `my_addon.migrate_storage` |
| 取消指定 Job | `my_addon.cancel_job` |
| 启动 Server | `my_addon.start_server` |
| 停止 Server | `my_addon.stop_server` |
| 重启 Server | `my_addon.restart_server` |
| 打开 Server 日志 | `my_addon.open_server_log` |

停止、重启和迁移会取消全部 Job，等待终态与各自的 `cleanup()` 完成后执行。每行的 × 只取消对应任务；取消请求确认前保留该行和输入文件。

## 直接提交 Job

使用 `submit()` 可以在不创建 Operator 的情况下发起业务 Job：

```python
job = runtime.submit("download_model", {"model": "depth-v1"})
```

交互式 Blender 中可传入 `on_done(job)` 接收完成通知。独立脚本或 Blender 后台模式可以等待结果：

```python
result = runtime.submit("download_model", {"model": "depth-v1"}).wait()
print(result.value)
```

`wait()` 返回 `JobResult`，失败或取消时抛出异常。交互式 Blender 使用回调或 `JobOperator` 接收结果。

## 多任务

每次调用独立保存参数、进度、结果回调和清理。`request()` 捕获目标并准备独立输入文件；服务器 FIFO 执行，成功结果按提交顺序应用。关闭侧边栏不影响任务运行。

用 `runtime.draw_jobs(layout)` 绘制任务列表，每行只有带译文的进度条和取消按钮。排队时显示「等待中」，任务结束后移除该行。业务按钮用 `runtime.can_submit()`，处理中仍可继续提交。

注册 Runtime 后，状态栏自动显示第一个正在运行的任务，结束后切换到下一个；没有运行任务时隐藏。`runtime.draw_jobs(layout)` 可在任意布局绘制完整列表，开发者自行选择位置。两处共用进度、翻译与单任务取消。
