## 1. Runtime 默认配置

- [x] 1.1 保留 JobRuntime 名称，以 namespace 为首参数，保留显式 environment 和可选 server、storage_root、post_setup，移除旧构造调用方式。
- [x] 1.2 实现调用文件旁的默认 Server 入口和用户目录下的默认存储，覆盖命名空间校验、显式覆盖、工厂调用和工作目录独立性。
- [x] 1.3 验证动态存储、安装完成回调、构造无运行副作用及运行时相关回归。

## 2. AnyImage 接入

- [x] 2.1 使用默认入口，保留现有环境字典、Preferences 存储和模型准备回调。
- [x] 2.2 在 server/__init__.py 直接创建 JobServer，按任务和资源工厂导入后端实现，删除 __getattr__ 与多余 app.py。
- [x] 2.3 更新并通过导入隔离、任务执行、错误清理和插件注册相关测试。

## 3. 完成验证

- [x] 3.1 将提案、设计和规格收敛至已确认的 Runtime 默认配置及 AnyImage 接入。
- [x] 3.2 通过 OpenSpec 严格校验并确认当前范围全部完成。
