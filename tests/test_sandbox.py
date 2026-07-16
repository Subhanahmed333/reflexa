from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from reflexa.sandbox import LocalSandboxRunner


class SandboxTests(unittest.TestCase):
    def test_local_runner_executes_fixture_tests(self) -> None:
        runner = LocalSandboxRunner()
        repo = Path("tests/fixtures/demo_repo")
        result = runner.run(repo, ["python", "-m", "unittest", "discover", "-s", "tests"], timeout_seconds=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("AssertionError", result.combined_output())

