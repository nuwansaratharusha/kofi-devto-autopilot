#!/usr/bin/env python3
"""Unified CLI & Localhost Control Center for the Omnichannel Content & Storefront Autopilot."""

from __future__ import annotations

import argparse
import html
import json
import mimetypes
import sys
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote, urlparse

from engine.blogger_client import BloggerClient, run_blogger_pkce_loopback
from engine.composer import compose_article
from engine.config import AppConfig
from engine.db import PipelineDatabase, utc_now_iso
from engine.devto_client import DevtoClient, DevtoPublishError
from engine.experiment import (
    build_experiment_report,
    colombo_today_iso,
    compute_series_day_for_date,
    is_mon_wed_fri,
    render_experiment_markdown,
    write_experiment_artifacts,
)
from engine.exporter import export_article_bundle, load_bundle_texts
from engine.harvester import harvest_signals
from engine.models import PILLAR_META, SERIES_30D_NAME, VALID_CHANNELS
from engine.pinterest_client import PinterestClient
from engine.site_builder import SITE_STYLESHEET, build_static_site
from engine.storefronts import sync_storefronts
from engine.verifier import verify_article
from products.builder import build_all_products, load_catalog

PILLAR_CLI_MAP = {
    "a": "pillar_a",
    "pillar_a": "pillar_a",
    "b": "pillar_b",
    "pillar_b": "pillar_b",
    "c": "pillar_c",
    "pillar_c": "pillar_c",
}


def _safe_json_for_html_script(obj: Any) -> str:
    """Serialize JSON safely for embedding inside `<script type="application/json">`."""
    raw = json.dumps(obj)
    return raw.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")


def _load_or_build_products(config: AppConfig, *, force_rebuild: bool = False) -> dict[str, Any]:
    manifest_file = config.dist_dir / "manifest.json"
    dual_setup_file = config.exports_dir / "storefront" / "lemonsqueezy_and_gumroad_setup.md"
    if not force_rebuild and manifest_file.is_file() and dual_setup_file.is_file():
        data = json.loads(manifest_file.read_text(encoding="utf-8"))
        products = data.get("products", [])
        if products and all(Path(p["zip_path"]).is_file() for p in products):
            return {
                "manifest_path": str(manifest_file),
                "storefront_copy_path": str(
                    config.exports_dir / "storefront" / "kofi_profile_and_shop_copy.md"
                ),
                "dual_storefront_setup_path": str(dual_setup_file),
                "products": products,
            }
    return build_all_products(
        config.project_root,
        kofi_profile_url=config.kofi_profile_url,
        primary_store_url=config.primary_store_url,
        mirror_store_url=config.mirror_store_url,
        lemonsqueezy_store_url=config.lemonsqueezy_store_url,
        gumroad_store_url=config.gumroad_store_url,
    )


def cmd_build_products(config: AppConfig, *, as_json: bool = False) -> dict[str, Any]:
    result = _load_or_build_products(config, force_rebuild=True)
    if as_json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Built {len(result['products'])} verified .zip toolkits:")
        for p in result["products"]:
            print(
                f"  - {p['zip_filename']} ({p['size_bytes']:,} bytes | SHA-256: {p['sha256'][:16]}...)"
            )
        print(f"Dual Storefront setup ready at: {result['dual_storefront_setup_path']}")
        print(f"Ko-fi copy ready at           : {result['storefront_copy_path']}")
    return result


def cmd_run_pipeline(
    config: AppConfig,
    *,
    limit: int = 3,
    pillar: Optional[str] = None,
    push_draft: bool = False,
    offline: bool = False,
    as_json: bool = False,
) -> dict[str, Any]:
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    started_at = utc_now_iso()
    db = PipelineDatabase(config.db_path)

    product_info = _load_or_build_products(config, force_rebuild=False)
    discovered = harvest_signals(offline=offline)
    newly_inserted = db.upsert_signals(discovered)

    pillar_code = PILLAR_CLI_MAP.get(pillar.lower()) if pillar else None
    candidates = db.select_unused_signals(limit=max(0, limit), pillar_filter=pillar_code)

    devto = DevtoClient(config)
    processed_articles: list[dict[str, Any]] = []
    verified_count = 0
    staged_count = 0

    for sig in candidates:
        day_num = db.next_series_day()
        draft_record = compose_article(sig, config, series_day=day_num)
        verified_record, report = verify_article(draft_record, config=config)
        saved = db.save_article(verified_record)
        db.mark_signal_used(sig.fingerprint)

        entry: dict[str, Any] = {
            "slug": saved.slug,
            "series_day": saved.series_day,
            "pillar": saved.pillar,
            "scheduled_slot": saved.scheduled_slot,
            "title": saved.title,
            "status": saved.status,
            "word_count": saved.word_count,
            "verification": report.to_dict(),
        }

        if report.passed:
            verified_count += 1
            series_so_far = db.list_articles(verified_only=True)
            bundle_paths = export_article_bundle(saved, config, series_articles=series_so_far)
            entry["exports"] = bundle_paths

            if push_draft:
                push_res = devto.stage_or_publish(saved)
                saved = db.save_article(saved)
                entry["devto"] = push_res
                if push_res.get("status") in {"staged_draft", "published_live"}:
                    staged_count += 1
        processed_articles.append(entry)

    all_verified = db.list_articles(verified_only=True)
    # Re-export Blogger series index so earlier days link to newly added days cleanly
    for art in all_verified:
        BloggerClient(config).export_artifacts(art, series_articles=all_verified)

    exp_artifacts = write_experiment_artifacts(db, config)
    site_summary = build_static_site(
        all_verified,
        product_info["products"],
        config,
        experiment_report=exp_artifacts["report"],
    )

    finished_at = utc_now_iso()
    summary: dict[str, Any] = {
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "signals_harvested": len(discovered),
        "signals_newly_inserted": newly_inserted,
        "articles_processed": processed_articles,
        "articles_verified_this_run": verified_count,
        "total_verified_articles_on_site": len(all_verified),
        "site": site_summary,
        "experiment_report_json": exp_artifacts["json_path"],
        "products": product_info["products"],
    }
    db.record_publish_run(
        run_id=run_id,
        started_at=started_at,
        finished_at=finished_at,
        mode=config.publish_mode if push_draft else "local_verify",
        articles_verified=verified_count,
        articles_staged=staged_count,
        status="ok",
        details={"articles": [a["slug"] for a in processed_articles]},
    )

    if as_json:
        print(json.dumps(summary, indent=2))
    else:
        print(
            f"[{run_id}] Verified {verified_count}/{len(processed_articles)} new tutorials "
            f"({len(all_verified)} total live on static canonical hub)."
        )
        for item in processed_articles:
            devto_state = item.get("devto", {}).get("status", "local_bundle_ready")
            print(
                f"  - [Day {item['series_day']}/30 | {item['status']}] {item['slug']} ({item['word_count']} words | dev.to: {devto_state})"
            )
        print(f"Static site built at: {site_summary['index_html']}")
    return summary


