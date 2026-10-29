"""Translate BlendJob UI at display time, keeping process messages unchanged."""
import re
from string import Formatter


def translate_progress(message):
    """Resolve the current Blender interface language without translating parameters."""
    try:
        import bpy
    except ModuleNotFoundError:
        return message

    translated = bpy.app.translations.pgettext_iface(message)
    if translated != message:
        return translated
    phase = re.fullmatch(r"(\d+/\d+) (.+)", message)
    if phase:
        return f"{phase[1]} {translate_progress(phase[2])}"
    for pattern, template in _PROGRESS_PATTERNS:
        match = pattern.fullmatch(message)
        if match:
            return bpy.app.translations.pgettext_iface(template).format(**match.groupdict())
    return message


_ZH_HANS = {
    'Job queued': '任务已排队',
    'Cancelling job': '正在取消任务',
    'Ready': '就绪',
    'Starting job': '正在启动任务',
    'Job complete': '任务已完成',
    'Job cancelled': '任务已取消',
    'Job failed': '任务失败',
    'Preparing Server': '正在准备服务器',
    'Migrating Folder': '正在迁移文件夹',
    'Stopping Server': '正在停止服务器',
    'Saving Server location': '正在保存服务器位置',
    'Server is ready': '服务器已就绪',
    'Migrating Server files': '正在迁移文件夹中的文件',
    'Removing the old Server folder': '正在移除旧服务器文件夹',
    'Installing environment': '正在安装运行环境',
    'Starting installation ...': '正在开始安装…',
    'Starting uv download ...': '正在开始下载 uv…',
    'Installing uv ...': '正在安装 uv…',
    'Checking Python ...': '正在检查 Python…',
    'Installing Python ...': '正在安装 Python…',
    'Resolving packages ...': '正在解析依赖包…',
    'Resolving packages (mirror) ...': '正在通过镜像解析依赖包…',
    'Installing packages ...': '正在安装依赖包…',
    'Verifying installation ...': '正在验证安装…',
    'Environment installation failed': '运行环境安装失败',
    'Downloading packages {count}': '正在下载依赖包 {count}',
    'Downloading packages (mirror) {count}': '正在通过镜像下载依赖包 {count}',
    'Delete Files': '删除文件',
    'Permanently delete job outputs and logs.': '永久删除任务输出和日志。',
    'No unused job files to clear': '没有可清理的闲置任务文件',
    'Set Up Server': '设置服务器',
    'Migrate Folder': '迁移文件夹',
    'Clear Job Files': '清理任务文件',
    'Cancel Job': '取消任务',
    'Start Server': '启动服务器',
    'Stop Server': '停止服务器',
    'Restart Server': '重启服务器',
    'Open Server Log': '打开服务器日志',
    'Starting': '启动中',
    'Stopping': '停止中',
    'Stopped': '已停止',
    'Environment is not installed': '尚未安装运行环境',
    'Server Location': '服务器位置',
    'Use China Mirror': '使用中国镜像',
    'Busy': '忙碌',
    'Control process exited': '控制进程已退出',
    'Another job is running': '另一个任务正在运行',
    'Reading job files': '正在读取任务文件',
    'Online Access is disabled': '网络访问已禁用',
    'Delete {count} jobs ({size} MB)?': '删除 {count} 个任务（{size} MB）？',
    'Deleted {count} jobs ({size} MB); kept {kept}': '已删除 {count} 个任务（{size} MB）；保留 {kept} 个',
    'Downloading uv': '正在下载 uv',
    'Downloading uv {rate}': '正在下载 uv {rate}',
    'Downloading uv (mirror)': '正在通过镜像下载 uv',
    'Downloading uv (mirror) {rate}': '正在通过镜像下载 uv {rate}',
    'Downloading Python': '正在下载 Python',
    'Downloading Python {rate}': '正在下载 Python {rate}',
    'Downloading Python (mirror)': '正在通过镜像下载 Python',
    'Downloading Python (mirror) {rate}': '正在通过镜像下载 Python {rate}',
}

TRANSLATIONS = {
    "zh_HANS": {
        (context, message): translation
        for message, translation in _ZH_HANS.items()
        for context in ("*", "Operator")
    },
}

_PROGRESS_PATTERNS = tuple(
    (re.compile("".join(
        re.escape(literal) + (f"(?P<{field}>.+)" if field else "")
        for literal, field, _spec, _conversion in Formatter().parse(template)
    )), template)
    for template in sorted((message for message in _ZH_HANS if "{" in message), key=len, reverse=True)
)
