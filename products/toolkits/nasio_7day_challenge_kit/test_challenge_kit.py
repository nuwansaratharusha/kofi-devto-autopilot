"""Comprehensive deterministic unit tests for the 7-Day AI & Python Reliability Challenge Kit."""

from __future__ import annotations

import json
import random
import sys
import tempfile
import unittest
from pathlib import Path

_KIT_DIR = str(Path(__file__).resolve().parent)
if _KIT_DIR not in sys.path:
    sys.path.insert(0, _KIT_DIR)

from day01_retry_jitter import (
    RetryExhaustedError,
    RetryableHttpStatusError,
    compute_full_jitter_delay,
    execute_with_retry,
    parse_retry_after_seconds,
)
from day02_llm_schema_validator import (
    StructuredOutputError,
    extract_json_object,
    invoke_with_schema_repair,
    validate_contract,
)
from day03_sqlite_wal_queue import SqliteWalQueue
from day04_idempotent_cron_lease import CronLeaseGuard, compute_cron_window_key
from day05_hmac_webhook_guard import (
    ReplayProtectedWebhookGuard,
    WebhookVerificationError,
    sign_webhook_payload,
)
from day06_token_budget_router import (
    BudgetCapExceededError,
    BudgetedModelRouter,
    ProviderRoute,
)
from day07_end_to_end_agent_pipeline import EndToEndReliabilityPipeline, run_demo as run_capstone_demo


