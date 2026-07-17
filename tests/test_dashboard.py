from __future__ import annotations

import unittest

from reflexa.dashboard import build_dashboard_state


class DashboardStateTests(unittest.TestCase):
    def test_groups_attempts_and_counts_metrics(self) -> None:
        snapshot = {
            "run-1": {
                "run_id": "run-1",
                "status": "succeeded",
                "summary": "Validation passed on attempt 2.",
                "publication": {"patch_text": "diff --git a/demo.py b/demo.py"},
                "started_at": "2026-07-17T10:00:00+00:00",
                "finished_at": "2026-07-17T10:02:00+00:00",
                "last_event_at": "2026-07-17T10:02:00+00:00",
                "events": [
                    {"kind": "run_started", "message": "start", "data": {"run_id": "run-1"}, "timestamp": "2026-07-17T10:00:00+00:00"},
                    {"kind": "attempt_started", "message": "Attempt 1 started.", "data": {"run_id": "run-1", "attempt": 1}, "timestamp": "2026-07-17T10:00:05+00:00"},
                    {"kind": "test_result", "message": "Test run completed.", "data": {"run_id": "run-1", "attempt": 1, "passed": False, "returncode": 1, "stdout": "", "stderr": "AssertionError", "duration_seconds": 0.2}, "timestamp": "2026-07-17T10:00:10+00:00"},
                    {"kind": "failure_detected", "message": "A failing test was detected.", "data": {"run_id": "run-1", "attempt": 1, "failure_output": "AssertionError"}, "timestamp": "2026-07-17T10:00:11+00:00"},
                    {"kind": "diagnosis", "message": "Fix the off-by-one boundary.", "data": {"run_id": "run-1", "attempt": 1, "summary": "Loop includes the wrong bound.", "confidence": 0.9, "assumptions": ["Demo repo is Python"], "verification_notes": ["Re-run the same test command"]}, "timestamp": "2026-07-17T10:00:20+00:00"},
                    {"kind": "patch_applied", "message": "Patch applied to the working copy.", "data": {"run_id": "run-1", "attempt": 1, "edits": ["demo.py"]}, "timestamp": "2026-07-17T10:00:30+00:00"},
                    {"kind": "attempt_started", "message": "Attempt 2 started.", "data": {"run_id": "run-1", "attempt": 2}, "timestamp": "2026-07-17T10:01:00+00:00"},
                    {"kind": "test_result", "message": "Test run completed.", "data": {"run_id": "run-1", "attempt": 2, "passed": True, "returncode": 0, "stdout": "ok", "stderr": "", "duration_seconds": 0.3}, "timestamp": "2026-07-17T10:01:10+00:00"},
                    {"kind": "artifact_written", "message": "Repair artifacts were exported.", "data": {"run_id": "run-1", "patch_text": "diff --git a/demo.py b/demo.py"}, "timestamp": "2026-07-17T10:01:50+00:00"},
                    {"kind": "run_finished", "message": "Reflexa run finished.", "data": {"run_id": "run-1", "status": "succeeded", "attempts": 2, "summary": "Validation passed on attempt 2."}, "timestamp": "2026-07-17T10:02:00+00:00"},
                ],
            }
        }

        state = build_dashboard_state(snapshot)
        selected = state["selected_run"]
        self.assertEqual(state["selected_run_id"], "run-1")
        self.assertIsNotNone(selected)
        self.assertEqual(selected["attempt_count"], 2)
        self.assertEqual(selected["status"], "succeeded")
        self.assertEqual(state["totals"]["attempts"], 2)
        self.assertEqual(state["totals"]["provider_steps"], 1)
        self.assertEqual(state["totals"]["sandbox_steps"], 2)
        self.assertEqual(selected["attempts"][0]["patch_paths"], ["demo.py"])
        self.assertEqual(selected["publication"]["patch_text"], "diff --git a/demo.py b/demo.py")

    def test_prefers_running_run_when_available(self) -> None:
        snapshot = {
            "run-done": {
                "run_id": "run-done",
                "status": "succeeded",
                "summary": "done",
                "publication": None,
                "events": [],
                "started_at": "2026-07-17T09:00:00+00:00",
                "finished_at": "2026-07-17T09:01:00+00:00",
                "last_event_at": "2026-07-17T09:01:00+00:00",
            },
            "run-live": {
                "run_id": "run-live",
                "status": "running",
                "summary": "still working",
                "publication": None,
                "events": [],
                "started_at": "2026-07-17T10:00:00+00:00",
                "finished_at": None,
                "last_event_at": "2026-07-17T10:00:30+00:00",
            },
        }

        state = build_dashboard_state(snapshot)
        self.assertEqual(state["selected_run_id"], "run-live")
        self.assertEqual(state["selected_run"]["status"], "running")


if __name__ == "__main__":
    unittest.main()
