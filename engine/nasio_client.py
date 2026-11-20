"""First-Class Nas.io Strategy, Daily 60-Min Execution Pack, Webhook & Order Sync Module.

Designed for `$0`-capital Sri Lanka (`Asia/Colombo`) creators:
- Pairs Nas.io Free Plan (`Pass fees to buyers = ON`, `$10 USD` Payoneer USD payout unlock / `$65` LKR wire)
  with Patreon (`$25 USD` Payoneer payout) so both balances pool into one Payoneer USD account.
- Generates daily time-boxed 60-minute execution packs (`0-15m` Magic Reach, `15-40m` `<= 2`-link hook,
  `40-60m` warm DM script) and ingests Nas.io / Zapier purchase webhooks into `state/pipeline.db`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Mapping, Optional

from engine.config import AppConfig
from engine.db import utc_now_iso
from engine.models import PILLAR_META, SERIES_30D_NAME, ArticleRecord, StoreOrderRecord
from engine.patreon_client import estimate_patreon_fee_cents
from engine.storefronts import _extract_utm_from_mapping_or_url, append_storefront_utm
from engine.whop_client import CHALLENGE_SKU_ALIASES, estimate_whop_fee_cents

VALID_ORDER_STOREFRONTS = ("whop", "patreon", "lemonsqueezy", "gumroad", "nasio")


class NasioWebhookError(ValueError):
    """Raised when a Nas.io webhook payload or manual order input is invalid."""


_CURRENCY_PREFIX_RE = re.compile(r"^(?:usd|us\$|\$)\s*", re.IGNORECASE)
_CURRENCY_SUFFIX_RE = re.compile(r"\s*(?:usd)$", re.IGNORECASE)


def _safe_compare_str(left: str, right: str) -> bool:
    """Constant-time UTF-8 byte comparison safe against non-ASCII HTTP header values."""
    return hmac.compare_digest(
        left.encode("utf-8", errors="surrogateescape"),
        right.encode("utf-8", errors="surrogateescape"),
    )


def _parse_bool_flag(value: Any, *, field_name: str = "flag", default: bool = False) -> bool:
    """Parse JSON booleans and Zapier stringified booleans ('true'/'false'/'1'/'0') strictly."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        low = value.strip().lower()
        if not low:
            return default
        if low in {"true", "1", "yes", "y", "on"}:
            return True
        if low in {"false", "0", "no", "n", "off"}:
            return False
        raise NasioWebhookError(f"Invalid boolean field {field_name}: {value!r}")
    raise NasioWebhookError(f"Invalid boolean field {field_name}: {value!r}")


def _parse_nonneg_int_cents(field_name: str, value: Any) -> int:
    """Parse a non-negative integer cent field while rejecting booleans (`isinstance(True, int)`)."""
    if isinstance(value, bool):
        raise NasioWebhookError(f"Boolean not allowed for integer field {field_name}: {value!r}")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        clean = value.strip().replace(",", "")
        if not re.fullmatch(r"[+-]?\d+", clean):
            raise NasioWebhookError(f"Invalid integer field {field_name}: {value!r}")
        parsed = int(clean)
    else:
        raise NasioWebhookError(f"Invalid integer field {field_name}: {value!r}")
    if parsed < 0:
        raise NasioWebhookError(f"{field_name} must be >= 0 (got {value!r})")
    return parsed


