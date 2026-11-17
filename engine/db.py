"""SQLite WAL state ledger for signals, verified articles, and publish runs."""

from __future__ import annotations

import contextlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Iterable, Optional

from engine.models import ArticleRecord, TopicSignal


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class PipelineDatabase:
    """Durable SQLite WAL ledger preventing duplicate topics and duplicate API posts."""

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
            conn.execute("PRAGMA foreign_keys=ON;")
            conn.execute("PRAGMA busy_timeout=5000;")
            yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fingerprint TEXT NOT NULL UNIQUE,
                    pillar TEXT NOT NULL,
                    blueprint_key TEXT NOT NULL,
                    title TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    score INTEGER NOT NULL DEFAULT 0,
                    tags_json TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    discovered_at TEXT NOT NULL,
                    used_at TEXT
                );
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_signals_unused ON signals(pillar, used_at, score DESC);"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS articles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    slug TEXT NOT NULL UNIQUE,
                    signal_fingerprint TEXT NOT NULL REFERENCES signals(fingerprint),
                    pillar TEXT NOT NULL,
                    scheduled_slot TEXT NOT NULL,
                    title TEXT NOT NULL,
                    subtitle TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    canonical_url TEXT NOT NULL,
                    markdown_body TEXT NOT NULL,
                    word_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    verification_json TEXT NOT NULL DEFAULT '{}',
                    devto_article_id INTEGER,
                    devto_url TEXT,
                    published_mode TEXT NOT NULL DEFAULT 'draft',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status, created_at DESC);"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS publish_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL UNIQUE,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    articles_verified INTEGER NOT NULL DEFAULT 0,
                    articles_staged INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}'
                );
                """
            )

    def upsert_signals(self, signals: Iterable[TopicSignal]) -> int:
        """Insert newly discovered signals; ignores duplicates by `fingerprint`."""
        now = utc_now_iso()
        inserted = 0
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                for raw_sig in signals:
                    sig = raw_sig.with_defaults(now)
                    cur = conn.execute(
                        """
                        INSERT OR IGNORE INTO signals (
                            fingerprint, pillar, blueprint_key, title, source,
                            source_url, score, tags_json, summary, discovered_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            sig.fingerprint,
                            sig.pillar,
                            sig.blueprint_key,
                            sig.title,
                            sig.source,
                            sig.source_url,
                            sig.score,
                            json.dumps(sig.tags),
                            sig.summary,
                            sig.discovered_at,
                        ),
                    )
                    if cur.rowcount == 1:
                        inserted += 1
                conn.execute("COMMIT;")
            except Exception:
                conn.execute("ROLLBACK;")
                raise
        return inserted

    @staticmethod
    def _row_to_signal(row: sqlite3.Row) -> TopicSignal:
        return TopicSignal(
            pillar=str(row["pillar"]),
            title=str(row["title"]),
            source=str(row["source"]),
            source_url=str(row["source_url"]),
            score=int(row["score"]),
            tags=list(json.loads(row["tags_json"])),
            summary=str(row["summary"]),
            blueprint_key=str(row["blueprint_key"]),
            fingerprint=str(row["fingerprint"]),
            discovered_at=str(row["discovered_at"]),
        )

    def select_unused_signals(
        self,
        *,
        limit: int = 3,
        pillar_filter: Optional[str] = None,
    ) -> list[TopicSignal]:
        """Pick up to `limit` unused signals rotating cleanly across Mon/Wed/Fri pillars."""
        ring = ["pillar_a", "pillar_b", "pillar_c"]
        selected: list[TopicSignal] = []
        chosen_fps: set[str] = set()

        with self._connect() as conn:
            if pillar_filter:
                pillars = [pillar_filter]
            else:
                last_used_row = conn.execute(
                    """
                    SELECT pillar FROM signals
                     WHERE used_at IS NOT NULL
                     ORDER BY used_at DESC, id DESC
                     LIMIT 1
                    """
                ).fetchone()
                if last_used_row is not None and str(last_used_row["pillar"]) in ring:
                    next_idx = (ring.index(str(last_used_row["pillar"])) + 1) % len(ring)
                    pillars = ring[next_idx:] + ring[:next_idx]
                else:
                    pillars = list(ring)

            while len(selected) < limit:
                progress = False
                for p in pillars:
                    if len(selected) >= limit:
                        break
                    placeholders = ",".join("?" for _ in chosen_fps)
                    not_in_clause = f"AND fingerprint NOT IN ({placeholders})" if chosen_fps else ""
                    query = f"""
                        SELECT * FROM signals
                         WHERE pillar = ?
                           AND used_at IS NULL
                           {not_in_clause}
                         ORDER BY score DESC, id ASC
                         LIMIT 1
                    """
                    params: list[Any] = [p, *chosen_fps]
                    row = conn.execute(query, params).fetchone()
                    if row is not None:
                        sig = self._row_to_signal(row)
                        selected.append(sig)
                        chosen_fps.add(sig.fingerprint)
                        progress = True
                if not progress:
                    break
        return selected

    def mark_signal_used(self, fingerprint: str) -> None:
        precise_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        with self._connect() as conn:
            conn.execute(
                "UPDATE signals SET used_at = COALESCE(used_at, ?) WHERE fingerprint = ?",
                (precise_iso, fingerprint),
            )

    @staticmethod
    def _row_to_article(row: sqlite3.Row) -> ArticleRecord:
        return ArticleRecord(
            slug=str(row["slug"]),
            signal_fingerprint=str(row["signal_fingerprint"]),
            pillar=str(row["pillar"]),
            scheduled_slot=str(row["scheduled_slot"]),
            title=str(row["title"]),
            subtitle=str(row["subtitle"]),
            tags=list(json.loads(row["tags_json"])),
            canonical_url=str(row["canonical_url"]),
            markdown_body=str(row["markdown_body"]),
            word_count=int(row["word_count"]),
            status=str(row["status"]),
            verification=dict(json.loads(row["verification_json"])),
            devto_article_id=int(row["devto_article_id"]) if row["devto_article_id"] is not None else None,
            devto_url=str(row["devto_url"]) if row["devto_url"] is not None else None,
            published_mode=str(row["published_mode"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def save_article(self, article: ArticleRecord) -> ArticleRecord:
        now = utc_now_iso()
        created = article.created_at or now
        updated = now
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                existing = conn.execute(
                    "SELECT created_at, devto_article_id, devto_url FROM articles WHERE slug = ?",
                    (article.slug,),
                ).fetchone()
                if existing is not None:
                    created = str(existing["created_at"])
                    if article.devto_article_id is None and existing["devto_article_id"] is not None:
                        article.devto_article_id = int(existing["devto_article_id"])
                    if article.devto_url is None and existing["devto_url"] is not None:
                        article.devto_url = str(existing["devto_url"])

                conn.execute(
                    """
                    INSERT INTO articles (
                        slug, signal_fingerprint, pillar, scheduled_slot, title, subtitle,
                        tags_json, canonical_url, markdown_body, word_count, status,
                        verification_json, devto_article_id, devto_url, published_mode,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(slug) DO UPDATE SET
                        title = excluded.title,
                        subtitle = excluded.subtitle,
                        tags_json = excluded.tags_json,
                        canonical_url = excluded.canonical_url,
                        markdown_body = excluded.markdown_body,
                        word_count = excluded.word_count,
                        status = excluded.status,
                        verification_json = excluded.verification_json,
                        devto_article_id = excluded.devto_article_id,
                        devto_url = excluded.devto_url,
                        published_mode = excluded.published_mode,
                        updated_at = excluded.updated_at
                    """,
                    (
                        article.slug,
                        article.signal_fingerprint,
                        article.pillar,
                        article.scheduled_slot,
                        article.title,
                        article.subtitle,
                        json.dumps(article.tags),
                        article.canonical_url,
                        article.markdown_body,
                        article.word_count,
                        article.status,
                        json.dumps(article.verification),
                        article.devto_article_id,
                        article.devto_url,
                        article.published_mode,
                        created,
                        updated,
                    ),
                )
                row = conn.execute("SELECT * FROM articles WHERE slug = ?", (article.slug,)).fetchone()
                conn.execute("COMMIT;")
                assert row is not None
                return self._row_to_article(row)
            except Exception:
                conn.execute("ROLLBACK;")
                raise

    def get_article(self, slug: str) -> Optional[ArticleRecord]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM articles WHERE slug = ?", (slug,)).fetchone()
            return self._row_to_article(row) if row is not None else None

    def list_articles(self, *, verified_only: bool = False) -> list[ArticleRecord]:
        valid_statuses = ("code_verified", "staged_draft", "published_live")
        with self._connect() as conn:
            if verified_only:
                rows = conn.execute(
                    """
                    SELECT * FROM articles
                     WHERE status IN (?, ?, ?)
                     ORDER BY id ASC
                    """,
                    valid_statuses,
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM articles ORDER BY id ASC").fetchall()
            return [self._row_to_article(r) for r in rows]

    def record_publish_run(
        self,
        *,
        run_id: str,
        started_at: str,
        finished_at: str,
        mode: str,
        articles_verified: int,
        articles_staged: int,
        status: str,
        details: dict[str, Any],
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO publish_runs (
                    run_id, started_at, finished_at, mode,
                    articles_verified, articles_staged, status, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    started_at,
                    finished_at,
                    mode,
                    articles_verified,
                    articles_staged,
                    status,
                    json.dumps(details),
                ),
            )

    def summary_counts(self) -> dict[str, Any]:
        with self._connect() as conn:
            sig_total = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
            sig_unused = conn.execute("SELECT COUNT(*) FROM signals WHERE used_at IS NULL").fetchone()[0]
            art_total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
            art_verified = conn.execute(
                "SELECT COUNT(*) FROM articles WHERE status IN ('code_verified', 'staged_draft', 'published_live')"
            ).fetchone()[0]
            art_staged = conn.execute(
                "SELECT COUNT(*) FROM articles WHERE devto_article_id IS NOT NULL"
            ).fetchone()[0]
            runs_total = conn.execute("SELECT COUNT(*) FROM publish_runs").fetchone()[0]
        return {
            "signals_total": int(sig_total),
            "signals_unused": int(sig_unused),
            "articles_total": int(art_total),
            "articles_verified": int(art_verified),
            "articles_staged_remote": int(art_staged),
            "publish_runs_total": int(runs_total),
        }
