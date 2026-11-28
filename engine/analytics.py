"""Multi-channel live analytics synchronizer across Whop Forum, DEV.to, Patreon & Store Orders."""

from __future__ import annotations

import urllib.request
from collections import defaultdict
from typing import Any, Callable

from engine.config import AppConfig
from engine.db import PipelineDatabase, utc_now_iso
from engine.devto_client import DevtoClient, DevtoPublishError, HttpResult, _urllib_sender
from engine.experiment import normalize_utm_channel
from engine.models import VALID_CHANNELS
from engine.patreon_client import PatreonApiError, PatreonClient
from engine.whop_client import WhopApiError, WhopClient


def reconcile_order_click_floors(db: PipelineDatabase) -> int:
    """Ensure every slug with attributed non-refunded store orders has `clicks >= order_count` on that channel."""
    articles = db.list_articles(verified_only=True)
    known_slugs = {a.slug for a in articles}
    orders = db.list_store_orders(include_refunded=False)

    counts_by_slug_channel: dict[tuple[str, str], int] = defaultdict(int)
    for order in orders:
        ch = normalize_utm_channel(order.utm_source)
        if ch not in VALID_CHANNELS:
            continue
        matched_slug = ""
        for raw_cand in (order.attributed_slug, order.utm_campaign):
            cand = raw_cand.strip()
            if not cand:
                continue
            if cand in known_slugs:
                matched_slug = cand
                break
            if len(cand) >= 12:
                for ks in known_slugs:
                    if ks.startswith(cand):
                        matched_slug = ks
                        break
            if matched_slug:
                break
        if matched_slug:
            counts_by_slug_channel[(matched_slug, ch)] += 1

    updated = 0
    for (slug, ch), order_count in counts_by_slug_channel.items():
        db.upsert_channel_metrics_monotonic(
            slug=slug,
            channel=ch,
            min_views=order_count,
            min_clicks=order_count,
        )
        updated += 1
    return updated


def sync_all_analytics(
    config: AppConfig,
    db: PipelineDatabase,
    *,
    whop_sender: Callable[[urllib.request.Request, float], HttpResult] = _urllib_sender,
    devto_sender: Callable[[urllib.request.Request, float], HttpResult] = _urllib_sender,
    patreon_sender: Callable[[urllib.request.Request, float], HttpResult] = _urllib_sender,
) -> dict[str, Any]:
    """Pull live views, likes/reactions, comments & order click floors across Whop, DEV.to & Patreon."""
    whop_client = WhopClient(config, sender=whop_sender, sleeper=lambda _s: None)
    devto_client = DevtoClient(config, sender=devto_sender, sleeper=lambda _s: None)
    patreon_client = PatreonClient(config, sender=patreon_sender, sleeper=lambda _s: None)

    try:
        whop_res = whop_client.sync_forum_metrics(db)
    except (WhopApiError, OSError) as exc:
        whop_res = {"status": "error", "error": str(exc), "posts_matched": 0, "views_total": 0}

    try:
        devto_res = devto_client.sync_article_metrics(db)
    except (DevtoPublishError, OSError) as exc:
        devto_res = {"status": "error", "error": str(exc), "articles_matched": 0, "views_total": 0}

    try:
        patreon_res = patreon_client.sync_post_metrics(db)
    except (PatreonApiError, OSError) as exc:
        patreon_res = {"status": "error", "error": str(exc), "posts_matched": 0, "views_total": 0}

    floors_applied = reconcile_order_click_floors(db)

    return {
        "synced_at": utc_now_iso(),
        "whop": whop_res,
        "devto": devto_res,
        "patreon": patreon_res,
        "order_click_floors_applied": floors_applied,
    }
