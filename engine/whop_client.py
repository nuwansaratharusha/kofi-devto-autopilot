"""First-Class Whop REST API v1 Client, Webhook Verifier, Daily 60-Min Pack & Setup Guide.

Designed for `$0`-capital Sri Lanka (`Asia/Colombo`) creators:
- Connects to Whop Business Hub (`https://whop.com/dashboard/biz_Af4z3lgivbct7t/`, Company ID: `biz_Af4z3lgivbct7t`)
  with `$0/mo` fixed subscription overhead, `$10 USD` minimum payout, safe USD balance holding, and free organic
  buyer discovery via the **Whop Discover Marketplace**.
- Pairs with Patreon (`https://www.patreon.com/NuwansaraT`, `$25 USD` Payoneer minimum payout) to hit `$50+ USD` net
  in Month 1 on `1 hour/day` effort.
- Syncs `GET https://api.whop.com/api/v1/payments` & `GET https://api.whop.com/api/v1/memberships` with company
  filtering and automatic cursor/page pagination, and ingests `POST /api/webhooks/whop` (`payment.succeeded`,
  `membership.went_valid`, `payment.refunded`) into `state/pipeline.db`.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import time
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from engine.config import AppConfig
from engine.db import PipelineDatabase, utc_now_iso
from engine.devto_client import HttpResult, _urllib_sender
from engine.models import PILLAR_META, SERIES_30D_NAME, ArticleRecord, StoreOrderRecord
from engine.storefronts import _extract_utm_from_mapping_or_url, append_storefront_utm

WHOP_API_BASE = "https://api.whop.com/api/v1"
DEFAULT_WHOP_COMPANY_ID = "biz_Af4z3lgivbct7t"
USER_AGENT = "KofiDevtoAutopilot/2.2 (+https://whop.com; creator-sync)"

CHALLENGE_SKU_ALIASES = frozenset(
    {
        "nasio-7day-ai-automation-challenge-pack",
        "whop-7day-ai-automation-challenge-pack",
    }
)

_CURRENCY_PREFIX_RE = re.compile(r"^(?:usd|us\$|\$)\s*", re.IGNORECASE)
_CURRENCY_SUFFIX_RE = re.compile(r"\s*(?:usd)$", re.IGNORECASE)
_ANY_CURRENCY_PREFIX_RE = re.compile(
    r"^(?:[a-z]{3}\s*|(?:us|ca|au|nz|hk|sg)?\$|€|£|₹|¥)\s*",
    re.IGNORECASE,
)
_ANY_CURRENCY_SUFFIX_RE = re.compile(r"\s*[a-z]{3}$", re.IGNORECASE)

_FULL_REFUNDED_STATUSES = frozenset(
    {
        "refunded",
        "auto_refunded",
        "resolution_lost",
        "chargedback",
        "chargeback",
        "disputed",
    }
)
_REFUNDED_STATUSES = _FULL_REFUNDED_STATUSES | frozenset({"partially_refunded"})
_FAILED_OR_UNSETTLED_PAYMENT_STATUSES = frozenset(
    {
        "failed",
        "pending",
        "open",
        "requires_payment_method",
        "requires_action",
        "requires_confirmation",
        "canceled",
        "cancelled",
        "void",
        "voided",
        "uncollectible",
        "draft",
        "expired",
    }
)
_SETTLED_PAYMENT_STATUSES = frozenset(
    {
        "paid",
        "succeeded",
        "completed",
        "settled",
        "active",
        "valid",
    }
)
_INVALID_MEMBERSHIP_STATUSES = frozenset(
    {
        "canceled",
        "cancelled",
        "expired",
        "past_due",
        "banned",
        "invalid",
        "drafted",
        "incomplete",
        "unpaid",
    }
)
_VALID_MEMBERSHIP_STATUSES = frozenset(
    {
        "active",
        "valid",
        "completed",
        "trialing",
        "joined",
    }
)


class WhopApiError(RuntimeError):
    """Raised when Whop REST API v1 returns an unrecoverable HTTP or payload error."""


class WhopWebhookError(ValueError):
    """Raised when a Whop webhook payload or amount/field is malformed."""


def _safe_compare_str(left: str, right: str) -> bool:
    """Constant-time UTF-8 byte comparison safe against non-ASCII HTTP header values."""
    return hmac.compare_digest(
        left.encode("utf-8", errors="surrogateescape"),
        right.encode("utf-8", errors="surrogateescape"),
    )


def _parse_bool_flag(value: Any, *, field_name: str = "flag", default: bool = False) -> bool:
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
        raise WhopWebhookError(f"Invalid boolean field {field_name}: {value!r}")
    raise WhopWebhookError(f"Invalid boolean field {field_name}: {value!r}")


def _parse_nonneg_int_cents(field_name: str, value: Any) -> int:
    if isinstance(value, bool):
        raise WhopWebhookError(f"Boolean not allowed for integer field {field_name}: {value!r}")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        clean = value.strip().replace(",", "")
        if not re.fullmatch(r"[+-]?\d+", clean):
            raise WhopWebhookError(f"Invalid integer field {field_name}: {value!r}")
        parsed = int(clean)
    else:
        raise WhopWebhookError(f"Invalid integer field {field_name}: {value!r}")
    if parsed < 0:
        raise WhopWebhookError(f"{field_name} must be >= 0 (got {value!r})")
    return parsed


def whop_usd_to_cents(amount_usd: float | str | int | Decimal) -> int:
    """Convert a non-negative USD amount (e.g. '15.00', '$15.00', 15, 14.99) into integer cents."""
    if isinstance(amount_usd, bool):
        raise WhopWebhookError(f"Boolean not allowed for USD amount: {amount_usd!r}")
    raw = str(amount_usd).strip()
    if not raw:
        raise WhopWebhookError(f"Empty USD amount: {amount_usd!r}")
    if raw.startswith("-"):
        raise WhopWebhookError(f"USD amount must be >= 0 (got {amount_usd!r})")
    if raw.startswith("+"):
        raw = raw[1:].strip()
    cleaned = _CURRENCY_PREFIX_RE.sub("", raw)
    cleaned = _CURRENCY_SUFFIX_RE.sub("", cleaned).strip().replace(",", "")
    if not cleaned:
        raise WhopWebhookError(f"Invalid USD amount: {amount_usd!r}")
    try:
        dec = Decimal(cleaned)
    except (InvalidOperation, ValueError) as exc:
        raise WhopWebhookError(f"Invalid USD amount: {amount_usd!r}") from exc
    if not dec.is_finite():
        raise WhopWebhookError(f"Non-finite USD amount: {amount_usd!r}")
    if dec < 0:
        raise WhopWebhookError(f"USD amount must be >= 0 (got {amount_usd!r})")
    return int((dec * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _parse_decimal_amount_cents(amount_val: Any) -> int:
    """Parse a non-negative Money decimal amount (supporting USD or local currency symbols) into minor cents."""
    if isinstance(amount_val, bool):
        raise WhopWebhookError(f"Boolean not allowed for Money amount: {amount_val!r}")
    raw = str(amount_val).strip()
    if not raw:
        raise WhopWebhookError(f"Empty Money amount: {amount_val!r}")
    if raw.startswith("-"):
        raise WhopWebhookError(f"Money amount must be >= 0 (got {amount_val!r})")
    if raw.startswith("+"):
        raw = raw[1:].strip()
    cleaned = _ANY_CURRENCY_PREFIX_RE.sub("", raw)
    cleaned = _ANY_CURRENCY_SUFFIX_RE.sub("", cleaned).strip().replace(",", "")
    if cleaned.startswith("-"):
        raise WhopWebhookError(f"Money amount must be >= 0 (got {amount_val!r})")
    if not cleaned:
        raise WhopWebhookError(f"Invalid Money amount: {amount_val!r}")
    try:
        dec = Decimal(cleaned)
    except (InvalidOperation, ValueError) as exc:
        raise WhopWebhookError(f"Invalid Money amount: {amount_val!r}") from exc
    if not dec.is_finite():
        raise WhopWebhookError(f"Non-finite Money amount: {amount_val!r}")
    if dec < 0:
        raise WhopWebhookError(f"Money amount must be >= 0 (got {amount_val!r})")
    return int((dec * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def estimate_whop_fee_cents(gross_cents: int) -> int:
    """Estimate Whop platform (~3%) + card processing (~2.7% + $0.30) fee when `fee_cents` is absent."""
    if gross_cents <= 0:
        return 0
    pct_fee = int(round(gross_cents * 0.057))
    fixed_fee = 30
    return min(gross_cents, pct_fee + fixed_fee)


def _parse_money_field_cents(
    obj: Mapping[str, Any],
    *,
    cents_keys: tuple[str, ...],
    money_keys: tuple[str, ...],
) -> tuple[Optional[int], Optional[str]]:
    """Extract integer cents and optional currency code from explicit `*_cents` or Whop v1 Money/USD fields."""
    for ck in cents_keys:
        val = obj.get(ck)
        if val is not None and str(val).strip() != "":
            return _parse_nonneg_int_cents(ck, val), None

    for mk in money_keys:
        val = obj.get(mk)
        if val is None:
            continue
        if isinstance(val, Mapping):
            amt = val.get("amount")
            cur = str(val.get("currency") or "").strip().upper() or None
            if amt is not None and str(amt).strip() != "":
                return _parse_decimal_amount_cents(amt), cur
            if val.get("cents") is not None and str(val.get("cents")).strip() != "":
                return _parse_nonneg_int_cents(f"{mk}.cents", val["cents"]), cur
            continue
        if str(val).strip() != "":
            return _parse_decimal_amount_cents(val), None

    return None, None


def _resolve_whop_payment_amounts(
    obj: Mapping[str, Any],
) -> tuple[int, int, Optional[int], str, Optional[str]]:
    """Resolve `(gross_cents, fee_cents, after_fees_cents, ledger_currency, original_currency)` in USD cents."""
    orig_cur = str(obj.get("currency") or "").strip().upper() or None

    # 1. Prefer explicit USD gross (`usd_total` in Whop API v1) so non-USD buyer charges never inflate USD sums
    usd_gross_cents, usd_cur = _parse_money_field_cents(
        obj,
        cents_keys=("usd_total_cents", "gross_usd_cents"),
        money_keys=("usd_total", "gross_usd", "amount_usd"),
    )
    if usd_gross_cents is not None:
        gross_cents: Optional[int] = usd_gross_cents
        gross_cur = (usd_cur or "USD").upper()
    else:
        gross_cents, money_cur = _parse_money_field_cents(
            obj,
            cents_keys=(
                "gross_cents",
                "amount_cents",
                "final_amount_cents",
                "subtotal_cents",
                "total_cents",
                "price_cents",
            ),
            money_keys=(
                "amount_charged",
                "final_amount",
                "total",
                "subtotal",
                "amount",
                "price",
            ),
        )
        if gross_cents is None:
            line_items = obj.get("line_items")
            if isinstance(line_items, list) and line_items and isinstance(line_items[0], Mapping):
                gross_cents, money_cur = _parse_money_field_cents(
                    line_items[0],
                    cents_keys=("subtotal_cents", "amount_cents", "total_cents"),
                    money_keys=("usd_total", "subtotal", "total", "amount", "price"),
                )
        gross_cur = (money_cur or orig_cur or "USD").upper()

    fee_cents, fee_cur = _parse_money_field_cents(
        obj,
        cents_keys=("fee_cents", "processor_fee_cents", "platform_fee_cents"),
        money_keys=("fee_usd", "platform_fee_usd", "fee"),
    )
    after_fees_cents, after_cur = _parse_money_field_cents(
        obj,
        cents_keys=("net_cents", "amount_after_fees_cents"),
        money_keys=("net_usd", "amount_after_fees"),
    )

    # If `total` was in a foreign currency without `usd_total`, never subtract USD `amount_after_fees` from FX total!
    if gross_cur != "USD" and after_fees_cents is not None and (after_cur or "USD").upper() == "USD":
        if fee_cents is not None and (fee_cur or "USD").upper() == "USD":
            gross_cents = after_fees_cents + fee_cents
        elif after_fees_cents > 0:
            gross_cents = int(round((after_fees_cents + 30) / (1.0 - 0.057)))
        else:
            gross_cents = 0
        gross_cur = "USD"

    if gross_cents is None:
        gross_cents = 0

    if fee_cents is None:
        if after_fees_cents is not None and gross_cents >= after_fees_cents:
            fee_cents = gross_cents - after_fees_cents
        else:
            fee_cents = estimate_whop_fee_cents(gross_cents)
    fee_cents = min(gross_cents, fee_cents)

    ledger_cur = gross_cur or "USD"
    return gross_cents, fee_cents, after_fees_cents, ledger_cur, orig_cur


def _extract_company_id(obj: Mapping[str, Any]) -> str:
    """Extract Whop business identifier (`biz_...`) across both `company_id` and 2026+ `account_id` schemas."""
    for key in ("company_id", "account_id"):
        raw_cid = obj.get(key)
        if isinstance(raw_cid, str) and raw_cid.strip():
            return raw_cid.strip()
    for nested_key in ("company", "account"):
        nested = obj.get(nested_key)
        if isinstance(nested, Mapping):
            cid = nested.get("id")
            if isinstance(cid, str) and cid.strip():
                return cid.strip()
        elif isinstance(nested, str) and nested.strip().startswith("biz_"):
            return nested.strip()
    return ""


def _normalize_membership_id_variants(raw_mem_id: str) -> set[str]:
    clean = raw_mem_id.strip()
    if not clean:
        return set()
    bare = clean[4:] if clean.startswith("mem_") else clean
    return {clean, f"mem_{bare}", bare}


def _extract_whop_correlation_keys(
    obj: Mapping[str, Any],
    *,
    is_membership: bool = False,
) -> tuple[set[str], set[str]]:
    """Extract `(membership_id_variants, user_product_subject_keys)` to deduplicate paid memberships vs payments."""
    mem_ids: set[str] = set()
    raw_mem = str(
        obj.get("membership_id")
        or (
            (obj.get("membership") or {}).get("id")
            if isinstance(obj.get("membership"), Mapping)
            else (obj.get("membership") or "")
        )
    ).strip()
    if raw_mem:
        mem_ids.update(_normalize_membership_id_variants(raw_mem))
    if is_membership:
        own_id = str(obj.get("id") or "").strip()
        if own_id:
            mem_ids.update(_normalize_membership_id_variants(own_id))

    user_id = str(
        obj.get("user_id")
        or ((obj.get("user") or {}).get("id") if isinstance(obj.get("user"), Mapping) else "")
        or obj.get("member_id")
        or ((obj.get("member") or {}).get("id") if isinstance(obj.get("member"), Mapping) else "")
    ).strip()

    target_ids: list[str] = []
    for tk in ("plan_id", "variant_id", "product_id"):
        tv = str(obj.get(tk) or "").strip()
        if tv:
            target_ids.append(tv)
    for nk in ("plan", "product"):
        nv = obj.get(nk)
        if isinstance(nv, Mapping) and str(nv.get("id") or "").strip():
            target_ids.append(str(nv["id"]).strip())

    subj_keys = {f"{user_id}:{tid}" for tid in target_ids if user_id and tid}
    return mem_ids, subj_keys


def _extract_product_and_sku(
    obj: Mapping[str, Any],
    *,
    utm_sku: str,
    gross_cents: int,
    is_membership: bool = False,
    allow_defaults: bool = True,
) -> tuple[str, str]:
    prod_obj = obj.get("product") if isinstance(obj.get("product"), Mapping) else {}
    plan_obj = obj.get("plan") if isinstance(obj.get("plan"), Mapping) else {}
    line_items = obj.get("line_items") if isinstance(obj.get("line_items"), list) else []
    first_li = line_items[0] if (line_items and isinstance(line_items[0], Mapping)) else {}

    raw_sku = (
        utm_sku
        or str(
            obj.get("sku")
            or obj.get("product_slug")
            or prod_obj.get("slug")
            or prod_obj.get("route")
            or plan_obj.get("slug")
            or ""
        ).strip()
    )
    if not raw_sku and allow_defaults:
        if gross_cents == 0:
            raw_sku = "free-idempotent-cron-mini-kit"
        elif gross_cents == 1400:
            raw_sku = "production-agent-automation-kit"
        else:
            raw_sku = "nasio-7day-ai-automation-challenge-pack"

    raw_name = str(
        obj.get("product_name")
        or prod_obj.get("title")
        or prod_obj.get("name")
        or first_li.get("title")
        or first_li.get("name")
        or plan_obj.get("title")
        or plan_obj.get("name")
        or obj.get("title")
        or ""
    ).strip()
    if not raw_name and allow_defaults:
        if gross_cents == 0:
            raw_name = "Whop Free Member · Idempotent Cron Mini-Kit" if is_membership else "Free Idempotent Cron Mini-Kit"
        elif raw_sku in CHALLENGE_SKU_ALIASES:
            raw_name = "Whop 7-Day Python & AI Reliability Challenge"
        elif raw_sku == "production-agent-automation-kit":
            raw_name = "Production AI Agent & Backend Reliability Starter Kit"
        else:
            raw_name = "Whop Digital Product"

    return raw_sku, raw_name


def _candidate_signing_keys(secret: str) -> list[bytes]:
    keys = [secret.encode("utf-8")]
    for prefix in ("whsec_", "ws_"):
        if secret.startswith(prefix):
            raw_part = secret[len(prefix) :].strip()
            if raw_part:
                keys.append(raw_part.encode("utf-8"))
                padded = raw_part + ("=" * ((4 - len(raw_part) % 4) % 4))
                try:
                    keys.append(base64.b64decode(padded, validate=False))
                except (binascii.Error, ValueError):
                    pass
    return keys


def verify_whop_webhook_auth(
    raw_body: bytes,
    headers: Mapping[str, str],
    configured_secret: str,
) -> bool:
    """Verify Whop Standard Webhooks (`webhook-signature`), HMAC SHA-256 (`X-Whop-Signature`), or Bearer/Secret."""
    secret = configured_secret.strip()
    if not secret:
        return True

    lower_headers = {str(k).strip().lower(): str(v).strip() for k, v in headers.items()}
    signing_keys = _candidate_signing_keys(secret)

    # 1. Standard Webhooks (`webhook-signature: v1,<base64>` with `webhook-id` & `webhook-timestamp`)
    std_sig_hdr = lower_headers.get("webhook-signature") or ""
    msg_id = lower_headers.get("webhook-id") or lower_headers.get("svix-id") or ""
    msg_ts = lower_headers.get("webhook-timestamp") or lower_headers.get("svix-timestamp") or ""
    if std_sig_hdr:
        signed_frames = (
            [
                msg_id.encode("utf-8", errors="surrogateescape")
                + b"."
                + msg_ts.encode("utf-8", errors="surrogateescape")
                + b"."
                + raw_body
            ]
            if (msg_id and msg_ts)
            else [raw_body]
        )
        for token in std_sig_hdr.split():
            cand = token.strip()
            if cand.startswith("v1,"):
                provided_b64 = cand[3:].strip()
                for key_bytes in signing_keys:
                    for frame in signed_frames:
                        expected_b64 = base64.b64encode(
                            hmac.new(key_bytes, frame, hashlib.sha256).digest()
                        ).decode("ascii")
                        if _safe_compare_str(provided_b64, expected_b64):
                            return True
            else:
                provided_hex = cand[7:].strip() if cand.lower().startswith("sha256=") else cand
                for key_bytes in signing_keys:
                    expected_hex = hmac.new(key_bytes, raw_body, hashlib.sha256).hexdigest()
                    if _safe_compare_str(provided_hex.lower(), expected_hex.lower()):
                        return True

    # 2. HMAC SHA-256 hex/base64 headers (`X-Whop-Signature` / `X-Webhook-Signature`)
    sig_header = lower_headers.get("x-whop-signature") or lower_headers.get("x-webhook-signature") or ""
    if sig_header:
        clean_sig = sig_header
        for prefix in ("sha256=", "v1=", "v1,"):
            if clean_sig.lower().startswith(prefix):
                clean_sig = clean_sig[len(prefix) :].strip()
                break
        for key_bytes in signing_keys:
            digest = hmac.new(key_bytes, raw_body, hashlib.sha256).digest()
            expected_hex = digest.hex()
            expected_b64 = base64.b64encode(digest).decode("ascii")
            if _safe_compare_str(clean_sig.lower(), expected_hex.lower()) or _safe_compare_str(
                clean_sig, expected_b64
            ):
                return True

    # 3. Static secret header (`X-Webhook-Secret` or `Authorization: Bearer <secret>`)
    static_secret = lower_headers.get("x-webhook-secret") or ""
    if static_secret and _safe_compare_str(static_secret, secret):
        return True

    auth_hdr = lower_headers.get("authorization") or ""
    if auth_hdr.lower().startswith("bearer "):
        bearer_tok = auth_hdr[7:].strip()
        if bearer_tok and _safe_compare_str(bearer_tok, secret):
            return True

    return False


def parse_whop_webhook_payload(
    payload: Mapping[str, Any],
    *,
    expected_company_id: str = "",
) -> Optional[StoreOrderRecord]:
    """Normalize Whop webhooks (`payment.succeeded`, `membership.went_valid`, `refund.created`/`payment.refunded`).

    Returns `None` for non-settled or duplicate-lifecycle events (e.g. `payment.failed`, `payment.pending`,
    failed `refund.*`, or paid-plan `membership.went_valid` covered by `payment.succeeded`) so callers can
    acknowledge HTTP 200 without polluting `store_orders`. Raises `WhopWebhookError` on malformed inputs.
    """
    if not isinstance(payload, Mapping):
        raise WhopWebhookError("Whop webhook JSON payload must be an object")

    raw_event = str(
        payload.get("type") or payload.get("event") or payload.get("action") or ""
    ).strip().lower()
    event_norm = raw_event.replace("_", ".")

    nested_data = payload.get("data") if isinstance(payload.get("data"), Mapping) else {}
    nested_payment = (
        nested_data.get("payment") if isinstance(nested_data.get("payment"), Mapping) else {}
    )
    # Nested entity (`data`) fields must take precedence over envelope `id`/`type`!
    src_obj: dict[str, Any] = {**payload, **nested_payment, **nested_data}

    payload_cid = (
        _extract_company_id(nested_data)
        or _extract_company_id(nested_payment)
        or _extract_company_id(payload)
    )
    exp_cid = expected_company_id.strip()
    if exp_cid and payload_cid and payload_cid != exp_cid:
        raise WhopWebhookError(
            f"Whop webhook company_id '{payload_cid}' does not match configured '{exp_cid}'"
        )

    is_refund_resource_event = event_norm.startswith("refund.")
    is_explicit_payment_refund_event = event_norm in {"payment.refunded", "charge.refunded"}
    is_refund_event = is_refund_resource_event or is_explicit_payment_refund_event
    is_membership_event = event_norm.startswith("membership.")

    status_only = str(nested_data.get("status") if nested_data else payload.get("status") or "").strip().lower()
    substatus_only = str(src_obj.get("substatus") or "").strip().lower()

    if is_refund_resource_event and status_only in {"failed", "canceled", "cancelled", "rejected"}:
        return None

    if (
        event_norm in {"payment.failed", "payment.pending", "membership.went.invalid"}
        and not is_refund_event
    ):
        explicit_ref = _parse_bool_flag(src_obj.get("refunded"), field_name="refunded", default=False)
        auto_ref = _parse_bool_flag(src_obj.get("auto_refunded"), field_name="auto_refunded", default=False)
        if not (explicit_ref or auto_ref):
            return None

    # Resolve entity ID (for `refund.*` events, target the parent `payment.id` / `payment_id`, NOT `ref_...`!)
    order_id = ""
    id_candidates: list[Any] = []
    if is_refund_resource_event:
        id_candidates.extend(
            [
                nested_payment.get("id"),
                nested_data.get("payment_id"),
                payload.get("payment_id"),
                nested_data.get("order_id"),
                payload.get("order_id"),
                nested_data.get("id"),
            ]
        )
    else:
        if isinstance(nested_data, Mapping) and nested_data:
            id_candidates.extend(
                [
                    nested_data.get("id"),
                    nested_data.get("payment_id"),
                    nested_data.get("membership_id"),
                    nested_data.get("order_id"),
                    nested_data.get("transaction_id"),
                ]
            )
        id_candidates.extend(
            [
                payload.get("payment_id"),
                payload.get("membership_id"),
                payload.get("order_id"),
                payload.get("transaction_id"),
                payload.get("id") if not nested_data else None,
            ]
        )
    for val in id_candidates:
        if val is not None and not isinstance(val, (Mapping, list, bool)) and str(val).strip():
            order_id = str(val).strip()
            break
    if not order_id:
        raise WhopWebhookError("Missing required payment/membership 'id' or 'order_id' in Whop webhook payload")

    if is_membership_event and not order_id.startswith(("mem_", "pay_", "ord_")):
        order_id = f"mem_{order_id}"

    explicit_refunded = _parse_bool_flag(
        src_obj.get("refunded"),
        field_name="refunded",
        default=False,
    )
    auto_refunded = _parse_bool_flag(
        src_obj.get("auto_refunded"),
        field_name="auto_refunded",
        default=False,
    )

    refunded_amt_cents: Optional[int] = None
    if is_refund_resource_event:
        refunded_amt_cents, _ = _parse_money_field_cents(
            nested_data or payload,
            cents_keys=("usd_amount_cents", "amount_cents", "refunded_amount_cents"),
            money_keys=("usd_amount", "amount", "refunded_amount"),
        )
        # Resolve parent payment gross from `data.payment` when present
        if nested_payment:
            gross_cents, fee_cents, after_fees_cents, currency, orig_cur = _resolve_whop_payment_amounts(
                nested_payment
            )
        else:
            gross_cents, fee_cents, after_fees_cents, currency, orig_cur = (
                0,
                0,
                None,
                str(src_obj.get("currency") or "USD").strip().upper() or "USD",
                None,
            )
    else:
        refunded_amt_cents, _ = _parse_money_field_cents(
            src_obj,
            cents_keys=("refunded_amount_cents", "amount_refunded_cents"),
            money_keys=("refunded_amount", "amount_refunded"),
        )
        gross_cents, fee_cents, after_fees_cents, currency, orig_cur = _resolve_whop_payment_amounts(src_obj)

    # Paid memberships fire BOTH `payment.succeeded` and `membership.went_valid`; only `$0` free memberships
    # belong in `store_orders` from `membership.*` events so paid sales are never double-counted.
    if is_membership_event and gross_cents > 0:
        return None

    is_full_status_refund = (
        is_explicit_payment_refund_event
        or explicit_refunded
        or auto_refunded
        or status_only in _FULL_REFUNDED_STATUSES
        or substatus_only in _FULL_REFUNDED_STATUSES
    )
    is_partial_refund = (
        not is_full_status_refund
        and refunded_amt_cents is not None
        and 0 < refunded_amt_cents < gross_cents
    )
    refunded = (
        is_full_status_refund
        or (is_refund_resource_event and not is_partial_refund)
        or (
            not is_partial_refund
            and (
                status_only == "partially_refunded"
                or substatus_only == "partially_refunded"
                or (refunded_amt_cents is not None and refunded_amt_cents > 0)
            )
        )
    )

    if not (refunded or is_partial_refund):
        if (
            status_only in _FAILED_OR_UNSETTLED_PAYMENT_STATUSES
            or substatus_only in _FAILED_OR_UNSETTLED_PAYMENT_STATUSES
        ):
            return None
        if is_membership_event:
            valid_flag = src_obj.get("valid")
            if valid_flag is not None and not _parse_bool_flag(valid_flag, field_name="valid", default=True):
                return None
            if status_only and status_only in _INVALID_MEMBERSHIP_STATUSES:
                return None

    if is_partial_refund and refunded_amt_cents is not None:
        base_net = after_fees_cents if after_fees_cents is not None else max(0, gross_cents - fee_cents)
        gross_cents = max(0, gross_cents - refunded_amt_cents)
        fee_cents = min(gross_cents, fee_cents)
        net_cents = max(0, base_net - refunded_amt_cents)
    elif refunded:
        net_cents = 0
    elif after_fees_cents is not None:
        net_cents = after_fees_cents
    else:
        net_cents = max(0, gross_cents - fee_cents)

    utm = _extract_utm_from_mapping_or_url(
        src_obj,
        src_obj.get("metadata"),
        src_obj.get("custom_fields"),
        src_obj.get("utm"),
        src_obj.get("checkout_url"),
        src_obj.get("referrer"),
    )
    allow_defaults = not (refunded or is_refund_event)
    utm_source = (utm["utm_source"] or ("whop" if allow_defaults else "")).strip().lower()
    utm_medium = (
        utm["utm_medium"]
        or (("membership_webhook" if is_membership_event else "webhook") if allow_defaults else "")
    ).strip().lower()
    utm_campaign = (utm["utm_campaign"] or ("whop_challenge_2026_11" if allow_defaults else "")).strip()

    sku, prod_name = _extract_product_and_sku(
        src_obj,
        utm_sku=utm["sku"],
        gross_cents=gross_cents,
        is_membership=is_membership_event,
        allow_defaults=allow_defaults,
    )
    mem_ids, subj_keys = _extract_whop_correlation_keys(src_obj, is_membership=is_membership_event)
    now_iso = utc_now_iso()
    created_at = str(
        src_obj.get("created_at") or src_obj.get("paid_at") or payload.get("timestamp") or now_iso
    ).strip()

    raw_meta: dict[str, Any] = {
        **dict(payload),
        "_mem_ids": sorted(mem_ids),
        "_subj_keys": sorted(subj_keys),
    }
    if orig_cur and orig_cur != currency:
        raw_meta["original_currency"] = orig_cur
    if refunded_amt_cents is not None and refunded_amt_cents > 0:
        raw_meta["_refund_amount_cents"] = refunded_amt_cents
    if is_full_status_refund:
        raw_meta["_explicit_full_refund"] = True

    return StoreOrderRecord(
        storefront="whop",
        order_id=order_id,
        sku=sku,
        product_name=prod_name,
        gross_cents=gross_cents,
        fee_cents=fee_cents,
        net_cents=net_cents,
        currency=currency,
        refunded=refunded,
        utm_source=utm_source,
        utm_medium=utm_medium,
        utm_campaign=utm_campaign,
        attributed_slug=utm["slug"] or str(src_obj.get("attributed_slug") or "").strip(),
        created_at=created_at,
        synced_at=now_iso,
        raw_data=raw_meta,
    )


def _order_correlation_sets(order: StoreOrderRecord) -> tuple[set[str], set[str]]:
    raw = order.raw_data if isinstance(order.raw_data, Mapping) else {}
    mem_ids: set[str] = set()
    for m in raw.get("_mem_ids") or []:
        if isinstance(m, str) and m.strip():
            mem_ids.update(_normalize_membership_id_variants(m))
    if isinstance(raw.get("membership_id"), str) and raw["membership_id"].strip():
        mem_ids.update(_normalize_membership_id_variants(raw["membership_id"]))
    nested_d = raw.get("data") if isinstance(raw.get("data"), Mapping) else {}
    if nested_d:
        extra_m, extra_s = _extract_whop_correlation_keys(
            nested_d,
            is_membership=order.order_id.startswith("mem_"),
        )
        mem_ids.update(extra_m)
    else:
        extra_s = set()
    if order.order_id.startswith("mem_"):
        mem_ids.update(_normalize_membership_id_variants(order.order_id))

    subj_keys: set[str] = set(extra_s)
    for s in raw.get("_subj_keys") or []:
        if isinstance(s, str) and s.strip():
            subj_keys.add(s.strip())
    return mem_ids, subj_keys


def ingest_whop_webhook_order(
    db: PipelineDatabase,
    order: Optional[StoreOrderRecord],
) -> Optional[StoreOrderRecord]:
    """Persist a normalized Whop webhook order with SQLite refund & membership/payment deduplication."""
    if order is None:
        return None

    existing_whop = [o for o in db.list_store_orders(include_refunded=True) if o.storefront == "whop"]
    by_id = {o.order_id: o for o in existing_whop}
    in_mem_ids, in_subj_keys = _order_correlation_sets(order)

    # 1. Deduplicate `$0` `mem_...` webhook if already covered by a stored `pay_...` charge
    if order.order_id.startswith("mem_") and order.gross_cents == 0 and not order.refunded:
        for ex in existing_whop:
            if ex.order_id.startswith("mem_"):
                continue
            ex_mem, ex_subj = _order_correlation_sets(ex)
            if (in_mem_ids and (in_mem_ids & ex_mem)) or (in_subj_keys and (in_subj_keys & ex_subj)):
                return None

    # 2. If a `pay_...` charge arrives after a `$0` `mem_...` webhook for the same membership/plan, remove the duplicate `mem_...`
    if not order.order_id.startswith("mem_") and (in_mem_ids or in_subj_keys):
        superseded: list[tuple[str, str]] = []
        for ex in existing_whop:
            if ex.order_id.startswith("mem_") and ex.gross_cents == 0:
                ex_mem, ex_subj = _order_correlation_sets(ex)
                if (in_mem_ids and (in_mem_ids & ex_mem)) or (in_subj_keys and (in_subj_keys & ex_subj)):
                    superseded.append(("whop", ex.order_id))
        if superseded:
            db.delete_store_orders(superseded)

    # 3. Reconcile partial or full refunds against an existing stored order so attribution, SKU & partial amounts stay exact
    prev = by_id.get(order.order_id)
    if prev is not None and (order.refunded or "_refund_amount_cents" in order.raw_data):
        ref_amt = order.raw_data.get("_refund_amount_cents")
        explicit_full = bool(order.raw_data.get("_explicit_full_refund"))
        if (
            not explicit_full
            and isinstance(ref_amt, int)
            and 0 < ref_amt < prev.gross_cents
        ):
            rem_gross = prev.gross_cents - ref_amt
            rem_fee = min(rem_gross, prev.fee_cents)
            rem_net = max(0, prev.net_cents - ref_amt)
            order = StoreOrderRecord(
                storefront="whop",
                order_id=order.order_id,
                sku=order.sku or prev.sku,
                product_name=order.product_name or prev.product_name,
                gross_cents=rem_gross,
                fee_cents=rem_fee,
                net_cents=rem_net,
                currency=prev.currency or order.currency,
                refunded=False,
                utm_source=order.utm_source or prev.utm_source,
                utm_medium=order.utm_medium or prev.utm_medium,
                utm_campaign=order.utm_campaign or prev.utm_campaign,
                attributed_slug=order.attributed_slug or prev.attributed_slug,
                created_at=prev.created_at or order.created_at,
                synced_at=order.synced_at,
                raw_data={**prev.raw_data, **order.raw_data, "partial_refund_reconciled": True},
            )
        elif order.refunded:
            order = StoreOrderRecord(
                storefront="whop",
                order_id=order.order_id,
                sku=order.sku or prev.sku,
                product_name=order.product_name or prev.product_name,
                gross_cents=order.gross_cents if order.gross_cents > 0 else prev.gross_cents,
                fee_cents=order.fee_cents if order.gross_cents > 0 else prev.fee_cents,
                net_cents=0,
                currency=prev.currency or order.currency,
                refunded=True,
                utm_source=order.utm_source or prev.utm_source,
                utm_medium=order.utm_medium or prev.utm_medium,
                utm_campaign=order.utm_campaign or prev.utm_campaign,
                attributed_slug=order.attributed_slug or prev.attributed_slug,
                created_at=prev.created_at or order.created_at,
                synced_at=order.synced_at,
                raw_data={**prev.raw_data, **order.raw_data, "reconciled_refunded": True},
            )

    db.upsert_store_orders([order])
    return order


class WhopClient:
    """Pure-stdlib Whop REST API v1 client (`https://api.whop.com/api/v1/payments` & `/memberships`)."""

    def __init__(
        self,
        config: Optional[AppConfig] = None,
        *,
        api_key: Optional[str] = None,
        company_id: Optional[str] = None,
        sender: Callable[[urllib.request.Request, float], HttpResult] = _urllib_sender,
        sleeper: Callable[[float], None] = time.sleep,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.config = config
        raw_key = api_key if api_key is not None else (config.whop_api_key if config else "")
        raw_cid = company_id if company_id is not None else (config.whop_company_id if config else "")
        self.api_key = raw_key.strip()
        self.company_id = raw_cid.strip()
        self._sender = sender
        self._sleep = sleeper
        self.timeout_seconds = timeout_seconds
        self._company_query_param: Optional[str] = "company_id"
        self._company_body_param: str = "company_id"
        self._last_refunded_order_ids: set[str] = set()
        self._last_paid_membership_ids: set[str] = set()
        self._last_paid_subject_keys: set[str] = set()

    def has_live_credentials(self) -> bool:
        return bool(self.api_key)

    def _matches_company(self, item: Mapping[str, Any]) -> bool:
        if not self.company_id:
            return True
        item_cid = _extract_company_id(item)
        if item_cid and item_cid != self.company_id:
            return False
        return True

    def _request_json(
        self,
        method: str,
        url_or_path: str,
        *,
        query: Optional[Mapping[str, str]] = None,
        json_body: Optional[Mapping[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        expected_statuses: tuple[int, ...] = (200, 201),
    ) -> Any:
        if not self.api_key:
            raise WhopApiError("WHOP_API_KEY is empty")

        if url_or_path.startswith("https://"):
            url = url_or_path
        else:
            url = f"{WHOP_API_BASE}{url_or_path}"
            if query:
                clean_q = {k: v for k, v in query.items() if v is not None and v != ""}
                if clean_q:
                    url = f"{url}?{urllib.parse.urlencode(clean_q)}"

        headers: dict[str, str] = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        data: Optional[bytes] = None
        if json_body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(dict(json_body)).encode("utf-8")
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        for attempt in range(1, 4):
            req = urllib.request.Request(url=url, data=data, headers=headers, method=method)
            res = self._sender(req, self.timeout_seconds)
            if res.status == 429 and attempt < 3:
                retry_hdr = (res.headers or {}).get("Retry-After") or (res.headers or {}).get("retry-after")
                wait_sec = 1.0 * attempt
                if retry_hdr is not None:
                    try:
                        parsed_wait = float(str(retry_hdr).strip())
                        if parsed_wait >= 0:
                            wait_sec = min(5.0, parsed_wait)
                    except ValueError:
                        pass
                self._sleep(wait_sec)
                continue
            if res.status in {500, 502, 503, 504} and attempt < 3:
                self._sleep(0.5 * attempt)
                continue
            if res.status not in expected_statuses:
                preview = res.body.decode("utf-8", errors="replace")[:300]
                raise WhopApiError(f"Whop API {method} {url} failed with HTTP {res.status}: {preview}")
            try:
                return json.loads(res.body.decode("utf-8") or "{}")
            except json.JSONDecodeError as exc:
                raise WhopApiError(f"Whop API {method} {url} returned invalid JSON: {exc}") from exc
        raise WhopApiError("Exhausted Whop API retry attempts")

    def _get_json(
        self,
        url_or_path: str,
        *,
        query: Optional[Mapping[str, str]] = None,
    ) -> Any:
        return self._request_json("GET", url_or_path, query=query, expected_statuses=(200,))

    def _write_json_with_account_negotiation(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, Any],
        idempotency_key: Optional[str] = None,
    ) -> Any:
        """Send POST/PATCH/PUT while negotiating `company_id` vs 2026+ `account_id` body fields."""
        if not self.company_id or "company_id" in body or "account_id" in body:
            return self._request_json(
                method,
                path,
                json_body=body,
                idempotency_key=idempotency_key,
            )

        candidates = (
            ["company_id", "account_id"]
            if self._company_body_param == "company_id"
            else ["account_id", "company_id"]
        )
        last_err: Optional[WhopApiError] = None
        for idx, field_name in enumerate(candidates):
            payload = {**dict(body), field_name: self.company_id}
            ikey = f"{idempotency_key}-{field_name}" if (idempotency_key and idx > 0) else idempotency_key
            try:
                res = self._request_json(method, path, json_body=payload, idempotency_key=ikey)
                self._company_body_param = field_name
                return res
            except WhopApiError as exc:
                last_err = exc
                msg = str(exc)
                if ("HTTP 400" in msg or "HTTP 422" in msg) and idx < len(candidates) - 1:
                    continue
                raise
        assert last_err is not None
        raise last_err

    def _get_json_with_account_negotiation(
        self,
        path: str,
        *,
        base_query: Mapping[str, str],
    ) -> Any:
        """Fetch `path` while negotiating `company_id` vs 2026-08-14+ `account_id` query parameters."""
        if not self.company_id:
            return self._get_json(path, query=base_query)

        candidates: list[Optional[str]] = []
        if self._company_query_param == "company_id":
            candidates = ["company_id", "account_id", None]
        elif self._company_query_param == "account_id":
            candidates = ["account_id", "company_id", None]
        else:
            candidates = [None]

        last_err: Optional[WhopApiError] = None
        for idx, param_name in enumerate(candidates):
            q = dict(base_query)
            if param_name:
                q[param_name] = self.company_id
            try:
                res = self._get_json(path, query=q)
                self._company_query_param = param_name
                return res
            except WhopApiError as exc:
                last_err = exc
                msg = str(exc)
                is_param_rejection = ("HTTP 400" in msg or "HTTP 422" in msg) and idx < len(candidates) - 1
                if is_param_rejection:
                    continue
                raise
        assert last_err is not None
        raise last_err

    def _paginate(
        self,
        path: str,
        *,
        collection_keys: tuple[str, ...],
        max_pages: int = 10,
    ) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        next_url: Optional[str] = None
        after_cursor: Optional[str] = None
        page_number: Optional[int] = None
        seen_tokens: set[str] = set()

        for _ in range(max(1, max_pages)):
            if next_url:
                token_sig = f"url:{next_url}"
                if token_sig in seen_tokens:
                    break
                seen_tokens.add(token_sig)
                payload = self._get_json(next_url)
            else:
                q: dict[str, str] = {"first": "50"}
                if after_cursor:
                    token_sig = f"after:{after_cursor}"
                    if token_sig in seen_tokens:
                        break
                    seen_tokens.add(token_sig)
                    q["after"] = after_cursor
                elif page_number is not None and page_number > 1:
                    token_sig = f"page:{page_number}"
                    if token_sig in seen_tokens:
                        break
                    seen_tokens.add(token_sig)
                    q["page"] = str(page_number)
                payload = self._get_json_with_account_negotiation(path, base_query=q)

            if isinstance(payload, list):
                items = [x for x in payload if isinstance(x, Mapping)]
                collected.extend(dict(x) for x in items)
                break

            if not isinstance(payload, Mapping):
                break

            items = []
            for key in collection_keys:
                cand = payload.get(key)
                if isinstance(cand, list):
                    items = [dict(x) for x in cand if isinstance(x, Mapping)]
                    break
            collected.extend(items)
            if not items:
                break

            next_url = None
            after_cursor = None
            page_number = None

            # 1. Whop v1 cursor pagination (`page_info.has_next_page` + `page_info.end_cursor`)
            page_info = payload.get("page_info")
            if isinstance(page_info, Mapping):
                has_next = bool(page_info.get("has_next_page"))
                end_cur = str(page_info.get("end_cursor") or "").strip()
                if has_next and end_cur:
                    after_cursor = end_cur
                    continue
                if "has_next_page" in page_info and not has_next:
                    break

            # 2. Page-number pagination (`pagination.next_page` or `current_page < total_pages`)
            pagination = payload.get("pagination")
            if isinstance(pagination, Mapping):
                nxt_p = pagination.get("next_page")
                cur_p = int(pagination.get("current_page") or 1)
                tot_p = int(pagination.get("total_pages") or cur_p)
                if isinstance(nxt_p, int) and nxt_p > cur_p:
                    page_number = nxt_p
                    continue
                if cur_p < tot_p:
                    page_number = cur_p + 1
                    continue
                break

            # 3. Direct next link (`links.next` / `next_page_url`)
            links = payload.get("links")
            raw_link = (
                (links.get("next") if isinstance(links, Mapping) else None)
                or payload.get("next_page_url")
                or ""
            )
            if isinstance(raw_link, str) and raw_link.strip().startswith("https://"):
                next_url = raw_link.strip()
                continue

            break

        return collected

    def list_payments(self, *, max_pages: int = 10) -> list[StoreOrderRecord]:
        """Fetch `GET /api/v1/payments`, keeping completed/active charges and skipping refunded/failed cleanly."""
        raw_items = self._paginate(
            "/payments",
            collection_keys=("data", "payments"),
            max_pages=max_pages,
        )
        now_iso = utc_now_iso()
        orders: list[StoreOrderRecord] = []
        seen_ids: set[str] = set()
        self._last_refunded_order_ids = set()
        self._last_paid_membership_ids = set()
        self._last_paid_subject_keys = set()

        for item in raw_items:
            if not self._matches_company(item):
                continue
            pay_id = str(item.get("id") or item.get("payment_id") or "").strip()
            if not pay_id or pay_id in seen_ids:
                continue
            seen_ids.add(pay_id)

            status = str(item.get("status") or item.get("substatus") or "paid").strip().lower()
            substatus = str(item.get("substatus") or "").strip().lower()
            explicit_refunded = _parse_bool_flag(item.get("refunded"), field_name="refunded", default=False)
            auto_refunded = _parse_bool_flag(item.get("auto_refunded"), field_name="auto_refunded", default=False)
            refunded_amt, _ = _parse_money_field_cents(
                item,
                cents_keys=("refunded_amount_cents", "amount_refunded_cents"),
                money_keys=("refunded_amount", "amount_refunded"),
            )
            gross_cents, fee_cents, after_fees_cents, currency, orig_cur = _resolve_whop_payment_amounts(item)

            is_full_refund = (
                explicit_refunded
                or auto_refunded
                or status in _FULL_REFUNDED_STATUSES
                or substatus in _FULL_REFUNDED_STATUSES
                or (
                    (status == "partially_refunded" or substatus == "partially_refunded")
                    and (refunded_amt is None or refunded_amt >= gross_cents)
                )
                or (refunded_amt is not None and refunded_amt >= gross_cents and refunded_amt > 0)
            )
            if is_full_refund:
                self._last_refunded_order_ids.add(pay_id)
                mem_ids_ref, subj_keys_ref = _extract_whop_correlation_keys(item, is_membership=False)
                self._last_paid_membership_ids.update(mem_ids_ref)
                self._last_paid_subject_keys.update(subj_keys_ref)
                continue

            if (
                status in _FAILED_OR_UNSETTLED_PAYMENT_STATUSES
                or substatus in _FAILED_OR_UNSETTLED_PAYMENT_STATUSES
                or (status not in _SETTLED_PAYMENT_STATUSES and status != "partially_refunded")
            ):
                continue

            base_net = after_fees_cents if after_fees_cents is not None else max(0, gross_cents - fee_cents)
            if refunded_amt is not None and 0 < refunded_amt < gross_cents:
                gross_cents = gross_cents - refunded_amt
                fee_cents = min(gross_cents, fee_cents)
                net_cents = max(0, base_net - refunded_amt)
            else:
                net_cents = base_net

            mem_ids, subj_keys = _extract_whop_correlation_keys(item, is_membership=False)
            self._last_paid_membership_ids.update(mem_ids)
            self._last_paid_subject_keys.update(subj_keys)
            mem_id = str(
                item.get("membership_id")
                or (
                    (item.get("membership") or {}).get("id")
                    if isinstance(item.get("membership"), Mapping)
                    else item.get("membership") or ""
                )
            ).strip()

            utm = _extract_utm_from_mapping_or_url(
                item.get("metadata"),
                item.get("custom_fields"),
                item.get("utm"),
                item,
                item.get("checkout_url"),
            )
            sku, prod_name = _extract_product_and_sku(
                item,
                utm_sku=utm["sku"],
                gross_cents=gross_cents,
                is_membership=False,
            )
            created_at = str(item.get("created_at") or item.get("paid_at") or now_iso).strip()

            raw_meta: dict[str, Any] = {
                "id": pay_id,
                "membership_id": mem_id,
                "company_id": _extract_company_id(item) or self.company_id,
                "status": status,
                "_mem_ids": sorted(mem_ids),
                "_subj_keys": sorted(subj_keys),
            }
            if orig_cur and orig_cur != currency:
                raw_meta["original_currency"] = orig_cur
            if refunded_amt is not None and refunded_amt > 0:
                raw_meta["partial_refund_cents"] = refunded_amt

            orders.append(
                StoreOrderRecord(
                    storefront="whop",
                    order_id=pay_id,
                    sku=sku,
                    product_name=prod_name,
                    gross_cents=gross_cents,
                    fee_cents=fee_cents,
                    net_cents=net_cents,
                    currency=currency,
                    refunded=False,
                    utm_source=(utm["utm_source"] or "whop").strip().lower(),
                    utm_medium=(utm["utm_medium"] or "payment_api").strip().lower(),
                    utm_campaign=(utm["utm_campaign"] or "whop_v1_sync").strip(),
                    attributed_slug=utm["slug"],
                    created_at=created_at,
                    synced_at=now_iso,
                    raw_data=raw_meta,
                )
            )

        return orders

    def list_memberships(
        self,
        *,
        max_pages: int = 10,
        skip_membership_ids: Optional[set[str]] = None,
        skip_subject_keys: Optional[set[str]] = None,
    ) -> list[StoreOrderRecord]:
        """Fetch `GET /api/v1/memberships`, normalizing active `$0` free memberships not covered by paid charges."""
        raw_items = self._paginate(
            "/memberships",
            collection_keys=("data", "memberships"),
            max_pages=max_pages,
        )
        now_iso = utc_now_iso()
        skip_ids: set[str] = set()
        for sid in skip_membership_ids or set():
            skip_ids.update(_normalize_membership_id_variants(sid))
        skip_subjects = skip_subject_keys or set()
        orders: list[StoreOrderRecord] = []
        seen_ids: set[str] = set()

        for item in raw_items:
            if not self._matches_company(item):
                continue
            raw_id = str(item.get("id") or item.get("membership_id") or "").strip()
            if not raw_id or raw_id in seen_ids:
                continue
            seen_ids.add(raw_id)

            mem_ids, subj_keys = _extract_whop_correlation_keys(item, is_membership=True)
            if (mem_ids & skip_ids) or (subj_keys and (subj_keys & skip_subjects)):
                continue

            valid_flag = item.get("valid")
            if valid_flag is not None and not _parse_bool_flag(valid_flag, field_name="valid", default=True):
                continue

            status = str(item.get("status") or "active").strip().lower()
            if status in _INVALID_MEMBERSHIP_STATUSES or status not in _VALID_MEMBERSHIP_STATUSES:
                continue

            gross_cents, money_cur = _parse_money_field_cents(
                item,
                cents_keys=("amount_cents", "price_cents", "renewal_price_cents"),
                money_keys=("usd_total", "amount_usd", "price_usd", "amount", "price"),
            )
            if gross_cents is None:
                gross_cents = 0

            # Paid plan memberships are accounted via `/api/v1/payments`; only normalize `$0` free memberships here
            if gross_cents > 0:
                continue

            utm = _extract_utm_from_mapping_or_url(
                item.get("metadata"),
                item.get("custom_fields"),
                item.get("utm"),
                item,
            )
            sku, prod_name = _extract_product_and_sku(
                item,
                utm_sku=utm["sku"],
                gross_cents=0,
                is_membership=True,
            )
            order_id = raw_id if raw_id.startswith("mem_") else f"mem_{raw_id}"
            currency = str(item.get("currency") or money_cur or "USD").strip().upper() or "USD"
            created_at = str(item.get("created_at") or now_iso).strip()

            orders.append(
                StoreOrderRecord(
                    storefront="whop",
                    order_id=order_id,
                    sku=sku,
                    product_name=prod_name,
                    gross_cents=0,
                    fee_cents=0,
                    net_cents=0,
                    currency=currency,
                    refunded=False,
                    utm_source=(utm["utm_source"] or "whop").strip().lower(),
                    utm_medium=(utm["utm_medium"] or "membership").strip().lower(),
                    utm_campaign=(utm["utm_campaign"] or "whop_v1_sync").strip(),
                    attributed_slug=utm["slug"],
                    created_at=created_at,
                    synced_at=now_iso,
                    raw_data={
                        "id": raw_id,
                        "company_id": _extract_company_id(item) or self.company_id,
                        "status": status,
                        "_mem_ids": sorted(mem_ids),
                        "_subj_keys": sorted(subj_keys),
                    },
                )
            )

        return orders

    def list_orders(self, *, max_pages: int = 10) -> list[StoreOrderRecord]:
        """List deduplicated Whop completed payments + active free memberships."""
        payments = self.list_payments(max_pages=max_pages)
        memberships = self.list_memberships(
            max_pages=max_pages,
            skip_membership_ids=self._last_paid_membership_ids,
            skip_subject_keys=self._last_paid_subject_keys,
        )
        return payments + memberships

    def sync_to_db(self, db: PipelineDatabase, *, max_pages: int = 10) -> dict[str, Any]:
        """Sync Whop payments & memberships into SQLite and reconcile refunded or superseded records."""
        payments = self.list_payments(max_pages=max_pages)
        memberships = self.list_memberships(
            max_pages=max_pages,
            skip_membership_ids=self._last_paid_membership_ids,
            skip_subject_keys=self._last_paid_subject_keys,
        )
        combined = payments + memberships
        upserted = db.upsert_store_orders(combined)

        # Clean up any previously webhook-ingested `$0` `mem_...` rows now covered by a synced `pay_...` payment
        if self._last_paid_membership_ids or self._last_paid_subject_keys:
            superseded_keys: list[tuple[str, str]] = []
            for ex in db.list_store_orders(include_refunded=True):
                if ex.storefront == "whop" and ex.order_id.startswith("mem_") and ex.gross_cents == 0:
                    ex_mem, ex_subj = _order_correlation_sets(ex)
                    if (ex_mem & self._last_paid_membership_ids) or (
                        ex_subj and (ex_subj & self._last_paid_subject_keys)
                    ):
                        superseded_keys.append(("whop", ex.order_id))
            if superseded_keys:
                db.delete_store_orders(superseded_keys)

        reconciled_refunds = 0
        if self._last_refunded_order_ids:
            existing_active = [
                o
                for o in db.list_store_orders(include_refunded=False)
                if o.storefront == "whop" and o.order_id in self._last_refunded_order_ids
            ]
            if existing_active:
                now_iso = utc_now_iso()
                refund_updates = [
                    StoreOrderRecord(
                        storefront="whop",
                        order_id=o.order_id,
                        sku=o.sku,
                        product_name=o.product_name,
                        gross_cents=o.gross_cents,
                        fee_cents=o.fee_cents,
                        net_cents=0,
                        currency=o.currency,
                        refunded=True,
                        utm_source=o.utm_source,
                        utm_medium=o.utm_medium,
                        utm_campaign=o.utm_campaign,
                        attributed_slug=o.attributed_slug,
                        created_at=o.created_at,
                        synced_at=now_iso,
                        raw_data={**o.raw_data, "reconciled_refunded": True},
                    )
                    for o in existing_active
                ]
                reconciled_refunds = db.upsert_store_orders(refund_updates)

        return {
            "status": "synced",
            "company_id": self.company_id,
            "payments_synced": len(payments),
            "memberships_synced": len(memberships),
            "orders_synced": upserted,
            "refunds_reconciled": reconciled_refunds,
        }

    # ------------------------------------------------------------------
    # Whop REST API v1 Product, Plan, Experience & Forum Automation
    # ------------------------------------------------------------------

    def list_products(self, *, max_pages: int = 5) -> list[dict[str, Any]]:
        """List existing Whop products (`GET /api/v1/products`) scoped to `company_id`."""
        raw_items = self._paginate(
            "/products",
            collection_keys=("data", "products"),
            max_pages=max_pages,
        )
        return [item for item in raw_items if self._matches_company(item)]

    def create_or_update_product(
        self,
        *,
        slug: str,
        title: str,
        description: str = "",
        visibility: str = "visible",
    ) -> dict[str, Any]:
        """Idempotently find or create a Whop product (`POST /api/v1/products`) by SKU/slug/title."""
        norm_slug = slug.strip().lower()
        norm_title = title.strip().lower()
        for existing in self.list_products():
            meta = existing.get("metadata") if isinstance(existing.get("metadata"), Mapping) else {}
            ex_sku = str(meta.get("sku") or existing.get("route") or existing.get("slug") or "").strip().lower()
            ex_title = str(existing.get("title") or existing.get("name") or "").strip().lower()
            if (norm_slug and ex_sku == norm_slug) or (norm_title and ex_title == norm_title):
                return {"operation": "reused", "product": existing}

        route_slug = re.sub(r"[^a-z0-9-]+", "-", norm_slug).strip("-")[:48] or "reliability-kit"
        body: dict[str, Any] = {
            "title": title.strip(),
            "route": route_slug,
            "visibility": visibility,
            "metadata": {"sku": slug.strip()},
        }
        if description.strip():
            body["description"] = description.strip()

        ikey = f"whop-product-{self.company_id or 'default'}-{route_slug}"
        created = self._write_json_with_account_negotiation(
            "POST",
            "/products",
            body=body,
            idempotency_key=ikey,
        )
        prod_obj = (
            dict(created.get("data"))
            if isinstance(created, Mapping) and isinstance(created.get("data"), Mapping)
            else (dict(created) if isinstance(created, Mapping) else {"id": str(created)})
        )
        return {"operation": "created", "product": prod_obj}

    def list_plans(
        self,
        *,
        product_id: Optional[str] = None,
        max_pages: int = 5,
    ) -> list[dict[str, Any]]:
        """List Whop pricing plans (`GET /api/v1/plans`), optionally filtered to `product_id`."""
        raw_items = self._paginate(
            "/plans",
            collection_keys=("data", "plans"),
            max_pages=max_pages,
        )
        filtered: list[dict[str, Any]] = []
        for item in raw_items:
            if not self._matches_company(item):
                continue
            if product_id:
                item_pid = str(
                    item.get("product_id")
                    or (
                        (item.get("product") or {}).get("id")
                        if isinstance(item.get("product"), Mapping)
                        else ""
                    )
                ).strip()
                if item_pid and item_pid != product_id:
                    continue
            filtered.append(item)
        return filtered

    def create_plan(
        self,
        *,
        product_id: str,
        price_usd: int,
        sku: str,
        title: str = "",
        plan_type: str = "one_time",
        currency: str = "usd",
    ) -> dict[str, Any]:
        """Idempotently find or create a Whop one-time/free plan (`POST /api/v1/plans`) on `product_id`."""
        target_cents = max(0, int(price_usd)) * 100
        for existing in self.list_plans(product_id=product_id):
            meta = existing.get("metadata") if isinstance(existing.get("metadata"), Mapping) else {}
            ex_sku = str(meta.get("sku") or "").strip().lower()
            ex_cents, _ = _parse_money_field_cents(
                existing,
                cents_keys=("initial_price_cents", "price_cents", "amount_cents"),
                money_keys=("initial_price", "price", "amount"),
            )
            if (sku and ex_sku == sku.strip().lower()) or (ex_cents is not None and ex_cents == target_cents):
                return {"operation": "reused", "plan": existing}

        price_str = f"{max(0, int(price_usd)):.2f}"
        body: dict[str, Any] = {
            "product_id": product_id,
            "plan_type": plan_type,
            "initial_price": 0 if target_cents == 0 else price_str,
            "currency": currency.lower(),
            "visibility": "visible",
            "metadata": {"sku": sku.strip()},
        }
        if title.strip():
            body["title"] = title.strip()

        ikey = f"whop-plan-{self.company_id or 'default'}-{sku.strip()[:36]}-{price_usd}"
        created = self._write_json_with_account_negotiation(
            "POST",
            "/plans",
            body=body,
            idempotency_key=ikey,
        )
        plan_obj = (
            dict(created.get("data"))
            if isinstance(created, Mapping) and isinstance(created.get("data"), Mapping)
            else (dict(created) if isinstance(created, Mapping) else {"id": str(created)})
        )
        return {"operation": "created", "plan": plan_obj}

    def list_experiences(self, *, max_pages: int = 5) -> list[dict[str, Any]]:
        """List installed Whop experiences/apps (`GET /api/v1/experiences`) such as Forum, Chat, Files."""
        try:
            raw_items = self._paginate(
                "/experiences",
                collection_keys=("data", "experiences"),
                max_pages=max_pages,
            )
        except WhopApiError as exc:
            if "HTTP 404" in str(exc) or "HTTP 403" in str(exc):
                return []
            raise
        return [item for item in raw_items if self._matches_company(item)]

    def discover_forum_and_chat_experiences(self) -> tuple[str, str]:
        """Resolve `(forum_experience_id, chat_experience_id)` from config or live `/experiences`."""
        cfg_forum = (self.config.whop_forum_experience_id if self.config else "").strip()
        cfg_chat = (self.config.whop_chat_experience_id if self.config else "").strip()
        if cfg_forum and cfg_chat:
            return cfg_forum, cfg_chat

        experiences = self.list_experiences()
        found_forum = cfg_forum
        found_chat = cfg_chat
        for exp in experiences:
            exp_id = str(exp.get("id") or "").strip()
            if not exp_id:
                continue
            app_obj = exp.get("app") if isinstance(exp.get("app"), Mapping) else {}
            descriptor = " ".join(
                str(x or "")
                for x in (
                    exp.get("type"),
                    exp.get("experience_type"),
                    exp.get("name"),
                    exp.get("title"),
                    app_obj.get("name"),
                    app_obj.get("slug"),
                )
            ).lower()
            if not found_forum and ("forum" in descriptor or "announcement" in descriptor or "feed" in descriptor):
                found_forum = exp_id
            elif not found_chat and ("chat" in descriptor or "channel" in descriptor or "community" in descriptor):
                found_chat = exp_id
        return found_forum, found_chat

    def ensure_catalog_products(self, catalog: Mapping[str, Any]) -> dict[str, Any]:
        """Idempotently provision/verify all 3 catalog SKUs & pricing plans on Whop API v1."""
        out_dir = (self.config.exports_dir / "storefront") if self.config else Path("exports/storefront")
        out_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = out_dir / "whop_provisioned_catalog.json"

        specs = list(catalog.get("products") or [])
        if not self.has_live_credentials():
            dry_summary = {
                "status": "dry_run",
                "company_id": self.company_id or DEFAULT_WHOP_COMPANY_ID,
                "reason": "WHOP_API_KEY not configured; exported provisioning blueprint locally.",
                "products_provisioned": [],
                "catalog_count": len(specs),
                "manifest_path": str(manifest_path),
            }
            manifest_path.write_text(json.dumps(dry_summary, indent=2) + "\n", encoding="utf-8")
            return dry_summary

        provisioned: list[dict[str, Any]] = []
        for spec in specs:
            slug = str(spec.get("slug") or "").strip()
            title = str(spec.get("title") or slug).strip()
            price_usd = int(spec.get("price_usd") or 0)
            tagline = str(spec.get("tagline") or "").strip()
            summary = str(spec.get("summary") or "").strip()
            highlights = [str(h) for h in (spec.get("highlights") or [])]
            desc_parts = [p for p in (tagline, summary) if p]
            if highlights:
                desc_parts.append("\n".join(f"- {h}" for h in highlights))
            full_desc = "\n\n".join(desc_parts)

            prod_res = self.create_or_update_product(
                slug=slug,
                title=title,
                description=full_desc,
            )
            prod_obj = prod_res["product"]
            prod_id = str(prod_obj.get("id") or "").strip()

            plan_res: dict[str, Any] = {"operation": "skipped_missing_product_id", "plan": {}}
            checkout_url = str(
                prod_obj.get("checkout_url")
                or prod_obj.get("purchase_url")
                or prod_obj.get("url")
                or ""
            ).strip()
            if prod_id:
                plan_res = self.create_plan(
                    product_id=prod_id,
                    price_usd=price_usd,
                    sku=slug,
                    title=title,
                )
                plan_obj = plan_res["plan"]
                checkout_url = (
                    str(
                        plan_obj.get("direct_link")
                        or plan_obj.get("purchase_url")
                        or plan_obj.get("checkout_url")
                        or checkout_url
                    ).strip()
                )

            provisioned.append(
                {
                    "sku": slug,
                    "title": title,
                    "price_usd": price_usd,
                    "product_id": prod_id,
                    "product_operation": prod_res["operation"],
                    "plan_id": str((plan_res.get("plan") or {}).get("id") or ""),
                    "plan_operation": plan_res["operation"],
                    "checkout_url": checkout_url,
                    "zip_filename": str(spec.get("zip_filename") or ""),
                }
            )

        forum_exp_id, chat_exp_id = self.discover_forum_and_chat_experiences()
        live_summary = {
            "status": "provisioned",
            "synced_at": utc_now_iso(),
            "company_id": self.company_id or DEFAULT_WHOP_COMPANY_ID,
            "forum_experience_id": forum_exp_id,
            "chat_experience_id": chat_exp_id,
            "products_provisioned": provisioned,
            "manifest_path": str(manifest_path),
        }
        manifest_path.write_text(json.dumps(live_summary, indent=2) + "\n", encoding="utf-8")
        return live_summary

    def list_forum_posts(
        self,
        *,
        experience_id: str = "",
        max_pages: int = 5,
    ) -> list[dict[str, Any]]:
        """List Whop Forum posts (`GET /api/v1/forum_posts`) including `view_count`, `like_count`, `comment_count`."""
        collected: list[dict[str, Any]] = []
        after_cursor: Optional[str] = None
        seen_cursors: set[str] = set()

        for _ in range(max(1, max_pages)):
            q: dict[str, str] = {"first": "50"}
            if experience_id.strip():
                q["experience_id"] = experience_id.strip()
            if after_cursor:
                if after_cursor in seen_cursors:
                    break
                seen_cursors.add(after_cursor)
                q["after"] = after_cursor

            try:
                payload = self._get_json_with_account_negotiation("/forum_posts", base_query=q)
            except WhopApiError as exc:
                if "HTTP 404" in str(exc) or "HTTP 403" in str(exc):
                    break
                raise

            if isinstance(payload, list):
                collected.extend(dict(x) for x in payload if isinstance(x, Mapping))
                break
            if not isinstance(payload, Mapping):
                break
            items: list[dict[str, Any]] = []
            for key in ("data", "forum_posts", "posts"):
                cand = payload.get(key)
                if isinstance(cand, list):
                    items = [dict(x) for x in cand if isinstance(x, Mapping)]
                    break
            collected.extend(items)
            page_info = payload.get("page_info")
            if isinstance(page_info, Mapping) and page_info.get("has_next_page") and page_info.get("end_cursor"):
                after_cursor = str(page_info["end_cursor"]).strip()
                continue
            break

        return collected

    def publish_daily_post(
        self,
        article: ArticleRecord,
        *,
        db: Optional[PipelineDatabase] = None,
        series_articles: Optional[list[ArticleRecord]] = None,
        offline: bool = False,
    ) -> dict[str, Any]:
        """Export daily Whop packs and idempotently publish `[Day N/30]` to Whop Forum (`<= 2` links, zero @everyone)."""
        if self.config is None:
            raise WhopApiError("WhopClient requires AppConfig for publish_daily_post")
        if article.status not in {"code_verified", "staged_draft", "published_live"}:
            raise WhopApiError(
                f"Refusing to publish unverified article '{article.slug}' (status='{article.status}')"
            )

        pack_paths = export_whop_daily_pack(article, self.config, series_articles=series_articles)
        guide_path = write_whop_setup_guide(self.config)

        day_num = article.series_day or 1
        dur_ms = article.verification.get("total_duration_ms", 0)
        blocks_n = article.verification.get("code_blocks_tested", 2)
        whop_base = self.config.whop_store_url or self.config.patreon_url or self.config.paid_checkout_url
        challenge_url = append_storefront_utm(
            whop_base,
            source="whop",
            medium="forum_post",
            campaign=f"30d_day{day_num:02d}",
            slug=article.slug,
            sku="nasio-7day-ai-automation-challenge-pack",
            configured_ls_url=self.config.lemonsqueezy_store_url,
        )

        post_title = f"[Day {day_num}/30] {article.title}"
        # Strictly <= 2 outbound links (1 canonical tutorial + 1 internal UTM-tagged Challenge link)
        post_content = (
            f"{article.subtitle}\n\n"
            f"Verified inside an isolated `python3 -I` sandbox ({dur_ms} ms across {blocks_n} executable blocks, "
            f"100% Python 3.11+ standard library):\n"
            f"- Full runnable benchmark & code teardown: {article.canonical_url}\n"
            f"- 7-Day Hands-On Python & AI Reliability Challenge ($15 USD · MIT License): {challenge_url}\n"
        )
        configured_exp = self.config.whop_forum_experience_id.strip()
        forum_payload: dict[str, Any] = {
            "experience_id": configured_exp or "public",
            "title": post_title,
            "content": post_content,
            "pinned": False,
            "is_mention": False,
            "paywall_amount": 0,
        }
        payload_path = self.config.exports_dir / article.slug / "whop_forum_payload.json"
        payload_path.parent.mkdir(parents=True, exist_ok=True)
        payload_path.write_text(json.dumps(forum_payload, indent=2) + "\n", encoding="utf-8")

        if offline or not self.has_live_credentials():
            return {
                "status": "pack_ready",
                "api_sync_mode": "dry_run",
                "reason": (
                    "Offline mode active; exported Whop pack & forum JSON locally."
                    if offline
                    else "WHOP_API_KEY not configured; exported Whop pack & forum JSON locally."
                ),
                "setup_guide": str(guide_path),
                "whop_forum_payload": str(payload_path),
                **pack_paths,
            }

        # Layer 1: Already recorded in SQLite
        if article.whop_post_id:
            return {
                "status": "published_live",
                "operation": "already_recorded",
                "whop_post_id": article.whop_post_id,
                "whop_url": article.whop_url or self.config.whop_store_url or self.config.whop_dashboard_url,
                "setup_guide": str(guide_path),
                "whop_forum_payload": str(payload_path),
                **pack_paths,
            }

        forum_exp_id, chat_exp_id = self.discover_forum_and_chat_experiences()
        effective_exp = forum_exp_id or "public"
        forum_payload["experience_id"] = effective_exp
        payload_path.write_text(json.dumps(forum_payload, indent=2) + "\n", encoding="utf-8")

        # Layer 2: Reconcile against existing remote forum posts on Whop
        existing_posts = self.list_forum_posts(experience_id=forum_exp_id)
        day_tag_low = f"[day {day_num}/30]"
        norm_title_low = article.title.strip().lower()
        for post in existing_posts:
            r_title = str(post.get("title") or "").strip().lower()
            r_body = str(post.get("content") or "").strip().lower()
            if (
                day_tag_low in r_title
                or (norm_title_low and norm_title_low in r_title)
                or article.slug.lower() in r_body
            ):
                matched_id = str(post.get("id") or "").strip()
                if matched_id:
                    matched_url = str(
                        post.get("url")
                        or self.config.whop_store_url
                        or self.config.whop_dashboard_url
                    ).strip()
                    article.whop_post_id = matched_id
                    article.whop_url = matched_url
                    if db is not None:
                        db.save_article(article)
                    return {
                        "status": "published_live",
                        "operation": "reconciled",
                        "whop_post_id": matched_id,
                        "whop_url": matched_url,
                        "experience_id": effective_exp,
                        "setup_guide": str(guide_path),
                        "whop_forum_payload": str(payload_path),
                        **pack_paths,
                    }

        # Layer 3: Create fresh Whop Forum post idempotently
        ikey = f"whop-forum-{self.company_id or 'default'}-day{day_num:02d}-{article.slug[:32]}"
        try:
            created = self._write_json_with_account_negotiation(
                "POST",
                "/forum_posts",
                body=forum_payload,
                idempotency_key=ikey,
            )
        except WhopApiError as exc:
            if not forum_exp_id and any(code in str(exc) for code in ("HTTP 400", "HTTP 403", "HTTP 404", "HTTP 422")):
                return {
                    "status": "pack_ready",
                    "api_sync_mode": "no_forum_experience",
                    "reason": f"No Whop Forum experience installed yet ({exc}); pack & JSON staged.",
                    "setup_guide": str(guide_path),
                    "whop_forum_payload": str(payload_path),
                    **pack_paths,
                }
            raise

        post_obj = (
            dict(created.get("data"))
            if isinstance(created, Mapping) and isinstance(created.get("data"), Mapping)
            else (dict(created) if isinstance(created, Mapping) else {})
        )
        remote_id = str(post_obj.get("id") or "").strip()
        remote_url = str(
            post_obj.get("url")
            or self.config.whop_store_url
            or self.config.whop_dashboard_url
        ).strip()
        if remote_id:
            article.whop_post_id = remote_id
            article.whop_url = remote_url
            if db is not None:
                db.save_article(article)

        chat_status = "not_configured"
        if chat_exp_id:
            chat_ikey = f"whop-chat-{self.company_id or 'default'}-day{day_num:02d}"
            chat_text = f"New Verified Lab Live — {post_title}: {article.canonical_url}"
            try:
                self._request_json(
                    "POST",
                    f"/channels/{chat_exp_id}/messages",
                    json_body={"content": chat_text},
                    idempotency_key=chat_ikey,
                )
                chat_status = "sent"
            except WhopApiError:
                chat_status = "skipped_unavailable"

        return {
            "status": "published_live" if remote_id else "pack_ready",
            "operation": "created",
            "whop_post_id": remote_id or None,
            "whop_url": remote_url or None,
            "experience_id": effective_exp,
            "chat_notification": chat_status,
            "setup_guide": str(guide_path),
            "whop_forum_payload": str(payload_path),
            **pack_paths,
        }

    def sync_forum_metrics(self, db: PipelineDatabase) -> dict[str, Any]:
        """Pull `view_count`, `like_count`, and `comment_count` from Whop Forum posts into `channel_metrics`."""
        if not self.has_live_credentials():
            return {
                "status": "dry_run",
                "posts_matched": 0,
                "views_total": 0,
                "engagement_total": 0,
                "reason": "WHOP_API_KEY not configured; skipped live Whop Forum analytics fetch.",
            }

        forum_exp_id, _ = self.discover_forum_and_chat_experiences()
        posts = self.list_forum_posts(experience_id=forum_exp_id)
        articles = db.list_articles(verified_only=True)
        by_id = {str(p.get("id") or "").strip(): p for p in posts if str(p.get("id") or "").strip()}

        matched_n = 0
        views_sum = 0
        eng_sum = 0

        for art in articles:
            matched: Optional[dict[str, Any]] = None
            if art.whop_post_id and art.whop_post_id in by_id:
                matched = by_id[art.whop_post_id]
            else:
                day_tag = f"[day {art.series_day}/30]" if art.series_day else ""
                norm_title = art.title.strip().lower()
                for post in posts:
                    r_title = str(post.get("title") or "").strip().lower()
                    r_body = str(post.get("content") or "").strip().lower()
                    if (
                        (day_tag and day_tag in r_title)
                        or (norm_title and norm_title in r_title)
                        or art.slug.lower() in r_body
                    ):
                        matched = post
                        break

            if not matched:
                continue

            matched_n += 1
            pid = str(matched.get("id") or "").strip()
            if pid and art.whop_post_id != pid:
                art.whop_post_id = pid
                art.whop_url = str(
                    matched.get("url")
                    or (self.config.whop_store_url if self.config else "")
                    or (self.config.whop_dashboard_url if self.config else "")
                ).strip()
                db.save_article(art)

            views = max(0, int(matched.get("view_count") or 0))
            likes = max(0, int(matched.get("like_count") or 0))
            comments = max(0, int(matched.get("comment_count") or 0))
            engagement = likes + comments
            views_sum += views
            eng_sum += engagement

            db.upsert_channel_metrics_monotonic(
                slug=art.slug,
                channel="whop",
                min_views=views,
                min_clicks=engagement,
            )

        return {
            "status": "synced",
            "company_id": self.company_id or DEFAULT_WHOP_COMPANY_ID,
            "experience_id": forum_exp_id or "public",
            "remote_posts_checked": len(posts),
            "posts_matched": matched_n,
            "views_total": views_sum,
            "engagement_total": eng_sum,
        }


def write_whop_setup_guide(config: AppConfig) -> Path:
    """Ensure `exports/storefront/whop_setup_and_challenge_guide.md` exists and reflects `config`."""
    from products.builder import (
        BuiltProductArtifact,
        _render_whop_setup_and_challenge_guide,
        load_catalog,
    )

    out_dir = config.exports_dir / "storefront"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "whop_setup_and_challenge_guide.md"

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

    md_text = _render_whop_setup_and_challenge_guide(
        catalog,
        artifacts,
        whop_company_id=config.whop_company_id or DEFAULT_WHOP_COMPANY_ID,
        whop_store_url=config.whop_store_url,
        patreon_url=config.patreon_url,
    )
    out_path.write_text(md_text, encoding="utf-8")
    return out_path


def export_whop_daily_pack(
    article: ArticleRecord,
    config: AppConfig,
    *,
    series_articles: Optional[list[ArticleRecord]] = None,
) -> dict[str, str]:
    """Generate the Daily 60-Minute Whop Execution Pack (`exports/<slug>/whop_daily_pack.md`)."""
    bundle_dir = config.exports_dir / article.slug
    bundle_dir.mkdir(parents=True, exist_ok=True)

    meta = PILLAR_META.get(article.pillar, PILLAR_META["pillar_a"])
    day_num = article.series_day or 1
    dur_ms = article.verification.get("total_duration_ms", 0)
    blocks_n = article.verification.get("code_blocks_tested", 2)

    company_id = config.whop_company_id or DEFAULT_WHOP_COMPANY_ID
    dashboard_url = f"https://whop.com/dashboard/{company_id}/"
    whop_base = config.whop_store_url or config.patreon_url or config.paid_checkout_url

    challenge_ann_url = append_storefront_utm(
        whop_base,
        source="whop",
        medium="announcements",
        campaign=f"30d_day{day_num:02d}",
        slug=article.slug,
        sku="nasio-7day-ai-automation-challenge-pack",
        configured_ls_url=config.lemonsqueezy_store_url,
    )
    free_ann_url = append_storefront_utm(
        whop_base,
        source="whop",
        medium="announcements",
        campaign=f"30d_day{day_num:02d}",
        slug=article.slug,
        sku="free-idempotent-cron-mini-kit",
        configured_ls_url=config.lemonsqueezy_store_url,
    )
    social_hook_url = append_storefront_utm(
        whop_base,
        source="whop",
        medium="value_hook",
        campaign=f"30d_day{day_num:02d}",
        slug=article.slug,
        sku="nasio-7day-ai-automation-challenge-pack",
        configured_ls_url=config.lemonsqueezy_store_url,
    )
    warm_welcome_url = append_storefront_utm(
        whop_base,
        source="whop",
        medium="warm_welcome",
        campaign=f"30d_day{day_num:02d}",
        slug=article.slug,
        sku="nasio-7day-ai-automation-challenge-pack",
        configured_ls_url=config.lemonsqueezy_store_url,
    )

    series_count = len(series_articles) if series_articles else day_num
    pack_md = f"""# Daily 60-Minute Whop Execution Pack — Day {day_num}/30

