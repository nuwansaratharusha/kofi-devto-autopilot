"""Unit tests for Patreon API v2 sync, post reconciliation, $25 payout meter, and dashboard packs."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from autopilot import cmd_log_metrics, cmd_run_daily, render_dashboard_html
from engine.config import AppConfig
from engine.db import PipelineDatabase
from engine.devto_client import HttpResult
from engine.experiment import VERDICT_WORKING, build_experiment_report
from engine.models import ArticleRecord
from engine.patreon_client import PatreonClient, export_patreon_bundle
from engine.storefronts import sync_storefronts


class TestPatreonOmnichannelIntegration(unittest.TestCase):
    def test_patreon_v2_campaign_discovery_member_sync_and_post_reconciliation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            shutil.copytree(Path(_PROJECT_ROOT) / "products", ws / "products")

            cfg = AppConfig.load(
                project_root=ws,
                environ={
                    "PATREON_URL": "https://www.patreon.com/c/tharushanuwansara",
                    "PATREON_CREATOR_ACCESS_TOKEN": "pat_v2_test_token_123",
                    "LEMONSQUEEZY_STORE_URL": "https://tharusha.lemonsqueezy.com",
                    "GUMROAD_STORE_URL": "https://tharusha.gumroad.com",
                },
            )
            db = PipelineDatabase(cfg.db_path)

            seen_urls: list[str] = []

            def fake_patreon_sender(req: urllib.request.Request, _timeout: float) -> HttpResult:
                seen_urls.append(req.full_url)
                self.assertEqual(req.get_header("Authorization"), "Bearer pat_v2_test_token_123")
                self.assertIn("KofiDevtoAutopilot", req.get_header("User-agent") or "")
                if "/campaigns?" in req.full_url:
                    body = {
                        "data": [
                            {
                                "id": "camp_9988",
                                "type": "campaign",
                                "attributes": {
                                    "creation_name": "Production Python Reliability",
                                    "patron_count": 4,
                                },
                            }
                        ]
                    }
                    return HttpResult(status=200, headers={}, body=json.dumps(body).encode("utf-8"))
                if "/campaigns/camp_9988/members" in req.full_url:
                    body = {
                        "data": [
                            {
                                "id": "mem_paid_1",
                                "type": "member",
                                "attributes": {
                                    "full_name": "Alice Builder",
                                    "patron_status": "active_patron",
                                    "currently_entitled_amount_cents": 1500,
                                    "lifetime_support_cents": 3000,
                                    "pledge_relationship_start": "2026-11-19T10:00:00Z",
                                },
                            },
                            {
                                "id": "mem_paid_2",
                                "type": "member",
                                "attributes": {
                                    "full_name": "Bob Systems",
                                    "patron_status": "active_patron",
                                    "currently_entitled_amount_cents": 1500,
                                    "lifetime_support_cents": 1500,
                                    "pledge_relationship_start": "2026-11-20T09:00:00Z",
                                },
                            },
                            {
                                "id": "mem_free_1",
                                "type": "member",
                                "attributes": {
                                    "full_name": "Free Follower",
                                    "patron_status": "active_patron",
                                    "currently_entitled_amount_cents": 0,
                                    "lifetime_support_cents": 0,
                                    "pledge_relationship_start": "2026-11-20T11:00:00Z",
                                },
                            },
                            {
                                "id": "mem_declined",
                                "type": "member",
                                "attributes": {
                                    "full_name": "Declined Card",
                                    "patron_status": "declined_patron",
                                    "currently_entitled_amount_cents": 500,
                                    "lifetime_support_cents": 0,
                                    "pledge_relationship_start": "2026-11-20T12:00:00Z",
                                },
                            },
                        ],
                        "links": {},
                    }
                    return HttpResult(status=200, headers={}, body=json.dumps(body).encode("utf-8"))
                if "/campaigns/camp_9988/posts" in req.full_url:
                    body = {
                        "data": [
                            {
                                "id": "post_77001",
                                "type": "post",
                                "attributes": {
                                    "title": "[Day 1/30] Zero-Dependency Python Circuit Breakers",
                                    "url": "/posts/day-1-30-zero-77001",
                                    "is_public": True,
                                    "is_paid": False,
                                    "published_at": "2026-11-18T13:00:00Z",
                                },
                            }
                        ],
                        "links": {},
                    }
                    return HttpResult(status=200, headers={}, body=json.dumps(body).encode("utf-8"))
                raise AssertionError(f"Unexpected URL: {req.full_url}")

            # 1. Run Day 1 offline to generate verified article + Patreon public/paid Markdown packs
            cfg_no_token = AppConfig.load(project_root=ws, environ={})
            daily_res = cmd_run_daily(cfg_no_token, date_str="2026-11-18", offline=True, as_json=False)
            self.assertEqual(daily_res["patreon"]["status"], "pack_ready")
            slug1 = daily_res["article"]["slug"]
            self.assertTrue((cfg.exports_dir / slug1 / "patreon_public_post.md").is_file())
            self.assertTrue((cfg.exports_dir / slug1 / "patreon_paid_tier_post.md").is_file())

            # 2. Reconcile post via Patreon API v2 + sync members into store_orders
            art = db.get_article(slug1)
            assert art is not None
            pt_client = PatreonClient(cfg, sender=fake_patreon_sender, sleeper=lambda _s: None)
            rec_res = pt_client.package_and_reconcile(art, db=db)
            self.assertEqual(rec_res["status"], "published_live")
            self.assertEqual(art.patreon_post_id, "post_77001")
            self.assertEqual(art.patreon_url, "https://www.patreon.com/posts/day-1-30-zero-77001")

            sync_res = sync_storefronts(cfg, db, patreon_sender=fake_patreon_sender)
            self.assertEqual(sync_res["patreon"]["status"], "synced")
            self.assertEqual(sync_res["patreon"]["campaign_id"], "camp_9988")

            # 3. Verify Patreon $25 Payoneer meter unlocks ($30 gross -> ~$26.14 net >= $25.00!)
            cmd_log_metrics(cfg, slug=slug1, channel="patreon", views=210, clicks=28)
            rep = build_experiment_report(db, cfg, as_of="2026-11-20")
            pt_meter = rep["storefronts"]["patreon"]
            self.assertEqual(pt_meter["paid_orders"], 2)
            self.assertEqual(pt_meter["free_downloads"], 1)
            self.assertEqual(pt_meter["refunded_orders"], 1)
            self.assertTrue(pt_meter["unlocked"])
            self.assertEqual(rep["channels"]["patreon"]["verdict"], VERDICT_WORKING)

            # 4. Verify dashboard HTML exposes Patreon meter & 1-click Copy buttons cleanly
            dash_html = render_dashboard_html(cfg)
            self.assertIn("Patreon Payoneer Unlock Meter", dash_html)
            self.assertIn("Copy Patreon Public MD", dash_html)
            self.assertIn("Copy Patreon Paid/Shop MD", dash_html)
            self.assertIn("Copy Patreon Tiers &amp; Shop Setup", dash_html)


if __name__ == "__main__":
    unittest.main()
