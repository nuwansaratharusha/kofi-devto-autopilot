"""Comprehensive tests for the 3rd Nas.io Challenge SKU, 7-day Patreon batch, Nas.io 1-hr pack, log-order & webhooks."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from autopilot import (
    build_arg_parser,
    cmd_batch_patreon,
    cmd_build_products,
    cmd_log_metrics,
    cmd_log_order,
    cmd_run_daily,
    create_dashboard_handler,
    render_dashboard_html,
)
from engine.composer import compose_article
from engine.config import AppConfig
from engine.db import PipelineDatabase
from engine.devto_client import HttpResult
from engine.experiment import (
    VERDICT_WORKING,
    build_experiment_report,
    normalize_utm_channel,
    render_experiment_markdown,
)
from engine.harvester import DETERMINISTIC_PILLAR_SEEDS
from engine.models import VALID_CHANNELS
from engine.nasio_client import (
    NasioWebhookError,
    build_logged_order,
    parse_nasio_webhook_payload,
    usd_to_cents,
    verify_nasio_webhook_auth,
)
from engine.patreon_client import PatreonClient, generate_patreon_weekly_batch
from engine.verifier import verify_article


def _http_post(
    url: str,
    raw_body: bytes,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, object]]:
    req_headers = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url=url, data=raw_body, headers=req_headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return int(resp.status), json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        try:
            body_bytes = err.read()
            parsed = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
        finally:
            err.close()
        return int(err.code), parsed


class TestNasioAndPatreonBatch(unittest.TestCase):
    def test_third_sku_challenge_pack_deterministic_zip_and_embedded_tests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            shutil.copytree(PROJECT_ROOT / "products", ws / "products")
            shutil.rmtree(ws / "products" / "dist", ignore_errors=True)

            cfg = AppConfig.load(
                ws,
                environ={
                    "NASIO_STORE_URL": "https://nas.io/nuwansarat",
                    "PATREON_URL": "https://www.patreon.com/NuwansaraT",
                },
            )
            res = cmd_build_products(cfg, as_json=False)
            self.assertEqual(len(res["products"]), 3)

            chal = next(
                p for p in res["products"] if p["slug"] == "nasio-7day-ai-automation-challenge-pack"
            )
            self.assertEqual(chal["price_usd"], 15)
            self.assertTrue(chal["python_tests_passed"])
            self.assertEqual(chal["zip_filename"], "nasio-7day-ai-automation-challenge-pack.zip")

            zip_file = Path(chal["zip_path"])
            self.assertTrue(zip_file.is_file())
            with zipfile.ZipFile(zip_file, "r") as zf:
                self.assertIsNone(zf.testzip())
                names = set(zf.namelist())
                for day_idx in range(1, 8):
                    self.assertTrue(
                        any(f"day0{day_idx}_" in name and name.endswith(".py") for name in names),
                        f"Missing day0{day_idx} lab in zip",
                    )
                self.assertIn("nasio-7day-ai-automation-challenge-pack/test_challenge_kit.py", names)
                self.assertIn("nasio-7day-ai-automation-challenge-pack/CHALLENGE_WORKBOOK.md", names)
                self.assertIn("nasio-7day-ai-automation-challenge-pack/LICENSE", names)

            # Extract zip into isolated temp dir and run embedded Day 7 capstone demo
            extract_dir = Path(tmp) / "extracted"
            with zipfile.ZipFile(zip_file, "r") as zf:
                zf.extractall(extract_dir)
            kit_dir = extract_dir / "nasio-7day-ai-automation-challenge-pack"
            demo_proc = subprocess.run(
                [sys.executable, "-I", "day07_end_to_end_agent_pipeline.py", "--demo"],
                cwd=str(kit_dir),
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertEqual(demo_proc.returncode, 0, demo_proc.stderr)
            demo_json = json.loads(demo_proc.stdout)
            self.assertEqual(demo_json["worker_first"]["result"]["jobs_fulfilled"], 1)

            guide_md = Path(res["nasio_setup_path"]).read_text(encoding="utf-8")
            self.assertIn("Pass fees to buyers = ON", guide_md)
            self.assertIn("$10 USD", guide_md)
            self.assertIn("$65 USD", guide_md)
            self.assertIn("nasio-7day-ai-automation-challenge-pack.zip", guide_md)

    def test_patreon_weekly_batch_generator_idempotency_validation_and_token_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            shutil.copytree(PROJECT_ROOT / "products", ws / "products")
            cfg = AppConfig.load(
                ws,
                environ={
                    "PATREON_URL": "https://www.patreon.com/NuwansaraT",
                    "PATREON_CAMPAIGN_ID": "16519195",
                    "NASIO_STORE_URL": "https://nas.io/nuwansarat",
                },
            )

            # Boundary & invalid input checks
            with self.assertRaises(ValueError):
                generate_patreon_weekly_batch(cfg, start_date="2026-11-18", days=0, offline=True)
            with self.assertRaises(ValueError):
                generate_patreon_weekly_batch(cfg, start_date="2026-11-18", days=31, offline=True)
            with self.assertRaises(ValueError):
                generate_patreon_weekly_batch(cfg, start_date="not-a-date", days=7, offline=True)

            # Generate 7-day batch offline
            b1 = cmd_batch_patreon(cfg, start_date="2026-11-18", days=7, offline=True, as_json=False)
            self.assertEqual(b1["days"], 7)
            self.assertEqual(len(b1["items"]), 7)
            self.assertTrue(all(it["composed_fresh"] for it in b1["items"]))
            self.assertEqual([it["series_day"] for it in b1["items"]], [1, 2, 3, 4, 5, 6, 7])
            self.assertEqual(len(b1["attachments"]), 3)

            batch_dir = Path(b1["batch_dir"])
            self.assertTrue((batch_dir / "manifest.json").is_file())
            self.assertTrue((batch_dir / "README_WEEKLY_QUEUE.md").is_file())
            self.assertEqual(len(list(batch_dir.glob("day*_public.md"))), 7)
            self.assertEqual(len(list(batch_dir.glob("day*_paid_tier_and_shop.md"))), 7)

            # Second run on same 7-day window must reuse existing DB articles idempotently
            b2 = cmd_batch_patreon(cfg, start_date="2026-11-18", days=7, offline=True, as_json=False)
            self.assertTrue(all(not it["composed_fresh"] for it in b2["items"]))
            self.assertEqual([it["slug"] for it in b1["items"]], [it["slug"] for it in b2["items"]])

            # Test PatreonClient automatic 401 -> OAuth2 refresh_token exchange + batch reconciliation
            cfg_oauth = AppConfig.load(
                ws,
                environ={
                    "PATREON_URL": "https://www.patreon.com/NuwansaraT",
                    "PATREON_CAMPAIGN_ID": "16519195",
                    "PATREON_CREATOR_ACCESS_TOKEN": "expired_tok_001",
                    "PATREON_CREATOR_REFRESH_TOKEN": "refresh_tok_999",
                    "PATREON_CLIENT_ID": "client_id_abc",
                    "PATREON_CLIENT_SECRET": "client_sec_xyz",
                },
            )
            calls: list[tuple[str, str, str]] = []

            def refresh_mock_sender(req: urllib.request.Request, _timeout: float) -> HttpResult:
                auth_hdr = req.get_header("Authorization") or ""
                calls.append((req.get_method(), req.full_url, auth_hdr))
                if "api/oauth2/token" in req.full_url:
                    return HttpResult(
                        status=200,
                        headers={},
                        body=b'{"access_token":"fresh_tok_002"}',
                    )
                if auth_hdr == "Bearer expired_tok_001":
                    return HttpResult(status=401, headers={}, body=b'{"errors":[{"status":"401"}]}')
                self.assertEqual(auth_hdr, "Bearer fresh_tok_002")
                return HttpResult(
                    status=200,
                    headers={},
                    body=json.dumps(
                        {
                            "data": [
                                {
                                    "id": "pt_paid_day1_locked",
                                    "attributes": {
                                        "title": b1["items"][0]["paid_title"],
                                        "url": "/posts/day-1-paid-locked",
                                        "is_public": False,
                                        "is_paid": True,
                                    },
                                },
                                {
                                    "id": "pt_live_day1",
                                    "attributes": {
                                        "title": b1["items"][0]["public_title"],
                                        "url": "/posts/day-1-live",
                                        "is_public": True,
                                        "is_paid": False,
                                    },
                                },
                            ]
                        }
                    ).encode("utf-8"),
                )

            pt_reconciled = generate_patreon_weekly_batch(
                cfg_oauth,
                start_date="2026-11-18",
                days=2,
                offline=False,
                sender=refresh_mock_sender,
            )
            self.assertEqual(pt_reconciled["reconciliation_mode"], "patreon_api_v2_synced")
            self.assertTrue(pt_reconciled["items"][0]["reconciled_live"])
            self.assertEqual(pt_reconciled["items"][0]["patreon_post_id"], "pt_live_day1")
            self.assertEqual(
                pt_reconciled["items"][0]["patreon_url"],
                "https://www.patreon.com/posts/day-1-live",
            )
            self.assertEqual(len(calls), 3)  # initial 401 -> POST token -> retried 200 GET

            # End-of-series boundary: batch starting Day 28 (2026-12-15) for 7 days caps cleanly at Day 30
            tail_batch = generate_patreon_weekly_batch(
                cfg,
                start_date="2026-12-15",
                days=7,
                offline=True,
            )
            self.assertEqual([it["series_day"] for it in tail_batch["items"]], [28, 29, 30])
            self.assertEqual(tail_batch["days"], 3)
            self.assertEqual(tail_batch["requested_days"], 7)

    def test_nasio_config_precedence_verifier_and_daily_60min_pack_link_cap(self) -> None:
        self.assertIn("nasio", VALID_CHANNELS)
        self.assertEqual(normalize_utm_channel("nas.io"), "nasio")
        self.assertEqual(normalize_utm_channel("magic_reach"), "nasio")

        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            shutil.copytree(PROJECT_ROOT / "products", ws / "products")
            cfg = AppConfig.load(
                ws,
                environ={
                    "NASIO_STORE_URL": "https://nas.io/nuwansarat",
                    "PATREON_URL": "https://www.patreon.com/NuwansaraT",
                },
            )
            self.assertEqual(cfg.free_checkout_url, "https://nas.io/nuwansarat")
            self.assertEqual(cfg.paid_checkout_url, "https://nas.io/nuwansarat")

            # Verify sandbox verifier recognizes nas.io URLs as valid storefront links
            sig = DETERMINISTIC_PILLAR_SEEDS[0].with_defaults("2026-11-20T13:00:00Z")
            draft = compose_article(sig, cfg, series_day=1)
            _, v_rep = verify_article(draft, config=cfg)
            self.assertTrue(v_rep.passed, v_rep.lint_errors)
            self.assertEqual(v_rep.storefront_link_count, 2)

            daily = cmd_run_daily(cfg, date_str="2026-11-20", offline=True, as_json=False)
            self.assertEqual(daily["nasio"]["status"], "pack_ready")
            pack_path = Path(daily["nasio"]["nasio_daily_pack"])
            self.assertTrue(pack_path.is_file())
            pack_text = pack_path.read_text(encoding="utf-8")

            self.assertIn("Block 1 (`00–15 min`)", pack_text)
            self.assertIn("Block 2 (`15–40 min`)", pack_text)
            self.assertIn("Block 3 (`40–60 min`)", pack_text)
            self.assertIn("Magic Reach", pack_text)

            # Enforce <= 2 https:// links inside Block 2's ready-to-post social text block
            block2_section = pack_text.split("## Block 2")[1].split("## Block 3")[0]
            fenced_match = re.search(r"```text\n(.*?)```", block2_section, re.DOTALL)
            self.assertIsNotNone(fenced_match)
            assert fenced_match is not None
            hook_links = re.findall(r"https://\S+", fenced_match.group(1))
            self.assertLessEqual(len(hook_links), 2)
            self.assertGreaterEqual(len(hook_links), 1)

    def test_log_order_cli_webhook_endpoint_and_scoreboard_hygiene(self) -> None:
        # Exact Decimal cent conversion, currency symbols, Zapier string-bools & validation edge cases
        self.assertEqual(usd_to_cents(14.99), 1499)
        self.assertEqual(usd_to_cents("15.00"), 1500)
        self.assertEqual(usd_to_cents("$15.00"), 1500)
        self.assertEqual(usd_to_cents("USD 1,250.50"), 125050)
        self.assertEqual(usd_to_cents(0), 0)
        with self.assertRaises(NasioWebhookError):
            usd_to_cents(True)
        with self.assertRaises(NasioWebhookError):
            usd_to_cents(-1.0)
        with self.assertRaises(NasioWebhookError):
            usd_to_cents("-$15.00")
        with self.assertRaises(NasioWebhookError):
            usd_to_cents("NaN")
        with self.assertRaises(NasioWebhookError):
            build_logged_order(storefront="unknown", order_id="x1", gross_usd=10)
        with self.assertRaises(NasioWebhookError):
            build_logged_order(storefront="nasio", order_id="   ", gross_usd=10)
        with self.assertRaises(NasioWebhookError):
            parse_nasio_webhook_payload({"amount": 15.0})  # missing order_id
        with self.assertRaises(NasioWebhookError):
            parse_nasio_webhook_payload({"order_id": "b1", "gross_cents": True})
        with self.assertRaises(NasioWebhookError):
            parse_nasio_webhook_payload({"order_id": "b2", "fee_cents": "not_an_int"})
        with self.assertRaises(NasioWebhookError):
            parse_nasio_webhook_payload({"order_id": "b3", "net_cents": -10})

        # Zapier stringified boolean regression check: "refunded": "false" MUST NOT mark order refunded!
        zap_paid = parse_nasio_webhook_payload(
            {
                "transaction_id": "zap_str_bool_1",
                "amount": "$15.00",
                "refunded": "false",
                "pass_fees_to_buyer": "true",
            }
        )
        self.assertFalse(zap_paid.refunded)
        self.assertEqual(zap_paid.gross_cents, 1500)
        self.assertEqual(zap_paid.fee_cents, 0)
        self.assertEqual(zap_paid.net_cents, 1500)

        zap_absorbed_fees = parse_nasio_webhook_payload(
            {
                "transaction_id": "zap_str_bool_2",
                "amount": "$15.00",
                "refunded": "0",
                "pass_fees_to_buyer": "false",
            }
        )
        self.assertFalse(zap_absorbed_fees.refunded)
        self.assertGreater(zap_absorbed_fees.fee_cents, 0)
        self.assertEqual(zap_absorbed_fees.net_cents, 1500 - zap_absorbed_fees.fee_cents)

        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            shutil.copytree(PROJECT_ROOT / "products", ws / "products")
            secret = "nasio_wh_secret_test_2026"
            cfg = AppConfig.load(
                ws,
                environ={
                    "NASIO_STORE_URL": "https://nas.io/nuwansarat",
                    "NASIO_WEBHOOK_SECRET": secret,
                    "PATREON_URL": "https://www.patreon.com/NuwansaraT",
                    "CANONICAL_BASE_URL": "https://nuwansaratharusha.github.io/kofi-devto-autopilot",
                },
            )
            daily = cmd_run_daily(cfg, date_str="2026-11-20", offline=True, as_json=False)
            slug = daily["article"]["slug"]

            # 1. Log 2 x $15 Challenge seats + 1 free lead via cmd_log_order
            o1 = cmd_log_order(
                cfg,
                storefront="nasio",
                order_id="nas_cli_1",
                gross_usd=15.0,
                utm_source="nasio",
                sku="nasio-7day-ai-automation-challenge-pack",
                slug=slug,
            )
            self.assertEqual(o1["order"]["net_cents"], 1500)
            self.assertTrue(o1["storefront_meter"]["payoneer_unlocked"])  # $15 >= $10 Payoneer USD min

            cmd_log_order(
                cfg,
                storefront="nasio",
                order_id="nas_cli_free",
                gross_usd=0.0,
                utm_source="nasio",
                slug=slug,
            )

            # Refunded order must contribute $0 net
            cmd_log_order(
                cfg,
                storefront="nasio",
                order_id="nas_cli_refund",
                gross_usd=15.0,
                refunded=True,
            )

            # 2. Boot localhost server and test POST /api/webhooks/nasio security & ingestion
            server = ThreadingHTTPServer(("127.0.0.1", 0), create_dashboard_handler(cfg))
            port = server.server_address[1]
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            try:
                wh_url = f"http://127.0.0.1:{port}/api/webhooks/nasio"

                # Unknown POST route -> 404
                st_404, _ = _http_post(f"http://127.0.0.1:{port}/api/unknown", b"{}")
                self.assertEqual(st_404, 404)

                # Empty body -> 400
                st_empty, _ = _http_post(wh_url, b"")
                self.assertEqual(st_empty, 400)

                # Missing signature -> 401
                valid_payload = json.dumps(
                    {
                        "transaction_id": "nas_wh_2",
                        "amount_usd": "35.00",
                        "sku": "nasio-7day-ai-automation-challenge-pack",
                        "utm_source": "nasio",
                        "slug": slug,
                    }
                ).encode("utf-8")
                st_401, _ = _http_post(wh_url, valid_payload)
                self.assertEqual(st_401, 401)

                # Oversized payload (> 64 KiB) -> 413
                st_413, _ = _http_post(
                    wh_url,
                    b"x" * 70_000,
                    headers={"X-Webhook-Secret": secret},
                )
                self.assertEqual(st_413, 413)

                # Malformed JSON with valid secret -> 400
                st_bad_json, _ = _http_post(
                    wh_url,
                    b"{not_json",
                    headers={"X-Webhook-Secret": secret},
                )
                self.assertEqual(st_bad_json, 400)

                # Non-ASCII latin-1 HTTP header bytes must return 401 cleanly (never TypeError crash)
                st_non_ascii, _ = _http_post(
                    wh_url,
                    valid_payload,
                    headers={"X-Nasio-Signature": "sha256=\xe9bad", "X-Webhook-Secret": "\xe9secret"},
                )
                self.assertEqual(st_non_ascii, 401)
                self.assertFalse(
                    verify_nasio_webhook_auth(
                        b"{}",
                        {"Authorization": "Bearer \u2603non_ascii"},
                        secret,
                    )
                )

                # Valid HMAC SHA-256 signature -> 200 OK ($15 + $35 = $50.00 target unlocked!)
                sig_hex = hmac.new(secret.encode("utf-8"), valid_payload, hashlib.sha256).hexdigest()
                st_200, body_200 = _http_post(
                    wh_url,
                    valid_payload,
                    headers={"X-Nasio-Signature": f"sha256={sig_hex}"},
                )
                self.assertEqual(st_200, 200)
                self.assertTrue(body_200["ok"])
                meter = body_200["nasio_meter"]
                assert isinstance(meter, dict)
                self.assertEqual(meter["net_usd"], 50.0)
                self.assertTrue(meter["unlocked"])

                # Concurrent burst of 6 Zapier-style webhooks ($5.00 each, formatted "$5.00", "refunded": "false")
                burst_statuses: list[int] = []
                burst_lock = threading.Lock()

                def _fire_burst_order(idx: int) -> None:
                    p_bytes = json.dumps(
                        {
                            "transaction_id": f"nas_burst_{idx}",
                            "amount": "$5.00",
                            "refunded": "false",
                            "utm_source": "nasio",
                            "slug": slug,
                        }
                    ).encode("utf-8")
                    code, _ = _http_post(wh_url, p_bytes, headers={"X-Webhook-Secret": secret})
                    with burst_lock:
                        burst_statuses.append(code)

                workers = [threading.Thread(target=_fire_burst_order, args=(i,)) for i in range(6)]
                for w in workers:
                    w.start()
                for w in workers:
                    w.join(timeout=10)
                self.assertEqual(burst_statuses, [200] * 6)

                # Static site (`GET /site/index.html`) must immediately reflect $50 + 6*$5 = $80.00 WITHOUT manual rebuild!
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/site/index.html", timeout=5) as hub_resp:
                    live_hub_html = hub_resp.read().decode("utf-8")
                self.assertIn("$80.00 / $50 USD", live_hub_html)
                self.assertIn("STOREFRONT #3 · LEMON SQUEEZY", live_hub_html)
                self.assertIn("STOREFRONT #4 · GUMROAD", live_hub_html)

                # Also verify Authorization: Bearer <secret> auth mode
                self.assertTrue(
                    verify_nasio_webhook_auth(
                        b"{}",
                        {"Authorization": f"Bearer {secret}"},
                        secret,
                    )
                )

                # Verify dashboard HTML & static hub HTML hygiene, tap targets, and zero ko-fi regressions
                cmd_log_metrics(cfg, slug=slug, channel="nasio", views=180, clicks=22)
                db = PipelineDatabase(cfg.db_path)
                rep = build_experiment_report(db, cfg, as_of="2026-11-20")
                self.assertEqual(rep["channels"]["nasio"]["verdict"], VERDICT_WORKING)

                md_rep = render_experiment_markdown(rep)
                self.assertIn("**Nas.io**", md_rep)
                self.assertIn("TARGET HIT ($50+)", md_rep)

                dash_html = render_dashboard_html(cfg)
                dash_no_scripts = re.sub(r"<script.*?</script>", "", dash_html, flags=re.DOTALL)
                self.assertIn("Copy Nas.io 1-Hr Pack", dash_no_scripts)
                self.assertIn("Nas.io ($10 Payoneer USD / $65 LKR Wire)", dash_no_scripts)
                self.assertIn("Patreon Payoneer Unlock Meter", dash_no_scripts)
                self.assertNotIn("`", dash_no_scripts)
                self.assertNotIn("ko-fi.com", dash_no_scripts.lower())
                self.assertIn("min-height: 44px;", dash_html)
                self.assertIn("overflow-x: clip;", dash_html)

                site_html = (cfg.site_dir / "index.html").read_text(encoding="utf-8")
                site_no_scripts = re.sub(r"<script.*?</script>", "", site_html, flags=re.DOTALL)
                self.assertIn("STOREFRONT #1 · NAS.IO", site_no_scripts)
                self.assertIn("STOREFRONT #2 · PATREON", site_no_scripts)
                self.assertNotIn("`", site_no_scripts)
                self.assertNotIn("ko-fi.com", site_no_scripts.lower())
            finally:
                server.shutdown()
                server.server_close()

            # Verify CLI parser subcommands parse cleanly without touching live repo DB
            parser = build_arg_parser()
            parsed_batch = parser.parse_args(["batch-patreon", "--start-date", "2026-11-18", "--days", "7", "--json"])
            self.assertEqual((parsed_batch.command, parsed_batch.days, parsed_batch.json), ("batch-patreon", 7, True))
            parsed_order = parser.parse_args(
                ["log-order", "--storefront", "nasio", "--order-id", "o1", "--gross-usd", "$15.00", "--json"]
            )
            self.assertEqual((parsed_order.command, parsed_order.storefront, parsed_order.gross_usd), ("log-order", "nasio", "$15.00"))


if __name__ == "__main__":
    unittest.main()
