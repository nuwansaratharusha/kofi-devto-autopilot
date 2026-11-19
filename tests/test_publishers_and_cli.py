"""Tests for Forem DEV.to client, static canonical HTML/RSS builder, exporters & localhost server."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from http.server import ThreadingHTTPServer
from pathlib import Path

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from autopilot import cmd_run_pipeline, create_dashboard_handler
from engine.config import AppConfig
from engine.devto_client import DevtoClient, DevtoPublishError, HttpResult, sanitize_devto_tags
from engine.models import ArticleRecord

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestPublishersAndCli(unittest.TestCase):
    def test_tag_sanitizer_enforces_forem_alphanumeric_and_four_tag_limit(self) -> None:
        tags = sanitize_devto_tags(["Python 3.14!", "Node.js", "C++", "SQL-WAL", "ExtraTagIgnored"])
        self.assertEqual(tags, ["python314", "nodejs", "cpp", "sqlwal"])

    def test_devto_client_refuses_unverified_and_handles_422_fallback_and_idempotent_put(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = AppConfig.load(
                Path(tmp),
                environ={"DEVTO_API_KEY": "forem_test_key", "PUBLISH_MODE": "draft"},
            )
            unverified = ArticleRecord(
                slug="unverified-post",
                signal_fingerprint="fp",
                pillar="pillar_a",
                scheduled_slot="Mon",
                title="Draft",
                subtitle="Sub",
                tags=["python"],
                canonical_url="https://tharushanuwansara.github.io/posts/unverified-post/",
                markdown_body="body",
                status="composed",
            )
            client_guard = DevtoClient(cfg)
            with self.assertRaises(DevtoPublishError):
                client_guard.stage_or_publish(unverified)

            # Verify 422 fallback -> POST 201 -> subsequent call uses PUT /api/articles/445566
            recorded_requests: list[tuple[str, str, dict]] = []
            responses = [
                HttpResult(status=422, headers={}, body=b'{"error":"unknown attribute ai_disclosure_level"}'),
                HttpResult(
                    status=201,
                    headers={},
                    body=b'{"id":445566,"url":"https://dev.to/tharusha/draft-445566","published":false}',
                ),
                HttpResult(
                    status=200,
                    headers={},
                    body=b'{"id":445566,"url":"https://dev.to/tharusha/draft-445566","published":false}',
                ),
            ]

            def fake_sender(req: urllib.request.Request, _timeout: float) -> HttpResult:
                body_dict = json.loads((req.data or b"{}").decode("utf-8"))
                recorded_requests.append((req.get_method(), req.full_url, body_dict))
                return responses.pop(0)

            verified = ArticleRecord(
                slug="verified-post",
                signal_fingerprint="fp",
                pillar="pillar_a",
                scheduled_slot="Mon",
                title="Verified Engineering Post",
                subtitle="Subtitle",
                tags=["python", "node.js"],
                canonical_url="https://tharushanuwansara.github.io/posts/verified-post/",
                markdown_body="Verified content",
                status="code_verified",
            )
            client = DevtoClient(cfg, sender=fake_sender, sleeper=lambda _s: None)
            res1 = client.stage_or_publish(verified)
            self.assertEqual(res1["status"], "staged_draft")
            self.assertEqual(res1["operation"], "created")
            self.assertTrue(res1["used_422_fallback"])
            self.assertEqual(verified.devto_article_id, 445566)
            self.assertFalse(recorded_requests[0][2]["article"]["published"])
            self.assertIn("ai_disclosure_level", recorded_requests[0][2]["article"])
            self.assertNotIn("ai_disclosure_level", recorded_requests[1][2]["article"])

            # Second push must perform idempotent PUT update rather than creating duplicate draft
            res2 = client.stage_or_publish(verified)
            self.assertEqual(res2["operation"], "updated")
            self.assertEqual(recorded_requests[2][0], "PUT")
            self.assertTrue(recorded_requests[2][1].endswith("/445566"))

    def test_end_to_end_pipeline_site_rss_exports_and_localhost_dashboard_routes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            shutil.copytree(PROJECT_ROOT / "products", workspace / "products")
            cfg = AppConfig.load(
                workspace,
                environ={
                    "DEVTO_API_KEY": "",  # dry-run mode
                    "KOFI_PROFILE_URL": "https://ko-fi.com/tharushanuwansara",
                    "PRIMARY_STORE_URL": "https://ko-fi.com/tharushanuwansara/shop",
                    "MIRROR_STORE_URL": "https://polar.sh/tharushanuwansara",
                    "CANONICAL_BASE_URL": "https://tharushanuwansara.github.io/kofi-devto-autopilot",
                },
            )

            summary = cmd_run_pipeline(cfg, limit=2, push_draft=True, offline=True, as_json=False)
            self.assertEqual(summary["articles_verified_this_run"], 2)

            # Validate RSS 2.0 & Sitemap XML syntax
            rss_tree = ET.parse(cfg.site_dir / "feed.xml")
            self.assertEqual(len(rss_tree.findall("./channel/item")), 2)
            sitemap_tree = ET.parse(cfg.site_dir / "sitemap.xml")
            self.assertGreaterEqual(len(sitemap_tree.getroot()), 3)

            # Validate Microformats2 h-entry & canonical link for Medium Import
            slug = summary["articles_processed"][0]["slug"]
            post_html = (cfg.site_dir / "posts" / slug / "index.html").read_text(encoding="utf-8")
            self.assertIn('class="h-entry"', post_html)
            self.assertIn('rel="canonical"', post_html)
            self.assertIn("https://polar.sh/tharushanuwansara", post_html)

            # Boot localhost review server on ephemeral port and verify HTTP endpoints + traversal block
            server = ThreadingHTTPServer(("127.0.0.1", 0), create_dashboard_handler(cfg))
            port = server.server_address[1]
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r_dash:
                    dash_html = r_dash.read().decode("utf-8")
                self.assertIn('aria-live="polite"', dash_html)
                self.assertIn("Copy Patreon Public MD", dash_html)
                self.assertIn("Copy Pinterest Pin JSON", dash_html)
                self.assertNotIn("Copy Ko-fi Post", dash_html)

                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=5) as r_api:
                    api_json = json.loads(r_api.read().decode("utf-8"))
                self.assertEqual(api_json["counts"]["articles_verified"], 2)

                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/dist/free-idempotent-cron-mini-kit.zip", timeout=5
                ) as zip_resp:
                    self.assertEqual(zip_resp.status, 200)
                    self.assertEqual(zip_resp.headers["Content-Type"], "application/zip")

                # Path traversal attempt must return 404
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/dist/../../pyproject.toml", timeout=5)
                try:
                    self.assertEqual(ctx.exception.code, 404)
                finally:
                    ctx.exception.close()
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
