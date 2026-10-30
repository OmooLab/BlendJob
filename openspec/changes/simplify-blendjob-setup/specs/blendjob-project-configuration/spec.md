## ADDED Requirements

### Requirement: Environment configuration precedence

系统 SHALL 按显式 environment、所属项目 pyproject.toml、扩展根 blendjob.json 的顺序选择环境。跨越 Manifest 根目录的项目 MUST 通过 build.source 与扩展根路径匹配确认归属。有效来源中的错误 MUST 明确报出，不回退使用旧配置。

#### Scenario: Explicit environment
- **WHEN** 提供完整 environment 且附近存在不同配置文件
- **THEN** 完整使用显式环境，不与文件逐字段合并

#### Scenario: Development source with manifest
- **WHEN** src 下的扩展有 Manifest，项目 build.source 指向它，且本地存在旧 blendjob.json
- **THEN** 使用项目最新配置，Server 默认位置仍以调用方文件为基准

#### Scenario: Installed extension
- **WHEN** 扩展包只有有效 blendjob.json，外层 pyproject.toml 的 source 不匹配扩展根
- **THEN** 使用包内配置，不使用外层宿主项目

#### Scenario: Invalid project configuration
- **WHEN** 所属项目 TOML 损坏、缺少 blendjob 组或缺少 tool.blendjob.python
- **THEN** 报出文件与字段错误，不静默使用旧 JSON 或更远项目

### Requirement: Standard dependency group resolution

系统 SHALL 读取 dependency-groups.blendjob 与 tool.blendjob.python，按标准归一化组名、展开 include-group 并保留依赖标记；Server Python MUST 独立于 project.requires-python。无效要求、循环、缺失引用和归一化重名 MUST 被拒绝。

#### Scenario: Included group and platform marker
- **WHEN** blendjob 引用公共依赖组并包含 win32 条件依赖
- **THEN** 展开引用并保留条件字符串，最终由 Server 目标 Python 的安装流程评估标记

#### Scenario: Independent Python versions
- **WHEN** 项目 requires-python 为 3.11 范围且 tool.blendjob.python 为 3.12
- **THEN** 生成的 Server 环境使用 3.12

#### Scenario: Cyclic group
- **WHEN** include-group 存在直接或间接循环
- **THEN** 报出引用链，停止配置导出及环境安装

### Requirement: Portable generated environment

系统 SHALL 提供 blendjob.build.export_environment(project_file, destination)，生成包含 schema、python 和展开 packages 的 blendjob.json。运行时 SHALL 使用与源码配置一致的校验和环境哈希；生成文件 MUST 不包含机器路径、回调和构建设置。

#### Scenario: Export and reload
- **WHEN** 导出合法项目环境并在没有 pyproject.toml 的扩展中读取
- **THEN** 归一化环境与开发配置相同，环境哈希相同

#### Scenario: Invalid portable configuration
- **WHEN** 配置包含无法分发的本地路径依赖，或生成 JSON 损坏、schema 不支持
- **THEN** 导出或加载明确失败，不启动不完整环境
