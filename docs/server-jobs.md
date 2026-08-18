# Server 与 Job

`JobServer` 运行在独立 Python 进程中。项目通过装饰器注册 Handler，Handler 使用 `JobContext` 访问工作目录、进度、取消和 Resource。

## 注册 Handler

```python
from blendjob import JobServer


server = JobServer("Image Tools")


@server.job("resize-image")
def resize_image(context, parameters):
    source = parameters["input"]
    width = int(parameters["width"])
    context.progress(0.1, "Opening image")

    image = open_image(source)
    context.check_cancelled()

    context.progress(0.5, "Resizing image")
    resized = image.resize(width)

    output = context.directory / "resized.png"
    resized.save(output)
    return {"image": output.name, "width": width}
```

`job_type` 是 Blender Operator 与 Handler 之间的稳定名称。`parameters` 是 Operator 提交的字典。

## JobContext

Handler 常用的上下文成员如下：

| 成员 | 用途 |
| --- | --- |
| `job_id` | 当前 Job 的唯一 ID |
| `job_type` | 当前 Job 类型 |
| `storage_root` | Runtime 配置的持久根目录 |
| `directory` | 当前 Job 的独立工作目录 |
| `progress(value, message)` | 发布 0.0–1.0 进度与显示文本 |
| `check_cancelled()` | 在安全检查点响应取消 |
| `resource(name)` | 取得 Server Resource |

BlendJob 根据 Handler 的执行结果维护 `queued`、`running`、`cancelling`、`succeeded`、`failed` 和 `cancelled` 状态。

## 报告进度

```python
context.progress(0.05, "Loading input")
context.progress(0.40, "Running inference")
context.progress(0.90, "Saving output")
```

Runtime 把最新的进度和消息显示在 Blender 状态栏。对同一个 Job，界面进度保持单调递增。

## 响应取消

在长任务的自然分段之间调用：

```python
for index, batch in enumerate(batches):
    context.check_cancelled()
    process(batch)
    context.progress((index + 1) / len(batches), "Processing batches")
```

`check_cancelled()` 会结束 Handler，并将 Job 转为取消状态。把检查点放在批次之间、模型阶段之间或文件处理循环中，可以及时响应用户操作。

## 返回普通结果

Handler 可以返回任何可由 FastAPI 编码为 JSON 的值：

```python
return {
    "vertices": 1280,
    "duration": 1.42,
}
```

Blender 侧通过 `result.value` 读取它。

## 返回输出文件

把输出写入 `context.directory`，并返回相对文件名：

```python
output = context.directory / "surface.npz"
save_surface(output, vertices, faces)
return {"surface": output.name}
```

Blender 侧使用：

```python
surface_path = result.file("surface")
```

这种方式让每次调用的文件保持独立，也让 Blender 在读取前验证文件属于当前 Job Directory。

## 队列行为

Job Server 使用单 Worker FIFO Queue。提交会立即返回 Job ID 和目录；空闲 Job 开始运行，其余 Job 按提交顺序排队。排队中与运行中的 Job 都可以接收取消请求。

一个 Runtime 的交互式 Operator 同一时间管理一个活动调用。Server Queue 同时支持来自同步 `runtime.request()` 等其它调用路径的排队任务。

## 失败与日志

Handler 抛出的异常会把 Job 标记为失败，并把错误信息返回 Runtime。完整 traceback 写入 `server.log`，可通过 `<namespace>.open_server_log` Operator 打开。
