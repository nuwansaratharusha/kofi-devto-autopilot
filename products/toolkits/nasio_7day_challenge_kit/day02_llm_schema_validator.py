#!/usr/bin/env python3
"""Day 2 Lab: Strict LLM Structured-JSON Extractor, Bool-vs-Int Guard & Repair Loop."""

from __future__ import annotations

import argparse
import json
import re
from typing import Any, Callable, Mapping

FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)


class StructuredOutputError(ValueError):
    """Raised when an LLM/tool response violates the target JSON contract."""


def extract_json_object(raw_text: str) -> dict[str, Any]:
    """Extract a JSON object from raw text or Markdown code fences."""
    if not raw_text or not raw_text.strip():
        raise StructuredOutputError("empty_response")

    fenced = FENCED_JSON_RE.search(raw_text)
    candidate = fenced.group(1).strip() if fenced else raw_text.strip()

    if not candidate.startswith("{"):
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start != -1 and end > start:
            candidate = candidate[start : end + 1]

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise StructuredOutputError(f"invalid_json:{exc.msg}") from exc

    if not isinstance(parsed, dict):
        raise StructuredOutputError("expected_top_level_object")
    return parsed


def validate_contract(payload: Mapping[str, Any], schema: Mapping[str, type]) -> dict[str, Any]:
    """Validate required keys and strict Python types (preventing `isinstance(True, int)` bugs)."""
    validated: dict[str, Any] = {}
    for key, expected_type in schema.items():
        if key not in payload:
            raise StructuredOutputError(f"missing_field:{key}")
        val = payload[key]
        if val is None:
            raise StructuredOutputError(f"null_field:{key}")
        if isinstance(val, bool) and expected_type in (int, float):
            raise StructuredOutputError(f"bool_rejected_for_numeric:{key}")
        if expected_type is float and isinstance(val, int) and not isinstance(val, bool):
            validated[key] = float(val)
            continue
        if not isinstance(val, expected_type):
            raise StructuredOutputError(
                f"type_mismatch:{key}:expected_{expected_type.__name__}_got_{type(val).__name__}"
            )
        validated[key] = val
    return validated


def invoke_with_schema_repair(
    prompt: str,
    schema: Mapping[str, type],
    model_fn: Callable[[str], str],
    *,
    max_repairs: int = 2,
) -> tuple[dict[str, Any], int]:
    """Invoke `model_fn` and feed validation diagnostics back up to `max_repairs` times."""
    if max_repairs < 0:
        raise ValueError("max_repairs must be >= 0")

    current_prompt = prompt
    last_err: StructuredOutputError | None = None
    for attempt in range(1, max_repairs + 2):
        raw = model_fn(current_prompt)
        try:
            obj = extract_json_object(raw)
            clean = validate_contract(obj, schema)
            return clean, attempt
        except StructuredOutputError as err:
            last_err = err
            current_prompt = (
                f"{prompt}\n\n[SCHEMA_REPAIR_REQUIRED] Previous output failed with '{err}'. "
                f"Return ONLY a valid JSON object matching keys: {list(schema.keys())}."
            )

    raise StructuredOutputError(f"exhausted_repairs:{last_err}")


def run_demo() -> dict[str, Any]:
    schema = {"action": str, "priority": int, "confidence": float, "idempotent": bool}
    state = {"turn": 0}

    def simulated_llm(prompt: str) -> str:
        state["turn"] += 1
        if state["turn"] == 1:
            # Classic LLM bug: wraps in prose + passes boolean `true` for integer `priority`
            return 'Sure! Here is the JSON:\n```json\n{"action":"dispatch","priority":true,"confidence":0.94,"idempotent":true}\n```'
        assert "bool_rejected_for_numeric:priority" in prompt
        return '{"action":"dispatch","priority":2,"confidence":0.94,"idempotent":true}'

    validated, attempts = invoke_with_schema_repair("Classify order webhook", schema, simulated_llm)
    return {"day": 2, "validated": validated, "attempts_used": attempts}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Day 2 LLM Schema Validator & Repair Lab")
    parser.add_argument("--demo", action="store_true", help="Run deterministic schema repair demo")
    parser.parse_args()
    print(json.dumps(run_demo(), indent=2))
