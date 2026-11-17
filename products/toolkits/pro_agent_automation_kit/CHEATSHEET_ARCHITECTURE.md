# Production Reliability Architecture Cheat Sheet ($0 Infrastructure Edition)

**Author:** Tharusha Nuwansara (`https://ko-fi.com/tharushanuwansara`)  
**Scope:** Fault-tolerant AI agents, SQLite WAL job queues, HMAC webhooks, and double-entry ledgers.

---

## 1. SQLite WAL Concurrency & Lease Rules (`wal_queue.py`)

| Setting / Invariant | Exact Configuration | Why It Matters |
| :--- | :--- | :--- |
| `PRAGMA journal_mode=WAL;` | Always enabled at connection open | Readers never block writers; eliminates `SQLITE_BUSY` on read-heavy endpoints. |
| `PRAGMA synchronous=NORMAL;` | Safe with WAL on modern SSD/APFS/ext4 | Cuts fsync overhead by 5–10x while preserving crash consistency. |
| `BEGIN IMMEDIATE;` | Wrap every `claim` / `nack` / `enqueue` | Acquires the write lock *upfront* instead of deadlocking halfway through `SELECT -> UPDATE`. |
| Visibility Leases | `lease_expires_at = now + timeout` | Workers that OOM or lose power automatically forfeit their lease without losing the job. |
| Dead-Letter Quarantine | `attempts >= max_attempts -> dead_letter` | Prevents poison-pill payloads from starving healthy jobs; inspect & replay via `requeue-dlq`. |

---

## 2. LLM & External API Guardrail Checklist (`llm_circuit_breaker.py` & `agent_guardrails.mjs`)

1. **Never parse raw LLM strings directly with `json.loads`:** Strip Markdown code fences first and validate required keys + strict scalar types (`bool` is a subclass of `int` in Python—explicitly reject `isinstance(val, bool)` when `int` is expected!).
2. **Self-Repair Before Failing Over:** Feed the exact schema error (`REPAIR_HINT`) back once; if the provider throws a transport `5xx`/`429`, increment the circuit breaker and fail over immediately to the secondary provider.
3. **Hard Token Ceiling:** Enforce `max_total_tokens` per workflow run so cyclic tool-calling bugs abort deterministically instead of burning API credits overnight.

---

## 3. Webhook & Financial Ledger Invariants (`webhook_hmac.py` & `sql/idempotent_ledger.sql`)

1. **Verify Raw Bytes Before JSON Parsing:** Compute `HMAC-SHA256(secret, f"{timestamp}.{nonce}." + raw_body)` and compare with `hmac.compare_digest` / `crypto.timingSafeEqual`.
2. **Reject Replay Windows & Nonces:** Enforce `abs(now - timestamp) <= 300s` *and* insert `nonce` into a `UNIQUE` table inside `BEGIN IMMEDIATE` so captured curl replays within the 5-minute window still fail atomically.
3. **Integer Minor Units Only:** Store `$14.00` as `1400` (`amount_cents INTEGER CHECK (amount_cents > 0)`), enforce `UNIQUE(external_source, external_event_id)` on `ledger_transactions`, and compute account balances from immutable `DEBIT`/`CREDIT` postings.
