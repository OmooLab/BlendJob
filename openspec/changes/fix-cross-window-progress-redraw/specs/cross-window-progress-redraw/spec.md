## ADDED Requirements

### Requirement: Progress redraw reaches every open window

Runtime SHALL 在收到任务进度更新后，通过 Blender 正常 UI 事件循环更新每个打开窗口的可见状态栏，无需鼠标移动或其他用户输入。

#### Scenario: Download starts in independent Preferences

- **WHEN** 用户从独立 Preferences 窗口启动下载，Runtime 连续收到不同进度值
- **THEN** 主窗口状态栏自动显示最新进度与消息
- **AND** 鼠标保持静止时仍持续更新

#### Scenario: Multiple main windows

- **WHEN** 任务运行时存在多个带可见状态栏的窗口，包括共享 workspace 和使用不同 workspace 的窗口
- **THEN** 每个窗口都自动反映 Runtime 最新进度

#### Scenario: Task starts in main window

- **WHEN** 用户从主窗口启动普通 Server 任务
- **THEN** 进度继续自动显示，且同样覆盖其他打开窗口

### Requirement: Terminal states redraw all status bars

Runtime SHALL 在处理成功、失败或取消并清理 active_job 后，通知所有打开窗口刷新任务状态栏控件。

#### Scenario: Task reaches a terminal state

- **WHEN** Runtime 完成成功、失败、取消或停止 Server 所触发的任务收尾
- **THEN** 所有可见状态栏自动移除该任务的进度条和取消按钮
- **AND** 无需鼠标经过状态栏

### Requirement: Redraw uses valid main-thread window context

Runtime MUST 在主线程使用当前有效的窗口上下文刷新 UI，并安全处理没有可用窗口或 workspace 的情况。

#### Scenario: Global status bar is absent from Screen areas

- **WHEN** window.screen.areas 只包含普通编辑器区域
- **THEN** Runtime 仍触发该窗口全局状态栏的更新

#### Scenario: Window context is unavailable

- **WHEN** 刷新时窗口集合为空，或某目标窗口没有可用的 screen 或 workspace
- **THEN** Runtime 安全跳过不可用目标，并继续处理其他有效窗口
