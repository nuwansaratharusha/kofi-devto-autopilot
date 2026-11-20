"""Dual Merchant-of-Record Storefront Clients: Gumroad v2 & Lemon Squeezy JSON:API v1."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from engine.config import AppConfig
from engine.db import PipelineDatabase, utc_now_iso
from engine.devto_client import HttpResult, _urllib_sender
from engine.models import StoreOrderRecord

GUMROAD_API_BASE = "https://api.gumroad.com/v2"
LEMONSQUEEZY_API_BASE = "https://api.lemonsqueezy.com/v1"
USER_AGENT = "KofiDevtoAutopilot/2.0 (+https://ko-fi.com/tharushanuwansara)"


class StorefrontApiError(RuntimeError):
    """Raised when Gumroad v2 or Lemon Squeezy v1 returns an unrecoverable error."""


def is_lemonsqueezy_url(url: str, configured_ls_url: str = "") -> bool:
    """Return True if `url` targets `*.lemonsqueezy.com` or the configured Lemon Squeezy store host."""
    host = urllib.parse.urlparse(url).netloc.strip().lower()
    if not host:
        return False
    if host == "lemonsqueezy.com" or host.endswith(".lemonsqueezy.com"):
        return True
    if configured_ls_url:
        cfg_host = urllib.parse.urlparse(configured_ls_url).netloc.strip().lower()
        if cfg_host and host == cfg_host:
            return True
    return False


def append_storefront_utm(
    url: str,
    *,
    source: str,
    medium: str,
    campaign: str,
    slug: str = "",
    sku: str = "",
    configured_ls_url: str = "",
) -> str:
    """Append standard `utm_*` params plus `checkout[custom][*]` when targeting Lemon Squeezy."""
    parsed = urllib.parse.urlparse(url)
    qs = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    qs["utm_source"] = source
    qs["utm_medium"] = medium
    qs["utm_campaign"] = campaign
    if slug:
        qs["slug"] = slug
    elif "slug" in qs:
        slug = qs["slug"]
    if sku:
        qs["sku"] = sku
    elif "sku" in qs:
        sku = qs["sku"]

    # Remove stale Lemon Squeezy custom keys before re-populating if applicable
    for stale_k in [k for k in qs if k.startswith("checkout[custom][")]:
        del qs[stale_k]

    if is_lemonsqueezy_url(url, configured_ls_url):
        qs["checkout[custom][utm_source]"] = source
        qs["checkout[custom][utm_medium]"] = medium
        qs["checkout[custom][utm_campaign]"] = campaign
        if slug:
            qs["checkout[custom][slug]"] = slug[:120]
        if sku:
            qs["checkout[custom][sku]"] = sku[:80]

    new_query = urllib.parse.urlencode(qs)
    return urllib.parse.urlunparse(parsed._replace(query=new_query))


def _normalize_utm_key(raw_key: str) -> str:
    low = raw_key.strip().lower()
    if low.startswith("checkout[custom][") and low.endswith("]"):
        return low[len("checkout[custom][") : -1].strip()
    return low


def _extract_utm_from_mapping_or_url(
    *sources: Any,
) -> dict[str, str]:
    """Extract `utm_source`, `utm_medium`, `utm_campaign`, `sku`, `slug` across nested dicts/URLs."""
    found: dict[str, str] = {
        "utm_source": "",
        "utm_medium": "",
        "utm_campaign": "",
        "sku": "",
        "slug": "",
    }
    target_keys = set(found.keys())
    nested_containers = {"custom", "custom_data", "checkout_data", "url_params", "custom_fields"}

    def _visit(src: Any, depth: int = 0) -> None:
        if not src or depth > 3:
            return
        if isinstance(src, Mapping):
            for k, v in src.items():
                norm_k = _normalize_utm_key(str(k))
                if norm_k in target_keys and v is not None and not isinstance(v, (Mapping, list)) and not found[norm_k]:
                    val_str = str(v).strip()
                    if val_str:
                        found[norm_k] = val_str
                elif norm_k in nested_containers and isinstance(v, Mapping):
                    _visit(v, depth + 1)
        elif isinstance(src, str) and ("?" in src or "=" in src):
            parsed = urllib.parse.urlparse(src)
            qs = urllib.parse.parse_qs(parsed.query if "?" in src else src)
            for raw_k, vals in qs.items():
                norm_k = _normalize_utm_key(raw_k)
                if norm_k in target_keys and vals and not found[norm_k]:
                    val_str = str(vals[0]).strip()
                    if val_str:
                        found[norm_k] = val_str

    for root_src in sources:
        _visit(root_src, 0)
    return found


class GumroadClient:
    """Pure-stdlib Gumroad REST API v2 client (`Authorization: Bearer {token}`)."""

    def __init__(
        self,
        access_token: str,
        *,
        sender: Callable[[urllib.request.Request, float], HttpResult] = _urllib_sender,
        sleeper: Callable[[float], None] = time.sleep,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.access_token = access_token.strip()
        self._sender = sender
        self._sleep = sleeper
        self.timeout_seconds = timeout_seconds

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: Optional[Mapping[str, str]] = None,
        json_body: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        if not self.access_token:
            raise StorefrontApiError("GUMROAD_ACCESS_TOKEN is empty")
        url = f"{GUMROAD_API_BASE}{path}"
        if query:
            clean_q = {k: v for k, v in query.items() if v is not None and v != ""}
            if clean_q:
                url = f"{url}?{urllib.parse.urlencode(clean_q)}"

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        data: Optional[bytes] = None
        if json_body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(json_body).encode("utf-8")

        for attempt in range(1, 4):
            req = urllib.request.Request(url=url, data=data, headers=headers, method=method)
            res = self._sender(req, self.timeout_seconds)
            if res.status == 429 and attempt < 3:
                self._sleep(1.0 * attempt)
                continue
            if res.status in {500, 502, 503, 504} and attempt < 3:
                self._sleep(0.5 * attempt)
                continue
            if res.status not in {200, 201}:
                preview = res.body.decode("utf-8", errors="replace")[:300]
                raise StorefrontApiError(
                    f"Gumroad API {method} {url} failed with HTTP {res.status}: {preview}"
                )
            parsed = json.loads(res.body.decode("utf-8") or "{}")
            if isinstance(parsed, dict) and parsed.get("success") is False:
                raise StorefrontApiError(
                    f"Gumroad API returned success=false: {parsed.get('message', 'unknown error')}"
                )
            return parsed
        raise StorefrontApiError("Exhausted Gumroad API retry attempts")

    def list_products(self) -> list[dict[str, Any]]:
        payload = self._request("GET", "/products")
        return list(payload.get("products", []))

    def create_draft_product(
        self,
        *,
        name: str,
        price_cents: int,
        description: str,
        summary: str = "",
        tags: Optional[list[str]] = None,
        custom_permalink: str = "",
    ) -> dict[str, Any]:
        """Create an unpublished (`published=false`) draft digital product on Gumroad v2."""
        body: dict[str, Any] = {
            "name": name,
            "price": max(0, int(price_cents)),
            "description": description,
            "published": False,
        }
        if summary:
            body["summary"] = summary
        if tags:
            body["tags"] = ",".join(tags[:5])
        if custom_permalink:
            body["custom_permalink"] = custom_permalink
        payload = self._request("POST", "/products", json_body=body)
        return dict(payload.get("product") or payload)

    def list_sales(
        self,
        *,
        after: Optional[str] = None,
        before: Optional[str] = None,
        max_pages: int = 5,
    ) -> list[StoreOrderRecord]:
        """Fetch Gumroad `/v2/sales`, extracting UTM attribution and zeroing refunded net revenue."""
        orders: list[StoreOrderRecord] = []
        now_iso = utc_now_iso()
        page_key: Optional[str] = None

        for _ in range(max_pages):
            q: dict[str, str] = {}
            if after:
                q["after"] = after
            if before:
                q["before"] = before
            if page_key:
                q["page_key"] = page_key
            payload = self._request("GET", "/sales", query=q)
            for sale in payload.get("sales", []):
                order_id = str(sale.get("id") or sale.get("order_id") or "").strip()
                if not order_id:
                    continue
                gross_cents = int(sale.get("price") or 0)
                fee_cents = int(sale.get("gumroad_fee") or 0)
                refunded = bool(
                    sale.get("refunded")
                    or sale.get("partially_refunded")
                    or sale.get("chargedback")
                    or sale.get("disputed")
                )
                net_cents = 0 if refunded else max(0, gross_cents - fee_cents)

                utm = _extract_utm_from_mapping_or_url(
                    sale.get("url_params"),
                    sale.get("custom_fields"),
                    sale,
                    sale.get("referrer"),
                )
                sku_guess = (
                    utm["sku"]
                    or str(sale.get("product_permalink") or sale.get("short_product_id") or "")
                )
                orders.append(
                    StoreOrderRecord(
                        storefront="gumroad",
                        order_id=order_id,
                        sku=sku_guess,
                        product_name=str(sale.get("product_name") or ""),
                        gross_cents=gross_cents,
                        fee_cents=fee_cents,
                        net_cents=net_cents,
                        currency=str(sale.get("currency_symbol") if len(str(sale.get("currency_symbol") or "")) == 3 else (sale.get("currency") or "USD")).upper(),
                        refunded=refunded,
                        utm_source=utm["utm_source"],
                        utm_medium=utm["utm_medium"],
                        utm_campaign=utm["utm_campaign"],
                        attributed_slug=utm["slug"],
                        created_at=str(sale.get("created_at") or now_iso),
                        synced_at=now_iso,
                        raw_data={
                            "id": order_id,
                            "product_id": sale.get("product_id"),
                            "refunded": refunded,
                        },
                    )
                )
            next_key = payload.get("next_page_key")
            if not next_key:
                break
            page_key = str(next_key)
        return orders


class LemonSqueezyClient:
    """Pure-stdlib Lemon Squeezy JSON:API v1 client (`Accept: application/vnd.api+json`)."""

    def __init__(
        self,
        api_key: str,
        *,
        sender: Callable[[urllib.request.Request, float], HttpResult] = _urllib_sender,
        sleeper: Callable[[float], None] = time.sleep,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.api_key = api_key.strip()
        self._sender = sender
        self._sleep = sleeper
        self.timeout_seconds = timeout_seconds

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: Optional[Mapping[str, str]] = None,
        json_body: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        if not self.api_key:
            raise StorefrontApiError("LEMONSQUEEZY_API_KEY is empty")
        url = f"{LEMONSQUEEZY_API_BASE}{path}"
        if query:
            clean_q = {k: v for k, v in query.items() if v is not None and v != ""}
            if clean_q:
                url = f"{url}?{urllib.parse.urlencode(clean_q)}"

        headers = {
            "Accept": "application/vnd.api+json",
            "Content-Type": "application/vnd.api+json",
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": USER_AGENT,
        }
        data = json.dumps(json_body).encode("utf-8") if json_body is not None else None

        for attempt in range(1, 4):
            req = urllib.request.Request(url=url, data=data, headers=headers, method=method)
            res = self._sender(req, self.timeout_seconds)
            if res.status == 429 and attempt < 3:
                self._sleep(1.0 * attempt)
                continue
            if res.status in {500, 502, 503, 504} and attempt < 3:
                self._sleep(0.5 * attempt)
                continue
            if res.status not in {200, 201}:
                preview = res.body.decode("utf-8", errors="replace")[:300]
                raise StorefrontApiError(
                    f"Lemon Squeezy API {method} {url} failed with HTTP {res.status}: {preview}"
                )
            return json.loads(res.body.decode("utf-8") or "{}")
        raise StorefrontApiError("Exhausted Lemon Squeezy API retry attempts")

    def list_products(self, store_id: Optional[str] = None) -> list[dict[str, Any]]:
        q = {"filter[store_id]": store_id} if store_id else None
        payload = self._request("GET", "/products", query=q)
        return list(payload.get("data", []))

    def list_orders(
        self,
        store_id: Optional[str] = None,
        *,
        max_pages: int = 10,
    ) -> list[StoreOrderRecord]:
        now_iso = utc_now_iso()
        orders: list[StoreOrderRecord] = []
        seen_ids: set[str] = set()

        for page_num in range(1, max(1, max_pages) + 1):
            q: dict[str, str] = {"page[size]": "100"}
            if store_id:
                q["filter[store_id]"] = store_id
            if page_num > 1:
                q["page[number]"] = str(page_num)

            payload = self._request("GET", "/orders", query=q)
            top_meta = payload.get("meta") or {}
            top_meta_custom = top_meta.get("custom_data")
            items = payload.get("data") or []

            for item in items:
                order_id = str(item.get("id") or "").strip()
                if not order_id or order_id in seen_ids:
                    continue
                seen_ids.add(order_id)
                attrs = item.get("attributes") or {}
                status = str(attrs.get("status") or "paid").strip().lower()
                refunded = bool(attrs.get("refunded", False) or status == "refunded")
                if status in {"pending", "failed", "fraudulent"} and not refunded:
                    continue

                gross_cents = int(
                    attrs.get("subtotal_usd")
                    if attrs.get("subtotal_usd") is not None
                    else attrs.get("total_usd", attrs.get("total", 0))
                )
                fee_cents = int(attrs.get("fee_usd", attrs.get("processor_fee_cents", 0)) or 0)
                net_cents = 0 if refunded else max(0, gross_cents - fee_cents)

                first_item = attrs.get("first_order_item") or {}
                checkout_data = attrs.get("checkout_data") or {}
                utm = _extract_utm_from_mapping_or_url(
                    attrs.get("custom_data"),
                    checkout_data.get("custom") if isinstance(checkout_data, Mapping) else None,
                    checkout_data,
                    (item.get("meta") or {}).get("custom_data"),
                    top_meta_custom,
                    attrs,
                    (attrs.get("urls") or {}).get("receipt"),
                )
                prod_name = str(
                    first_item.get("product_name")
                    or attrs.get("product_name")
                    or ""
                )
                sku = utm["sku"] or str(first_item.get("variant_name") or "")

                orders.append(
                    StoreOrderRecord(
                        storefront="lemonsqueezy",
                        order_id=order_id,
                        sku=sku,
                        product_name=prod_name,
                        gross_cents=gross_cents,
                        fee_cents=fee_cents,
                        net_cents=net_cents,
                        currency=str(attrs.get("currency") or "USD").upper(),
                        refunded=refunded,
                        utm_source=utm["utm_source"],
                        utm_medium=utm["utm_medium"],
                        utm_campaign=utm["utm_campaign"],
                        attributed_slug=utm["slug"],
                        created_at=str(attrs.get("created_at") or now_iso),
                        synced_at=now_iso,
                        raw_data={
                            "id": order_id,
                            "order_number": attrs.get("order_number"),
                            "status": status,
                            "refunded": refunded,
                        },
                    )
                )

            page_meta = top_meta.get("page") or {}
            cur_p = int(page_meta.get("currentPage") or page_num)
            last_p = int(page_meta.get("lastPage") or cur_p)
            has_next = bool((payload.get("links") or {}).get("next")) or (cur_p < last_p)
            if not items or not has_next:
                break

        return orders

    def create_checkout(
        self,
        *,
        store_id: str,
        variant_id: str,
        custom_price_cents: Optional[int] = None,
        custom_data: Optional[Mapping[str, str]] = None,
        redirect_url: Optional[str] = None,
    ) -> dict[str, Any]:
        """Create a dynamic Lemon Squeezy JSON:API v1 checkout session with UTM custom_data."""
        attributes: dict[str, Any] = {
            "checkout_data": {
                "custom": dict(custom_data or {}),
            }
        }
        if custom_price_cents is not None:
            attributes["custom_price"] = max(0, int(custom_price_cents))
        if redirect_url:
            attributes["product_options"] = {"redirect_url": redirect_url}

        body = {
            "data": {
                "type": "checkouts",
                "attributes": attributes,
                "relationships": {
                    "store": {"data": {"type": "stores", "id": str(store_id)}},
                    "variant": {"data": {"type": "variants", "id": str(variant_id)}},
                },
            }
        }
        return self._request("POST", "/checkouts", json_body=body)


def sync_storefronts(
    config: AppConfig,
    db: PipelineDatabase,
    *,
    create_gumroad_drafts: bool = False,
    catalog_products: Optional[list[dict[str, Any]]] = None,
    gumroad_sender: Callable[[urllib.request.Request, float], HttpResult] = _urllib_sender,
    lemonsqueezy_sender: Callable[[urllib.request.Request, float], HttpResult] = _urllib_sender,
    patreon_sender: Callable[[urllib.request.Request, float], HttpResult] = _urllib_sender,
    whop_sender: Callable[[urllib.request.Request, float], HttpResult] = _urllib_sender,
) -> dict[str, Any]:
    """Sync products, members & sales across Whop v1, Patreon v2, Gumroad v2 & Lemon Squeezy v1 into SQLite."""
    from engine.patreon_client import PatreonClient
    from engine.whop_client import WhopClient

    report: dict[str, Any] = {
        "synced_at": utc_now_iso(),
        "whop": {
            "status": "dry_run",
            "company_id": config.whop_company_id,
            "payments_synced": 0,
            "memberships_synced": 0,
            "orders_synced": 0,
        },
        "gumroad": {"status": "dry_run", "products_count": 0, "orders_synced": 0, "drafts_created": []},
        "lemonsqueezy": {"status": "dry_run", "products_count": 0, "orders_synced": 0},
        "patreon": {"status": "dry_run", "campaign_id": "", "orders_synced": 0},
    }

    if config.whop_api_key:
        whop = WhopClient(config, sender=whop_sender, sleeper=lambda _s: None)
        report["whop"] = whop.sync_to_db(db)
    else:
        report["whop"]["reason"] = "WHOP_API_KEY not configured; skipped live HTTP calls."

    if config.gumroad_access_token:
        gr = GumroadClient(config.gumroad_access_token, sender=gumroad_sender, sleeper=lambda _s: None)
        products = gr.list_products()
        existing_names = {str(p.get("name") or "").strip().lower() for p in products}
        drafts_created: list[dict[str, Any]] = []

        if create_gumroad_drafts and catalog_products:
            for spec in catalog_products:
                title = str(spec.get("title") or "").strip()
                if title and title.lower() not in existing_names:
                    created_prod = gr.create_draft_product(
                        name=title,
                        price_cents=int(spec.get("price_usd", 0)) * 100,
                        description=str(spec.get("summary") or spec.get("tagline") or title),
                        summary=str(spec.get("tagline") or ""),
                        tags=["python", "sqlite", "reliability", "automation"],
                        custom_permalink=str(spec.get("slug") or "")[:32],
                    )
                    drafts_created.append(created_prod)
                    existing_names.add(title.lower())

        gr_orders = gr.list_sales()
        upserted_gr = db.upsert_store_orders(gr_orders)
        report["gumroad"] = {
            "status": "synced",
            "products_count": len(products) + len(drafts_created),
            "orders_synced": upserted_gr,
            "drafts_created": drafts_created,
        }
    else:
        report["gumroad"]["reason"] = "GUMROAD_ACCESS_TOKEN not configured; skipped live HTTP calls."

    if config.lemonsqueezy_api_key:
        ls = LemonSqueezyClient(config.lemonsqueezy_api_key, sender=lemonsqueezy_sender, sleeper=lambda _s: None)
        store_id = config.lemonsqueezy_store_id or None
        ls_products = ls.list_products(store_id=store_id)
        ls_orders = ls.list_orders(store_id=store_id)
        upserted_ls = db.upsert_store_orders(ls_orders)
        report["lemonsqueezy"] = {
            "status": "synced",
            "products_count": len(ls_products),
            "orders_synced": upserted_ls,
        }
    else:
        report["lemonsqueezy"]["reason"] = "LEMONSQUEEZY_API_KEY not configured; skipped live HTTP calls."

    if config.patreon_creator_access_token:
        pt = PatreonClient(config, sender=patreon_sender, sleeper=lambda _s: None)
        pt_orders = pt.list_members_as_orders()
        upserted_pt = db.upsert_store_orders(pt_orders)
        report["patreon"] = {
            "status": "synced",
            "campaign_id": pt.campaign_id,
            "orders_synced": upserted_pt,
        }
    else:
        report["patreon"]["reason"] = "PATREON_CREATOR_ACCESS_TOKEN not configured; skipped live HTTP calls."

    return report
