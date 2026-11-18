"""Google Blogger API v3 Daily Series Publisher + Pure-Stdlib OAuth2 Refresh & PKCE Helper."""

from __future__ import annotations

import base64
import hashlib
import html
import json
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from engine.config import AppConfig
from engine.devto_client import HttpResult, _urllib_sender
from engine.models import PILLAR_META, SERIES_30D_NAME, ArticleRecord
from engine.site_builder import render_markdown_to_html
from engine.storefronts import append_storefront_utm

GOOGLE_OAUTH_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
BLOGGER_V3_BASE = "https://www.googleapis.com/blogger/v3"
BLOGGER_SCOPE = "https://www.googleapis.com/auth/blogger"
BLOGGER_UTM_CAMPAIGN = "30d_exp_2026_11"
USER_AGENT = "KofiDevtoAutopilot/2.0 (+https://ko-fi.com/tharushanuwansara)"


class BloggerPublishError(RuntimeError):
    """Raised when an article fails verification gate or Blogger OAuth2/API v3 returns an error."""


def append_blogger_utm(
    url: str,
    *,
    slug: str = "",
    sku: str = "",
    configured_ls_url: str = "",
) -> str:
    """Attach `utm_source=blogger&utm_medium=series&utm_campaign=30d_exp_2026_11` cleanly."""
    return append_storefront_utm(
        url,
        source="blogger",
        medium="series",
        campaign=BLOGGER_UTM_CAMPAIGN,
        slug=slug,
        sku=sku,
        configured_ls_url=configured_ls_url,
    )


def _rewrite_html_utm_for_blogger(
    rendered_html: str,
    slug: str,
    configured_ls_url: str = "",
) -> str:
    """Rewrite in-body `utm_source=devto` checkout links to Blogger series UTM parameters."""

    def _replace_href(match: re.Match[str]) -> str:
        raw_url = html.unescape(match.group(1))
        if "utm_source=devto" not in raw_url:
            return match.group(0)
        rewritten = append_blogger_utm(
            raw_url,
            slug=slug,
            configured_ls_url=configured_ls_url,
        )
        return f'href="{html.escape(rewritten, quote=True)}"'

    no_buttons = re.sub(
        r'<div class="code-toolbar"><span>(.*?)</span><button[^>]*>.*?</button></div>',
        r'<div style="background:#1e293b;color:#e2e8f0;padding:6px 12px;font-size:12px;font-family:monospace;font-weight:700;">\1</div>',
        rendered_html,
    )
    no_buttons = no_buttons.replace(
        '<div class="code-frame">',
        '<div style="margin:18px 0;border:1px solid #334155;border-radius:8px;overflow:hidden;background:#0f172a;color:#f8fafc;">',
    ).replace(
        "<pre><code>",
        '<pre style="margin:0;padding:14px;overflow-x:auto;background:#0f172a;color:#f8fafc;font-size:13px;line-height:1.55;"><code>',
    )
    return re.sub(r'href="([^"]+)"', _replace_href, no_buttons)


def format_blogger_series_title(article: ArticleRecord, total_days: int = 30) -> str:
    day_num = max(1, int(article.series_day or 1))
    prefix = f"[Day {day_num}/{total_days}] "
    if article.title.startswith("[Day "):
        return article.title
    return f"{prefix}{article.title}"


