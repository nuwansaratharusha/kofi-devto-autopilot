#!/usr/bin/env python3
"""Day 6 Lab: Multi-Provider AI Router With Hard Token & Cost Ceilings + Circuit Breaker."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Any, Callable


class BudgetCapExceededError(RuntimeError):
    """Raised when cumulative token usage or USD cent spend reaches the configured ceiling."""


@dataclass
class ProviderRoute:
    name: str
    cost_per_1k_tokens_cents: float
    invoke: Callable[[str], tuple[str, int]]
    failure_threshold: int = 2
    cooldown_seconds: float = 30.0
    consecutive_failures: int = 0
    opened_until: float = 0.0

    def is_available(self, now: float) -> bool:
        return now >= self.opened_until

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.opened_until = 0.0

    def record_failure(self, now: float) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.failure_threshold:
            self.opened_until = now + self.cooldown_seconds


class BudgetedModelRouter:
    """Routes prompts across primary/fallback providers while enforcing hard token & cent budgets."""

    def __init__(
        self,
        routes: list[ProviderRoute],
        *,
        max_tokens: int = 8_000,
        max_cost_cents: float = 10.0,
    ) -> None:
        if not routes:
            raise ValueError("At least one ProviderRoute is required")
        if max_tokens <= 0 or max_cost_cents <= 0:
            raise ValueError("max_tokens and max_cost_cents must be > 0")
        self.routes = list(routes)
        self.max_tokens = int(max_tokens)
        self.max_cost_cents = float(max_cost_cents)
        self.tokens_used = 0
        self.cost_cents_used = 0.0

    def complete(self, prompt: str, *, now: float = 0.0) -> dict[str, Any]:
        if self.tokens_used >= self.max_tokens or self.cost_cents_used >= self.max_cost_cents:
            raise BudgetCapExceededError(
                f"budget_exceeded:tokens={self.tokens_used}/{self.max_tokens},cents={self.cost_cents_used:.4f}/{self.max_cost_cents:.4f}"
            )

        errors: list[str] = []
        for route in self.routes:
            if not route.is_available(now):
                errors.append(f"{route.name}:circuit_open")
                continue
            try:
                text, tokens = route.invoke(prompt)
            except Exception as exc:
                route.record_failure(now)
                errors.append(f"{route.name}:{exc}")
                continue

            tok_n = max(0, int(tokens))
            step_cents = (tok_n / 1000.0) * route.cost_per_1k_tokens_cents
            self.tokens_used += tok_n
            self.cost_cents_used = round(self.cost_cents_used + step_cents, 6)
            route.record_success()
            return {
                "provider": route.name,
                "text": text,
                "tokens": tok_n,
                "total_tokens_used": self.tokens_used,
                "total_cost_cents": round(self.cost_cents_used, 4),
            }

        raise RuntimeError(f"All provider routes failed: {', '.join(errors)}")


def run_demo() -> dict[str, Any]:
    calls = {"n": 0}

    def primary_llm(_p: str) -> tuple[str, int]:
        calls["n"] += 1
        if calls["n"] in {2, 3}:
            raise TimeoutError("upstream_503")
        return '{"category":"challenge_order","priority":1}', 400

    def backup_llm(_p: str) -> tuple[str, int]:
        return '{"category":"challenge_order","priority":2}', 250

    router = BudgetedModelRouter(
        [
            ProviderRoute("primary-frontier", cost_per_1k_tokens_cents=1.2, invoke=primary_llm, failure_threshold=2),
            ProviderRoute("backup-flash", cost_per_1k_tokens_cents=0.2, invoke=backup_llm, failure_threshold=2),
        ],
        max_tokens=1_500,
        max_cost_cents=5.0,
    )
    winners: list[str] = []
    halted = False
    for step in range(10):
        try:
            res = router.complete("classify", now=100.0 + step)
            winners.append(str(res["provider"]))
        except BudgetCapExceededError:
            halted = True
            break

    return {
        "day": 6,
        "providers_selected": winners,
        "tokens_used": router.tokens_used,
        "cost_cents_used": round(router.cost_cents_used, 4),
        "halted_at_budget_cap": halted,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Day 6 Token-Budget Multi-Provider Router Lab")
    parser.add_argument("--demo", action="store_true", help="Run budgeted failover router simulation")
    parser.parse_args()
    print(json.dumps(run_demo(), indent=2))
