# Reflexa

Reflexa is a Python-first MVP for an autonomous bug-fixing loop: it runs a failing test, inspects the failure, proposes a patch, validates the patch in an isolated sandbox, and exports either a GitHub PR request or a local patch bundle.

## Quick Start

Run the unit and integration tests:

```powershell
python -m unittest discover -s tests -v
```

Launch the manual-trigger dashboard against the sample fixture:

```powershell
python -m reflexa serve --repo tests/fixtures/demo_repo --tests "python -m unittest discover -s tests -v" --host 127.0.0.1 --port 8000
```

Run a single repair cycle from the CLI:

```powershell
python -m reflexa run --repo tests/fixtures/demo_repo --tests "python -m unittest discover -s tests -v"
```

## Scope

This MVP assumes an existing failing test. It does not generate new tests and it keeps execution bounded through a sandbox runner and retry limit.

