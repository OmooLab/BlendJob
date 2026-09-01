## ADDED Requirements

### Requirement: Stopping the Server terminates the active Job

系统 MUST 在 Stop Server 操作发现活动 Job 时取消该 Job、停止 Server，并结束对应的 Blender Modal Job 生命周期。

#### Scenario: Active Server Job is stopped with the Server

- **WHEN** 调用方在活动 Job 已提交给 Server 后执行 Stop Server
- **THEN** 系统向活动 Job 发送取消请求，停止 Server，并以取消结果结束 Blender 侧 Modal Job

#### Scenario: Job submission is still in progress

- **WHEN** 调用方在活动 Job 的提交线程尚未返回 Job ID 时执行 Stop Server
- **THEN** 系统记录取消状态，使提交不再开始或在取得 Job ID 后立即取消，并完成 Server 与 Blender 侧收尾

#### Scenario: No active Job exists

- **WHEN** 调用方在 Server 空闲且没有活动 Blender Job 时执行 Stop Server
- **THEN** 系统停止 Server，且不执行不存在的 Job 取消或 cleanup

### Requirement: Server shutdown precedes local Job cleanup

系统 SHALL 在活动 Job 的 Server 执行已经退出或 Server 停止步骤已经结束后，才执行对应业务 Operator 的 cleanup，并 MUST 在停止异常时仍清除 Blender Runtime 的活动 Job 状态。

#### Scenario: Active Job owns temporary input

- **WHEN** Stop Server 取消一个可能仍在读取临时输入的活动 Job
- **THEN** 系统先完成 Server 停止步骤，再执行一次业务 Operator cleanup

#### Scenario: Server stop raises an error

- **WHEN** Server 停止步骤以异常结束
- **THEN** 系统仍执行一次活动 Job cleanup、清除 Runtime 活动状态并重绘 UI

#### Scenario: Modal event arrives after stop

- **WHEN** Stop Server 已关闭 Runtime 活动状态，原业务 Modal Operator 随后收到 Timer 事件
- **THEN** 该 Operator 返回 `CANCELLED`，且不再次执行 cleanup

### Requirement: Runtime remains manually restartable after stop

系统 SHALL 在 Stop Server 完成后保持 Server 为 STOPPED 且禁用自动启动，直到调用方显式启动 Server 或新的既有启动路径启用它。

#### Scenario: Poll occurs after stop

- **WHEN** Stop Server 完成后 Runtime 继续轮询且调用方尚未启动 Server
- **THEN** Server 保持 STOPPED，不因轮询重新启动

#### Scenario: Caller starts the Server again

- **WHEN** 调用方在完成 Stop Server 后执行 Start Server
- **THEN** 系统按既有启动流程创建可接受新 Job 的 Server
