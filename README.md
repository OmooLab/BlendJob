# BlendJob

BlendJob 是面向 Blender Add-on 的本地 Job Server 运行时。它把 Blender Operator、独立 Python Environment、HTTP Job Server、FIFO Queue、进度、取消和 Resource 生命周期组合成一套可复用 API。

BlendJob 适合需要在 Blender 界面之外运行 AI 推理、模型下载、媒体处理或其它重型 Python 任务的 Add-on。Blender 主进程只保留轻量 Client 和 UI 逻辑，业务依赖安装在独立 Environment 中。

## 要求

- Python 3.10 或更高版本
- 支持 Python 3.10+ 的 Blender 版本
- Windows x64、macOS arm64 或 Linux x64；内建 uv 安装器目前只提供这些平台

## 安装

普通 Python 项目可以使用 pip 或 uv：

```bash
python -m pip install blendjob
```

```bash
uv add blendjob
```

默认安装保持零依赖，适合 Blender 主进程。需要在普通 Python 环境中直接创建 FastAPI App 或运行 Runner 时，安装 `server` extra：

```bash
python -m pip install "blendjob[server]"
```

Blender Extension 需要把 BlendJob wheel 随扩展分发，并在 `blender_manifest.toml` 中声明：

```toml
wheels = ["./wheels/blendjob-0.1.7-py3-none-any.whl"]
```

Runtime 会在独立 Environment 中自动安装与 Blender 侧相同版本的 BlendJob，使 Server Python 可以执行 `python -m blendjob.runner`。`packages` 只声明 Job 自身的额外依赖；没有额外依赖时可以省略：

```python
environment = {
    "python": "3.10",
}
```

## 最小示例

Server package 导出一个 `JobServer`：

```python
from blendjob import JobServer


server = JobServer("Example Job Server")


@server.job("double")
def double(context, parameters):
    context.progress(0.5, "Calculating")
    return {"value": parameters["value"] * 2}
```

Blender 侧创建一个 Runtime，并让业务 Operator 继承绑定后的基类：

```python
from pathlib import Path

from blendjob import JobRuntime


runtime = JobRuntime(
    "server:server",
    entrypoint_root=Path(__file__).parent,
    storage_root=Path.home() / ".example-addon",
    environment=environment,
    namespace="example_addon",
)

JobOperatorBase = runtime.JobOperatorBase
```

完整的 Runtime、Operator、Server Handler、Resource 和存储结构说明见 [开发者指南](https://docs.omoolab.xyz/blendjob/)。

## 开发

```bash
uv sync
uv run pytest
uv build
uv run mkdocs build --strict
```

版本化文档使用 Mike：

```bash
uv run mike deploy --update-aliases 0.1 latest
uv run mike set-default latest
```

## License

BlendJob 使用 [MIT License](https://github.com/OmooLab/BlendJob/blob/main/LICENSE)。
