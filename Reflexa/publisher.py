from __future__ import annotations

import difflib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib import request

from .models import PatchEdit, PublicationResult


def _read_text(path: Path) -> str:
    return path.read_text(encoding='utf-8')


def build_patch_text(original_root: Path, working_root: Path, edits: Iterable[PatchEdit]) -> str:
    chunks: list[str] = []
    for edit in edits:
        original_path = original_root / edit.path
        working_path = working_root / edit.path
        before = _read_text(original_path) if original_path.exists() else ''
        after = _read_text(working_path) if working_path.exists() else edit.content
        chunks.extend(
            difflib.unified_diff(
                before.splitlines(),
                after.splitlines(),
                fromfile=str(original_path),
                tofile=str(working_path),
                lineterm='',
            )
        )
    return '\n'.join(chunks) + ('\n' if chunks else '')


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault('GIT_AUTHOR_NAME', 'Reflexa')
    env.setdefault('GIT_AUTHOR_EMAIL', 'reflexa@example.com')
    env.setdefault('GIT_COMMITTER_NAME', env['GIT_AUTHOR_NAME'])
    env.setdefault('GIT_COMMITTER_EMAIL', env['GIT_AUTHOR_EMAIL'])
    return env


def _run_git(args: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True, check=False, env=env)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or 'git command failed')
    return completed.stdout.strip()


def _discover_github_repository(cwd: Path) -> str | None:
    for value in (os.getenv('GITHUB_REPOSITORY'), os.getenv('GITHUB_REPO')):
        if value:
            return value
    try:
        remote_url = _run_git(['remote', 'get-url', 'origin'], cwd=cwd)
    except Exception:
        return None
    match = re.search(r'github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/.]+)(?:\.git)?$', remote_url)
    if not match:
        return None
    return f"{match.group('owner')}/{match.group('repo')}"


def _discover_base_branch(cwd: Path) -> str:
    return os.getenv('GITHUB_BASE_REF') or os.getenv('REFLEXA_GITHUB_BASE_BRANCH') or 'main'


def _authenticated_remote_url(repository: str, token: str) -> str:
    return f'https://x-access-token:{token}@github.com/{repository}.git'


def _push_github_branch(workspace_root: Path, run_id: str, token: str, repository: str, commit_message: str) -> str:
    branch = f'reflexa/{run_id}'
    env = _git_env()
    _run_git(['checkout', '-b', branch], cwd=workspace_root, env=env)
    _run_git(['add', '-A'], cwd=workspace_root, env=env)
    staged = _run_git(['diff', '--cached', '--name-only'], cwd=workspace_root, env=env)
    if not staged:
        raise RuntimeError('No changes were staged for commit.')
    _run_git(['commit', '-m', commit_message], cwd=workspace_root, env=env)
    remote_url = _authenticated_remote_url(repository, token)
    _run_git(['push', '--set-upstream', remote_url, branch], cwd=workspace_root, env=env)
    return branch


@dataclass(slots=True)
class LocalArtifactPublisher:
    output_dir: Path = Path('.reflexa_artifacts')

    def publish(self, run_id: str, title: str, summary: str, patch_text: str) -> PublicationResult:
        run_dir = self.output_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        patch_path = run_dir / 'patch.diff'
        summary_path = run_dir / 'summary.md'
        patch_path.write_text(patch_text, encoding='utf-8')
        summary_path.write_text(f'# {title}\n\n{summary}\n', encoding='utf-8')
        return PublicationResult(
            mode='local_artifacts',
            title=title,
            patch_path=patch_path,
            summary_path=summary_path,
        )


@dataclass(slots=True)
class GitHubPRPublisher:
    token: str | None = None
    repository: str | None = None
    api_base: str = 'https://api.github.com'

    def publish(self, title: str, summary: str, head: str, base: str = 'main') -> PublicationResult:
        token = self.token or os.getenv('GITHUB_TOKEN')
        repository = self.repository or os.getenv('GITHUB_REPOSITORY') or os.getenv('GITHUB_REPO')
        if not token or not repository:
            raise RuntimeError('GITHUB_TOKEN and GITHUB_REPOSITORY are required for GitHub PR publishing.')

        payload = json.dumps(
            {
                'title': title,
                'head': head,
                'base': base,
                'body': summary,
            }
        ).encode('utf-8')
        req = request.Request(
            f'{self.api_base}/repos/{repository}/pulls',
            data=payload,
            headers={
                'Authorization': f'Bearer {token}',
                'Accept': 'application/vnd.github+json',
                'Content-Type': 'application/json',
            },
            method='POST',
        )
        with request.urlopen(req, timeout=60) as response:
            body = json.loads(response.read().decode('utf-8'))
        return PublicationResult(
            mode='github_pr',
            title=title,
            pull_request_url=body.get('html_url'),
        )


def publish_output(run_id: str, title: str, summary: str, patch_text: str, artifact_dir: Path | None = None, workspace_root: Path | None = None) -> PublicationResult:
    workspace_root = workspace_root or (artifact_dir.parent if artifact_dir else Path.cwd())
    token = os.getenv('GITHUB_TOKEN')
    repository = _discover_github_repository(workspace_root)
    base = _discover_base_branch(workspace_root)

    if token and repository:
        try:
            head = _push_github_branch(workspace_root, run_id, token, repository, title)
            return GitHubPRPublisher(token=token, repository=repository).publish(title=title, summary=summary, head=head, base=base)
        except Exception as exc:
            print(f'GitHub PR publish failed: {exc}')

    return LocalArtifactPublisher(output_dir=artifact_dir or Path('.reflexa_artifacts')).publish(run_id, title, summary, patch_text)
