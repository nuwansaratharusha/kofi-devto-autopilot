#!/usr/bin/env python3
"""Unified CLI & Localhost Review Dashboard for the Ko-fi & DEV.to Content Autopilot."""

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

from engine.composer import compose_article
from engine.config import AppConfig
from engine.db import PipelineDatabase, utc_now_iso
from engine.devto_client import DevtoClient, DevtoPublishError
from engine.exporter import export_article_bundle, load_bundle_texts
from engine.harvester import harvest_signals
from engine.models import PILLAR_META
from engine.site_builder import SITE_STYLESHEET, build_static_site
from engine.verifier import verify_article
from products.builder import build_all_products

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
    if not force_rebuild and manifest_file.is_file():
        data = json.loads(manifest_file.read_text(encoding="utf-8"))
        products = data.get("products", [])
        if products and all(Path(p["zip_path"]).is_file() for p in products):
            return {
                "manifest_path": str(manifest_file),
                "storefront_copy_path": str(
                    config.exports_dir / "storefront" / "kofi_profile_and_shop_copy.md"
                ),
                "products": products,
            }
    return build_all_products(
        config.project_root,
        kofi_profile_url=config.kofi_profile_url,
        primary_store_url=config.primary_store_url,
        mirror_store_url=config.mirror_store_url,
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
        print(f"Storefront copy ready at: {result['storefront_copy_path']}")
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
        draft_record = compose_article(sig, config)
        verified_record, report = verify_article(draft_record, config=config)
        saved = db.save_article(verified_record)
        db.mark_signal_used(sig.fingerprint)

        entry: dict[str, Any] = {
            "slug": saved.slug,
            "pillar": saved.pillar,
            "scheduled_slot": saved.scheduled_slot,
            "title": saved.title,
            "status": saved.status,
            "word_count": saved.word_count,
            "verification": report.to_dict(),
        }

        if report.passed:
            verified_count += 1
            bundle_paths = export_article_bundle(saved, config)
            entry["exports"] = bundle_paths

            if push_draft:
                push_res = devto.stage_or_publish(saved)
                saved = db.save_article(saved)
                entry["devto"] = push_res
                if push_res.get("status") in {"staged_draft", "published_live"}:
                    staged_count += 1
        processed_articles.append(entry)

    all_verified = db.list_articles(verified_only=True)
    site_summary = build_static_site(all_verified, product_info["products"], config)

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
                f"  - [{item['status']}] {item['slug']} ({item['word_count']} words | dev.to: {devto_state})"
            )
        print(f"Static site built at: {site_summary['index_html']}")
    return summary


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

    manifest_path = config.dist_dir / "manifest.json"
    products: list[dict[str, Any]] = []
    if manifest_path.is_file():
        products = json.loads(manifest_path.read_text(encoding="utf-8")).get("products", [])

    return {
        "author": config.author_name,
        "timezone": config.timezone,
        "schedule": "Mon / Wed / Fri at 18:30 Asia/Colombo (13:00 UTC)",
        "publish_mode": config.publish_mode,
        "devto_api_key_configured": bool(config.devto_api_key),
        "kofi_profile_url": config.kofi_profile_url,
        "primary_store_url": config.primary_store_url,
        "mirror_store_url": config.mirror_store_url,
        "canonical_base_url": config.canonical_base_url,
        "counts": counts,
        "products": products,
        "articles": [
            {
                "slug": a.slug,
                "pillar": a.pillar,
                "scheduled_slot": a.scheduled_slot,
                "title": a.title,
                "status": a.status,
                "word_count": a.word_count,
                "canonical_url": a.canonical_url,
                "devto_article_id": a.devto_article_id,
                "devto_url": a.devto_url,
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
        print(f"=== Ko-fi & DEV.to Verified Autopilot ({info['author']}) ===")
        print(f"Schedule       : {info['schedule']}")
        print(f"Primary Store  : {info['primary_store_url']}")
        print(
            f"Mirror Store   : {info['mirror_store_url'] or '(not configured — using Primary Store)'}"
        )
        print(
            f"DEV.to Mode    : {info['publish_mode']} (API key configured: {info['devto_api_key_configured']})"
        )
        print(
            f"Ledger Counts  : {info['counts']['articles_verified']} verified articles / "
            f"{info['counts']['signals_unused']} unused signals queued"
        )
        print(f"Built Products : {len(info['products'])} .zip kits in products/dist/")
        for a in info["articles"]:
            print(f"  * [{a['status']}] {a['slug']} ({a['word_count']} words, {a['scheduled_slot']})")
    return info


def render_dashboard_html(config: AppConfig) -> str:
    """Render the zero-dependency accessible Operator Review & 1-Click Copy Dashboard."""
    status_data = gather_status(config)
    copy_map: dict[str, str] = {}

    storefront_md_path = config.exports_dir / "storefront" / "kofi_profile_and_shop_copy.md"
    if storefront_md_path.is_file():
        copy_map["storefront_guide"] = storefront_md_path.read_text(encoding="utf-8")
    else:
        copy_map["storefront_guide"] = "Run `python3 autopilot.py build-products` to generate storefront copy."

    prod_cards: list[str] = []
    for p in status_data["products"]:
        price_str = "$0+ PWYW (Suggested $3)" if int(p["price_usd"]) == 0 else f"${p['price_usd']} USD Flagship"
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

    article_cards: list[str] = []
    for art in reversed(status_data["articles"]):
        slug = str(art["slug"])
        bundles = load_bundle_texts(config.exports_dir, slug)
        copy_map[f"{slug}:kofi"] = bundles.get("kofi_post", "")
        copy_map[f"{slug}:medium"] = bundles.get("medium_import", "")
        copy_map[f"{slug}:substack"] = bundles.get("substack_post", "")
        copy_map[f"{slug}:linkedin"] = bundles.get("linkedin_snippet", "")
        copy_map[f"{slug}:canonical"] = str(art["canonical_url"])

        meta = PILLAR_META.get(str(art["pillar"]), PILLAR_META["pillar_a"])
        article_cards.append(
            f"""        <article class="card">
          <div>
            <div class="meta-row">
              <span class="badge badge-verified">{html.escape(str(art["status"]).upper())} · {art["duration_ms"]} ms</span>
              <span class="badge">{html.escape(meta["weekday"])} ({html.escape(str(art["scheduled_slot"]))})</span>
              <span class="tabular">{art["word_count"]} words · {art["code_blocks_tested"]} blocks verified</span>
            </div>
            <h3><a href="/site/posts/{html.escape(slug, quote=True)}/index.html" style="color:var(--text);text-decoration:none;">{html.escape(str(art["title"]))}</a></h3>
            <p class="tabular">Canonical URL: <code>{html.escape(str(art["canonical_url"]))}</code></p>
          </div>
          <div class="cta-row" role="group" aria-label="Syndication actions for {html.escape(str(art['title']), quote=True)}">
            <a class="btn btn-primary" href="/site/posts/{html.escape(slug, quote=True)}/index.html">Preview Post</a>
            <button type="button" class="btn copy-pack-btn" data-copy-key="{html.escape(slug, quote=True)}:kofi" data-label="Copy Ko-fi Post">Copy Ko-fi Post</button>
            <button type="button" class="btn copy-pack-btn" data-copy-key="{html.escape(slug, quote=True)}:medium" data-label="Copy Medium MD">Copy Medium MD</button>
            <button type="button" class="btn copy-pack-btn" data-copy-key="{html.escape(slug, quote=True)}:substack" data-label="Copy Substack MD">Copy Substack MD</button>
            <button type="button" class="btn copy-pack-btn" data-copy-key="{html.escape(slug, quote=True)}:linkedin" data-label="Copy LinkedIn">Copy LinkedIn</button>
            <button type="button" class="btn copy-pack-btn" data-copy-key="{html.escape(slug, quote=True)}:canonical" data-label="Copy Canonical URL">Copy Canonical URL</button>
          </div>
        </article>"""
        )

    devto_badge = (
        f"LIVE KEY SET ({status_data['publish_mode'].upper()})"
        if status_data["devto_api_key_configured"]
        else "DRY-RUN READY (Set DEVTO_API_KEY in .env)"
    )

    embedded_json = _safe_json_for_html_script(copy_map)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Operator Review Dashboard — Ko-fi &amp; DEV.to Autopilot</title>
  <style>
{SITE_STYLESHEET}
  .kpi-strip {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 210px), 1fr));
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
      <a class="brand-link" href="/">{html.escape(config.author_name)} · Autopilot Control Center</a>
      <nav aria-label="Dashboard navigation">
        <ul class="nav-list">
          <li><a class="nav-link" href="/site/index.html">Live Static Blog</a></li>
          <li><a class="nav-link" href="/site/feed.xml">RSS 2.0</a></li>
          <li><a class="nav-link" href="/api/status">Status JSON</a></li>
          <li><a class="nav-link btn-primary" href="{html.escape(config.kofi_profile_url, quote=True)}" target="_blank" rel="noopener">Open Ko-fi Page</a></li>
          <li><button type="button" id="theme-toggle" class="btn" aria-pressed="false" aria-label="Switch color theme">Dark Theme</button></li>
        </ul>
      </nav>
    </div>
  </header>

  <main id="main-content">
    <section class="hero">
      <div class="container">
        <span class="badge badge-verified">$0 CAPITAL · {html.escape(devto_badge)}</span>
        <h1>Ko-fi &amp; DEV.to Verified Content Autopilot</h1>
        <p>Mon / Wed / Fri (<code>18:30 Asia/Colombo</code> = <code>13:00 UTC</code>) automated tutorial engine. Every Python &amp; shell snippet runs inside an isolated <code>python3 -I</code> sandbox before staging. 4 sales of the $14 Flagship Kit = ~$52 net.</p>
        <div class="cta-row">
          <button type="button" class="btn btn-primary copy-pack-btn" data-copy-key="storefront_guide" data-label="Copy Ko-fi Profile & Shop Copy">Copy Ko-fi Profile &amp; Shop Copy</button>
          <a class="btn" href="/site/index.html">Preview Canonical Blog</a>
        </div>
        <div class="kpi-strip">
          <div class="kpi-box">
            <div class="kpi-label">Verified Tutorials</div>
            <div class="kpi-val tabular">{status_data["counts"]["articles_verified"]}</div>
          </div>
          <div class="kpi-box">
            <div class="kpi-label">Queued Pillar Topics</div>
            <div class="kpi-val tabular">{status_data["counts"]["signals_unused"]}</div>
          </div>
          <div class="kpi-box">
            <div class="kpi-label">Packaged .ZIP SKUs</div>
            <div class="kpi-val tabular">{len(status_data["products"])} ($0+ &amp; $14)</div>
          </div>
          <div class="kpi-box">
            <div class="kpi-label">30-Day Target Math</div>
            <div class="kpi-val tabular">4 × $14 = $56 Gross</div>
          </div>
        </div>
      </div>
    </section>

    <section class="section-block" aria-labelledby="skus-heading">
      <div class="container">
        <h2 id="skus-heading" class="section-title">1. Downloadable Storefront <code>.zip</code> Kits (Upload Once to Ko-fi Shop)</h2>
        <div class="grid-cards">
{chr(10).join(prod_cards)}
        </div>
      </div>
    </section>

    <section class="section-block" aria-labelledby="syndication-heading">
      <div class="container">
        <h2 id="syndication-heading" class="section-title">2. Sandbox-Verified Tutorials &amp; 1-Click Cross-Post Packs</h2>
        <div class="stack-list">
{chr(10).join(article_cards) if article_cards else '<p>No articles generated yet. Run <code>python3 autopilot.py run-pipeline --limit 3 --push-draft</code>.</p>'}
        </div>
      </div>
    </section>
  </main>

  <footer class="site-footer">
    <div class="container">
      <p>Primary Store: <code>{html.escape(config.primary_store_url)}</code> · Mirror Store: <code>{html.escape(config.mirror_store_url or "none (using Primary)")}</code> · Timezone: <code>{html.escape(config.timezone)}</code></p>
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
            return  # Keep stdout clean during automated runs and tests

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
        description="Zero-Capital Ko-fi & DEV.to Verified Content Autopilot CLI",
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
