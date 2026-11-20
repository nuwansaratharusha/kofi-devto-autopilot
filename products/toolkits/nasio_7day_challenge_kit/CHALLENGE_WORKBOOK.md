# 7-Day Fault-Tolerant Python & AI Agent Reliability Challenge — Participant Workbook

**Format:** 7 Self-Contained Daily Engineering Labs (`~45 minutes/day`)  
**Runtime:** Python `3.11+` Standard Library Only (`0` pip packages, `$0` cloud bills)  
**License:** MIT Commercial & Personal License — copy any module straight into client or production repositories.

---

## How to Use This Workbook

Every day follows the same 3-step loop:
1. **Run the deterministic lab demo** (`python3 day0X_*.py --demo`) and inspect the JSON telemetry.
2. **Complete the Failure-Injection Drill** so you watch the guardrail catch a real production bug.
3. **Run `python3 -m unittest test_challenge_kit -v`** to verify every contract stays green.

---

## Day 1 — `Retry-After` Header Parsing & Full-Jitter Backoff (`day01_retry_jitter.py`)
- **Production Bug Eliminated:** Thundering-herd retry storms where 50 workers wake at identical `0.5s, 1.0s, 2.0s` ticks and trigger cascading `HTTP 429` bans.
- **Run:** `python3 day01_retry_jitter.py --demo`
- **Failure-Injection Drill:** Pass `RetryableHttpStatusError(400, "invalid_api_key")` into `execute_with_retry` and confirm non-retryable `4xx` errors abort in `1` attempt with `0.0s` wasted sleep.

## Day 2 — Strict LLM Structured-JSON Validation & Self-Repair (`day02_llm_schema_validator.py`)
- **Production Bug Eliminated:** Markdown code-fence wrappers (` ```json ... ``` `) crashing `json.loads`, plus Python's `isinstance(True, int) == True` footgun silently accepting booleans for integer fields.
- **Run:** `python3 day02_llm_schema_validator.py --demo`
- **Failure-Injection Drill:** Return `{"action": "ship", "priority": false, "confidence": 0.9, "idempotent": true}` on attempt 1 and observe `[SCHEMA_REPAIR_REQUIRED]` self-heal on attempt 2.

## Day 3 — Transactional SQLite WAL Queue & Dead-Letter Quarantine (`day03_sqlite_wal_queue.py`)
- **Production Bug Eliminated:** Paying `$30+/month` for managed Redis/SQS on single-node workloads—or letting a single poison job block healthy queue items forever.
- **Run:** `python3 day03_sqlite_wal_queue.py --demo`
- **Failure-Injection Drill:** Enqueue two jobs with the same `idempotency_key` and verify `counts()` records exactly one queued job (`INSERT OR IGNORE` inside `BEGIN IMMEDIATE`).

## Day 4 — Time-Bucketed Idempotent Cron Leases (`day04_idempotent_cron_lease.py`)
- **Production Bug Eliminated:** Delayed GitHub Actions / `crontab` schedules overlapping a still-running worker—or permanently wedging when a worker OOM-crashes before releasing a lockfile.
- **Run:** `python3 day04_idempotent_cron_lease.py --demo`
- **Failure-Injection Drill:** Acquire a 60-second lease as `runner-a`, verify `runner-b` is blocked at `t+15s`, and watch `runner-c` cleanly recover the expired lease at `t+65s`.

## Day 5 — Constant-Time HMAC Webhooks & Dual-Key Rotation (`day05_hmac_webhook_guard.py`)
- **Production Bug Eliminated:** Timing side-channels (`==` instead of `hmac.compare_digest`), replay attacks, and dropped payment webhooks during 24-hour signing-secret rotations.
- **Run:** `python3 day05_hmac_webhook_guard.py --demo`
- **Failure-Injection Drill:** Sign a payload with `retiring_old_secret_2025` while `new_primary_secret_2026` is first in `secrets=[...]` and confirm zero-downtime verification + SQLite replay deduplication.

## Day 6 — Multi-Provider AI Router With Token & Cent Ceilings (`day06_token_budget_router.py`)
- **Production Bug Eliminated:** Runaway agent loops burning `$50` overnight on a frontier model or stalling when the primary provider returns `HTTP 503`.
- **Run:** `python3 day06_token_budget_router.py --demo`
- **Failure-Injection Drill:** Lower `max_tokens=900` and verify `BudgetCapExceededError` halts execution cleanly while `ProviderRoute` fails over to `backup-flash` on transport errors.

## Day 7 — Capstone: End-to-End Signed Webhook -> WAL Queue -> AI Agent Fulfillment (`day07_end_to_end_agent_pipeline.py`)
- **Production Architecture:** Wires Days 1–6 into one cohesive pipeline:
  `HMAC Webhook Guard -> Idempotent SQLite WAL Queue -> Cron Window Lease -> Budgeted AI Schema Router -> Retry-Guarded Receipt Delivery`.
- **Run:** `python3 day07_end_to_end_agent_pipeline.py --demo`
- **Graduation Check:** Run `python3 -m unittest test_challenge_kit -v` (`7/7` test suites green) and drop `EndToEndReliabilityPipeline` straight into your own webhook or background automation service!
