"""Comprehensive unit & integration tests for Whop REST API v1, Webhooks, Daily 1-Hr Pack & Scoreboard."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from autopilot import (
    build_arg_parser,
    cmd_build_products,
    cmd_log_metrics,
    cmd_log_order,
    cmd_run_daily,
    cmd_setup_whop,
    cmd_sync_analytics,
    create_dashboard_handler,
    render_dashboard_html,
)
from engine.analytics import sync_all_analytics
from engine.composer import compose_article
from engine.config import AppConfig
from engine.db import PipelineDatabase
from engine.devto_client import HttpResult
from engine.experiment import (
    READINESS_LIVE,
    READINESS_UNCONFIGURED_DRY_RUN,
    VERDICT_WORKING,
    build_experiment_report,
    normalize_utm_channel,
    render_experiment_markdown,
)
from engine.harvester import DETERMINISTIC_PILLAR_SEEDS
from engine.models import VALID_CHANNELS
from engine.storefronts import sync_storefronts
from engine.verifier import verify_article
from engine.whop_client import (
    WhopApiError,
    WhopClient,
    WhopWebhookError,
    estimate_whop_fee_cents,
    ingest_whop_webhook_order,
    parse_whop_webhook_payload,
    verify_whop_webhook_auth,
    whop_usd_to_cents,
)


def _http_post(
    url: str,
    raw_body: bytes,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, object]]:
    req_headers = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url=url, data=raw_body, headers=req_headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return int(resp.status), json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        try:
            body_bytes = err.read()
            parsed = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
        finally:
            err.close()
        return int(err.code), parsed


class TestWhopIntegration(unittest.TestCase):
    def test_fee_estimation_money_parsing_and_validation_edge_cases(self) -> None:
        self.assertEqual(estimate_whop_fee_cents(0), 0)
        self.assertEqual(estimate_whop_fee_cents(-50), 0)
        # $15.00 (1500c): round(1500 * 0.057) + 30 = 86 + 30 = 116c ($13.84 net)
        self.assertEqual(estimate_whop_fee_cents(1500), 116)
        # $14.00 (1400c): round(1400 * 0.057) + 30 = 80 + 30 = 110c ($12.90 net)
        self.assertEqual(estimate_whop_fee_cents(1400), 110)
        # Tiny charge clamping: fee never exceeds gross
        self.assertEqual(estimate_whop_fee_cents(15), 15)

        self.assertEqual(whop_usd_to_cents("15.00"), 1500)
        self.assertEqual(whop_usd_to_cents("$14.99"), 1499)
        self.assertEqual(whop_usd_to_cents(0), 0)
        with self.assertRaises(WhopWebhookError):
            whop_usd_to_cents(True)
        with self.assertRaises(WhopWebhookError):
            whop_usd_to_cents("-5.00")
        with self.assertRaises(WhopWebhookError):
            whop_usd_to_cents("NaN")
        with self.assertRaises(WhopWebhookError):
            parse_whop_webhook_payload({"type": "payment.succeeded", "data": {}})
        with self.assertRaises(WhopWebhookError):
            parse_whop_webhook_payload(
                {"type": "payment.succeeded", "data": {"id": "pay_bad", "amount_cents": True}}
            )
        with self.assertRaises(WhopWebhookError):
            parse_whop_webhook_payload(
                {
                    "type": "payment.succeeded",
                    "data": {"id": "pay_wrong_co", "company_id": "biz_other", "amount_cents": 1500},
                },
                expected_company_id="biz_Af4z3lgivbct7t",
            )

        # Empty WHOP_API_KEY raises WhopApiError cleanly
        empty_client = WhopClient(api_key="", company_id="biz_Af4z3lgivbct7t")
        self.assertFalse(empty_client.has_live_credentials())
        with self.assertRaises(WhopApiError):
            empty_client.list_orders()

    def test_whop_rest_client_pagination_company_filter_retries_and_refund_reconciliation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            shutil.copytree(PROJECT_ROOT / "products", ws / "products")
            cfg = AppConfig.load(
                ws,
                environ={
                    "WHOP_COMPANY_ID": "biz_Af4z3lgivbct7t",
                    "WHOP_API_KEY": "whop_live_key_test_999",
                    "WHOP_STORE_URL": "https://whop.com/nuwansarat",
                    "PATREON_URL": "https://www.patreon.com/NuwansaraT",
                },
            )
            db = PipelineDatabase(cfg.db_path)
            # Pre-seed an order that will later come back as refunded during REST sync
            db.upsert_store_orders(
                [
                    parse_whop_webhook_payload(
                        {
                            "type": "payment.succeeded",
                            "data": {"id": "pay_later_refunded", "amount_cents": 1500},
                        }
                    )  # type: ignore[list-item]
                ]
            )
            self.assertEqual(len(db.list_store_orders(include_refunded=False)), 1)

            seen_requests: list[str] = []
            sleep_calls: list[float] = []
            rate_limited_once = {"done": False}

            def fake_whop_sender(req: urllib.request.Request, _timeout: float) -> HttpResult:
                self.assertEqual(req.get_header("Authorization"), "Bearer whop_live_key_test_999")
                seen_requests.append(req.full_url)
                parsed_u = urllib.parse.urlparse(req.full_url)
                qs = urllib.parse.parse_qs(parsed_u.query)
                self.assertEqual(qs.get("company_id"), ["biz_Af4z3lgivbct7t"])

                if not rate_limited_once["done"]:
                    rate_limited_once["done"] = True
                    return HttpResult(status=429, headers={"Retry-After": "0.05"}, body=b'{"error":"rate_limited"}')

                if parsed_u.path == "/api/v1/payments":
                    if qs.get("after") == ["cur_p1"]:
                        body = {
                            "data": [
                                {
                                    "id": "pay_pro_kit_2",
                                    "company_id": "biz_Af4z3lgivbct7t",
                                    "status": "paid",
                                    "amount_cents": 1400,
                                    "fee_cents": 105,
                                    "metadata": {"utm_source": "blogger", "sku": "production-agent-automation-kit"},
                                    "created_at": "2026-11-20T09:00:00Z",
                                },
                                {
                                    "id": "pay_later_refunded",
                                    "company_id": "biz_Af4z3lgivbct7t",
                                    "status": "refunded",
                                    "refunded": True,
                                    "amount_cents": 1500,
                                },
                                {
                                    "id": "pay_failed_skip",
                                    "company_id": "biz_Af4z3lgivbct7t",
                                    "status": "failed",
                                    "amount_cents": 1500,
                                },
                            ],
                            "page_info": {"has_next_page": False, "end_cursor": "cur_p2"},
                        }
                        return HttpResult(status=200, headers={}, body=json.dumps(body).encode("utf-8"))

                    body = {
                        "data": [
                            {
                                "id": "pay_chal_1",
                                "company_id": "biz_Af4z3lgivbct7t",
                                "membership_id": "mem_paid_covered",
                                "status": "succeeded",
                                "total": {"amount": "15.00", "currency": "usd"},
                                "metadata": {
                                    "utm_source": "whop_discover",
                                    "sku": "nasio-7day-ai-automation-challenge-pack",
                                },
                                "created_at": "2026-11-19T12:00:00Z",
                            },
                            {
                                "id": "pay_foreign_company",
                                "company_id": "biz_other_company_000",
                                "status": "paid",
                                "amount_cents": 9900,
                            },
                        ],
                        "page_info": {"has_next_page": True, "end_cursor": "cur_p1"},
                    }
                    return HttpResult(status=200, headers={}, body=json.dumps(body).encode("utf-8"))

                if parsed_u.path == "/api/v1/memberships":
                    if qs.get("page") == ["2"]:
                        body = {
                            "data": [
                                {
                                    "id": "mem_canceled_skip",
                                    "company_id": "biz_Af4z3lgivbct7t",
                                    "status": "canceled",
                                    "valid": False,
                                },
                                {
                                    "id": "mem_free_lead_2",
                                    "company": {"id": "biz_Af4z3lgivbct7t"},
                                    "status": "valid",
                                    "valid": True,
                                    "metadata": {"utm_source": "devto"},
                                    "created_at": "2026-11-20T11:00:00Z",
                                },
                            ],
                            "pagination": {"current_page": 2, "total_pages": 2, "next_page": None},
                        }
                        return HttpResult(status=200, headers={}, body=json.dumps(body).encode("utf-8"))

                    body = {
                        "data": [
                            {
                                # Already covered by pay_chal_1 -> must NOT double-count!
                                "id": "mem_paid_covered",
                                "company_id": "biz_Af4z3lgivbct7t",
                                "status": "active",
                                "valid": True,
                            },
                            {
                                "id": "mem_free_lead_1",
                                "company_id": "biz_Af4z3lgivbct7t",
                                "status": "active",
                                "valid": True,
                                "metadata": {"utm_source": "whop"},
                                "created_at": "2026-11-19T14:00:00Z",
                            },
                        ],
                        "pagination": {"current_page": 1, "total_pages": 2, "next_page": 2},
                    }
                    return HttpResult(status=200, headers={}, body=json.dumps(body).encode("utf-8"))

                raise AssertionError(f"Unexpected URL: {req.full_url}")

            whop = WhopClient(cfg, sender=fake_whop_sender, sleeper=sleep_calls.append)
            direct_orders = whop.list_orders()
            # Expect 2 completed payments (pay_chal_1 + pay_pro_kit_2) + 2 free memberships (mem_free_lead_1 + mem_free_lead_2)
            self.assertEqual(len(direct_orders), 4)
            self.assertEqual(len(sleep_calls), 1)
            self.assertAlmostEqual(sleep_calls[0], 0.05, places=2)

            by_id = {o.order_id: o for o in direct_orders}
            # pay_chal_1 had no fee_cents -> ~3% + 2.7% + $0.30 = 116c -> 1384c net
            self.assertEqual(by_id["pay_chal_1"].gross_cents, 1500)
            self.assertEqual(by_id["pay_chal_1"].fee_cents, 116)
            self.assertEqual(by_id["pay_chal_1"].net_cents, 1384)
            # pay_pro_kit_2 had explicit fee_cents=105 -> 1295c net
            self.assertEqual(by_id["pay_pro_kit_2"].net_cents, 1295)
            # Free memberships have 0 gross, 0 fee, 0 net
            self.assertEqual(by_id["mem_free_lead_1"].gross_cents, 0)
            self.assertEqual(by_id["mem_free_lead_1"].fee_cents, 0)
            self.assertEqual(by_id["mem_free_lead_2"].utm_source, "devto")

            # Run full sync_storefronts and verify previously stored pay_later_refunded is reconciled to refunded!
            sync_res = sync_storefronts(cfg, db, whop_sender=fake_whop_sender)
            self.assertEqual(sync_res["whop"]["status"], "synced")
            self.assertEqual(sync_res["whop"]["payments_synced"], 2)
            self.assertEqual(sync_res["whop"]["memberships_synced"], 2)
            self.assertEqual(sync_res["whop"]["refunds_reconciled"], 1)

            active_in_db = db.list_store_orders(include_refunded=False)
            self.assertEqual(len(active_in_db), 4)
            self.assertEqual(sum(o.net_cents for o in active_in_db), 1384 + 1295)

    def test_whop_verifier_daily_pack_setup_guide_webhook_events_and_ui_hygiene(self) -> None:
        self.assertIn("whop", VALID_CHANNELS)
        self.assertEqual(normalize_utm_channel("whop"), "whop")
        self.assertEqual(normalize_utm_channel("whop_discover"), "whop")
        self.assertEqual(normalize_utm_channel("whop.com"), "whop")

        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            shutil.copytree(PROJECT_ROOT / "products", ws / "products")
            shutil.rmtree(ws / "products" / "dist", ignore_errors=True)

            raw_secret_bytes = b"whop_std_secret_bytes_32_chars!!"
            wh_secret = "whsec_" + base64.b64encode(raw_secret_bytes).decode("ascii")

            cfg = AppConfig.load(
                ws,
                environ={
                    "WHOP_COMPANY_ID": "biz_Af4z3lgivbct7t",
                    "WHOP_STORE_URL": "https://whop.com/nuwansarat",
                    "WHOP_WEBHOOK_SECRET": wh_secret,
                    "PATREON_URL": "https://www.patreon.com/NuwansaraT",
                    "CANONICAL_BASE_URL": "https://nuwansaratharusha.github.io/kofi-devto-autopilot",
                },
            )
            self.assertFalse(cfg.is_legacy_nasio_mode)
            self.assertEqual(cfg.whop_dashboard_url, "https://whop.com/dashboard/biz_Af4z3lgivbct7t/")
            self.assertEqual(cfg.free_checkout_url, "https://whop.com/nuwansarat")
            self.assertEqual(cfg.paid_checkout_url, "https://whop.com/nuwansarat")

            # 1. Build products & verify Whop setup guide tailored to biz_Af4z3lgivbct7t
            prod_res = cmd_build_products(cfg, as_json=False)
            whop_guide_path = Path(prod_res["whop_setup_path"])
            self.assertTrue(whop_guide_path.is_file())
            guide_text = whop_guide_path.read_text(encoding="utf-8")
            self.assertIn("https://whop.com/dashboard/biz_Af4z3lgivbct7t/", guide_text)
            self.assertIn("https://whop.com/dashboard/biz_Af4z3lgivbct7t/developer/", guide_text)
            self.assertIn("Whop Discover", guide_text)
            self.assertIn("WHOP_API_KEY", guide_text)
            self.assertIn("free-idempotent-cron-mini-kit.zip", guide_text)
            self.assertIn("nasio-7day-ai-automation-challenge-pack.zip", guide_text)
            self.assertIn("production-agent-automation-kit.zip", guide_text)

            # 2. Verify sandbox verifier recognizes whop.com links
            sig = DETERMINISTIC_PILLAR_SEEDS[0].with_defaults("2026-11-20T13:00:00Z")
            draft = compose_article(sig, cfg, series_day=3)
            _, v_rep = verify_article(draft, config=cfg)
            self.assertTrue(v_rep.passed, v_rep.lint_errors)
            self.assertEqual(v_rep.storefront_link_count, 2)

            # 3. Run daily pipeline and verify exports/<slug>/whop_daily_pack.md + <= 2 links in Block 2
            daily = cmd_run_daily(cfg, date_str="2026-11-20", offline=True, as_json=False)
            self.assertEqual(daily["whop"]["status"], "pack_ready")
            slug = daily["article"]["slug"]
            pack_path = Path(daily["whop"]["whop_daily_pack"])
            self.assertTrue(pack_path.is_file())
            pack_md = pack_path.read_text(encoding="utf-8")
            self.assertIn("https://whop.com/dashboard/biz_Af4z3lgivbct7t/", pack_md)
            self.assertIn("Block 1 (`00–15 min`)", pack_md)
            self.assertIn("Block 2 (`15–40 min`)", pack_md)
            self.assertIn("Block 3 (`40–60 min`)", pack_md)

            block2 = pack_md.split("## Block 2")[1].split("## Block 3")[0]
            fenced = re.search(r"```text\n(.*?)```", block2, re.DOTALL)
            self.assertIsNotNone(fenced)
            assert fenced is not None
            links = re.findall(r"https://\S+", fenced.group(1))
            self.assertEqual(len(links), 2)

            # 4. Log CLI Whop order + test POST /api/webhooks/whop (payment.succeeded, membership.went_valid, payment.refunded, payment.failed)
            cli_ord = cmd_log_order(
                cfg,
                storefront="whop",
                order_id="pay_cli_01",
                gross_usd=15.0,
                utm_source="whop",
                slug=slug,
            )
            self.assertEqual(cli_ord["order"]["net_cents"], 1384)
            self.assertTrue(cli_ord["storefront_meter"]["min_payout_unlocked"])  # $13.84 >= $10 min payout

            server = ThreadingHTTPServer(("127.0.0.1", 0), create_dashboard_handler(cfg))
            port = server.server_address[1]
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            try:
                wh_url = f"http://127.0.0.1:{port}/api/webhooks/whop"

                # Unauthorized without signature -> 401
                evt_succeeded = json.dumps(
                    {
                        "id": "msg_evt_001",
                        "type": "payment.succeeded",
                        "data": {
                            "id": "pay_wh_02",
                            "company_id": "biz_Af4z3lgivbct7t",
                            "total": {"amount": "40.00", "currency": "usd"},
                            "amount_after_fees": {"amount": "37.42", "currency": "usd"},
                            "metadata": {"utm_source": "whop", "slug": slug},
                        },
                    }
                ).encode("utf-8")
                st_401, _ = _http_post(wh_url, evt_succeeded)
                self.assertEqual(st_401, 401)

                # Non-ASCII header attack -> 401 (no crash)
                self.assertFalse(
                    verify_whop_webhook_auth(
                        evt_succeeded,
                        {"webhook-signature": "v1,\u2603bad", "webhook-id": "msg_1", "webhook-timestamp": "1700000000"},
                        wh_secret,
                    )
                )

                # Valid Standard Webhooks signature (whsec_ base64 key + msg_id.timestamp.body) -> 200 OK
                msg_id = "msg_evt_001"
                msg_ts = "1763640000"
                signed_bytes = f"{msg_id}.{msg_ts}.".encode("utf-8") + evt_succeeded
                std_sig_b64 = base64.b64encode(
                    hmac.new(raw_secret_bytes, signed_bytes, hashlib.sha256).digest()
                ).decode("ascii")
                st_200, res_200 = _http_post(
                    wh_url,
                    evt_succeeded,
                    headers={
                        "webhook-id": msg_id,
                        "webhook-timestamp": msg_ts,
                        "webhook-signature": f"v1,{std_sig_b64}",
                    },
                )
                self.assertEqual(st_200, 200)
                self.assertTrue(res_200["ok"])
                # $13.84 + $37.42 = $51.26 >= $50.00 target unlocked!
                wh_meter = res_200["whop_meter"]
                assert isinstance(wh_meter, dict)
                self.assertEqual(wh_meter["net_usd"], 51.26)
                self.assertTrue(wh_meter["unlocked"])

                # membership.went_valid ($0 free member) via X-Whop-Signature hex
                evt_mem = json.dumps(
                    {
                        "id": "msg_evt_002",
                        "type": "membership.went_valid",
                        "data": {
                            "id": "mem_free_wh_01",
                            "company_id": "biz_Af4z3lgivbct7t",
                            "status": "active",
                            "valid": True,
                            "metadata": {"utm_source": "whop_discover", "slug": slug},
                        },
                    }
                ).encode("utf-8")
                hex_sig = hmac.new(wh_secret.encode("utf-8"), evt_mem, hashlib.sha256).hexdigest()
                st_mem, res_mem = _http_post(
                    wh_url,
                    evt_mem,
                    headers={"X-Whop-Signature": f"sha256={hex_sig}"},
                )
                self.assertEqual(st_mem, 200)
                self.assertEqual(res_mem["whop_meter"]["free_downloads"], 1)  # type: ignore[index]

                # payment.failed -> 200 OK ignored=True without polluting orders
                evt_failed = json.dumps(
                    {
                        "id": "msg_evt_003",
                        "type": "payment.failed",
                        "data": {"id": "pay_declined_99", "company_id": "biz_Af4z3lgivbct7t", "amount": "15.00"},
                    }
                ).encode("utf-8")
                st_fail, res_fail = _http_post(
                    wh_url,
                    evt_failed,
                    headers={"X-Webhook-Secret": wh_secret},
                )
                self.assertEqual(st_fail, 200)
                self.assertTrue(res_fail["ignored"])

                # payment.refunded for pay_cli_01 (different outer msg id, matching data.id!) -> zeroes pay_cli_01!
                evt_refund = json.dumps(
                    {
                        "id": "msg_evt_004",
                        "type": "payment.refunded",
                        "data": {
                            "id": "pay_cli_01",
                            "company_id": "biz_Af4z3lgivbct7t",
                            "amount": "15.00",
                        },
                    }
                ).encode("utf-8")
                st_ref, res_ref = _http_post(
                    wh_url,
                    evt_refund,
                    headers={"X-Webhook-Secret": wh_secret},
                )
                self.assertEqual(st_ref, 200)
                self.assertEqual(res_ref["whop_meter"]["net_usd"], 37.42)  # type: ignore[index]
                self.assertEqual(res_ref["whop_meter"]["refunded_orders"], 1)  # type: ignore[index]

                # Verify UI, Scoreboard & 30d Report render Whop #1 + Patreon #2 with zero Nas.io/Ko-fi regressions
                cmd_log_metrics(cfg, slug=slug, channel="whop", views=240, clicks=31)
                db = PipelineDatabase(cfg.db_path)
                rep = build_experiment_report(db, cfg, as_of="2026-11-20")
                self.assertEqual(rep["channels"]["whop"]["verdict"], VERDICT_WORKING)
                self.assertNotIn("nasio", rep["channels"])

                md_rep = render_experiment_markdown(rep)
                self.assertIn("Storefront #1: Whop ($10 Min Payout · Built-in Marketplace)", md_rep)
                self.assertIn("Storefront #2: Patreon ($25 Payoneer)", md_rep)
                self.assertNotIn("Nas.io", md_rep)

                dash_html = render_dashboard_html(cfg)
                dash_no_scripts = re.sub(r"<script.*?</script>", "", dash_html, flags=re.DOTALL)
                self.assertIn("Storefront #1: Whop ($10 Min Payout · Built-in Marketplace)", dash_no_scripts)
                self.assertIn("Storefront #2: Patreon ($25 Payoneer)", dash_no_scripts)
                self.assertIn("Copy Whop 1-Hr Pack", dash_no_scripts)
                self.assertNotIn("Nas.io", dash_no_scripts)
                self.assertNotIn("`", dash_no_scripts)
                self.assertNotIn("ko-fi.com", dash_no_scripts.lower())

                site_html = (cfg.site_dir / "index.html").read_text(encoding="utf-8")
                site_no_scripts = re.sub(r"<script.*?</script>", "", site_html, flags=re.DOTALL)
                self.assertIn("Storefront #1: Whop ($10 Min Payout · Built-in Marketplace)", site_no_scripts)
                self.assertIn("Storefront #2: Patreon ($25 Payoneer)", site_no_scripts)
                self.assertNotIn("Nas.io", site_no_scripts)
                self.assertNotIn("`", site_no_scripts)
                self.assertNotIn("ko-fi.com", site_no_scripts.lower())
            finally:
                server.shutdown()
                server.server_close()

            parser = build_arg_parser()
            parsed = parser.parse_args(
                ["log-order", "--storefront", "whop", "--order-id", "pay_1", "--gross-usd", "$15.00", "--json"]
            )
            self.assertEqual((parsed.command, parsed.storefront), ("log-order", "whop"))

    def test_whop_2026_account_id_multicurrency_refund_events_and_membership_dedup(self) -> None:
        # 1. Admin `/dashboard/biz_...` URL in WHOP_STORE_URL must extract company_id and never leak into public checkout CTAs
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            shutil.copytree(PROJECT_ROOT / "products", ws / "products")
            ws_secret = "ws_live_literal_whop_secret_2026"
            cfg = AppConfig.load(
                ws,
                environ={
                    "WHOP_STORE_URL": "https://whop.com/dashboard/biz_Af4z3lgivbct7t/",
                    "WHOP_WEBHOOK_SECRET": ws_secret,
                    "PATREON_URL": "https://www.patreon.com/NuwansaraT",
                },
            )
            self.assertEqual(cfg.whop_company_id, "biz_Af4z3lgivbct7t")
            self.assertEqual(cfg.whop_store_url, "")
            self.assertEqual(cfg.paid_checkout_url, "https://www.patreon.com/NuwansaraT")

            # 2. Foreign `account_id` (2026-08-14+ schema) must be rejected cleanly
            with self.assertRaises(WhopWebhookError):
                parse_whop_webhook_payload(
                    {
                        "type": "payment.succeeded",
                        "account_id": "biz_foreign_attacker",
                        "data": {"id": "pay_foreign_2026", "account_id": "biz_foreign_attacker", "usd_total": 99},
                    },
                    expected_company_id="biz_Af4z3lgivbct7t",
                )

            # 3. Multi-currency buyer charge (INR/EUR) with `usd_total` + `amount_after_fees` normalizes to exact USD cents
            fx_order = parse_whop_webhook_payload(
                {
                    "type": "payment.succeeded",
                    "account_id": "biz_Af4z3lgivbct7t",
                    "data": {
                        "id": "pay_inr_fx_01",
                        "currency": "inr",
                        "amount_charged": {"amount": "1300.00", "currency": "inr"},
                        "total": {"amount": "1300.00", "currency": "inr"},
                        "usd_total": {"amount": "15.00", "currency": "usd"},
                        "amount_after_fees": {"amount": "13.84", "currency": "usd"},
                        "metadata": {"utm_source": "blogger", "utm_medium": "tutorial", "sku": "production-agent-automation-kit"},
                    },
                },
                expected_company_id="biz_Af4z3lgivbct7t",
            )
            assert fx_order is not None
            self.assertEqual(fx_order.gross_cents, 1500)
            self.assertEqual(fx_order.fee_cents, 116)
            self.assertEqual(fx_order.net_cents, 1384)
            self.assertEqual(fx_order.currency, "USD")
            self.assertEqual(fx_order.raw_data.get("original_currency"), "INR")

            # 4. `substatus="refunded"` (when `status="paid"`) and `auto_refunded=True` must mark refunded=True, net_cents=0
            sub_ref = parse_whop_webhook_payload(
                {"type": "payment.updated", "data": {"id": "pay_s1", "status": "paid", "substatus": "refunded", "usd_total": "15.00"}}
            )
            assert sub_ref is not None
            self.assertTrue(sub_ref.refunded)
            self.assertEqual(sub_ref.net_cents, 0)

            auto_ref = parse_whop_webhook_payload(
                {"type": "payment.succeeded", "data": {"id": "pay_s2", "status": "paid", "auto_refunded": True, "usd_total": "15.00"}}
            )
            assert auto_ref is not None
            self.assertTrue(auto_ref.refunded)
            self.assertEqual(auto_ref.net_cents, 0)

            # Failed refund (`refund.updated` status=failed) is ignored (None)
            self.assertIsNone(
                parse_whop_webhook_payload(
                    {"type": "refund.updated", "data": {"id": "ref_fail_1", "status": "failed", "payment": {"id": "pay_inr_fx_01"}}}
                )
            )

            # 5. Live HTTP webhook server: test literal `ws_...` Standard Webhooks signing, out-of-order `membership.went_valid` +
            #    `payment.succeeded` deduplication, partial `refund.created`, and full `refund.created` preserving attribution!
            server = ThreadingHTTPServer(("127.0.0.1", 0), create_dashboard_handler(cfg))
            port = server.server_address[1]
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                wh_url = f"http://127.0.0.1:{port}/api/webhooks/whop"

                def post_signed_ws(msg_id: str, payload_obj: dict[str, object]) -> tuple[int, dict[str, object]]:
                    raw = json.dumps(payload_obj).encode("utf-8")
                    ts = "1763641000"
                    signed = f"{msg_id}.{ts}.".encode("utf-8") + raw
                    sig_b64 = base64.b64encode(
                        hmac.new(ws_secret.encode("utf-8"), signed, hashlib.sha256).digest()
                    ).decode("ascii")
                    return _http_post(
                        wh_url,
                        raw,
                        headers={
                            "webhook-id": msg_id,
                            "webhook-timestamp": ts,
                            "webhook-signature": f"v1,{sig_b64}",
                        },
                    )

                # Genuine $0 free Mini-Kit lead on `prod_free`
                st0, _ = post_signed_ws(
                    "msg_w0",
                    {
                        "type": "membership.went_valid",
                        "account_id": "biz_Af4z3lgivbct7t",
                        "data": {"id": "mem_free_only", "user": {"id": "u_buyer"}, "product": {"id": "prod_free"}},
                    },
                )
                self.assertEqual(st0, 200)

                # Out-of-order: `membership.went_valid` for `prod_chal` arrives BEFORE `payment.succeeded`, then paid `membership.went_valid` after
                st1, _ = post_signed_ws(
                    "msg_w1",
                    {
                        "type": "membership.went_valid",
                        "account_id": "biz_Af4z3lgivbct7t",
                        "data": {"id": "mem_chal_dup", "user": {"id": "u_buyer"}, "product": {"id": "prod_chal"}},
                    },
                )
                self.assertEqual(st1, 200)
                st2, res2 = post_signed_ws(
                    "msg_w2",
                    {
                        "type": "payment.succeeded",
                        "account_id": "biz_Af4z3lgivbct7t",
                        "data": {
                            "id": "pay_chal_main",
                            "user": {"id": "u_buyer"},
                            "product": {"id": "prod_chal"},
                            "usd_total": {"amount": "15.00", "currency": "usd"},
                            "amount_after_fees": {"amount": "13.84", "currency": "usd"},
                            "metadata": {"utm_source": "blogger", "utm_medium": "tutorial", "sku": "production-agent-automation-kit"},
                        },
                    },
                )
                self.assertEqual(st2, 200)
                st3, res3 = post_signed_ws(
                    "msg_w3",
                    {
                        "type": "membership.went_valid",
                        "account_id": "biz_Af4z3lgivbct7t",
                        "data": {"id": "mem_chal_dup_paid", "price": "15.00", "user": {"id": "u_buyer"}, "product": {"id": "prod_chal"}},
                    },
                )
                self.assertEqual(st3, 200)
                self.assertTrue(res3.get("ignored"))
                # Exactly 1 free lead (`prod_free`) + 1 paid order (`pay_chal_main`, $13.84 net)
                self.assertEqual(res2["whop_meter"]["free_downloads"], 1)  # type: ignore[index]
                self.assertEqual(res2["whop_meter"]["paid_orders"], 1)  # type: ignore[index]
                self.assertEqual(res2["whop_meter"]["net_usd"], 13.84)  # type: ignore[index]

                # Partial `refund.created` ($5.00 off $15.00) reduces net from $13.84 -> $8.84 while preserving `blogger` attribution & SKU
                st4, res4 = post_signed_ws(
                    "msg_w4",
                    {
                        "type": "refund.created",
                        "account_id": "biz_Af4z3lgivbct7t",
                        "data": {
                            "id": "ref_partial_5",
                            "amount": "5.00",
                            "currency": "usd",
                            "status": "succeeded",
                            "payment": {"id": "pay_chal_main"},
                        },
                    },
                )
                self.assertEqual(st4, 200)
                self.assertEqual(res4["whop_meter"]["paid_orders"], 1)  # type: ignore[index]
                self.assertEqual(res4["whop_meter"]["net_usd"], 8.84)  # type: ignore[index]

                # Full `refund.created` ($10.00 remaining) marks `pay_chal_main` refunded=True while keeping original SKU & `utm_source="blogger"`
                st5, res5 = post_signed_ws(
                    "msg_w5",
                    {
                        "type": "refund.created",
                        "account_id": "biz_Af4z3lgivbct7t",
                        "data": {
                            "id": "ref_full_rem",
                            "amount": "10.00",
                            "currency": "usd",
                            "status": "succeeded",
                            "payment": {"id": "pay_chal_main"},
                        },
                    },
                )
                self.assertEqual(st5, 200)
                self.assertEqual(res5["whop_meter"]["paid_orders"], 0)  # type: ignore[index]
                self.assertEqual(res5["whop_meter"]["refunded_orders"], 1)  # type: ignore[index]
                self.assertEqual(res5["whop_meter"]["net_usd"], 0.0)  # type: ignore[index]

                db_chk = PipelineDatabase(cfg.db_path)
                stored_by_id = {o.order_id: o for o in db_chk.list_store_orders(include_refunded=True)}
                self.assertIn("pay_chal_main", stored_by_id)
                self.assertNotIn("ref_partial_5", stored_by_id)
                self.assertNotIn("ref_full_rem", stored_by_id)
                self.assertEqual(stored_by_id["pay_chal_main"].utm_source, "blogger")
                self.assertEqual(stored_by_id["pay_chal_main"].sku, "production-agent-automation-kit")
                self.assertTrue(stored_by_id["pay_chal_main"].refunded)
            finally:
                server.shutdown()
                server.server_close()

            # 6. 2026-08-21+ API key rejecting `company_id` with HTTP 400 negotiates `account_id` automatically
            seen_queries: list[dict[str, list[str]]] = []

            def strict_2026_api(req: urllib.request.Request, _timeout: float) -> HttpResult:
                parsed_u = urllib.parse.urlparse(req.full_url)
                qs = urllib.parse.parse_qs(parsed_u.query)
                seen_queries.append(qs)
                if "company_id" in qs:
                    return HttpResult(status=400, headers={}, body=b'{"error":"Use account_id"}')
                self.assertEqual(qs.get("account_id"), ["biz_Af4z3lgivbct7t"])
                if parsed_u.path == "/api/v1/payments":
                    return HttpResult(
                        status=200,
                        headers={},
                        body=json.dumps(
                            {
                                "data": [
                                    {
                                        "id": "pay_2026_1",
                                        "account_id": "biz_Af4z3lgivbct7t",
                                        "status": "paid",
                                        "substatus": "partially_refunded",
                                        "usd_total": {"amount": "15.00", "currency": "usd"},
                                        "amount_after_fees": {"amount": "13.84", "currency": "usd"},
                                        "refunded_amount": {"amount": "4.00", "currency": "usd"},
                                        "user": {"id": "u_9"},
                                        "plan": {"id": "plan_9"},
                                    },
                                    {
                                        "id": "pay_foreign_acct",
                                        "account": {"id": "biz_other_999"},
                                        "status": "paid",
                                        "usd_total": "50.00",
                                    },
                                ],
                                "page_info": {"has_next_page": False},
                            }
                        ).encode("utf-8"),
                    )
                return HttpResult(
                    status=200,
                    headers={},
                    body=json.dumps(
                        {
                            "data": [
                                {
                                    "id": "mem_covered_by_subject",
                                    "account_id": "biz_Af4z3lgivbct7t",
                                    "status": "active",
                                    "user": {"id": "u_9"},
                                    "plan": {"id": "plan_9"},
                                }
                            ],
                            "page_info": {"has_next_page": False},
                        }
                    ).encode("utf-8"),
                )

            c2026 = WhopClient(api_key="apik_2026", company_id="biz_Af4z3lgivbct7t", sender=strict_2026_api)
            synced_orders = c2026.list_orders()
            # 1st `/payments` tried company_id (400) -> 2nd `/payments` used account_id (200) -> 3rd `/memberships` reused account_id directly!
            self.assertEqual(len(seen_queries), 3)
            self.assertEqual(len(synced_orders), 1)
            self.assertEqual(synced_orders[0].order_id, "pay_2026_1")
            self.assertEqual(synced_orders[0].gross_cents, 1100)
            self.assertEqual(synced_orders[0].net_cents, 984)

    def test_whop_catalog_provisioning_forum_publishing_live_analytics_and_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            shutil.copytree(PROJECT_ROOT / "products", ws / "products")

            # 1. Dry-run setup-whop & diagnostics before API keys are configured
            dry_cfg = AppConfig.load(
                ws,
                environ={
                    "WHOP_COMPANY_ID": "biz_Af4z3lgivbct7t",
                    "PATREON_URL": "https://www.patreon.com/NuwansaraT",
                    "CANONICAL_BASE_URL": "https://nuwansaratharusha.github.io/kofi-devto-autopilot",
                },
            )
            dry_prov = cmd_setup_whop(dry_cfg, as_json=False)
            self.assertEqual(dry_prov["status"], "dry_run")
            self.assertEqual(dry_prov["catalog_count"], 3)

            d1_dry = cmd_run_daily(dry_cfg, date_str="2026-11-18", offline=True, as_json=False)
            self.assertEqual(d1_dry["whop"]["status"], "pack_ready")
            self.assertTrue(Path(d1_dry["whop"]["whop_forum_payload"]).is_file())

            db = PipelineDatabase(dry_cfg.db_path)
            rep_dry = build_experiment_report(db, dry_cfg, as_of="2026-11-18")
            self.assertEqual(
                rep_dry["diagnostics"]["channel_readiness"]["whop"]["readiness"],
                READINESS_UNCONFIGURED_DRY_RUN,
            )
            self.assertIn(
                "WHOP_API_KEY",
                rep_dry["diagnostics"]["channel_readiness"]["whop"]["missing_env_keys"],
            )
            md_dry = render_experiment_markdown(rep_dry)
            self.assertIn("## 4. Channel Readiness & Root-Cause Diagnostics", md_dry)

            # 2. Live API provisioning (`company_id` -> `account_id` 422 body negotiation + idempotent reuse)
            live_cfg = AppConfig.load(
                ws,
                environ={
                    "WHOP_COMPANY_ID": "biz_Af4z3lgivbct7t",
                    "WHOP_API_KEY": "apik_live_whop_test",
                    "WHOP_STORE_URL": "https://whop.com/reliability-lab",
                    "DEVTO_API_KEY": "devto_live_test_key",
                    "PATREON_URL": "https://www.patreon.com/NuwansaraT",
                    "CANONICAL_BASE_URL": "https://nuwansaratharusha.github.io/kofi-devto-autopilot",
                },
            )

            created_products: list[dict[str, object]] = []
            created_plans: list[dict[str, object]] = []
            created_forum_posts: list[dict[str, object]] = []
            sent_chat_messages: list[dict[str, object]] = []

            def fake_whop_full_api(req: urllib.request.Request, _timeout: float) -> HttpResult:
                parsed_u = urllib.parse.urlparse(req.full_url)
                qs = urllib.parse.parse_qs(parsed_u.query)
                method = req.get_method()
                raw_payload = json.loads(req.data.decode("utf-8")) if req.data else {}

                # Enforce 2026+ `account_id` on write bodies (422 if legacy `company_id` sent)
                if method == "POST" and parsed_u.path in {"/api/v1/products", "/api/v1/plans", "/api/v1/forum_posts"}:
                    if "company_id" in raw_payload:
                        return HttpResult(status=422, headers={}, body=b'{"error":"account_id required"}')
                    self.assertEqual(raw_payload.get("account_id"), "biz_Af4z3lgivbct7t")
                    self.assertTrue(bool(req.get_header("Idempotency-key") or req.get_header("Idempotency-Key")))

                if parsed_u.path == "/api/v1/products":
                    if method == "GET":
                        return HttpResult(
                            status=200,
                            headers={},
                            body=json.dumps({"data": created_products, "page_info": {"has_next_page": False}}).encode("utf-8"),
                        )
                    new_prod = {
                        "id": f"prod_{len(created_products) + 1}",
                        "account_id": "biz_Af4z3lgivbct7t",
                        "title": raw_payload["title"],
                        "route": raw_payload["route"],
                        "metadata": raw_payload.get("metadata", {}),
                    }
                    created_products.append(new_prod)
                    return HttpResult(status=201, headers={}, body=json.dumps(new_prod).encode("utf-8"))

                if parsed_u.path == "/api/v1/plans":
                    if method == "GET":
                        return HttpResult(
                            status=200,
                            headers={},
                            body=json.dumps({"data": created_plans, "page_info": {"has_next_page": False}}).encode("utf-8"),
                        )
                    new_plan = {
                        "id": f"plan_{len(created_plans) + 1}",
                        "account_id": "biz_Af4z3lgivbct7t",
                        "product_id": raw_payload["product_id"],
                        "initial_price": raw_payload["initial_price"],
                        "metadata": raw_payload.get("metadata", {}),
                        "direct_link": f"https://whop.com/checkout/plan_{len(created_plans) + 1}",
                    }
                    created_plans.append(new_plan)
                    return HttpResult(status=201, headers={}, body=json.dumps(new_plan).encode("utf-8"))

                if parsed_u.path == "/api/v1/experiences":
                    return HttpResult(
                        status=200,
                        headers={},
                        body=json.dumps(
                            {
                                "data": [
                                    {
                                        "id": "exp_forum_main",
                                        "account_id": "biz_Af4z3lgivbct7t",
                                        "name": "Builder Forum & Announcements",
                                        "type": "forum",
                                    },
                                    {
                                        "id": "exp_chat_main",
                                        "account_id": "biz_Af4z3lgivbct7t",
                                        "name": "Community Chat",
                                        "type": "chat",
                                    },
                                ],
                                "page_info": {"has_next_page": False},
                            }
                        ).encode("utf-8"),
                    )

                if parsed_u.path == "/api/v1/forum_posts":
                    if method == "GET":
                        self.assertEqual(qs.get("experience_id"), ["exp_forum_main"])
                        return HttpResult(
                            status=200,
                            headers={},
                            body=json.dumps(
                                {"data": created_forum_posts, "page_info": {"has_next_page": False}}
                            ).encode("utf-8"),
                        )
                    # Anti-spam hygiene assertions
                    self.assertFalse(raw_payload.get("is_mention", True))
                    self.assertFalse(raw_payload.get("pinned", True))
                    links_found = re.findall(r"https://\S+", str(raw_payload.get("content") or ""))
                    self.assertLessEqual(len(links_found), 2)
                    new_fp = {
                        "id": f"fp_{len(created_forum_posts) + 1}",
                        "experience_id": raw_payload["experience_id"],
                        "title": raw_payload["title"],
                        "content": raw_payload["content"],
                        "url": f"https://whop.com/reliability-lab/forum/fp_{len(created_forum_posts) + 1}",
                        "view_count": 148,
                        "like_count": 11,
                        "comment_count": 4,
                    }
                    created_forum_posts.append(new_fp)
                    return HttpResult(status=201, headers={}, body=json.dumps(new_fp).encode("utf-8"))

                if parsed_u.path == "/api/v1/channels/exp_chat_main/messages" and method == "POST":
                    sent_chat_messages.append(raw_payload)
                    return HttpResult(status=201, headers={}, body=b'{"id":"msg_1"}')

                raise AssertionError(f"Unexpected Whop request: {method} {req.full_url}")

            whop_live = WhopClient(live_cfg, sender=fake_whop_full_api, sleeper=lambda _s: None)
            from products.builder import load_catalog

            catalog = load_catalog(PROJECT_ROOT)
            prov1 = whop_live.ensure_catalog_products(catalog)
            self.assertEqual(prov1["status"], "provisioned")
            self.assertEqual(len(created_products), 3)
            self.assertEqual(len(created_plans), 3)
            self.assertEqual(prov1["forum_experience_id"], "exp_forum_main")
            self.assertEqual(prov1["chat_experience_id"], "exp_chat_main")

            # Second provisioning call must reuse all 3 products & plans idempotently (0 duplicates)
            prov2 = whop_live.ensure_catalog_products(catalog)
            self.assertEqual(len(created_products), 3)
            self.assertEqual(len(created_plans), 3)
            self.assertTrue(all(p["product_operation"] == "reused" for p in prov2["products_provisioned"]))
            self.assertTrue(all(p["plan_operation"] == "reused" for p in prov2["products_provisioned"]))

            # 3. Publish Day 1 article to Whop Forum & verify 3-layer deduplication + SQLite persistence
            art1 = db.get_article_by_series_day(1)
            assert art1 is not None
            pub1 = whop_live.publish_daily_post(art1, db=db)
            self.assertEqual(pub1["status"], "published_live")
            self.assertEqual(pub1["operation"], "created")
            self.assertEqual(pub1["whop_post_id"], "fp_1")
            self.assertEqual(pub1["chat_notification"], "sent")
            self.assertEqual(len(created_forum_posts), 1)
            self.assertEqual(len(sent_chat_messages), 1)

            # Re-saving article with whop_post_id=None must preserve stored whop_post_id!
            art1_reload = db.get_article(art1.slug)
            assert art1_reload is not None
            self.assertEqual(art1_reload.whop_post_id, "fp_1")
            art1_reload.whop_post_id = None
            art1_reload.whop_url = None
            preserved = db.save_article(art1_reload)
            self.assertEqual(preserved.whop_post_id, "fp_1")

            # Re-publishing must hit Layer 1 (`already_recorded`) with zero extra POSTs
            pub_repeat = whop_live.publish_daily_post(preserved, db=db)
            self.assertEqual(pub_repeat["operation"], "already_recorded")
            self.assertEqual(len(created_forum_posts), 1)

            # 4. Sync live analytics across Whop Forum + DEV.to and verify monotonic floor & diagnostics
            def fake_devto_me_sender(req: urllib.request.Request, _timeout: float) -> HttpResult:
                self.assertIn("/api/articles/me/all", req.full_url)
                body = [
                    {
                        "id": 778899,
                        "title": preserved.title,
                        "url": f"https://dev.to/nuwansarat/{preserved.slug}",
                        "canonical_url": preserved.canonical_url,
                        "published": True,
                        "page_views_count": 312,
                        "positive_reactions_count": 19,
                        "comments_count": 5,
                    }
                ]
                return HttpResult(status=200, headers={}, body=json.dumps(body).encode("utf-8"))

            an_res = sync_all_analytics(
                live_cfg,
                db,
                whop_sender=fake_whop_full_api,
                devto_sender=fake_devto_me_sender,
            )
            self.assertEqual(an_res["whop"]["status"], "synced")
            self.assertEqual(an_res["whop"]["views_total"], 148)
            self.assertEqual(an_res["whop"]["engagement_total"], 15)
            self.assertEqual(an_res["devto"]["status"], "synced")
            self.assertEqual(an_res["devto"]["views_total"], 312)
            self.assertEqual(an_res["devto"]["engagement_total"], 24)

            # Manual higher outbound click count must never be overwritten downward by subsequent sync!
            cmd_log_metrics(live_cfg, slug=preserved.slug, channel="whop", views=148, clicks=28)
            sync_all_analytics(
                live_cfg,
                db,
                whop_sender=fake_whop_full_api,
                devto_sender=fake_devto_me_sender,
            )
            rep_live = build_experiment_report(db, live_cfg, as_of="2026-11-18")
            self.assertEqual(rep_live["channels"]["whop"]["views"], 148)
            self.assertEqual(rep_live["channels"]["whop"]["clicks"], 28)
            self.assertEqual(
                rep_live["diagnostics"]["channel_readiness"]["whop"]["readiness"],
                READINESS_LIVE,
            )
            self.assertEqual(rep_live["channels"]["devto"]["views"], 312)
            self.assertEqual(rep_live["channels"]["devto"]["clicks"], 24)

            # Verify zero raw backticks in updated site/index.html & dashboard HTML
            cmd_sync_analytics(
                AppConfig.load(
                    ws,
                    environ={
                        "WHOP_COMPANY_ID": "biz_Af4z3lgivbct7t",
                        "WHOP_STORE_URL": "https://whop.com/reliability-lab",
                        "PATREON_URL": "https://www.patreon.com/NuwansaraT",
                    },
                ),
                as_json=False,
            )
            site_html = (live_cfg.site_dir / "index.html").read_text(encoding="utf-8")
            site_no_scripts = re.sub(r"<script.*?</script>", "", site_html, flags=re.DOTALL)
            self.assertNotIn("`", site_no_scripts)
            self.assertIn("Discuss on Whop", site_no_scripts)

            dash_html = render_dashboard_html(live_cfg)
            dash_no_scripts = re.sub(r"<script.*?</script>", "", dash_html, flags=re.DOTALL)
            self.assertNotIn("`", dash_no_scripts)
            self.assertIn("LIVE FORUM SYNCED", dash_no_scripts)


if __name__ == "__main__":
    unittest.main()
