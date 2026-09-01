## Why

停止 Job Server 时，BlendJob 当前只关闭 Server 进程，不会同步结束 Blender Runtime 中的活动 Modal Job；消费者会遗留进度 UI、活动状态和延迟 cleanup。Stop Server 应成为完整的生命周期操作，同时取消活动 Job、等待 Server 停止并清理本地 Job 状态。

## What Changes

- 让 Stop Server 操作先取消 Server 中的活动 Job，再停止 Server 进程，最后关闭 Blender 侧活动 Job 状态。
- 保证停止过程中出现异常时仍执行本地 cleanup、清除活动状态并重绘 UI。
- 保持独立 Cancel Job、停止空闲 Server、手动重新启动 Server 的既有行为。
- 增加 Runtime 与 Modal Operator 测试，覆盖已提交 Job、提交中的 Job、空闲 Server、停止异常和停止后的 Modal 收尾。

## Capabilities

### New Capabilities

- `active-job-server-stop`: 规定停止 Job Server 时同步取消并关闭活动 Blender Job 的完整生命周期。

### Modified Capabilities

无。

## Impact

- 影响 `src/blendjob/runtime.py` 中生成的 Stop Server Operator 和 Runtime 活动 Job 生命周期。
- 影响 Runtime 与 Modal Operator 测试及 Blender 集成文档。
- Server Job 协议、业务 Operator 接口、Job 产物和 Server shutdown timeout 不变。
