## Context

BlendJob 0.1.16 的 JobOperatorBase 每 0.25 秒轮询任务，并调用 Runtime 更新进度及强制刷新。redraw_ui 遍历 window.screen.areas，但 Blender 状态栏属于 Window 的全局区域；force 分支仅对当前 context.workspace 调用 status_text_set_internal(None)。从 Preferences 发起下载后主窗口依赖鼠标触发刷新，符合目标窗口通知遗漏的表现，仍需实际 UI 复现确认。

参考：https://developer.blender.org/docs/features/interface/screen/

## Goals / Non-Goals

**Goals:**

- 任意窗口启动任务后，所有可见状态栏自动显示进度，并在任务终止后清理任务控件。
- 统一通过 Runtime 主线程刷新入口处理窗口通知。

**Non-Goals:**

- 本地任务进度 API 留待独立变更。

## Decisions

### 按目标窗口上下文刷新状态栏

普通区域继续通过 tag_redraw 刷新。update_ui 与停止任务请求强制刷新时，注册一次性的主线程 timer；同一轮请求合并为一个回调。回调使用 bpy.context 与当前窗口集合，对拥有全局状态栏的有效主窗口分别执行 temp_override 和 status_text_set_internal(None)。共享 workspace 的窗口也分别通知。

窗口销毁触发 Operator.cancel 时，先完成任务清理，再由下一轮 timer 通知仍存在的窗口。临时 Preferences 窗口只刷新其普通编辑器区域。Runtime 注销时移除待执行的状态栏 timer。

### 公共入口覆盖整个生命周期

进度更新、成功、失败和取消由 update_ui 请求状态栏刷新；停止任务在清理后请求刷新。Operator 删除重复的强制刷新调用。普通 Server 轮询保持区域重绘语义，保留空闲时的状态栏提示。

### 行为测试与实际绘制共同验证

单元测试用不含 STATUSBAR 的 screen.areas 建模窗口，检查每个窗口上下文都收到通知，包含共享 workspace、多个 workspace、无窗口以及目标缺少 screen/workspace 的情况。现有 Header 回调恢复测试继续覆盖 UI 重建行为。

实际 UI 在独立 Blender 会话中用确定性的 controller 回放进度，经真实 JobOperatorBase.modal 与状态栏 Header 绘制，对比主窗口和独立 Preferences 启动入口：记录收到的进度值与界面显示，鼠标静止观察连续变化，再验证成功、失败和取消后控件清理。两个主窗口分别使用相同和不同 workspace，验证所有可见状态栏。

## Risks / Trade-offs

- 通知方法可能仍依赖 Blender 当前窗口 → 先完成双窗口最小复现，验证 override 的实际效果，再固化实现。
- Mock 无法证明真正发生绘制 → Blender UI 验证是验收必需项。
- Preferences 关闭可能影响其 modal timer → 记录实际行为；若进度采集也中断，区分调度问题并明确后续处理范围。
- 状态栏通知可能影响现有提示 → 验证运行时没有留下空白或陈旧提示，保持其他插件 Header 回调完整。
