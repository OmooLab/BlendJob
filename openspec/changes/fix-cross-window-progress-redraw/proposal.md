## Why

从独立 Preferences 窗口启动模型下载时，主窗口状态栏进度需要鼠标经过才刷新。BlendJob 应让任务进度与完成状态在各窗口自动显示，保持不同启动入口的一致体验。

## What Changes

- 在 Runtime 公共 UI 刷新入口覆盖所有打开窗口的状态栏。
- 根据目标窗口上下文触发状态栏重绘，处理全局状态栏区域与普通 Screen 区域的区别。
- 覆盖进度更新、成功、失败和取消后的 UI 刷新。
- 补充多窗口回归测试和 Blender 实际界面验证。

## Capabilities

### New Capabilities

- `cross-window-progress-redraw`: 任务从任意窗口启动后，各窗口状态栏自动反映最新进度和终态。

### Modified Capabilities

## Impact

- `src/blendjob/runtime.py` 的公共刷新逻辑。
- `src/blendjob/operator.py` 的进度更新及终态调用链验证。
- `tests/test_runtime.py` 的窗口上下文与刷新覆盖测试。
- AnyImage 的 Preferences 下载入口作为实际复现场景。
