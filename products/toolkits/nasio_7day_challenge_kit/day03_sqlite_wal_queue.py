#!/usr/bin/env python3
"""Day 3 Lab: Transactional SQLite WAL Background Queue With Leases & Dead-Letter Quarantine."""

from __future__ import annotations

import argparse
import contextlib
import json
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional


@dataclass(frozen=True)
class LeasedJob:
    job_id: str
    idempotency_key: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int
    lease_expires_at: float


class SqliteWalQueue:
    """Zero-dependency transactional job queue backed by SQLite WAL + `BEGIN IMMEDIATE`."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
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
                CREATE TABLE IF NOT EXISTS wal_jobs (
                    job_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('queued', 'leased', 'completed', 'dead_letter')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    available_at REAL NOT NULL,
                    lease_expires_at REAL,
                    last_error TEXT NOT NULL DEFAULT ''
                );
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_wal_jobs_ready ON wal_jobs(status, available_at);"
            )

    def enqueue(
        self,
        job_id: str,
        *,
        idempotency_key: str,
        payload: dict[str, Any],
        max_attempts: int = 3,
        now: Optional[float] = None,
    ) -> bool:
        if not job_id.strip() or not idempotency_key.strip():
            raise ValueError("job_id and idempotency_key must be non-empty")
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        ts = time.time() if now is None else float(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO wal_jobs (
                        job_id, idempotency_key, payload_json, status, max_attempts, available_at
                    ) VALUES (?, ?, ?, 'queued', ?, ?)
                    """,
                    (job_id.strip(), idempotency_key.strip(), json.dumps(payload), int(max_attempts), ts),
                )
                conn.execute("COMMIT;")
                return cur.rowcount == 1
            except Exception:
                conn.execute("ROLLBACK;")
                raise

    def claim(self, *, lease_seconds: float = 30.0, now: Optional[float] = None) -> Optional[LeasedJob]:
        ts = time.time() if now is None else float(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                conn.execute(
                    """
                    UPDATE wal_jobs
                       SET status = 'dead_letter', lease_expires_at = NULL,
                           last_error = CASE WHEN last_error = '' THEN 'lease_expired_max_attempts' ELSE last_error END
                     WHERE status = 'leased' AND lease_expires_at <= ? AND attempts >= max_attempts
                    """,
                    (ts,),
                )
                row = conn.execute(
                    """
                    SELECT job_id FROM wal_jobs
                     WHERE (status = 'queued' AND available_at <= ?)
                        OR (status = 'leased' AND lease_expires_at <= ? AND attempts < max_attempts)
                     ORDER BY available_at ASC, rowid ASC
                     LIMIT 1
                    """,
                    (ts, ts),
                ).fetchone()
                if row is None:
                    conn.execute("COMMIT;")
                    return None
                expires = ts + float(lease_seconds)
                conn.execute(
                    """
                    UPDATE wal_jobs
                       SET status = 'leased', attempts = attempts + 1, lease_expires_at = ?
                     WHERE job_id = ?
                    """,
                    (expires, str(row["job_id"])),
                )
                claimed = conn.execute(
                    "SELECT * FROM wal_jobs WHERE job_id = ?", (str(row["job_id"]),)
                ).fetchone()
                conn.execute("COMMIT;")
                assert claimed is not None
                return LeasedJob(
                    job_id=str(claimed["job_id"]),
                    idempotency_key=str(claimed["idempotency_key"]),
                    payload=dict(json.loads(str(claimed["payload_json"]))),
                    attempts=int(claimed["attempts"]),
                    max_attempts=int(claimed["max_attempts"]),
                    lease_expires_at=float(claimed["lease_expires_at"]),
                )
            except Exception:
                conn.execute("ROLLBACK;")
                raise

    def ack(self, job_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE wal_jobs SET status = 'completed', lease_expires_at = NULL WHERE job_id = ?",
                (job_id,),
            )

    def nack(self, job_id: str, *, error: str, backoff_seconds: float = 2.0, now: Optional[float] = None) -> str:
        ts = time.time() if now is None else float(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                row = conn.execute(
                    "SELECT attempts, max_attempts FROM wal_jobs WHERE job_id = ?",
                    (job_id,),
                ).fetchone()
                if row is None:
                    conn.execute("COMMIT;")
                    raise KeyError(f"Unknown job_id: {job_id}")
                if int(row["attempts"]) >= int(row["max_attempts"]):
                    conn.execute(
                        "UPDATE wal_jobs SET status = 'dead_letter', lease_expires_at = NULL, last_error = ? WHERE job_id = ?",
                        (error, job_id),
                    )
                    conn.execute("COMMIT;")
                    return "dead_letter"
                conn.execute(
                    "UPDATE wal_jobs SET status = 'queued', available_at = ?, lease_expires_at = NULL, last_error = ? WHERE job_id = ?",
                    (ts + float(backoff_seconds), error, job_id),
                )
                conn.execute("COMMIT;")
                return "requeued"
            except Exception:
                conn.execute("ROLLBACK;")
                raise

    def counts(self) -> dict[str, int]:
        out = {"queued": 0, "leased": 0, "completed": 0, "dead_letter": 0}
        with self._connect() as conn:
            for row in conn.execute("SELECT status, COUNT(*) AS n FROM wal_jobs GROUP BY status"):
                out[str(row["status"])] = int(row["n"])
        return out


def run_demo() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        q = SqliteWalQueue(Path(tmp) / "day03.db")
        now = 1_763_600_000.0
        q.enqueue("j1", idempotency_key="ord_100", payload={"amount_cents": 1500}, max_attempts=2, now=now)
        dup_ok = q.enqueue("j1_dup", idempotency_key="ord_100", payload={"amount_cents": 1500}, now=now)
        q.enqueue("j_poison", idempotency_key="ord_bad", payload={"corrupt": True}, max_attempts=2, now=now)

        first = q.claim(now=now)
        assert first is not None and first.job_id == "j1"
        q.ack(first.job_id)

        for tick in range(2):
            bad = q.claim(now=now + tick * 5.0)
            assert bad is not None
            q.nack(bad.job_id, error="schema_error", backoff_seconds=1.0, now=now + tick * 5.0)

        return {"day": 3, "duplicate_inserted": dup_ok, "counts": q.counts()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Day 3 SQLite WAL Queue Lab")
    parser.add_argument("--demo", action="store_true", help="Run deterministic SQLite WAL queue demo")
    parser.parse_args()
    print(json.dumps(run_demo(), indent=2))
