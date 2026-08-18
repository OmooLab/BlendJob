import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_environment():
    from blendjob import environment

    return environment


class EnvironmentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.environment = load_environment()

    def config(self):
        return {
            "python": "3.12",
            "packages": ["numpy==2.4.2"],
            "platform_packages": {
                "windows": ["onnxruntime-directml==1.24.4"],
                "default": ["onnxruntime==1.24.4"],
            },
        }

    def test_normalizes_public_dictionary(self):
        self.assertEqual(
            self.environment.normalized_environment(self.config()),
            self.config(),
        )

    def test_requires_python_and_list_packages(self):
        with self.assertRaises(ValueError):
            self.environment.normalized_environment({})
        with self.assertRaises(TypeError):
            self.environment.normalized_environment(
                {"python": "3.12", "packages": "numpy"}
            )

    def test_environment_digest_tracks_configuration(self):
        first = self.environment.environment_digest(self.config())
        reordered = {
            "platform_packages": self.config()["platform_packages"],
            "packages": ["numpy==2.4.2"],
            "python": "3.12",
        }
        self.assertEqual(first, self.environment.environment_digest(reordered))
        reordered["packages"].append("pillow==12.1.1")
        self.assertNotEqual(first, self.environment.environment_digest(reordered))

    def test_selects_platform_packages_and_server_packages(self):
        with patch.object(self.environment.sys, "platform", "win32"):
            packages = self.environment.resolved_packages(self.config())
        self.assertFalse(
            any(package.startswith("blendjob") for package in packages)
        )
        self.assertIn("fastapi==0.139.2", packages)
        self.assertIn("uvicorn==0.51.0", packages)
        self.assertIn("numpy==2.4.2", packages)
        self.assertIn("onnxruntime-directml==1.24.4", packages)
        self.assertNotIn("onnxruntime==1.24.4", packages)

    def test_blendjob_is_not_installed_into_server_environment(self):
        normalized = self.environment.normalized_environment(
            {"python": "3.12"}
        )
        self.assertEqual(normalized["packages"], [])
        self.assertFalse(
            any(
                package.startswith("blendjob")
                for package in self.environment.resolved_packages(normalized)
            )
        )

    def test_uses_default_platform_packages(self):
        with patch.object(self.environment.sys, "platform", "linux"):
            packages = self.environment.resolved_packages(self.config())
        self.assertIn("onnxruntime==1.24.4", packages)

    def test_install_writes_manifest_from_environment_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            uv = root / "tools" / "uv"
            uv.parent.mkdir()
            uv.touch()
            status = root / "install-status.json"
            with patch.object(
                self.environment,
                "install_uv",
                return_value=uv,
            ), patch.object(
                self.environment,
                "install_python_environment",
            ), patch.object(
                self.environment,
                "install_packages",
            ) as install_packages:
                self.environment.install(root, self.config(), status)

            manifest = json.loads((root / "manifest.json").read_text("utf-8"))
            final_status = json.loads(status.read_text("utf-8"))
            self.assertEqual(
                manifest["environment_hash"],
                self.environment.environment_digest(self.config()),
            )
            self.assertIn("numpy==2.4.2", manifest["packages"])
            self.assertFalse(
                any(package.startswith("blendjob") for package in manifest["packages"])
            )
            self.assertNotIn("stage", final_status)
            self.assertNotIn("stages", final_status)
            self.assertEqual(final_status["progress"], 1.0)
            self.assertEqual(final_status["message"], "Verifying installation ...")
            self.assertTrue(install_packages.called)

    def test_environment_python_is_inside_runtime_storage(self):
        path = self.environment.environment_python("storage")
        expected = (
            Path("storage/.venv/Scripts/python.exe")
            if sys.platform == "win32"
            else Path("storage/.venv/bin/python")
        )
        self.assertEqual(path, expected)

    def test_install_phase_is_rendered_into_message_only(self):
        with tempfile.TemporaryDirectory() as directory:
            status_path = Path(directory) / "status.json"
            self.environment.write_status(
                status_path,
                0.25,
                "Downloading Python 75.0 KiB/s",
                phase=2,
            )
            status = json.loads(status_path.read_text(encoding="utf-8"))

        self.assertEqual(
            status,
            {
                "progress": 0.25,
                "message": "2/3 Downloading Python 75.0 KiB/s",
            },
        )

    def test_uv_download_reports_source_size_and_speed(self):
        payload = b"x" * (300 * 1024)
        response = io.BytesIO(payload)
        response.headers = {"Content-Length": str(len(payload))}
        reports = []
        times = iter((10.0, 12.0, 14.0))

        with tempfile.TemporaryDirectory() as directory, patch.object(
            self.environment.urllib.request,
            "urlopen",
            return_value=response,
        ), patch.object(
            self.environment,
            "write_status",
            side_effect=lambda _path, progress, message, **_details: reports.append(
                (progress, message)
            ),
        ):
            self.environment._download(
                "https://downloads.example/uv.zip",
                Path(directory) / "uv.zip",
                Path(directory) / "status.json",
                1,
                0.0,
                0.1,
                clock=lambda: next(times),
            )

        self.assertEqual(
            reports[-1][1],
            "Downloading uv 75.0 KiB/s",
        )

    def test_official_install_overrides_inherited_source_configuration(self):
        with patch.object(
            self.environment,
            "child_environment",
            return_value={
                "PATH": "tools",
                "UV_DEFAULT_INDEX": "configured",
                "UV_INDEX_URL": "configured",
                "UV_INDEX": "configured",
                "UV_PYTHON_INSTALL_MIRROR": "configured",
            },
        ):
            environment = self.environment.install_environment_variables()

        self.assertEqual(environment["PATH"], "tools")
        self.assertEqual(
            environment["UV_PYTHON_INSTALL_MIRROR"],
            self.environment.PYTHON_SOURCES[0],
        )
        self.assertEqual(
            environment["UV_INDEX_URL"],
            self.environment.PYPI_SOURCES[0],
        )
        self.assertNotIn("UV_DEFAULT_INDEX", environment)
        self.assertNotIn("UV_INDEX", environment)

    def test_mirror_install_sets_python_and_pypi_mirrors(self):
        with patch.object(
            self.environment,
            "child_environment",
            return_value={"PATH": "tools", "UV_DEFAULT_INDEX": "configured"},
        ):
            environment = self.environment.install_environment_variables(
                mirror=True
            )

        self.assertEqual(
            environment["UV_PYTHON_INSTALL_MIRROR"],
            self.environment.PYTHON_SOURCES[1],
        )
        self.assertEqual(
            environment["UV_INDEX_URL"],
            self.environment.PYPI_SOURCES[1],
        )
        self.assertNotIn("UV_DEFAULT_INDEX", environment)

    def test_python_output_uses_fixed_phase_messages(self):
        reports = []

        def run_command(_command, on_line, on_tick, environment=None):
            self.assertIsNone(environment)
            on_line("Downloading cpython-3.12 75.0 KiB/s")
            on_tick()
            on_line("Creating virtual environment")
            on_tick()

        with patch.object(
            self.environment,
            "_run_output_command",
            side_effect=run_command,
        ), patch.object(
            self.environment,
            "write_status",
            side_effect=lambda _path, progress, message, **details: reports.append(
                (progress, message, details)
            ),
        ):
            self.environment.install_python_environment(
                ["uv", "venv"],
                "status.json",
            )

        messages = [item[1] for item in reports]
        self.assertIn("Checking Python ...", messages)
        self.assertIn("Downloading Python 75.0 KiB/s", messages)
        self.assertIn("Installing Python ...", messages)
        self.assertTrue(all(item[2] == {"phase": 2} for item in reports))

    def test_package_output_reports_count_and_mirror(self):
        reports = []

        def run_command(_command, on_line, on_tick, environment=None):
            self.assertEqual(environment, {"UV_INDEX_URL": "mirror"})
            on_line("Resolved 40 packages")
            on_line("Downloading numpy")
            on_line("Downloading pillow")
            on_tick()
            on_line("Prepared 40 packages")
            on_tick()

        with patch.object(
            self.environment,
            "_run_output_command",
            side_effect=run_command,
        ), patch.object(
            self.environment,
            "write_status",
            side_effect=lambda _path, progress, message, **details: reports.append(
                (progress, message, details)
            ),
        ):
            self.environment.install_packages(
                ["uv", "pip", "install"],
                "status.json",
                environment={"UV_INDEX_URL": "mirror"},
                mirror=True,
            )

        messages = [item[1] for item in reports]
        self.assertIn("Resolving packages (mirror) ...", messages)
        self.assertIn("Downloading packages (mirror) 2/40", messages)
        self.assertIn("Installing packages ...", messages)
        self.assertTrue(all(item[2] == {"phase": 3} for item in reports))


if __name__ == "__main__":
    unittest.main()
