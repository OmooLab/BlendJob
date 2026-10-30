## ADDED Requirements

### Requirement: Default build and independent wheel command

CLI SHALL 提供默认构建动作及独立 wheel 子命令，支持 -p 和 --platform，平台选项可置于 wheel 前后。无平台的构建 SHALL 选择所有配置平台，无平台的 wheel SHALL 选择当前主机平台。重复平台参数和未知平台 MUST 报错。

#### Scenario: Short build command
- **WHEN** 执行 blendjob -p windows-x64
- **THEN** 只构建 Windows x64 扩展，无需额外子命令

#### Scenario: Wheel command
- **WHEN** 执行 blendjob wheel -p windows-x64 或 blendjob -p windows-x64 wheel
- **THEN** 两者均只准备该平台源码依赖和配置，不输出 ZIP

#### Scenario: Missing build dependencies
- **WHEN** CLI 在没有 bpy 且缺少构建 extra 的开发环境被调用
- **THEN** 导入不因 bpy 失败，执行所需构建依赖检查提示安装 blendjob[build]

### Requirement: Explicit extension build targets

构建 SHALL 从最近项目文件读取 source、output、platforms、targets；source 和 targets 必填，output 默认 dist，platforms 默认 windows-x64、macos-arm64、linux-x64。source MUST 在项目目录内，output MUST 不在 source 内。Manifest SHALL 提供扩展身份及输出版本。

#### Scenario: Project relative paths
- **WHEN** 从项目子目录执行命令
- **THEN** 配置路径基于项目文件解析，不随 cwd 改变产物位置

#### Scenario: Invalid target configuration
- **WHEN** targets 为空、source 不合法或 output 位于源码内
- **THEN** 在下载和文件修改前失败并报告具体配置

### Requirement: Target specific Blender wheels

构建 SHALL 为每个目标平台和 Python 解析 project.dependencies，仅接受 wheels，正确处理目标环境标记；provided-packages SHALL 参与兼容约束但不随包携带。多 Python 目标 SHALL 合并兼容 wheels，允许从源码 wheels 读取本地候选。Server 依赖组 SHALL 仅导出环境配置。

#### Scenario: Cross platform preparation
- **WHEN** Windows 主机构建 Linux 平台且依赖含平台标记
- **THEN** 选择 Linux 目标的依赖和 wheels，不按 Windows 宿主标记筛选

#### Scenario: Blender provided dependency
- **WHEN** 目标声明 Blender 提供某个 NumPy 范围
- **THEN** 解析使用该范围且最终 wheels 不包含 NumPy；与其他依赖冲突时失败

#### Scenario: Multiple Python targets
- **WHEN** 项目配置多个 Blender Python 版本
- **THEN** 收集各目标二进制 wheels，纯 Python 包保持共同兼容版本，无法满足共同版本时报告冲突

### Requirement: Source preparation with recovery

wheel SHALL 在临时目录完成下载和校验后，同步源码 wheels、目标 Manifest 和 blendjob.json，并清理未选中的旧 wheel。提交失败 MUST 恢复三者原状态。

#### Scenario: Successful preparation
- **WHEN** wheel 完成
- **THEN** 源码 Manifest 引用存在的目标 wheels，生成配置可加载，未生成扩展 ZIP

#### Scenario: Download or commit failure
- **WHEN** 下载失败或提交任一文件失败
- **THEN** 原 wheels、Manifest 和配置保持或恢复为执行前内容

### Requirement: Isolated extension archives

默认构建 SHALL 在临时目录准备每个平台产物，保持源码不变，并输出 <name>.v<version>.<platform>.zip。归档 SHALL 携带目标 Manifest、wheels 和 blendjob.json，遵循 Manifest build.paths_exclude，排除缓存及 Blender 备份，保留 Manifest 其他字段，拒绝跟随 source 外符号链接。

#### Scenario: Build complete archive
- **WHEN** 默认构建成功
- **THEN** ZIP 包根目录包含全部目标文件，可在无项目文件时加载环境，源码内容未改变

#### Scenario: Existing output and partial failure
- **WHEN** 多平台构建中某个平台失败
- **THEN** 已有有效 ZIP 不被不完整文件覆盖，命令失败退出并报告已完成及失败的平台
