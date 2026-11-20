#!/usr/bin/env python3
"""Day 4 Lab: Time-Bucketed SQLite WAL Cron Lease Runner With Stale-Worker Recovery."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional


def compute_cron_window_key(job_name: str, *, cadence: str = "daily", epoch: Optional[float] = None) -> str:
    """Build a deterministic execution window key (`hourly` | `daily`)."""
    clean = job_name.strip()
    if not clean:
        raise ValueError("job_name must be non-empty")
    ts = time.time() if epoch is None else float(epoch)
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    if cadence == "hourly":
        bucket = dt.strftime("%Y-%m-%dT%HZ")
    elif cadence == "daily":
        bucket = dt.strftime("%Y-%m-%d")
    else:
        raise ValueError(f"Unsupported cadence '{cadence}'. Expected 'hourly' or 'daily'.")
    digest = hashlib.sha256(f"{clean}:{bucket}".encode("utf-8")).hexdigest()[:10]
    return f"{clean}:{bucket}:{digest}"


class CronLeaseGuard:
    """Prevents overlapping cron schedules or duplicate webhook triggers from double-running."""

    def __init__(self, db_path: str | Path, *, lease_ttl_seconds: float = 120.0) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.lease_ttl_seconds = float(lease_ttl_seconds)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=5.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def _init_schema(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cron_window_leases (
                    window_key TEXT PRIMARY KEY,
                    worker_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('running', 'completed')),
                    attempts INTEGER NOT NULL DEFAULT 1,
                    lease_until REAL NOT NULL,
                    result_json TEXT NOT NULL DEFAULT '{}'
                );
                """
            )
        finally:
            conn.close()

    def acquire(self, window_key: str, *, worker_id: str, now: Optional[float] = None) -> str:
        ts = time.time() if now is None else float(now)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE;")
            row = conn.execute(
                "SELECT status, lease_until FROM cron_window_leases WHERE window_key = ?",
                (window_key,),
            ).fetchone()
            if row is not None:
                if str(row["status"]) == "completed":
                    conn.execute("COMMIT;")
                    return "skipped_completed"
                if float(row["lease_until"]) > ts:
                    conn.execute("COMMIT;")
                    return "skipped_active_lease"
                conn.execute(
                    """
                    UPDATE cron_window_leases
                       SET worker_id = ?, attempts = attempts + 1, lease_until = ?
                     WHERE window_key = ?
                    """,
                    (worker_id, ts + self.lease_ttl_seconds, window_key),
                )
                conn.execute("COMMIT;")
                return "recovered_stale_lease"

            conn.execute(
                """
                INSERT INTO cron_window_leases (window_key, worker_id, status, attempts, lease_until)
                VALUES (?, ?, 'running', 1, ?)
                """,
                (window_key, worker_id, ts + self.lease_ttl_seconds),
            )
            conn.execute("COMMIT;")
            return "acquired_fresh"
        except Exception:
            conn.execute("ROLLBACK;")
            raise
        finally:
            conn.close()

    def complete(self, window_key: str, *, result: Optional[dict[str, Any]] = None, now: Optional[float] = None) -> None:
        ts = time.time() if now is None else float(now)
        conn = self._connect()
        try:
            conn.execute(
                """
                UPDATE cron_window_leases
                   SET status = 'completed', lease_until = ?, result_json = ?
                 WHERE window_key = ?
                """,
                (ts, json.dumps(result or {}), window_key),
            )
        finally:
            conn.close()

    def run_idempotent(
        self,
        window_key: str,
        fn: Callable[[], dict[str, Any]],
        *,
        worker_id: str = "worker-1",
        now: Optional[float] = None,
    ) -> dict[str, Any]:
        decision = self.acquire(window_key, worker_id=worker_id, now=now)
        if decision not in {"acquired_fresh", "recovered_stale_lease"}:
            return {"decision": decision, "executed": False}
        output = fn()
        self.complete(window_key, result=output, now=now)
        return {"decision": decision, "executed": True, "result": output}


def run_demo() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        guard = CronLeaseGuard(Path(tmp) / "cron.db", lease_ttl_seconds=60.0)
        t0 = 1_763_620_000.0
        wkey = compute_cron_window_key("patreon-reconcile", cadence="daily", epoch=t0)
        s1 = guard.acquire(wkey, worker_id="runner-a", now=t0)  # runner-a crashes mid-flight
        s2 = guard.acquire(wkey, worker_id="runner-b", now=t0 + 15.0)  # blocked while lease active
        s3 = guard.run_idempotent(wkey, lambda: {"synced": 4}, worker_id="runner-c", now=t0 + 65.0)
        s4 = guard.run_idempotent(wkey, lambda: {"synced": 4}, worker_id="runner-d", now=t0 + 90.0)
        return {"day": 4, "timeline": [s1, s2, s3["decision"], s4["decision"]], "final_run": s3}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Day 4 Idempotent Cron Lease Lab")
    parser.add_argument("--demo", action="store_true", help="Run cron lease takeover simulation")
    parser.parse_args()
    print(json.dumps(run_demo(), indent=2))
