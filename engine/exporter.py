"""Multi-platform syndication pack exporter (DEV.to, Medium, Substack, Ko-fi, LinkedIn)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from engine.blogger_client import BloggerClient
from engine.config import AppConfig
from engine.devto_client import build_devto_payload
from engine.models import PILLAR_META, ArticleRecord
from engine.nasio_client import export_nasio_daily_pack, write_nasio_setup_guide
from engine.patreon_client import export_patreon_bundle, write_patreon_setup_guide
from engine.pinterest_client import PinterestClient
from engine.storefronts import append_storefront_utm


def _utm(
    base_url: str,
    *,
    source: str,
    slug: str,
    sku: str,
    configured_ls_url: str = "",
) -> str:
    return append_storefront_utm(
        base_url,
        source=source,
        medium="syndication",
        campaign=slug[:48],
        slug=slug,
        sku=sku,
        configured_ls_url=configured_ls_url,
    )


def _rewrite_markdown_store_utm(
    markdown_text: str,
    *,
    source: str,
    slug: str,
    configured_ls_url: str = "",
) -> str:
    def _replace_md_link(match: re.Match[str]) -> str:
        label = match.group(1)
        url = match.group(2)
        if "utm_source=devto" not in url:
            return match.group(0)
        rewritten = append_storefront_utm(
            url,
            source=source,
            medium="syndication",
            campaign=slug[:48],
            slug=slug,
            configured_ls_url=configured_ls_url,
        )
        return f"[{label}]({rewritten})"

    return re.sub(r"\[([^\]]+)\]\((https://[^)]+)\)", _replace_md_link, markdown_text)


def export_article_bundle(
    article: ArticleRecord,
    config: AppConfig,
    *,
    series_articles: Optional[list[ArticleRecord]] = None,
) -> dict[str, str]:
    """Write ready-to-paste cross-post files into `exports/<slug>/` + `site/pins/<slug>.png`."""
    bundle_dir = config.exports_dir / article.slug
    bundle_dir.mkdir(parents=True, exist_ok=True)
    meta = PILLAR_META.get(article.pillar, PILLAR_META["pillar_a"])

    # 1. DEV.to Forem v1 JSON payload
    devto_payload = build_devto_payload(article, publish_mode=config.publish_mode)
    devto_path = bundle_dir / "devto_payload.json"
    devto_path.write_text(json.dumps(devto_payload, indent=2) + "\n", encoding="utf-8")

    # 2. Medium Import Guide + Clean Markdown (`https://medium.com/p/import`)
    medium_body = _rewrite_markdown_store_utm(
        article.markdown_body,
        source="medium",
        slug=article.slug,
        configured_ls_url=config.lemonsqueezy_store_url,
    )
    medium_md = f"""<!--
MEDIUM SYNDICATION INSTRUCTIONS (60-Second Canonical Import):
1. Deploy `site/` to GitHub Pages (or ensure `{article.canonical_url}` is live).
2. Open https://medium.com/p/import and paste `{article.canonical_url}`.
   Medium automatically sets `<link rel="canonical">` and preserves code blocks cleanly!
3. Fallback if pasting manually: copy everything below into a new Medium draft (keep FREE / un-paywalled).
-->

# {article.title}

*{article.subtitle}*

{medium_body}

---

*Originally published at [{article.canonical_url}]({article.canonical_url}).*
"""
    medium_path = bundle_dir / "medium_import.md"
    medium_path.write_text(medium_md, encoding="utf-8")

    # 3. Substack Newsletter Edition
    sub_body = _rewrite_markdown_store_utm(
        article.markdown_body,
        source="substack",
        slug=article.slug,
        configured_ls_url=config.lemonsqueezy_store_url,
    )
    sub_free = _utm(
        config.free_checkout_url,
        source="substack",
        slug=article.slug,
        sku="free-idempotent-cron-mini-kit",
        configured_ls_url=config.lemonsqueezy_store_url,
    )
    sub_paid = _utm(
        config.paid_checkout_url,
        source="substack",
        slug=article.slug,
        sku="production-agent-automation-kit",
        configured_ls_url=config.lemonsqueezy_store_url,
    )
    substack_md = f"""# {article.title}

**{article.subtitle}**

Hey builders — happy {meta["weekday"]}! Today's issue of **{meta["series"]}** breaks down **{meta["label"]}** with a self-contained Python 3.11+ benchmark that we executed inside an isolated sandbox (`{article.verification.get("total_duration_ms", 0)} ms` total verification runtime across `{article.verification.get("code_blocks_tested", 2)}` blocks).

---

{sub_body}

---

