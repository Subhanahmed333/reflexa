from __future__ import annotations

import shutil
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
    """Fallback runner used for tests and local demos when Docker is unavailable."""

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
class DockerSandboxRunner:
    image: str = "python:3.12-slim"
    cpus: str = "1.0"
    memory: str = "512m"
    network: str = "none"

    def build_command(self, repo_path: Path, command: list[str]) -> list[str]:
        if shutil.which("docker") is None:
            raise RuntimeError("Docker is not available on this machine.")

        mount_path = repo_path.resolve()
        return [
            "docker",
            "run",
            "--rm",
            "--network",
            self.network,
            "--cpus",
            self.cpus,
            "-m",
            self.memory,
            "-v",
            f"{mount_path}:/workspace",
            "-w",
            "/workspace",
            self.image,
            *command,
        ]

    def run(self, repo_path: Path, command: list[str], timeout_seconds: int = 60) -> SandboxResult:
        docker_command = self.build_command(repo_path, command)
        started = time.perf_counter()
        completed = subprocess.run(
            docker_command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        duration = time.perf_counter() - started
        return SandboxResult(
            command=docker_command,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            duration_seconds=duration,
        )

