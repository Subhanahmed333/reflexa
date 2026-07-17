from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from reflexa.providers import GroqRepairProvider, OpenAIRepairProvider, RepairContractError, RepairContext


class OpenAIProviderContractTests(unittest.TestCase):
    def test_parses_json_wrapped_in_code_fences(self) -> None:
        provider = OpenAIRepairProvider()
        payload = {
            "analysis": "The test fails because the boundary is exclusive.",
            "summary": "Make the range inclusive.",
            "confidence": 0.92,
            "assumptions": ["The bug is in the target file."],
            "verification_notes": ["The patch changes one boundary expression."],
            "edits": [
                {
                    "path": "app/math_utils.py",
                    "content": "def inclusive_sum(limit):\n    return sum(range(limit + 1))\n",
                    "note": "Include the upper bound in the range.",
                }
            ],
        }
        parsed = provider._parse_repair_plan(f"```json\n{json.dumps(payload)}\n```")

        self.assertEqual(parsed["analysis"], "The test fails because the boundary is exclusive.")
        self.assertEqual(parsed["edits"][0]["path"], "app/math_utils.py")

    def test_rejects_malformed_repair_plan(self) -> None:
        provider = OpenAIRepairProvider()
        with self.assertRaises(RepairContractError):
            provider._parse_repair_plan(
                '{"analysis":"a","summary":"b","confidence":0.5,"assumptions":[],"verification_notes":[]}'
            )

    def test_prompt_mentions_strict_contract(self) -> None:
        provider = OpenAIRepairProvider()
        prompt = provider._build_system_prompt()
        self.assertIn("Return exactly one JSON object", prompt)
        self.assertIn("smallest safe patch", prompt)


class GroqProviderContractTests(unittest.TestCase):
    def test_uses_chat_completions_shape(self) -> None:
        provider = GroqRepairProvider(api_key="groq-token")
        context = RepairContext(
            failure_output="AssertionError: 6 != 3",
            repository=Path("tests/fixtures/demo_repo"),
            relevant_files={"app/math_utils.py": "def inclusive_sum(limit):\n    return sum(range(limit))\n"},
            test_command=["python", "-m", "unittest", "discover", "-s", "tests"],
        )

        payload = provider._build_payload(context)

        self.assertEqual(provider.endpoint, "https://api.groq.com/openai/v1/chat/completions")
        self.assertIn("messages", payload)
        self.assertNotIn("input", payload)
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertEqual(payload["messages"][1]["role"], "user")
        self.assertEqual(payload["response_format"]["type"], "json_schema")
        self.assertTrue(payload["response_format"]["json_schema"]["strict"])

    def test_parses_chat_completion_response_content(self) -> None:
        provider = GroqRepairProvider(api_key="groq-token")
        body = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "```json\n{\"analysis\":\"a\",\"summary\":\"b\",\"confidence\":0.5,\"assumptions\":[],\"verification_notes\":[\"ok\"],\"edits\":[{\"path\":\"app/math_utils.py\",\"content\":\"x\",\"note\":\"y\"}]}\n```",
                    }
                }
            ]
        }

        text = provider._extract_text(body)
        self.assertIn('"analysis":"a"', text)

    def test_resolves_api_key_from_environment(self) -> None:
        with patch.dict(os.environ, {"GROQ_API_KEY": "env-token"}, clear=False):
            self.assertEqual(GroqRepairProvider()._resolve_api_key(), "env-token")