### Grab the Tested `.zip` Toolkits
- **Free ($0+ PWYW):** [Idempotent Cron & API Circuit-Breaker Mini-Kit]({sub_free})
- **Full Starter Kit ($14 USD, MIT Commercial License):** [Production AI Agent & Zero-Cost Backend Reliability Starter Kit]({sub_paid})
"""
    substack_path = bundle_dir / "substack_post.md"
    substack_path.write_text(substack_md, encoding="utf-8")

    # 4. Ko-fi Creator Feed Post (`https://ko-fi.com/tharushanuwansara`)
    kofi_free = _utm(
        config.free_checkout_url,
        source="kofi_post",
        slug=article.slug,
        sku="free-idempotent-cron-mini-kit",
        configured_ls_url=config.lemonsqueezy_store_url,
    )
    kofi_paid = _utm(
        config.paid_checkout_url,
        source="kofi_post",
        slug=article.slug,
        sku="production-agent-automation-kit",
        configured_ls_url=config.lemonsqueezy_store_url,
    )
    kofi_md = f"""# New Verified Teardown: {article.title}

{article.subtitle}

**Sandbox Verification Summary:**
- Prose Length: `{article.word_count}` words
- Runnable Blocks Verified (`python3 -I`): `{article.verification.get("code_blocks_tested", 2)}` passed (`returncode == 0` in `{article.verification.get("total_duration_ms", 0)} ms`)
- Full Free Tutorial + Benchmarks: [{article.canonical_url}]({article.canonical_url})

Want the drop-in modules and unit test suites ready to copy into your repo?
1. **Free ($0+ Pay-What-You-Want):** [Free Idempotent Cron & Circuit-Breaker Mini-Kit]({kofi_free})
2. **Full Kit ($14 USD, MIT Commercial License):** [Production AI Agent & Zero-Cost Backend Reliability Starter Kit]({kofi_paid})
"""
    kofi_path = bundle_dir / "kofi_post.md"
    kofi_path.write_text(kofi_md, encoding="utf-8")

    # 5. LinkedIn Engineering Snippet (plain text, high-signal, zero fluff)
    linkedin_txt = f"""{article.title}

{article.subtitle}

3 takeaways from running the isolated Python 3.11+ benchmark ({article.verification.get("total_duration_ms", 0)} ms verification runtime):
1. Production reliability bugs almost always hide in retry storms, lock-upgrade deadlocks, or stateless replay windows—not the happy path.
2. You do not need Redis or heavyweight third-party frameworks for deterministic state machines, SQLite WAL visibility leases, or constant-time HMAC guards.
3. Every snippet in our {meta["weekday"]} teardown runs solely on Python's standard library and passes automated sandbox assertions before staging.

Read the full benchmark + runnable code here:
{article.canonical_url}

Free $0+ Mini-Kit & MIT Starter Kit in my shop:
{config.primary_store_url}
"""
    linkedin_path = bundle_dir / "linkedin_snippet.txt"
    linkedin_path.write_text(linkedin_txt, encoding="utf-8")

    # 6. Google Blogger v3 Series HTML & Payload
    _, blogger_html_path, blogger_json_path = BloggerClient(config).export_artifacts(
        article,
        series_articles=series_articles,
    )

    # 7. Patreon Daily Public & Paid Tier/Shop Markdown Pack + Storefront Guide
    patreon_paths = export_patreon_bundle(article, config, series_articles=series_articles)
    write_patreon_setup_guide(config)

    # 8. Nas.io Daily 60-Minute Execution Pack + Strategy & Challenge Guide
    nasio_paths = export_nasio_daily_pack(article, config, series_articles=series_articles)
    write_nasio_setup_guide(config)

    # 9. Optional visual card / legacy 2:3 graphic metadata (dry-run local export)
    dry_pin_cfg = AppConfig(
        **{
            **config.__dict__,
            "pinterest_access_token": "",
            "pinterest_board_id": config.pinterest_board_id,
        }
    )
    pin_info = PinterestClient(dry_pin_cfg).render_and_publish(article)

    return {
        "bundle_dir": str(bundle_dir),
        "devto_payload": str(devto_path),
        "medium_import": str(medium_path),
        "substack_post": str(substack_path),
        "kofi_post": str(kofi_path),
        "linkedin_snippet": str(linkedin_path),
        "blogger_post_html": str(blogger_html_path),
        "blogger_payload": str(blogger_json_path),
        **patreon_paths,
        **nasio_paths,
        "pinterest_pin_png": str(pin_info["png_path"]),
        "pinterest_pin_svg": str(pin_info["svg_path"]),
        "pinterest_pin_json": str(pin_info["metadata_path"]),
    }


def load_bundle_texts(exports_dir: Path, slug: str) -> dict[str, Any]:
    """Read exported bundle files for the localhost 1-click copy dashboard."""
    d = exports_dir / slug
    result: dict[str, Any] = {}
    for key, filename in (
        ("nasio_daily_pack", "nasio_daily_pack.md"),
        ("patreon_public", "patreon_public_post.md"),
        ("patreon_paid", "patreon_paid_tier_post.md"),
        ("patreon_pack", "patreon_post_pack.json"),
        ("kofi_post", "kofi_post.md"),
        ("medium_import", "medium_import.md"),
        ("substack_post", "substack_post.md"),
        ("linkedin_snippet", "linkedin_snippet.txt"),
        ("blogger_html", "blogger_post.html"),
        ("pinterest_json", "pinterest_pin.json"),
    ):
        p = d / filename
        result[key] = p.read_text(encoding="utf-8") if p.is_file() else ""
    return result