def cmd_run_daily(
    config: AppConfig,
    *,
    date_str: Optional[str] = None,
    offline: bool = False,
    force_devto: bool = False,
    as_json: bool = False,
) -> dict[str, Any]:
    """Execute daily Blogger + Pinterest automation (+ Mon/Wed/Fri DEV.to) for the 30-day experiment."""
    run_id = f"daily_{uuid.uuid4().hex[:12]}"
    started_at = utc_now_iso()
    effective_date = (date_str or colombo_today_iso()).strip()
    day_num = compute_series_day_for_date(effective_date, config.experiment_start_date)

    db = PipelineDatabase(config.db_path)
    product_info = _load_or_build_products(config, force_rebuild=False)
    discovered = harvest_signals(offline=offline)
    db.upsert_signals(discovered)

    saved = db.get_article_by_series_day(day_num)
    composed_fresh = False
    if saved is None:
        candidates = db.select_unused_signals(limit=1)
        if candidates:
            sig = candidates[0]
            draft_record = compose_article(sig, config, series_day=day_num)
            verified_record, _ = verify_article(draft_record, config=config)
            saved = db.save_article(verified_record)
            db.mark_signal_used(sig.fingerprint)
            composed_fresh = True

    blogger_res: dict[str, Any] = {"status": "no_article_available"}
    pinterest_res: dict[str, Any] = {"status": "no_article_available"}
    devto_res: dict[str, Any] = {"status": "no_article_available"}

    if saved is not None and saved.status in {"code_verified", "staged_draft", "published_live"}:
        all_verified_pre = db.list_articles(verified_only=True)
        blogger_client = BloggerClient(config)
        blogger_res = blogger_client.stage_or_publish(saved, series_articles=all_verified_pre)
        saved = db.save_article(saved)

        pinterest_client = PinterestClient(config)
        pinterest_res = pinterest_client.render_and_publish(saved)
        saved = db.save_article(saved)

        export_article_bundle(saved, config, series_articles=db.list_articles(verified_only=True))

        if force_devto or is_mon_wed_fri(effective_date):
            devto_res = DevtoClient(config).stage_or_publish(saved)
            saved = db.save_article(saved)
        else:
            devto_res = {
                "status": "skipped_non_mwf_cadence",
                "reason": f"{effective_date} is outside Mon/Wed/Fri DEV.to cadence (pass --force-devto to override).",
            }

    store_sync_res = sync_storefronts(config, db)
    all_verified = db.list_articles(verified_only=True)
    for art in all_verified:
        BloggerClient(config).export_artifacts(art, series_articles=all_verified)

    exp_artifacts = write_experiment_artifacts(db, config, as_of=effective_date)
    site_summary = build_static_site(
        all_verified,
        product_info["products"],
        config,
        experiment_report=exp_artifacts["report"],
    )

    finished_at = utc_now_iso()
    staged_n = int(
        sum(
            1
            for r in (blogger_res, pinterest_res, devto_res)
            if r.get("status") in {"staged_draft", "published_live"}
        )
    )
    db.record_publish_run(
        run_id=run_id,
        started_at=started_at,
        finished_at=finished_at,
        mode=f"daily:{effective_date}",
        articles_verified=1 if (saved and saved.status != "verification_failed") else 0,
        articles_staged=staged_n,
        status="ok" if (saved is None or saved.status != "verification_failed") else "verification_failed",
        details={
            "date": effective_date,
            "series_day": day_num,
            "slug": saved.slug if saved else None,
            "composed_fresh": composed_fresh,
        },
    )

    result = {
        "run_id": run_id,
        "date": effective_date,
        "series_day": day_num,
        "composed_fresh": composed_fresh,
        "article": {
            "slug": saved.slug,
            "title": saved.title,
            "status": saved.status,
            "word_count": saved.word_count,
        }
        if saved
        else None,
        "blogger": blogger_res,
        "pinterest": pinterest_res,
        "devto": devto_res,
        "storefronts": store_sync_res,
        "site": site_summary,
        "experiment_report_json": exp_artifacts["json_path"],
        "experiment_report_md": exp_artifacts["markdown_path"],
    }
    if as_json:
        print(json.dumps(result, indent=2))
    else:
        slug_lbl = saved.slug if saved else "none"
        print(f"[{run_id}] Day {day_num}/30 ({effective_date}) -> {slug_lbl}")
        print(f"  - Blogger   : {blogger_res.get('status')}")
        print(f"  - Pinterest : {pinterest_res.get('status')} (RSS: {site_summary['pinterest_feed_xml']})")
        print(f"  - DEV.to    : {devto_res.get('status')}")
        print(f"  - 30d Report: {exp_artifacts['markdown_path']}")
    return result


