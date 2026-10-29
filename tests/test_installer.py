import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class InstallerTest(unittest.TestCase):
    def test_starts_without_distribution_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "blendjob"
            shutil.copytree(PROJECT_ROOT / "src" / "blendjob", package)
            installer = package / "launcher" / "install.py"

            result = subprocess.run(
                [sys.executable, "-S", str(installer), "--help"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--storage-root", result.stdout)
        self.assertIn("--config", result.stdout)

    def test_runner_launcher_uses_bundled_package_without_installing_it(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "blendjob"
            shutil.copytree(PROJECT_ROOT / "src" / "blendjob", package)
            launcher = package / "launcher" / "runner.py"

            result = subprocess.run(
                [sys.executable, "-S", str(launcher), "--help"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--entrypoint", result.stdout)
        self.assertIn("--storage-root", result.stdout)


class EnvironmentInstallationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from blendjob.installer import environment
        cls.installer = environment

    def config(self):
        return {
            "python": "3.12",
            "packages": ["numpy==2.4.2"],
            "platform_packages": {
                "windows": ["platform-windows==1.0"],
                "default": ["platform-default==1.0"],
            },
        }


    def test_install_writes_manifest_from_environment_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            uv = root / "tools" / "uv"
            uv.parent.mkdir()
            uv.touch()
            status = root / "install-status.json"
            with patch.object(
                self.installer,
                "install_uv",
                return_value=uv,
            ), patch.object(
                self.installer,
                "install_python_environment",
            ) as install_python_environment, patch.object(
                self.installer,
                "install_packages",
            ) as install_packages:
                self.installer.install(root, self.config(), status)

            manifest = json.loads((root / "manifest.json").read_text("utf-8"))
            final_status = json.loads(status.read_text("utf-8"))
            self.assertEqual(
                manifest["environment_hash"],
                self.installer.environment_digest(self.config()),
            )
            self.assertIn("numpy==2.4.2", manifest["packages"])
            self.assertFalse(
                any(package.startswith("blendjob") for package in manifest["packages"])
            )
            self.assertNotIn("stage", final_status)
            self.assertNotIn("stages", final_status)
            self.assertEqual(final_status["progress"], 1.0)
            self.assertEqual(final_status["message"], "Verifying installation ...")
            self.assertEqual(
                install_python_environment.call_args.args[0],
                [uv, "venv", "--clear", "--managed-python", "--python", "3.12", root / ".venv"],
            )
            self.assertTrue(install_packages.called)


    def test_uv_release_artifacts_match_supported_mirrors(self):
        self.assertEqual(self.installer.UV_VERSION, "0.12.11")
        self.assertEqual(
            self.installer.UV_ARTIFACTS,
            {
                "x86_64-pc-windows-msvc": (
                    "uv-x86_64-pc-windows-msvc.zip",
                    "e94225dea91e051472847bd6d146d7d66c4f54ffcd1f106678866a99580845f9",
                ),
                "aarch64-apple-darwin": (
                    "uv-aarch64-apple-darwin.tar.gz",
                    "e01b69ee15e81918d5e8fc9cf39b3db7f59c5576e5e306cd9b7aeb2c7b7321c3",
                ),
                "x86_64-unknown-linux-gnu": (
                    "uv-x86_64-unknown-linux-gnu.tar.gz",
                    "4ae93e0f148a18434cc094072547cec88912fc4a72b984183c7d0d0e9586cb5e",
                ),
            },
        )
        self.assertEqual(
            self.installer.UV_SOURCES,
            (
                "https://github.com/astral-sh/uv/releases/download/0.12.11",
                "https://mirror.omoolab.xyz/uv/0.12.11",
            ),
        )


    def test_install_phase_is_rendered_into_message_only(self):
        with tempfile.TemporaryDirectory() as directory:
            status_path = Path(directory) / "status.json"
            self.installer.write_status(
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
            self.installer.urllib.request,
            "urlopen",
            return_value=response,
        ), patch.object(
            self.installer,
            "write_status",
            side_effect=lambda _path, progress, message, **_details: reports.append(
                (progress, message)
            ),
        ):
            self.installer._download(
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
            self.installer,
            "child_environment",
            return_value={
                "PATH": "tools",
                "UV_DEFAULT_INDEX": "configured",
                "UV_INDEX_URL": "configured",
                "UV_INDEX": "configured",
                "UV_PYTHON_INSTALL_MIRROR": "configured",
            },
        ):
            environment = self.installer.install_environment_variables()

        self.assertEqual(environment["PATH"], "tools")
        self.assertEqual(
            environment["UV_PYTHON_INSTALL_MIRROR"],
            self.installer.PYTHON_SOURCES[0],
        )
        self.assertEqual(
            environment["UV_INDEX_URL"],
            self.installer.PYPI_SOURCES[0],
        )
        self.assertNotIn("UV_DEFAULT_INDEX", environment)
        self.assertNotIn("UV_INDEX", environment)


    def test_mirror_install_sets_python_and_pypi_mirrors(self):
        with patch.object(
            self.installer,
            "child_environment",
            return_value={"PATH": "tools", "UV_DEFAULT_INDEX": "configured"},
        ):
            environment = self.installer.install_environment_variables(
                mirror=True
            )

        self.assertEqual(
            environment["UV_PYTHON_INSTALL_MIRROR"],
            "https://mirror.omoolab.xyz/python-build-standalone",
        )
        self.assertEqual(
            environment["UV_INDEX_URL"],
            self.installer.PYPI_SOURCES[1],
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
            self.installer,
            "_run_output_command",
            side_effect=run_command,
        ), patch.object(
            self.installer,
            "write_status",
            side_effect=lambda _path, progress, message, **details: reports.append(
                (progress, message, details)
            ),
        ):
            self.installer.install_python_environment(
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
            self.installer,
            "_run_output_command",
            side_effect=run_command,
        ), patch.object(
            self.installer,
            "write_status",
            side_effect=lambda _path, progress, message, **details: reports.append(
                (progress, message, details)
            ),
        ):
            self.installer.install_packages(
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
