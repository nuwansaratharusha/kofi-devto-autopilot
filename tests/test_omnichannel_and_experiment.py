"""Comprehensive tests for Dual Storefronts, Blogger v3, 1000x1500 PNG/Pinterest v5, 30-Day Curriculum & Experiment Verdicts."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import sqlite3
import struct
import sys
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from http.server import ThreadingHTTPServer
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from autopilot import (
    cmd_log_metrics,
    cmd_report_30d,
    cmd_run_daily,
    cmd_sync_stores,
    create_dashboard_handler,
    render_dashboard_html,
)
from engine.blogger_client import (
    BloggerClient,
    BloggerPublishError,
    generate_pkce_pair,
    run_blogger_pkce_loopback,
)
from engine.composer import compose_article, slugify
from engine.config import AppConfig
from engine.db import PipelineDatabase
from engine.devto_client import HttpResult
from engine.experiment import (
    VERDICT_BOTTLENECK,
    VERDICT_NOT_WORKING,
    VERDICT_WORKING,
    compute_series_day_for_date,
)
from engine.harvester import DETERMINISTIC_PILLAR_SEEDS
from engine.models import ArticleRecord, StoreOrderRecord, TopicSignal
from engine.pin_renderer import (
    PALETTE_RGB,
    PIN_HEIGHT,
    PIN_WIDTH,
    PNG_SIGNATURE,
    _wrap_text,
    render_pin_png_bytes,
    render_pin_svg,
)
from engine.pinterest_client import PinterestClient, PinterestPublishError, build_pinterest_pin_copy
from engine.storefronts import (
    GumroadClient,
    LemonSqueezyClient,
    _extract_utm_from_mapping_or_url,
    append_storefront_utm,
    sync_storefronts,
)
from engine.verifier import verify_article


def _parse_and_validate_png_chunks(png_bytes: bytes) -> dict[bytes, bytes]:
    if not png_bytes.startswith(PNG_SIGNATURE):
        raise AssertionError("Invalid 8-byte PNG signature")
    offset = len(PNG_SIGNATURE)
    chunks: dict[bytes, list[bytes]] = {}
    while offset < len(png_bytes):
        length = struct.unpack(">I", png_bytes[offset : offset + 4])[0]
        ctype = png_bytes[offset + 4 : offset + 8]
        cdata = png_bytes[offset + 8 : offset + 8 + length]
        stored_crc = struct.unpack(">I", png_bytes[offset + 8 + length : offset + 12 + length])[0]
        computed_crc = zlib.crc32(ctype + cdata) & 0xFFFFFFFF
        if stored_crc != computed_crc:
            raise AssertionError(f"CRC mismatch on chunk {ctype!r}: {stored_crc} != {computed_crc}")
        chunks.setdefault(ctype, []).append(cdata)
        offset += 12 + length
        if ctype == b"IEND":
            break
    if offset != len(png_bytes):
        raise AssertionError("Trailing bytes after IEND chunk")
    return {k: b"".join(v) for k, v in chunks.items()}


class TestOmnichannelAndExperiment(unittest.TestCase):
    def test_png_ihdr_crc_decompression_and_svg_validity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = AppConfig.load(Path(tmp))
            seed = DETERMINISTIC_PILLAR_SEEDS[9].with_defaults("2026-11-18T13:00:00Z")
            draft = compose_article(seed, cfg, series_day=10)
            verified, rep = verify_article(draft, config=cfg)
            self.assertTrue(rep.passed, rep.lint_errors)

            png_bytes = render_pin_png_bytes(verified, cfg)
            self.assertLess(len(png_bytes), 35 * 1024, "PNG must stay strictly under 35 KB")

            chunks = _parse_and_validate_png_chunks(png_bytes)
            self.assertIn(b"IHDR", chunks)
            self.assertIn(b"PLTE", chunks)
            self.assertIn(b"IDAT", chunks)
            self.assertIn(b"IEND", chunks)

            width, height, bit_depth, color_type, comp, filt, interlace = struct.unpack(
                ">IIBBBBB", chunks[b"IHDR"]
            )
            self.assertEqual((width, height), (PIN_WIDTH, PIN_HEIGHT))
            self.assertEqual((bit_depth, color_type, comp, filt, interlace), (8, 3, 0, 0, 0))
            self.assertEqual(len(chunks[b"PLTE"]), len(PALETTE_RGB) * 3)

            raw_scanlines = zlib.decompress(chunks[b"IDAT"])
            self.assertEqual(len(raw_scanlines), PIN_HEIGHT * (1 + PIN_WIDTH))
            for y in range(PIN_HEIGHT):
                row_start = y * (1 + PIN_WIDTH)
                self.assertEqual(raw_scanlines[row_start], 0)
                row_pixels = raw_scanlines[row_start + 1 : row_start + 1 + PIN_WIDTH]
                self.assertTrue(all(px < len(PALETTE_RGB) for px in row_pixels))

            svg_xml = render_pin_svg(verified, cfg)
            svg_root = ET.fromstring(svg_xml)
            self.assertEqual(svg_root.attrib.get("width"), "1000")
            self.assertEqual(svg_root.attrib.get("height"), "1500")

    def test_sqlite_legacy_schema_migration_and_upsert_column_preservation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "legacy.db"
            raw_conn = sqlite3.connect(str(db_file))
            raw_conn.executescript(
                """
                CREATE TABLE signals (
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
                CREATE TABLE articles (
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
            raw_conn.close()

            # Initialize PipelineDatabase twice to verify idempotent ALTER TABLE migration
            db = PipelineDatabase(db_file)
            db = PipelineDatabase(db_file)

            sig = DETERMINISTIC_PILLAR_SEEDS[0].with_defaults("2026-11-18T00:00:00Z")
            db.upsert_signals([sig])

            rec = ArticleRecord(
                slug="day1-legacy-test",
                signal_fingerprint=sig.fingerprint,
                pillar="pillar_a",
                scheduled_slot="Mon",
                title="Title",
                subtitle="Sub",
                tags=["python"],
                canonical_url="https://tharushanuwansara.github.io/posts/day1-legacy-test/",
                markdown_body="body",
                status="code_verified",
                series_day=1,
                blogger_post_id="blog_991",
                blogger_url="https://myblog.blogspot.com/2026/11/day-1.html",
                pinterest_pin_id="pin_772",
                pinterest_url="https://www.pinterest.com/pin/pin_772/",
            )
            db.save_article(rec)

            # Re-saving with None channel IDs must preserve all existing non-null remote IDs
            partial = ArticleRecord(
                slug="day1-legacy-test",
                signal_fingerprint=sig.fingerprint,
                pillar="pillar_a",
                scheduled_slot="Mon",
                title="Updated Title",
                subtitle="Sub",
                tags=["python"],
                canonical_url="https://tharushanuwansara.github.io/posts/day1-legacy-test/",
                markdown_body="body updated",
                status="published_live",
            )
            saved = db.save_article(partial)
            self.assertEqual(saved.series_day, 1)
            self.assertEqual(saved.blogger_post_id, "blog_991")
            self.assertEqual(saved.blogger_url, "https://myblog.blogspot.com/2026/11/day-1.html")
            self.assertEqual(saved.pinterest_pin_id, "pin_772")
            self.assertEqual(saved.pinterest_url, "https://www.pinterest.com/pin/pin_772/")

    def test_blogger_oauth2_series_html_post_put_and_pkce_loopback(self) -> None:
        verifier, challenge = generate_pkce_pair()
        expected_c = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
        self.assertEqual(challenge, expected_c)

        with tempfile.TemporaryDirectory() as tmp:
            cfg_missing = AppConfig.load(Path(tmp), environ={})
            with self.assertRaises(BloggerPublishError):
                run_blogger_pkce_loopback(cfg_missing, timeout_seconds=1.0, open_browser=False)

            cfg = AppConfig.load(
                Path(tmp),
                environ={
                    "GUMROAD_STORE_URL": "https://tharusha.gumroad.com/l/free-cron",
                    "LEMONSQUEEZY_STORE_URL": "https://tharusha.lemonsqueezy.com/buy/pro-kit",
                    "BLOGGER_BLOG_ID": "33445566",
                    "BLOGGER_CLIENT_ID": "cid_123.apps.googleusercontent.com",
                    "BLOGGER_CLIENT_SECRET": "csec_456",
                    "BLOGGER_REFRESH_TOKEN": "rtok_789",
                    "BLOGGER_PUBLISH_MODE": "draft",
                },
            )
            unverified = ArticleRecord(
                slug="unverified",
                signal_fingerprint="fp",
                pillar="pillar_a",
                scheduled_slot="Mon",
                title="Draft",
                subtitle="Sub",
                tags=["python"],
                canonical_url="https://tharushanuwansara.github.io/posts/unverified/",
                markdown_body="body",
                status="composed",
            )
            with self.assertRaises(BloggerPublishError):
                BloggerClient(cfg).stage_or_publish(unverified)

            sig = DETERMINISTIC_PILLAR_SEEDS[0].with_defaults("2026-11-18T13:00:00Z")
            art = compose_article(sig, cfg, series_day=1)
            art, rep = verify_article(art, config=cfg)
            self.assertTrue(rep.passed, rep.lint_errors)
            self.assertEqual(rep.storefront_link_count, 2)

            calls: list[tuple[str, str, str]] = []

            def fake_blogger_sender(req: urllib.request.Request, _timeout: float) -> HttpResult:
                body_str = (req.data or b"").decode("utf-8")
                calls.append((req.get_method(), req.full_url, body_str))
                if "oauth2.googleapis.com/token" in req.full_url:
                    return HttpResult(status=200, headers={}, body=b'{"access_token":"ya29.mock_token"}')
                if req.get_method() == "POST":
                    return HttpResult(
                        status=200,
                        headers={},
                        body=b'{"id":"post_8801","url":"http://tharusha-dev.blogspot.com/2026/11/day-1.html"}',
                    )
                return HttpResult(
                    status=200,
                    headers={},
                    body=b'{"id":"post_8801","url":"https://tharusha-dev.blogspot.com/2026/11/day-1.html"}',
                )

            bclient = BloggerClient(cfg, sender=fake_blogger_sender, sleeper=lambda _s: None)
            r1 = bclient.stage_or_publish(art, series_articles=[art])
            self.assertEqual(r1["status"], "staged_draft")
            self.assertEqual(r1["operation"], "created")
            self.assertEqual(art.blogger_post_id, "post_8801")
            self.assertEqual(art.blogger_url, "https://tharusha-dev.blogspot.com/2026/11/day-1.html")
            self.assertIn("isDraft=true", calls[1][1])

            html_exported = Path(r1["html_path"]).read_text(encoding="utf-8")
            self.assertIn("30 Days of Zero-Cost Production Python &amp; AI Reliability", html_exported)
            self.assertIn("utm_source=blogger&amp;utm_medium=series&amp;utm_campaign=30d_exp_2026_11", html_exported)
            self.assertNotIn("`", html_exported, "Blogger HTML must contain zero raw backticks")

            # Second call with mode_override='live' must use PUT on existing post_8801
            r2 = bclient.stage_or_publish(art, mode_override="live", series_articles=[art])
            self.assertEqual(r2["operation"], "updated")
            self.assertEqual(r2["status"], "published_live")
            self.assertEqual(calls[3][0], "PUT")
            self.assertIn("/posts/post_8801?isDraft=false", calls[3][1])

    def test_pinterest_v5_client_limits_base64_and_idempotency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = AppConfig.load(
                Path(tmp),
                environ={
                    "PINTEREST_ACCESS_TOKEN": "pina_mock_secret",
                    "PINTEREST_BOARD_ID": "board_990011",
                },
            )
            long_art = ArticleRecord(
                slug="long-pin-test",
                signal_fingerprint="fp",
                pillar="pillar_b",
                scheduled_slot="Wed",
                title="X" * 160,
                subtitle="Y" * 900,
                tags=["python", "sqlite", "wal", "reliability"],
                canonical_url="https://tharushanuwansara.github.io/posts/long-pin-test/",
                markdown_body="```python\nassert 1 == 1\n```",
                word_count=910,
                status="code_verified",
                series_day=4,
                blogger_url="https://tharusha.blogspot.com/2026/11/day-4.html",
            )
            sent_payloads: list[dict] = []

            def fake_pin_sender(req: urllib.request.Request, _timeout: float) -> HttpResult:
                self.assertEqual(req.headers.get("Authorization"), "Bearer pina_mock_secret")
                payload = json.loads((req.data or b"{}").decode("utf-8"))
                sent_payloads.append(payload)
                return HttpResult(
                    status=201,
                    headers={},
                    body=b'{"id":"pin_554433","url":"https://www.pinterest.com/pin/pin_554433/"}',
                )

            pclient = PinterestClient(cfg, sender=fake_pin_sender, sleeper=lambda _s: None)
            with self.assertRaises(PinterestPublishError):
                pclient.render_and_publish(
                    ArticleRecord(
                        slug="bad",
                        signal_fingerprint="fp",
                        pillar="pillar_a",
                        scheduled_slot="Mon",
                        title="T",
                        subtitle="S",
                        tags=[],
                        canonical_url="https://tharushanuwansara.github.io/posts/bad/",
                        markdown_body="",
                        status="verification_failed",
                    )
                )

            res1 = pclient.render_and_publish(long_art)
            self.assertEqual(res1["status"], "published_live")
            self.assertEqual(long_art.pinterest_pin_id, "pin_554433")
            self.assertEqual(len(sent_payloads), 1)

            sent = sent_payloads[0]
            self.assertLessEqual(len(sent["title"]), 100)
            self.assertLessEqual(len(sent["description"]), 800)
            self.assertLessEqual(len(sent["alt_text"]), 500)
            self.assertTrue(sent["link"].startswith("https://tharusha.blogspot.com/2026/11/day-4.html?"))
            self.assertIn("utm_source=pinterest", sent["link"])
            self.assertIn("utm_campaign=30d_exp_2026_11", sent["link"])
            self.assertEqual(sent["media_source"]["source_type"], "image_base64")
            self.assertEqual(sent["media_source"]["content_type"], "image/png")
            decoded_png = base64.b64decode(sent["media_source"]["data"])
            self.assertTrue(decoded_png.startswith(PNG_SIGNATURE))

            # Idempotent re-run must not send a second POST
            res2 = pclient.render_and_publish(long_art)
            self.assertEqual(res2["status"], "already_pinned")
            self.assertEqual(len(sent_payloads), 1)

    def test_gumroad_v2_and_lemonsqueezy_v1_sync_utm_and_refund_exclusion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = AppConfig.load(
                Path(tmp),
                environ={
                    "GUMROAD_STORE_URL": "https://tharusha.gumroad.com",
                    "GUMROAD_ACCESS_TOKEN": "gr_tok_test",
                    "LEMONSQUEEZY_STORE_URL": "https://tharusha.lemonsqueezy.com",
                    "LEMONSQUEEZY_API_KEY": "ls_key_test",
                    "LEMONSQUEEZY_STORE_ID": "9988",
                },
            )
            self.assertEqual(cfg.free_checkout_url, "https://tharusha.gumroad.com")
            self.assertEqual(cfg.paid_checkout_url, "https://tharusha.lemonsqueezy.com")

            db = PipelineDatabase(cfg.db_path)

            gr_requests: list[tuple[str, str, bytes]] = []

            def fake_gr_sender(req: urllib.request.Request, _t: float) -> HttpResult:
                gr_requests.append((req.get_method(), req.full_url, req.data or b""))
                self.assertEqual(req.headers.get("Authorization"), "Bearer gr_tok_test")
                if req.full_url.endswith("/v2/products") and req.get_method() == "GET":
                    return HttpResult(200, {}, b'{"success":true,"products":[]}')
                if req.full_url.endswith("/v2/products") and req.get_method() == "POST":
                    body = json.loads((req.data or b"{}").decode("utf-8"))
                    self.assertIs(body["published"], False)
                    return HttpResult(200, {}, json.dumps({"success": True, "product": {"id": "p1", "name": body["name"]}}).encode())
                return HttpResult(
                    200,
                    {},
                    json.dumps(
                        {
                            "success": True,
                            "sales": [
                                {
                                    "id": "gr_sale_free_1",
                                    "product_name": "Free Mini-Kit",
                                    "price": 0,
                                    "gumroad_fee": 0,
                                    "refunded": False,
                                    "url_params": {"utm_source": "pinterest", "utm_medium": "pin", "utm_campaign": "30d_exp_2026_11"},
                                    "created_at": "2026-11-19T10:00:00Z",
                                },
                                {
                                    "id": "gr_sale_tip_2",
                                    "product_name": "Free Mini-Kit",
                                    "price": 500,
                                    "gumroad_fee": 80,
                                    "refunded": False,
                                    "url_params": {"utm_source": "blogger", "utm_campaign": "30d_exp_2026_11"},
                                    "created_at": "2026-11-20T10:00:00Z",
                                },
                                {
                                    "id": "gr_sale_refunded_3",
                                    "product_name": "Pro Kit",
                                    "price": 1400,
                                    "gumroad_fee": 190,
                                    "refunded": True,
                                    "url_params": {"utm_source": "blogger"},
                                    "created_at": "2026-11-21T10:00:00Z",
                                },
                            ],
                        }
                    ).encode("utf-8"),
                )

            ls_requests: list[tuple[str, str, bytes]] = []

            def fake_ls_sender(req: urllib.request.Request, _t: float) -> HttpResult:
                ls_requests.append((req.get_method(), req.full_url, req.data or b""))
                self.assertEqual(req.headers.get("Accept"), "application/vnd.api+json")
                self.assertEqual(req.headers.get("Authorization"), "Bearer ls_key_test")
                if "/v1/products" in req.full_url:
                    return HttpResult(200, {}, b'{"data":[{"id":"prod_1","type":"products"}]}')
                if "/v1/checkouts" in req.full_url:
                    return HttpResult(201, {}, b'{"data":{"id":"chk_1","attributes":{"url":"https://tharusha.lemonsqueezy.com/checkout/custom/chk_1"}}}')
                return HttpResult(
                    200,
                    {},
                    json.dumps(
                        {
                            "data": [
                                {
                                    "id": "ls_ord_101",
                                    "type": "orders",
                                    "attributes": {
                                        "status": "paid",
                                        "refunded": False,
                                        "subtotal_usd": 5600,
                                        "fee_usd": 360,
                                        "currency": "USD",
                                        "custom_data": {
                                            "utm_source": "pinterest",
                                            "utm_medium": "pin",
                                            "utm_campaign": "30d_exp_2026_11",
                                            "sku": "production-agent-automation-kit",
                                        },
                                        "created_at": "2026-11-22T14:00:00Z",
                                    },
                                },
                                {
                                    "id": "ls_ord_102_refunded",
                                    "type": "orders",
                                    "attributes": {
                                        "status": "refunded",
                                        "refunded": True,
                                        "subtotal_usd": 1400,
                                        "fee_usd": 120,
                                        "currency": "USD",
                                        "custom_data": {"utm_source": "devto"},
                                        "created_at": "2026-11-23T14:00:00Z",
                                    },
                                },
                            ]
                        }
                    ).encode("utf-8"),
                )

            sync_res = sync_storefronts(
                cfg,
                db,
                create_gumroad_drafts=True,
                catalog_products=[{"slug": "free-kit", "title": "Free Kit", "price_usd": 0, "summary": "S", "tagline": "T"}],
                gumroad_sender=fake_gr_sender,
                lemonsqueezy_sender=fake_ls_sender,
            )
            self.assertEqual(sync_res["gumroad"]["orders_synced"], 3)
            self.assertEqual(len(sync_res["gumroad"]["drafts_created"]), 1)
            self.assertEqual(sync_res["lemonsqueezy"]["orders_synced"], 2)

            # Verify Lemon Squeezy POST /v1/checkouts contract
            ls_client = LemonSqueezyClient("ls_key_test", sender=fake_ls_sender, sleeper=lambda _s: None)
            chk = ls_client.create_checkout(
                store_id="9988",
                variant_id="4455",
                custom_price_cents=1400,
                custom_data={"utm_source": "blogger"},
            )
            self.assertEqual(chk["data"]["id"], "chk_1")

            active_orders = db.list_store_orders(include_refunded=False)
            self.assertEqual(len(active_orders), 3)
            # Net revenue must exclude both refunded orders: 420c (Gumroad) + 5240c (LemonSqueezy) = 5660c
            self.assertEqual(sum(o.net_cents for o in active_orders), 5660)

    def test_30_curriculum_uniqueness_daily_flow_rss_and_report_verdicts(self) -> None:
        # 1. Verify all 30 deterministic seeds are unique and preserve indices 0..8
        self.assertEqual(len(DETERMINISTIC_PILLAR_SEEDS), 30)
        original_9_bps = [
            "pillar_a_circuit_breaker_json",
            "pillar_a_retry_after_jitter",
            "pillar_a_token_budget_failover",
            "pillar_b_sqlite_wal_queue",
            "pillar_b_idempotent_cron_leases",
            "pillar_b_priority_backoff_dlq",
            "pillar_c_hmac_ledger",
            "pillar_c_replay_nonce_dedup",
            "pillar_c_reconciliation_audit",
        ]
        self.assertEqual([s.blueprint_key for s in DETERMINISTIC_PILLAR_SEEDS[:9]], original_9_bps)

        norm_seeds = [s.with_defaults("2026-11-18T13:00:00Z") for s in DETERMINISTIC_PILLAR_SEEDS]
        self.assertEqual(len({s.fingerprint for s in norm_seeds}), 30)
        self.assertEqual(len({s.blueprint_key for s in norm_seeds}), 30)
        self.assertEqual(len({s.title for s in norm_seeds}), 30)
        self.assertEqual(len({slugify(s.title, s.fingerprint) for s in norm_seeds}), 30)

        # 2. End-to-end daily automation + Pinterest RSS + 3-verdict classification + HTML hygiene
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            shutil.copytree(PROJECT_ROOT / "products", ws / "products")
            cfg = AppConfig.load(
                ws,
                environ={
                    "GUMROAD_STORE_URL": "https://tharusha.gumroad.com",
                    "LEMONSQUEEZY_STORE_URL": "https://tharusha.lemonsqueezy.com",
                    "CANONICAL_BASE_URL": "https://tharushanuwansara.github.io/kofi-devto-autopilot",
                    "EXPERIMENT_START_DATE": "2026-11-18",
                    "EXPERIMENT_END_DATE": "2026-12-18",
                },
            )

            # Day 1: 2026-11-18 (Wednesday -> DEV.to runs dry_run + Blogger + Pinterest)
            d1 = cmd_run_daily(cfg, date_str="2026-11-18", offline=True, as_json=False)
            self.assertEqual(d1["series_day"], 1)
            self.assertTrue(d1["composed_fresh"])
            self.assertEqual(d1["devto"]["status"], "dry_run")

            # Re-running same date must be 100% idempotent (reuses Day 1 article, composed_fresh=False)
            d1_repeat = cmd_run_daily(cfg, date_str="2026-11-18", offline=True, as_json=False)
            self.assertFalse(d1_repeat["composed_fresh"])
            self.assertEqual(d1_repeat["article"]["slug"], d1["article"]["slug"])

            # Day 2: 2026-11-19 (Thursday -> Blogger + Pinterest run, DEV.to skipped_non_mwf_cadence)
            d2 = cmd_run_daily(cfg, date_str="2026-11-19", offline=True, as_json=False)
            self.assertEqual(d2["series_day"], 2)
            self.assertEqual(d2["devto"]["status"], "skipped_non_mwf_cadence")

            # Validate Pinterest Auto-Publish RSS 2.0 XML (`site/pinterest_feed.xml`)
            pin_rss_path = cfg.site_dir / "pinterest_feed.xml"
            pin_rss_raw = pin_rss_path.read_text(encoding="utf-8")
            self.assertIn('xmlns:media="http://search.yahoo.com/mrss/"', pin_rss_raw)
            pin_tree = ET.fromstring(pin_rss_raw)
            items = pin_tree.findall("./channel/item")
            self.assertEqual(len(items), 2)
            enc = items[0].find("enclosure")
            self.assertIsNotNone(enc)
            assert enc is not None
            self.assertEqual(enc.attrib.get("type"), "image/png")
            self.assertGreater(int(enc.attrib.get("length", "0")), 5000)
            mcontent = items[0].find("{http://search.yahoo.com/mrss/}content")
            self.assertIsNotNone(mcontent)
            assert mcontent is not None
            self.assertEqual(mcontent.attrib.get("medium"), "image")
            self.assertEqual(mcontent.attrib.get("width"), "1000")
            self.assertEqual(mcontent.attrib.get("height"), "1500")

            # Seed metrics & store orders to verify all 3 deterministic verdicts:
            # - pinterest: WORKING (paid Lemon Squeezy order unlocking $50 wire!)
            # - blogger: BOTTLENECK (240 views, 18 clicks, $0 paid)
            # - devto: NOT WORKING (15 views, 2 clicks, $0 paid)
            slug1 = d1["article"]["slug"]
            cmd_log_metrics(cfg, slug=slug1, channel="pinterest", views=620, clicks=44)
            cmd_log_metrics(cfg, slug=slug1, channel="blogger", views=240, clicks=18)
            cmd_log_metrics(cfg, slug=slug1, channel="devto", views=15, clicks=2)

            db = PipelineDatabase(cfg.db_path)
            db.upsert_store_orders(
                [
                    StoreOrderRecord(
                        storefront="lemonsqueezy",
                        order_id="ls_unlock_1",
                        sku="production-agent-automation-kit",
                        product_name="Pro Kit",
                        gross_cents=5600,
                        fee_cents=360,
                        net_cents=5240,
                        utm_source="pinterest",
                        utm_medium="pin",
                        utm_campaign="30d_exp_2026_11",
                        attributed_slug=slug1,
                    )
                ]
            )

            rep = cmd_report_30d(cfg, as_of="2026-12-18", as_json=False)
            self.assertTrue(rep["storefronts"]["lemonsqueezy"]["unlocked"])
            self.assertEqual(rep["storefronts"]["lemonsqueezy"]["unlock_progress_pct"], 100.0)
            self.assertFalse(rep["storefronts"]["gumroad"]["unlocked"])
            self.assertEqual(rep["channels"]["pinterest"]["verdict"], VERDICT_WORKING)
            self.assertEqual(rep["channels"]["blogger"]["verdict"], VERDICT_BOTTLENECK)
            self.assertEqual(rep["channels"]["devto"]["verdict"], VERDICT_NOT_WORKING)
            self.assertEqual(rep["pillars"]["pillar_a"]["verdict"], VERDICT_WORKING)

            # Verify zero raw backticks in rendered site/index.html & dashboard HTML outside <script>
            site_index_html = (cfg.site_dir / "index.html").read_text(encoding="utf-8")
            site_no_scripts = re.sub(r"<script.*?</script>", "", site_index_html, flags=re.DOTALL)
            self.assertNotIn("`", site_no_scripts)
            self.assertIn("30-Day Experiment Scoreboard", site_no_scripts)

            dash_html = render_dashboard_html(cfg)
            dash_no_scripts = re.sub(r"<script.*?</script>", "", dash_html, flags=re.DOTALL)
            self.assertNotIn("`", dash_no_scripts)
            self.assertIn("Copy Blogger Series HTML", dash_no_scripts)

            # Verify /api/experiment and /site/pins/<slug>.png over HTTP
            cmd_sync_stores(cfg, as_json=False)
            server = ThreadingHTTPServer(("127.0.0.1", 0), create_dashboard_handler(cfg))
            port = server.server_address[1]
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/experiment", timeout=5) as r_exp:
                    exp_http = json.loads(r_exp.read().decode("utf-8"))
                self.assertEqual(exp_http["channels"]["pinterest"]["verdict"], VERDICT_WORKING)

                with urllib.request.urlopen(f"http://127.0.0.1:{port}/site/pins/{slug1}.png", timeout=5) as r_png:
                    self.assertEqual(r_png.headers["Content-Type"], "image/png")
                    self.assertTrue(r_png.read(8) == PNG_SIGNATURE)
            finally:
                server.shutdown()
                server.server_close()

    def test_edge_cases_metrics_validation_date_clamping_and_all_30_sandbox_runs(self) -> None:
        # Boundary date clamping
        self.assertEqual(compute_series_day_for_date("2026-11-10", "2026-11-18"), 1)
        self.assertEqual(compute_series_day_for_date("2026-11-18", "2026-11-18"), 1)
        self.assertEqual(compute_series_day_for_date("2026-12-17", "2026-11-18"), 30)
        self.assertEqual(compute_series_day_for_date("2026-12-25", "2026-11-18"), 30)

        with tempfile.TemporaryDirectory() as tmp:
            db = PipelineDatabase(Path(tmp) / "edge.db")
            with self.assertRaises(ValueError):
                db.upsert_channel_metrics(slug="s1", channel="tiktok", views=10)
            with self.assertRaises(ValueError):
                db.upsert_channel_metrics(slug="s1", channel="pinterest", views=-1)
            with self.assertRaises(ValueError):
                db.upsert_channel_metrics(slug="s1", channel="pinterest", clicks=-5)

            # Partial updates must preserve previously logged counter
            m1 = db.upsert_channel_metrics(slug="s1", channel="blogger", views=310)
            self.assertEqual((m1.views, m1.clicks), (310, 0))
            m2 = db.upsert_channel_metrics(slug="s1", channel="blogger", clicks=24)
            self.assertEqual((m2.views, m2.clicks), (310, 24))

            # Execute all 30 curriculum seeds end-to-end inside isolated python3 -I sandbox
            cfg = AppConfig.load(
                Path(tmp),
                environ={
                    "GUMROAD_STORE_URL": "https://tharusha.gumroad.com",
                    "LEMONSQUEEZY_STORE_URL": "https://tharusha.lemonsqueezy.com",
                },
            )
            for idx, seed in enumerate(DETERMINISTIC_PILLAR_SEEDS, start=1):
                sig = seed.with_defaults("2026-11-18T13:00:00Z")
                draft = compose_article(sig, cfg, series_day=idx)
                _, rep = verify_article(draft, config=cfg)
                self.assertTrue(rep.passed, f"Day {idx} ({sig.blueprint_key}) failed: {rep.lint_errors}")
                self.assertGreaterEqual(rep.word_count, 750)
                self.assertEqual(rep.storefront_link_count, 2)

    def test_pkce_loopback_socket_survives_favicon_and_exchanges_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = AppConfig.load(
                Path(tmp),
                environ={
                    "BLOGGER_CLIENT_ID": "cid_loopback.apps.googleusercontent.com",
                    "BLOGGER_CLIENT_SECRET": "csec_loopback",
                },
            )
            token_requests: list[dict[str, list[str]]] = []

            def fake_token_sender(req: urllib.request.Request, _timeout: float) -> HttpResult:
                form = urllib.parse.parse_qs((req.data or b"").decode("utf-8"))
                token_requests.append(form)
                return HttpResult(
                    status=200,
                    headers={},
                    body=b'{"access_token":"ya29.pkce_ok","refresh_token":"1//0g_pkce_refresh_tok","scope":"https://www.googleapis.com/auth/blogger"}',
                )

            def simulate_browser(auth_url: str) -> None:
                parsed_auth = urllib.parse.urlparse(auth_url)
                auth_qs = urllib.parse.parse_qs(parsed_auth.query)
                redirect_uri = auth_qs["redirect_uri"][0]
                state = auth_qs["state"][0]
                challenge = auth_qs["code_challenge"][0]
                base_origin = redirect_uri.rsplit("/callback", 1)[0]

                def _fire() -> None:
                    # 1. Browser sends stray /favicon.ico before OAuth callback (must NOT abort server!)
                    with urllib.request.urlopen(f"{base_origin}/favicon.ico", timeout=3) as r_fav:
                        self.assertEqual(r_fav.status, 204)
                    # 2. Browser delivers real OAuth2 /callback with code + state
                    cb_url = f"{redirect_uri}?{urllib.parse.urlencode({'code': 'auth_code_777', 'state': state})}"
                    with urllib.request.urlopen(cb_url, timeout=3) as r_cb:
                        self.assertEqual(r_cb.status, 200)
                        self.assertIn(b"Blogger OAuth2 Complete", r_cb.read())

                t = threading.Thread(target=_fire, daemon=True)
                t.start()
                # Stash expected challenge on closure for S256 verification
                token_requests.append({"_expected_challenge": [challenge]})

            res = run_blogger_pkce_loopback(
                cfg,
                timeout_seconds=5.0,
                open_browser=False,
                sender=fake_token_sender,
                on_listen=simulate_browser,
            )
            self.assertEqual(res["status"], "authorized")
            self.assertEqual(res["refresh_token"], "1//0g_pkce_refresh_tok")
            self.assertTrue(res["access_token_received"])

            expected_challenge = token_requests[0]["_expected_challenge"][0]
            posted_form = token_requests[1]
            sent_verifier = posted_form["code_verifier"][0]
            recomputed = (
                base64.urlsafe_b64encode(hashlib.sha256(sent_verifier.encode("ascii")).digest())
                .rstrip(b"=")
                .decode("ascii")
            )
            self.assertEqual(recomputed, expected_challenge)
            self.assertEqual(posted_form["code"][0], "auth_code_777")

    def test_lemonsqueezy_custom_checkout_utms_pagination_and_canonical_site_attribution(self) -> None:
        ls_url = append_storefront_utm(
            "https://tharusha.lemonsqueezy.com/buy/pro-kit",
            source="blogger",
            medium="series",
            campaign="30d_exp_2026_11",
            slug="zero-dependency-python-circuit-breakers-04a7e6",
            sku="production-agent-automation-kit",
        )
        self.assertIn("utm_source=blogger", ls_url)
        self.assertIn("checkout%5Bcustom%5D%5Butm_source%5D=blogger", ls_url)
        self.assertIn("checkout%5Bcustom%5D%5Bsku%5D=production-agent-automation-kit", ls_url)

        # Verify extraction when plain top-level UTMs are stripped and only checkout[custom][*] remains
        only_custom_qs = "https://tharusha.lemonsqueezy.com/buy/pro-kit?checkout%5Bcustom%5D%5Butm_source%5D=pinterest&checkout%5Bcustom%5D%5Butm_campaign%5D=30d_exp_2026_11&checkout%5Bcustom%5D%5Bslug%5D=day1-slug"
        extracted = _extract_utm_from_mapping_or_url(only_custom_qs)
        self.assertEqual(extracted["utm_source"], "pinterest")
        self.assertEqual(extracted["utm_campaign"], "30d_exp_2026_11")
        self.assertEqual(extracted["slug"], "day1-slug")

        # Verify LemonSqueezyClient.list_orders follows multi-page JSON:API responses
        pages_requested: list[str] = []

        def paginated_ls_sender(req: urllib.request.Request, _t: float) -> HttpResult:
            pages_requested.append(req.full_url)
            if "page%5Bnumber%5D=2" in req.full_url:
                body = {
                    "meta": {"page": {"currentPage": 2, "lastPage": 2}},
                    "data": [
                        {
                            "id": "ls_page2_ord",
                            "attributes": {
                                "status": "paid",
                                "subtotal_usd": 1400,
                                "fee_usd": 120,
                                "checkout_data": {"custom": {"utm_source": "canonical_site", "slug": "s2"}},
                            },
                        }
                    ],
                }
            else:
                body = {
                    "meta": {"page": {"currentPage": 1, "lastPage": 2}},
                    "links": {"next": "https://api.lemonsqueezy.com/v1/orders?page%5Bnumber%5D=2"},
                    "data": [
                        {
                            "id": "ls_page1_ord",
                            "attributes": {
                                "status": "paid",
                                "subtotal_usd": 1400,
                                "fee_usd": 120,
                                "checkout_data": {"custom": {"utm_source": "blogger", "slug": "s1"}},
                            },
                        }
                    ],
                }
            return HttpResult(200, {}, json.dumps(body).encode("utf-8"))

        ls_client = LemonSqueezyClient("ls_key", sender=paginated_ls_sender, sleeper=lambda _s: None)
        paginated_orders = ls_client.list_orders(store_id="77")
        self.assertEqual(len(pages_requested), 2)
        self.assertEqual([o.order_id for o in paginated_orders], ["ls_page1_ord", "ls_page2_ord"])
        self.assertEqual(paginated_orders[1].utm_source, "canonical_site")

        # Verify canonical site static pages rewrite utm_source=devto -> utm_source=canonical_site
        # and cmd_log_metrics updates site/index.html scoreboard immediately
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            shutil.copytree(PROJECT_ROOT / "products", ws / "products")
            cfg = AppConfig.load(
                ws,
                environ={
                    "GUMROAD_STORE_URL": "https://tharusha.gumroad.com/l/free-kit",
                    "LEMONSQUEEZY_STORE_URL": "https://tharusha.lemonsqueezy.com/buy/pro-kit",
                },
            )
            d1 = cmd_run_daily(cfg, date_str="2026-11-18", offline=True, as_json=False)
            slug = d1["article"]["slug"]
            post_html = (cfg.site_dir / "posts" / slug / "index.html").read_text(encoding="utf-8")
            self.assertIn("utm_source=canonical_site", post_html)
            self.assertNotIn("utm_source=devto", post_html)
            self.assertIn("checkout%5Bcustom%5D%5Butm_source%5D=canonical_site", post_html)

            cmd_log_metrics(cfg, slug=slug, channel="canonical_site", views=350, clicks=28)
            hub_html = (cfg.site_dir / "index.html").read_text(encoding="utf-8")
            self.assertIn("350", hub_html)
            self.assertIn(VERDICT_BOTTLENECK, hub_html)

    def test_online_blueprint_deduplication_31day_sweep_and_pin_word_wrap(self) -> None:
        # 1. Verify 4-line _wrap_text fills line 4 instead of dropping words after word 1
        wrapped = _wrap_text(
            "PRODUCTION TEARDOWN: SHOW HN HIGH THROUGHPUT SQLITE WAL JOB QUEUE IN PYTHON FOR RELIABLE ZERO COST BACKGROUND TASKS",
            max_chars=35,
            max_lines=4,
        )
        self.assertEqual(len(wrapped), 4)
        self.assertIn("TASKS", " ".join(wrapped))

        wrapped_overflow = _wrap_text(
            "PRODUCTION TEARDOWN: SHOW HN HIGH THROUGHPUT SQLITE WAL JOB QUEUE IN PYTHON FOR RELIABLE ZERO COST BACKGROUND TASKS WITH EXTRA OVERFLOW WORDS AT END",
            max_chars=35,
            max_lines=4,
        )
        self.assertEqual(len(wrapped_overflow), 4)
        self.assertTrue(wrapped_overflow[-1].endswith("..."))

        # 2. Verify Pinterest title truncates cleanly on word boundary within <= 100 chars
        cfg_tmp = AppConfig.load(PROJECT_ROOT)
        sample_art = ArticleRecord(
            slug="s",
            signal_fingerprint="fp",
            pillar="pillar_b",
            scheduled_slot="Wed",
            title="Replacing Redis Queue With a 180-Line Transactional SQLite WAL Job Broker & Dead-Letter CLI",
            subtitle="Sub",
            tags=["python"],
            canonical_url="https://tharushanuwansara.github.io/posts/s/",
            markdown_body="body",
            status="code_verified",
            series_day=2,
        )
        pin_copy = build_pinterest_pin_copy(sample_art, cfg_tmp)
        self.assertLessEqual(len(pin_copy["title"]), 100)
        self.assertFalse(pin_copy["title"].endswith(" C"))

        # 3. Inject high-scoring duplicate-blueprint live signals & verify 30 picks produce 30 unique blueprints
        with tempfile.TemporaryDirectory() as tmp:
            db = PipelineDatabase(Path(tmp) / "online_dedup.db")
            seeds = [s.with_defaults("2026-11-18T13:00:00Z") for s in DETERMINISTIC_PILLAR_SEEDS]
            noisy_live = [
                TopicSignal(
                    pillar="pillar_a",
                    blueprint_key="pillar_a_circuit_breaker_json",
                    title=f"High-Score Live HN Noise #{i}",
                    source="hn_algolia",
                    source_url="https://news.ycombinator.com/item?id=1",
                    score=235,
                    tags=["python"],
                    summary="Duplicate blueprint live noise",
                ).with_defaults("2026-11-18T13:00:00Z")
                for i in range(6)
            ]
            db.upsert_signals([*seeds, *noisy_live])

            picked_bps: list[str] = []
            for _ in range(30):
                chosen = db.select_unused_signals(limit=1)[0]
                picked_bps.append(chosen.blueprint_key)
                db.mark_signal_used(chosen.fingerprint)
            self.assertEqual(len(set(picked_bps)), 30, "All 30 daily picks must use distinct blueprints even with live noise!")

        # 4. Verify Day 30 (2026-12-17, Thu) -> Day 31 (2026-12-18, Fri) clamping & idempotent refresh
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws31"
            shutil.copytree(PROJECT_ROOT / "products", ws / "products")
            cfg = AppConfig.load(ws, environ={"EXPERIMENT_START_DATE": "2026-11-18", "EXPERIMENT_END_DATE": "2026-12-18"})
            r_dec17 = cmd_run_daily(cfg, date_str="2026-12-17", offline=True, as_json=False)
            self.assertEqual(r_dec17["series_day"], 30)
            self.assertTrue(r_dec17["composed_fresh"])
            self.assertEqual(r_dec17["devto"]["status"], "skipped_non_mwf_cadence")

            r_dec18 = cmd_run_daily(cfg, date_str="2026-12-18", offline=True, as_json=False)
            self.assertEqual(r_dec18["series_day"], 30)
            self.assertFalse(r_dec18["composed_fresh"])
            self.assertEqual(r_dec18["article"]["slug"], r_dec17["article"]["slug"])
            self.assertEqual(r_dec18["devto"]["status"], "dry_run")


if __name__ == "__main__":
    unittest.main()
