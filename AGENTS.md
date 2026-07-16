# Repository Guidelines

## Project Structure & Module Organization
The runnable MVP lives in `Reflexa/` and is exposed through the top-level loader `reflexa.py`. Core code is organized as a small Python package: `Reflexa/cli.py` for command entrypoints, `Reflexa/orchestrator.py` for the repair loop, `Reflexa/sandbox.py` for local or Docker execution, `Reflexa/providers.py` for model-backed repair proposals, `Reflexa/publisher.py` for artifact/PR output, and `Reflexa/dashboard.py` for the live UI. Tests live under `tests/`, with the demo fixture repository in `tests/fixtures/demo_repo/`.

## Build, Test, and Development Commands
Use standard library tooling only:
- `python -m unittest discover -s tests -v` runs the full test suite.
- `python -m reflexa run --repo tests/fixtures/demo_repo --tests "python -m unittest discover -s tests -v"` runs one repair cycle.
- `python -m reflexa serve --repo tests/fixtures/demo_repo --tests "python -m unittest discover -s tests -v"` starts the dashboard and manual trigger flow.

## Coding Style & Naming Conventions
This repo uses plain Python with 4-space indentation, `snake_case` for functions and modules, and `PascalCase` for classes. Keep modules focused and prefer explicit names such as `orchestrator.py`, `sandbox.py`, and `test_orchestrator.py`. Add short comments only when the control flow is not obvious.

## Testing Guidelines
Tests use `unittest`. Put unit tests in `tests/` with `test_*.py` filenames and keep fixture repos isolated under `tests/fixtures/`. The current integration fixture intentionally starts with one failing test so the repair loop can prove it applies a patch and re-validates successfully.

## Commit & Pull Request Guidelines
There is no historical commit convention to mirror, so use short imperative commits like `add local artifact publisher`. Pull requests should summarize the failing test, the patch strategy, and the validation result. Include screenshots for dashboard changes and call out whether the run produced a GitHub PR or a local patch artifact.

## Security & Configuration Tips
Never execute generated code on the host when using the Docker path. Keep `GITHUB_TOKEN` and `OPENAI_API_KEY` out of source control. The MVP intentionally depends on an existing failing test and does not generate new tests in this version.
