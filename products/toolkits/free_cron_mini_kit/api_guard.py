#!/usr/bin/env python3
"""Zero-dependency HTTP retry & sliding-window circuit breaker for Python 3.11+."""

from __future__ import annotations

import argparse
import json
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional


class CircuitOpenError(RuntimeError):
    """Raised immediately when the circuit breaker is OPEN and cooldown has not elapsed."""


@dataclass(frozen=True)
class HttpResponseData:
    status: int
    headers: Mapping[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


class CircuitBreaker:
    """Deterministic `CLOSED -> OPEN -> HALF_OPEN` state machine."""

    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        recovery_timeout_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = float(recovery_timeout_seconds)
        self._clock = clock
        self._state = "CLOSED"
        self._consecutive_failures = 0
        self._opened_at: Optional[float] = None

    @property
    def state(self) -> str:
        if self._state == "OPEN" and self._opened_at is not None:
            if (self._clock() - self._opened_at) >= self.recovery_timeout_seconds:
                self._state = "HALF_OPEN"
        return self._state

    def before_call(self) -> str:
        current = self.state
        if current == "OPEN":
            remaining = 0.0
            if self._opened_at is not None:
                remaining = max(0.0, self.recovery_timeout_seconds - (self._clock() - self._opened_at))
            raise CircuitOpenError(f"Circuit breaker is OPEN (retry after {remaining:.2f}s)")
        return current

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._state = "CLOSED"
        self._opened_at = None

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._state == "HALF_OPEN" or self._consecutive_failures >= self.failure_threshold:
            self._state = "OPEN"
            self._opened_at = self._clock()


def compute_backoff_seconds(
    attempt: int,
    *,
    base_delay: float = 0.25,
    max_delay: float = 8.0,
    retry_after_header: Optional[str] = None,
    rng: Callable[[], float] = random.random,
) -> float:
    """Full-jitter exponential backoff honoring numeric `Retry-After` headers."""
    if retry_after_header:
        try:
            parsed = float(retry_after_header.strip())
            if parsed >= 0:
                return min(parsed, max_delay)
        except ValueError:
            pass
    ceiling = min(max_delay, base_delay * (2 ** max(0, attempt - 1)))
    return round(ceiling * max(0.1, rng()), 4)


class GuardedHttpClient:
    """Executes HTTP calls with bounded retries, `Retry-After` awareness, and circuit breaking."""

    RETRYABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})

    def __init__(
        self,
        *,
        breaker: Optional[CircuitBreaker] = None,
        max_attempts: int = 3,
        timeout_seconds: float = 8.0,
        base_delay: float = 0.25,
        max_delay: float = 5.0,
        sleeper: Callable[[float], None] = time.sleep,
        rng: Callable[[], float] = random.random,
        transport: Optional[Callable[[urllib.request.Request, float], HttpResponseData]] = None,
    ) -> None:
        self.breaker = breaker or CircuitBreaker()
        self.max_attempts = max(1, max_attempts)
        self.timeout_seconds = timeout_seconds
        self.base_delay = base_delay
        self.max_delay = max_delay
        self._sleep = sleeper
        self._rng = rng
        self._transport = transport or self._urllib_transport

    @staticmethod
    def _urllib_transport(req: urllib.request.Request, timeout: float) -> HttpResponseData:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                headers = {k.lower(): v for k, v in resp.headers.items()}
                return HttpResponseData(status=int(resp.status), headers=headers, body=resp.read())
        except urllib.error.HTTPError as err:
            headers = {k.lower(): v for k, v in (err.headers.items() if err.headers else [])}
            body = err.read() if err.fp else b""
            return HttpResponseData(status=int(err.code), headers=headers, body=body)

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Mapping[str, str]] = None,
        json_body: Optional[ Any] = None,
    ) -> HttpResponseData:
        payload = json.dumps(json_body).encode("utf-8") if json_body is not None else None
        req_headers = {"User-Agent": "FreeCronMiniKit/1.0"}
        if json_body is not None:
            req_headers["Content-Type"] = "application/json"
        if headers:
            req_headers.update(headers)

        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_attempts + 1):
            self.breaker.before_call()
            req = urllib.request.Request(url=url, data=payload, headers=req_headers, method=method.upper())
            try:
                resp = self._transport(req, self.timeout_seconds)
            except (TimeoutError, urllib.error.URLError, OSError) as exc:
                last_exc = exc
                self.breaker.record_failure()
                if attempt >= self.max_attempts or self.breaker.state == "OPEN":
                    raise
                delay = compute_backoff_seconds(
                    attempt, base_delay=self.base_delay, max_delay=self.max_delay, rng=self._rng
                )
                self._sleep(delay)
                continue

            if resp.status in self.RETRYABLE_STATUSES:
                self.breaker.record_failure()
                if attempt >= self.max_attempts or self.breaker.state == "OPEN":
                    return resp
                retry_after = resp.headers.get("retry-after")
                delay = compute_backoff_seconds(
                    attempt,
                    base_delay=self.base_delay,
                    max_delay=self.max_delay,
                    retry_after_header=retry_after,
                    rng=self._rng,
                )
                self._sleep(delay)
                continue

            if 200 <= resp.status < 400:
                self.breaker.record_success()
            return resp

        if last_exc is not None:
            raise last_exc
        raise RuntimeError("Unreachable retry exhaustion state")


def _run_demo() -> int:
    simulated = [
        HttpResponseData(status=429, headers={"retry-after": "0.05"}, body=b'{"error":"rate_limited"}'),
        HttpResponseData(status=200, headers={}, body=b'{"ok":true,"items":3}'),
    ]
    slept: list[float] = []

    def fake_transport(_req: urllib.request.Request, _timeout: float) -> HttpResponseData:
        return simulated.pop(0)

    client = GuardedHttpClient(
        max_attempts=3,
        sleeper=lambda s: slept.append(s),
        rng=lambda: 0.5,
        transport=fake_transport,
    )
    resp = client.request("GET", "https://api.internal.invalid/v1/jobs")
    print(
        json.dumps(
            {
                "final_status": resp.status,
                "payload": resp.json(),
                "backoff_sleeps_sec": slept,
                "breaker_state": client.breaker.state,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Guarded HTTP retry & circuit breaker.")
    parser.add_argument("--demo", action="store_true", help="Run deterministic retry/breaker demo.")
    parsed_args = parser.parse_args()
    if parsed_args.demo:
        raise SystemExit(_run_demo())
    parser.print_help()
