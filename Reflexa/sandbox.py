from __future__ import annotations

import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .models import SandboxResult


class SandboxRunner(Protocol):
    def run(self, repo_path: Path, command: list[str], timeout_seconds: int = 60) -> SandboxResult: ...


@dataclass(slots=True)
class LocalSandboxRunner:
    """Fallback runner used for tests and local demos when E2B is unavailable."""

    def run(self, repo_path: Path, command: list[str], timeout_seconds: int = 60) -> SandboxResult:
        started = time.perf_counter()
        completed = subprocess.run(
            command,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        duration = time.perf_counter() - started
        return SandboxResult(
            command=command,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            duration_seconds=duration,
        )


@dataclass(slots=True)
class E2BSandboxRunner:
    """Runs the repo inside an E2B cloud sandbox microVM."""

    api_key: str | None = None
    workspace_root: str = "/workspace"
    command_timeout_buffer_seconds: int = 120
    sandbox_timeout_buffer_seconds: int = 180
    allow_internet_access: bool = False

    def run(self, repo_path: Path, command: list[str], timeout_seconds: int = 60) -> SandboxResult:
        if not command:
            raise ValueError("command must not be empty")

        sandbox_cls = self._load_sandbox_class()
        sandbox = self._create_sandbox(sandbox_cls, timeout_seconds)
        started = time.perf_counter()
        executed_command = self._build_command(command, timeout_seconds)
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        try:
            self._sync_repo_to_sandbox(sandbox, repo_path)
            result = sandbox.commands.run(
                executed_command,
                cwd=self.workspace_root,
                on_stdout=stdout_parts.append,
                on_stderr=stderr_parts.append,
                timeout=timeout_seconds + self.command_timeout_buffer_seconds,
            )
            duration = time.perf_counter() - started
            stdout = self._coalesce_output(result, stdout_parts, "stdout")
            stderr = self._coalesce_output(result, stderr_parts, "stderr")
            returncode, stdout = self._normalize_result(stdout, stderr, result)
            return SandboxResult(
                command=["bash", "-lc", executed_command],
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
                duration_seconds=duration,
            )
        finally:
            self._shutdown_sandbox(sandbox)

    def _load_sandbox_class(self):
        try:
            from e2b import Sandbox  # type: ignore
        except ImportError as exc:  # pragma: no cover - exercised only without SDK installed
            raise RuntimeError(
                "The E2B SDK is not installed. Install the `e2b` package and set E2B_API_KEY to use the demo sandbox."
            ) from exc
        return Sandbox

    def _create_sandbox(self, sandbox_cls, timeout_seconds: int):
        api_key = self.api_key or os.getenv("E2B_API_KEY")
        if not api_key:
            raise RuntimeError("E2B_API_KEY is required to create an E2B sandbox.")
        sandbox_timeout = max(timeout_seconds + self.sandbox_timeout_buffer_seconds, timeout_seconds + 60)
        return sandbox_cls.create(api_key=api_key, timeout=sandbox_timeout)

    def _sync_repo_to_sandbox(self, sandbox, repo_path: Path) -> None:
        for file_path in sorted(repo_path.rglob("*")):
            if not file_path.is_file():
                continue
            if self._should_skip_path(file_path, repo_path):
                continue
            rel_path = file_path.relative_to(repo_path).as_posix()
            target_path = f"{self.workspace_root}/{rel_path}"
            try:
                content = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                content = file_path.read_bytes()
            sandbox.files.write(target_path, content)

    def _should_skip_path(self, file_path: Path, repo_root: Path) -> bool:
        relative_parts = file_path.relative_to(repo_root).parts
        blocked_names = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".reflexa_artifacts"}
        return any(part in blocked_names for part in relative_parts)

    def _build_command(self, command: list[str], timeout_seconds: int) -> str:
        quoted = shlex.join(command)
        sandbox_timeout = max(timeout_seconds, 1)
        return (
            f"cd {shlex.quote(self.workspace_root)} && set +e; "
            f"timeout --preserve-status {sandbox_timeout}s {quoted}; "
            'status=$?; printf "__REFLEXA_EXIT_CODE__=%s\\n" "$status"'
        )

    def _coalesce_output(self, result, chunks: list[str], attr_name: str) -> str:
        value = getattr(result, attr_name, None)
        if isinstance(value, str) and value:
            return value
        return "".join(chunks)

    def _normalize_result(self, stdout: str, stderr: str, result) -> tuple[int, str]:
        marker = re.search(r"__REFLEXA_EXIT_CODE__=(\d+)", stdout)
        if marker:
            return int(marker.group(1)), re.sub(r"\n?__REFLEXA_EXIT_CODE__=\d+\s*$", "", stdout).rstrip("\n")
        return_code = getattr(result, "exit_code", None)
        if return_code is None:
            return_code = getattr(result, "returncode", 1)
        return int(return_code), stdout

    def _shutdown_sandbox(self, sandbox) -> None:
        kill = getattr(sandbox, "kill", None)
        if callable(kill):
            try:
                kill()
            except Exception as exc:
                print(f'Sandbox shutdown failed: {exc}')
                pass

