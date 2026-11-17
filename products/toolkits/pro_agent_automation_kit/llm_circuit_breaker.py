#!/usr/bin/env python3
"""Zero-dependency LLM/API Structured-Output Validator, Token Budget & Failover Router."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence


class BudgetExceededError(RuntimeError):
    """Raised when cumulative token or USD cost budget is exceeded."""


class StructuredValidationError(ValueError):
    """Raised when provider output fails JSON extraction or schema validation."""


class AllProvidersFailedError(RuntimeError):
    """Raised when primary and all fallback providers fail or trip breakers."""


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL | re.IGNORECASE)


def extract_strict_json(raw_text: str) -> Any:
    """Extract and parse JSON from raw text or fenced Markdown responses."""
    cleaned = raw_text.strip()
    fence_match = _JSON_FENCE_RE.search(cleaned)
    if fence_match:
        cleaned = fence_match.group(1).strip()
    else:
        first_obj = cleaned.find("{")
        last_obj = cleaned.rfind("}")
        if first_obj != -1 and last_obj > first_obj:
            cleaned = cleaned[first_obj : last_obj + 1]
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise StructuredValidationError(f"Invalid JSON output: {exc}") from exc


def validate_required_fields(data: Any, schema_types: Mapping[str, type | tuple[type, ...]]) -> dict[str, Any]:
    """Validate that `data` is a dict containing required keys with matching Python types."""
    if not isinstance(data, dict):
        raise StructuredValidationError(f"Expected JSON object, got {type(data).__name__}")
    for key, expected_type in schema_types.items():
        if key not in data:
            raise StructuredValidationError(f"Missing required field '{key}'")
        val = data[key]
        if isinstance(val, bool) and expected_type is int:
            raise StructuredValidationError(f"Field '{key}' expected int, got bool")
        if not isinstance(val, expected_type):
            raise StructuredValidationError(
                f"Field '{key}' expected {expected_type}, got {type(val).__name__}"
            )
    return data


@dataclass
class ProviderSpec:
    name: str
    call: Callable[[str], tuple[str, int]]  # returns (raw_text, tokens_used)
    failure_threshold: int = 2
    cooldown_seconds: float = 30.0


class StructuredAgentRouter:
    """Routes structured prompts across prioritized providers with circuit breaking & token caps."""

    def __init__(
        self,
        providers: Sequence[ProviderSpec],
        *,
        max_total_tokens: int = 50_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not providers:
            raise ValueError("At least one ProviderSpec is required")
        self.providers = list(providers)
        self.max_total_tokens = max_total_tokens
        self.tokens_consumed = 0
        self._clock = clock
        self._failures: dict[str, int] = {p.name: 0 for p in providers}
        self._opened_until: dict[str, float] = {p.name: 0.0 for p in providers}

    def is_open(self, provider_name: str) -> bool:
        return self._clock() < self._opened_until.get(provider_name, 0.0)

    def invoke_structured(
        self,
        prompt: str,
        schema_types: Mapping[str, type | tuple[type, ...]],
        *,
        retries_per_provider: int = 2,
    ) -> tuple[dict[str, Any], str]:
        """Return `(validated_dict, winning_provider_name)` or raise `AllProvidersFailedError`."""
        if self.tokens_consumed >= self.max_total_tokens:
            raise BudgetExceededError(
                f"Token budget exhausted ({self.tokens_consumed}/{self.max_total_tokens})"
            )

        errors: list[str] = []
        for provider in self.providers:
            if self.is_open(provider.name):
                errors.append(f"{provider.name}:circuit_open")
                continue

            current_prompt = prompt
            for attempt in range(1, max(1, retries_per_provider) + 1):
                if self.tokens_consumed >= self.max_total_tokens:
                    raise BudgetExceededError(
                        f"Token budget exhausted ({self.tokens_consumed}/{self.max_total_tokens})"
                    )
                try:
                    raw_text, used_tokens = provider.call(current_prompt)
                    self.tokens_consumed += max(0, int(used_tokens))
                    parsed = extract_strict_json(raw_text)
                    validated = validate_required_fields(parsed, schema_types)
                    self._failures[provider.name] = 0
                    self._opened_until[provider.name] = 0.0
                    return validated, provider.name
                except StructuredValidationError as exc:
                    errors.append(f"{provider.name}[attempt {attempt}]:{exc}")
                    current_prompt = (
                        f"{prompt}\n\nREPAIR_HINT: Previous output failed schema validation ({exc}). "
                        "Respond ONLY with a valid JSON object."
                    )
                except Exception as exc:
                    errors.append(f"{provider.name}[attempt {attempt}]:{type(exc).__name__}:{exc}")
                    self._failures[provider.name] += 1
                    if self._failures[provider.name] >= provider.failure_threshold:
                        self._opened_until[provider.name] = self._clock() + provider.cooldown_seconds
                        break

        raise AllProvidersFailedError("; ".join(errors))
