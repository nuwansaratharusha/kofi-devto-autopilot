"""30-Day Omnichannel Experiment Analytics, Payout Unlock Meters & Verdict Engine."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from engine.config import AppConfig
from engine.db import PipelineDatabase, utc_now_iso
from engine.models import PILLAR_META, SERIES_30D_NAME, VALID_CHANNELS, StoreOrderRecord

COLOMBO_TZ = timezone(timedelta(hours=5, minutes=30))

VERDICT_WORKING = "WORKING — SCALE IN MONTH 2"
VERDICT_BOTTLENECK = "BOTTLENECK — KEEP CHANNEL, FIX CTA"
VERDICT_NOT_WORKING = "NOT WORKING — PAUSE & REALLOCATE"

LEMONSQUEEZY_MIN_PAYOUT_CENTS = 5_000   # $50.00 USD Sri Lanka bank wire threshold
GUMROAD_MIN_PAYOUT_CENTS = 10_000       # $100.00 USD Sri Lanka bank deposit threshold
PATREON_MIN_PAYOUT_CENTS = 2_500        # $25.00 USD Patreon -> Payoneer payout threshold
NASIO_TARGET_PAYOUT_CENTS = 5_000       # $50.00 USD Month-1 Nas.io Challenge & Store target
NASIO_PAYONEER_MIN_CENTS = 1_000        # $10.00 USD Nas.io -> Payoneer USD payout unlock
NASIO_LKR_WIRE_MIN_CENTS = 6_500        # $65.00 USD Nas.io -> Sri Lanka LKR bank wire threshold


def colombo_today_iso() -> str:
    return datetime.now(COLOMBO_TZ).strftime("%Y-%m-%d")


def is_mon_wed_fri(date_str: str) -> bool:
    dt = date.fromisoformat(date_str)
    return dt.weekday() in {0, 2, 4}


def compute_series_day_for_date(date_str: str, start_date_str: str) -> int:
    target = date.fromisoformat(date_str)
    start = date.fromisoformat(start_date_str)
    raw_day = (target - start).days + 1
    return max(1, min(30, raw_day))


def normalize_utm_channel(raw_source: str) -> str:
    low = raw_source.strip().lower()
    if low in {"blogger", "blogspot", "google_blogger"}:
        return "blogger"
    if low in {"patreon", "patreon_post", "patreon_shop", "patreon_tier"}:
        return "patreon"
    if low in {"nasio", "nas.io", "nas_io", "nasio_challenge", "nasio_community", "nasio_dm", "magic_reach"}:
        return "nasio"
    if low in {"pinterest", "pin", "pinterest_rss"}:
        return "pinterest"
    if low in {"devto", "dev.to", "forem"}:
        return "devto"
    if low in {"canonical_site", "site", "github_pages", "rss", "blog"}:
        return "canonical_site"
    return "unattributed"


def evaluate_verdict(
    *,
    views: int,
    clicks: int,
    free_downloads: int,
    paid_orders: int,
    net_cents: int,
) -> tuple[str, str]:
    """Deterministic 3-state verdict classifier for channels and content pillars."""
    if paid_orders >= 1 or net_cents > 0 or (free_downloads >= 5 and clicks >= 10):
        return (
            VERDICT_WORKING,
            f"Proven conversion ({paid_orders} paid / ${net_cents / 100.0:.2f} net, {free_downloads} free leads) — double cadence & ad-free SEO focus in Month 2.",
        )
    if views >= 100 or clicks >= 10 or free_downloads >= 1:
        return (
            VERDICT_BOTTLENECK,
            f"Top-of-funnel reach confirmed ({views} views, {clicks} clicks, {free_downloads} free downloads) with $0 paid checkout — keep publishing and tighten above-the-fold $15 Challenge / $14 Kit CTA.",
        )
    return (
        VERDICT_NOT_WORKING,
        f"Low reach & zero conversions ({views} views, {clicks} clicks, 0 orders) — pause manual effort here if unchanged by Day 30 and reallocate to top channel.",
    )


def _storefront_meter(
    orders: list[StoreOrderRecord],
    *,
    storefront_key: str,
    label: str,
    threshold_cents: int,
    store_url: str,
) -> dict[str, Any]:
    matching = [o for o in orders if o.storefront.lower() == storefront_key]
    active = [o for o in matching if not o.refunded]
    refunded = [o for o in matching if o.refunded]

    paid_active = [o for o in active if o.gross_cents > 0]
    free_active = [o for o in active if o.gross_cents == 0]
    gross_cents = sum(o.gross_cents for o in active)
    fee_cents = sum(o.fee_cents for o in active)
    net_cents = sum(o.net_cents for o in active)
    pct = round(min(100.0, (net_cents / float(threshold_cents)) * 100.0), 1) if threshold_cents > 0 else 100.0
    rem_cents = max(0, threshold_cents - net_cents)

    return {
        "storefront": storefront_key,
        "label": label,
        "store_url": store_url,
        "paid_orders": len(paid_active),
        "free_downloads": len(free_active),
        "refunded_orders": len(refunded),
        "gross_cents": gross_cents,
        "fee_cents": fee_cents,
        "net_cents": net_cents,
        "net_usd": round(net_cents / 100.0, 2),
        "threshold_cents": threshold_cents,
        "threshold_usd": round(threshold_cents / 100.0, 2),
        "unlock_progress_pct": pct,
        "remaining_to_unlock_usd": round(rem_cents / 100.0, 2),
        "unlocked": net_cents >= threshold_cents,
    }


def build_experiment_report(
    db: PipelineDatabase,
    config: AppConfig,
    *,
    as_of: Optional[str] = None,
) -> dict[str, Any]:
    """Compute the full 30-day experiment report across channels, pillars, and storefronts."""
    as_of_str = (as_of or colombo_today_iso()).strip()
    start_dt = date.fromisoformat(config.experiment_start_date)
    end_dt = date.fromisoformat(config.experiment_end_date)
    cur_dt = date.fromisoformat(as_of_str)

    total_window_days = max(1, (end_dt - start_dt).days)
    days_elapsed = max(0, min(total_window_days, (cur_dt - start_dt).days + 1))
    days_remaining = max(0, (end_dt - cur_dt).days)

    articles = db.list_articles(verified_only=True)
    all_orders = db.list_store_orders(include_refunded=True)
    metrics = db.list_channel_metrics()

    slug_to_pillar = {a.slug: a.pillar for a in articles}

    # Storefront payout unlock meters
    ls_meter = _storefront_meter(
        all_orders,
        storefront_key="lemonsqueezy",
        label="Lemon Squeezy ($50 Sri Lanka Bank Wire)",
        threshold_cents=LEMONSQUEEZY_MIN_PAYOUT_CENTS,
        store_url=config.lemonsqueezy_store_url or config.paid_checkout_url,
    )
    gr_meter = _storefront_meter(
        all_orders,
        storefront_key="gumroad",
        label="Gumroad ($100 Sri Lanka Bank Deposit)",
        threshold_cents=GUMROAD_MIN_PAYOUT_CENTS,
        store_url=config.gumroad_store_url or config.free_checkout_url,
    )
    pt_meter = _storefront_meter(
        all_orders,
        storefront_key="patreon",
        label="Patreon ($25 Payoneer Payout)",
        threshold_cents=PATREON_MIN_PAYOUT_CENTS,
        store_url=config.patreon_url or "https://www.patreon.com",
    )
    nasio_meter = _storefront_meter(
        all_orders,
        storefront_key="nasio",
        label="Nas.io ($10 Payoneer USD / $65 LKR Wire)",
        threshold_cents=NASIO_TARGET_PAYOUT_CENTS,
        store_url=config.nasio_store_url or config.patreon_url or config.paid_checkout_url,
    )
    nasio_meter["payoneer_min_usd"] = round(NASIO_PAYONEER_MIN_CENTS / 100.0, 2)
    nasio_meter["payoneer_unlocked"] = nasio_meter["net_cents"] >= NASIO_PAYONEER_MIN_CENTS
    nasio_meter["lkr_wire_min_usd"] = round(NASIO_LKR_WIRE_MIN_CENTS / 100.0, 2)
    nasio_meter["lkr_wire_unlocked"] = nasio_meter["net_cents"] >= NASIO_LKR_WIRE_MIN_CENTS

    active_orders = [o for o in all_orders if not o.refunded]
    refunded_orders = [o for o in all_orders if o.refunded]

    # Per-channel aggregation
    channel_kpis: dict[str, dict[str, Any]] = {}
    for ch in VALID_CHANNELS:
        ch_metrics = [m for m in metrics if m.channel == ch]
        views = sum(m.views for m in ch_metrics)
        clicks = sum(m.clicks for m in ch_metrics)
        ch_orders = [o for o in active_orders if normalize_utm_channel(o.utm_source) == ch]
        free_dl = sum(1 for o in ch_orders if o.gross_cents == 0)
        paid_n = sum(1 for o in ch_orders if o.gross_cents > 0)
        net_c = sum(o.net_cents for o in ch_orders)

        if ch == "blogger":
            posts_count = len(articles)
            live_remote = sum(1 for a in articles if a.blogger_post_id)
        elif ch == "patreon":
            posts_count = sum(
                1
                for a in articles
                if a.patreon_post_id
                or (config.exports_dir / a.slug / "patreon_public_post.md").is_file()
            )
            live_remote = sum(1 for a in articles if a.patreon_post_id)
        elif ch == "nasio":
            posts_count = sum(
                1
                for a in articles
                if (config.exports_dir / a.slug / "nasio_daily_pack.md").is_file()
            )
            live_remote = sum(1 for o in active_orders if o.storefront.lower() == "nasio")
        elif ch == "pinterest":
            posts_count = sum(
                1 for a in articles if (config.site_dir / "pins" / f"{a.slug}.png").is_file() or a.pinterest_pin_id
            )
            live_remote = sum(1 for a in articles if a.pinterest_pin_id)
        elif ch == "devto":
            posts_count = sum(
                1 for a in articles if a.devto_article_id or (config.exports_dir / a.slug / "devto_payload.json").is_file()
            )
            live_remote = sum(1 for a in articles if a.devto_article_id)
        else:
            posts_count = len(articles)
            live_remote = len(articles)

        ctr_pct = round((clicks / float(views)) * 100.0, 2) if views > 0 else 0.0
        conv_pct = round(((free_dl + paid_n) / float(clicks)) * 100.0, 2) if clicks > 0 else 0.0
        verdict, note = evaluate_verdict(
            views=views,
            clicks=clicks,
            free_downloads=free_dl,
            paid_orders=paid_n,
            net_cents=net_c,
        )
        channel_kpis[ch] = {
            "channel": ch,
            "posts_prepared": posts_count,
            "posts_remote_id_logged": live_remote,
            "views": views,
            "clicks": clicks,
            "ctr_pct": ctr_pct,
            "free_downloads": free_dl,
            "paid_orders": paid_n,
            "net_cents": net_c,
            "net_usd": round(net_c / 100.0, 2),
            "conversion_rate_pct": conv_pct,
            "verdict": verdict,
            "action_note": note,
        }

    def _resolve_order_slug(o: StoreOrderRecord) -> str:
        for cand_raw in (o.attributed_slug, o.utm_campaign):
            cand = cand_raw.strip()
            if not cand:
                continue
            if cand in slug_to_pillar:
                return cand
            if len(cand) >= 12:
                for ks in slug_to_pillar:
                    if ks.startswith(cand):
                        return ks
        return ""

    # Per-pillar aggregation
    pillar_kpis: dict[str, dict[str, Any]] = {}
    for p_code, p_meta in PILLAR_META.items():
        p_slugs = {a.slug for a in articles if a.pillar == p_code}
        p_metrics = [m for m in metrics if m.slug in p_slugs]
        views = sum(m.views for m in p_metrics)
        clicks = sum(m.clicks for m in p_metrics)

        p_orders: list[StoreOrderRecord] = []
        for o in active_orders:
            matched_slug = _resolve_order_slug(o)
            if matched_slug and slug_to_pillar.get(matched_slug) == p_code:
                p_orders.append(o)

        free_dl = sum(1 for o in p_orders if o.gross_cents == 0)
        paid_n = sum(1 for o in p_orders if o.gross_cents > 0)
        net_c = sum(o.net_cents for o in p_orders)
        ctr_pct = round((clicks / float(views)) * 100.0, 2) if views > 0 else 0.0
        verdict, note = evaluate_verdict(
            views=views,
            clicks=clicks,
            free_downloads=free_dl,
            paid_orders=paid_n,
            net_cents=net_c,
        )
        pillar_kpis[p_code] = {
            "pillar": p_code,
            "label": p_meta["label"],
            "articles_verified": len(p_slugs),
            "views": views,
            "clicks": clicks,
            "ctr_pct": ctr_pct,
            "free_downloads": free_dl,
            "paid_orders": paid_n,
            "net_cents": net_c,
            "net_usd": round(net_c / 100.0, 2),
            "verdict": verdict,
            "action_note": note,
        }

    total_net_cents = sum(o.net_cents for o in active_orders)
    total_gross_cents = sum(o.gross_cents for o in active_orders)
    unattr_orders = [o for o in active_orders if normalize_utm_channel(o.utm_source) == "unattributed"]
    unattr_net_cents = sum(o.net_cents for o in unattr_orders)
    winning_channels = [
        ch for ch, data in channel_kpis.items() if data["verdict"] == VERDICT_WORKING
    ]

    return {
        "generated_at": utc_now_iso(),
        "series_name": SERIES_30D_NAME,
        "window": {
            "start_date": config.experiment_start_date,
            "end_date": config.experiment_end_date,
            "as_of_date": as_of_str,
            "timezone": config.timezone,
            "days_elapsed": days_elapsed,
            "days_remaining": days_remaining,
            "target_daily_articles": 30,
            "verified_articles_built": len(articles),
        },
        "totals": {
            "paid_orders": sum(1 for o in active_orders if o.gross_cents > 0),
            "free_downloads": sum(1 for o in active_orders if o.gross_cents == 0),
            "refunded_orders_excluded": len(refunded_orders),
            "unattributed_orders": len(unattr_orders),
            "unattributed_net_usd": round(unattr_net_cents / 100.0, 2),
            "gross_cents": total_gross_cents,
            "net_cents": total_net_cents,
            "net_usd": round(total_net_cents / 100.0, 2),
            "winning_channels": winning_channels,
        },
        "storefronts": {
            "nasio": nasio_meter,
            "patreon": pt_meter,
            "lemonsqueezy": ls_meter,
            "gumroad": gr_meter,
        },
        "channels": channel_kpis,
        "pillars": pillar_kpis,
    }


def render_experiment_markdown(report: dict[str, Any]) -> str:
    w = report["window"]
    t = report["totals"]
    ls = report["storefronts"]["lemonsqueezy"]
    gr = report["storefronts"]["gumroad"]
    pt = report["storefronts"]["patreon"]
    ns = report["storefronts"].get("nasio") or {
        "net_usd": 0.0,
        "threshold_usd": 50.0,
        "unlock_progress_pct": 0.0,
        "remaining_to_unlock_usd": 50.0,
        "unlocked": False,
        "payoneer_unlocked": False,
    }
    ns_status = (
        "TARGET HIT ($50+)"
        if ns["unlocked"]
        else ("PAYONEER $10 UNLOCKED" if ns.get("payoneer_unlocked") else "ACCUMULATING")
    )

    ch_rows = "\n".join(
        f"| `{ch}` | {d['posts_prepared']} | {d['views']} | {d['clicks']} | {d['ctr_pct']}% | "
        f"{d['free_downloads']} | {d['paid_orders']} | ${d['net_usd']:.2f} | **{d['verdict']}** |"
        for ch, d in report["channels"].items()
    )
    pillar_rows = "\n".join(
        f"| `{p}` ({d['label']}) | {d['articles_verified']} | {d['views']} | {d['clicks']} | "
        f"{d['free_downloads']} | {d['paid_orders']} | ${d['net_usd']:.2f} | **{d['verdict']}** |"
        for p, d in report["pillars"].items()
    )

    unattr_note = (
        f"\n\n> *Direct / Other Referrer Orders (`unattributed` to primary channels): "
        f"`{t.get('unattributed_orders', 0)}` orders (`${t.get('unattributed_net_usd', 0.0):.2f}` net USD).*"
        if t.get("unattributed_orders", 0) > 0
        else ""
    )

    return f"""# 30-Day Omnichannel Experiment Report (`{w['start_date']}` → `{w['end_date']}`)

