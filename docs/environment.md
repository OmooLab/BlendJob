# Environment 与存储

BlendJob 为 Server 管理独立 Python Environment。Blender 进程保持轻量，任务依赖集中声明在 Runtime 配置中。

## 声明依赖

```python
environment = {
    "python": "3.10",
    "packages": [
        "numpy==2.4.2",
        "pillow==12.1.1",
    ],
    "platform_packages": {
        "windows": ["onnxruntime-directml==1.24.4"],
        "default": ["onnxruntime==1.24.4"],
    },
}
```

- `python`：独立 Environment 使用的 Python 版本，必填
- `packages`：所有平台安装的业务依赖
- `platform_packages`：为 `windows`、`macos` 或 `linux` 选择的依赖；缺少平台项时使用 `default`

BlendJob 会加入运行 Job Server 所需的 FastAPI 与 Uvicorn。随 Extension 分发的 BlendJob wheel 同时提供 Blender Client 与 Server Runtime，因此业务依赖列表专注于项目自己的 packages。

## 安装 Environment

`runtime.register()` 提供两个安装 Operator：

- `<namespace>.install_environment`：使用官方 uv、Python 与 PyPI 下载源
- `<namespace>.install_environment_mirror`：使用 CNB 的 uv Release、CNB 的 Python 镜像与清华 PyPI

安装过程依次准备 uv、Python 和 packages，并把实时消息与进度显示在 Blender 状态栏。安装源配置只作用于当前安装子进程。

Blender Preferences 中的 Online Access 需要处于允许状态。你可以根据用户区域在 Extension UI 中提供其中一个或两个安装入口。

## 配置变化

Runtime 会规范化 Environment 声明并计算稳定 Hash。成功安装后，Manifest 保存这个 Hash。当 Python、通用 packages 或平台 packages 改变时，`runtime.environment_ready()` 会返回 `False`，UI 可以再次显示安装按钮。

## Storage Root

`storage_root` 可以是路径，也可以是返回路径的 callable：

```python
runtime = JobRuntime(
    ...,
    storage_root=Path.home() / ".my-addon",
)
```

BlendJob 使用以下目录：

```text
<storage_root>/
├── .venv/
├── environment.json
├── install.log
├── install-status.json
├── jobs/
├── manifest.json
├── server.log
└── tools/
```

- `.venv/`：Server 的独立 Python Environment
- `environment.json`：本次期望安装的声明
- `manifest.json`：已安装 Environment 的版本与 Hash
- `jobs/<job-id>/`：每个 Job 的工作与输出目录
- `install.log`：Environment 安装输出
- `server.log`：Server 与 Handler 输出
- `tools/`：BlendJob 管理的 uv

`install-status.json` 在安装期间用于 UI 状态，安装任务收尾后会清理。项目可以在同一根目录下增加 `models/`、`cache/` 等领域目录。

## 日志与恢复

安装失败时查看 `install.log`，Server 启动或 Handler 失败时查看 `server.log`。Runtime 提供 `open_server_log` Operator，项目也可以在自己的设置面板中显示这些路径。

Environment 目录是可重新构建的数据。模型、用户下载与其它需要长期保留的内容放在 `.venv/` 之外。
