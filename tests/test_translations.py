"""Cover built-in translations independently of any consuming add-on."""
from string import Formatter
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from blendjob import JobRuntime
from blendjob.translations import TRANSLATIONS, translate_progress


@pytest.fixture
def blender(monkeypatch):
    catalogs = {}
    settings = {"translate": True}

    def register(name, catalog):
        assert name not in catalogs
        catalogs[name] = catalog

    def lookup(message):
        if settings["translate"]:
            for catalog in catalogs.values():
                translated = catalog["zh_HANS"].get(("*", message))
                if translated is not None:
                    return translated
        return message

    bpy = SimpleNamespace(
        app=SimpleNamespace(translations=SimpleNamespace(
            register=register, unregister=catalogs.pop, pgettext_iface=lookup,
        )),
        utils=SimpleNamespace(register_class=Mock(), unregister_class=Mock()),
    )
    monkeypatch.setitem(__import__('sys').modules, 'bpy', bpy)
    return bpy, catalogs, settings


@pytest.mark.parametrize("source,expected", [
    ("Preparing Server", "正在准备服务器"),
    ("Migrate Folder", "迁移文件夹"),
    ("2/3 Installing Python ...", "2/3 正在安装 Python…"),
    ("3/3 Downloading packages (mirror) 4/10", "3/3 正在通过镜像下载依赖包 4/10"),
    ("3/3 Downloading packages 4/10", "3/3 正在下载依赖包 4/10"),
    ("1/3 Downloading uv (mirror) 1.2 MB/s", "1/3 正在通过镜像下载 uv 1.2 MB/s"),
    ("2/3 Downloading Python 500 KB/s", "2/3 正在下载 Python 500 KB/s"),
    ("Downloading Python (mirror)", "正在通过镜像下载 Python"),
    ("Delete 12 jobs (3.5 MB)?", "删除 12 个任务（3.5 MB）？"),
    ("Deleted 12 jobs (3.5 MB); kept 2", "已删除 12 个任务（3.5 MB）；保留 2 个"),
    ("Unknown diagnostic: {path} C:/model.onnx", "Unknown diagnostic: {path} C:/model.onnx"),
])
def test_progress_uses_current_language_and_preserves_parameters(blender, source, expected):
    bpy, _catalogs, settings = blender
    bpy.app.translations.register('test', TRANSLATIONS)
    assert translate_progress(source) == expected
    settings['translate'] = False
    assert translate_progress(source) == source


def test_runtime_translation_lifetimes_are_independent(blender, tmp_path, monkeypatch):
    _bpy, catalogs, _settings = blender
    entrypoint = tmp_path / 'app.py'
    entrypoint.touch()
    runtimes = [JobRuntime(f'{entrypoint}:server', storage_root=tmp_path,
                           environment={'python': '3.12'}, namespace=name)
                for name in ('first', 'second')]
    for runtime in runtimes:
        monkeypatch.setattr(runtime, 'operator_classes', lambda: ())
        for name in ('enable', 'disable', '_ensure_status_bar_draw', '_remove_status_bar_draw'):
            monkeypatch.setattr(runtime, name, Mock())
        runtime.register()
        runtime.register()
    assert len(catalogs) == 2
    assert translate_progress('Installing environment') == '正在安装运行环境'
    runtimes[0].unregister()
    runtimes[0].unregister()
    assert len(catalogs) == 1
    assert translate_progress('Installing environment') == '正在安装运行环境'
    runtimes[1].unregister()
    assert catalogs == {}
    assert translate_progress('Installing environment') == 'Installing environment'


def test_status_bar_translates_at_draw_time_without_changing_messages(blender, tmp_path, monkeypatch):
    bpy, _catalogs, settings = blender
    bpy.app.translations.register('test', TRANSLATIONS)
    entrypoint = tmp_path / 'app.py'
    entrypoint.touch()
    runtime = JobRuntime(f'{entrypoint}:server', storage_root=tmp_path,
                         environment={'python': '3.12'}, namespace='test')
    runtime.active_job = object()
    monkeypatch.setattr(runtime, 'redraw_ui', Mock())
    context = SimpleNamespace()
    source = '2/3 Installing Python ...'
    runtime.update_ui(context, 0.25, source)
    layout = Mock()
    layout.row.return_value = layout
    owner = SimpleNamespace(layout=layout)
    runtime._draw_status_bar(owner, context)
    assert layout.progress.call_args.kwargs['text'] == '2/3 正在安装 Python…'
    settings['translate'] = False
    runtime._draw_status_bar(owner, context)
    assert layout.progress.call_args.kwargs['text'] == source
    assert runtime.message == source


def test_catalog_preserves_format_fields():
    def fields(message):
        return sorted((name, spec, conversion) for _, name, spec, conversion in Formatter().parse(message)
                      if name is not None)
    for (_, source), translated in TRANSLATIONS['zh_HANS'].items():
        assert fields(source) == fields(translated)


@pytest.mark.parametrize("translated", [False, True])
def test_cleanup_translates_templates_before_formatting(blender, tmp_path, translated):
    bpy, _catalogs, settings = blender
    bpy.app.translations.register("test", TRANSLATIONS)
    settings["translate"] = translated
    bpy.types = SimpleNamespace(Operator=type("Operator", (), {}))
    bpy.props = SimpleNamespace(StringProperty=Mock(), BoolProperty=Mock())
    entrypoint = tmp_path / "app.py"
    entrypoint.touch()
    runtime = JobRuntime(f"{entrypoint}:server", storage_root=tmp_path,
                         environment={"python": "3.12"}, namespace="test")
    result = {"jobs": ["job"] * 12, "bytes": 3_500_000, "skipped": 1, "failed": ["busy"]}
    cls = next(cls for cls in runtime.operator_classes() if cls.bl_idname.endswith(".clear_job_files"))
    operator = SimpleNamespace(_preview=result, _action="clear_files", layout=Mock(), report=Mock())
    cls.draw(operator, None)
    cls.response(operator, None, SimpleNamespace(value=result))
    assert operator.layout.label.call_args_list[0].kwargs == {
        "text": "删除 12 个任务（3.5 MB）？" if translated else "Delete 12 jobs (3.5 MB)?",
        "translate": False,
    }
    operator.report.assert_called_once_with(
        {"WARNING"}, "已删除 12 个任务（3.5 MB）；保留 2 个" if translated else "Deleted 12 jobs (3.5 MB); kept 2",
    )
