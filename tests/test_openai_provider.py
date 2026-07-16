from __future__ import annotations

import json
import unittest

from reflexa.providers import OpenAIRepairProvider, RepairContractError


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