def cmd_sync_stores(
    config: AppConfig,
    *,
    create_gumroad_drafts: bool = False,
    as_json: bool = False,
) -> dict[str, Any]:
    db = PipelineDatabase(config.db_path)
    catalog_products = load_catalog(config.project_root).get("products", [])
    sync_res = sync_storefronts(
        config,
        db,
        create_gumroad_drafts=create_gumroad_drafts,
        catalog_products=catalog_products,
    )
    exp_artifacts = write_experiment_artifacts(db, config)
    product_info = _load_or_build_products(config, force_rebuild=False)
    all_verified = db.list_articles(verified_only=True)
    build_static_site(
        all_verified,
        product_info["products"],
        config,
        experiment_report=exp_artifacts["report"],
    )
    out = {
        **sync_res,
        "experiment_report_json": exp_artifacts["json_path"],
        "storefront_meters": exp_artifacts["report"]["storefronts"],
    }
    if as_json:
        print(json.dumps(out, indent=2))
    else:
        print(f"Storefront Sync ({out['synced_at']}):")
        print(f"  - Gumroad      : {out['gumroad']['status']} ({out['gumroad']['orders_synced']} orders)")
        print(f"  - LemonSqueezy : {out['lemonsqueezy']['status']} ({out['lemonsqueezy']['orders_synced']} orders)")
    return out


def cmd_log_metrics(
    config: AppConfig,
    *,
    slug: str,
    channel: str,
    views: Optional[int] = None,
    clicks: Optional[int] = None,
    as_json: bool = False,
) -> dict[str, Any]:
    db = PipelineDatabase(config.db_path)
    rec = db.upsert_channel_metrics(slug=slug, channel=channel, views=views, clicks=clicks)
    exp_artifacts = write_experiment_artifacts(db, config)
    product_info = _load_or_build_products(config, force_rebuild=False)
    all_verified = db.list_articles(verified_only=True)
    build_static_site(
        all_verified,
        product_info["products"],
        config,
        experiment_report=exp_artifacts["report"],
    )
    out = {
        "metric": rec.to_dict(),
        "channel_verdict": exp_artifacts["report"]["channels"][rec.channel]["verdict"],
        "experiment_report_json": exp_artifacts["json_path"],
    }
    if as_json:
        print(json.dumps(out, indent=2))
    else:
        print(
            f"Logged {rec.channel} metrics for '{rec.slug}': views={rec.views}, clicks={rec.clicks} "
            f"-> Verdict: {out['channel_verdict']}"
        )
    return out


def cmd_report_30d(
    config: AppConfig,
    *,
    as_of: Optional[str] = None,
    as_json: bool = False,
) -> dict[str, Any]:
    db = PipelineDatabase(config.db_path)
    exp_artifacts = write_experiment_artifacts(db, config, as_of=as_of)
    product_info = _load_or_build_products(config, force_rebuild=False)
    all_verified = db.list_articles(verified_only=True)
    build_static_site(
        all_verified,
        product_info["products"],
        config,
        experiment_report=exp_artifacts["report"],
    )
    if as_json:
        print(json.dumps(exp_artifacts["report"], indent=2))
    else:
        print(render_experiment_markdown(exp_artifacts["report"]))
    return exp_artifacts["report"]


def cmd_auth_blogger(
    config: AppConfig,
    *,
    timeout_seconds: float = 120.0,
    open_browser: bool = True,
    as_json: bool = False,
) -> dict[str, Any]:
    res = run_blogger_pkce_loopback(
        config,
        timeout_seconds=timeout_seconds,
        open_browser=open_browser,
    )
    if as_json:
        print(json.dumps(res, indent=2))
    else:
        print("Blogger OAuth2 PKCE authorization succeeded! Add this line to your .env:")
        print(f"BLOGGER_REFRESH_TOKEN={res['refresh_token']}")
    return res


def cmd_push_devto(
    config: AppConfig,
    *,
    slug: str,
    live: bool = False,
    as_json: bool = False,
) -> dict[str, Any]:
    db = PipelineDatabase(config.db_path)
    article = db.get_article(slug)
    if article is None:
        raise DevtoPublishError(f"No article found in SQLite ledger with slug '{slug}'")

    client = DevtoClient(config)
    mode_override = "live" if live else config.publish_mode
    result = client.stage_or_publish(article, mode_override=mode_override)
    db.save_article(article)

    if as_json:
        print(json.dumps(result, indent=2))
    else:
        print(f"DEV.to push result for '{slug}': {result['status']}")
        if result.get("devto_url"):
            print(f"  URL: {result['devto_url']}")
        else:
            print(f"  Payload: {result.get('payload_path')}")
    return result


