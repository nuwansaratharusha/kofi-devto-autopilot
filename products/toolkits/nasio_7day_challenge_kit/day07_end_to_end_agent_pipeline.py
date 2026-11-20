#!/usr/bin/env python3
"""Day 7 Capstone Lab: End-to-End Fault-Tolerant Webhook -> SQLite WAL -> Budgeted AI Agent Pipeline."""

from __future__ import annotations

import argparse
import json
import random
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

_KIT_DIR = str(Path(__file__).resolve().parent)
if _KIT_DIR not in sys.path:
    sys.path.insert(0, _KIT_DIR)

from day01_retry_jitter import RetryableHttpStatusError, execute_with_retry
from day02_llm_schema_validator import invoke_with_schema_repair
from day03_sqlite_wal_queue import SqliteWalQueue
from day04_idempotent_cron_lease import CronLeaseGuard, compute_cron_window_key
from day05_hmac_webhook_guard import ReplayProtectedWebhookGuard, sign_webhook_payload
from day06_token_budget_router import BudgetedModelRouter, ProviderRoute

AGENT_OUTPUT_SCHEMA: dict[str, type] = {
    "welcome_tier": str,
    "onboarding_priority": int,
    "send_magic_reach": bool,
}


class EndToEndReliabilityPipeline:
    """Composes Days 1–6 into a single deterministic, zero-dependency fulfillment pipeline."""

    def __init__(
        self,
        state_dir: str | Path,
        *,
        webhook_secret: str,
        router: BudgetedModelRouter,
    ) -> None:
        base = Path(state_dir)
        base.mkdir(parents=True, exist_ok=True)
        self.webhook_guard = ReplayProtectedWebhookGuard(
            base / "webhooks.db",
            secrets=[webhook_secret],
            tolerance_seconds=300,
        )
        self.queue = SqliteWalQueue(base / "queue.db")
        self.cron_guard = CronLeaseGuard(base / "cron.db", lease_ttl_seconds=120.0)
        self.router = router
        self.receipts_path = base / "fulfillment_receipts.jsonl"

    def ingest_signed_webhook(
        self,
        *,
        event_id: str,
        timestamp: int,
        signature_header: str,
        raw_body: bytes,
        now_epoch: int,
    ) -> dict[str, Any]:
        verdict = self.webhook_guard.verify_and_record(
            event_id=event_id,
            timestamp=timestamp,
            signature_header=signature_header,
            raw_body=raw_body,
            now_epoch=now_epoch,
        )
        if verdict["duplicate"]:
            return {"status": "duplicate_ignored", "event_id": event_id}

        payload = json.loads(raw_body.decode("utf-8"))
        order_id = str(payload["order_id"])
        enqueued = self.queue.enqueue(
            f"job_{order_id}",
            idempotency_key=f"order:{order_id}",
            payload=payload,
            now=float(now_epoch),
        )
        return {"status": "enqueued" if enqueued else "already_queued", "order_id": order_id}

    def run_scheduled_worker_window(
        self,
        *,
        now_epoch: float,
        notify_fn: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        wkey = compute_cron_window_key("fulfillment-sweep", cadence="hourly", epoch=now_epoch)

        def _sweep() -> dict[str, Any]:
            processed = 0
            rng = random.Random(20261120)
            while (job := self.queue.claim(now=now_epoch)) is not None:
                prompt = f"Classify buyer onboarding for order {json.dumps(job.payload, sort_keys=True)}"

                def _model_step(p: str) -> str:
                    routed = self.router.complete(p, now=now_epoch)
                    return str(routed["text"])

                structured, attempts = invoke_with_schema_repair(
                    prompt,
                    AGENT_OUTPUT_SCHEMA,
                    _model_step,
                    max_repairs=2,
                )
                receipt = {
                    "job_id": job.job_id,
                    "order_id": job.payload["order_id"],
                    "agent_decision": structured,
                    "schema_attempts": attempts,
                }
                _, retry_meta = execute_with_retry(
                    lambda: notify_fn(receipt),
                    max_attempts=3,
                    sleeper=lambda _s: None,
                    rng=rng,
                )
                receipt["delivery_attempts"] = retry_meta.attempts
                with self.receipts_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(receipt, sort_keys=True) + "\n")
                self.queue.ack(job.job_id)
                processed += 1
            return {
                "jobs_fulfilled": processed,
                "tokens_used": self.router.tokens_used,
                "queue_counts": self.queue.counts(),
            }

        return self.cron_guard.run_idempotent(wkey, _sweep, worker_id="capstone-worker", now=now_epoch)


def run_demo() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        secret = "nasio_challenge_secret_key"
        llm_turn = {"n": 0}

        def simulated_primary(prompt: str) -> tuple[str, int]:
            llm_turn["n"] += 1
            if llm_turn["n"] == 1 and "SCHEMA_REPAIR_REQUIRED" not in prompt:
                return '```json\n{"welcome_tier":"challenge_vip","onboarding_priority":true,"send_magic_reach":true}\n```', 220
            return '{"welcome_tier":"challenge_vip","onboarding_priority":1,"send_magic_reach":true}', 210

        router = BudgetedModelRouter(
            [ProviderRoute("primary", cost_per_1k_tokens_cents=0.5, invoke=simulated_primary)],
            max_tokens=5_000,
            max_cost_cents=5.0,
        )
        pipeline = EndToEndReliabilityPipeline(tmp, webhook_secret=secret, router=router)

        now_ts = 1_763_680_000
        body = json.dumps({"order_id": "nas_chal_01", "sku": "nasio-7day-ai-automation-challenge-pack", "gross_cents": 1500}).encode("utf-8")
        sig = sign_webhook_payload(secret, now_ts, body)

        ing1 = pipeline.ingest_signed_webhook(
            event_id="evt_01",
            timestamp=now_ts,
            signature_header=sig,
            raw_body=body,
            now_epoch=now_ts + 2,
        )
        ing_dup = pipeline.ingest_signed_webhook(
            event_id="evt_01",
            timestamp=now_ts,
            signature_header=sig,
            raw_body=body,
            now_epoch=now_ts + 4,
        )

        notify_calls = {"n": 0}

        def flaky_webhook_notifier(receipt: dict[str, Any]) -> dict[str, Any]:
            notify_calls["n"] += 1
            if notify_calls["n"] == 1:
                raise RetryableHttpStatusError(503, "temporary_503")
            return {"delivered": receipt["order_id"]}

        sweep1 = pipeline.run_scheduled_worker_window(now_epoch=float(now_ts + 10), notify_fn=flaky_webhook_notifier)
        sweep_dup = pipeline.run_scheduled_worker_window(now_epoch=float(now_ts + 20), notify_fn=flaky_webhook_notifier)

        return {
            "day": 7,
            "ingest_first": ing1,
            "ingest_replay": ing_dup,
            "worker_first": sweep1,
            "worker_duplicate_window": sweep_dup,
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Day 7 End-to-End Agent Reliability Pipeline")
    parser.add_argument("--demo", action="store_true", help="Run full Day 1-7 capstone pipeline")
    parser.parse_args()
    print(json.dumps(run_demo(), indent=2))
