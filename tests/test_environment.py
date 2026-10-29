import sys
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
                "windows": ["platform-windows==1.0"],
                "default": ["platform-default==1.0"],
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
        self.assertIn("platform-windows==1.0", packages)
        self.assertNotIn("platform-default==1.0", packages)

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
        self.assertIn("platform-default==1.0", packages)


    def test_environment_python_is_inside_runtime_storage(self):
        path = self.environment.environment_python("storage")
        expected = (
            Path("storage/.venv/Scripts/python.exe")
            if sys.platform == "win32"
            else Path("storage/.venv/bin/python")
        )
        self.assertEqual(path, expected)


if __name__ == "__main__":
    unittest.main()
