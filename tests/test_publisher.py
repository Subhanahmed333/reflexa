from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import Reflexa.publisher as publisher
from Reflexa.publisher import LocalArtifactPublisher, _push_github_branch, publish_output


class PublisherTests(unittest.TestCase):
    def test_discovers_legacy_github_repo_env_name(self) -> None:
        with patch.dict('os.environ', {'GITHUB_REPO': 'Subhanahmed333/reflexa'}, clear=False):
            self.assertEqual(publisher._discover_github_repository(Path('.')), 'Subhanahmed333/reflexa')

    def test_publish_output_logs_and_falls_back_when_github_push_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir) / 'artifacts'
            with patch.dict('os.environ', {'GITHUB_TOKEN': 'token', 'GITHUB_REPOSITORY': 'Subhanahmed333/reflexa'}, clear=False):
                with patch('Reflexa.publisher._push_github_branch', side_effect=RuntimeError('push failed')):
                    with patch('builtins.print') as mock_print:
                        result = publish_output(
                            run_id='run-123',
                            title='Reflexa autonomous fix',
                            summary='fixed',
                            patch_text='diff --git a/a b/a\n',
                            artifact_dir=out_dir,
                            workspace_root=Path(tmpdir),
                        )
            self.assertEqual(result.mode, 'local_artifacts')
            self.assertTrue(result.patch_path and result.patch_path.exists())
            mock_print.assert_called()
            self.assertIn('GitHub PR publish failed: push failed', ' '.join(str(arg) for arg in mock_print.call_args.args))

    def test_push_github_branch_uses_authenticated_remote(self) -> None:
        calls: list[tuple[list[str], Path | None]] = []

        def fake_run_git(args: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
            calls.append((args, cwd))
            if args[:3] == ['diff', '--cached', '--name-only']:
                return 'app.py'
            return ''

        with patch('Reflexa.publisher._run_git', side_effect=fake_run_git):
            branch = _push_github_branch(Path('workspace'), 'abc123', 'secret-token', 'owner/repo', 'Reflexa autonomous fix')

        self.assertEqual(branch, 'reflexa/abc123')
        self.assertEqual(calls[0][0], ['checkout', '-b', 'reflexa/abc123'])
        self.assertEqual(calls[1][0], ['add', '-A'])
        self.assertEqual(calls[2][0], ['diff', '--cached', '--name-only'])
        self.assertEqual(calls[3][0], ['commit', '-m', 'Reflexa autonomous fix'])
        self.assertEqual(calls[4][0], ['push', '--set-upstream', 'https://x-access-token:secret-token@github.com/owner/repo.git', 'reflexa/abc123'])


if __name__ == '__main__':
    unittest.main()
