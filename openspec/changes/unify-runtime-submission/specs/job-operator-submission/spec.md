## ADDED Requirements

### Requirement: Operator submission adapter
JobOperator SHALL 使用 job_type 和 request 经 runtime.submit 提交普通任务或内置操作；JobOperator SHALL 移除 controller(runtime) 扩展方法。modal SHALL 不直接驱动 controller 或执行生命周期收尾。

#### Scenario: Clear resource operator
- **WHEN** Operator 的 job_type 为 clear_resource，request 返回资源名称
- **THEN** 按名称自动选择内部执行实现并完成操作，进度、取消和释放由 runtime 管理

### Requirement: Main thread response and cleanup
Operator 的 request、response、cleanup SHALL 在 Blender 主线程执行；request 捕获本次任务目标，成功调用 response，所有终结及提交前失败 SHALL cleanup 恰好一次。

#### Scenario: Original target unavailable
- **WHEN** 完成时原任务目标或必要窗口上下文失效
- **THEN** 报告结果应用失败并清理，不修改当前新目标，不阻断后续任务

#### Scenario: Preflight failure
- **WHEN** request 或 submit 校验失败
- **THEN** Operator 报告错误、清理一次并取消，不留下活动记录

### Requirement: Builtin and AnyImage migration
内置安装、迁移、文件清理和服务控制 Operator 及 AnyImage 资源卸载 SHALL 使用统一提交路径，文件清理 SHALL 保留预览确认与服务实例校验。

#### Scenario: Cleanup confirmation
- **WHEN** 文件预览完成后用户确认删除
- **THEN** 前一任务收尾后才提交 clear_files，服务实例变更时拒绝删除并提示重新预览

### Requirement: Consistent task name migration
BlendJob 示例、测试与 AnyImage 的服务端注册、job_type、直接提交 SHALL 同步采用 snake_case 任务名；资源卸载 SHALL 使用内置 clear_resource。

#### Scenario: Existing image task
- **WHEN** AnyImage 提交去背景任务
- **THEN** Operator 和 JobServer 均使用 remove_background，旧 remove-background 名称没有兼容别名
