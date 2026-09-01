## Context

See `proposal.md` - Why。`JobRuntime` 生成 Start、Stop、Restart 与 Cancel Job Operator，并用 `active_job` 保存当前 Modal Job 状态。当前 Stop Server Operator 仅调用 `ServerController.stop()`；Server 会等待活动 Job 结束或超时终止，但 `active_job`、进度 UI 与业务 Operator 的 `cleanup()` 仍由后续 Modal 轮询收尾。

活动 Job 可能处于提交线程尚未返回、Server 已开始执行或等待下一次 Modal 轮询三种状态。停止流程必须先传递取消意图，避免 graceful shutdown 等待完整 Job；同时必须等 Server 不再读取 Job 输入后才能 cleanup，防止提前删除临时文件。

## Goals / Non-Goals

**Goals:**

- 一次 Stop 操作同时取消活动 Server Job、停止 Server 并结束 Blender 侧 Modal Job 生命周期。
- 保证业务 Operator cleanup 只执行一次，并在 Server 停止步骤结束后执行。
- 让停止空闲 Server、独立 Cancel Job 与下一次手动启动保持现有语义。

**Non-Goals:**

- 不改变消费者如何绘制 Server 控件，也不规定 BUSY 状态下的面板布局。
- 不让 Stop 自动重启 Server，也不改变 Restart Server 的语义。
- 不改变 Job 内部取消检查密度、shutdown timeout 或强制终止策略。
- 不改变 Job 协议、产物、错误格式或单 Worker 队列。

## Decisions

### 1. JobRuntime 提供单一复合停止入口

新增 Runtime 级 `stop()` 作为生成 Stop Server Operator 的唯一执行入口：

1. `cancel_active()` 标记活动状态，并在已有 Job ID 时发送取消请求。
2. `ServerController.stop()` 关闭自动启动，等待 Server 响应取消并退出，沿用既有超时终止兜底。
3. 在 `finally` 中调用 `close_active()`，移除活动状态并执行 Operator cleanup。
4. 同一 `finally` 中重绘 UI，使状态栏和消费者面板立即读取终态。

把 `close_active()` 放在 Server 停止之后，避免 cleanup 删除 Server 仍可能读取的临时输入。使用 `finally` 保证停止异常不会把本地 Runtime 永久留在 busy 状态。

不把这组调用直接留在生成 Operator 中：独立 Runtime 方法更容易测试，也为没有 Blender UI 的调用方提供相同停止语义。

### 2. 提交中的 Job 复用现有 cancelled 状态

若 Stop 发生在提交线程返回 Job ID 前，`cancel_active()` 先设置 `job.cancelled`。提交线程在发起请求前看到该状态时直接结束；若请求已在途，则取得 Job ID 后立即调用 Controller cancel。Server 停止完成后再关闭 Runtime 状态，不引入线程终止、额外锁或新协议。

### 3. Modal Operator 通过缺失活动状态自然退出

Stop 完成后 `active_job` 为 `None`。原 Modal Operator 在下一次 Timer 事件中移除自身 Timer 并返回 `CANCELLED`，不再次调用 cleanup。这样保持现有 Modal 状态机，不增加 Stop 专用事件或回调。

### 4. 测试固定顺序和一次性收尾

Runtime 测试使用记录型 Controller、Server 和活动 Job，验证已提交 Job 的调用顺序为 cancel、Server stop、mark complete、cleanup；空闲路径只停止 Server；停止异常仍 cleanup 和重绘。

提交竞争测试让 Server stop 等待在途 submit 返回，验证 cancelled 状态触发取消且 cleanup 位于 Server stop 结束之后。Modal 测试验证停止后的 Timer 事件返回 `CANCELLED` 且 cleanup 不重复。

## Risks / Trade-offs

- [Job 只在阶段边界检查取消时，Stop 仍可能等待] → 沿用 shutdown timeout，超时后终止 Server 进程。
- [Server 停止异常时远端进程状态可能不确定] → 始终清理本地活动状态并重绘；Controller 保留既有进程兜底。
- [提交线程可能在 Stop 期间才取得 Job ID] → 复用提交返回后的 cancelled 检查发送取消，Server stop 继续负责等待或终止。
- [Stop 会放弃活动 Job 的后续结果] → 这是停止 Server 的明确语义；cleanup 仅在停止步骤结束后执行。

## Migration Plan

1. 增加 Runtime 与 Modal 生命周期测试。
2. 实现 Runtime 复合 `stop()` 并让生成 Operator 调用。
3. 更新中英文 Blender 集成文档，提升包版本并运行完整测试。
4. 构建 wheel 并验证版本和内容。

该变更没有数据迁移。回退时恢复 Stop Server Operator 的直接 Controller 调用；Storage Root、Environment 与已有 Job 目录无需转换。

## Open Questions

无。