def build_blogger_series_html(
    article: ArticleRecord,
    config: AppConfig,
    *,
    series_articles: Optional[list[ArticleRecord]] = None,
    total_days: int = 30,
) -> str:
    """Render complete Blogger API v3 HTML body with series banner, UTMs, and series index."""
    day_num = max(1, int(article.series_day or 1))
    meta = PILLAR_META.get(article.pillar, PILLAR_META["pillar_a"])
    dur_ms = article.verification.get("total_duration_ms", 0)
    blocks_n = article.verification.get("code_blocks_tested", 2)

    hub_url = append_blogger_utm(f"{config.canonical_base_url}/", slug=article.slug)
    canon_utm_url = append_blogger_utm(article.canonical_url, slug=article.slug)
    free_url = append_blogger_utm(
        config.free_checkout_url,
        slug=article.slug,
        sku="free-idempotent-cron-mini-kit",
        configured_ls_url=config.lemonsqueezy_store_url,
    )
    paid_url = append_blogger_utm(
        config.paid_checkout_url,
        slug=article.slug,
        sku="production-agent-automation-kit",
        configured_ls_url=config.lemonsqueezy_store_url,
    )

    body_html = _rewrite_html_utm_for_blogger(
        render_markdown_to_html(article.markdown_body),
        article.slug,
        configured_ls_url=config.lemonsqueezy_store_url,
    )

    index_items: list[str] = []
    if series_articles:
        for item in sorted(series_articles, key=lambda a: (a.series_day or 999, a.slug)):
            item_day = item.series_day or 1
            target = item.blogger_url or append_blogger_utm(item.canonical_url, slug=item.slug)
            if item.slug == article.slug:
                index_items.append(
                    f"<li><strong>Day {item_day}/{total_days}: {html.escape(item.title)} (Current Lesson)</strong></li>"
                )
            else:
                index_items.append(
                    f'<li><a href="{html.escape(target, quote=True)}" rel="noopener">'
                    f"Day {item_day}/{total_days}: {html.escape(item.title)}</a></li>"
                )
    else:
        index_items.append(
            f"<li><strong>Day {day_num}/{total_days}: {html.escape(article.title)}</strong></li>"
        )

    series_index_html = "\n".join(index_items)

    return f"""<div class="autopilot-blogger-series" style="line-height:1.7;font-size:16px;">
  <div style="padding:16px 18px;margin-bottom:22px;border-left:4px solid #0f766e;background:#f1f5f9;color:#0f172a;border-radius:6px;">
    <p style="margin:0 0 6px;font-weight:800;font-size:14px;text-transform:uppercase;letter-spacing:0.04em;color:#0f766e;">
      Series: {html.escape(SERIES_30D_NAME)} · Day {day_num} of {total_days}
    </p>
    <p style="margin:0;font-size:14px;color:#334155;">
      <strong>Track:</strong> {html.escape(meta["label"])} ·
      <strong>Sandbox Gate:</strong> {blocks_n} runnable blocks verified in {dur_ms} ms (<code>python3 -I</code> exit 0) ·
      <a href="{html.escape(hub_url, quote=True)}" rel="noopener" style="color:#0f766e;font-weight:700;">Browse All 30 Days &amp; Benchmarks</a>
    </p>
  </div>

  <p style="font-size:18px;color:#334155;font-style:italic;margin:0 0 20px;">
    {html.escape(article.subtitle)}
  </p>

  {body_html}

  <hr style="margin:28px 0;border:0;border-top:1px solid #cbd5e1;" />

  <div style="padding:18px;margin:20px 0;border:1px solid #cbd5e1;border-radius:8px;background:#f8fafc;color:#0f172a;">
    <h3 style="margin:0 0 10px;font-size:18px;">Download the Unit-Tested Starter Kits</h3>
    <ul style="margin:0 0 12px;padding-left:20px;">
      <li><strong>Free ($0+ Pay-What-You-Want):</strong> <a href="{html.escape(free_url, quote=True)}" rel="noopener">Free Idempotent Cron &amp; API Circuit-Breaker Mini-Kit (.zip)</a></li>
      <li><strong>Full Kit ($14 USD, MIT Commercial License):</strong> <a href="{html.escape(paid_url, quote=True)}" rel="noopener">Production AI Agent &amp; Zero-Cost Backend Reliability Starter Kit (.zip)</a></li>
    </ul>
  </div>

  <div style="padding:16px 18px;margin-top:18px;border:1px solid #e2e8f0;border-radius:8px;">
    <h3 style="margin:0 0 10px;font-size:17px;">{html.escape(SERIES_30D_NAME)} — Series Index</h3>
    <ol style="margin:0 0 12px;padding-left:22px;">
      {series_index_html}
    </ol>
    <p style="margin:0;font-size:13px;color:#475569;">
      Canonical engineering edition published by <strong>{html.escape(config.author_name)}</strong> at
      <a href="{html.escape(canon_utm_url, quote=True)}" rel="canonical noopener">{html.escape(article.canonical_url)}</a>.
    </p>
  </div>
</div>
"""


def build_blogger_payload(
    article: ArticleRecord,
    config: AppConfig,
    *,
    series_articles: Optional[list[ArticleRecord]] = None,
) -> tuple[dict[str, Any], str]:
    """Construct Blogger API v3 post resource and raw HTML string."""
    content_html = build_blogger_series_html(
        article,
        config,
        series_articles=series_articles,
    )
    raw_labels = [*article.tags[:4], "30dayseries", "python", "reliability"]
    labels: list[str] = []
    for raw_lbl in raw_labels:
        clean_lbl = re.sub(r"[,&<>]", "", str(raw_lbl)).strip()[:40]
        if clean_lbl and clean_lbl not in labels:
            labels.append(clean_lbl)
    labels = labels[:8]
    payload: dict[str, Any] = {
        "kind": "blogger#post",
        "title": format_blogger_series_title(article),
        "content": content_html,
        "labels": labels,
    }
    if config.blogger_blog_id:
        payload["blog"] = {"id": config.blogger_blog_id}
    if article.blogger_post_id:
        payload["id"] = article.blogger_post_id
    return payload, content_html


