#!/usr/bin/env python3
"""Unit tests for the Free Idempotent Cron & API Circuit-Breaker Mini-Kit."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from api_guard import CircuitBreaker, CircuitOpenError, GuardedHttpClient, HttpResponseData
from idempotent_cron import IdempotentCronRunner, compute_window_key


class TestIdempotentCronRunner(unittest.TestCase):
    def test_duplicate_trigger_executes_side_effect_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = IdempotentCronRunner(Path(tmp) / "cron.db", lease_seconds=120.0)
            key = compute_window_key("daily-digest", cadence="daily", now_epoch=1760000000.0)
            calls = []

            def job() -> dict[str, int]:
                calls.append(1)
                return {"sent": 10}

            first = runner.run_once("daily-digest", key, job)
            second = runner.run_once("daily-digest", key, job)

            self.assertEqual(first.status, "completed")
            self.assertEqual(second.status, "skipped_already_completed")
            self.assertEqual(len(calls), 1)

    def test_active_lease_blocks_overlapping_worker_then_recovers_when_stale(self) -> None:
        now = {"t": 1000.0}
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "lease.db"
            worker_a = IdempotentCronRunner(
                db_path, lease_seconds=30.0, clock=lambda: now["t"], worker_id="worker-a"
            )
            worker_b = IdempotentCronRunner(
                db_path, lease_seconds=30.0, clock=lambda: now["t"], worker_id="worker-b"
            )

            # Simulate worker-a crashing mid-run by inserting an active lease directly
            with worker_a._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO cron_executions (
                        execution_key, job_name, status, worker_id,
                        attempt_count, started_at, lease_expires_at
                    ) VALUES ('job:1', 'sync', 'running', 'worker-a', 1, 1000.0, 1030.0)
                    """
                )

            now["t"] = 1015.0  # Lease still active
            blocked = worker_b.run_once("sync", "job:1", lambda: "recovered")
            self.assertEqual(blocked.status, "skipped_active_lease")

            now["t"] = 1035.0  # Lease expired -> worker-b takes over cleanly
            recovered = worker_b.run_once("sync", "job:1", lambda: "recovered")
            self.assertEqual(recovered.status, "completed")
            self.assertEqual(recovered.attempt_count, 2)

    def test_failed_run_records_error_and_allows_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = IdempotentCronRunner(Path(tmp) / "retry.db")
            state = {"attempt": 0}

            def flaky() -> str:
                state["attempt"] += 1
                if state["attempt"] == 1:
                    raise RuntimeError("upstream timeout")
                return "ok"

            with self.assertRaises(RuntimeError):
                runner.run_once("flaky-job", "flaky:1", flaky)

            second = runner.run_once("flaky-job", "flaky:1", flaky)
            self.assertEqual(second.status, "completed")
            self.assertEqual(second.attempt_count, 2)


class TestGuardedHttpClient(unittest.TestCase):
    def test_circuit_breaker_transitions_closed_open_half_open_closed(self) -> None:
        clock = {"t": 0.0}
        breaker = CircuitBreaker(
            failure_threshold=2, recovery_timeout_seconds=10.0, clock=lambda: clock["t"]
        )
        self.assertEqual(breaker.state, "CLOSED")

        breaker.record_failure()
        self.assertEqual(breaker.state, "CLOSED")
        breaker.record_failure()
        self.assertEqual(breaker.state, "OPEN")

        with self.assertRaises(CircuitOpenError):
            breaker.before_call()

        clock["t"] = 11.0
        self.assertEqual(breaker.state, "HALF_OPEN")
        breaker.record_success()
        self.assertEqual(breaker.state, "CLOSED")

    def test_http_client_honors_retry_after_and_succeeds(self) -> None:
        responses = [
            HttpResponseData(status=429, headers={"retry-after": "1.25"}, body=b"slow down"),
            HttpResponseData(status=503, headers={}, body=b"unavailable"),
            HttpResponseData(status=200, headers={}, body=b'{"healthy":true}'),
        ]
        sleeps: list[float] = []
        client = GuardedHttpClient(
            breaker=CircuitBreaker(failure_threshold=5),
            max_attempts=3,
            sleeper=sleeps.append,
            rng=lambda: 0.5,
            transport=lambda _req, _timeout: responses.pop(0),
        )
        res = client.request("GET", "https://api.example.invalid/ping")
        self.assertEqual(res.status, 200)
        self.assertEqual(res.json(), {"healthy": True})
        self.assertEqual(sleeps[0], 1.25)
        self.assertEqual(len(sleeps), 2)

    def test_cli_demos_execute_cleanly(self) -> None:
        here = Path(__file__).resolve().parent
        for script in ("idempotent_cron.py", "api_guard.py"):
            proc = subprocess.run(
                [sys.executable, "-I", str(here / script), "--demo"],
                cwd=str(here),
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
            self.assertIn("{", proc.stdout)


if __name__ == "__main__":
    unittest.main()
