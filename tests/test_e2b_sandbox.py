from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path

from reflexa.sandbox import E2BSandboxRunner


class FakeResult:
    def __init__(self, stdout: str, stderr: str = "", exit_code: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code


class FakeFiles:
    def __init__(self) -> None:
        self.writes: list[tuple[str, object]] = []

    def write(self, path: str, content: object) -> None:
        self.writes.append((path, content))


class FakeCommands:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def run(self, cmd: str, cwd: str | None = None, on_stdout=None, on_stderr=None, timeout: float | None = None):
        self.calls.append({"cmd": cmd, "cwd": cwd, "timeout": timeout})
        if on_stdout:
            on_stdout("hello from sandbox\n")
        if on_stderr:
            on_stderr("warning line\n")
        return FakeResult(
            stdout="hello from sandbox\n__REFLEXA_EXIT_CODE__=0\n",
            stderr="warning line\n",
            exit_code=0,
        )


class FakeSandbox:
    created: list[dict[str, object]] = []

    def __init__(self) -> None:
        self.files = FakeFiles()
        self.commands = FakeCommands()
        self.killed = False

    @classmethod
    def create(cls, **kwargs):
        cls.created.append(kwargs)
        return cls()

    def kill(self) -> bool:
        self.killed = True
        return True


class E2BSandboxRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_e2b = sys.modules.get("e2b")
        sys.modules["e2b"] = types.SimpleNamespace(Sandbox=FakeSandbox)
        FakeSandbox.created.clear()

    def tearDown(self) -> None:
        if self._original_e2b is None:
            sys.modules.pop("e2b", None)
        else:
            sys.modules["e2b"] = self._original_e2b

    def test_runs_command_and_syncs_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            (repo / "app.py").write_text("print('hi')\n", encoding="utf-8")
            (repo / "subdir").mkdir()
            (repo / "subdir" / "note.txt").write_text("hello\n", encoding="utf-8")

            runner = E2BSandboxRunner(api_key="test-key")
            result = runner.run(repo, ["python", "-m", "unittest", "discover", "-s", "tests"], timeout_seconds=12)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "hello from sandbox")
        self.assertEqual(result.stderr, "warning line\n")
        self.assertTrue(FakeSandbox.created)
        self.assertEqual(FakeSandbox.created[0]["api_key"], "test-key")
        self.assertGreaterEqual(FakeSandbox.created[0]["timeout"], 192)

    def test_wraps_command_with_shell_and_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            (repo / "app.py").write_text("print('hi')\n", encoding="utf-8")

            runner = E2BSandboxRunner(api_key="test-key")
            result = runner.run(repo, ["python", "-V"], timeout_seconds=5)

        self.assertEqual(result.returncode, 0)
        call = FakeSandbox.created[0]
        self.assertEqual(call["api_key"], "test-key")