class TestNasio7DayChallengeKit(unittest.TestCase):
    def test_day01_retry_after_and_non_retryable_4xx_fast_fail(self) -> None:
        self.assertEqual(parse_retry_after_seconds("2.5"), 2.5)
        self.assertEqual(parse_retry_after_seconds("-4"), 0.0)
        self.assertIsNone(parse_retry_after_seconds("not-a-date"))
        self.assertIsNone(parse_retry_after_seconds("inf"))
        self.assertIsNone(parse_retry_after_seconds("NaN"))
        self.assertEqual(
            parse_retry_after_seconds("Wed, 21 Oct 2026 07:28:10", now_epoch=1792567680.0),
            10.0,
        )

        rng = random.Random(42)
        d = compute_full_jitter_delay(2, base_seconds=0.5, max_seconds=4.0, retry_after_header="1.0", rng=rng)
        self.assertGreaterEqual(d, 1.01)
        self.assertLessEqual(d, 1.25)

        # HTTP 400 must fail immediately without sleeping or retrying
        calls = {"n": 0}

        def bad_request() -> str:
            calls["n"] += 1
            raise RetryableHttpStatusError(400, "bad_input")

        with self.assertRaises(RetryableHttpStatusError):
            execute_with_retry(bad_request, max_attempts=4, sleeper=lambda _s: None)
        self.assertEqual(calls["n"], 1)

        # Exhausting max_attempts raises RetryExhaustedError
        with self.assertRaises(RetryExhaustedError):
            execute_with_retry(
                lambda: (_ for _ in ()).throw(TimeoutError("stalled")),
                max_attempts=2,
                sleeper=lambda _s: None,
            )

    def test_day02_schema_validator_catches_bool_as_int_and_repairs(self) -> None:
        schema = {"route": str, "workers": int, "ratio": float}
        obj = extract_json_object('Prefix text ```json\n{"route":"fast","workers":4,"ratio":1}\n``` trailing')
        clean = validate_contract(obj, schema)
        self.assertEqual(clean, {"route": "fast", "workers": 4, "ratio": 1.0})

        with self.assertRaises(StructuredOutputError):
            validate_contract({"route": "fast", "workers": True, "ratio": 0.5}, schema)

        replies = ['{"route":"fast","workers":"wrong","ratio":0.5}', '{"route":"fast","workers":3,"ratio":0.5}']
        res, used = invoke_with_schema_repair("prompt", schema, lambda _p: replies.pop(0), max_repairs=2)
        self.assertEqual(used, 2)
        self.assertEqual(res["workers"], 3)

    def test_day03_sqlite_wal_queue_dedup_stale_lease_and_dlq(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            q = SqliteWalQueue(Path(tmp) / "q.db")
            t0 = 1_700_000_000.0
            self.assertTrue(q.enqueue("job_a", idempotency_key="k1", payload={"x": 1}, max_attempts=2, now=t0))
            self.assertFalse(q.enqueue("job_a2", idempotency_key="k1", payload={"x": 1}, max_attempts=2, now=t0))

            # Claim once and let lease expire without ack -> reclaimed on 2nd claim -> expires into dead_letter
            c1 = q.claim(lease_seconds=10.0, now=t0)
            self.assertIsNotNone(c1)
            self.assertIsNone(q.claim(lease_seconds=10.0, now=t0 + 5.0))
            c2 = q.claim(lease_seconds=10.0, now=t0 + 11.0)
            assert c2 is not None
            self.assertEqual(c2.attempts, 2)
            self.assertIsNone(q.claim(lease_seconds=10.0, now=t0 + 25.0))
            self.assertEqual(q.counts()["dead_letter"], 1)

    def test_day04_cron_lease_prevents_double_execution_and_recovers_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            guard = CronLeaseGuard(Path(tmp) / "c.db", lease_ttl_seconds=30.0)
            wkey = compute_cron_window_key("daily-payout-check", cadence="daily", epoch=1_700_000_000.0)
            self.assertEqual(guard.acquire(wkey, worker_id="w1", now=100.0), "acquired_fresh")
            self.assertEqual(guard.acquire(wkey, worker_id="w2", now=110.0), "skipped_active_lease")
            out = guard.run_idempotent(wkey, lambda: {"ok": 1}, worker_id="w3", now=135.0)
            self.assertEqual(out["decision"], "recovered_stale_lease")
            self.assertTrue(out["executed"])
            out2 = guard.run_idempotent(wkey, lambda: {"ok": 2}, worker_id="w4", now=140.0)
            self.assertEqual(out2["decision"], "skipped_completed")
            self.assertFalse(out2["executed"])

    def test_day05_hmac_webhook_dual_key_replay_and_tamper_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            guard = ReplayProtectedWebhookGuard(
                Path(tmp) / "wh.db",
                secrets=["sec_v2", "sec_v1"],
                tolerance_seconds=120,
            )
            body = b'{"id":"ord_1","cents":1500}'
            sig = sign_webhook_payload("sec_v1", 1000, body)
            r1 = guard.verify_and_record(
                event_id="e1",
                timestamp=1000,
                signature_header=sig,
                raw_body=body,
                now_epoch=1010,
            )
            self.assertFalse(r1["duplicate"])
            r2 = guard.verify_and_record(
                event_id="e1",
                timestamp=1000,
                signature_header=sig,
                raw_body=body,
                now_epoch=1015,
            )
            self.assertTrue(r2["duplicate"])

            # Tampered body or expired timestamp must raise WebhookVerificationError
            with self.assertRaises(WebhookVerificationError):
                guard.verify_and_record(
                    event_id="e2",
                    timestamp=1000,
                    signature_header=sig,
                    raw_body=b'{"id":"ord_1","cents":0}',
                    now_epoch=1015,
                )
            with self.assertRaises(WebhookVerificationError):
                guard.verify_and_record(
                    event_id="e3",
                    timestamp=500,
                    signature_header=sign_webhook_payload("sec_v2", 500, body),
                    raw_body=body,
                    now_epoch=1000,
                )
            with self.assertRaises(WebhookVerificationError):
                guard.verify_and_record(
                    event_id="e4",
                    timestamp=1000,
                    signature_header="v1=é_non_ascii_attack",
                    raw_body=body,
                    now_epoch=1010,
                )

    def test_day06_budgeted_router_failover_and_ceiling_halt(self) -> None:
        router = BudgetedModelRouter(
            [
                ProviderRoute(
                    "broken-primary",
                    cost_per_1k_tokens_cents=2.0,
                    invoke=lambda _p: (_ for _ in ()).throw(RuntimeError("503")),
                    failure_threshold=1,
                ),
                ProviderRoute(
                    "healthy-backup",
                    cost_per_1k_tokens_cents=1.0,
                    invoke=lambda _p: ("ok", 600),
                ),
            ],
            max_tokens=1_000,
            max_cost_cents=5.0,
        )
        first = router.complete("hello", now=10.0)
        self.assertEqual(first["provider"], "healthy-backup")
        second = router.complete("hello", now=11.0)
        self.assertEqual(second["total_tokens_used"], 1200)
        with self.assertRaises(BudgetCapExceededError):
            router.complete("third_blocked", now=12.0)

    def test_day07_end_to_end_capstone_pipeline(self) -> None:
        summary = run_capstone_demo()
        self.assertEqual(summary["ingest_first"]["status"], "enqueued")
        self.assertEqual(summary["ingest_replay"]["status"], "duplicate_ignored")
        self.assertTrue(summary["worker_first"]["executed"])
        self.assertEqual(summary["worker_first"]["result"]["jobs_fulfilled"], 1)
        self.assertFalse(summary["worker_duplicate_window"]["executed"])

        with tempfile.TemporaryDirectory() as tmp:
            router = BudgetedModelRouter(
                [
                    ProviderRoute(
                        "fast",
                        cost_per_1k_tokens_cents=0.1,
                        invoke=lambda _p: (
                            json.dumps(
                                {
                                    "welcome_tier": "builder",
                                    "onboarding_priority": 1,
                                    "send_magic_reach": True,
                                }
                            ),
                            100,
                        ),
                    )
                ]
            )
            pipe = EndToEndReliabilityPipeline(tmp, webhook_secret="s3cr3t", router=router)
            raw = b'{"order_id":"ord_77","gross_cents":1500}'
            sig = sign_webhook_payload("s3cr3t", 2000, raw)
            pipe.ingest_signed_webhook(
                event_id="ev77",
                timestamp=2000,
                signature_header=sig,
                raw_body=raw,
                now_epoch=2001,
            )
            res = pipe.run_scheduled_worker_window(now_epoch=2005.0, notify_fn=lambda r: r)
            self.assertEqual(res["result"]["jobs_fulfilled"], 1)
            lines = pipe.receipts_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(json.loads(lines[0])["order_id"], "ord_77")


if __name__ == "__main__":
    unittest.main()
