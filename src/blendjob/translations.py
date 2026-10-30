"""Translate progress templates through Blender at display time."""


def translate_progress(message, message_args=None):
    """Translate the template before formatting its unchanged parameters."""
    try:
        import bpy
    except ModuleNotFoundError:
        translated = message
    else:
        translated = bpy.app.translations.pgettext_iface(message)
    return translated.format(**message_args) if message_args else translated


# 服务器状态与控制
SERVER_MESSAGES = {
    'Idle': '空闲',
    'Starting': '启动中',
    'Processing': '处理中',
    'Stopping': '停止中',
    'Offline': '未启动',
    'Server error': '服务器错误',
    'Environment is not installed': '尚未安装运行环境',
    'Start Server': '启动服务器',
    'Stop Server': '停止服务器',
    'Restart Server': '重启服务器',
    'Open Server Log': '打开服务器日志',
    'Control process exited': '控制进程已退出',
}


# 任务生命周期
JOB_MESSAGES = {
    'Job queued': '等待中',
    'Starting job': '任务启动中',
    'Cancel Job': '取消任务',
    'Cancelling job': '取消中',
    'Job complete': '任务已完成',
    'Job cancelled': '任务已取消',
    'Job failed': '任务失败',
    'Another job is running': '另一个任务正在运行',
}


# 环境安装
INSTALLATION_MESSAGES = {
    'Set Up Server': '设置服务器',
    'Server Location': '服务器位置',
    'Use China Mirror': '使用中国镜像',
    'Online Access is disabled': '网络访问已禁用',
    'Preparing Server': '服务器准备中',
    'Installing environment': '运行环境安装中',
    'Starting installation': '安装启动中',
    'Starting uv download': 'uv 下载启动中',
    'Downloading uv': 'uv 下载中',
    'Downloading uv {rate}': 'uv 下载中 {rate}',
    'Downloading uv (mirror)': 'uv 下载中（镜像）',
    'Downloading uv (mirror) {rate}': 'uv 下载中（镜像） {rate}',
    'Installing uv': 'uv 安装中',
    'Checking Python': 'Python 检查中',
    'Downloading Python': 'Python 下载中',
    'Downloading Python {rate}': 'Python 下载中 {rate}',
    'Downloading Python (mirror)': 'Python 下载中（镜像）',
    'Downloading Python (mirror) {rate}': 'Python 下载中（镜像） {rate}',
    'Installing Python': 'Python 安装中',
    'Resolving packages': '依赖包解析中',
    'Resolving packages (mirror)': '依赖包解析中（镜像）',
    'Downloading packages {count}': '依赖包下载中 {count}',
    'Downloading packages (mirror) {count}': '依赖包下载中（镜像） {count}',
    'Installing packages': '依赖包安装中',
    'Verifying installation': '安装验证中',
    'Server is ready': '服务器已就绪',
    'Environment installation failed': '运行环境安装失败',
}


# 服务器目录迁移
MIGRATION_MESSAGES = {
    'Migrate Storage': '迁移存储',
    'Migrating Folder': '文件夹迁移中',
    'Stopping Server': '服务器停止中',
    'Migrating Server files': '服务器文件迁移中',
    'Saving Server location': '服务器位置保存中',
    'Removing the old Server folder': '旧服务器文件夹移除中',
    'Folder migration completed': '文件夹迁移已完成',
}


# 任务文件清理
JOB_FILE_MESSAGES = {
    'Clear Job Files': '清理任务文件',
    'Reading job files': '任务文件读取中',
    'Deleting job files': '任务文件删除中',
    'Job files inspected': '任务文件已检查',
    'No unused job files to clear': '没有可清理的闲置任务文件',
    'Delete Files': '删除文件',
    'Permanently delete job outputs and logs.': '永久删除任务输出和日志。',
    'Delete {count} jobs ({size} MB)?': '删除 {count} 个任务（{size} MB）？',
    'Deleted {count} jobs ({size} MB); kept {kept}': '已删除 {count} 个任务（{size} MB）；保留 {kept} 个',
}

TRANSLATIONS = {
    "zh_HANS": {
        (context, message): translation
        for messages in (
            SERVER_MESSAGES,
            JOB_MESSAGES,
            INSTALLATION_MESSAGES,
            MIGRATION_MESSAGES,
            JOB_FILE_MESSAGES,
        )
        for message, translation in messages.items()
        for context in ("*", "Operator", "BlendJob")
    },
}
