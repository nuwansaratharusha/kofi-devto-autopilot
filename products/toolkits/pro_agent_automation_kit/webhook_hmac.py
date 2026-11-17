#!/usr/bin/env python3
"""Constant-time SHA-256 HMAC Webhook Signature Verifier & Replay Nonce Guard."""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import sqlite3
import time
from pathlib import Path
from typing import Callable, Iterator, Optional


class WebhookVerificationError(ValueError):
    """Raised when an incoming webhook fails signature, timestamp, or replay checks."""


def sign_webhook_payload(
    secret: str | bytes,
    payload_bytes: bytes,
    *,
    timestamp: Optional[int] = None,
    nonce: str = "evt_default",
) -> str:
    """Create header `t=<epoch>,id=<nonce>,v1=<hex_hmac_sha256>`."""
    ts = int(time.time()) if timestamp is None else int(timestamp)
    key = secret.encode("utf-8") if isinstance(secret, str) else secret
    signed_content = f"{ts}.{nonce}.".encode("utf-8") + payload_bytes
    digest = hmac.new(key, signed_content, hashlib.sha256).hexdigest()
    return f"t={ts},id={nonce},v1={digest}"


class WebhookReplayGuard:
    """Verifies constant-time HMAC signatures and rejects replay attacks via SQLite WAL."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        tolerance_seconds: int = 300,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.tolerance_seconds = max(1, int(tolerance_seconds))
        self._clock = clock
        self._init_schema()

    @contextlib.contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=5.0, isolation_level=None)
        try:
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
                CREATE TABLE IF NOT EXISTS webhook_nonces (
                    nonce TEXT PRIMARY KEY,
                    received_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                );
                """
            )

    def verify_and_record(
        self,
        secret: str | bytes,
        signature_header: str,
        payload_bytes: bytes,
    ) -> dict[str, object]:
        parts: dict[str, str] = {}
        for item in signature_header.split(","):
            if "=" in item:
                k, v = item.strip().split("=", 1)
                parts[k] = v

        if "t" not in parts or "v1" not in parts or "id" not in parts:
            raise WebhookVerificationError("Malformed signature header; expected t, id, and v1")

        try:
            ts = int(parts["t"])
        except ValueError as exc:
            raise WebhookVerificationError("Invalid timestamp in signature header") from exc

        nonce = parts["id"].strip()
        provided_sig = parts["v1"].strip()
        if not nonce or not provided_sig:
            raise WebhookVerificationError("Empty nonce or signature digest")

        now = self._clock()
        if abs(now - ts) > self.tolerance_seconds:
            raise WebhookVerificationError(
                f"Webhook timestamp outside {self.tolerance_seconds}s replay tolerance"
            )

        key = secret.encode("utf-8") if isinstance(secret, str) else secret
        signed_content = f"{ts}.{nonce}.".encode("utf-8") + payload_bytes
        expected_sig = hmac.new(key, signed_content, hashlib.sha256).hexdigest()

        if not hmac.compare_digest(expected_sig, provided_sig):
            raise WebhookVerificationError("HMAC-SHA256 signature mismatch")

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                conn.execute("DELETE FROM webhook_nonces WHERE expires_at <= ?", (now,))
                existing = conn.execute(
                    "SELECT 1 FROM webhook_nonces WHERE nonce = ?",
                    (nonce,),
                ).fetchone()
                if existing is not None:
                    conn.execute("ROLLBACK;")
                    raise WebhookVerificationError(f"Duplicate replay nonce rejected: {nonce}")

                conn.execute(
                    "INSERT INTO webhook_nonces (nonce, received_at, expires_at) VALUES (?, ?, ?)",
                    (nonce, now, now + (self.tolerance_seconds * 2)),
                )
                conn.execute("COMMIT;")
            except WebhookVerificationError:
                raise
            except Exception:
                conn.execute("ROLLBACK;")
                raise

        return {"verified": True, "nonce": nonce, "timestamp": ts}
