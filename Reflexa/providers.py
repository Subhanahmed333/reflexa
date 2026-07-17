from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Protocol
from urllib import request

from .models import PatchEdit, PatchProposal


class RepairContractError(ValueError):
    pass


@dataclass(slots=True)
class RepairContext:
    failure_output: str
    repository: Path
    relevant_files: dict[str, str]
    test_command: list[str]


class RepairProvider(Protocol):
    def propose_patch(self, context: RepairContext) -> PatchProposal: ...


@dataclass(slots=True)
class HeuristicRepairProvider:
    """Deterministic fallback that can repair simple off-by-one style failures."""

    def propose_patch(self, context: RepairContext) -> PatchProposal:
        analysis = self._build_analysis(context.failure_output)
        edits = self._build_edits(context)
        summary = "Apply a targeted fix based on the failing test output."
        return PatchProposal(analysis=analysis, summary=summary, edits=edits)

    def _build_analysis(self, failure_output: str) -> str:
        if "AssertionError" in failure_output and "!=" in failure_output:
            return "The test is failing because the observed value does not match the expected value."
        if "ImportError" in failure_output:
            return "The test is failing because a module import could not be resolved."
        return "The failure appears to be a small mechanical bug in the code path exercised by the test."

    def _build_edits(self, context: RepairContext) -> list[PatchEdit]:
        edits: list[PatchEdit] = []
        pattern = re.compile(r"range\((?P<value>[A-Za-z_][A-Za-z0-9_]*)\)")
        for rel_path, content in context.relevant_files.items():
            match = pattern.search(content)
            if not match:
                continue
            replacement = pattern.sub(r"range(\g<value> + 1)", content, count=1)
            if replacement != content:
                note = "Adjust an inclusive range boundary."
                edits.append(PatchEdit(path=rel_path, content=replacement, note=note))
                break
        return edits


REPAIR_PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["analysis", "summary", "confidence", "edits", "assumptions", "verification_notes"],
    "properties": {
        "analysis": {"type": "string", "minLength": 1},
        "summary": {"type": "string", "minLength": 1},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "assumptions": {"type": "array", "items": {"type": "string", "minLength": 1}},
        "verification_notes": {"type": "array", "items": {"type": "string", "minLength": 1}},
        "edits": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["path", "content", "note"],
                "properties": {
                    "path": {"type": "string", "minLength": 1},
                    "content": {"type": "string"},
                    "note": {"type": "string"},
                },
            },
        },
    },
}


