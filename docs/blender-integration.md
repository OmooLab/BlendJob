# Blender 集成

`JobRuntime` 提供 Blender 侧的统一入口。业务 Operator 继承它绑定的 `JobOperatorBase` 后，就能使用异步提交、进度显示、取消与结果回调。

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

JobOperatorBase = runtime.JobOperatorBase
```

最小 Extension 可以把 Runtime、Operator、Panel 与注册代码放在根 `__init__.py` 中。项目增长后，再按职责拆分到 `job_runtime.py`、`operators.py`、`panel.py` 等模块。

Server 入口使用 `<python-file-or-package>:<attribute>` 格式。相对入口由 `entrypoint_root` 解析，package 目录会使用其中的 `__init__.py`。

## 把 Operator 接入 Job

最小 Operator 声明 `job_type` 和业务 Property：

```python
class ResizeImage(JobOperatorBase, bpy.types.Operator):
    bl_idname = "my_addon.resize_image"
    bl_label = "Resize Image"
    job_type = "resize-image"

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

## 注册 Runtime

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
| 安装 Environment | `my_addon.install_environment` |
| 使用镜像安装 | `my_addon.install_environment_mirror` |
| 取消活动 Job | `my_addon.cancel_job` |
| 启动 Server | `my_addon.start_server` |
| 停止 Server | `my_addon.stop_server` |
| 重启 Server | `my_addon.restart_server` |
| 打开 Server 日志 | `my_addon.open_server_log` |

## 直接发起同步 Request

脚本、后台线程或 `post_install` 可以使用同步 API：

```python
result = runtime.request(
    "download-model",
    {"model": "depth-v1"},
)
print(result.value)
```

交互式 Blender 功能使用 `JobOperatorBase`，它通过 Modal Operator 保持界面响应。`runtime.request()` 会等待 Job 完成，适合已经运行在后台的调用路径。
