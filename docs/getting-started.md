# 快速开始

本章会创建一个最小 Blender Extension：安装独立 Python Environment，把整数发送到本地 Job Server，再在 Blender 中显示计算结果。

## 最小目录

```text
example_job/
├── __init__.py
├── blender_manifest.toml
├── server/
│   └── __init__.py
└── wheels/
    └── blendjob-0.1.13-py3-none-any.whl
```

`server/` 中的模块运行在独立进程，根目录的 `__init__.py` 由 Blender 加载。为了突出完整调用流程，本章先把 Blender 侧代码集中写在 `__init__.py` 中；实际项目可以再按职责拆分模块。

## 1. 分发 BlendJob wheel

把 BlendJob wheel 复制到 `wheels/`，然后在 `blender_manifest.toml` 中声明它：

下面只列出 BlendJob 需要新增的字段；Manifest 仍需保留 Extension 原有的名称、版本、Blender 版本和许可证等配置。

```toml
wheels = ["./wheels/blendjob-0.1.13-py3-none-any.whl"]
```

这样 Blender 启用 Extension 时就可以导入 `blendjob`。

## 2. 注册 Server Handler

创建 `server/__init__.py`：

```python
from blendjob import JobServer


server = JobServer("Example Job Server")


@server.job("double")
def double(context, parameters):
    context.progress(0.5, "Calculating")
    context.check_cancelled()
    return {"value": parameters["value"] * 2}
```

`@server.job("double")` 注册公开 Job 类型。Handler 接收 `JobContext` 和一个普通参数字典，返回值会交给 Blender 侧的 `JobResult`。

## 3. 配置 Runtime

在根目录的 `__init__.py` 中创建 `JobRuntime` 实例：

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

这里的配置分别表示：

- `server:server`：从 `server/__init__.py` 取得名为 `server` 的对象
- `entrypoint_root`：相对 Server 入口的基准目录
- `storage_root`：Environment、Job 文件、模型和日志的持久目录
- `environment`：后端所需的 Python 与 packages
- `namespace`：BlendJob 内建 Operator 的 `bl_idname` 前缀

这里用 NumPy 演示业务依赖的声明方式；`double` Handler 本身无需 NumPy，删除 `packages` 后示例仍然可以运行。

## 4. 定义 Blender Operator

继续在 `__init__.py` 中定义业务 Operator：

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

这个例子会提交 `{"value": 21}`。Job 成功后，`response()` 在 Blender 主线程收到结果。

这里显式写出 `request()`，方便看清 Blender 与 Server 之间传递的数据。省略它时，`JobOperatorBase` 会把 Operator 声明的 Property 自动组成参数字典。`response()` 也可以省略；Job 成功后 Operator 会直接以 `FINISHED` 结束。

## 5. 首次使用时安装 Environment

继续在 `__init__.py` 中添加最小面板：

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

首次使用时面板显示 Environment 安装按钮，安装完成后显示业务按钮。国内网络环境可以在界面中提供 `example_job.install_environment_mirror`。

## 6. 注册 Extension

最后，在同一个 `__init__.py` 中注册业务类型与 Runtime：

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

`runtime.register()` 会注册 Environment 安装、取消 Job、Server 启停与日志相关 Operator，并把活动 Job 的进度放入 Blender 状态栏。

## 7. 安装并运行

1. 构建并安装 Extension。
2. 在 Blender Preferences 中允许 Online Access。
3. 打开 3D View 侧栏的 **Example** 页签。
4. 点击 **Install Environment**。
5. 安装完成后点击 **Double Value**。
6. Blender 信息区会显示 `Result: 42`。

第一次 Job 提交会启动当前 Blender 实例专属的本地 Server。后续 Job 复用同一进程与 Environment。

## 下一步

- 在 [Blender 集成](blender-integration.md)中学习自定义请求、结果导入和清理。
- 在 [Server 与 Job](server-jobs.md)中学习输出文件、进度与取消。
- 在 [AI 工作流](ai-workflows.md)中把最小示例扩展成模型下载与推理功能。
