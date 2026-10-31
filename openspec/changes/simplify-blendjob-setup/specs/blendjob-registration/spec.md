## ADDED Requirements

### Requirement: Simple JobRuntime construction

系统 SHALL 提供 JobRuntime(namespace, *, environment, server=None, storage_root=None, post_setup=None)。environment MUST 使用已有显式字典结构；实例 SHALL 保留 JobOperator、submit 和注册生命周期。

#### Scenario: Minimal instance
- **WHEN** 开发者提供命名空间和合法环境字典，调用方旁存在默认 Server 包
- **THEN** Runtime 完成配置，不导入后端、不创建存储目录、不启动服务或注册 Blender 类型

#### Scenario: Existing task behavior
- **WHEN** 任务提交、取消或完成
- **THEN** 保持 FIFO 执行、主线程响应、资源清理和进度刷新行为

### Requirement: Predictable entrypoint and storage defaults

系统 SHALL 使用直接调用文件旁的 server/__init__.py:server 作为默认入口，使用 Path.home() / f".{namespace}" 作为默认存储目录。namespace MUST 为非空 ASCII 小写标识符。

#### Scenario: Factory and working directory independence
- **WHEN** 工厂函数创建 Runtime，进程工作目录与工厂文件目录不同
- **THEN** 默认入口相对于工厂模块，默认存储位置相对于用户目录

#### Scenario: Explicit server
- **WHEN** server 指定文件、目录或 path:attribute
- **THEN** 使用指定入口，相对路径基于直接调用文件，未指定属性时使用 server

#### Scenario: No caller file
- **WHEN** 创建位置没有模块文件
- **THEN** 允许显式绝对 Server 入口，默认入口或相对入口明确报错

#### Scenario: Invalid namespace
- **WHEN** namespace 含路径分隔符、空白、大写或其他非法标识符内容
- **THEN** 在执行文件操作前报出配置错误

### Requirement: Custom storage and post-setup callback

系统 SHALL 保留自定义 storage_root 和 post_setup。动态存储 SHALL 在读取时调用设置函数；安装完成回调 SHALL 接收 Runtime 实例。

#### Scenario: Preferences storage
- **WHEN** 设置函数返回的路径发生变化
- **THEN** 后续读取获得新路径，构造时不固定该值

#### Scenario: Download after setup
- **WHEN** 安装成功且提供 post_setup
- **THEN** 在主线程调用回调，回调可通过 Runtime 提交模型下载任务

### Requirement: Lightweight server package

Server 包 SHALL 可以在 __init__.py 直接创建 JobServer、登记任务和资源工厂。AnyImage SHALL 在任务函数和资源工厂内部导入对应后端实现。

#### Scenario: Blender reads model metadata
- **WHEN** Blender 导入 AnyImage Server 包或其模型目录子模块
- **THEN** 任务和资源工厂已登记，后端任务实现、模型管理器及模型运行依赖尚未加载

#### Scenario: Server executes work
- **WHEN** Server 绑定存储或执行任务
- **THEN** 相应资源工厂或任务函数导入实现并执行，保留模型资源异常清理行为
