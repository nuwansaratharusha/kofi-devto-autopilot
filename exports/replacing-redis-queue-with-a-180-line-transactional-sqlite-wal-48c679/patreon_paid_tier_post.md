# [Members & Shop · Day 2/30] Runnable Kit + Full Benchmark: Replacing Redis Queue With a 180-Line Transactional SQLite WAL Job Broker & Dead-Letter CLI

Thank you for backing **30 Days of Zero-Cost Production Python & AI Reliability**! Attached to this post is the latest unit-tested `.zip` release (`production-agent-automation-kit.zip` — MIT Commercial License) covering today's **Zero-Cost Backend, SQLite WAL Queues & Cron Automation** teardown.

**Attachment Checklist Before Clicking Publish in Patreon:**
- Access Setting: **Paid members only** (or sell as a **$14–$15 one-time Patreon Shop post**)
- File Attachment: `products/dist/production-agent-automation-kit.zip` (or `products/dist/nasio-7day-ai-automation-challenge-pack.zip`)

---

## Why Most Small-to-Midsize Backends Do Not Need Redis Just to Queue Jobs

Spinning up an external Redis instance, Celery worker fleet, and broker health monitor before your service handles thousands of concurrent writes per second introduces three immediate operational taxes: extra monthly hosting bills, cross-network serialization timeouts, and split-brain state where a database row commits while the broker publish silently drops.

Modern SQLite running in **Write-Ahead Logging (`WAL`)** mode on local NVMe/APFS/ext4 storage easily sustains thousands of atomic transactions per second—*provided* you configure locking, visibility leases, and poison-pill quarantine correctly. When teams complain about `sqlite3.OperationalError: database is locked`, the root cause is almost always one of three mistakes:

1. Leaving SQLite in legacy rollback-journal mode instead of enabling `PRAGMA journal_mode=WAL;` and `PRAGMA busy_timeout=5000;`.
2. Opening an implicit deferred `SELECT` transaction and attempting an `UPDATE` halfway through, triggering an instant deadlock between two workers competing to upgrade from a shared read lock to a reserved write lock.
3. Retrying a malformed ("poison-pill") job forever at the head of the queue until healthy jobs starve behind it.