def generate_pkce_pair() -> tuple[str, str]:
    """Generate RFC 7636 S256 PKCE `(code_verifier, code_challenge)`."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def exchange_refresh_token(
    *,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    sender: Callable[[urllib.request.Request, float], HttpResult] = _urllib_sender,
    timeout_seconds: float = 10.0,
) -> str:
    """Exchange a Google OAuth2 refresh token for a fresh Bearer access token."""
    form_data = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url=GOOGLE_OAUTH_TOKEN_URL,
        data=form_data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    res = sender(req, timeout_seconds)
    if res.status != 200:
        preview = res.body.decode("utf-8", errors="replace")[:300]
        raise BloggerPublishError(
            f"Google OAuth2 token refresh failed with HTTP {res.status}: {preview}"
        )
    parsed = json.loads(res.body.decode("utf-8") or "{}")
    token = str(parsed.get("access_token") or "").strip()
    if not token:
        raise BloggerPublishError("Google OAuth2 response missing access_token")
    return token


def run_blogger_pkce_loopback(
    config: AppConfig,
    *,
    timeout_seconds: float = 120.0,
    open_browser: bool = True,
    sender: Callable[[urllib.request.Request, float], HttpResult] = _urllib_sender,
    on_listen: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    """Run one-time `127.0.0.1:0` loopback OAuth2 PKCE consent to obtain `BLOGGER_REFRESH_TOKEN`."""
    if not config.blogger_client_id or not config.blogger_client_secret:
        raise BloggerPublishError(
            "BLOGGER_CLIENT_ID and BLOGGER_CLIENT_SECRET must be set in .env before running auth-blogger. "
            "Create a free Desktop OAuth 2.0 Client ID in Google Cloud Console (enable Blogger API v3) first."
        )

    verifier, challenge = generate_pkce_pair()
    expected_state = secrets.token_urlsafe(24)
    captured: dict[str, str] = {}

    class LoopbackCallbackHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/callback":
                # Ignore browser /favicon.ico or preconnect hits without terminating OAuth wait
                self.send_response(HTTPStatus.NO_CONTENT)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            qs = urllib.parse.parse_qs(parsed.query)
            code_val = (qs.get("code") or [""])[0]
            err_val = (qs.get("error") or [""])[0]
            state_val = (qs.get("state") or [""])[0]
            if not code_val and not err_val:
                self.send_response(HTTPStatus.NO_CONTENT)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            captured["code"] = code_val
            captured["state"] = state_val
            captured["error"] = err_val
            msg = (
                b"<html><body style='font-family:sans-serif;padding:2rem;'>"
                b"<h1>Blogger OAuth2 Complete</h1>"
                b"<p>You may close this tab and return to your terminal.</p>"
                b"</body></html>"
            )
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)

    server = HTTPServer(("127.0.0.1", 0), LoopbackCallbackHandler)
    port = server.server_address[1]
    redirect_uri = f"http://127.0.0.1:{port}/callback"

    auth_query = urllib.parse.urlencode(
        {
            "client_id": config.blogger_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": BLOGGER_SCOPE,
            "access_type": "offline",
            "prompt": "consent",
            "state": expected_state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    auth_url = f"{GOOGLE_OAUTH_AUTH_URL}?{auth_query}"
    print(f"Open this URL to authorize Google Blogger API v3 (waiting up to {int(timeout_seconds)}s):\n{auth_url}\n", flush=True)
    if open_browser:
        try:
            webbrowser.open(auth_url)
        except Exception:
            pass
    if on_listen is not None:
        on_listen(auth_url)

    deadline = time.monotonic() + max(0.1, float(timeout_seconds))
    try:
        while "code" not in captured and "error" not in captured:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            server.timeout = min(1.0, remaining)
            server.handle_request()
    finally:
        server.server_close()

    if captured.get("error"):
        raise BloggerPublishError(f"OAuth2 authorization denied: {captured['error']}")
    if not captured.get("code"):
        raise BloggerPublishError(f"Timed out after {int(timeout_seconds)}s waiting for loopback OAuth2 callback")
    if captured.get("state") != expected_state:
        raise BloggerPublishError("OAuth2 state mismatch; aborting PKCE token exchange")

    token_form = urllib.parse.urlencode(
        {
            "client_id": config.blogger_client_id,
            "client_secret": config.blogger_client_secret,
            "code": captured["code"],
            "code_verifier": verifier,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url=GOOGLE_OAUTH_TOKEN_URL,
        data=token_form,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    res = sender(req, 15.0)
    if res.status != 200:
        preview = res.body.decode("utf-8", errors="replace")[:300]
        raise BloggerPublishError(f"PKCE authorization_code exchange failed (HTTP {res.status}): {preview}")
    tokens = json.loads(res.body.decode("utf-8") or "{}")
    return {
        "status": "authorized",
        "redirect_uri": redirect_uri,
        "refresh_token": str(tokens.get("refresh_token") or ""),
        "access_token_received": bool(tokens.get("access_token")),
        "scope": str(tokens.get("scope") or BLOGGER_SCOPE),
    }


class BloggerClient:
    """Idempotent Google Blogger API v3 publisher supporting dry-run exports, POST, and PUT."""

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

    def export_artifacts(
        self,
        article: ArticleRecord,
        *,
        series_articles: Optional[list[ArticleRecord]] = None,
    ) -> tuple[dict[str, Any], Path, Path]:
        payload, html_body = build_blogger_payload(
            article,
            self.config,
            series_articles=series_articles,
        )
        out_dir = self.config.exports_dir / article.slug
        out_dir.mkdir(parents=True, exist_ok=True)
        html_path = out_dir / "blogger_post.html"
        json_path = out_dir / "blogger_payload.json"
        html_path.write_text(html_body, encoding="utf-8")
        json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return payload, html_path, json_path

    def has_live_credentials(self) -> bool:
        return bool(
            self.config.blogger_blog_id
            and self.config.blogger_client_id
            and self.config.blogger_client_secret
            and self.config.blogger_refresh_token
        )

    def stage_or_publish(
        self,
        article: ArticleRecord,
        *,
        mode_override: Optional[str] = None,
        series_articles: Optional[list[ArticleRecord]] = None,
    ) -> dict[str, Any]:
        allowed_statuses = {"code_verified", "staged_draft", "published_live"}
        if article.status not in allowed_statuses:
            raise BloggerPublishError(
                f"Refusing to push unverified article '{article.slug}' to Blogger (status='{article.status}')"
            )

        effective_mode = (mode_override or self.config.blogger_publish_mode or "draft").strip().lower()
        is_draft_bool = effective_mode != "live"

        payload, html_path, json_path = self.export_artifacts(
            article,
            series_articles=series_articles,
        )

        if not self.has_live_credentials():
            return {
                "status": "dry_run",
                "reason": "Blogger OAuth2 credentials not configured in .env; exported ready-to-paste HTML & API v3 JSON.",
                "is_draft": is_draft_bool,
                "html_path": str(html_path),
                "payload_path": str(json_path),
                "title": payload["title"],
            }

        access_token = exchange_refresh_token(
            client_id=self.config.blogger_client_id,
            client_secret=self.config.blogger_client_secret,
            refresh_token=self.config.blogger_refresh_token,
            sender=self._sender,
            timeout_seconds=self.timeout_seconds,
        )

        is_update = bool(article.blogger_post_id)
        method = "PUT" if is_update else "POST"
        is_draft_param = "true" if is_draft_bool else "false"
        if is_update:
            url = (
                f"{BLOGGER_V3_BASE}/blogs/{self.config.blogger_blog_id}"
                f"/posts/{article.blogger_post_id}?isDraft={is_draft_param}"
            )
        else:
            url = (
                f"{BLOGGER_V3_BASE}/blogs/{self.config.blogger_blog_id}"
                f"/posts?isDraft={is_draft_param}"
            )

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        data = json.dumps(payload).encode("utf-8")

        res: Optional[HttpResult] = None
        for attempt in range(1, 4):
            req = urllib.request.Request(url=url, data=data, headers=headers, method=method)
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
            raise BloggerPublishError(
                f"Blogger API v3 {method} {url} failed with HTTP {res.status}: {preview}"
            )

        parsed = json.loads(res.body.decode("utf-8") or "{}")
        post_id = str(parsed.get("id") or article.blogger_post_id or "").strip()
        post_url = str(parsed.get("url") or "").strip()
        article.blogger_post_id = post_id or None
        if post_url.startswith("http://"):
            post_url = "https://" + post_url[len("http://") :]
        if post_url:
            article.blogger_url = post_url

        return {
            "status": "staged_draft" if is_draft_bool else "published_live",
            "operation": "updated" if is_update else "created",
            "blogger_post_id": post_id,
            "blogger_url": article.blogger_url,
            "is_draft": is_draft_bool,
            "html_path": str(html_path),
            "payload_path": str(json_path),
        }
