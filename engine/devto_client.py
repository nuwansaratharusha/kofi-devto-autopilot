"""Official DEV.to (Forem API v1) client with safe draft defaults, tag sanitizer & 422 fallback."""

from __future__ import annotations

import json
import re
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from engine.config import AppConfig
from engine.models import PILLAR_META, ArticleRecord

DEVTO_BASE_URL = "https://dev.to/api/articles"
USER_AGENT = "KofiDevtoAutopilot/1.0 (+https://ko-fi.com/tharushanuwansara)"

TAG_SYNONYMS = {
    "c++": "cpp",
    "c#": "csharp",
    "node.js": "nodejs",
    "next.js": "nextjs",
    "dev.to": "devto",
}


class DevtoPublishError(RuntimeError):
    """Raised when an article fails verification gate or unrecoverable Forem API error."""


@dataclass(frozen=True)
class HttpResult:
    status: int
    headers: Mapping[str, str]
    body: bytes


def sanitize_devto_tags(raw_tags: list[str]) -> list[str]:
    """Sanitize tags to Forem v1 rules: max 4 tags, lowercase alphanumeric `[a-z0-9]`, <= 30 chars."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for tag in raw_tags:
        low = tag.strip().lower()
        low = TAG_SYNONYMS.get(low, low)
        alnum = re.sub(r"[^a-z0-9]", "", low)[:30]
        if alnum and alnum not in seen:
            seen.add(alnum)
            cleaned.append(alnum)
        if len(cleaned) == 4:
            break
    if not cleaned:
        cleaned = ["python", "backend"]
    return cleaned


def build_devto_payload(
    article: ArticleRecord,
    *,
    publish_mode: str = "draft",
    include_ai_disclosure: bool = True,
) -> dict[str, Any]:
    """Construct the exact Forem v1 JSON payload."""
    meta = PILLAR_META.get(article.pillar, PILLAR_META["pillar_a"])
    published_bool = publish_mode.strip().lower() == "live"
    article_obj: dict[str, Any] = {
        "title": article.title,
        "published": published_bool,
        "body_markdown": article.markdown_body,
        "tags": sanitize_devto_tags(article.tags),
        "series": meta["series"],
        "canonical_url": article.canonical_url,
        "description": article.subtitle[:240],
    }
    if include_ai_disclosure:
        article_obj["ai_disclosure_level"] = "some_ai"
    return {"article": article_obj}


def build_ssl_context() -> ssl.SSLContext:
    """Create a strict TLS context, loading macOS `/etc/ssl/cert.pem` when Python's bundled store is empty."""
    ctx = ssl.create_default_context()
    if ctx.cert_store_stats().get("x509_ca", 0) == 0 and Path("/etc/ssl/cert.pem").is_file():
        ctx.load_verify_locations(cafile="/etc/ssl/cert.pem")
    return ctx


_DEFAULT_SSL_CONTEXT = build_ssl_context()


def _urllib_sender(req: urllib.request.Request, timeout: float) -> HttpResult:
    try:
        ctx = _DEFAULT_SSL_CONTEXT if req.full_url.lower().startswith("https://") else None
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            hdrs = {k.lower(): v for k, v in resp.headers.items()}
            return HttpResult(status=int(resp.status), headers=hdrs, body=resp.read())
    except urllib.error.HTTPError as err:
        hdrs = {k.lower(): v for k, v in (err.headers.items() if err.headers else [])}
        body = err.read() if err.fp else b""
        return HttpResult(status=int(err.code), headers=hdrs, body=body)


