## Context

当前 operator.py 持有 JobOperatorState，通过 modal 调用 controller.submit/status，应用结果并清理。runtime.py 管理列表、取消及服务状态；maintenance.py 在 status 阶段完成主线程保存和确认。直接提交绕开 Operator 会缺少推进与收尾，当前 request_blocking 又单独维护循环。

现有控制进程与 JobServer 已承担执行及串行排队，主要调整 Blender 侧职责即可。工作区已有多任务改动，实施须以当时的源码为准保留有效行为。

## Goals / Non-Goals

本次范围为统一公开提交入口、runtime 生命周期、轻量任务句柄、Operator 适配和 AnyImage 调用迁移。沿用现有控制进程、HTTP 服务、注册机制和串行执行队列；安装构造配置及打包工作继续归属各自提案。

## Decisions

### 1. 一个提交方法、一份参数字典

```python
job = runtime.submit("setup", {"target": str(path), "mirror": True})
job = runtime.submit("migrate", {"target": str(path)})
job = runtime.submit("clear_resource", {"name": "model_manager"})
job = runtime.submit("generate_depth", {"input_path": str(image_path)})
```

签名为 `submit(name, parameters=None, *, on_done=None)`。参数为普通 JSON 可序列化字典，None 视为空字典，提交时复制为独立快照。同步检查名称、参数和执行资格，通过后返回 Job 句柄并自动安排执行；受队列或维护屏障约束时句柄处于 queued。执行异常写入句柄，参数错误在创建任务前抛出。

操作名和任务名统一采用小写 snake_case，格式为 `[a-z][a-z0-9]*(?:_[a-z0-9]+)*`。submit 与 JobServer.job 注册共用同一校验函数；非法名称立即报错，不自动替换连字符或保留别名。JobOperator.job_type 与服务端注册必须完全一致。

内置名称固定为 setup、migrate、clear_resource、preview_files、clear_files、start、stop、restart；其余名称派发给 JobServer。服务端注册任务时拒绝保留名称；未知普通任务由服务端按既有协议明确失败。使用单一常量定义名称集合，不建立动态动作注册框架。选择单一名称是为了让直接提交与 JobOperator 使用同一个表达。

setup 的 target 默认当前 storage_root，mirror 默认 False；online 在 Blender 内默认遵循 bpy.app.online_access，显式 True 也不能绕过禁用网络设置；独立 Python 默认 True。migrate 必须明确 target。runtime 对 target 做 Path 归一化，路径参数使用字符串进入序列化协议。内置操作逐项校验参数，防止拼错键后静默忽略。

### 2. Job 是状态和控制句柄

将 JobOperatorState 中执行相关字段迁到 jobs.py 的 Job。句柄公开 id、state、progress、message、done、result、error，以及 cancel()、wait()。id 提交时即稳定可用，内部控制进程 ID 单独保存；result 成功时为现有 JobResult，失败/取消时为空。state 使用 queued/running/succeeded/failed/cancelled，取消请求以独立标志表达，收到确认才终结。Job 不要求持有 Operator 或 Blender context。

Blender 中 submit 确保唯一主线程 timer 已启用，调用方无需先执行 enable、创建 Operator 或查询 status。读取句柄没有推进副作用。普通 Python 没有 Blender 事件循环时，`runtime.submit(...).wait()` 在调用线程驱动相同内部 tick，返回 JobResult，失败或取消抛出明确异常。Blender 后台模式同样允许 wait；交互 UI 内拒绝阻塞 wait 并提示使用完成回调。无需引入 asyncio 或额外线程。

### 3. runtime 唯一拥有生命周期

把校验、控制器选择、实际提交、状态读取、进度更新、取消竞争、结果构建、完成通知、release 和移出活动列表集中到 runtime 的一个 tick/finalize 路径。每个 tick 推进所有活动任务，安装、迁移期间也继续推进对应维护任务。服务端保持已有串行队列，客户端只保留活动记录与维护屏障，不再增加业务执行队列。

Blender timer 与脚本 wait 复用相同 tick。进度刷新按现有频率合并。成功结果及完成通知按提交顺序交付，取消/失败可及时收尾；取消已请求时不应用随后到达的成功结果。on_done(job) 在执行线程调用，Blender 中保证主线程；每个已接受任务恰好一次。回调异常记为该任务失败，finally 仍释放并继续推进后续任务。

完成顺序为构建结果、执行操作专属主线程完成逻辑、调用 on_done、释放控制器状态和移出活动列表。调用 on_done 时可以读取终态/result；done 在完整收尾后为真。防止 tick 重入；回调内新提交的普通任务加入下一轮，维护/stop 屏障等待当前回调和收尾结束。

setup/migrate 在维护开始前阻止普通提交，取消并等待现有任务完成回调与释放，再进入维护。awaiting_save 在主线程写入 storage_root 并确认；提交成功后禁止取消。迁移失败保持实际已提交位置与控制端状态一致。保留安装后准备模型的 post_setup：安装成功释放执行器并移出活动列表后在主线程调用，回调接收 runtime，并可直接 submit 后续任务；回调失败明确报告“安装已完成，后续处理失败”，不伪装成安装回滚。

