<!-- PATREON WEEKLY BATCH · DAY 6/30 · SCHEDULE: 2026-11-23 18:30 Asia/Colombo (2026-11-23T13:00:00Z) · ACCESS: PAID MEMBERS ($5/MO) & SHOP · ATTACH: products/dist/nasio-7day-ai-automation-challenge-pack.zip -->

# [Members & Shop · Day 6/30] Runnable Kit + Full Benchmark: Why Timestamp Windows Alone Fail Webhook Security (Atomic SQLite Nonce Deduplication Under Load)

Thank you for backing **30 Days of Zero-Cost Production Python & AI Reliability**! Attached to this post is the latest unit-tested `.zip` release (`production-agent-automation-kit.zip` — MIT Commercial License) covering today's **Idempotent Webhooks & Double-Entry Ledger Teardowns** teardown.

**Attachment Checklist Before Clicking Publish in Patreon:**
- Access Setting: **Paid members only** (or sell as a **$14–$15 one-time Patreon Shop post**)
- File Attachment: `products/dist/production-agent-automation-kit.zip` (or `products/dist/nasio-7day-ai-automation-challenge-pack.zip`)

---

## Why Valid HMAC Signatures + 300-Second Timestamp Checks Still Allow Double-Fulfillment

Many webhook tutorials stop right after verifying `hmac.compare_digest(expected, v1)` and checking `abs(now - timestamp) <= 300`. While those two checks block forged payloads and stale recordings from yesterday, they leave a glaring 5-minute replay window wide open against two very real scenarios:

1. **Aggressive Gateway & Proxy Retries:** If your HTTP ingress times out at the load balancer just as your app finishes processing a webhook—or if an upstream webhook sender retries on transient TCP resets—two identical, cryptographically valid signed requests hit your worker pool seconds (or microseconds) apart.
2. **Parallel Race Conditions on `SELECT` Before `INSERT`:** Checking `if nonce not in db` in application memory or inside a deferred SQLite transaction allows 8 Gunicorn/Uvicorn threads receiving the same captured `curl` replay simultaneously to *all* read `None` before the first `INSERT` commits—granting 8 license keys or 8 account credits for a single payment.
3. **Unbounded Nonce Table Growth:** Teams that do add a `seen_nonces` table often forget TTL pruning, letting millions of historical nonces bloat indexes even though any request older than `300s` is already rejected at the timestamp gate before touching disk.