class _RepairContractMixin:
    provider_name: ClassVar[str] = "Provider"
    api_key_env_var: ClassVar[str] = "API_KEY"

    api_key: str | None
    model: str
    endpoint: str
    max_response_attempts: int

    def _resolve_api_key(self) -> str:
        api_key = self.api_key or os.getenv(self.api_key_env_var)
        if not api_key:
            raise RuntimeError(f"{self.api_key_env_var} is required for {self.__class__.__name__}.")
        return api_key

    def _build_system_prompt(self) -> str:
        return (
            "You are Reflexa, an autonomous bug-fixing agent.\n"
            "Return exactly one JSON object matching the schema provided by the caller.\n"
            "Do not output markdown, code fences, prose outside the object, or trailing commentary.\n"
            "Repair only the files needed to fix the failing test.\n"
            "Prefer the smallest safe patch.\n"
            "Every edit must be a complete replacement for a file path relative to the repository root.\n"
            "If you cannot confidently repair the failure, still return the best concrete edit plan you can justify in the schema."
        )

    def _build_user_prompt(
        self,
        context: RepairContext,
        validation_errors: list[str] | None = None,
        previous_output: str | None = None,
    ) -> str:
        files = "\n\n".join(f"FILE: {path}\n{content}" for path, content in context.relevant_files.items())
        prompt = [
            "Diagnose the failing test and produce a repair plan that satisfies the JSON schema.",
            f"Test command: {' '.join(context.test_command)}",
            "",
            "Failure output:",
            context.failure_output,
            "",
            "Relevant files:",
            files or "(none provided)",
            "",
            "Contract requirements:",
            "- analysis: one concise paragraph explaining the root cause.",
            "- summary: one concise sentence for humans.",
            "- confidence: a number from 0 to 1.",
            "- assumptions: short strings naming any assumptions made.",
            "- verification_notes: short strings explaining why the patch should pass.",
            "- edits: at least one complete file replacement.",
            "- Each edit note should explain the reason for that file change.",
        ]
        if validation_errors:
            prompt.extend(
                [
                    "",
                    "Your previous response did not satisfy the contract.",
                    "Fix these validation errors and return a corrected JSON object only:",
                    *[f"- {error}" for error in validation_errors],
                ]
            )
        if previous_output:
            prompt.extend(
                [
                    "",
                    "Previous output to correct:",
                    previous_output,
                ]
            )
        return "\n".join(prompt)

    def _parse_repair_plan(self, text: str) -> dict:
        payload = self._load_json_like(text)
        if not isinstance(payload, dict):
            raise RepairContractError("Repair plan must be a JSON object.")

        allowed_keys = {"analysis", "summary", "confidence", "edits", "assumptions", "verification_notes"}
        extra_keys = sorted(set(payload) - allowed_keys)
        missing_keys = sorted(allowed_keys - set(payload))
        if extra_keys:
            raise RepairContractError(f"Unexpected top-level keys: {', '.join(extra_keys)}")
        if missing_keys:
            raise RepairContractError(f"Missing top-level keys: {', '.join(missing_keys)}")

        analysis = self._require_str(payload, "analysis")
        summary = self._require_str(payload, "summary")
        confidence = payload.get("confidence")
        if not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
            raise RepairContractError("confidence must be a number between 0 and 1.")

        assumptions = self._require_str_list(payload.get("assumptions"), "assumptions")
        verification_notes = self._require_str_list(payload.get("verification_notes"), "verification_notes")
        edits = self._require_edits(payload.get("edits"))

        return {
            "analysis": analysis,
            "summary": summary,
            "confidence": float(confidence),
            "assumptions": assumptions,
            "verification_notes": verification_notes,
            "edits": edits,
        }

    def _load_json_like(self, text: str) -> object:
        candidate = text.strip()
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.IGNORECASE)
        candidate = re.sub(r"\s*```$", "", candidate)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise RepairContractError(f"Response was not valid JSON: {exc.msg}") from exc

    def _require_str(self, payload: dict, key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise RepairContractError(f"{key} must be a non-empty string.")
        return value.strip()

    def _require_str_list(self, value: object, key: str) -> list[str]:
        if not isinstance(value, list):
            raise RepairContractError(f"{key} must be a list of strings.")
        items: list[str] = []
        for index, item in enumerate(value):
            if not isinstance(item, str) or not item.strip():
                raise RepairContractError(f"{key}[{index}] must be a non-empty string.")
            items.append(item.strip())
        return items

    def _require_edits(self, value: object) -> list[dict[str, str]]:
        if not isinstance(value, list) or not value:
            raise RepairContractError("edits must be a non-empty list.")
        edits: list[dict[str, str]] = []
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                raise RepairContractError(f"edits[{index}] must be an object.")
            allowed = {"path", "content", "note"}
            extra_keys = sorted(set(item) - allowed)
            missing = sorted(k for k in allowed if k not in item)
            if extra_keys:
                raise RepairContractError(f"edits[{index}] has unexpected keys: {', '.join(extra_keys)}")
            if missing:
                raise RepairContractError(f"edits[{index}] is missing keys: {', '.join(missing)}")
            path = item.get("path")
            content = item.get("content")
            note = item.get("note")
            if not isinstance(path, str) or not path.strip():
                raise RepairContractError(f"edits[{index}].path must be a non-empty string.")
            if not isinstance(content, str):
                raise RepairContractError(f"edits[{index}].content must be a string.")
            if not isinstance(note, str):
                raise RepairContractError(f"edits[{index}].note must be a string.")
            normalized_path = Path(path).as_posix().lstrip("/\\")
            if normalized_path.startswith("..") or Path(normalized_path).is_absolute():
                raise RepairContractError(f"edits[{index}].path must stay within the repository root.")
            edits.append({"path": normalized_path, "content": content, "note": note})
        return edits

    def _format_validation_errors(self, exc: Exception, text: str) -> list[str]:
        errors = [str(exc)]
        candidate = text.strip()
        if candidate:
            errors.append(f"Model returned {len(candidate)} characters of output.")
        return errors

    def _proposal_from_parsed(self, parsed: dict) -> PatchProposal:
        return PatchProposal(
            analysis=parsed["analysis"],
            summary=parsed["summary"],
            edits=[PatchEdit(**item) for item in parsed["edits"]],
            confidence=parsed.get("confidence"),
            assumptions=parsed.get("assumptions", []),
            verification_notes=parsed.get("verification_notes", []),
            raw_response=parsed,
        )


@dataclass(slots=True)
class OpenAIRepairProvider(_RepairContractMixin):
    """OpenAI-backed repair provider with a strict JSON repair-plan contract."""

    provider_name: ClassVar[str] = "OpenAI"
    api_key_env_var: ClassVar[str] = "OPENAI_API_KEY"

    api_key: str | None = None
    model: str = "gpt-5.6"
    endpoint: str = "https://api.openai.com/v1/responses"
    max_response_attempts: int = 2

    def propose_patch(self, context: RepairContext) -> PatchProposal:
        api_key = self._resolve_api_key()
        validation_errors: list[str] = []
        previous_output = ""
        last_error: Exception | None = None

        for attempt in range(1, self.max_response_attempts + 1):
            payload = self._build_payload(
                context,
                validation_errors=validation_errors if attempt > 1 else None,
                previous_output=previous_output if attempt > 1 else None,
            )
            body = self._post_payload(api_key, payload)
            text = self._extract_text(body)
            previous_output = text
            try:
                parsed = self._parse_repair_plan(text)
                return self._proposal_from_parsed(parsed)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                validation_errors = self._format_validation_errors(exc, text)

        raise RuntimeError(
            f"{self.provider_name} repair plan did not satisfy the contract after {self.max_response_attempts} attempt(s): {last_error}"
        )

    def _build_payload(
        self,
        context: RepairContext,
        validation_errors: list[str] | None = None,
        previous_output: str | None = None,
    ) -> dict[str, object]:
        user_prompt = self._build_user_prompt(context, validation_errors=validation_errors, previous_output=previous_output)
        return {
            "model": self.model,
            "temperature": 0.1,
            "max_output_tokens": 1800,
            "input": [
                {"role": "system", "content": self._build_system_prompt()},
                {"role": "user", "content": user_prompt},
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "reflexa_repair_plan",
                    "strict": True,
                    "schema": REPAIR_PLAN_SCHEMA,
                }
            },
        }

    def _post_payload(self, api_key: str, payload: dict[str, object]) -> dict:
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            self.endpoint,
            data=data,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0",
            },
            method="POST",
        )

        opener = request.build_opener(request.ProxyHandler({}))
        with opener.open(req, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))

    def _extract_text(self, response_body: dict) -> str:
        if isinstance(response_body.get("output_text"), str) and response_body["output_text"].strip():
            return response_body["output_text"]
        for item in response_body.get("output", []):
            for content in item.get("content", []):
                text = content.get("text")
                if text:
                    return text
        raise RuntimeError(f"{self.provider_name} response did not contain any text output.")


