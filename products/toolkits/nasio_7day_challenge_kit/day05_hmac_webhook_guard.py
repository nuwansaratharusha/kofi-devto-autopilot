#!/usr/bin/env python3
"""Day 5 Lab: Constant-Time SHA-256 HMAC Webhook Verifier, Dual-Key Rotation & Replay Dedup."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Iterable, Optional


class WebhookVerificationError(PermissionError):
    """Raised when signature verification, timestamp bounds, or replay deduplication fails."""


def sign_webhook_payload(secret: str, timestamp: int, raw_body: bytes) -> str:
    """Compute canonical `v1=<hex>` SHA-256 HMAC over `<timestamp>.<raw_body>`."""
    if not secret:
        raise ValueError("secret must be non-empty")
    signed_bytes = f"{int(timestamp)}.".encode("ascii") + raw_body
    digest = hmac.new(secret.encode("utf-8"), signed_bytes, hashlib.sha256).hexdigest()
    return f"v1={digest}"


class ReplayProtectedWebhookGuard:
    """Verifies constant-time HMAC across active/retiring secrets + SQLite nonce deduplication."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        secrets: Iterable[str],
        tolerance_seconds: int = 300,
    ) -> None:
        clean_secrets = [s.strip() for s in secrets if s and s.strip()]
        if not clean_secrets:
            raise ValueError("At least one signing secret is required")
        self.secrets = tuple(clean_secrets)
        self.tolerance_seconds = int(tolerance_seconds)
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=5.0, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def _init_schema(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS seen_webhook_events (
                    event_id TEXT PRIMARY KEY,
                    received_at INTEGER NOT NULL,
                    payload_sha256 TEXT NOT NULL
                );
                """
            )
        finally:
            conn.close()

    def verify_and_record(
        self,
        *,
        event_id: str,
        timestamp: int,
        signature_header: str,
        raw_body: bytes,
        now_epoch: Optional[int] = None,
    ) -> dict[str, object]:
        if not event_id.strip():
            raise WebhookVerificationError("missing_event_id")
        now_ts = int(time.time()) if now_epoch is None else int(now_epoch)
        if abs(now_ts - int(timestamp)) > self.tolerance_seconds:
            raise WebhookVerificationError("timestamp_outside_replay_window")

        provided = signature_header.strip()
        if not provided.startswith("v1="):
            raise WebhookVerificationError("malformed_signature_scheme")

        matched = False
        provided_bytes = provided.encode("utf-8", errors="surrogateescape")
        for sec in self.secrets:
            expected = sign_webhook_payload(sec, int(timestamp), raw_body)
            if hmac.compare_digest(provided_bytes, expected.encode("ascii")):
                matched = True
                break
        if not matched:
            raise WebhookVerificationError("invalid_hmac_signature")

        body_hash = hashlib.sha256(raw_body).hexdigest()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE;")
            # Prune expired nonces older than 2x replay tolerance
            conn.execute(
                "DELETE FROM seen_webhook_events WHERE received_at < ?",
                (now_ts - (self.tolerance_seconds * 2),),
            )
            cur = conn.execute(
                "INSERT OR IGNORE INTO seen_webhook_events (event_id, received_at, payload_sha256) VALUES (?, ?, ?)",
                (event_id.strip(), now_ts, body_hash),
            )
            conn.execute("COMMIT;")
            if cur.rowcount == 0:
                return {"verified": True, "duplicate": True, "event_id": event_id.strip()}
            return {"verified": True, "duplicate": False, "event_id": event_id.strip()}
        except Exception:
            conn.execute("ROLLBACK;")
            raise
        finally:
            conn.close()


def run_demo() -> dict[str, object]:
    with tempfile.TemporaryDirectory() as tmp:
        guard = ReplayProtectedWebhookGuard(
            Path(tmp) / "webhooks.db",
            secrets=["new_primary_secret_2026", "retiring_old_secret_2025"],
            tolerance_seconds=300,
        )
        now_ts = 1_763_650_000
        body = b'{"order_id":"nas_901","gross_cents":1500}'
        # Signed with retiring secret during zero-downtime key rollover
        sig = sign_webhook_payload("retiring_old_secret_2025", now_ts, body)
        first = guard.verify_and_record(
            event_id="evt_nas_901",
            timestamp=now_ts,
            signature_header=sig,
            raw_body=body,
            now_epoch=now_ts + 5,
        )
        replay = guard.verify_and_record(
            event_id="evt_nas_901",
            timestamp=now_ts,
            signature_header=sig,
            raw_body=body,
            now_epoch=now_ts + 8,
        )
        return {"day": 5, "first_delivery": first, "replay_delivery": replay}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Day 5 HMAC Webhook Guard Lab")
    parser.add_argument("--demo", action="store_true", help="Run dual-key HMAC & replay guard demo")
    parser.parse_args()
    print(json.dumps(run_demo(), indent=2))