def usd_to_cents(amount_usd: float | str | int | Decimal) -> int:
    """Convert a non-negative USD amount (including '$15.00' / 'USD 1,250.50') into integer cents."""
    if isinstance(amount_usd, bool):
        raise NasioWebhookError(f"Boolean not allowed for USD amount: {amount_usd!r}")
    raw = str(amount_usd).strip()
    if not raw:
        raise NasioWebhookError(f"Empty USD amount: {amount_usd!r}")
    if raw.startswith("-"):
        raise NasioWebhookError(f"USD amount must be >= 0 (got {amount_usd!r})")
    if raw.startswith("+"):
        raw = raw[1:].strip()
    cleaned = _CURRENCY_PREFIX_RE.sub("", raw)
    cleaned = _CURRENCY_SUFFIX_RE.sub("", cleaned).strip().replace(",", "")
    if not cleaned:
        raise NasioWebhookError(f"Invalid USD amount: {amount_usd!r}")
    try:
        dec = Decimal(cleaned)
    except (InvalidOperation, ValueError) as exc:
        raise NasioWebhookError(f"Invalid USD amount: {amount_usd!r}") from exc
    if not dec.is_finite():
        raise NasioWebhookError(f"Non-finite USD amount: {amount_usd!r}")
    if dec < 0:
        raise NasioWebhookError(f"USD amount must be >= 0 (got {amount_usd!r})")
    cents = int((dec * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return cents


def estimate_nasio_fee_cents(gross_cents: int, *, pass_fees_to_buyer: bool = True) -> int:
    """Estimate Nas.io fee on creator list price (`0c` when `Pass fees to buyers = ON`)."""
    if gross_cents <= 0:
        return 0
    if pass_fees_to_buyer:
        return 0
    pct_fee = int(round(gross_cents * 0.088))
    fixed_fee = 30 if gross_cents >= 100 else 0
    return min(gross_cents, pct_fee + fixed_fee)


def estimate_default_storefront_fee_cents(storefront: str, gross_cents: int) -> int:
    """Default fee estimator when `--fee-usd` is omitted in `log-order`."""
    if gross_cents <= 0:
        return 0
    sf = storefront.strip().lower()
    if sf == "whop":
        return estimate_whop_fee_cents(gross_cents)
    if sf == "nasio":
        return estimate_nasio_fee_cents(gross_cents, pass_fees_to_buyer=True)
    if sf == "patreon":
        return estimate_patreon_fee_cents(gross_cents)
    if sf == "lemonsqueezy":
        return min(gross_cents, int(round(gross_cents * 0.05)) + 50)
    if sf == "gumroad":
        return min(gross_cents, int(round(gross_cents * 0.10)) + 50)
    return 0


def build_logged_order(
    *,
    storefront: str,
    order_id: str,
    gross_usd: float | str | int,
    fee_usd: Optional[float | str | int] = None,
    utm_source: str = "",
    utm_medium: str = "manual_log",
    utm_campaign: str = "30d_exp_2026_11",
    sku: str = "",
    slug: str = "",
    product_name: str = "",
    currency: str = "USD",
    refunded: bool = False,
    raw_data: Optional[Mapping[str, Any]] = None,
) -> StoreOrderRecord:
    """Construct a validated `StoreOrderRecord` for CLI `log-order` or webhook persistence."""
    sf = storefront.strip().lower()
    if sf not in VALID_ORDER_STOREFRONTS:
        raise NasioWebhookError(
            f"Unsupported storefront '{storefront}'. Expected one of {VALID_ORDER_STOREFRONTS}"
        )
    clean_id = str(order_id or "").strip()
    if not clean_id:
        raise NasioWebhookError("order_id must be non-empty")

    gross_cents = usd_to_cents(gross_usd)
    if fee_usd is not None and str(fee_usd).strip() != "":
        fee_cents = usd_to_cents(fee_usd)
    else:
        fee_cents = estimate_default_storefront_fee_cents(sf, gross_cents)

    if fee_cents > gross_cents:
        fee_cents = gross_cents
    net_cents = 0 if refunded else max(0, gross_cents - fee_cents)

    eff_source = utm_source.strip().lower()
    if not eff_source and sf in {"whop", "nasio", "patreon"}:
        eff_source = sf

    eff_sku = sku.strip()
    if not eff_sku:
        if gross_cents == 0:
            eff_sku = "free-idempotent-cron-mini-kit"
        elif sf in {"whop", "nasio"}:
            eff_sku = "nasio-7day-ai-automation-challenge-pack"
        else:
            eff_sku = "production-agent-automation-kit"

    eff_name = product_name.strip()
    if not eff_name:
        if gross_cents == 0:
            eff_name = "Free Idempotent Cron Mini-Kit"
        elif eff_sku in CHALLENGE_SKU_ALIASES:
            eff_name = "7-Day AI & Python Reliability Challenge Pack"
        else:
            eff_name = f"{sf.capitalize()} Digital Kit"

    now_iso = utc_now_iso()
    return StoreOrderRecord(
        storefront=sf,
        order_id=clean_id,
        sku=eff_sku,
        product_name=eff_name,
        gross_cents=gross_cents,
        fee_cents=fee_cents,
        net_cents=net_cents,
        currency=(currency or "USD").strip().upper() or "USD",
        refunded=bool(refunded),
        utm_source=eff_source,
        utm_medium=(utm_medium or "manual_log").strip().lower(),
        utm_campaign=(utm_campaign or "30d_exp_2026_11").strip(),
        attributed_slug=slug.strip(),
        created_at=now_iso,
        synced_at=now_iso,
        raw_data=dict(raw_data or {"source": "log_order", "storefront": sf, "order_id": clean_id}),
    )


def verify_nasio_webhook_auth(
    raw_body: bytes,
    headers: Mapping[str, str],
    configured_secret: str,
) -> bool:
    """Verify constant-time HMAC (`X-Nasio-Signature`) or shared secret header (`X-Webhook-Secret` / Bearer)."""
    secret = configured_secret.strip()
    if not secret:
        return True

    lower_headers = {str(k).strip().lower(): str(v).strip() for k, v in headers.items()}

    # 1. Check HMAC SHA-256 signature headers (raw hex or `sha256=<hex>`)
    sig_header = lower_headers.get("x-nasio-signature") or lower_headers.get("x-webhook-signature") or ""
    if sig_header:
        provided_hex = sig_header[7:].strip() if sig_header.lower().startswith("sha256=") else sig_header
        expected_hex = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        if _safe_compare_str(provided_hex.lower(), expected_hex.lower()):
            return True

    # 2. Check Zapier / webhook static secret headers (`X-Webhook-Secret` or `Authorization: Bearer`)
    static_secret = lower_headers.get("x-webhook-secret") or ""
    if static_secret and _safe_compare_str(static_secret, secret):
        return True

    auth_hdr = lower_headers.get("authorization") or ""
    if auth_hdr.lower().startswith("bearer "):
        bearer_tok = auth_hdr[7:].strip()
        if bearer_tok and _safe_compare_str(bearer_tok, secret):
            return True

    return False


def parse_nasio_webhook_payload(payload: Mapping[str, Any]) -> StoreOrderRecord:
    """Normalize Nas.io / Zapier 'New Purchase Transaction' JSON payloads into `StoreOrderRecord`."""
    if not isinstance(payload, Mapping):
        raise NasioWebhookError("Webhook JSON payload must be an object")

    nested_data = payload.get("data") if isinstance(payload.get("data"), Mapping) else {}
    src_obj: dict[str, Any] = {**nested_data, **payload}

    order_id = ""
    for id_key in ("order_id", "transaction_id", "purchase_id", "id", "event_id"):
        val = src_obj.get(id_key)
        if val is not None and not isinstance(val, (Mapping, list, bool)) and str(val).strip():
            order_id = str(val).strip()
            break
    if not order_id:
        raise NasioWebhookError("Missing required 'order_id' or 'transaction_id' in Nas.io payload")

    # Parse gross amount (cents take precedence over USD float/string fields)
    gross_cents: Optional[int] = None
    for cents_key in ("gross_cents", "amount_cents", "price_cents", "total_cents"):
        if src_obj.get(cents_key) is not None and str(src_obj.get(cents_key)).strip() != "":
            gross_cents = _parse_nonneg_int_cents(cents_key, src_obj[cents_key])
            break

    if gross_cents is None:
        for usd_key in ("gross_usd", "amount_usd", "amount", "price", "total"):
            if src_obj.get(usd_key) is not None and str(src_obj.get(usd_key)).strip() != "":
                gross_cents = usd_to_cents(src_obj[usd_key])
                break
    if gross_cents is None:
        gross_cents = 0

    pass_fees = _parse_bool_flag(
        src_obj.get("pass_fees_to_buyer"),
        field_name="pass_fees_to_buyer",
        default=True,
    )
    fee_cents: Optional[int] = None
    if src_obj.get("fee_cents") is not None and str(src_obj.get("fee_cents")).strip() != "":
        fee_cents = _parse_nonneg_int_cents("fee_cents", src_obj["fee_cents"])
    else:
        for fee_usd_key in ("fee_usd", "platform_fee_usd", "fee"):
            if src_obj.get(fee_usd_key) is not None and str(src_obj.get(fee_usd_key)).strip() != "":
                fee_cents = usd_to_cents(src_obj[fee_usd_key])
                break
    if fee_cents is None:
        fee_cents = estimate_nasio_fee_cents(gross_cents, pass_fees_to_buyer=pass_fees)
    fee_cents = min(gross_cents, fee_cents)

    status_raw = str(src_obj.get("status") or "paid").strip().lower()
    explicit_refunded = _parse_bool_flag(
        src_obj.get("refunded"),
        field_name="refunded",
        default=False,
    )
    refunded = explicit_refunded or status_raw in {
        "refunded",
        "cancelled",
        "canceled",
        "chargedback",
        "chargeback",
    }

    if refunded:
        net_cents = 0
    elif src_obj.get("net_cents") is not None and str(src_obj.get("net_cents")).strip() != "":
        net_cents = _parse_nonneg_int_cents("net_cents", src_obj["net_cents"])
    elif src_obj.get("net_usd") is not None and str(src_obj.get("net_usd")).strip() != "":
        net_cents = usd_to_cents(src_obj["net_usd"])
    else:
        net_cents = max(0, gross_cents - fee_cents)

    utm = _extract_utm_from_mapping_or_url(
        src_obj,
        src_obj.get("utm"),
        src_obj.get("metadata"),
        src_obj.get("custom_fields"),
        src_obj.get("checkout_url"),
        src_obj.get("referrer"),
    )
    utm_source = (utm["utm_source"] or "nasio").strip().lower()
    utm_medium = (utm["utm_medium"] or "webhook").strip().lower()
    utm_campaign = (utm["utm_campaign"] or "nasio_challenge_2026_11").strip()

    sku = (
        utm["sku"]
        or str(src_obj.get("sku") or src_obj.get("product_slug") or src_obj.get("challenge_slug") or "").strip()
    )
    if not sku:
        sku = (
            "free-idempotent-cron-mini-kit"
            if gross_cents == 0
            else "nasio-7day-ai-automation-challenge-pack"
        )

    prod_name = str(
        src_obj.get("product_name")
        or src_obj.get("challenge_name")
        or src_obj.get("item_name")
        or src_obj.get("title")
        or ("Free Idempotent Cron Mini-Kit" if gross_cents == 0 else "Nas.io 7-Day Challenge Pack")
    ).strip()

    now_iso = utc_now_iso()
    created_at = str(src_obj.get("created_at") or src_obj.get("purchased_at") or now_iso).strip()

    return StoreOrderRecord(
        storefront="nasio",
        order_id=order_id,
        sku=sku,
        product_name=prod_name,
        gross_cents=gross_cents,
        fee_cents=fee_cents,
        net_cents=net_cents,
        currency=str(src_obj.get("currency") or "USD").strip().upper() or "USD",
        refunded=refunded,
        utm_source=utm_source,
        utm_medium=utm_medium,
        utm_campaign=utm_campaign,
        attributed_slug=utm["slug"] or str(src_obj.get("attributed_slug") or "").strip(),
        created_at=created_at,
        synced_at=now_iso,
        raw_data=dict(payload),
    )


def write_nasio_setup_guide(config: AppConfig) -> Path:
    """Ensure `exports/storefront/nasio_setup_and_challenge_guide.md` exists and reflects `config`."""
    from products.builder import (
        BuiltProductArtifact,
        _render_nasio_setup_and_challenge_guide,
        load_catalog,
    )

    out_dir = config.exports_dir / "storefront"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "nasio_setup_and_challenge_guide.md"

    catalog = load_catalog(config.project_root)
    manifest_file = config.dist_dir / "manifest.json"
    artifacts: list[BuiltProductArtifact] = []
    if manifest_file.is_file():
        try:
            raw_manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            for item in raw_manifest.get("products", []):
                artifacts.append(BuiltProductArtifact(**item))
        except (TypeError, ValueError, KeyError):
            artifacts = []

    if not artifacts:
        for spec in catalog.get("products", []):
            artifacts.append(
                BuiltProductArtifact(
                    slug=str(spec["slug"]),
                    title=str(spec["title"]),
                    tier=str(spec["tier"]),
                    price_usd=int(spec["price_usd"]),
                    suggested_price_usd=int(spec["suggested_price_usd"]),
                    zip_path=str(config.dist_dir / str(spec["zip_filename"])),
                    zip_filename=str(spec["zip_filename"]),
                    size_bytes=0,
                    sha256="pending_build_products",
                    file_count=len(spec.get("required_files", [])),
                    python_tests_passed=True,
                    node_tests_passed=None,
                )
            )

    md_text = _render_nasio_setup_and_challenge_guide(
        catalog,
        artifacts,
        nasio_store_url=config.nasio_store_url,
        patreon_url=config.patreon_url,
    )
    out_path.write_text(md_text, encoding="utf-8")
    return out_path


def export_nasio_daily_pack(
    article: ArticleRecord,
    config: AppConfig,
    *,
    series_articles: Optional[list[ArticleRecord]] = None,
) -> dict[str, str]:
    """Generate the Daily 60-Minute Nas.io Execution Pack (`exports/<slug>/nasio_daily_pack.md`)."""
    bundle_dir = config.exports_dir / article.slug
    bundle_dir.mkdir(parents=True, exist_ok=True)

    meta = PILLAR_META.get(article.pillar, PILLAR_META["pillar_a"])
    day_num = article.series_day or 1
    dur_ms = article.verification.get("total_duration_ms", 0)
    blocks_n = article.verification.get("code_blocks_tested", 2)

    nasio_base = config.nasio_store_url or config.patreon_url or config.paid_checkout_url
    challenge_mr_url = append_storefront_utm(
        nasio_base,
        source="nasio",
        medium="magic_reach",
        campaign=f"30d_day{day_num:02d}",
        slug=article.slug,
        sku="nasio-7day-ai-automation-challenge-pack",
        configured_ls_url=config.lemonsqueezy_store_url,
    )
    free_mr_url = append_storefront_utm(
        nasio_base,
        source="nasio",
        medium="magic_reach",
        campaign=f"30d_day{day_num:02d}",
        slug=article.slug,
        sku="free-idempotent-cron-mini-kit",
        configured_ls_url=config.lemonsqueezy_store_url,
    )
    social_hook_url = append_storefront_utm(
        nasio_base,
        source="nasio",
        medium="value_hook",
        campaign=f"30d_day{day_num:02d}",
        slug=article.slug,
        sku="nasio-7day-ai-automation-challenge-pack",
        configured_ls_url=config.lemonsqueezy_store_url,
    )
    warm_dm_url = append_storefront_utm(
        nasio_base,
        source="nasio",
        medium="warm_dm",
        campaign=f"30d_day{day_num:02d}",
        slug=article.slug,
        sku="nasio-7day-ai-automation-challenge-pack",
        configured_ls_url=config.lemonsqueezy_store_url,
    )

    series_count = len(series_articles) if series_articles else day_num
    pack_md = f"""# Daily 60-Minute Nas.io Execution Pack — Day {day_num}/30

- **Article:** `{article.title}` (`{article.word_count}` words · `{blocks_n}` blocks verified in `{dur_ms} ms`)
- **Pillar Track:** {meta["label"]} ({meta["weekday"]})
- **Target Storefront:** `{nasio_base}` (`Pass fees to buyers = ON` · `$15` 7-Day Challenge = `4` sales for `$60` net)

---

## Block 1 (`00–15 min`): Nas.io Community Feed Drop + Magic Reach Email Broadcast

**Magic Reach Subject Line:**
`[Day {day_num}/30] {article.title} (Verified Python 3.11+ Benchmark)`

**Community Feed & Magic Reach Body (Copy & Paste):**
```markdown
Hey builders — welcome to Day {day_num}/30 of {SERIES_30D_NAME}!

Today's lab tackles {article.subtitle}

What we verified inside an isolated `python3 -I` sandbox ({dur_ms} ms runtime across {blocks_n} executable blocks):
- Why happy-path wrappers fail under real concurrency, retry storms, or malformed upstream payloads
- How to eliminate the failure mode using 100% Python standard library primitives ({series_count}/30 lessons live)

Read today's full runnable benchmark free:
{article.canonical_url}

Ready to build all 7 production guardrails hands-on with unit tests?
1. Free Starter Resource ($0): {free_mr_url}
2. Join the 7-Day Python & AI Reliability Challenge ($15 USD · 7 Runnable Labs + MIT License): {challenge_mr_url}
```

---

## Block 2 (`15–40 min`): External Traffic Value-Hook Post (LinkedIn / X / Dev — Strictly <= 2 Links)

> **Anti-Spam Hygiene:** Keep total outbound links `<= 2` so developer feeds do not throttle reach. Spend the remaining 15 minutes leaving 3 thoughtful, zero-link technical replies on Python/AI engineering threads.

**Ready-to-Post Social Value Hook (2 Links Exact):**
```text
[Day {day_num}/30] {article.title}

{article.subtitle}

We ran {blocks_n} isolated Python 3.11+ benchmark blocks ({dur_ms} ms total verification time, zero third-party packages) to test what actually breaks under production load:
- Retries without jitter + Retry-After parsing amplify upstream 429 outages
- Unvalidated LLM JSON (`isinstance(True, int)`) silently corrupts downstream state
- SQLite WAL + BEGIN IMMEDIATE leases replace external queue infrastructure on single-node deployments

Full runnable teardown & benchmark output:
{article.canonical_url}

7-Day Hands-On Python & AI Reliability Challenge Pack ($15 · MIT License):
{social_hook_url}
```

---

## Block 3 (`40–60 min`): Warm Welcome & Challenge Invite DM Script (Zero Cold Spam)

> **Who to Message:** Only new Nas.io Free Mini-Kit downloaders, new community members, or engaged commenters/followers from today's drop.

**3-Sentence Diagnostic Welcome DM:**
```text
Hey {{first_name}} — thanks for grabbing the Python reliability resources today! Quick question so I point you to the most useful lab: are you currently dealing more with flaky LLM/API retries or background queue/webhook idempotency?

If you want all 7 runnable stdlib labs + the failure-injection workbook (`test_challenge_kit.py` included under MIT license), here is direct access to the $15 7-Day Challenge Pack: {warm_dm_url}
```
"""

    pack_path = bundle_dir / "nasio_daily_pack.md"
    pack_path.write_text(pack_md, encoding="utf-8")
    return {"nasio_daily_pack": str(pack_path)}
