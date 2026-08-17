# BlendJob

BlendJob 是面向 Blender Add-on 的本地 Job Server 运行时。它把 Blender Operator、独立 Python Environment、HTTP Job Server、FIFO Queue、进度、取消和 Resource 生命周期组合成一套可复用 API。

## 安装

```bash
uv add blendjob
```

Blender Extension 可以构建 wheel 后在 `blender_manifest.toml` 中声明：

```toml
wheels = ["./wheels/blendjob-0.1.1-py3-none-any.whl"]
```

## 开发

```bash
uv sync
uv run pytest
uv build --wheel
uv run mkdocs serve
```

版本化文档使用 Mike：

```bash
uv run mike deploy --update-aliases 0.1 latest
uv run mike set-default latest
```

完整 API、Runtime 接入和 Server Handler 示例见 [docs/index.md](docs/index.md)。
