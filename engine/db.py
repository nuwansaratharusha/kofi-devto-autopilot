"""SQLite WAL state ledger for signals, verified articles, store orders, metrics & publish runs."""

from __future__ import annotations

import contextlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

from engine.models import (
    VALID_CHANNELS,
    ArticleRecord,
    ChannelMetricRecord,
    StoreOrderRecord,
    TopicSignal,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


ARTICLE_MIGRATION_COLUMNS: tuple[tuple[str, str], ...] = (
    ("series_day", "INTEGER"),
    ("blogger_post_id", "TEXT"),
    ("blogger_url", "TEXT"),
    ("pinterest_pin_id", "TEXT"),
    ("pinterest_url", "TEXT"),
    ("patreon_post_id", "TEXT"),
    ("patreon_url", "TEXT"),
)


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
                    updated_at TEXT NOT NULL,
                    series_day INTEGER,
                    blogger_post_id TEXT,
                    blogger_url TEXT,
                    pinterest_pin_id TEXT,
                    pinterest_url TEXT
                );
                """
            )
            existing_cols = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(articles);").fetchall()
            }
            for col_name, col_type in ARTICLE_MIGRATION_COLUMNS:
                if col_name not in existing_cols:
                    conn.execute(f"ALTER TABLE articles ADD COLUMN {col_name} {col_type};")

            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status, created_at DESC);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_articles_series_day ON articles(series_day);"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS store_orders (
                    storefront TEXT NOT NULL,
                    order_id TEXT NOT NULL,
                    sku TEXT NOT NULL DEFAULT '',
                    product_name TEXT NOT NULL DEFAULT '',
                    gross_cents INTEGER NOT NULL DEFAULT 0,
                    fee_cents INTEGER NOT NULL DEFAULT 0,
                    net_cents INTEGER NOT NULL DEFAULT 0,
                    currency TEXT NOT NULL DEFAULT 'USD',
                    refunded INTEGER NOT NULL DEFAULT 0,
                    utm_source TEXT NOT NULL DEFAULT '',
                    utm_medium TEXT NOT NULL DEFAULT '',
                    utm_campaign TEXT NOT NULL DEFAULT '',
                    attributed_slug TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    synced_at TEXT NOT NULL,
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (storefront, order_id)
                );
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_orders_utm ON store_orders(utm_source, refunded);"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS channel_metrics (
                    slug TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    views INTEGER NOT NULL DEFAULT 0,
                    clicks INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (slug, channel)
                );
                """
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
                         ORDER BY
                           CASE WHEN blueprint_key IN (
                               SELECT blueprint_key FROM signals WHERE used_at IS NOT NULL
                           ) THEN 1 ELSE 0 END ASC,
                           score DESC,
                           id ASC
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
        keys = row.keys()
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
            series_day=int(row["series_day"]) if "series_day" in keys and row["series_day"] is not None else None,
            blogger_post_id=str(row["blogger_post_id"]) if "blogger_post_id" in keys and row["blogger_post_id"] is not None else None,
            blogger_url=str(row["blogger_url"]) if "blogger_url" in keys and row["blogger_url"] is not None else None,
            pinterest_pin_id=str(row["pinterest_pin_id"]) if "pinterest_pin_id" in keys and row["pinterest_pin_id"] is not None else None,
            pinterest_url=str(row["pinterest_url"]) if "pinterest_url" in keys and row["pinterest_url"] is not None else None,
            patreon_post_id=str(row["patreon_post_id"]) if "patreon_post_id" in keys and row["patreon_post_id"] is not None else None,
            patreon_url=str(row["patreon_url"]) if "patreon_url" in keys and row["patreon_url"] is not None else None,
        )

    def save_article(self, article: ArticleRecord) -> ArticleRecord:
        now = utc_now_iso()
        created = article.created_at or now
        updated = now
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                existing = conn.execute(
                    """
                    SELECT created_at, devto_article_id, devto_url,
                           series_day, blogger_post_id, blogger_url,
                           pinterest_pin_id, pinterest_url,
                           patreon_post_id, patreon_url
                      FROM articles
                     WHERE slug = ?
                    """,
                    (article.slug,),
                ).fetchone()
                if existing is not None:
                    created = str(existing["created_at"])
                    if article.devto_article_id is None and existing["devto_article_id"] is not None:
                        article.devto_article_id = int(existing["devto_article_id"])
                    if article.devto_url is None and existing["devto_url"] is not None:
                        article.devto_url = str(existing["devto_url"])
                    if article.series_day is None and existing["series_day"] is not None:
                        article.series_day = int(existing["series_day"])
                    if article.blogger_post_id is None and existing["blogger_post_id"] is not None:
                        article.blogger_post_id = str(existing["blogger_post_id"])
                    if article.blogger_url is None and existing["blogger_url"] is not None:
                        article.blogger_url = str(existing["blogger_url"])
                    if article.pinterest_pin_id is None and existing["pinterest_pin_id"] is not None:
                        article.pinterest_pin_id = str(existing["pinterest_pin_id"])
                    if article.pinterest_url is None and existing["pinterest_url"] is not None:
                        article.pinterest_url = str(existing["pinterest_url"])
                    if article.patreon_post_id is None and existing["patreon_post_id"] is not None:
                        article.patreon_post_id = str(existing["patreon_post_id"])
                    if article.patreon_url is None and existing["patreon_url"] is not None:
                        article.patreon_url = str(existing["patreon_url"])

                conn.execute(
                    """
                    INSERT INTO articles (
                        slug, signal_fingerprint, pillar, scheduled_slot, title, subtitle,
                        tags_json, canonical_url, markdown_body, word_count, status,
                        verification_json, devto_article_id, devto_url, published_mode,
                        created_at, updated_at, series_day,
                        blogger_post_id, blogger_url, pinterest_pin_id, pinterest_url,
                        patreon_post_id, patreon_url
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        updated_at = excluded.updated_at,
                        series_day = excluded.series_day,
                        blogger_post_id = excluded.blogger_post_id,
                        blogger_url = excluded.blogger_url,
                        pinterest_pin_id = excluded.pinterest_pin_id,
                        pinterest_url = excluded.pinterest_url,
                        patreon_post_id = excluded.patreon_post_id,
                        patreon_url = excluded.patreon_url
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
                        article.series_day,
                        article.blogger_post_id,
                        article.blogger_url,
                        article.pinterest_pin_id,
                        article.pinterest_url,
                        article.patreon_post_id,
                        article.patreon_url,
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

    def get_article_by_series_day(self, series_day: int) -> Optional[ArticleRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM articles WHERE series_day = ? ORDER BY id ASC LIMIT 1",
                (int(series_day),),
            ).fetchone()
            return self._row_to_article(row) if row is not None else None

    def next_series_day(self) -> int:
        with self._connect() as conn:
            val = conn.execute("SELECT COALESCE(MAX(series_day), COUNT(*)) FROM articles").fetchone()[0]
            return int(val or 0) + 1

    def list_articles(self, *, verified_only: bool = False) -> list[ArticleRecord]:
        valid_statuses = ("code_verified", "staged_draft", "published_live")
        with self._connect() as conn:
            if verified_only:
                rows = conn.execute(
                    """
                    SELECT * FROM articles
                     WHERE status IN (?, ?, ?)
                     ORDER BY COALESCE(series_day, id) ASC, id ASC
                    """,
                    valid_statuses,
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM articles ORDER BY COALESCE(series_day, id) ASC, id ASC"
                ).fetchall()
            return [self._row_to_article(r) for r in rows]

    @staticmethod
    def _row_to_store_order(row: sqlite3.Row) -> StoreOrderRecord:
        return StoreOrderRecord(
            storefront=str(row["storefront"]),
            order_id=str(row["order_id"]),
            sku=str(row["sku"]),
            product_name=str(row["product_name"]),
            gross_cents=int(row["gross_cents"]),
            fee_cents=int(row["fee_cents"]),
            net_cents=int(row["net_cents"]),
            currency=str(row["currency"]),
            refunded=bool(int(row["refunded"])),
            utm_source=str(row["utm_source"]),
            utm_medium=str(row["utm_medium"]),
            utm_campaign=str(row["utm_campaign"]),
            attributed_slug=str(row["attributed_slug"]),
            created_at=str(row["created_at"]),
            synced_at=str(row["synced_at"]),
            raw_data=dict(json.loads(row["raw_json"] or "{}")),
        )

    def upsert_store_orders(self, orders: Iterable[StoreOrderRecord]) -> int:
        now = utc_now_iso()
        upserted = 0
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                known_slugs = {
                    str(r["slug"])
                    for r in conn.execute("SELECT slug FROM articles").fetchall()
                }
                for ord_rec in orders:
                    attr_slug = ord_rec.attributed_slug.strip()
                    if not attr_slug and ord_rec.utm_campaign:
                        cand = ord_rec.utm_campaign.strip()
                        if cand in known_slugs:
                            attr_slug = cand
                        else:
                            for ks in known_slugs:
                                if ks.startswith(cand) and len(cand) >= 12:
                                    attr_slug = ks
                                    break
                    net_val = 0 if ord_rec.refunded else int(ord_rec.net_cents)
                    conn.execute(
                        """
                        INSERT INTO store_orders (
                            storefront, order_id, sku, product_name,
                            gross_cents, fee_cents, net_cents, currency, refunded,
                            utm_source, utm_medium, utm_campaign, attributed_slug,
                            created_at, synced_at, raw_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(storefront, order_id) DO UPDATE SET
                            sku = excluded.sku,
                            product_name = excluded.product_name,
                            gross_cents = excluded.gross_cents,
                            fee_cents = excluded.fee_cents,
                            net_cents = excluded.net_cents,
                            currency = excluded.currency,
                            refunded = excluded.refunded,
                            utm_source = CASE WHEN excluded.utm_source != '' THEN excluded.utm_source ELSE store_orders.utm_source END,
                            utm_medium = CASE WHEN excluded.utm_medium != '' THEN excluded.utm_medium ELSE store_orders.utm_medium END,
                            utm_campaign = CASE WHEN excluded.utm_campaign != '' THEN excluded.utm_campaign ELSE store_orders.utm_campaign END,
                            attributed_slug = CASE WHEN excluded.attributed_slug != '' THEN excluded.attributed_slug ELSE store_orders.attributed_slug END,
                            synced_at = excluded.synced_at,
                            raw_json = excluded.raw_json
                        """,
                        (
                            ord_rec.storefront.strip().lower(),
                            str(ord_rec.order_id).strip(),
                            ord_rec.sku.strip(),
                            ord_rec.product_name.strip(),
                            int(ord_rec.gross_cents),
                            int(ord_rec.fee_cents),
                            net_val,
                            (ord_rec.currency or "USD").strip().upper(),
                            1 if ord_rec.refunded else 0,
                            ord_rec.utm_source.strip().lower(),
                            ord_rec.utm_medium.strip().lower(),
                            ord_rec.utm_campaign.strip(),
                            attr_slug,
                            ord_rec.created_at or now,
                            ord_rec.synced_at or now,
                            json.dumps(ord_rec.raw_data),
                        ),
                    )
                    upserted += 1
                conn.execute("COMMIT;")
            except Exception:
                conn.execute("ROLLBACK;")
                raise
        return upserted

    def list_store_orders(self, *, include_refunded: bool = True) -> list[StoreOrderRecord]:
        with self._connect() as conn:
            if include_refunded:
                rows = conn.execute(
                    "SELECT * FROM store_orders ORDER BY created_at ASC, storefront ASC, order_id ASC"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM store_orders WHERE refunded = 0 ORDER BY created_at ASC, storefront ASC, order_id ASC"
                ).fetchall()
            return [self._row_to_store_order(r) for r in rows]

    def upsert_channel_metrics(
        self,
        *,
        slug: str,
        channel: str,
        views: Optional[int] = None,
        clicks: Optional[int] = None,
    ) -> ChannelMetricRecord:
        norm_channel = channel.strip().lower()
        if norm_channel not in VALID_CHANNELS:
            raise ValueError(f"Invalid channel '{channel}'. Expected one of {VALID_CHANNELS}")
        if views is not None and views < 0:
            raise ValueError("views must be >= 0")
        if clicks is not None and clicks < 0:
            raise ValueError("clicks must be >= 0")

        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                existing = conn.execute(
                    "SELECT views, clicks FROM channel_metrics WHERE slug = ? AND channel = ?",
                    (slug, norm_channel),
                ).fetchone()
                eff_views = int(views) if views is not None else (int(existing["views"]) if existing else 0)
                eff_clicks = int(clicks) if clicks is not None else (int(existing["clicks"]) if existing else 0)

                conn.execute(
                    """
                    INSERT INTO channel_metrics (slug, channel, views, clicks, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(slug, channel) DO UPDATE SET
                        views = excluded.views,
                        clicks = excluded.clicks,
                        updated_at = excluded.updated_at
                    """,
                    (slug, norm_channel, eff_views, eff_clicks, now),
                )
                conn.execute("COMMIT;")
            except Exception:
                conn.execute("ROLLBACK;")
                raise
        return ChannelMetricRecord(
            slug=slug,
            channel=norm_channel,
            views=eff_views,
            clicks=eff_clicks,
            updated_at=now,
        )

    def list_channel_metrics(self, *, slug: Optional[str] = None) -> list[ChannelMetricRecord]:
        with self._connect() as conn:
            if slug:
                rows = conn.execute(
                    "SELECT * FROM channel_metrics WHERE slug = ? ORDER BY channel ASC",
                    (slug,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM channel_metrics ORDER BY slug ASC, channel ASC"
                ).fetchall()
            return [
                ChannelMetricRecord(
                    slug=str(r["slug"]),
                    channel=str(r["channel"]),
                    views=int(r["views"]),
                    clicks=int(r["clicks"]),
                    updated_at=str(r["updated_at"]),
                )
                for r in rows
            ]

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
            art_blogger = conn.execute(
                "SELECT COUNT(*) FROM articles WHERE blogger_post_id IS NOT NULL"
            ).fetchone()[0]
            art_pinterest = conn.execute(
                "SELECT COUNT(*) FROM articles WHERE pinterest_pin_id IS NOT NULL"
            ).fetchone()[0]
            art_patreon = conn.execute(
                "SELECT COUNT(*) FROM articles WHERE patreon_post_id IS NOT NULL"
            ).fetchone()[0]
            paid_orders = conn.execute(
                "SELECT COUNT(*) FROM store_orders WHERE refunded = 0 AND gross_cents > 0"
            ).fetchone()[0]
            net_cents = conn.execute(
                "SELECT COALESCE(SUM(net_cents), 0) FROM store_orders WHERE refunded = 0"
            ).fetchone()[0]
            runs_total = conn.execute("SELECT COUNT(*) FROM publish_runs").fetchone()[0]
        return {
            "signals_total": int(sig_total),
            "signals_unused": int(sig_unused),
            "articles_total": int(art_total),
            "articles_verified": int(art_verified),
            "articles_staged_remote": int(art_staged),
            "articles_blogger_remote": int(art_blogger),
            "articles_pinterest_remote": int(art_pinterest),
            "articles_patreon_remote": int(art_patreon),
            "store_orders_paid": int(paid_orders),
            "net_revenue_cents": int(net_cents),
            "publish_runs_total": int(runs_total),
        }
