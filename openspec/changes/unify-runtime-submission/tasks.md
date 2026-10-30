## 1. 执行模型与提交入口

- [x] 1.1 对照当前工作区梳理全部提交、控制器和完成回调调用，保留已有多任务改动并确认与 simplify-blendjob-setup 的接口契约。
- [x] 1.2 将执行状态迁至 jobs.py 的 Job，提供稳定 ID、状态、结果、取消和 wait；移除对 Operator/context 的必需依赖。
- [x] 1.3 实现 runtime.submit 的参数快照、名称校验、执行资格和内部控制器选择；统一 snake_case 校验，固定保留名称并在 JobServer 注册时检查格式和冲突。
- [x] 1.4 删除公开控制器工厂、JobOperator.controller 扩展方法及 controller 注入参数，仅保留现有维护等职责必需的内部控制器。

## 2. Runtime 生命周期

- [x] 2.1 实现唯一 tick/finalize 路径，统一提交、进度、按序成功通知、失败、取消、release 和活动记录移除。
- [x] 2.2 接入 Blender 主线程 timer，提交自动启用推进，安装与迁移期间持续处理状态；处理回调异常、重入和回调内提交。
- [x] 2.3 将 setup/migrate 参数统一到提交字典，保留维护屏障、网络设置、主线程位置保存确认、提交后的取消限制。
- [x] 2.4 将 start/stop/restart 纳入统一任务生命周期，处理全队取消、服务切换、控制进程退出和 disable 清理。
- [x] 2.5 将 post_setup 调整为收尾后接收 runtime；失败明确区分安装成功和后续回调失败。
- [x] 2.6 让独立脚本及 Blender 后台的 Job.wait 复用 tick，交互 UI 拒绝阻塞等待；删除旧 request_blocking 执行路径。

## 3. Operator 与消费端

- [x] 3.1 将 JobOperator.execute 改为 request 后调用 runtime.submit；移除 modal 的控制器轮询和收尾职责。
- [x] 3.2 实现主线程完成适配回调，验证上下文和目标，保证 response/cleanup 和提交失败处理正确且 cleanup 恰好一次。
- [x] 3.3 将安装、迁移、清理文件及服务控制 Operator 改为统一 job_type/参数，保留文件预览确认流程和实例校验。
- [x] 3.4 将 AnyImage 资源卸载迁移至内置 clear_resource，删除 controller 覆盖；同步 BlendJob 示例、测试和 AnyImage 服务端注册、job_type、直接提交的 snake_case 名称，检查注册与逆序注销。
- [x] 3.5 移除公开 clear_resource/stop 重复执行入口，更新真实调用方及测试，不留兼容别名。

## 4. 验证

- [x] 4.1 更新相关测试：直接提交无人轮询、参数、snake_case 和保留名称校验、普通任务及内部维护生命周期、脚本 wait。
- [x] 4.2 补充关键生命周期行为覆盖：FIFO 结果、取消竞争、回调异常、维护保存失败、post_setup、服务重启、进程退出及窗口关闭。
- [x] 4.3 更新 AnyImage 内置操作接入测试，检查旧工厂、旧状态对象和独立生命周期引用已清除，执行静态检查。
- [ ] 4.4 在用户允许运行测试后，仅运行相关 BlendJob/AnyImage 测试并确认正常退出；当前暂不运行测试，本任务在实际执行前保持未完成。

## 5. 临时 wheel 同步

- [x] 5.1 在 BlendJob 构建临时开发 wheel 到 AnyImage/src/anyimage/wheels，保持功能实现位于 BlendJob。
- [x] 5.2 刷新 AnyImage 的本地 wheel 锁文件并重装 BlendJob，核对 Manifest 和依赖声明；不构建扩展包。