In this teardown—*Benchmarking atomic BEGIN IMMEDIATE visibility-timeout leases, crash recovery, and dead-letter replay on a single $0 disk volume.* (technical reference: [SQLite Official Specification](https://www.sqlite.org/wal.html))—we write and benchmark a complete transactional SQLite WAL queue in pure Python 3.11+ that processes 400 burst events, suppresses duplicate retries atomically, and isolates a simulated crashing worker into a `dead_letter` state without losing a single healthy job.

## Four SQLite Invariants That Make Disk Queues Production-Safe

| Failure Scenario | Broken Pattern | Atomic SQLite WAL Pattern |
| :--- | :--- | :--- |
| **Duplicate Upstream Triggers** | Plain `INSERT` enqueues two copies of the same order webhook | Enforce `idempotency_key TEXT NOT NULL UNIQUE` with `INSERT OR IGNORE` inside `BEGIN IMMEDIATE` |
| **Lock-Upgrade Deadlocks** | `SELECT` ready job first, then `UPDATE` status in Python's default transaction mode | Start every claim transaction with explicit `BEGIN IMMEDIATE` (`isolation_level=None`) so SQLite serializes writers cleanly via `busy_timeout` |
| **Worker OOM / SIGKILL Mid-Job** | `DELETE FROM queue RETURNING *` permanently destroys in-flight work on crash | Set `status = 'leased'` with `lease_expires_at = now + lease_sec` so surviving workers reclaim expired leases automatically |
| **Poison-Pill Infinite Loops** | Expired leases re-enter the queue indefinitely | Promote leased rows where `attempts >= max_attempts` straight to `dead_letter` before claiming the next ready item |

For single-command cron jobs where you only need atomic window deduplication (`hourly` / `daily`) and HTTP backoff without a full queue table, download the [Free Idempotent Cron & Circuit-Breaker Mini-Kit ($0+ PWYW)](https://nuwansaratharusha.github.io/kofi-devto-autopilot?utm_source=devto&utm_medium=tutorial&utm_campaign=replacing-redis-queue-with-a-180-line-transactio&slug=replacing-redis-queue-with-a-180-line-transactional-sqlite-wal-48c679&sku=free-idempotent-cron-mini-kit) and drop `idempotent_cron.py` directly into your scheduler.

## Runnable SQLite WAL Queue & Crash-Recovery Benchmark

The standalone script below initializes a fresh SQLite WAL database, enqueues 400 burst events (with every 5th event simulating a duplicate retry key), simulates a worker repeatedly crashing on `job_0` until its visibility lease expires 3 times, and measures end-to-end throughput:

```python
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path


def init_wal_queue(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS queue_jobs (
            job_id TEXT PRIMARY KEY,
            idempotency_key TEXT NOT NULL UNIQUE,
            payload TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('queued', 'leased', 'completed', 'dead_letter')),
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            available_at REAL NOT NULL,
            lease_expires_at REAL
        );
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_queue_ready ON queue_jobs(status, available_at);"
    )
    return conn


def enqueue_idempotent(conn: sqlite3.Connection, job_id: str, idem_key: str, payload: dict, now: float) -> bool:
    conn.execute("BEGIN IMMEDIATE;")
    try:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO queue_jobs (job_id, idempotency_key, payload, status, available_at)
            VALUES (?, ?, ?, 'queued', ?)
            """,
            (job_id, idem_key, json.dumps(payload), now),
        )
        conn.execute("COMMIT;")
        return cur.rowcount == 1
    except Exception:
        conn.execute("ROLLBACK;")
        raise


def claim_next(conn: sqlite3.Connection, now: float, lease_sec: float = 15.0) -> sqlite3.Row | None:
    conn.execute("BEGIN IMMEDIATE;")
    try:
        conn.execute(
            """
            UPDATE queue_jobs
               SET status = 'dead_letter', lease_expires_at = NULL
             WHERE status = 'leased' AND lease_expires_at <= ? AND attempts >= max_attempts
            """,
            (now,),
        )
        row = conn.execute(
            """
            SELECT job_id FROM queue_jobs
             WHERE (status = 'queued' AND available_at <= ?)
                OR (status = 'leased' AND lease_expires_at <= ? AND attempts < max_attempts)
             ORDER BY available_at ASC
             LIMIT 1
            """,
            (now, now),
        ).fetchone()
        if row is None:
            conn.execute("COMMIT;")
            return None
        conn.execute(
            """
            UPDATE queue_jobs
               SET status = 'leased', attempts = attempts + 1, lease_expires_at = ?
             WHERE job_id = ?
            """,
            (now + lease_sec, row["job_id"]),
        )
        claimed = conn.execute("SELECT * FROM queue_jobs WHERE job_id = ?", (row["job_id"],)).fetchone()
        conn.execute("COMMIT;")
        return claimed
    except Exception:
        conn.execute("ROLLBACK;")
        raise


def run_wal_benchmark() -> None:
    db_file = Path("wal_bench.db")
    conn = init_wal_queue(db_file)
    now = 1_760_000_000.0
    t0 = time.perf_counter()

    inserted = 0
    duplicates_suppressed = 0
    for i in range(400):
        idem = f"evt_{i - 1}" if i % 5 == 4 else f"evt_{i}"
        if enqueue_idempotent(conn, f"job_{i}", idem, {"seq": i}, now):
            inserted += 1
        else:
            duplicates_suppressed += 1

    completed = 0
    while True:
        leased = claim_next(conn, now, lease_sec=5.0)
        if leased is None:
            break
        if leased["job_id"] == "job_0":
            now += 6.0
            continue
        conn.execute("UPDATE queue_jobs SET status = 'completed', lease_expires_at = NULL WHERE job_id = ?", (leased["job_id"],))
        completed += 1

    dlq_count = int(conn.execute("SELECT COUNT(*) FROM queue_jobs WHERE status = 'dead_letter'").fetchone()[0])
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    throughput = round((inserted + completed) / max(0.001, elapsed_ms / 1000.0), 1)

    report = {
        "enqueued_unique": inserted,
        "duplicates_suppressed": duplicates_suppressed,
        "completed_cleanly": completed,
        "dead_letter_quarantined": dlq_count,
        "elapsed_ms": elapsed_ms,
        "ops_per_sec": throughput,
    }
    Path("wal_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    conn.close()


if __name__ == "__main__":
    run_wal_benchmark()
```

Now we verify strict conservation of jobs (`completed_cleanly + dead_letter_quarantined == enqueued_unique`) using an isolated assertion check:

```bash
python3 -I -c '
import json
r = json.load(open("wal_report.json"))
assert r["dead_letter_quarantined"] == 1, "Poison job was not isolated in dead_letter!"
assert r["completed_cleanly"] + r["dead_letter_quarantined"] == r["enqueued_unique"]
print(f"Verified SQLite WAL accounting: {r["completed_cleanly"]} completed + 1 DLQ = {r["enqueued_unique"]} unique jobs ({r["ops_per_sec"]} ops/sec).")
'
```

## Real Captured Execution Output

The benchmark and conservation check above were executed inside an isolated sandbox before this post was staged. Here is the exact captured output:

<!-- EMPIRICAL_OUTPUT_START -->
```text
# Block 1 (python, exit=0, 99.7 ms)
{
  "enqueued_unique": 320,
  "duplicates_suppressed": 80,
  "completed_cleanly": 319,
  "dead_letter_quarantined": 1,
  "elapsed_ms": 46.21,
  "ops_per_sec": 13828.2
}

# Block 2 (bash, exit=0, 53.2 ms)
Verified SQLite WAL accounting: 319 completed + 1 DLQ = 320 unique jobs (13828.2 ops/sec).
```
<!-- EMPIRICAL_OUTPUT_END -->

## Dissecting the Benchmark Results

Notice three concrete operational properties proven by the run above:

- **Zero Duplicate Side Effects:** Out of 400 incoming enqueue calls, all 80 duplicate `idempotency_key` retries were suppressed at the storage constraint layer before a worker ever woke up (`320` unique jobs enqueued).
- **Deterministic Crash Isolation:** When `job_0` simulated three consecutive worker crashes across visibility-timeout boundaries, the claim query promoted `job_0` to `dead_letter` on the fourth pass while completing all `319` healthy jobs without manual intervention.
- **Sub-50ms Batch Latency:** All 320 unique enqueues plus 322 atomic `BEGIN IMMEDIATE` lease claims and completions finished in tens of milliseconds on standard local storage (`ops_per_sec` in the thousands), leaving huge headroom for indie SaaS backends, webhook consumers, and scheduled AI pipelines.

### When You Should Migrate Beyond SQLite WAL

Engineering honesty matters: SQLite WAL requires a single host filesystem (attached NVMe, EBS, or Fly/Railway persistent volume) because WAL shared-memory (`-shm`) relies on OS-local mmap semantics. Do **not** mount a SQLite WAL file across NFS or SMB network shares. Once you need multi-region active-active writers across separate VMs, port the exact same `lease_expires_at` + `BEGIN` / `FOR UPDATE SKIP LOCKED` schema to PostgreSQL.

## Grab the Complete CLI, Dead-Letter Replayer & CI Workflows

If you want the full CLI wrapper (`enqueue`, `stats`, `requeue-dlq`), exponential NACK backoff, Python + Node/TypeScript LLM circuit breakers, HMAC webhook replay guards, and 4 GitHub Actions YAML workflows ready to ship, pick up the [Production AI Agent & Zero-Cost Backend Reliability Starter Kit ($14, MIT License)](https://nuwansaratharusha.github.io/kofi-devto-autopilot?utm_source=devto&utm_medium=tutorial&utm_campaign=replacing-redis-queue-with-a-180-line-transactio&slug=replacing-redis-queue-with-a-180-line-transactional-sqlite-wal-48c679&sku=production-agent-automation-kit).

---

*Verification & Disclosure: Published under the **Zero-Cost Production Reliability** column by **Tharusha Nuwansara**. Every Python and shell block above was executed and verified inside an isolated `python3 -I` sandbox (`returncode == 0`) prior to publication.*
