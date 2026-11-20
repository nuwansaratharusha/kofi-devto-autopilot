#!/usr/bin/env python3
"""Day 1 Lab: Retry-After Header Parsing, Full-Jitter Backoff & Retry Budget Guard."""

from __future__ import annotations

import argparse
import json
import math
import random
import time
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Callable, Mapping, Optional, TypeVar

T = TypeVar("T")


class RetryExhaustedError(RuntimeError):
    """Raised when transient failures exceed max_attempts or retry budget."""


@dataclass(frozen=True)
class RetryOutcome:
    value: object
    attempts: int
    total_sleep_seconds: float


def parse_retry_after_seconds(header_value: Optional[str], *, now_epoch: Optional[float] = None) -> Optional[float]:
    """Parse HTTP `Retry-After` as non-negative delta-seconds or RFC-7231 HTTP-date."""
    if header_value is None:
        return None
    raw = header_value.strip()
    if not raw:
        return None
    try:
        sec = float(raw)
        if not math.isfinite(sec):
            return None
        return max(0.0, sec)
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        ref = time.time() if now_epoch is None else now_epoch
        return max(0.0, dt.timestamp() - ref)
    except (TypeError, ValueError, OverflowError):
        return None


def compute_full_jitter_delay(
    attempt: int,
    *,
    base_seconds: float = 0.25,
    max_seconds: float = 8.0,
    retry_after_header: Optional[str] = None,
    rng: Optional[random.Random] = None,
    now_epoch: Optional[float] = None,
) -> float:
    """Compute desynchronized exponential backoff respecting upstream `Retry-After`."""
    if attempt < 1:
        raise ValueError("attempt must be >= 1")
    if base_seconds <= 0 or max_seconds <= 0:
        raise ValueError("base_seconds and max_seconds must be > 0")

    gen = rng or random.Random()
    parsed_ra = parse_retry_after_seconds(retry_after_header, now_epoch=now_epoch)
    if parsed_ra is not None:
        capped = min(max_seconds, parsed_ra)
        jitter = gen.uniform(0.01, min(0.25, max_seconds * 0.1))
        return round(min(max_seconds, capped + jitter), 4)

    ceiling = min(max_seconds, base_seconds * (2 ** (attempt - 1)))
    return round(gen.uniform(0.1 * ceiling, ceiling), 4)


class RetryableHttpStatusError(RuntimeError):
    """Represents a transient HTTP status (e.g., 429, 502, 503, 504) with headers."""

    def __init__(self, status: int, message: str = "", headers: Optional[Mapping[str, str]] = None) -> None:
        super().__init__(message or f"HTTP {status}")
        self.status = int(status)
        self.headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}


def execute_with_retry(
    operation: Callable[[], T],
    *,
    max_attempts: int = 4,
    base_seconds: float = 0.25,
    max_seconds: float = 8.0,
    max_total_sleep_seconds: float = 15.0,
    sleeper: Callable[[float], None] = time.sleep,
    rng: Optional[random.Random] = None,
) -> tuple[T, RetryOutcome]:
    """Run `operation` with bounded full-jitter retries on `RetryableHttpStatusError` or `TimeoutError`."""
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    gen = rng or random.Random()
    slept_total = 0.0
    last_exc: Optional[BaseException] = None

    for attempt in range(1, max_attempts + 1):
        try:
            result = operation()
            return result, RetryOutcome(
                value=result,
                attempts=attempt,
                total_sleep_seconds=round(slept_total, 4),
            )
        except (RetryableHttpStatusError, TimeoutError, ConnectionError) as exc:
            last_exc = exc
            if isinstance(exc, RetryableHttpStatusError) and exc.status not in {408, 429, 500, 502, 503, 504}:
                raise
            if attempt >= max_attempts:
                break
            ra_hdr = exc.headers.get("retry-after") if isinstance(exc, RetryableHttpStatusError) else None
            delay = compute_full_jitter_delay(
                attempt,
                base_seconds=base_seconds,
                max_seconds=max_seconds,
                retry_after_header=ra_hdr,
                rng=gen,
            )
            if slept_total + delay > max_total_sleep_seconds:
                break
            sleeper(delay)
            slept_total += delay

    raise RetryExhaustedError(f"Operation failed after {max_attempts} attempts: {last_exc}") from last_exc


def run_demo() -> dict[str, object]:
    rng = random.Random(20261120)
    calls = {"n": 0}
    recorded_sleeps: list[float] = []

    def flaky_upstream() -> dict[str, object]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RetryableHttpStatusError(429, "rate_limited", {"Retry-After": "0.5"})
        if calls["n"] == 2:
            raise RetryableHttpStatusError(503, "gateway_busy")
        return {"ok": True, "call_number": calls["n"]}

    payload, meta = execute_with_retry(
        flaky_upstream,
        max_attempts=4,
        sleeper=recorded_sleeps.append,
        rng=rng,
    )
    return {
        "day": 1,
        "payload": payload,
        "attempts": meta.attempts,
        "recorded_sleeps": recorded_sleeps,
        "total_sleep_seconds": meta.total_sleep_seconds,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Day 1 Retry-After & Full-Jitter Lab")
    parser.add_argument("--demo", action="store_true", help="Run deterministic retry simulation")
    args = parser.parse_args()
    print(json.dumps(run_demo(), indent=2))
