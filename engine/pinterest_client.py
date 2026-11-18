"""Pinterest REST API v5 Daily Pin Publisher + Dry-Run Metadata Exporter."""

from __future__ import annotations

import base64
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

from engine.config import AppConfig
from engine.devto_client import HttpResult, _urllib_sender
from engine.models import PILLAR_META, SERIES_30D_NAME, ArticleRecord
from engine.pin_renderer import generate_pin_assets

PINTEREST_V5_PINS_URL = "https://api.pinterest.com/v5/pins"
PINTEREST_UTM_CAMPAIGN = "30d_exp_2026_11"
USER_AGENT = "KofiDevtoAutopilot/2.0 (+https://ko-fi.com/tharushanuwansara)"


class PinterestPublishError(RuntimeError):
    """Raised when an article fails verification gate or Pinterest v5 returns an error."""


def append_pinterest_utm(url: str, *, slug: str = "") -> str:
    """Attach `utm_source=pinterest&utm_medium=pin&utm_campaign=30d_exp_2026_11`."""
    parsed = urllib.parse.urlparse(url)
    qs = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    qs["utm_source"] = "pinterest"
    qs["utm_medium"] = "pin"
    qs["utm_campaign"] = PINTEREST_UTM_CAMPAIGN
    if slug:
        qs.setdefault("slug", slug)
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(qs)))


def resolve_pin_destination_url(article: ArticleRecord) -> str:
    base = (
        article.blogger_url
        if (article.blogger_url and article.blogger_url.startswith("https://"))
        else article.canonical_url
    )
    return append_pinterest_utm(base, slug=article.slug)


