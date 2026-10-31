## Why

JobRuntime 的入口路径和默认存储位置可以由固定约定确定，减少项目接入时的重复配置。环境使用普通 Python 字典，与 Runtime 一起随源码发布，开发者可以直接查看和修改。

## What Changes

- **BREAKING**：保留 JobRuntime 名称，改为 JobRuntime(namespace, *, environment, server=None, storage_root=None, post_setup=None)；移除旧构造调用方式。
- 默认使用直接调用文件旁的 server/__init__.py:server，以及用户目录下的 .<namespace> 存储目录。
- 保留显式 Server 入口、自定义存储和安装完成回调。
- AnyImage 使用默认入口；server/__init__.py 直接创建 JobServer 并注册任务，后端实现按任务执行和资源创建时机导入。
- 更新构造、导入边界和任务生命周期相关测试。

## Capabilities

### New Capabilities

- blendjob-registration：简化 Runtime 创建、默认路径及 Server 定义，保留环境字典和既有生命周期。

### Modified Capabilities

无。

## Impact

涉及 BlendJob 的 Runtime 构造、入口解析和相关测试，以及 AnyImage 的 Runtime 接入、Server 定义和对应测试。
