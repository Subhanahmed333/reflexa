from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(slots=True)
class RunEvent:
    kind: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "message": self.message,
            "data": self.data,
            "timestamp": self.timestamp,
        }


@dataclass(slots=True)
class PatchEdit:
    path: str
    content: str
    note: str = ""


@dataclass(slots=True)
class PatchProposal:
    analysis: str
    summary: str
    edits: list[PatchEdit] = field(default_factory=list)
    confidence: float | None = None
    assumptions: list[str] = field(default_factory=list)
    verification_notes: list[str] = field(default_factory=list)
    raw_response: dict[str, Any] | None = None


@dataclass(slots=True)
class SandboxResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float

    @property
    def passed(self) -> bool:
        return self.returncode == 0

    def combined_output(self) -> str:
        return "\n".join(part for part in (self.stdout.strip(), self.stderr.strip()) if part)


@dataclass(slots=True)
class PublicationResult:
    mode: str
    title: str
    summary_path: Path | None = None
    patch_path: Path | None = None
    pull_request_url: str | None = None


@dataclass(slots=True)
class RunResult:
    run_id: str
    status: RunStatus
    attempts: int
    summary: str
    events: list[RunEvent]
    publication: PublicationResult | None = None
