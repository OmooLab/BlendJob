## ADDED Requirements

### Requirement: Autonomous runtime lifecycle
Runtime SHALL 使用唯一 Blender 主线程 timer 推进所有任务，维护状态同样持续轮询。接受任务后 SHALL 恰好一次完成通知及收尾，done 仅在完整收尾后为真。

#### Scenario: No modal events
- **WHEN** 直接提交任务或任务所属窗口关闭，后续没有 modal 事件
- **THEN** runtime 仍推进执行及资源释放；界面上下文失效导致的结果应用错误不阻断收尾

#### Scenario: Callback raises
- **WHEN** on_done 或 Operator response 抛出异常
- **THEN** 记录任务失败并释放，后续任务正常推进

### Requirement: Ordered completion and cancellation
Runtime SHALL 保持普通任务排队和成功结果按提交顺序应用，取消请求 SHALL 阻止之后到达的成功结果被应用；失败和取消 SHALL 及时收尾。

#### Scenario: Successful result races cancellation
- **WHEN** 对 A 请求取消后收到 A 成功状态
- **THEN** 不执行成功结果应用，A 完成取消收尾，其他任务继续

### Requirement: Maintenance lifecycle
setup/migrate SHALL 建立维护屏障，取消并等待其他任务回调与释放后维护；存储位置 SHALL 在主线程保存并确认，提交后取消 SHALL 被禁止。setup SHALL 遵守 Blender 网络设置。

#### Scenario: Migrate busy runtime
- **WHEN** A、B 未结束时提交 migrate
- **THEN** 阻止新普通任务，取消并收尾 A、B，随后迁移、保存位置并完成确认

#### Scenario: Save fails
- **WHEN** 迁移的配置保存抛出异常
- **THEN** 确认错误传回控制端，维持协议确定的存储位置并报告失败，不永久等待

#### Scenario: Post setup
- **WHEN** 安装成功并完成释放
- **THEN** 在主线程调用 post_setup(runtime)，允许提交模型准备任务；回调失败单独明确报告后续处理失败

### Requirement: Service controls and teardown
start/stop/restart SHALL 通过 submit 执行；stop/restart SHALL 先取消和释放其他任务，再完成服务切换。控制进程退出和 disable SHALL 完成受影响任务收尾并清理 timer。

#### Scenario: Restart queue
- **WHEN** 多个任务活动时提交 restart
- **THEN** 阻止新普通提交，取消和释放活动任务，重启完成后解除屏障

#### Scenario: Control process exits
- **WHEN** 控制进程异常退出
- **THEN** 相关句柄失败、回调及释放各执行一次，活动列表没有永久等待记录