@dataclass(slots=True)
class GroqRepairProvider(_RepairContractMixin):
    """Groq-backed repair provider using Groq's Chat Completions API."""

    provider_name: ClassVar[str] = "Groq"
    api_key_env_var: ClassVar[str] = "GROQ_API_KEY"

    api_key: str | None = None
    model: str = "openai/gpt-oss-120b"
    endpoint: str = "https://api.groq.com/openai/v1/chat/completions"
    max_response_attempts: int = 2

    def propose_patch(self, context: RepairContext) -> PatchProposal:
        api_key = self._resolve_api_key()
        validation_errors: list[str] = []
        previous_output = ""
        last_error: Exception | None = None

        for attempt in range(1, self.max_response_attempts + 1):
            payload = self._build_payload(
                context,
                validation_errors=validation_errors if attempt > 1 else None,
                previous_output=previous_output if attempt > 1 else None,
            )
            body = self._post_payload(api_key, payload)
            text = self._extract_text(body)
            previous_output = text
            try:
                parsed = self._parse_repair_plan(text)
                return self._proposal_from_parsed(parsed)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                validation_errors = self._format_validation_errors(exc, text)

        raise RuntimeError(
            f"{self.provider_name} repair plan did not satisfy the contract after {self.max_response_attempts} attempt(s): {last_error}"
        )

    def _build_payload(
        self,
        context: RepairContext,
        validation_errors: list[str] | None = None,
        previous_output: str | None = None,
    ) -> dict[str, object]:
        user_prompt = self._build_user_prompt(context, validation_errors=validation_errors, previous_output=previous_output)
        return {
            "model": self.model,
            "temperature": 0.1,
            "max_tokens": 1800,
            "messages": [
                {"role": "system", "content": self._build_system_prompt()},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "reflexa_repair_plan",
                    "strict": True,
                    "schema": REPAIR_PLAN_SCHEMA,
                },
            },
        }

    def _post_payload(self, api_key: str, payload: dict[str, object]) -> dict:
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            self.endpoint,
            data=data,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0",
            },
            method="POST",
        )

        opener = request.build_opener(request.ProxyHandler({}))
        with opener.open(req, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))

    def _extract_text(self, response_body: dict) -> str:
        choices = response_body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError(f"{self.provider_name} response did not contain any choices.")
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content
            if isinstance(content, list):
                parts = [part.get("text", "") for part in content if isinstance(part, dict) and isinstance(part.get("text"), str)]
                text = "".join(parts).strip()
                if text:
                    return text
        raise RuntimeError(f"{self.provider_name} response did not contain any message content.")