def gather_status(config: AppConfig) -> dict[str, Any]:
    db = PipelineDatabase(config.db_path)
    counts = db.summary_counts()
    articles = db.list_articles(verified_only=False)
    exp_report = build_experiment_report(db, config)

    manifest_path = config.dist_dir / "manifest.json"
    products: list[dict[str, Any]] = []
    if manifest_path.is_file():
        products = json.loads(manifest_path.read_text(encoding="utf-8")).get("products", [])

    return {
        "author": config.author_name,
        "timezone": config.timezone,
        "schedule": "Daily 18:30 Asia/Colombo (Blogger + Pinterest) & Mon/Wed/Fri (DEV.to)",
        "experiment_window": f"{config.experiment_start_date} -> {config.experiment_end_date}",
        "publish_mode": config.publish_mode,
        "blogger_publish_mode": config.blogger_publish_mode,
        "devto_api_key_configured": bool(config.devto_api_key),
        "blogger_credentials_configured": BloggerClient(config).has_live_credentials(),
        "pinterest_credentials_configured": PinterestClient(config).has_live_credentials(),
        "lemonsqueezy_api_configured": bool(config.lemonsqueezy_api_key),
        "gumroad_api_configured": bool(config.gumroad_access_token),
        "kofi_profile_url": config.kofi_profile_url,
        "primary_store_url": config.primary_store_url,
        "mirror_store_url": config.mirror_store_url,
        "lemonsqueezy_store_url": config.lemonsqueezy_store_url,
        "gumroad_store_url": config.gumroad_store_url,
        "free_checkout_url": config.free_checkout_url,
        "paid_checkout_url": config.paid_checkout_url,
        "canonical_base_url": config.canonical_base_url,
        "counts": counts,
        "products": products,
        "experiment": exp_report,
        "articles": [
            {
                "slug": a.slug,
                "series_day": a.series_day,
                "pillar": a.pillar,
                "scheduled_slot": a.scheduled_slot,
                "title": a.title,
                "status": a.status,
                "word_count": a.word_count,
                "canonical_url": a.canonical_url,
                "devto_article_id": a.devto_article_id,
                "devto_url": a.devto_url,
                "blogger_post_id": a.blogger_post_id,
                "blogger_url": a.blogger_url,
                "pinterest_pin_id": a.pinterest_pin_id,
                "pinterest_url": a.pinterest_url,
                "duration_ms": a.verification.get("total_duration_ms", 0),
                "code_blocks_tested": a.verification.get("code_blocks_tested", 0),
            }
            for a in articles
        ],
    }


def cmd_status(config: AppConfig, *, as_json: bool = False) -> dict[str, Any]:
    info = gather_status(config)
    if as_json:
        print(json.dumps(info, indent=2))
    else:
        print(f"=== Omnichannel Verified Autopilot ({info['author']}) ===")
        print(f"Experiment     : {info['experiment_window']} ({info['timezone']})")
        print(f"Schedule       : {info['schedule']}")
        print(f"Free Checkout  : {info['free_checkout_url']}")
        print(f"Paid Checkout  : {info['paid_checkout_url']}")
        print(
            f"Ledger Counts  : {info['counts']['articles_verified']}/30 verified articles / "
            f"{info['counts']['signals_unused']} unused signals queued"
        )
        print(f"Built Products : {len(info['products'])} .zip kits in products/dist/")
        for a in info["articles"]:
            day_tag = f"Day {a['series_day']}/30" if a.get("series_day") else a["scheduled_slot"]
            print(f"  * [{day_tag} | {a['status']}] {a['slug']} ({a['word_count']} words)")
    return info


