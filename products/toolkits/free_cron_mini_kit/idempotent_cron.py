#!/usr/bin/env python3
"""Zero-dependency SQLite WAL idempotent cron job runner with stale-lock recovery.

Guarantees at-most-once completion per `(job_name, window_key)` while allowing
safe automatic recovery when a previous worker crashed mid-flight (`lease_seconds`).
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import sqlite3
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Optional, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class CronRunOutcome:
    job_name: str
    execution_key: str
    status: str  # "completed" | "skipped_already_completed" | "skipped_active_lease" | "failed"
    attempt_count: int
    duration_ms: float
    worker_id: str
    result_summary: Optional[str] = None
    error_message: Optional[str] = None


def compute_window_key(job_name: str, cadence: str = "hourly", now_epoch: Optional[float] = None) -> str:
    """Derive a deterministic idempotency key from UTC wall clock and cadence."""
    ts = time.time() if now_epoch is None else now_epoch
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    if cadence == "minutely":
        bucket = dt.strftime("%Y-%m-%dT%H:%MZ")
    elif cadence == "hourly":
        bucket = dt.strftime("%Y-%m-%dT%HZ")
    elif cadence == "daily":
        bucket = dt.strftime("%Y-%m-%d")
    else:
        bucket = cadence
    digest = hashlib.sha256(f"{job_name}:{bucket}".encode("utf-8")).hexdigest()[:16]
    return f"{job_name}:{bucket}:{digest}"


class IdempotentCronRunner:
    """SQLite WAL-backed execution guard for scheduled jobs."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        lease_seconds: float = 300.0,
        clock: Callable[[], float] = time.time,
        worker_id: Optional[str] = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.lease_seconds = float(lease_seconds)
        self._clock = clock
        self.worker_id = worker_id or f"worker-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self._init_schema()

    @contextlib.contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=5.0, isolation_level=None)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
            yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cron_executions (
                    execution_key TEXT PRIMARY KEY,
                    job_name TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
                    worker_id TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 1,
                    started_at REAL NOT NULL,
                    lease_expires_at REAL NOT NULL,
                    finished_at REAL,
                    result_summary TEXT,
                    last_error TEXT
                );
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cron_job_status ON cron_executions(job_name, status);"
            )

    def run_once(
        self,
        job_name: str,
        execution_key: str,
        fn: Callable[[], T],
    ) -> CronRunOutcome:
        """Execute `fn` only if `execution_key` has not completed and has no live lease."""
        start_ts = self._clock()
        lease_until = start_ts + self.lease_seconds
        attempt_count = 0

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                row = conn.execute(
                    "SELECT * FROM cron_executions WHERE execution_key = ?",
                    (execution_key,),
                ).fetchone()

                if row is not None:
                    if row["status"] == "completed":
                        conn.execute("COMMIT;")
                        return CronRunOutcome(
                            job_name=job_name,
                            execution_key=execution_key,
                            status="skipped_already_completed",
                            attempt_count=int(row["attempt_count"]),
                            duration_ms=0.0,
                            worker_id=str(row["worker_id"]),
                            result_summary=row["result_summary"],
                        )
                    if row["status"] == "running" and float(row["lease_expires_at"]) > start_ts:
                        conn.execute("COMMIT;")
                        return CronRunOutcome(
                            job_name=job_name,
                            execution_key=execution_key,
                            status="skipped_active_lease",
                            attempt_count=int(row["attempt_count"]),
                            duration_ms=0.0,
                            worker_id=str(row["worker_id"]),
                        )
                    attempt_count = int(row["attempt_count"]) + 1
                    conn.execute(
                        """
                        UPDATE cron_executions
                           SET status = 'running',
                               worker_id = ?,
                               attempt_count = ?,
                               started_at = ?,
                               lease_expires_at = ?,
                               finished_at = NULL
                         WHERE execution_key = ?
                        """,
                        (self.worker_id, attempt_count, start_ts, lease_until, execution_key),
                    )
                else:
                    attempt_count = 1
                    conn.execute(
                        """
                        INSERT INTO cron_executions (
                            execution_key, job_name, status, worker_id,
                            attempt_count, started_at, lease_expires_at
                        ) VALUES (?, ?, 'running', ?, 1, ?, ?)
                        """,
                        (execution_key, job_name, self.worker_id, start_ts, lease_until),
                    )
                conn.execute("COMMIT;")
            except Exception:
                conn.execute("ROLLBACK;")
                raise

        try:
            result = fn()
            end_ts = self._clock()
            summary = json.dumps(result, default=str)[:500] if result is not None else "ok"
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE cron_executions
                       SET status = 'completed',
                           finished_at = ?,
                           lease_expires_at = ?,
                           result_summary = ?,
                           last_error = NULL
                     WHERE execution_key = ? AND worker_id = ?
                    """,
                    (end_ts, end_ts, summary, execution_key, self.worker_id),
                )
            return CronRunOutcome(
                job_name=job_name,
                execution_key=execution_key,
                status="completed",
                attempt_count=attempt_count,
                duration_ms=round(max(0.0, (end_ts - start_ts) * 1000.0), 3),
                worker_id=self.worker_id,
                result_summary=summary,
            )
        except Exception as exc:
            end_ts = self._clock()
            err_text = f"{type(exc).__name__}: {exc}"
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE cron_executions
                       SET status = 'failed',
                           finished_at = ?,
                           lease_expires_at = ?,
                           last_error = ?
                     WHERE execution_key = ? AND worker_id = ?
                    """,
                    (end_ts, end_ts, err_text[:500], execution_key, self.worker_id),
                )
            raise


def _run_demo() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        db_file = Path(tmp) / "cron_state.db"
        runner = IdempotentCronRunner(db_file, lease_seconds=60.0)
        key = compute_window_key("invoice-reconcile", cadence="hourly", now_epoch=1760000000.0)
        counter = {"calls": 0}

        def task() -> dict[str, int]:
            counter["calls"] += 1
            return {"processed_rows": 42, "invocations": counter["calls"]}

        first = runner.run_once("invoice-reconcile", key, task)
        second = runner.run_once("invoice-reconcile", key, task)
        print(
            json.dumps(
                {
                    "first_run": asdict(first),
                    "duplicate_trigger": asdict(second),
                    "side_effect_count": counter["calls"],
                },
                indent=2,
            )
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="SQLite WAL idempotent cron job runner.")
    parser.add_argument("--demo", action="store_true", help="Run a self-contained idempotency demonstration.")
    args = parser.parse_args()
    if args.demo:
        return _run_demo()
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
