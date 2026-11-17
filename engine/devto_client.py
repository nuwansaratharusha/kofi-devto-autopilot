"""Official DEV.to (Forem API v1) client with safe draft defaults, tag sanitizer & 422 fallback."""

from __future__ import annotations

import json
import re
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


def _urllib_sender(req: urllib.request.Request, timeout: float) -> HttpResult:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
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