In this Friday security teardown—*Blocking captured-payload replay attacks inside the 300-second tolerance window using atomic WAL nonce tables.* (stdlib reference: [Python 3 Standard Library Docs](https://docs.python.org/3/library/sqlite3.html))—we fire 16 concurrent threads carrying the exact same valid signed webhook against an atomic SQLite WAL `INSERT OR IGNORE` nonce guard and verify bounded TTL pruning.

## Replay Defense Layering

| Defense Layer | Attack / Bug Mitigated | What Still Slips Through Without Next Layer |
| :--- | :--- | :--- |
| **1. Raw-Byte `hmac.compare_digest`** | Payload tampering & timing side-channel leaks | Any unmodified captured request replayed indefinitely |
| **2. Timestamp Window (`abs(now - ts) <= 300`)** | Replays of old captured logs (`> 5 min` old) | Intra-window replays and parallel retry storms inside the 300s window |
| **3. Atomic `BEGIN IMMEDIATE` Nonce `PRIMARY KEY`** | Concurrent thread races & intra-window replays | Table growth over months unless paired with TTL pruning |
| **4. Opportunistic `expires_at <= now` Prune** | Unbounded index growth on disk | Bounded to `2 * window_sec` (10 minutes of active rows) |

Need an idempotent scheduled runner and HTTP circuit breaker alongside your webhook handlers? Grab the [Free Idempotent Cron & Circuit-Breaker Mini-Kit ($0+ PWYW)](https://www.patreon.com/NuwansaraT?utm_source=devto&utm_medium=tutorial&utm_campaign=why-timestamp-windows-alone-fail-webhook-securit&slug=why-timestamp-windows-alone-fail-webhook-security-atomic-sqlite-nonce-64887a&sku=free-idempotent-cron-mini-kit).

## Runnable 16-Thread Parallel Webhook Replay Race Benchmark

The pure Python 3.11+ benchmark below launches 16 concurrent threads via `ThreadPoolExecutor` racing the exact same valid HMAC-signed payment webhook against a SQLite WAL database, then advances the clock past `2 * window_sec` (`702s`) to confirm expired nonces prune automatically:

```python
from __future__ import annotations

import concurrent.futures
import hashlib
import hmac
import json
import sqlite3
import time
from pathlib import Path


def make_sig(secret: bytes, ts: int, nonce: str, body: bytes) -> str:
    mac = hmac.new(secret, f"{ts}.{nonce}.".encode("utf-8") + body, hashlib.sha256).hexdigest()
    return f"t={ts},id={nonce},v1={mac}"


def init_nonce_store(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path), timeout=5.0, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS seen_nonces (
            nonce TEXT PRIMARY KEY,
            expires_at INTEGER NOT NULL
        );
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_nonce_exp ON seen_nonces(expires_at);")
    conn.close()


def verify_atomic_nonce(db_path: Path, secret: bytes, header: str, body: bytes, now_ts: int, window_sec: int = 300) -> str:
    parts = dict(item.split("=", 1) for item in header.split(",") if "=" in item)
    ts = int(parts["t"])
    nonce = parts["id"]
    sig = parts["v1"]
    if abs(now_ts - ts) > window_sec:
        return "stale"
    expected = hmac.new(secret, f"{ts}.{nonce}.".encode("utf-8") + body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return "bad_sig"

    conn = sqlite3.connect(str(db_path), timeout=10.0, isolation_level=None)
    conn.execute("PRAGMA busy_timeout=10000;")
    try:
        conn.execute("BEGIN IMMEDIATE;")
        conn.execute("DELETE FROM seen_nonces WHERE expires_at <= ?", (now_ts,))
        cur = conn.execute(
            "INSERT OR IGNORE INTO seen_nonces (nonce, expires_at) VALUES (?, ?)",
            (nonce, now_ts + window_sec * 2),
        )
        conn.execute("COMMIT;")
        return "accepted" if cur.rowcount == 1 else "replay_blocked"
    except Exception:
        conn.execute("ROLLBACK;")
        raise
    finally:
        conn.close()


def run_concurrent_replay_benchmark() -> None:
    t0 = time.perf_counter()
    db_path = Path("nonce_race.db")
    init_nonce_store(db_path)
    secret = b"whsec_concurrency_key_42"
    now_ts = 1_760_600_000
    body = b'{"sku":"production-agent-automation-kit","amount_cents":1400}'
    signed_hdr = make_sig(secret, now_ts - 3, "evt_parallel_001", body)

    # Fire 16 parallel threads racing the exact same valid signed webhook at once
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [
            pool.submit(verify_atomic_nonce, db_path, secret, signed_hdr, body, now_ts)
            for _ in range(16)
        ]
        results = [f.result() for f in futures]

    # Advance clock past 2*window_sec and insert 1 fresh event to trigger bounded TTL pruning
    fresh_hdr = make_sig(secret, now_ts + 700, "evt_fresh_002", body)
    post_prune = verify_atomic_nonce(db_path, secret, fresh_hdr, body, now_ts + 702)
    conn = sqlite3.connect(str(db_path))
    active_rows = int(conn.execute("SELECT COUNT(*) FROM seen_nonces").fetchone()[0])
    conn.close()

    report = {
        "parallel_attempts": len(results),
        "accepted_exactly_once": results.count("accepted"),
        "replays_blocked": results.count("replay_blocked"),
        "post_ttl_fresh_status": post_prune,
        "retained_nonce_rows_after_ttl": active_rows,
        "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 2),
    }
    Path("nonce_race_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    run_concurrent_replay_benchmark()
```

Next, we assert that exactly `1` of the `16` parallel threads was accepted while all `15` concurrent replays were blocked and expired rows were pruned cleanly:

```bash
python3 -I -c '
import json
n = json.load(open("nonce_race_report.json"))
assert n["accepted_exactly_once"] == 1 and n["replays_blocked"] == 15
assert n["retained_nonce_rows_after_ttl"] == 1
print(f"Verified parallel replay race: {n["accepted_exactly_once"]}/16 accepted, {n["replays_blocked"]} concurrent replays blocked, TTL table pruned to {n["retained_nonce_rows_after_ttl"]} row.")
'
```

## Real Captured Execution Output

Both blocks above ran inside a clean isolated sandbox prior to staging. Here is the exact stdout captured from the run:

<!-- EMPIRICAL_OUTPUT_START -->
```text
# Block 1 (python, exit=0, 117.8 ms)
{
  "parallel_attempts": 16,
  "accepted_exactly_once": 1,
  "replays_blocked": 15,
  "post_ttl_fresh_status": "accepted",
  "retained_nonce_rows_after_ttl": 1,
  "elapsed_ms": 35.75
}

# Block 2 (bash, exit=0, 41.6 ms)
Verified parallel replay race: 1/16 accepted, 15 concurrent replays blocked, TTL table pruned to 1 row.
```
<!-- EMPIRICAL_OUTPUT_END -->

## Why `BEGIN IMMEDIATE` + `INSERT OR IGNORE` Wins Under Contention

Look at what happened across those 16 simultaneous threads in `nonce_race_report.json`:

- **Zero Race Window (`1/16` Accepted, `15/16` Blocked):** Because `BEGIN IMMEDIATE` acquires SQLite's write lock before executing `INSERT OR IGNORE INTO seen_nonces`, SQLite serializes all 16 threads cleanly via `busy_timeout=10000`. Exactly one thread sees `cur.rowcount == 1`; the other 15 observe `cur.rowcount == 0` and return `replay_blocked` in milliseconds without raising unhandled `IntegrityError` tracebacks.
- **Self-Pruning Storage Footprint:** Rejecting `abs(now - ts) > 300` *before* opening a write transaction means expired nonces can never pass the timestamp gate anyway. Pruning `WHERE expires_at <= now` (`now + 600s` TTL) inside legitimate writes keeps `seen_nonces` tiny (`retained_nonce_rows_after_ttl == 1`) without needing a separate cleanup daemon.
- **Strict Hex Validation Note:** If you also verify HMAC headers in Node.js (`Buffer.from(sigHex, "hex")`), always validate `/^[0-9a-f]64$/i.test(sigHex)` before decoding—Node's hex decoder silently ignores trailing non-hex characters!

### Secret Rotation Without Dropping In-Flight Webhooks

During live webhook secret rotation, gateways such as Stripe and Polar temporarily sign outgoing deliveries with both the retiring key and the newly rolled key (`v1=<sig_old>,v1=<sig_new>`). Support zero-downtime key rotation by accepting an ordered tuple of active secrets `(current_secret, previous_secret)` during the 24-hour rotation window, computing `hmac.compare_digest` against each candidate before entering the `BEGIN IMMEDIATE` nonce transaction, and retiring `previous_secret` as soon as all upstream endpoints confirm cutover.

## Get Both Python & Node.js Hardened HMAC Guards ($14 Kit)

Skip subtle crypto and concurrency pitfalls: the [Production AI Agent & Zero-Cost Backend Reliability Starter Kit ($14, MIT License)](https://www.patreon.com/NuwansaraT?utm_source=devto&utm_medium=tutorial&utm_campaign=why-timestamp-windows-alone-fail-webhook-securit&slug=why-timestamp-windows-alone-fail-webhook-security-atomic-sqlite-nonce-64887a&sku=production-agent-automation-kit) includes hardened Python (`webhook_hmac.py`) and Node/TypeScript (`agent_guardrails.mjs`) HMAC replay guards, portable SQLite/PostgreSQL double-entry ledgers, a transactional SQLite WAL job queue + CLI, and 4 GitHub Actions blueprints.

---

*Verification & Disclosure: Published under the **Zero-Cost Production Reliability** column by **Tharusha Nuwansara**. Every Python and shell block above was executed and verified inside an isolated `python3 -I` sandbox (`returncode == 0`) prior to publication.*