def _truncate_clean(text: str, limit: int) -> str:
    """Truncate `text` within `limit` chars cleanly at a word boundary without trailing spaces."""
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    if limit <= 1:
        return cleaned[:limit]
    window = cleaned[: limit - 1]
    space_idx = window.rfind(" ")
    if space_idx >= max(16, limit // 2):
        return window[:space_idx].rstrip(" ,;:-—–") + "…"
    return window.rstrip() + "…"


def build_pinterest_pin_copy(article: ArticleRecord, config: AppConfig) -> dict[str, str]:
    day_num = max(1, int(article.series_day or 1))
    meta = PILLAR_META.get(article.pillar, PILLAR_META["pillar_a"])
    dur_ms = article.verification.get("total_duration_ms", 0)
    blocks_n = article.verification.get("code_blocks_tested", 2)

    raw_title = f"[Day {day_num}/30] {article.title}"
    title = _truncate_clean(raw_title, 100)

    hashtags = " ".join(f"#{t.replace('-', '')}" for t in article.tags[:4])
    raw_desc = (
        f"Day {day_num}/30 of {SERIES_30D_NAME}: {article.subtitle} "
        f"Every Python 3.11+ & shell benchmark block passed isolated python3 -I sandbox verification "
        f"({blocks_n} blocks, {dur_ms} ms, 0 third-party dependencies). "
        f"Includes free $0+ Idempotent Cron Mini-Kit & MIT Production Starter Kit. {hashtags}"
    )
    description = _truncate_clean(raw_desc, 800)

    raw_alt = (
        f"1000x1500 technical infographic for Day {day_num} of 30 ({meta['label']}): "
        f"{article.title} — verified in Python 3.11+ stdlib sandbox ({article.word_count} words)."
    )
    alt_text = _truncate_clean(raw_alt, 500)

    return {
        "title": title,
        "description": description,
        "alt_text": alt_text,
        "link": resolve_pin_destination_url(article),
    }


def build_pinterest_v5_payload(
    article: ArticleRecord,
    config: AppConfig,
    *,
    png_bytes: bytes,
) -> dict[str, Any]:
    """Construct exact Pinterest API v5 `POST /v5/pins` JSON request body."""
    copy_fields = build_pinterest_pin_copy(article, config)
    b64_data = base64.b64encode(png_bytes).decode("ascii")
    return {
        "board_id": config.pinterest_board_id,
        "title": copy_fields["title"][:100],
        "description": copy_fields["description"][:800],
        "alt_text": copy_fields["alt_text"][:500],
        "link": copy_fields["link"],
        "media_source": {
            "source_type": "image_base64",
            "content_type": "image/png",
            "data": b64_data,
        },
    }


class PinterestClient:
    """Idempotent Pinterest API v5 publisher + dry-run metadata exporter."""

    def __init__(
        self,
        config: AppConfig,
        *,
        sender: Callable[[urllib.request.Request, float], HttpResult] = _urllib_sender,
        sleeper: Callable[[float], None] = time.sleep,
        timeout_seconds: float = 12.0,
    ) -> None:
        self.config = config
        self._sender = sender
        self._sleep = sleeper
        self.timeout_seconds = timeout_seconds

    def has_live_credentials(self) -> bool:
        return bool(self.config.pinterest_access_token and self.config.pinterest_board_id)

    def render_and_publish(
        self,
        article: ArticleRecord,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        allowed_statuses = {"code_verified", "staged_draft", "published_live"}
        if article.status not in allowed_statuses:
            raise PinterestPublishError(
                f"Refusing to pin unverified article '{article.slug}' (status='{article.status}')"
            )

        pin_assets = generate_pin_assets(article, self.config)
        png_bytes = Path(pin_assets["png_path"]).read_bytes()
        v5_payload = build_pinterest_v5_payload(article, self.config, png_bytes=png_bytes)

        # Write compact dry-run metadata file to exports/<slug>/pinterest_pin.json
        export_dir = self.config.exports_dir / article.slug
        export_dir.mkdir(parents=True, exist_ok=True)
        meta_path = export_dir / "pinterest_pin.json"
        dry_run_meta = {
            "board_id": v5_payload["board_id"] or "<set PINTEREST_BOARD_ID in .env>",
            "pinterest_pin_id": article.pinterest_pin_id,
            "pinterest_url": article.pinterest_url,
            "title": v5_payload["title"],
            "description": v5_payload["description"],
            "alt_text": v5_payload["alt_text"],
            "link": v5_payload["link"],
            "public_image_url": pin_assets["public_image_url"],
            "png_path": pin_assets["png_path"],
            "svg_path": pin_assets["svg_path"],
            "png_size_bytes": pin_assets["png_size_bytes"],
            "png_sha256": pin_assets["png_sha256"],
            "dimensions": {"width": pin_assets["width"], "height": pin_assets["height"]},
            "media_source": {
                "source_type": "image_base64",
                "content_type": "image/png",
                "base64_length": len(v5_payload["media_source"]["data"]),
            },
        }
        meta_path.write_text(json.dumps(dry_run_meta, indent=2) + "\n", encoding="utf-8")

        if not self.has_live_credentials():
            return {
                "status": "dry_run",
                "reason": "PINTEREST_ACCESS_TOKEN / PINTEREST_BOARD_ID not set; rendered 1000x1500 PNG + RSS Auto-Publish entry.",
                "metadata_path": str(meta_path),
                **pin_assets,
                "link": v5_payload["link"],
            }

        if article.pinterest_pin_id and not force:
            return {
                "status": "already_pinned",
                "pinterest_pin_id": article.pinterest_pin_id,
                "pinterest_url": article.pinterest_url,
                "metadata_path": str(meta_path),
                **pin_assets,
            }

        headers = {
            "Authorization": f"Bearer {self.config.pinterest_access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        data = json.dumps(v5_payload).encode("utf-8")

        res: Optional[HttpResult] = None
        for attempt in range(1, 4):
            req = urllib.request.Request(
                url=PINTEREST_V5_PINS_URL,
                data=data,
                headers=headers,
                method="POST",
            )
            res = self._sender(req, self.timeout_seconds)
            if res.status == 429 and attempt < 3:
                self._sleep(1.0 * attempt)
                continue
            if res.status in {500, 502, 503, 504} and attempt < 3:
                self._sleep(0.5 * attempt)
                continue
            break

        assert res is not None
        if res.status not in {200, 201}:
            preview = res.body.decode("utf-8", errors="replace")[:350]
            raise PinterestPublishError(
                f"Pinterest API v5 POST {PINTEREST_V5_PINS_URL} failed with HTTP {res.status}: {preview}"
            )

        parsed = json.loads(res.body.decode("utf-8") or "{}")
        pin_id = str(parsed.get("id") or "").strip()
        if not pin_id:
            raise PinterestPublishError("Pinterest API v5 response missing pin id")
        pin_url = str(parsed.get("url") or f"https://www.pinterest.com/pin/{pin_id}/")
        article.pinterest_pin_id = pin_id
        article.pinterest_url = pin_url
        dry_run_meta["pinterest_pin_id"] = pin_id
        dry_run_meta["pinterest_url"] = pin_url
        meta_path.write_text(json.dumps(dry_run_meta, indent=2) + "\n", encoding="utf-8")

        return {
            "status": "published_live",
            "pinterest_pin_id": pin_id,
            "pinterest_url": pin_url,
            "metadata_path": str(meta_path),
            **pin_assets,
            "link": v5_payload["link"],
        }
