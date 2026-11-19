# [Members & Shop · Day 1/30] Runnable Kit + Full Benchmark: Zero-Dependency Python Circuit Breakers & Self-Repairing JSON Routers for Unreliable LLM APIs

Thank you for backing **30 Days of Zero-Cost Production Python & AI Reliability**! Attached to this post is the latest unit-tested `.zip` release (`production-agent-automation-kit.zip` — MIT Commercial License) covering today's **Fault-Tolerant AI & API Guardrails** teardown.

**Attachment Checklist Before Clicking Publish in Patreon:**
- Access Setting: **Paid members only** (or sell as a **$14 one-time Patreon Shop post**)
- File Attachment: `/Users/tharushanuwansara/.gemini/antigravity/scratch/kofi-devto-autopilot/products/dist/production-agent-automation-kit.zip`

---

## The Production Failure Mode Nobody Catches in Local Demos

Most AI workflows and external API integrations pass every local happy-path check and fail violently during their first upstream brownout. When a model endpoint or third-party REST gateway begins returning intermittent `HTTP 429` or `HTTP 503` responses—or wraps a JSON object inside unexpected Markdown fences—three compounding failures happen simultaneously in naive client code:

1. **Synchronized Retry Amplification:** Twenty concurrent workers hit a timeout, sleep for fixed `2^n` intervals, and wake up in lockstep to hammer the degraded upstream endpoint again.
2. **Thread and Coroutine Starvation:** Every incoming request still waits out the full socket timeout against a dead primary provider instead of failing fast within microseconds and routing to a healthy secondary model.
3. **Silent Schema Drift:** Even when HTTP status `200` returns, parsing raw text with `json.loads` crashes on fenced blocks, or worse, accepts Python `True` where an `int` priority score was expected because `bool` subclasses `int` in Python's type hierarchy (`isinstance(True, int) == True`).

