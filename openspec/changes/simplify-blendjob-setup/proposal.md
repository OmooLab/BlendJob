## Why

当前 JobRuntime 的创建要求开发者同时理解入口路径、命名空间、存储目录和环境结构，接入成本高；环境声明移入 pyproject.toml 后，还需要可靠地随扩展发布。通过统一的 BlendJob 实例和命令行工具，让配置、开发依赖准备与发布构建形成完整流程。

## What Changes

- **BREAKING**：以 `BlendJob(namespace, **options)` 替换 `JobRuntime`，移除旧公开名称和转发层；保留实例上的 `JobOperatorBase` 及现有任务生命周期。
- 默认从创建实例的文件定位 `server/__init__.py:server`，存储目录使用 `Path.home() / f".{namespace}"`；保留入口、环境、存储路径及 `post_install` 的可选覆盖。
- 自动读取最近项目的 `dependency-groups.blendjob` 和 `tool.blendjob.python`；发布时生成 `blendjob.json`，运行时与开发配置使用一致的环境校验。
- 新增默认构建命令 `uv run blendjob`，独立准备命令 `uv run blendjob wheel`，平台使用 `-p` / `--platform`，支持 `uv run blendjob -p windows-x64`。
- wheel 命令准备源码中的 wheels、Blender Manifest 和环境配置；默认命令在临时目录准备并输出扩展 ZIP。
- 构建配置明确 Blender 端依赖目标与 Server 环境的职责，提供环境配置导出函数供已有构建脚本接入。

## Capabilities

### New Capabilities

- `blendjob-registration`: 简化实例创建、默认路径、可选参数和现有注册生命周期。
- `blendjob-project-configuration`: 项目环境声明、配置解析优先级与发布配置导出。
- `blendjob-extension-build`: 默认构建、wheel 准备、平台选择与扩展产物规则。

### Modified Capabilities

无。

## Impact

涉及 BlendJob 的公开导出、runtime、operator 和环境配置边界，以及新增 CLI、构建模块和测试。CLI 必须能在没有 bpy 的开发环境运行；构建专用依赖放入可选 build extra。当前仅编写提案，后续实现不构建实际发布产物。AnyImage 的接入迁移、节点资产构建和模型发布另行处理。
