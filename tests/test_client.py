import tempfile
import unittest
from pathlib import Path

from blendjob import JobResult


class JobResultTest(unittest.TestCase):
    def test_resolves_named_file_inside_job_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "result.txt"
            output.write_text("done", encoding="utf-8")
            result = JobResult("job", root, {"output": output.name})
            self.assertEqual(result.file("output"), output)
            self.assertFalse(hasattr(result, "status"))

    def test_rejects_file_outside_job_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = JobResult("job", root, {"output": "../escape.txt"})
            with self.assertRaisesRegex(RuntimeError, "escapes"):
                result.file("output")


if __name__ == "__main__":
    unittest.main()