- **As-Of Date (`{w['timezone']}`):** `{w['as_of_date']}` (Day `{w['days_elapsed']}` / `30` · `{w['days_remaining']}` days remaining)
- **Verified Series Articles Built:** `{w['verified_articles_built']} / {w['target_daily_articles']}`
- **Total Net Revenue (Refunds Excluded):** `${t['net_usd']:.2f} USD` (`{t['paid_orders']}` paid orders, `{t['free_downloads']}` free leads, `{t['refunded_orders_excluded']}` refunded excluded)

---

## 1. Sri Lanka Merchant-of-Record Payout Unlock Meters

| Storefront | Net Settled | Payout Unlock Minimum | Progress | Remaining | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Nas.io** | `${ns['net_usd']:.2f}` | `${ns['threshold_usd']:.2f}` ($10 Payoneer / $65 LKR Wire) | `{ns['unlock_progress_pct']}%` | `${ns['remaining_to_unlock_usd']:.2f}` | `{ns_status}` |
| **Patreon** | `${pt['net_usd']:.2f}` | `${pt['threshold_usd']:.2f}` (Payoneer) | `{pt['unlock_progress_pct']}%` | `${pt['remaining_to_unlock_usd']:.2f}` | `{"UNLOCKED" if pt["unlocked"] else "ACCUMULATING"}` |
| **Lemon Squeezy** | `${ls['net_usd']:.2f}` | `${ls['threshold_usd']:.2f}` (Bank Wire) | `{ls['unlock_progress_pct']}%` | `${ls['remaining_to_unlock_usd']:.2f}` | `{"UNLOCKED" if ls["unlocked"] else "ACCUMULATING"}` |
| **Gumroad** | `${gr['net_usd']:.2f}` | `${gr['threshold_usd']:.2f}` (Local Bank) | `{gr['unlock_progress_pct']}%` | `${gr['remaining_to_unlock_usd']:.2f}` | `{"UNLOCKED" if gr["unlocked"] else "ACCUMULATING"}` |

---

## 2. Channel Attribution & Deterministic Month-2 Verdicts

| Channel | Assets Ready | Views | Clicks | CTR | Free Kits | Paid Orders | Net USD | Verdict |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
{ch_rows}{unattr_note}

---

## 3. Content Pillar Performance Breakdown

| Pillar Track | Verified Posts | Views | Clicks | Free Kits | Paid Orders | Net USD | Verdict |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
{pillar_rows}
"""


def write_experiment_artifacts(
    db: PipelineDatabase,
    config: AppConfig,
    *,
    as_of: Optional[str] = None,
) -> dict[str, Any]:
    """Compute experiment report and persist `exports/experiment_30d_report.{json,md}`."""
    report = build_experiment_report(db, config, as_of=as_of)
    config.exports_dir.mkdir(parents=True, exist_ok=True)
    json_path = config.exports_dir / "experiment_30d_report.json"
    md_path = config.exports_dir / "experiment_30d_report.md"

    json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_experiment_markdown(report), encoding="utf-8")
    return {
        "report": report,
        "json_path": str(json_path),
        "markdown_path": str(md_path),
    }
