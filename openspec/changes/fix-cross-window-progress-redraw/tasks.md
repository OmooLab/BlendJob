## 1. 复现与确认

- [x] 1.1 在 Blender 中分别从主窗口和独立 Preferences 启动相同的确定性进度回放任务（复用下载的 JobOperatorBase 路径），记录 Runtime 进度与鼠标静止时状态栏显示，确认刷新遗漏。
- [x] 1.2 验证逐窗口 temp_override 下的状态栏通知，覆盖共享与不同 workspace，确定有效的定向刷新方式。

## 2. 公共刷新修复

- [x] 2.1 修改 Runtime 公共刷新入口，在主线程通知所有有效窗口的全局状态栏，处理缺少窗口、screen 和 workspace 的情况。
- [x] 2.2 检查进度更新与成功、失败、取消、停止 Server 的收尾调用链，确保统一刷新并避免同一轮重复通知。

## 3. 验证

- [x] 3.1 补充多窗口回归测试，使用不含 STATUSBAR 的 Screen 区域集合，覆盖不同及共享 workspace 与不可用窗口上下文。
- [x] 3.2 验证终态控件清理与既有 Header 回调恢复行为，运行 BlendJob 全部测试。
- [x] 3.3 在 Blender 实际界面验证 Preferences 与主窗口任务进度回放、多个主窗口以及成功、失败和取消后的状态栏；记录 Blender 版本与观察结果。
- [x] 3.4 检查关闭 Preferences 后的任务行为，区分刷新问题与 modal 生命周期问题，记录是否存在需另行处理的调度限制。

## 验证记录

2026-09-08，Windows，Blender 4.5.10 LTS。独立 factory-startup 会话通过真实 JobOperatorBase 的提交线程、modal timer 与 Header 绘制回调回放确定性进度，鼠标静止观察；此验证聚焦进度链，不执行外部模型下载。

旧刷新逻辑收到 32 次状态更新，两个主窗口状态栏均无绘制。最终实现的记录：

- new-preferences: 状态更新 32 次，两个主窗口分别绘制 33 / 33 次，结束后任务控件均清理。
- new-main: 状态更新 33 次，两个主窗口分别绘制 35 / 36 次，结束后任务控件均清理。
- new-failed: 状态更新 32 次，两个主窗口分别绘制 52 / 52 次，结束后任务控件均清理。
- new-cancelled: 状态更新 17 次，两个主窗口分别绘制 21 / 21 次，结束后任务控件均清理。
- new-stopped: 状态更新 16 次，两个主窗口分别绘制 18 / 18 次，结束后任务控件均清理。

- Preferences 运行中按 Alt+F4 关闭后，Operator 正常取消任务；两个主窗口清理控件，Blender 进程保持运行。
- 主窗口任务阶段将第二窗口切换到 Modeling，覆盖不同 workspace；Preferences 阶段两个主窗口共享 Layout。
- `uv run pytest -q`：54 passed；覆盖通知合并、关闭后读取有效窗口、临时区域、空闲提示保留、注销清理及终态。