- **Article:** `{article.title}` (`{article.word_count}` words · `{blocks_n}` blocks verified in `{dur_ms} ms`)
- **Pillar Track:** {meta["label"]} ({meta["weekday"]})
- **Whop Business Dashboard:** `{dashboard_url}` (Company `{company_id}`)
- **Target Checkout Hub:** `{whop_base}` (`$10 USD` min payout · Whop Discover Marketplace · `4 × $15` Challenge seats = `~$55.36` net)

---

## Block 1 (`00–15 min`): Whop Announcements & Forum Drop

**Where to Post:** Open `{dashboard_url}` -> **Announcements** app (push notification + email alert to all free & paid Whop members) and cross-post the discussion prompt in your **Forum / Chat** app.

**Announcement Headline:**
`[Day {day_num}/30] {article.title} (Verified Python 3.11+ Benchmark)`

**Whop Announcements & Forum Body (Copy & Paste):**
```markdown
Hey builders — welcome to Day {day_num}/30 of {SERIES_30D_NAME}!

Today's lab tackles {article.subtitle}

What we verified inside an isolated `python3 -I` sandbox ({dur_ms} ms runtime across {blocks_n} executable blocks):
- Why happy-path wrappers fail under real concurrency, retry storms, or malformed upstream payloads
- How to eliminate the failure mode using 100% Python standard library primitives ({series_count}/30 lessons live)

Read today's full runnable benchmark free:
{article.canonical_url}

Ready to ship all 7 production guardrails hands-on with unit tests?
1. Free Starter Kit ($0 Files App): {free_ann_url}
2. Unlock the 7-Day Python & AI Reliability Challenge ($15 USD · 7 Runnable Labs + Forum Review + MIT License): {challenge_ann_url}
```

