# Reflexa

Reflexa is a Python-first MVP for an autonomous bug-fixing loop: it runs a failing test, inspects the failure, proposes a patch through OpenAI or Groq, validates the patch in an isolated E2B cloud sandbox, and exports either a GitHub PR request or a local patch bundle.

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

Use Groq explicitly for the repair provider:

```powershell
python -m reflexa run --provider groq --repo tests/fixtures/demo_repo --tests "python -m unittest discover -s tests -v"
```

## Scope

This MVP assumes an existing failing test. It does not generate new tests and it keeps execution bounded through an E2B sandbox timeout and retry limit.

## Security

Generated code executes inside a third-party E2B microVM, not on the host machine. Keep `OPENAI_API_KEY`, `GROQ_API_KEY`, and `E2B_API_KEY` out of source control. The sandbox should be treated as an isolated execution boundary with explicit timeout limits and a fresh workspace sync for each run.