def render_dashboard_html(config: AppConfig) -> str:
    """Render the zero-dependency accessible Omnichannel Operator Dashboard (zero raw backticks!)."""
    status_data = gather_status(config)
    exp = status_data["experiment"]
    ls_meter = exp["storefronts"]["lemonsqueezy"]
    gr_meter = exp["storefronts"]["gumroad"]
    copy_map: dict[str, str] = {}

    storefront_md_path = config.exports_dir / "storefront" / "kofi_profile_and_shop_copy.md"
    copy_map["storefront_guide"] = (
        storefront_md_path.read_text(encoding="utf-8")
        if storefront_md_path.is_file()
        else "Run python3 autopilot.py build-products to generate storefront copy."
    )
    dual_md_path = config.exports_dir / "storefront" / "lemonsqueezy_and_gumroad_setup.md"
    copy_map["dual_storefront_guide"] = (
        dual_md_path.read_text(encoding="utf-8")
        if dual_md_path.is_file()
        else "Run python3 autopilot.py build-products to generate Lemon Squeezy & Gumroad setup copy."
    )

    prod_cards: list[str] = []
    for p in status_data["products"]:
        is_free = int(p["price_usd"]) == 0
        price_str = "$0+ PWYW (Gumroad Lead Magnet)" if is_free else f"${p['price_usd']} USD Flagship (Lemon Squeezy)"
        fname = str(p["zip_filename"])
        prod_cards.append(
            f"""        <div class="card">
          <div>
            <div class="meta-row">
              <span class="badge badge-verified">TESTS PASSED</span>
              <span class="badge tabular">{html.escape(price_str)}</span>
            </div>
            <h3>{html.escape(str(p["title"]))}</h3>
            <p class="tabular">Size: <strong>{int(p["size_bytes"]):,} bytes</strong> · Files: <strong>{int(p["file_count"])}</strong> · SHA-256: <code>{html.escape(str(p["sha256"])[:16])}…</code></p>
          </div>
          <div class="cta-row">
            <a class="btn btn-primary" href="/dist/{html.escape(fname, quote=True)}" download>Download {html.escape(fname)}</a>
          </div>
        </div>"""
        )

    channel_rows: list[str] = []
    for ch_key, ch_data in exp["channels"].items():
        v_txt = str(ch_data["verdict"])
        v_cls = (
            "badge badge-verified"
            if v_txt.startswith("WORKING")
            else ("badge badge-warn" if v_txt.startswith("BOTTLENECK") else "badge")
        )
        channel_rows.append(
            f"<tr>"
            f"<td><code>{html.escape(ch_key)}</code></td>"
            f'<td class="tabular">{ch_data["posts_prepared"]}</td>'
            f'<td class="tabular">{ch_data["views"]}</td>'
            f'<td class="tabular">{ch_data["clicks"]}</td>'
            f'<td class="tabular">{ch_data["ctr_pct"]}%</td>'
            f'<td class="tabular">{ch_data["free_downloads"]}</td>'
            f'<td class="tabular">{ch_data["paid_orders"]}</td>'
            f'<td class="tabular">${ch_data["net_usd"]:.2f}</td>'
            f'<td><span class="{v_cls}">{html.escape(v_txt)}</span></td>'
            f"</tr>"
        )

    article_cards: list[str] = []
    for idx, art in enumerate(reversed(status_data["articles"]), start=1):
        slug = str(art["slug"])
        day_num = art.get("series_day") or (len(status_data["articles"]) - idx + 1)
        bundles = load_bundle_texts(config.exports_dir, slug)
        copy_map[f"{slug}:blogger"] = bundles.get("blogger_html", "")
        copy_map[f"{slug}:pinterest"] = bundles.get("pinterest_json", "")
        copy_map[f"{slug}:kofi"] = bundles.get("kofi_post", "")
        copy_map[f"{slug}:medium"] = bundles.get("medium_import", "")
        copy_map[f"{slug}:substack"] = bundles.get("substack_post", "")
        copy_map[f"{slug}:linkedin"] = bundles.get("linkedin_snippet", "")
        copy_map[f"{slug}:canonical"] = str(art["canonical_url"])

        meta = PILLAR_META.get(str(art["pillar"]), PILLAR_META["pillar_a"])
        blogger_state = "LIVE/DRAFT SYNCED" if art.get("blogger_post_id") else "HTML EXPORTED"
        pin_state = "PINNED API v5" if art.get("pinterest_pin_id") else "PNG + RSS READY"
        article_cards.append(
            f"""        <article class="card">
          <div>
            <div class="meta-row">
              <span class="badge badge-verified">DAY {day_num}/30 · {html.escape(str(art["status"]).upper())} ({art["duration_ms"]} ms)</span>
              <span class="badge">Blogger: {blogger_state}</span>
              <span class="badge">Pinterest: {pin_state}</span>
              <span class="badge">{html.escape(meta["weekday"])}</span>
              <span class="tabular">{art["word_count"]} words · {art["code_blocks_tested"]} blocks</span>
            </div>
            <h3><a href="/site/posts/{html.escape(slug, quote=True)}/index.html" style="color:var(--text);text-decoration:none;">{html.escape(str(art["title"]))}</a></h3>
            <p class="tabular">Canonical URL: <code>{html.escape(str(art["canonical_url"]))}</code></p>
          </div>
          <div>
            <a class="pin-thumb-link" href="/site/pins/{html.escape(slug, quote=True)}.png" aria-label="View 1000x1500 Pinterest PNG for {html.escape(str(art['title']), quote=True)}">
              <img class="pin-thumb" src="/site/pins/{html.escape(slug, quote=True)}.png" width="180" height="270" loading="lazy" alt="2:3 Pinterest infographic preview for {html.escape(str(art['title']), quote=True)}" />
            </a>
          </div>
          <div class="cta-row" role="group" aria-label="Syndication actions for {html.escape(str(art['title']), quote=True)}">
            <a class="btn btn-primary" href="/site/posts/{html.escape(slug, quote=True)}/index.html">Preview Post</a>
            <a class="btn" href="/site/pins/{html.escape(slug, quote=True)}.png">Open 2:3 Pin PNG</a>
            <button type="button" class="btn copy-pack-btn" data-copy-key="{html.escape(slug, quote=True)}:blogger" data-label="Copy Blogger Series HTML">Copy Blogger Series HTML</button>
            <button type="button" class="btn copy-pack-btn" data-copy-key="{html.escape(slug, quote=True)}:pinterest" data-label="Copy Pin Metadata">Copy Pin Metadata</button>
            <button type="button" class="btn copy-pack-btn" data-copy-key="{html.escape(slug, quote=True)}:kofi" data-label="Copy Ko-fi Post">Copy Ko-fi Post</button>
            <button type="button" class="btn copy-pack-btn" data-copy-key="{html.escape(slug, quote=True)}:medium" data-label="Copy Medium MD">Copy Medium MD</button>
            <button type="button" class="btn copy-pack-btn" data-copy-key="{html.escape(slug, quote=True)}:substack" data-label="Copy Substack MD">Copy Substack MD</button>
            <button type="button" class="btn copy-pack-btn" data-copy-key="{html.escape(slug, quote=True)}:linkedin" data-label="Copy LinkedIn">Copy LinkedIn</button>
            <button type="button" class="btn copy-pack-btn" data-copy-key="{html.escape(slug, quote=True)}:canonical" data-label="Copy Canonical URL">Copy Canonical URL</button>
          </div>
        </article>"""
        )

    embedded_json = _safe_json_for_html_script(copy_map)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Omnichannel Control Center — Lemon Squeezy, Gumroad, Blogger &amp; Pinterest</title>
  <style>
{SITE_STYLESHEET}
  .kpi-strip {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 200px), 1fr));
    gap: 1rem;
    margin-top: 1.25rem;
  }}
  .kpi-box {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 0.65rem;
    padding: 1rem;
    min-width: 0;
    max-width: 100%;
    overflow-wrap: anywhere;
  }}
  .kpi-label {{
    font-size: 0.8rem;
    font-weight: 700;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }}
  .kpi-val {{
    margin-top: 0.3rem;
    font-size: 1.35rem;
    font-weight: 800;
    font-variant-numeric: tabular-nums;
  }}
  .stack-list {{
    display: flex;
    flex-direction: column;
    gap: 1.25rem;
  }}
  .copy-pack-btn[data-copied="true"] {{
    background: var(--badge-ok-bg);
    color: var(--badge-ok-fg);
    border-color: var(--accent);
  }}
  </style>