start/stop/restart 也通过 submit 管理。stop/restart 先设置屏障、取消并收尾其他活动任务，等待实际停服/重启状态后终结自身。runtime enable/disable 继续表示调度与注册生命周期，避免与服务 start 混用。控制进程异常退出时所有相关任务终结；disable 先停止执行者，再统一收尾、移除 timer，不能依赖 modal 继续触发。

### 4. JobOperator 是 Blender 适配器

```python
class UnloadModels(runtime.JobOperator, bpy.types.Operator):
    bl_idname = "anyimage.unload_models"
    bl_label = "Unload Models"
    job_type = "clear_resource"

    def request(self, context):
        return {"name": "model_manager"}

    def response(self, context, result):
        return "Models unloaded"
```

execute 调用 request 得到参数，再调用 runtime.submit，并传入一个完成适配回调。该回调负责成功时 response、失败时 report，以及 finally cleanup。提交前失败同样 cleanup 恰好一次。保留原调用的必要 UI 上下文，使用仍有效的 window/area 做临时覆盖；业务数据目标在 request 中捕获。上下文或目标失效时明确失败并清理，不能操作当前新选中的对象。

modal 仅转发 ESC 取消并根据句柄结束 Blender Operator，不轮询控制器、不负责释放。UI 关闭或 modal 丢失不阻断 runtime；完成回调与清理由 runtime 驱动。窗口关闭且回调所需上下文已失效时跳过结果应用，报告失败并清理。清理文件的预览/确认两阶段保留，前一任务收尾后才能复用 Operator 状态发起下一阶段。有效窗口保留 modal timer 到下一次事件返回终态；窗口失效或插件注销时立即移除，任务的结果处理和输入清理均由 runtime 回调完成。

### 5. 执行选择属于 runtime 内部

runtime 按操作名直接选择普通任务、维护操作或服务控制的执行实现。安装和迁移需要保存配置与提交确认，保留相应内部控制器；能够直接复用现有进程通讯的操作保持直接调用。内部结构只服务现有职责，不建立公开控制器协议或动态注册机制。

删除 JobOperator.controller 方法、setup_controller/migrate_controller/action_controller 公开工厂；submit 仅接受操作名、参数和完成回调。JobOperator 直接调用 runtime.submit，业务功能通过 JobServer.job 注册。

取消独立 request_blocking、clear_resource 和 stop 的公开执行入口，迁移到 submit 和 Job.wait；底层进程通讯方法保持内部职责。

### 6. 文件与迁移边界

- runtime.py：提交、调度、维护互斥、完成与服务控制。
- jobs.py：轻量句柄与执行状态，替代 JobOperatorState。
- operator.py：属性参数、Blender 交互、结果与清理适配。
- maintenance.py：安装/迁移提交协议与保存确认。
- process.py：控制进程通讯，删除独立阻塞生命周期。
- 内置 Operator 和 AnyImage：改用 job_type、request，移除 controller 覆盖。
- 名称迁移：同步服务端注册、Operator.job_type、直接调用、示例和相关测试。例如 download-model → download_model、remove-background → remove_background、generate-depth-plane-geometry → generate_depth_plane_geometry；资源卸载直接使用内置 clear_resource，移除虚设的 clear-model-cache 任务名。Blender bl_idname、界面文字和 OpenSpec 目录名保持各自命名规范。

implementation 完成前同步调用、测试和注册/逆序注销检查；本轮不更新一般说明文档或构建扩展。按用户实施指令，在 BlendJob 构建临时 wheel 到 AnyImage，并刷新锁文件及安装依赖；AnyImage 只消费公开接口。与 simplify-blendjob-setup 合并时，以本提案的 submit 和 JobOperator 契约为准，构造名称调整单独处理。

## Risks / Trade-offs

- Blender 上下文生命周期短 → runtime 完成回调在主线程执行，Operator 适配器验证捕获的上下文和目标。
- 维护抢占与取消竞争 → 一个维护屏障、一个 finalize 路径，覆盖已取消但收到成功及保存确认竞争。
- 回调中继续提交 → 防止 tick 重入，并使新任务下一轮处理，安装后的任务在维护收尾后提交。
- 现有多个未提交变更 → 按真实调用链迁移，保留多任务功能，不以整文件覆盖已有修改。
- 固定保留名称占用用户任务名 → JobServer 注册时立即明确报错，调用者改用业务名称。

## Migration Plan

先实现句柄与 runtime 生命周期，再迁移维护和服务控制，再使所有 Operator 共用 submit，最后迁移 AnyImage 和删除重复路径。相关测试随阶段更新，执行测试前遵循用户当时的指令；当前要求暂不运行测试。回退使用对应实现变更整体回退，不添加兼容路径。

## Open Questions

无待定公开扩展接口；采用单一 submit 入口、snake_case 操作名、普通参数字典和单个完成回调。
