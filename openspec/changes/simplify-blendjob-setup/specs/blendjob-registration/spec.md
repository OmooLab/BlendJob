## ADDED Requirements

### Requirement: Unified BlendJob instance

系统 SHALL 导出 BlendJob(namespace, *, server=None, environment=None, storage_root=None, post_install=None)，以可选关键字参数提供配置覆盖，保留实例 JobOperatorBase、request 和注册生命周期，并移除 JobRuntime 公开入口及兼容别名。

#### Scenario: Minimal instance
- **WHEN** 扩展有有效项目配置和默认 Server 包，开发者创建 BlendJob("anyimage")
- **THEN** 实例提供绑定的 JobOperatorBase 和 register/unregister，创建过程不创建存储目录、不安装环境、不启动 Server、不注册 Blender 类型

#### Scenario: Existing operator behavior
- **WHEN** 继承实例 JobOperatorBase 的 Operator 被执行、取消或完成
- **THEN** 保持现有 Server 请求、主线程响应、清理和跨窗口状态栏刷新行为

### Requirement: Predictable server and storage defaults

系统 SHALL 根据直接调用方文件定位 server/__init__.py:server，默认存储目录为 Path.home() / f".{namespace}"；namespace MUST 为合法小写标识符。显式 server SHALL 支持目录、文件和 path:attribute，相对路径基于调用方目录。

#### Scenario: Working directory independence
- **WHEN** cwd 改变但创建实例的文件位置不变
- **THEN** 默认 Server 与存储目录不变

#### Scenario: Factory and explicit entrypoint
- **WHEN** 工厂函数创建实例并传入绝对 Server 路径
- **THEN** 使用显式路径，不扫描上层调用栈猜测业务目录

#### Scenario: Invalid identity or unavailable caller
- **WHEN** namespace 包含路径分隔符，或默认定位所需的调用方文件不可用
- **THEN** 在执行文件操作前报出对应配置错误；仅在显式绝对 server 和 environment 足以完成配置时允许无调用方文件创建

### Requirement: Optional storage and installation callback

系统 SHALL 保留字符串、Path 或无参 callable 类型的 storage_root，以及普通 callable post_install；环境安装成功后以 BlendJob 实例调用回调。

#### Scenario: Preferences based storage
- **WHEN** storage_root callable 返回更新后的 Preferences 路径
- **THEN** 后续路径获取使用该值，不在实例创建时将其永久固定

#### Scenario: Post installation requests
- **WHEN** 成功安装环境且提供 post_install
- **THEN** 回调可通过传入实例的 request 提交 Server 任务，并沿用现有失败传播行为
