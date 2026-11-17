#!/usr/bin/env python3
"""Transactional SQLite WAL Job Queue with Visibility Leases & Dead-Letter CLI.

Replaces external brokers (Redis/RabbitMQ) for single-node or persistent-volume
workloads up to thousands of atomic leases per second with zero third-party deps.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sqlite3
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Optional


@dataclass(frozen=True)
class QueuedJob:
    job_id: str
    topic: str
    idempotency_key: str
    payload: Any
    status: str  # "queued" | "leased" | "completed" | "dead_letter"
    attempts: int
    max_attempts: int
    available_at: float
    lease_owner: Optional[str]
    lease_expires_at: Optional[float]
    last_error: Optional[str]


class SqliteWalQueue:
    """Atomic visibility-timeout queue backed by SQLite Write-Ahead Logging."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._init_schema()

    @contextlib.contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=5.0, isolation_level=None)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
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
                    topic TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 100,
                    status TEXT NOT NULL CHECK (status IN ('queued', 'leased', 'completed', 'dead_letter')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    available_at REAL NOT NULL,
                    lease_owner TEXT,
                    lease_expires_at REAL,
                    last_error TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_wal_jobs_claim
                ON wal_jobs(topic, status, available_at, priority);
                """
            )

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> QueuedJob:
        return QueuedJob(
            job_id=str(row["job_id"]),
            topic=str(row["topic"]),
            idempotency_key=str(row["idempotency_key"]),
            payload=json.loads(row["payload_json"]),
            status=str(row["status"]),
            attempts=int(row["attempts"]),
            max_attempts=int(row["max_attempts"]),
            available_at=float(row["available_at"]),
            lease_owner=row["lease_owner"],
            lease_expires_at=float(row["lease_expires_at"]) if row["lease_expires_at"] is not None else None,
            last_error=row["last_error"],
        )

    def enqueue(
        self,
        topic: str,
        payload: Any,
        *,
        idempotency_key: Optional[str] = None,
        priority: int = 100,
        max_attempts: int = 3,
        delay_seconds: float = 0.0,
    ) -> QueuedJob:
        """Idempotently enqueue a job; returns the existing job if `idempotency_key` matches."""
        now = self._clock()
        job_id = f"job_{uuid.uuid4().hex[:16]}"
        idem = idempotency_key or job_id
        payload_json = json.dumps(payload, sort_keys=True)
        avail = now + max(0.0, delay_seconds)

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                existing = conn.execute(
                    "SELECT * FROM wal_jobs WHERE idempotency_key = ?",
                    (idem,),
                ).fetchone()
                if existing is not None:
                    conn.execute("COMMIT;")
                    return self._row_to_job(existing)

                conn.execute(
                    """
                    INSERT INTO wal_jobs (
                        job_id, topic, idempotency_key, payload_json, priority,
                        status, attempts, max_attempts, available_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?, ?)
                    """,
                    (job_id, topic, idem, payload_json, priority, max(1, max_attempts), avail, now, now),
                )
                created = conn.execute("SELECT * FROM wal_jobs WHERE job_id = ?", (job_id,)).fetchone()
                conn.execute("COMMIT;")
                assert created is not None
                return self._row_to_job(created)
            except Exception:
                conn.execute("ROLLBACK;")
                raise

    def claim(
        self,
        topic: str,
        *,
        worker_id: str = "worker-default",
        visibility_timeout: float = 30.0,
    ) -> Optional[QueuedJob]:
        """Atomically claim one ready job (or reclaim an expired lease) for `topic`."""
        now = self._clock()
        lease_until = now + max(1.0, visibility_timeout)

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                # Promote expired leases that exhausted max_attempts to dead_letter first
                conn.execute(
                    """
                    UPDATE wal_jobs
                       SET status = 'dead_letter',
                           lease_owner = NULL,
                           lease_expires_at = NULL,
                           last_error = COALESCE(last_error, 'visibility_timeout_exceeded'),
                           updated_at = ?
                     WHERE topic = ?
                       AND status = 'leased'
                       AND lease_expires_at <= ?
                       AND attempts >= max_attempts
                    """,
                    (now, topic, now),
                )

                candidate = conn.execute(
                    """
                    SELECT job_id FROM wal_jobs
                     WHERE topic = ?
                       AND (
                           (status = 'queued' AND available_at <= ?)
                           OR (status = 'leased' AND lease_expires_at <= ? AND attempts < max_attempts)
                       )
                     ORDER BY priority ASC, available_at ASC, created_at ASC
                     LIMIT 1
                    """,
                    (topic, now, now),
                ).fetchone()

                if candidate is None:
                    conn.execute("COMMIT;")
                    return None

                jid = candidate["job_id"]
                conn.execute(
                    """
                    UPDATE wal_jobs
                       SET status = 'leased',
                           attempts = attempts + 1,
                           lease_owner = ?,
                           lease_expires_at = ?,
                           updated_at = ?
                     WHERE job_id = ?
                    """,
                    (worker_id, lease_until, now, jid),
                )
                claimed = conn.execute("SELECT * FROM wal_jobs WHERE job_id = ?", (jid,)).fetchone()
                conn.execute("COMMIT;")
                assert claimed is not None
                return self._row_to_job(claimed)
            except Exception:
                conn.execute("ROLLBACK;")
                raise

    def ack(self, job_id: str, *, worker_id: Optional[str] = None) -> bool:
        now = self._clock()
        with self._connect() as conn:
            if worker_id:
                cur = conn.execute(
                    """
                    UPDATE wal_jobs
                       SET status = 'completed', lease_owner = NULL, lease_expires_at = NULL, updated_at = ?
                     WHERE job_id = ? AND status = 'leased' AND lease_owner = ?
                    """,
                    (now, job_id, worker_id),
                )
            else:
                cur = conn.execute(
                    """
                    UPDATE wal_jobs
                       SET status = 'completed', lease_owner = NULL, lease_expires_at = NULL, updated_at = ?
                     WHERE job_id = ? AND status = 'leased'
                    """,
                    (now, job_id),
                )
            return cur.rowcount == 1

    def nack(self, job_id: str, *, error: str, base_backoff: float = 2.0) -> QueuedJob:
        """Release a failed job with exponential backoff, or quarantine in `dead_letter`."""
        now = self._clock()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                row = conn.execute("SELECT * FROM wal_jobs WHERE job_id = ?", (job_id,)).fetchone()
                if row is None:
                    conn.execute("ROLLBACK;")
                    raise KeyError(f"Unknown job_id: {job_id}")

                attempts = int(row["attempts"])
                max_attempts = int(row["max_attempts"])
                if attempts >= max_attempts:
                    conn.execute(
                        """
                        UPDATE wal_jobs
                           SET status = 'dead_letter',
                               lease_owner = NULL,
                               lease_expires_at = NULL,
                               last_error = ?,
                               updated_at = ?
                         WHERE job_id = ?
                        """,
                        (error[:500], now, job_id),
                    )
                else:
                    next_avail = now + (base_backoff * (2 ** max(0, attempts - 1)))
                    conn.execute(
                        """
                        UPDATE wal_jobs
                           SET status = 'queued',
                               available_at = ?,
                               lease_owner = NULL,
                               lease_expires_at = NULL,
                               last_error = ?,
                               updated_at = ?
                         WHERE job_id = ?
                        """,
                        (next_avail, error[:500], now, job_id),
                    )
                updated = conn.execute("SELECT * FROM wal_jobs WHERE job_id = ?", (job_id,)).fetchone()
                conn.execute("COMMIT;")
                assert updated is not None
                return self._row_to_job(updated)
            except Exception:
                conn.execute("ROLLBACK;")
                raise

    def requeue_dead_letters(self, topic: Optional[str] = None) -> int:
        """Replay quarantined `dead_letter` jobs by resetting attempts and setting `queued`."""
        now = self._clock()
        with self._connect() as conn:
            if topic:
                cur = conn.execute(
                    """
                    UPDATE wal_jobs
                       SET status = 'queued', attempts = 0, available_at = ?, updated_at = ?
                     WHERE status = 'dead_letter' AND topic = ?
                    """,
                    (now, now, topic),
                )
            else:
                cur = conn.execute(
                    """
                    UPDATE wal_jobs
                       SET status = 'queued', attempts = 0, available_at = ?, updated_at = ?
                     WHERE status = 'dead_letter'
                    """,
                    (now, now),
                )
            return int(cur.rowcount)

    def stats(self) -> dict[str, int]:
        counts = {"queued": 0, "leased": 0, "completed": 0, "dead_letter": 0}
        with self._connect() as conn:
            for row in conn.execute("SELECT status, COUNT(*) AS n FROM wal_jobs GROUP BY status"):
                counts[str(row["status"])] = int(row["n"])
        counts["total"] = sum(counts.values())
        return counts


def _run_demo() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        q = SqliteWalQueue(Path(tmp) / "jobs.db")
        j1 = q.enqueue("webhooks", {"event": "order.paid", "amount": 1400}, idempotency_key="evt_001")
        _dup = q.enqueue("webhooks", {"event": "order.paid", "amount": 1400}, idempotency_key="evt_001")
        claimed = q.claim("webhooks", worker_id=f"demo-{os.getpid()}")
        assert claimed is not None and claimed.job_id == j1.job_id
        q.ack(claimed.job_id, worker_id=f"demo-{os.getpid()}")
        print(json.dumps({"claimed_job": asdict(claimed), "queue_stats": q.stats()}, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Transactional SQLite WAL Queue & Dead-Letter CLI")
    parser.add_argument("--db", default="./wal_queue.db", help="Path to SQLite WAL database")
    parser.add_argument("--demo", action="store_true", help="Run self-contained queue benchmark demo")
    sub = parser.add_subparsers(dest="cmd")

    p_enq = sub.add_parser("enqueue", help="Enqueue a JSON job")
    p_enq.add_argument("--topic", required=True)
    p_enq.add_argument("--payload", required=True, help="JSON string payload")
    p_enq.add_argument("--key", default=None, help="Idempotency key")

    p_stats = sub.add_parser("stats", help="Show queue counts by status")
    _ = p_stats

    p_dlq = sub.add_parser("requeue-dlq", help="Replay dead-lettered jobs")
    p_dlq.add_argument("--topic", default=None)

    args = parser.parse_args()
    if args.demo:
        return _run_demo()

    queue = SqliteWalQueue(args.db)
    if args.cmd == "enqueue":
        job = queue.enqueue(args.topic, json.loads(args.payload), idempotency_key=args.key)
        print(json.dumps(asdict(job), indent=2))
        return 0
    if args.cmd == "stats":
        print(json.dumps(queue.stats(), indent=2))
        return 0
    if args.cmd == "requeue-dlq":
        replayed = queue.requeue_dead_letters(args.topic)
        print(json.dumps({"requeued": replayed, "stats": queue.stats()}, indent=2))
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
