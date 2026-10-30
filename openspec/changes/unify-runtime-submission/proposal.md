## Why

BlendJob 的任务生命周期分散在 JobOperator、runtime 和 controller 中，直接调用安装等操作需要创建控制器、提交、轮询和收尾。插件开发者应能直接调用 `runtime.submit()`，或继承 JobOperator，使用同一套完整执行流程。

## What Changes

- 新增统一入口 `runtime.submit(name, parameters=None, *, on_done=None)`，提交即接受执行并返回任务句柄。
- runtime 统一管理校验、提交、进度、取消、维护互斥、终态回调和资源释放；Blender 主线程定时器自动推进，直接调用无需 Operator 或手动轮询。
- JobOperator 保留 `job_type`、request、response、cleanup，通过 runtime.submit 提交；内置操作同样使用 job_type，只有一个操作名称入口。
- **BREAKING**：删除 JobOperator.controller 扩展方法和公开 controller 注入能力；runtime 内部按操作选择必要的执行实现。
- **BREAKING**：移除公开 setup_controller、migrate_controller、action_controller、独立 clear_resource/stop 提交入口与 request_blocking；独立脚本使用同一 submit 入口和任务句柄的 wait。
- **BREAKING**：内置操作和业务任务名称统一为 snake_case，submit 与 JobServer 注册使用同一命名校验；内置名称保留，冲突立即报错。同步迁移 BlendJob 示例、测试及 AnyImage 的任务注册、job_type 和直接提交，不保留旧名称别名。
- **BREAKING**：post_setup 在安装成功收尾后接收 runtime，便于直接提交后续任务。
- 保持多任务提交、按序应用结果、主线程配置写入及停止/重启协调行为。

## Capabilities

### New Capabilities

- `runtime-submission`: 统一提交协议、操作名称、参数、任务句柄和内部执行选择。
- `runtime-job-lifecycle`: runtime 驱动的进度、取消、维护、完成回调和退出处理。
- `job-operator-submission`: JobOperator 的提交适配、结果应用和 AnyImage 接入迁移。

### Modified Capabilities

无已归档的对应规格；本变更以新增规格明确契约。

## Impact

涉及 BlendJob runtime.py、operator.py、maintenance.py、process.py、server.py 及任务状态模块、相关测试，以及 AnyImage 的内置操作接入和业务任务命名。复用现有控制进程、服务端串行队列和 Blender timers，不增加依赖。

与 AnyImage 的 support-multiple-jobs 变更共享队列和结果顺序约束；本提案将其中生命周期归属明确为 runtime。与 simplify-blendjob-setup 的构造和打包工作独立，本提案沿用当前 JobRuntime、JobOperator 名称，后续合并时统一。实施阶段按用户要求将 BlendJob 临时 wheel 输出到 AnyImage 并刷新依赖；暂不运行测试、不构建扩展。
