import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class InstallerTest(unittest.TestCase):
    def test_starts_without_distribution_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "blendjob"
            shutil.copytree(PROJECT_ROOT / "src" / "blendjob", package)
            installer = package / "installer" / "environment.py"

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


if __name__ == "__main__":
    unittest.main()
