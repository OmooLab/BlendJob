## ADDED Requirements

### Requirement: Unified submission
Runtime SHALL 提供 submit(name, parameters=None, *, on_done=None)，内置操作和普通任务共用入口。参数 SHALL 在提交时校验并形成独立 JSON 快照；已接受任务返回稳定 ID 的句柄并自动安排执行。

#### Scenario: Direct setup
- **WHEN** Blender 调用 runtime.submit("setup", {"target": path}) 且安装条件满足
- **THEN** 返回任务句柄，runtime 自动安装和收尾，调用方无需 Operator 或控制器

#### Scenario: Invalid parameters
- **WHEN** 参数不可序列化、内置参数键错误或运行状态不允许操作
- **THEN** 提交同步报错，不留下活动任务或启动维护

### Requirement: Operation names
操作名与业务任务名 SHALL 使用小写 snake_case，格式为 `[a-z][a-z0-9]*(?:_[a-z0-9]+)*`。submit 与 JobServer 注册 SHALL 使用同一校验函数，非法名称 SHALL 同步报错，不自动归一化或保留旧名别名。系统 SHALL 保留 setup、migrate、clear_resource、preview_files、clear_files、start、stop、restart；其余名称派发至 JobServer，注册保留名称 SHALL 报错。

#### Scenario: Snake case task
- **WHEN** JobServer 注册 generate_depth，调用者以相同名称提交
- **THEN** 任务正常路由到对应处理函数

#### Scenario: Hyphenated name
- **WHEN** 调用者注册或提交 generate-depth
- **THEN** 同步报告名称格式错误，不创建任务，不自动转换名称

#### Scenario: Conflicting registration
- **WHEN** JobServer 注册 setup 业务任务
- **THEN** 明确拒绝名称冲突

#### Scenario: Unknown business job
- **WHEN** 提交未注册普通任务名
- **THEN** 任务以明确错误终结并完成释放

### Requirement: Internal execution selection
Runtime SHALL 在内部选择操作的执行实现；公开 API SHALL 仅通过 submit 提交，移除公开控制器工厂、controller 注入参数和 JobOperator.controller 扩展方法。维护所需的保存确认 SHALL 保留在内部实现。

#### Scenario: Internal maintenance handling
- **WHEN** 调用者直接提交 migrate
- **THEN** runtime 自动选择维护执行实现并完成保存确认，调用者无需构造或传入控制器

### Requirement: Job handle and script wait
Job SHALL 提供状态、进度、结果、错误、cancel 和 wait，读取状态 SHALL 无推进副作用。wait SHALL 在独立脚本或 Blender 后台模式复用 runtime tick，交互 Blender 内 SHALL 拒绝阻塞等待。

#### Scenario: Script execution
- **WHEN** 独立脚本调用 runtime.submit(name, parameters).wait()
- **THEN** 使用统一生命周期执行和释放，成功返回 JobResult，失败或取消抛出明确异常
