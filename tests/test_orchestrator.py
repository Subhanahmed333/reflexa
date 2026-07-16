from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from reflexa.models import PatchEdit, RunStatus
from reflexa.orchestrator import Orchestrator, OrchestratorConfig
from reflexa.publisher import LocalArtifactPublisher


class FakeRunner:
    def __init__(self) -> None:
        self.calls = 0

    def run(self, repo_path: Path, command: list[str], timeout_seconds: int = 60):
        from reflexa.models import SandboxResult

        self.calls += 1
        if self.calls == 1:
            return SandboxResult(command=command, returncode=1, stdout="", stderr='AssertionError: 6 != 3', duration_seconds=0.01)
        return SandboxResult(command=command, returncode=0, stdout="ok", stderr="", duration_seconds=0.01)


class FakeProvider:
    def propose_patch(self, context):
        from reflexa.models import PatchProposal

        for rel_path, content in context.relevant_files.items():
            if "range(limit)" in content:
                return PatchProposal(
                    analysis="Fix the inclusive boundary.",
                    summary="Change the range to include the upper bound.",
                    edits=[PatchEdit(path=rel_path, content=content.replace("range(limit)", "range(limit + 1)", 1))],
                )
        return PatchProposal(analysis="No change needed.", summary="No change needed.", edits=[])


class OrchestratorTests(unittest.TestCase):
    def test_repair_cycle_succeeds_with_retry(self) -> None:
        repo = Path("tests/fixtures/demo_repo")
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "artifacts"
            orchestrator = Orchestrator(provider=FakeProvider(), sandbox=FakeRunner(), publisher=LocalArtifactPublisher(output_dir))
            result = orchestrator.run(OrchestratorConfig(repo_path=repo, test_command=["python", "-m", "unittest", "discover", "-s", "tests"], max_attempts=3))
            self.assertEqual(result.status, RunStatus.SUCCEEDED)
            self.assertGreaterEqual(result.attempts, 2)
            self.assertTrue(result.publication and result.publication.patch_path and result.publication.patch_path.exists())