</head>
<body>
  <a class="skip-link" href="#main-content">Skip to main content</a>
  <div id="copy-announcer" class="sr-only" role="status" aria-live="polite" aria-atomic="true"></div>
  <header class="site-header">
    <div class="container header-inner">
      <a class="brand-link" href="/">{html.escape(config.author_name)} · Omnichannel Autopilot</a>
      <nav aria-label="Dashboard navigation">
        <ul class="nav-list">
          <li><a class="nav-link" href="/site/index.html">Canonical Hub</a></li>
          <li><a class="nav-link" href="/site/pinterest_feed.xml">Pinterest RSS</a></li>
          <li><a class="nav-link" href="/site/feed.xml">Article RSS</a></li>
          <li><a class="nav-link" href="/api/experiment">30d JSON</a></li>
          <li><a class="nav-link" href="/api/status">Status JSON</a></li>
          <li><button type="button" id="theme-toggle" class="btn" aria-pressed="false" aria-label="Switch color theme">Dark Theme</button></li>
        </ul>
      </nav>
    </div>
  </header>

  <main id="main-content">
    <section class="hero">
      <div class="container">
        <span class="badge badge-verified">30-DAY EXPERIMENT · NOV 18 → DEC 18, 2026</span>
        <h1>{html.escape(SERIES_30D_NAME)}</h1>
        <p>Automated Lemon Squeezy ($50 Sri Lanka wire) + Gumroad ($100 bank deposit) storefronts paired with daily Blogger series publishing, daily 1000x1500 Pinterest 2:3 pins, and Mon/Wed/Fri DEV.to syndication.</p>
        <div class="cta-row">
          <button type="button" class="btn btn-primary copy-pack-btn" data-copy-key="dual_storefront_guide" data-label="Copy Lemon Squeezy & Gumroad Setup">Copy Lemon Squeezy &amp; Gumroad Setup</button>
          <button type="button" class="btn copy-pack-btn" data-copy-key="storefront_guide" data-label="Copy Ko-fi Profile & Shop Copy">Copy Ko-fi Profile &amp; Shop Copy</button>
          <a class="btn" href="/site/pinterest_feed.xml">Open Pinterest Auto-Publish RSS</a>
          <a class="btn" href="/site/index.html">Preview Static Hub</a>
        </div>
        <div class="kpi-strip">
          <div class="kpi-box">
            <div class="kpi-label">30-Day Curriculum</div>
            <div class="kpi-val tabular">{status_data["counts"]["articles_verified"]} / 30 Verified</div>
          </div>
          <div class="kpi-box">
            <div class="kpi-label">Queued Unused Seeds</div>
            <div class="kpi-val tabular">{status_data["counts"]["signals_unused"]}</div>
          </div>
          <div class="kpi-box">
            <div class="kpi-label">Lemon Squeezy ($50 Wire)</div>
            <div class="kpi-val tabular">${ls_meter["net_usd"]:.2f} ({ls_meter["unlock_progress_pct"]:.1f}%)</div>
          </div>
          <div class="kpi-box">
            <div class="kpi-label">Gumroad ($100 Bank)</div>
            <div class="kpi-val tabular">${gr_meter["net_usd"]:.2f} ({gr_meter["unlock_progress_pct"]:.1f}%)</div>
          </div>
        </div>
      </div>
    </section>

    <section class="section-block" aria-labelledby="scoreboard-heading">
      <div class="container">
        <h2 id="scoreboard-heading" class="section-title">1. 30-Day Experiment Scoreboard (Nov 18 → Dec 18, 2026)</h2>
        <div class="grid-cards">
          <div class="card">
            <div>
              <div class="meta-row">
                <span class="badge badge-verified">LEMON SQUEEZY ($14 FLAGSHIP)</span>
                <span class="badge tabular">${ls_meter["net_usd"]:.2f} / $50.00</span>
              </div>
              <h3>Sri Lanka Bank Wire Unlock Meter</h3>
              <div class="meter-track" role="progressbar" aria-label="Lemon Squeezy payout unlock progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="{int(round(ls_meter['unlock_progress_pct']))}">
                <div class="meter-fill" style="width:{min(100.0, max(0.0, float(ls_meter['unlock_progress_pct']))):.1f}%;"></div>
              </div>
              <p class="tabular">Paid orders: <strong>{ls_meter["paid_orders"]}</strong> · Remaining to unlock wire: <strong>${ls_meter["remaining_to_unlock_usd"]:.2f} USD</strong></p>
            </div>
          </div>
          <div class="card">
            <div>
              <div class="meta-row">
                <span class="badge badge-verified">GUMROAD ($0+ LEAD MAGNET &amp; MIRROR)</span>
                <span class="badge tabular">${gr_meter["net_usd"]:.2f} / $100.00</span>
              </div>
              <h3>Sri Lanka Local Bank Deposit Meter</h3>
              <div class="meter-track" role="progressbar" aria-label="Gumroad payout unlock progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="{int(round(gr_meter['unlock_progress_pct']))}">
                <div class="meter-fill" style="width:{min(100.0, max(0.0, float(gr_meter['unlock_progress_pct']))):.1f}%;"></div>
              </div>
              <p class="tabular">Free leads: <strong>{gr_meter["free_downloads"]}</strong> · Paid orders: <strong>{gr_meter["paid_orders"]}</strong> · Remaining: <strong>${gr_meter["remaining_to_unlock_usd"]:.2f} USD</strong></p>
            </div>
          </div>
        </div>
        <div class="table-scroll">
          <table>
            <thead>
              <tr>
                <th scope="col">Channel</th>
                <th scope="col">Assets</th>
                <th scope="col">Views</th>
                <th scope="col">Clicks</th>
                <th scope="col">CTR</th>
                <th scope="col">Free Kits</th>
                <th scope="col">Paid ($14)</th>
                <th scope="col">Net USD</th>
                <th scope="col">Deterministic Month-2 Verdict</th>
              </tr>
            </thead>
            <tbody>
              {"".join(channel_rows)}
            </tbody>
          </table>
        </div>
      </div>
    </section>

    <section class="section-block" aria-labelledby="skus-heading">
      <div class="container">
        <h2 id="skus-heading" class="section-title">2. Dual Storefront <code>.zip</code> Toolkits (Lemon Squeezy + Gumroad)</h2>
        <div class="grid-cards">
{chr(10).join(prod_cards)}
        </div>
      </div>
    </section>

    <section class="section-block" aria-labelledby="syndication-heading">
      <div class="container">
        <h2 id="syndication-heading" class="section-title">3. Daily Verified Series, 2:3 Pinterest Pins &amp; 1-Click Packs</h2>
        <div class="stack-list">
{chr(10).join(article_cards) if article_cards else '<p>No articles generated yet. Run <code>python3 autopilot.py run-daily</code>.</p>'}
        </div>
      </div>
    </section>
  </main>

  <footer class="site-footer">
    <div class="container">
      <p>Free Checkout: <code>{html.escape(config.free_checkout_url)}</code> · Paid Checkout: <code>{html.escape(config.paid_checkout_url)}</code> · Timezone: <code>{html.escape(config.timezone)}</code></p>
    </div>
  </footer>

  <script id="copy-bundle-data" type="application/json">{embedded_json}</script>
  <script>
  (function () {{
    var root = document.documentElement;
    var saved = null;
    try {{ saved = localStorage.getItem('autopilot_theme'); }} catch (e) {{}}
    if (saved === 'dark' || saved === 'light') {{
      root.setAttribute('data-theme', saved);
    }}
    var toggle = document.getElementById('theme-toggle');
    function syncThemeBtn() {{
      if (!toggle) return;
      var isDark = root.getAttribute('data-theme') === 'dark' ||
        (!root.getAttribute('data-theme') && window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
      toggle.setAttribute('aria-pressed', isDark ? 'true' : 'false');
      toggle.textContent = isDark ? 'Light Theme' : 'Dark Theme';
    }}
    syncThemeBtn();
    if (toggle) {{
      toggle.addEventListener('click', function () {{
        var cur = root.getAttribute('data-theme');
        var sysDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
        var next = cur ? (cur === 'dark' ? 'light' : 'dark') : (sysDark ? 'light' : 'dark');
        root.setAttribute('data-theme', next);
        try {{ localStorage.setItem('autopilot_theme', next); }} catch (e) {{}}
        syncThemeBtn();
      }});
    }}

    var dataEl = document.getElementById('copy-bundle-data');
    var copyMap = dataEl ? JSON.parse(dataEl.textContent || '{{}}') : {{}};
    var announcer = document.getElementById('copy-announcer');

    document.addEventListener('click', function (ev) {{
      var btn = ev.target.closest('.copy-pack-btn');
      if (!btn) return;
      var key = btn.getAttribute('data-copy-key') || '';
      var payload = copyMap[key] || '';
      var origLabel = btn.getAttribute('data-label') || btn.textContent;

      function confirmCopied() {{
        btn.textContent = 'Copied!';
        btn.setAttribute('data-copied', 'true');
        if (announcer) {{
          announcer.textContent = origLabel + ' copied to clipboard.';
        }}
        setTimeout(function () {{
          btn.textContent = origLabel;
          btn.removeAttribute('data-copied');
        }}, 2000);
      }}

      if (navigator.clipboard && navigator.clipboard.writeText) {{
        navigator.clipboard.writeText(payload).then(confirmCopied).catch(confirmCopied);
      }} else {{
        confirmCopied();
      }}
    }});
  }})();
  </script>
</body>
</html>
"""


def create_dashboard_handler(config: AppConfig) -> type[BaseHTTPRequestHandler]:
    class DashboardHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def _send_bytes(
            self,
            status: int,
            content_type: str,
            body: bytes,
            extra_headers: Optional[dict[str, str]] = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            if extra_headers:
                for k, v in extra_headers.items():
                    self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            route = unquote(parsed.path)

            if route in {"/", "/index.html"}:
                body = render_dashboard_html(config).encode("utf-8")
                self._send_bytes(HTTPStatus.OK, "text/html; charset=utf-8", body)
                return

            if route == "/api/status":
                body = json.dumps(gather_status(config), indent=2).encode("utf-8")
                self._send_bytes(HTTPStatus.OK, "application/json; charset=utf-8", body)
                return

            if route == "/api/experiment":
                db = PipelineDatabase(config.db_path)
                body = json.dumps(build_experiment_report(db, config), indent=2).encode("utf-8")
                self._send_bytes(HTTPStatus.OK, "application/json; charset=utf-8", body)
                return

            if route.startswith("/dist/"):
                rel = route[len("/dist/") :].lstrip("/")
                target = (config.dist_dir / rel).resolve()
                if not target.is_relative_to(config.dist_dir.resolve()) or not target.is_file():
                    self._send_bytes(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"Not found")
                    return
                self._send_bytes(
                    HTTPStatus.OK,
                    "application/zip",
                    target.read_bytes(),
                    {"Content-Disposition": f'attachment; filename="{target.name}"'},
                )
                return

            if route == "/site":
                route = "/site/index.html"

            if route.startswith("/site/"):
                rel = route[len("/site/") :].lstrip("/") or "index.html"
                candidate = (config.site_dir / rel).resolve()
                if candidate.is_dir():
                    candidate = (candidate / "index.html").resolve()
                if not candidate.is_relative_to(config.site_dir.resolve()) or not candidate.is_file():
                    self._send_bytes(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"Not found")
                    return
                guessed, _ = mimetypes.guess_type(str(candidate))
                ctype = guessed or "application/octet-stream"
                if ctype.startswith("text/") or ctype in {"application/xml", "application/rss+xml"}:
                    ctype = f"{ctype}; charset=utf-8"
                self._send_bytes(HTTPStatus.OK, ctype, candidate.read_bytes())
                return

            self._send_bytes(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"Not found")

    return DashboardHandler


def cmd_serve(config: AppConfig, *, host: str = "127.0.0.1", port: int = 8765) -> None:
    handler_cls = create_dashboard_handler(config)
    server = ThreadingHTTPServer((host, port), handler_cls)
    actual_host, actual_port = server.server_address[:2]
    print(f"Autopilot Review Dashboard listening on http://{actual_host}:{actual_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autopilot.py",
        description="Omnichannel Lemon Squeezy, Gumroad, Blogger, Pinterest & DEV.to Autopilot CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build-products", help="Unit-test and package deterministic .zip kits")
    p_build.add_argument("--json", action="store_true", help="Emit JSON output")

    p_run = sub.add_parser("run-pipeline", help="Harvest signals, compose, sandbox-verify, build site & stage")
    p_run.add_argument("--limit", type=int, default=3, help="Max new tutorials to verify in this run")
    p_run.add_argument("--pillar", choices=["a", "b", "c"], default=None, help="Optional single pillar filter")
    p_run.add_argument("--push-draft", action="store_true", help="Stage verified tutorials via DEV.to API (or dry-run)")
    p_run.add_argument("--offline", action="store_true", help="Use deterministic pillar seeds only (skip HTTP trend fetch)")
    p_run.add_argument("--json", action="store_true", help="Emit JSON summary")

    p_daily = sub.add_parser(
        "run-daily",
        help="Daily automation: Blogger series + 1000x1500 Pinterest pin (+ Mon/Wed/Fri DEV.to) + 30d report",
    )
    p_daily.add_argument("--date", default=None, help="Override Asia/Colombo date (YYYY-MM-DD)")
    p_daily.add_argument("--offline", action="store_true", help="Use deterministic 30-day seeds only")
    p_daily.add_argument("--force-devto", action="store_true", help="Also stage/publish DEV.to on non-Mon/Wed/Fri dates")
    p_daily.add_argument("--json", action="store_true", help="Emit JSON summary")

    p_sync = sub.add_parser("sync-stores", help="Sync Gumroad v2 & Lemon Squeezy v1 orders + UTM attribution")
    p_sync.add_argument("--create-gumroad-drafts", action="store_true", help="Create unpublished Gumroad v2 draft SKUs if missing")
    p_sync.add_argument("--json", action="store_true", help="Emit JSON output")

    p_metrics = sub.add_parser("log-metrics", help="Record per-article channel views & clicks for 30d experiment verdicts")
    p_metrics.add_argument("--slug", required=True, help="Article slug")
    p_metrics.add_argument("--channel", required=True, choices=list(VALID_CHANNELS), help="Traffic channel")
    p_metrics.add_argument("--views", type=int, default=None, help="Total views/impressions on channel")
    p_metrics.add_argument("--clicks", type=int, default=None, help="Total outbound/storefront clicks on channel")
    p_metrics.add_argument("--json", action="store_true", help="Emit JSON output")

    p_rep = sub.add_parser("report-30d", help="Compute 30-day experiment KPIs, payout meters & deterministic verdicts")
    p_rep.add_argument("--as-of", default=None, help="Evaluation date YYYY-MM-DD (default: today in Asia/Colombo)")
    p_rep.add_argument("--json", action="store_true", help="Emit JSON output")

    p_auth = sub.add_parser("auth-blogger", help="Run loopback PKCE OAuth2 consent for Google Blogger API v3")
    p_auth.add_argument("--timeout", type=float, default=120.0, help="Loopback callback timeout in seconds (default: 120)")
    p_auth.add_argument("--no-browser", action="store_true", help="Print OAuth URL without calling webbrowser.open")
    p_auth.add_argument("--json", action="store_true", help="Emit JSON output")

    p_push = sub.add_parser("push-devto", help="Push a verified article slug to DEV.to (POST or idempotent PUT)")
    p_push.add_argument("--slug", required=True, help="Article slug in state/pipeline.db")
    p_push.add_argument("--live", action="store_true", help="Override PUBLISH_MODE and publish live immediately")
    p_push.add_argument("--json", action="store_true", help="Emit JSON output")

    p_status = sub.add_parser("status", help="Show SQLite ledger, product artifacts, and staging health")
    p_status.add_argument("--json", action="store_true", help="Emit JSON output")

    p_serve = sub.add_parser("serve", help="Start localhost review & 1-click copy dashboard")
    p_serve.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    p_serve.add_argument("--port", type=int, default=8765, help="Bind port (default: 8765)")

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    config = AppConfig.load()

    try:
        if args.command == "build-products":
            cmd_build_products(config, as_json=args.json)
            return 0
        if args.command == "run-pipeline":
            res = cmd_run_pipeline(
                config,
                limit=args.limit,
                pillar=args.pillar,
                push_draft=args.push_draft,
                offline=args.offline,
                as_json=args.json,
            )
            failed = [
                a for a in res["articles_processed"] if a.get("status") == "verification_failed"
            ]
            return 1 if failed else 0
        if args.command == "run-daily":
            res_daily = cmd_run_daily(
                config,
                date_str=args.date,
                offline=args.offline,
                force_devto=args.force_devto,
                as_json=args.json,
            )
            art_info = res_daily.get("article") or {}
            return 1 if art_info.get("status") == "verification_failed" else 0
        if args.command == "sync-stores":
            cmd_sync_stores(
                config,
                create_gumroad_drafts=args.create_gumroad_drafts,
                as_json=args.json,
            )
            return 0
        if args.command == "log-metrics":
            cmd_log_metrics(
                config,
                slug=args.slug,
                channel=args.channel,
                views=args.views,
                clicks=args.clicks,
                as_json=args.json,
            )
            return 0
        if args.command == "report-30d":
            cmd_report_30d(config, as_of=args.as_of, as_json=args.json)
            return 0
        if args.command == "auth-blogger":
            cmd_auth_blogger(
                config,
                timeout_seconds=args.timeout,
                open_browser=not args.no_browser,
                as_json=args.json,
            )
            return 0
        if args.command == "push-devto":
            cmd_push_devto(config, slug=args.slug, live=args.live, as_json=args.json)
            return 0
        if args.command == "status":
            cmd_status(config, as_json=args.json)
            return 0
        if args.command == "serve":
            cmd_serve(config, host=args.host, port=args.port)
            return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
