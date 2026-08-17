import sys
import tempfile
import unittest
from pathlib import Path

from blendjob.entrypoint import normalized_entrypoint
from blendjob.runner import load_server


class RunnerTest(unittest.TestCase):
    def test_loads_export_from_file_entrypoint_string(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "example_server"
            package.mkdir()
            entrypoint = package / "__init__.py"
            entrypoint.write_text("server = object()\n", encoding="utf-8")
            loaded = load_server(f"{entrypoint}:server")
            self.assertIsNotNone(loaded)

    def test_file_entrypoint_does_not_collide_with_imported_module(self):
        import json

        with tempfile.TemporaryDirectory() as directory:
            entrypoint = Path(directory) / "json.py"
            entrypoint.write_text("server = object()\n", encoding="utf-8")

            loaded = load_server(f"{entrypoint}:server")

        self.assertIsNotNone(loaded)
        self.assertIs(sys.modules["json"], json)

    def test_package_entrypoint_supports_relative_imports(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "example_server"
            package.mkdir()
            (package / "value.py").write_text("server = object()\n", encoding="utf-8")
            entrypoint = package / "__init__.py"
            entrypoint.write_text("from .value import server\n", encoding="utf-8")

            loaded = load_server(f"{entrypoint}:server")

        self.assertIsNotNone(loaded)

    def test_rejects_entrypoint_without_attribute(self):
        with self.assertRaisesRegex(ValueError, "python-file"):
            load_server("server.py")

    def test_normalizes_relative_package_from_explicit_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "example_server"
            package.mkdir()
            entrypoint = package / "__init__.py"
            entrypoint.touch()

            normalized = normalized_entrypoint(
                "example_server:server",
                root=root,
            )

        self.assertEqual(normalized, f"{entrypoint.resolve()}:server")

    def test_relative_entrypoint_requires_explicit_root(self):
        with self.assertRaisesRegex(ValueError, "entrypoint_root"):
            normalized_entrypoint("server:server")
