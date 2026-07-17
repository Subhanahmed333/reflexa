# Reflexa Handoff Guide

## What This Project Is
Reflexa is a Python MVP for an autonomous bug-fixing loop. It takes a repository with an existing failing test, runs the test in an isolated sandbox, asks a provider for a repair plan, applies the patch, re-validates, and exports either a local patch bundle or a GitHub PR.

## Current State
What is working now:
- Manual trigger flow through the CLI and dashboard.
- Provider support for `openai`, `groq`, and `heuristic`.
- E2B sandbox execution for validation.
- Retry-limited repair loop with run events and attempt tracking.
- Live dashboard that shows runs, attempts, event stream, and patch output.
- GitHub publishing path with branch push and fallback logging.

What is still missing or intentionally out of scope:
- No CI/webhook automation.
- No multi-tenant auth or user accounts.
- No automatic test generation.
- No production deployment hardening.
- No guarantee that every external API key or GitHub target is configured on a fresh machine.

## Repository Layout
- `reflexa.py`: module entrypoint.
- `Reflexa/cli.py`: CLI argument parsing and run/serve commands.
- `Reflexa/orchestrator.py`: repair loop, retries, patch application, events.
- `Reflexa/providers.py`: OpenAI, Groq, and heuristic repair providers.
- `Reflexa/sandbox.py`: E2B and local sandbox runners.
- `Reflexa/publisher.py`: local artifact export and GitHub PR publishing.
- `Reflexa/dashboard.py`: live dashboard server and in-memory run store.
- `tests/`: unit and integration tests.
- `tests/fixtures/demo_repo/`: demo repo with a known failing test.

## Requirements
- Python 3.10 or newer.
- Internet access for provider and E2B calls.
- A valid E2B account/API key.
- Optional: OpenAI API key, Groq API key, GitHub token, GitHub repository access.

## Setup From Scratch
1. Clone the repo.
2. Create a virtual environment.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

3. Install the package.

```powershell
pip install -e .
```

4. Install test tooling if needed.

```powershell
pip install pytest
```

5. Set environment variables.

```powershell
setx E2B_API_KEY "your-e2b-key"
setx OPENAI_API_KEY "your-openai-key"
setx GROQ_API_KEY "your-groq-key"
setx GITHUB_TOKEN "your-github-token"
setx GITHUB_REPOSITORY "Subhanahmed333/reflexa"
```

Use a new shell after `setx`, or set them for the current session with `$env:NAME = 'value'`.

## How To Run
### Run the test suite
```powershell
python -m unittest discover -s tests -v
```

### Run one repair cycle
```powershell
python -m reflexa run --repo tests/fixtures/demo_repo --tests "python -m unittest discover -s tests -v"
```

### Run with a specific provider
```powershell
python -m reflexa run --provider openai --repo tests/fixtures/demo_repo --tests "python -m unittest discover -s tests -v"
python -m reflexa run --provider groq --repo tests/fixtures/demo_repo --tests "python -m unittest discover -s tests -v"
python -m reflexa run --provider heuristic --repo tests/fixtures/demo_repo --tests "python -m unittest discover -s tests -v"
```

### Start the dashboard
```powershell
python -m reflexa serve --repo tests/fixtures/demo_repo --tests "python -m unittest discover -s tests -v" --host 127.0.0.1 --port 8000
```

If port `8000` is already busy, start on another port:

```powershell
python -m reflexa serve --repo tests/fixtures/demo_repo --tests "python -m unittest discover -s tests -v" --host 127.0.0.1 --port 8008
```

## How The System Works
1. `cli.py` parses the repo path, test command, provider, and sandbox choice.
2. `orchestrator.py` copies the target repo into a temp workspace.
3. The sandbox runs the failing test command.
4. The provider receives failure output and relevant files, then returns a repair proposal.
5. The orchestrator applies the proposed edits.
6. The sandbox reruns the test command.
7. The loop retries up to the configured cap and then exports either a patch summary or a GitHub PR.
8. The dashboard streams events in real time and shows attempt-level details.

## Working With The Demo Repo
The demo fixture lives at `tests/fixtures/demo_repo/`. It is intentionally designed to fail first so the repair loop has something to fix.

Typical demo command:

```powershell
python -m reflexa run --repo tests/fixtures/demo_repo --tests "python -m unittest discover -s tests -v" --provider openai
```

## Environment Variables
Required for real runs:
- `E2B_API_KEY`: required for the cloud sandbox.

Provider variables:
- `OPENAI_API_KEY`: used by the OpenAI provider.
- `GROQ_API_KEY`: used by the Groq provider.

GitHub publishing variables:
- `GITHUB_TOKEN`: token with repo write access.
- `GITHUB_REPOSITORY`: repository in `owner/name` form.

## Known Caveats
- The project is still an MVP and is not production hardened.
- The dashboard is local-only and single-user.
- The provider and sandbox are external dependencies, so failures may come from missing credentials or network issues.
- The GitHub publishing path needs a valid token and repository target before it can create a PR.
- The sandbox is cloud-hosted through E2B, so generated code does not run on the host machine.

## What A New Person Should Verify First
1. `python -m unittest discover -s tests -v`
2. `python -m reflexa run --repo tests/fixtures/demo_repo --tests "python -m unittest discover -s tests -v"`
3. `python -m reflexa serve --repo tests/fixtures/demo_repo --tests "python -m unittest discover -s tests -v"`
4. Confirm E2B credentials work.
5. Confirm either OpenAI or Groq credentials work.
6. If GitHub PR publishing is expected, confirm `GITHUB_TOKEN` and `GITHUB_REPOSITORY`.

## Suggested First Demo Flow
1. Open the dashboard.
2. Start a repair run.
3. Watch the failing test, diagnosis, patch, sandbox rerun, and final artifact.
4. If GitHub publishing is configured, confirm the PR URL.

## Support Notes For A Friend
If you hand this to someone else, tell them to keep the following in mind:
- Use the demo fixture first.
- Prefer `openai` for the primary path, `groq` as the alternate API path, and `heuristic` only as fallback.
- Keep secrets out of the repo.
- If the dashboard looks blank, check the console for syntax or dependency errors and confirm the correct port is open.