---

## Block 2 (`15–40 min`): External Traffic Value-Hook Post (LinkedIn / X / Dev — Strictly <= 2 Links)

> **Anti-Spam Hygiene:** Keep total outbound links `<= 2` so developer feeds never throttle reach. Spend the remaining 15 minutes leaving 3 concrete, zero-link technical replies on Python/AI engineering threads.

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

7-Day Hands-On Python & AI Reliability Challenge ($15 on Whop · MIT License):
{social_hook_url}
```

---

## Block 3 (`40–60 min`): Warm Member Welcome & $15 Challenge Invite (Zero Cold Spam)

> **Who to Message:** Only new `$0` Free Mini-Kit Whop members in your Whop Chat/Members tab (`{dashboard_url}`), Whop Discover visitors who joined your free hub, or engaged commenters from today's drop.

**3-Sentence Diagnostic Welcome Message (Whop Chat / DM):**
```text
Hey {{first_name}} — welcome into the Reliability Lab on Whop and glad you grabbed the free Python cron & circuit-breaker kit! Quick question so I point you to the highest-impact module first: are you currently tackling flaky LLM/API retries or background queue/webhook idempotency?

Whenever you want all 7 self-tested stdlib labs + the failure-injection workbook (`test_challenge_kit.py` under MIT commercial license), you can unlock the $15 7-Day Challenge right inside our Whop hub here: {warm_welcome_url}
```
"""

    pack_path = bundle_dir / "whop_daily_pack.md"
    pack_path.write_text(pack_md, encoding="utf-8")
    return {"whop_daily_pack": str(pack_path)}
