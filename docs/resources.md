# Server Resource

Server Resource 用于保存跨 Job 复用的对象，例如 AI 模型、ONNX Session、媒体解码器、数据库连接或内存缓存。它们随 Server 创建，并在 Server 关闭时释放。

## 注册延迟 Resource

使用 `@server.resource()` 注册工厂：

```python
@server.resource("model_manager")
def create_model_manager(job_server):
    return ModelManager(job_server.storage_root / "models")
```

工厂在 Runtime 绑定 Storage Root 后执行，因此可以安全地从 `job_server.storage_root` 创建项目目录。

## 在 Handler 中使用

```python
@server.job("run-inference")
def run_inference(context, parameters):
    manager = context.resource("model_manager")
    model = manager.get(parameters["model"])
    return model.predict(parameters["input"])
```

Resource 实例在同一 Server 的多个 Job 之间复用。对大型模型而言，这可以保留已加载权重或 Session，减少重复初始化。

## 向 Blender 暴露状态

实现 `snapshot()`，为 Runtime 提供可序列化状态：

```python
class ModelManager:
    def snapshot(self):
        return {
            "downloaded": sorted(self.downloaded_models()),
            "loaded": sorted(self.loaded_models),
        }
```

Blender 侧同步查询：

```python
status = runtime.resource("model_manager")
is_ready = "depth-v1" in status["downloaded"]
```

这个模式适合在 Panel 中显示模型是否已下载、当前加载的模型或缓存大小。

## 清理内存状态

Resource 实现 `clear()` 后，可以在 Server 空闲时从 Blender 调用：

```python
class ModelManager:
    def clear(self):
        self.loaded_models.clear()


runtime.clear_resource("model_manager")
```

`clear()` 通常释放内存或显存，同时保留磁盘模型。Server 会协调清理与 Job 提交，使 Resource 在空闲状态完成清理。

## 关闭 Resource

Resource 可以实现 `close()`：

```python
class ModelManager:
    def close(self):
        self.loaded_models.clear()
        self.executor.shutdown()
```

Server 关闭时等待活动 Handler 收尾，然后按注册的逆序调用 Resource 的 `close()`。适合关闭线程池、文件句柄或原生 Runtime Session。

## 直接添加实例

普通 Python 测试或已绑定 Storage Root 的 Server 也可以添加现有实例：

```python
server.add_resource("cache", Cache())
```

Extension 项目通常优先使用延迟工厂，让目录配置和 Resource 创建保持在同一个 Server 生命周期中。
