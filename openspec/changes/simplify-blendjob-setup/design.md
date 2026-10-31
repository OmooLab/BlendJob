## Context

AnyImage 通过 JobRuntime 管理独立 Server。Blender 主进程也读取 server 包中的模型目录和公共数据，因此包入口需要保持轻量。

## Goals

开发者只需填写命名空间和环境字典，特殊需求使用明确的可选参数。

## Decisions

### 1. Runtime 创建

    runtime = JobRuntime("example", environment=ENVIRONMENT)

environment 沿用现有 python、packages、platform_packages 字典。配置与 Python 源码一起发布，使用已有环境校验和哈希。

保留 JobRuntime、JobOperator、submit、post_setup 及注册生命周期。namespace 为非空 ASCII 小写标识符。

### 2. Server 入口

默认入口为直接调用文件旁的 server/__init__.py:server。调用方位置由直接调用帧的模块文件确定，工厂函数以工厂模块为准；解析过程不导入入口模块。

可选 server 接受文件、目录或 path:attribute。文件或目录默认导出 server；相对路径以调用方目录为基准。没有调用方文件时，需要显式绝对入口。

### 3. 存储与回调

默认存储目录为 Path.home() / f".{namespace}"。storage_root 可传字符串、Path 或 callable；回调按需读取当前位置，并沿用已有位置保存行为。

post_setup 接收 Runtime 实例，安装完成后可调用 submit 提交业务任务。创建 Runtime 只建立配置和内部对象。

AnyImage 保留基于 Preferences 的 storage_root 和下载默认模型的 post_setup，仅省略默认入口配置。

### 4. 轻量 Server 定义

server/__init__.py 直接创建 JobServer，并用 job 和 resource 装饰器登记任务与资源工厂。

    from blendjob import JobServer

    server = JobServer("Example")

    @server.job("process_image")
    def process_image(context, parameters):
        from .jobs.process_image import run
        return run(context, parameters)

任务实现放在任务函数内导入；模型管理器在资源工厂内导入。Blender 读取同包元数据时仅创建实例和登记函数。Server 绑定存储时创建资源，处理任务时加载对应实现。

AnyImage 的 Server 定义集中在包入口，保留统一的模型资源错误处理。

## Validation

- BlendJob 相关测试：162 项通过，2 项跳过；覆盖默认路径、工厂调用、显式覆盖、动态存储、命名空间、进程及任务生命周期。
- AnyImage 默认入口接入测试：33 项通过，29 个子测试通过。
- AnyImage Server 定义调整后相关测试：38 项通过，14 个子测试通过；独立进程测试阻止后端依赖导入，验证任务注册、模型目录读取和轻量下载入口。
- OpenSpec 使用严格校验确认提案、规格及任务一致。

## Completion

本次实现范围为 Runtime 默认配置和 AnyImage 接入，任务按此范围验收。