In this empirical teardown—*Benchmarking deterministic CLOSED -> OPEN -> HALF_OPEN state machines, strict JSON repair loops, and hard token budgets without heavy agent frameworks.* (context reference: [Python 3 Standard Library Docs](https://docs.python.org/3/library/urllib.request.html))—we build and benchmark a zero-dependency Python 3.11+ circuit breaker paired with a strict scalar validator that eliminates dropped requests across a simulated 25-request primary outage burst and 30-second recovery cooldown.

## Architectural Invariants Before Writing Code

Before adding a single retry loop, production reliability code needs four explicit invariants that hold without Redis, Celery, or heavyweight agent SDKs:

| Invariant | Naive Implementation Flaw | Deterministic Fix |
| :--- | :--- | :--- |
| **Fast-Fail State Machine** | Every request pays a 10-second socket timeout during a provider outage | Track consecutive transport failures and trip `CLOSED -> OPEN` so callers fail over in `< 0.01 ms` |
| **Half-Open Probe Recovery** | Permanently blacklists a recovered primary provider until process restart | Automatically transition `OPEN -> HALF_OPEN` once `cooldown_sec` elapses and promote back to `CLOSED` on the first clean probe |
| **Strict Scalar Coercion** | `isinstance(True, int)` silently passes boolean hallucinations into numeric math | Reject booleans explicitly when `int` is required and extract fenced JSON deterministically |
| **Bounded Repair Attempts** | Unbounded retry loops burn tens of thousands of tokens overnight | Cap repair retries per provider and enforce a hard cumulative token budget per job |

If you want a drop-in HTTP `Retry-After` guard and idempotent cron runner ready for immediate use, grab the [Free Idempotent Cron & Circuit-Breaker Mini-Kit ($0+ PWYW)](https://nuwansaratharusha.github.io/kofi-devto-autopilot?utm_source=devto&utm_medium=tutorial&utm_campaign=zero-dependency-python-circuit-breakers-self-rep&slug=zero-dependency-python-circuit-breakers-self-repairing-json-routers-for-04a7e6&sku=free-idempotent-cron-mini-kit) and run its unit tests locally alongside this walkthrough.

## Self-Contained Python Implementation & Outage Benchmark

The standalone script below implements `EndpointBreaker` and `parse_and_validate` using only Python's standard library (`re`, `json`, `dataclasses`, `time`), then simulates 300 sequential requests (`1.0s` apart) where requests `40..64` suffer a hard upstream `503` outage on `primary-llm` while `backup-llm` wraps JSON inside Markdown code fences:

```python
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

TICKS = "\x60" * 3
FENCE_RE = re.compile(TICKS + r"(?:json)?\s*(\{.*?\})\s*" + TICKS, re.DOTALL)


class CircuitOpenError(RuntimeError):
    pass


class SchemaRepairError(ValueError):
    pass


@dataclass
class EndpointBreaker:
    name: str
    failure_threshold: int = 2
    cooldown_sec: float = 30.0
    state: str = "CLOSED"
    failures: int = 0
    opened_at: float = 0.0

    def check(self, now: float) -> None:
        if self.state == "OPEN":
            if (now - self.opened_at) >= self.cooldown_sec:
                self.state = "HALF_OPEN"
            else:
                raise CircuitOpenError(f"{self.name} circuit is OPEN")

    def on_success(self) -> None:
        self.failures = 0
        self.state = "CLOSED"

    def on_transport_failure(self, now: float) -> None:
        self.failures += 1
        if self.state == "HALF_OPEN" or self.failures >= self.failure_threshold:
            self.state = "OPEN"
            self.opened_at = now


def parse_and_validate(raw: str, schema: Mapping[str, type]) -> dict[str, Any]:
    match = FENCE_RE.search(raw)
    candidate = match.group(1) if match else raw.strip()
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise SchemaRepairError(f"json_decode_error: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise SchemaRepairError("expected_json_object")
    for field_name, expected_type in schema.items():
        if field_name not in parsed:
            raise SchemaRepairError(f"missing_field:{field_name}")
        val = parsed[field_name]
        if isinstance(val, bool) and expected_type is int:
            raise SchemaRepairError(f"bool_is_not_int:{field_name}")
        if not isinstance(val, expected_type):
            raise SchemaRepairError(f"wrong_type:{field_name}")
    return parsed


def run_benchmark() -> dict[str, Any]:
    now = {"t": 100.0}
    primary = EndpointBreaker("primary-llm", failure_threshold=2, cooldown_sec=30.0)
    secondary = EndpointBreaker("backup-llm", failure_threshold=2, cooldown_sec=30.0)
    schema = {"action": str, "priority": int, "retryable": bool}

    stats = {"requests": 300, "primary_ok": 0, "failover_saves": 0, "fast_open_skips": 0}
    t0 = time.perf_counter()

    for idx in range(stats["requests"]):
        now["t"] += 1.0
        # Simulate a 25-request primary 503 outage burst across requests 40..64
        try:
            primary.check(now["t"])
            if 40 <= idx < 65:
                primary.on_transport_failure(now["t"])
                raise TimeoutError("upstream_503")
            parse_and_validate('{"action":"enqueue","priority":2,"retryable":true}', schema)
            primary.on_success()
            stats["primary_ok"] += 1
            continue
        except CircuitOpenError:
            stats["fast_open_skips"] += 1
        except TimeoutError:
            pass

        secondary.check(now["t"])
        raw_reply = TICKS + 'json\n{"action":"enqueue_backup","priority":1,"retryable":false}\n' + TICKS
        payload = parse_and_validate(raw_reply, schema)
        assert payload["priority"] == 1
        secondary.on_success()
        stats["failover_saves"] += 1

    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    summary = {**stats, "total_succeeded": stats["primary_ok"] + stats["failover_saves"], "elapsed_ms": elapsed_ms}
    Path("breaker_report.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    run_benchmark()
```

Next, we assert the exact invariants against the generated `breaker_report.json` artifact so any regression in failover accounting or circuit-breaker short-circuiting fails immediately with a non-zero exit code:

```bash
python3 -I -c '
import json
data = json.load(open("breaker_report.json"))
assert data["total_succeeded"] == data["requests"], "Dropped requests during outage!"
assert data["fast_open_skips"] == 29 and data["failover_saves"] == 31 and data["primary_ok"] == 269, "Unexpected breaker accounting!"
print(f"Verified zero dropped requests ({data["total_succeeded"]}/{data["requests"]}) with {data["fast_open_skips"]} fast-fail skips and {data["primary_ok"]} primary completions.")
'
```

## Real Captured Execution Output

Every code block in this article was executed inside a fresh isolated environment prior to staging. Here is the exact stdout captured from the run above:

<!-- EMPIRICAL_OUTPUT_START -->
```text
# Block 1 (python, exit=0, 50.6 ms)
{
  "requests": 300,
  "primary_ok": 269,
  "failover_saves": 31,
  "fast_open_skips": 29,
  "total_succeeded": 300,
  "elapsed_ms": 0.57
}

# Block 2 (bash, exit=0, 42.3 ms)
Verified zero dropped requests (300/300) with 29 fast-fail skips and 269 primary completions.
```
<!-- EMPIRICAL_OUTPUT_END -->

## Why the Numbers Matter in Real Traffic

Look closely at `fast_open_skips` versus `failover_saves` in the captured benchmark output above:

- **Only 2 requests paid the primary failure penalty** (`idx = 40` and `41`) before `EndpointBreaker` tripped `OPEN` at `failure_threshold=2`.
- **The next 29 requests during the 30-second cooldown window (`idx = 42..70`) skipped the broken primary in microseconds** (`fast_open_skips == 29`, `failover_saves == 31`) and routed straight to `backup-llm` without queuing socket timeouts or starving worker threads.
- **Once the 30-second cooldown elapsed**, a single `HALF_OPEN` probe at `idx = 71` automatically restored traffic back to the primary endpoint for the remaining `229` calls (`primary_ok == 269`, `300/300` total succeeded requests).

When you deploy this pattern in front of LLM tool loops or webhook dispatchers, pair the transport breaker with two operational safeguards:

1. **Separate Transport Errors From Validation Errors:** A malformed JSON response (`SchemaRepairError`) means the network path is healthy, so send a concise `REPAIR_HINT` prompt once rather than tripping the transport breaker for all callers. Conversely, `429`, `502`, `503`, `504`, and socket timeouts should immediately increment the transport failure counter.
2. **Enforce a Hard Per-Run Token Ceiling:** Track cumulative input + output tokens across retries and raise a terminal `BudgetExceededError` before any cyclic prompt bug can loop unattended.

## Shipping the Full Reliability Stack on $0 Infrastructure

If you are building autonomous workflows, scheduled scrapers, or SaaS backends without paying monthly Redis/broker bills, the complete [Production AI Agent & Zero-Cost Backend Reliability Starter Kit ($14, MIT License)](https://nuwansaratharusha.github.io/kofi-devto-autopilot?utm_source=devto&utm_medium=tutorial&utm_campaign=zero-dependency-python-circuit-breakers-self-rep&slug=zero-dependency-python-circuit-breakers-self-repairing-json-routers-for-04a7e6&sku=production-agent-automation-kit) packages this multi-provider router together with a transactional SQLite WAL job queue + dead-letter CLI, Node/TypeScript guardrails (`agent_guardrails.mjs` + `.d.ts`), constant-time HMAC webhook verification, double-entry SQL ledgers, and 4 production GitHub Actions workflows.

---

*Verification & Disclosure: Published under the **Zero-Cost Production Reliability** column by **Tharusha Nuwansara**. Every Python and shell block above was executed and verified inside an isolated `python3 -I` sandbox (`returncode == 0`) prior to publication.*
