from __future__ import annotations

import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from reflexa.cli import create_orchestrator, parse_test_command
from reflexa.dashboard import RunStore
from reflexa.models import RunEvent
from reflexa.providers import GroqRepairProvider, OpenAIRepairProvider


class CliTests(unittest.TestCase):
    def test_parse_test_command(self) -> None:
        self.assertEqual(parse_test_command("python -m unittest -v"), ["python", "-m", "unittest", "-v"])

    def test_create_orchestrator_uses_openai_by_default(self) -> None:
        args = Namespace(
            provider="openai",
            sandbox="local",
            openai_api_key="openai-token",
            groq_api_key=None,
            e2b_api_key=None,
            allow_heuristic_fallback=False,
            output_dir=".reflexa_artifacts",
        )
        orchestrator = create_orchestrator(args)
        self.assertIsInstance(orchestrator.provider, OpenAIRepairProvider)
        self.assertEqual(orchestrator.provider.api_key, "openai-token")

    def test_create_orchestrator_uses_groq_when_requested(self) -> None:
        args = Namespace(
            provider="groq",
            sandbox="local",
            openai_api_key=None,
            groq_api_key="groq-token",
            e2b_api_key=None,
            allow_heuristic_fallback=False,
            output_dir=".reflexa_artifacts",
        )
        orchestrator = create_orchestrator(args)
        self.assertIsInstance(orchestrator.provider, GroqRepairProvider)
        self.assertEqual(orchestrator.provider.api_key, "groq-token")


class RunStoreTests(unittest.TestCase):
    def test_store_collects_events(self) -> None:
        store = RunStore()
        store.create("run-1")
        store.update(RunEvent(kind="run_started", message="start", data={"run_id": "run-1"}))
        snapshot = store.snapshot()
        self.assertIn("run-1", snapshot)
        self.assertEqual(snapshot["run-1"]["status"], "running")
