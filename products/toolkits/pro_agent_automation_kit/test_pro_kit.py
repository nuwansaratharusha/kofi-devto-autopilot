#!/usr/bin/env python3
"""Unit tests for the Flagship Production AI Agent & Zero-Cost Backend Starter Kit."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from llm_circuit_breaker import (
    AllProvidersFailedError,
    BudgetExceededError,
    ProviderSpec,
    StructuredAgentRouter,
)
from wal_queue import SqliteWalQueue
from webhook_hmac import WebhookReplayGuard, WebhookVerificationError, sign_webhook_payload


class TestWalQueue(unittest.TestCase):
    def test_idempotent_enqueue_claim_ack_and_dlq_replay(self) -> None:
        now = {"t": 1000.0}
        with tempfile.TemporaryDirectory() as tmp:
            q = SqliteWalQueue(Path(tmp) / "q.db", clock=lambda: now["t"])
            j1 = q.enqueue("emails", {"to": "dev@example.org"}, idempotency_key="msg-1", max_attempts=2)
            j2 = q.enqueue("emails", {"to": "dev@example.org"}, idempotency_key="msg-1", max_attempts=2)
            self.assertEqual(j1.job_id, j2.job_id)

            c1 = q.claim("emails", worker_id="w1", visibility_timeout=10.0)
            self.assertIsNotNone(c1)
            assert c1 is not None
            self.assertEqual(c1.attempts, 1)

            # First failure -> queued with backoff
            q.nack(c1.job_id, error="smtp_timeout", base_backoff=5.0)
            self.assertIsNone(q.claim("emails", worker_id="w1"))  # backoff active

            now["t"] = 1006.0
            c2 = q.claim("emails", worker_id="w1", visibility_timeout=10.0)
            self.assertIsNotNone(c2)
            assert c2 is not None
            self.assertEqual(c2.attempts, 2)

            # Second failure reaches max_attempts=2 -> moved to dead_letter
            dlq_job = q.nack(c2.job_id, error="smtp_timeout_2")
            self.assertEqual(dlq_job.status, "dead_letter")
            self.assertEqual(q.stats()["dead_letter"], 1)

            # Replay DLQ and complete cleanly
            replayed = q.requeue_dead_letters("emails")
            self.assertEqual(replayed, 1)
            c3 = q.claim("emails", worker_id="w2")
            assert c3 is not None
            self.assertTrue(q.ack(c3.job_id, worker_id="w2"))
            self.assertEqual(q.stats()["completed"], 1)

    def test_wal_queue_cli_demo(self) -> None:
        here = Path(__file__).resolve().parent
        proc = subprocess.run(
            [sys.executable, "-I", str(here / "wal_queue.py"), "--demo"],
            cwd=str(here),
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        self.assertIn('"completed": 1', proc.stdout)


class TestStructuredAgentRouter(unittest.TestCase):
    def test_self_repairs_malformed_json_and_fails_over_on_outage(self) -> None:
        calls = {"primary": 0, "backup": 0}

        def flaky_primary(_prompt: str) -> tuple[str, int]:
            calls["primary"] += 1
            raise RuntimeError("503 upstream overloaded")

        def fenced_backup(prompt: str) -> tuple[str, int]:
            calls["backup"] += 1
            if calls["backup"] == 1:
                return ("```json\n{\"summary\": \"missing score\"}\n```", 120)
            self.assertIn("REPAIR_HINT", prompt)
            return ("```json\n{\"summary\": \"verified\", \"score\": 98}\n```", 140)

        router = StructuredAgentRouter(
            [
                ProviderSpec("primary-llm", flaky_primary, failure_threshold=1, cooldown_seconds=60.0),
                ProviderSpec("backup-llm", fenced_backup, failure_threshold=2, cooldown_seconds=60.0),
            ],
            max_total_tokens=2000,
        )
        data, winner = router.invoke_structured(
            "Analyze deployment risk",
            {"summary": str, "score": int},
            retries_per_provider=2,
        )
        self.assertEqual(winner, "backup-llm")
        self.assertEqual(data, {"summary": "verified", "score": 98})
        self.assertTrue(router.is_open("primary-llm"))
        self.assertEqual(router.tokens_consumed, 260)

    def test_budget_cap_halts_runaway_loop(self) -> None:
        router = StructuredAgentRouter(
            [ProviderSpec("cheap", lambda _p: ('{"ok": true}', 500))],
            max_total_tokens=400,
        )
        router.tokens_consumed = 400
        with self.assertRaises(BudgetExceededError):
            router.invoke_structured("ping", {"ok": bool})

    def test_all_providers_failed_raises_summary(self) -> None:
        router = StructuredAgentRouter(
            [ProviderSpec("broken", lambda _p: ("not json", 10))],
            max_total_tokens=1000,
        )
        with self.assertRaises(AllProvidersFailedError):
            router.invoke_structured("ping", {"ok": bool}, retries_per_provider=1)


class TestWebhookReplayGuard(unittest.TestCase):
    def test_constant_time_hmac_accepts_valid_and_blocks_replay_or_tamper(self) -> None:
        now = {"t": 1760000000.0}
        secret = "whsec_live_test_secret_key"
        body = b'{"event":"shop.order","amount_usd":14}'

        with tempfile.TemporaryDirectory() as tmp:
            guard = WebhookReplayGuard(
                Path(tmp) / "webhooks.db", tolerance_seconds=300, clock=lambda: now["t"]
            )
            header = sign_webhook_payload(secret, body, timestamp=1760000000, nonce="evt_1001")
            res = guard.verify_and_record(secret, header, body)
            self.assertTrue(res["verified"])

            # Exact replay rejected
            with self.assertRaises(WebhookVerificationError):
                guard.verify_and_record(secret, header, body)

            # Tampered body rejected
            header2 = sign_webhook_payload(secret, body, timestamp=1760000005, nonce="evt_1002")
            with self.assertRaises(WebhookVerificationError):
                guard.verify_and_record(secret, header2, b'{"event":"shop.order","amount_usd":0}')

            # Stale timestamp rejected
            stale_header = sign_webhook_payload(secret, body, timestamp=1759999000, nonce="evt_1003")
            with self.assertRaises(WebhookVerificationError):
                guard.verify_and_record(secret, stale_header, body)


if __name__ == "__main__":
    unittest.main()
