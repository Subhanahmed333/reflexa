from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from reflexa.cli import parse_test_command
from reflexa.dashboard import RunStore
from reflexa.models import RunEvent


class CliTests(unittest.TestCase):
    def test_parse_test_command(self) -> None:
        self.assertEqual(parse_test_command("python -m unittest -v"), ["python", "-m", "unittest", "-v"])


class RunStoreTests(unittest.TestCase):
    def test_store_collects_events(self) -> None:
        store = RunStore()
        store.create("run-1")
        store.update(RunEvent(kind="run_started", message="start", data={"run_id": "run-1"}))
        snapshot = store.snapshot()
        self.assertIn("run-1", snapshot)
        self.assertEqual(snapshot["run-1"]["status"], "running")

