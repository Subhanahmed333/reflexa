from __future__ import annotations

import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .models import PatchEdit, RunEvent, RunResult, RunStatus
from .publisher import LocalArtifactPublisher, build_patch_text, publish_output
from .providers import HeuristicRepairProvider, RepairContext, RepairProvider
from .sandbox import SandboxRunner


class EventSink(Protocol):
    def emit(self, event: RunEvent) -> None: ...


@dataclass(slots=True)
class ListEventSink:
    events: list[RunEvent] = field(default_factory=list)

    def emit(self, event: RunEvent) -> None:
        self.events.append(event)


@dataclass(slots=True)
class OrchestratorConfig:
    repo_path: Path
    test_command: list[str]
    max_attempts: int = 3
    sandbox_timeout_seconds: int = 60
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])


@dataclass(slots=True)
class Orchestrator:
    provider: RepairProvider
    sandbox: SandboxRunner
    publisher: LocalArtifactPublisher = field(default_factory=LocalArtifactPublisher)
    allow_heuristic_fallback: bool = False

    def run(self, config: OrchestratorConfig, sink: EventSink | None = None) -> RunResult:
        sink = sink or ListEventSink()
        events: list[RunEvent] = []
        self._emit(sink, events, config.run_id, 'run_started', 'Reflexa run started.', {})

        with tempfile.TemporaryDirectory(prefix='reflexa-work-') as tmpdir:
            working_root = Path(tmpdir) / 'workspace'
            shutil.copytree(config.repo_path, working_root)
            status = RunStatus.RUNNING
            attempts = 0
            last_summary = 'The run has not completed yet.'
            publication = None

            while attempts < config.max_attempts:
                attempts += 1
                self._emit(
                    sink,
                    events,
                    config.run_id,
                    'attempt_started',
                    f'Attempt {attempts} started.',
                    {'attempt': attempts},
                )
                result = self.sandbox.run(working_root, config.test_command, timeout_seconds=config.sandbox_timeout_seconds)
                self._emit(
                    sink,
                    events,
                    config.run_id,
                    'test_result',
                    'Test run completed.',
                    {
                        'attempt': attempts,
                        'passed': result.passed,
                        'returncode': result.returncode,
                        'stdout': result.stdout,
                        'stderr': result.stderr,
                        'duration_seconds': result.duration_seconds,
                    },
                )
                if result.passed:
                    status = RunStatus.SUCCEEDED
                    last_summary = f'Validation passed on attempt {attempts}.'
                    break

                failure_output = result.combined_output()
                self._emit(
                    sink,
                    events,
                    config.run_id,
                    'failure_detected',
                    'A failing test was detected.',
                    {'attempt': attempts, 'failure_output': failure_output},
                )
                if attempts >= config.max_attempts:
                    status = RunStatus.FAILED
                    last_summary = 'The retry limit was reached before the failing test could be repaired.'
                    break

                relevant_files = self._collect_relevant_files(working_root, failure_output)
                context = RepairContext(
                    failure_output=failure_output,
                    repository=working_root,
                    relevant_files=relevant_files,
                    test_command=config.test_command,
                )
                proposal = self._request_proposal(context, sink, events, config.run_id)
                self._emit(
                    sink,
                    events,
                    config.run_id,
                    'diagnosis',
                    proposal.analysis,
                    {
                        'attempt': attempts,
                        'summary': proposal.summary,
                        'confidence': proposal.confidence,
                        'assumptions': proposal.assumptions,
                        'verification_notes': proposal.verification_notes,
                    },
                )
                self._apply_edits(working_root, proposal.edits)
                self._emit(
                    sink,
                    events,
                    config.run_id,
                    'patch_applied',
                    'Patch applied to the working copy.',
                    {'attempt': attempts, 'edits': [edit.path for edit in proposal.edits]},
                )

            patch_text = build_patch_text(config.repo_path, working_root, self._all_edits(config.repo_path, working_root))
            title = 'Reflexa autonomous fix'
            publication = publish_output(
                run_id=config.run_id,
                title=title,
                summary=last_summary,
                patch_text=patch_text,
                artifact_dir=self.publisher.output_dir,
                workspace_root=config.repo_path,
            )
            self._emit(
                sink,
                events,
                config.run_id,
                'artifact_written',
                'Repair artifacts were exported.',
                {
                    'mode': publication.mode,
                    'patch_path': str(publication.patch_path) if publication.patch_path else None,
                    'summary_path': str(publication.summary_path) if publication.summary_path else None,
                    'pull_request_url': publication.pull_request_url,
                },
            )
            self._emit(
                sink,
                events,
                config.run_id,
                'run_finished',
                'Reflexa run finished.',
                {'status': status.value, 'attempts': attempts},
            )
            return RunResult(
                run_id=config.run_id,
                status=status,
                attempts=attempts,
                summary=last_summary,
                events=events,
                publication=publication,
            )

    def _request_proposal(self, context: RepairContext, sink: EventSink, events: list[RunEvent], run_id: str):
        try:
            return self.provider.propose_patch(context)
        except Exception as exc:  # noqa: BLE001
            if not self.allow_heuristic_fallback or isinstance(self.provider, HeuristicRepairProvider):
                self._emit(
                    sink,
                    events,
                    run_id,
                    'provider_error',
                    'OpenAI repair provider failed.',
                    {'error': str(exc), 'fallback_enabled': self.allow_heuristic_fallback},
                )
                raise
            self._emit(
                sink,
                events,
                run_id,
                'provider_fallback',
                'OpenAI repair provider failed; using heuristic fallback.',
                {'error': str(exc)},
            )
            return HeuristicRepairProvider().propose_patch(context)

    def _emit(self, sink: EventSink, events: list[RunEvent], run_id: str, kind: str, message: str, data: dict[str, object]) -> None:
        event = RunEvent(kind=kind, message=message, data={'run_id': run_id, **data})
        events.append(event)
        sink.emit(event)

    def _collect_relevant_files(self, repo_path: Path, failure_output: str) -> dict[str, str]:
        candidates: dict[str, str] = {}
        for path in sorted(repo_path.rglob('*.py')):
            rel_path = path.relative_to(repo_path).as_posix()
            try:
                candidates[rel_path] = path.read_text(encoding='utf-8')
            except UnicodeDecodeError:
                continue
            if len(candidates) >= 12:
                break
        mentioned_paths = self._paths_from_failure(failure_output)
        for rel_path in mentioned_paths:
            path = repo_path / rel_path
            if path.exists() and path.is_file():
                candidates[rel_path] = path.read_text(encoding='utf-8')
        return candidates

    def _paths_from_failure(self, failure_output: str) -> list[str]:
        paths: list[str] = []
        for line in failure_output.splitlines():
            if line.startswith('File '):
                parts = line.split('"')
                if len(parts) >= 2:
                    candidate = parts[1]
                    if candidate.endswith('.py'):
                        paths.append(Path(candidate).as_posix().lstrip('/\\'))
        return paths

    def _apply_edits(self, repo_path: Path, edits: list[PatchEdit]) -> None:
        for edit in edits:
            target = repo_path / edit.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(edit.content, encoding='utf-8')

    def _all_edits(self, original_root: Path, working_root: Path) -> list[PatchEdit]:
        edits: list[PatchEdit] = []
        for path in working_root.rglob('*.py'):
            rel_path = path.relative_to(working_root).as_posix()
            original_path = original_root / rel_path
            if original_path.exists():
                original = original_path.read_text(encoding='utf-8')
                updated = path.read_text(encoding='utf-8')
                if original != updated:
                    edits.append(PatchEdit(path=rel_path, content=updated))
        return edits