class DevtoClient:
    """Idempotent Forem v1 client supporting dry-run export, POST creation, and PUT updates."""

    def __init__(
        self,
        config: AppConfig,
        *,
        sender: Callable[[urllib.request.Request, float], HttpResult] = _urllib_sender,
        sleeper: Callable[[float], None] = time.sleep,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.config = config
        self._sender = sender
        self._sleep = sleeper
        self.timeout_seconds = timeout_seconds

    def _export_dry_run_payload(self, slug: str, payload: dict[str, Any]) -> Path:
        out_dir = self.config.exports_dir / slug
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / "devto_payload.json"
        out_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return out_file

    def _send_with_rate_limit_retry(
        self,
        method: str,
        url: str,
        payload: dict[str, Any],
        *,
        max_attempts: int = 3,
    ) -> HttpResult:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/vnd.forem.api-v1+json",
            "api-key": self.config.devto_api_key,
            "User-Agent": USER_AGENT,
        }
        data = json.dumps(payload).encode("utf-8")

        for attempt in range(1, max_attempts + 1):
            req = urllib.request.Request(url=url, data=data, headers=headers, method=method)
            res = self._sender(req, self.timeout_seconds)
            if res.status == 429 and attempt < max_attempts:
                retry_after = res.headers.get("retry-after", "")
                try:
                    wait = min(10.0, max(0.5, float(retry_after)))
                except ValueError:
                    wait = float(attempt)
                self._sleep(wait)
                continue
            if res.status in {500, 502, 503, 504} and attempt < max_attempts:
                self._sleep(0.5 * attempt)
                continue
            return res
        raise DevtoPublishError("Exhausted DEV.to HTTP retry attempts")

    def stage_or_publish(
        self,
        article: ArticleRecord,
        *,
        mode_override: Optional[str] = None,
    ) -> dict[str, Any]:
        """Stage (`published: false`) or publish (`published: true`) a verified article."""
        allowed_statuses = {"code_verified", "staged_draft", "published_live"}
        if article.status not in allowed_statuses:
            raise DevtoPublishError(
                f"Refusing to push unverified article '{article.slug}' (status='{article.status}')"
            )

        effective_mode = (mode_override or self.config.publish_mode or "draft").strip().lower()
        if effective_mode not in {"draft", "live"}:
            effective_mode = "draft"

        payload = build_devto_payload(article, publish_mode=effective_mode, include_ai_disclosure=True)
        payload_path = self._export_dry_run_payload(article.slug, payload)

        if not self.config.devto_api_key:
            return {
                "status": "dry_run",
                "reason": "DEVTO_API_KEY not set in .env; exported exact Forem v1 JSON payload locally.",
                "published": payload["article"]["published"],
                "payload_path": str(payload_path),
                "tags": payload["article"]["tags"],
            }

        is_update = article.devto_article_id is not None
        method = "PUT" if is_update else "POST"
        target_url = (
            f"{DEVTO_BASE_URL}/{article.devto_article_id}" if is_update else DEVTO_BASE_URL
        )

        res = self._send_with_rate_limit_retry(method, target_url, payload)

        # Automatic HTTP 422 fallback if `ai_disclosure_level` is rejected by Forem schema version
        used_422_fallback = False
        if res.status == 422:
            fallback_payload = build_devto_payload(
                article, publish_mode=effective_mode, include_ai_disclosure=False
            )
            self._export_dry_run_payload(article.slug, fallback_payload)
            res = self._send_with_rate_limit_retry(method, target_url, fallback_payload)
            used_422_fallback = True

        if res.status not in {200, 201}:
            body_preview = res.body.decode("utf-8", errors="replace")[:400]
            raise DevtoPublishError(
                f"DEV.to API {method} {target_url} failed with HTTP {res.status}: {body_preview}"
            )

        parsed = json.loads(res.body.decode("utf-8"))
        remote_id = int(parsed["id"])
        remote_url = str(parsed.get("url") or f"https://dev.to/dashboard")
        is_live = bool(parsed.get("published", effective_mode == "live"))

        article.devto_article_id = remote_id
        article.devto_url = remote_url
        article.published_mode = "live" if is_live else "draft"
        article.status = "published_live" if is_live else "staged_draft"

        return {
            "status": "published_live" if is_live else "staged_draft",
            "operation": "updated" if is_update else "created",
            "devto_article_id": remote_id,
            "devto_url": remote_url,
            "published": is_live,
            "used_422_fallback": used_422_fallback,
            "payload_path": str(payload_path),
        }

    def list_my_articles(
        self,
        *,
        per_page: int = 100,
        max_pages: int = 3,
    ) -> list[dict[str, Any]]:
        """Fetch authenticated author's drafts & published articles (`GET /api/articles/me/all`) with view/reaction stats."""
        if not self.config.devto_api_key:
            raise DevtoPublishError("DEVTO_API_KEY is empty")

        headers = {
            "Accept": "application/vnd.forem.api-v1+json",
            "api-key": self.config.devto_api_key,
            "User-Agent": USER_AGENT,
        }
        collected: list[dict[str, Any]] = []
        eff_per_page = max(1, min(1000, int(per_page)))

        for page_num in range(1, max(1, max_pages) + 1):
            url = f"{DEVTO_BASE_URL}/me/all?per_page={eff_per_page}&page={page_num}"
            res: Optional[HttpResult] = None
            for attempt in range(1, 4):
                req = urllib.request.Request(url=url, headers=headers, method="GET")
                res = self._sender(req, self.timeout_seconds)
                if res.status == 429 and attempt < 3:
                    retry_after = res.headers.get("retry-after", "")
                    try:
                        wait = min(10.0, max(0.5, float(retry_after)))
                    except ValueError:
                        wait = float(attempt)
                    self._sleep(wait)
                    continue
                if res.status in {500, 502, 503, 504} and attempt < 3:
                    self._sleep(0.5 * attempt)
                    continue
                break

            assert res is not None
            if res.status != 200:
                preview = res.body.decode("utf-8", errors="replace")[:300]
                raise DevtoPublishError(
                    f"DEV.to API GET {url} failed with HTTP {res.status}: {preview}"
                )
            parsed = json.loads(res.body.decode("utf-8") or "[]")
            if not isinstance(parsed, list) or not parsed:
                break
            batch = [dict(item) for item in parsed if isinstance(item, Mapping)]
            collected.extend(batch)
            if len(batch) < eff_per_page:
                break

        return collected

    def sync_article_metrics(self, db: Any) -> dict[str, Any]:
        """Reconcile remote DEV.to articles & upsert live `page_views_count` + reactions/comments into `channel_metrics`."""
        if not self.config.devto_api_key:
            return {
                "status": "dry_run",
                "articles_matched": 0,
                "views_total": 0,
                "engagement_total": 0,
                "reason": "DEVTO_API_KEY not configured; skipped live DEV.to analytics fetch.",
            }

        remote_articles = self.list_my_articles()
        local_articles = db.list_articles(verified_only=True)
        by_id = {
            int(item["id"]): item
            for item in remote_articles
            if item.get("id") is not None and str(item.get("id")).isdigit()
        }
        by_canonical = {
            str(item.get("canonical_url") or "").strip().rstrip("/"): item
            for item in remote_articles
            if str(item.get("canonical_url") or "").strip()
        }

        matched_count = 0
        views_sum = 0
        eng_sum = 0

        for art in local_articles:
            matched: Optional[dict[str, Any]] = None
            if art.devto_article_id is not None and art.devto_article_id in by_id:
                matched = by_id[art.devto_article_id]
            elif art.canonical_url.strip().rstrip("/") in by_canonical:
                matched = by_canonical[art.canonical_url.strip().rstrip("/")]
            else:
                norm_title = art.title.strip().lower()
                day_tag = f"[day {art.series_day}/30]" if art.series_day else ""
                for item in remote_articles:
                    r_title = str(item.get("title") or "").strip().lower()
                    if (norm_title and norm_title == r_title) or (day_tag and day_tag in r_title):
                        matched = item
                        break

            if not matched:
                continue

            matched_count += 1
            remote_id = matched.get("id")
            if remote_id is not None and str(remote_id).isdigit() and art.devto_article_id != int(remote_id):
                art.devto_article_id = int(remote_id)
            remote_url = str(matched.get("url") or "").strip()
            if remote_url and art.devto_url != remote_url:
                art.devto_url = remote_url
            is_pub = bool(matched.get("published", False))
            art.published_mode = "live" if is_pub else "draft"
            art.status = "published_live" if is_pub else ("staged_draft" if art.devto_article_id else art.status)
            db.save_article(art)

            views = max(0, int(matched.get("page_views_count") or 0))
            reactions = max(
                0,
                int(
                    matched.get("positive_reactions_count")
                    if matched.get("positive_reactions_count") is not None
                    else (matched.get("public_reactions_count") or 0)
                ),
            )
            comments = max(0, int(matched.get("comments_count") or 0))
            engagement = reactions + comments
            views_sum += views
            eng_sum += engagement

            db.upsert_channel_metrics_monotonic(
                slug=art.slug,
                channel="devto",
                min_views=views,
                min_clicks=engagement,
            )

        return {
            "status": "synced",
            "remote_articles_fetched": len(remote_articles),
            "articles_matched": matched_count,
            "views_total": views_sum,
            "engagement_total": eng_sum,
        }
