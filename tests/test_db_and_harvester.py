"""Tests for SQLite WAL state ledger deduplication and zero-key trend harvester."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.db import PipelineDatabase
from engine.harvester import (
    fetch_github_trending_signals,
    fetch_hn_signals,
    fetch_pypi_rss_signals,
    harvest_signals,
)
from engine.models import ArticleRecord, TopicSignal


class TestDatabaseAndHarvester(unittest.TestCase):
    def test_signal_deduplication_and_mon_wed_fri_pillar_rotation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = PipelineDatabase(Path(tmp) / "pipeline.db")
            seeds = harvest_signals(offline=True)
            self.assertGreaterEqual(len(seeds), 9)

            inserted_first = db.upsert_signals(seeds)
            inserted_second = db.upsert_signals(seeds)
            self.assertEqual(inserted_first, len(seeds))
            self.assertEqual(inserted_second, 0, "Duplicate fingerprints must be ignored idempotently")

            batch = db.select_unused_signals(limit=3)
            self.assertEqual([s.pillar for s in batch], ["pillar_a", "pillar_b", "pillar_c"])

            # Mark first pillar_a signal used and ensure next pick rotates cleanly
            db.mark_signal_used(batch[0].fingerprint)
            next_pillar_a = db.select_unused_signals(limit=1, pillar_filter="pillar_a")
            self.assertEqual(len(next_pillar_a), 1)
            self.assertNotEqual(next_pillar_a[0].fingerprint, batch[0].fingerprint)

    def test_article_upsert_preserves_remote_devto_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = PipelineDatabase(Path(tmp) / "pipeline.db")
            sig = TopicSignal(
                pillar="pillar_a",
                blueprint_key="pillar_a_circuit_breaker_json",
                title="Test Signal",
                source="seed",
                source_url="https://docs.python.org/3/",
                score=100,
                tags=["python"],
                summary="summary",
            ).with_defaults("2026-01-01T00:00:00Z")
            db.upsert_signals([sig])

            art = ArticleRecord(
                slug="test-slug",
                signal_fingerprint=sig.fingerprint,
                pillar="pillar_a",
                scheduled_slot="Mon 18:30 Asia/Colombo",
                title="Initial Title",
                subtitle="Initial Subtitle",
                tags=["python"],
                canonical_url="https://tharushanuwansara.github.io/posts/test-slug/",
                markdown_body="body v1",
                word_count=800,
                status="staged_draft",
                devto_article_id=987654,
                devto_url="https://dev.to/tharusha/test-slug",
            )
            db.save_article(art)

            # Subsequent local re-save with devto_article_id=None must preserve 987654
            art_update = ArticleRecord(
                slug="test-slug",
                signal_fingerprint=sig.fingerprint,
                pillar="pillar_a",
                scheduled_slot="Mon 18:30 Asia/Colombo",
                title="Updated Title",
                subtitle="Initial Subtitle",
                tags=["python"],
                canonical_url="https://tharushanuwansara.github.io/posts/test-slug/",
                markdown_body="body v2",
                word_count=820,
                status="staged_draft",
            )
            saved = db.save_article(art_update)
            self.assertEqual(saved.devto_article_id, 987654)
            self.assertEqual(saved.title, "Updated Title")

    def test_harvester_parses_mock_feeds_and_survives_network_outage(self) -> None:
        hn_json = json.dumps(
            {
                "hits": [
                    {
                        "title": "Show HN: High-throughput SQLite WAL job queue in Python",
                        "url": "https://github.com/org/sqlite-wal-demo",
                        "points": 180,
                        "objectID": "4001",
                    }
                ]
            }
        ).encode("utf-8")
        gh_json = json.dumps(
            {
                "items": [
                    {
                        "full_name": "acme/webhook-hmac-guard",
                        "description": "Idempotent webhook replay protection",
                        "html_url": "https://github.com/acme/webhook-hmac-guard",
                        "stargazers_count": 95,
                    }
                ]
            }
        ).encode("utf-8")
        rss_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"><channel>
          <item>
            <title>sqlite-retry-helper 1.2.0</title>
            <link>https://pypi.org/project/sqlite-retry-helper/</link>
            <description>Lightweight sqlite retry queue utility</description>
          </item>
        </channel></rss>"""

        self.assertEqual(len(fetch_hn_signals(http_get=lambda _u, _t: hn_json, sleeper=lambda _s: None)), 1)
        self.assertEqual(
            len(fetch_github_trending_signals(http_get=lambda _u, _t: gh_json, sleeper=lambda _s: None)), 1
        )
        self.assertEqual(
            len(fetch_pypi_rss_signals(http_get=lambda _u, _t: rss_xml, sleeper=lambda _s: None)), 1
        )

        # Total network failure must fall back cleanly to deterministic pillar seeds
        def failing_get(_url: str, _timeout: float) -> bytes:
            raise urllib.error.URLError("offline")

        fallback = harvest_signals(offline=False, http_get=failing_get, sleeper=lambda _s: None)
        self.assertGreaterEqual(len(fallback), 9)


if __name__ == "__main__":
    unittest.main()
