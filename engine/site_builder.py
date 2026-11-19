"""Accessible, responsive dark/light static HTML5 canonical blog, RSS 2.0 & Sitemap builder."""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any, Optional
import xml.etree.ElementTree as ET

from engine.config import AppConfig
from engine.models import PILLAR_META, SERIES_30D_NAME, ArticleRecord
from engine.pin_renderer import PIN_HEIGHT, PIN_WIDTH, generate_pin_assets
from engine.pinterest_client import build_pinterest_pin_copy
from engine.storefronts import append_storefront_utm

SITE_STYLESHEET = """/* Zero-Dependency Accessible Engineering Publication Stylesheet */
:root {
  color-scheme: light dark;
  --bg: #f8fafc;
  --surface: #ffffff;
  --surface-subtle: #f1f5f9;
  --border: #cbd5e1;
  --text: #0f172a;
  --text-muted: #334155;
  --accent: #0f766e;
  --accent-strong: #115e59;
  --accent-contrast: #ffffff;
  --accent-soft: #ccfbf1;
  --badge-ok-bg: #dcfce7;
  --badge-ok-fg: #14532d;
  --badge-warn-bg: #fef3c7;
  --badge-warn-fg: #78350f;
  --code-bg: #0f172a;
  --code-fg: #f8fafc;
  --focus-ring: #0284c7;
  --max-prose: 76ch;
}

@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #0b1120;
    --surface: #111827;
    --surface-subtle: #1e293b;
    --border: #334155;
    --text: #f8fafc;
    --text-muted: #cbd5e1;
    --accent: #2dd4bf;
    --accent-strong: #5eead4;
    --accent-contrast: #042f2e;
    --accent-soft: #134e4a;
    --badge-ok-bg: #052e16;
    --badge-ok-fg: #86efac;
    --badge-warn-bg: #451a03;
    --badge-warn-fg: #fde68a;
    --code-bg: #020617;
    --code-fg: #e2e8f0;
    --focus-ring: #38bdf8;
  }
}

:root[data-theme="dark"] {
  --bg: #0b1120;
  --surface: #111827;
  --surface-subtle: #1e293b;
  --border: #334155;
  --text: #f8fafc;
  --text-muted: #cbd5e1;
  --accent: #2dd4bf;
  --accent-strong: #5eead4;
  --accent-contrast: #042f2e;
  --accent-soft: #134e4a;
  --badge-ok-bg: #052e16;
  --badge-ok-fg: #86efac;
  --badge-warn-bg: #451a03;
  --badge-warn-fg: #fde68a;
  --code-bg: #020617;
  --code-fg: #e2e8f0;
  --focus-ring: #38bdf8;
}

*, *::before, *::after {
  box-sizing: border-box;
}

html {
  font-size: 100%;
  -webkit-text-size-adjust: 100%;
  max-width: 100%;
  overflow-x: clip;
}

body {
  margin: 0;
  background-color: var(--bg);
  color: var(--text);
  font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  line-height: 1.65;
  max-width: 100%;
  overflow-x: clip;
}

main, section, article, header, footer, div, p, li, h1, h2, h3, h4 {
  min-width: 0;
  max-width: 100%;
}

:not(pre) > code, p, li, h1, h2, h3, h4, td, th, .tabular {
  overflow-wrap: anywhere;
  word-break: break-word;
}

.tabular, time, table, code, .stat-value, .badge {
  font-variant-numeric: tabular-nums;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.skip-link {
  position: absolute;
  left: -9999px;
  top: 0.5rem;
  z-index: 1000;
  padding: 0.75rem 1.25rem;
  min-height: 44px;
  background: var(--accent);
  color: var(--accent-contrast);
  font-weight: 700;
  border-radius: 0.5rem;
  text-decoration: none;
}

.skip-link:focus {
  left: 0.75rem;
}

:focus-visible {
  outline: 3px solid var(--focus-ring);
  outline-offset: 2px;
}

.container {
  width: 100%;
  max-width: 1080px;
  margin: 0 auto;
  padding: 0 1rem;
}

.site-header {
  background: var(--surface);
  border-bottom: 1px solid var(--border);
}

.header-inner {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  padding: 0.85rem 0;
}

.brand-link {
  display: inline-flex;
  align-items: center;
  min-height: 44px;
  min-width: 44px;
  max-width: 100%;
  font-weight: 800;
  font-size: 1.05rem;
  color: var(--text);
  text-decoration: none;
  letter-spacing: -0.01em;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.nav-list {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
  list-style: none;
  margin: 0;
  padding: 0;
  max-width: 100%;
}

.nav-list > li {
  min-width: 0;
  max-width: 100%;
}

.nav-link, .btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 44px;
  min-width: 44px;
  max-width: 100%;
  padding: 0.5rem 0.9rem;
  border-radius: 0.5rem;
  font-size: 0.92rem;
  font-weight: 600;
  line-height: 1.25;
  white-space: normal;
  text-align: center;
  overflow-wrap: anywhere;
  word-break: break-word;
  text-decoration: none;
  cursor: pointer;
  border: 1px solid var(--border);
  background: var(--surface-subtle);
  color: var(--text);
}

.nav-link:hover, .btn:hover {
  border-color: var(--accent);
}

.btn-primary {
  background: var(--accent);
  color: var(--accent-contrast);
  border-color: var(--accent);
}

.btn-primary:hover {
  background: var(--accent-strong);
  border-color: var(--accent-strong);
}

.hero {
  padding: 2.25rem 0 1.75rem;
  border-bottom: 1px solid var(--border);
}

.hero h1 {
  margin: 0 0 0.75rem;
  font-size: clamp(1.65rem, 3.5vw, 2.35rem);
  line-height: 1.2;
  letter-spacing: -0.02em;
}

.hero p {
  margin: 0 0 1.25rem;
  max-width: 68ch;
  color: var(--text-muted);
  font-size: 1.05rem;
}

.cta-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
}

.section-block {
  padding: 2rem 0;
}

.section-title {
  margin: 0 0 1.25rem;
  font-size: 1.35rem;
  letter-spacing: -0.01em;
}

.grid-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 300px), 1fr));
  gap: 1.25rem;
}

.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 0.75rem;
  padding: 1.25rem;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 1rem;
}

.card h3 {
  margin: 0.35rem 0 0.5rem;
  font-size: 1.15rem;
  line-height: 1.35;
}

.card h3 a {
  display: inline-flex;
  align-items: center;
  min-height: 44px;
  min-width: 44px;
  max-width: 100%;
  padding: 0.35rem 0;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.card p {
  margin: 0;
  color: var(--text-muted);
  font-size: 0.95rem;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.meta-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.84rem;
  color: var(--text-muted);
  min-width: 0;
  max-width: 100%;
}

.badge {
  display: inline-flex;
  align-items: center;
  max-width: 100%;
  padding: 0.2rem 0.55rem;
  border-radius: 999px;
  font-size: 0.78rem;
  font-weight: 700;
  white-space: normal;
  overflow-wrap: anywhere;
  word-break: break-word;
  background: var(--surface-subtle);
  color: var(--text);
  border: 1px solid var(--border);
}

.badge-verified {
  background: var(--badge-ok-bg);
  color: var(--badge-ok-fg);
  border-color: transparent;
}

.badge-warn {
  background: var(--badge-warn-bg);
  color: var(--badge-warn-fg);
  border-color: transparent;
}

.meter-track {
  width: 100%;
  height: 0.75rem;
  border-radius: 999px;
  background: var(--surface-subtle);
  border: 1px solid var(--border);
  overflow: hidden;
  margin: 0.5rem 0;
}

.meter-fill {
  height: 100%;
  background: var(--accent);
  border-radius: 999px;
}

.pin-thumb-link {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 44px;
  min-width: 44px;
  max-width: 100%;
}

.pin-thumb {
  display: block;
  width: 100%;
  max-width: 180px;
  height: auto;
  aspect-ratio: 2 / 3;
  border-radius: 0.5rem;
  border: 1px solid var(--border);
}

/* Article h-entry layout */
.article-shell {
  max-width: 820px;
  margin: 0 auto;
  padding: 2rem 0 3rem;
}

.article-header {
  margin-bottom: 1.75rem;
  padding-bottom: 1.25rem;
  border-bottom: 1px solid var(--border);
}

.article-header h1 {
  margin: 0.6rem 0 0.75rem;
  font-size: clamp(1.6rem, 3.4vw, 2.25rem);
  line-height: 1.22;
}

.article-subtitle {
  margin: 0 0 1rem;
  font-size: 1.08rem;
  color: var(--text-muted);
}

.e-content {
  overflow-wrap: break-word;
}

.e-content h2 {
  margin: 2rem 0 0.75rem;
  font-size: 1.4rem;
  line-height: 1.3;
}

.e-content h3 {
  margin: 1.5rem 0 0.6rem;
  font-size: 1.18rem;
}

.e-content p, .e-content li {
  max-width: var(--max-prose);
}

.e-content a, .p-author, .site-footer a {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  min-height: 44px;
  min-width: 44px;
  max-width: 100%;
  padding: 0.25rem 0.15rem;
  color: var(--accent);
  text-underline-offset: 3px;
  font-weight: 600;
  overflow-wrap: anywhere;
  word-break: break-word;
}

:not(pre) > code, .e-content code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 0.88em;
  background: var(--surface-subtle);
  padding: 0.12rem 0.35rem;
  border-radius: 0.3rem;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.code-frame {
  margin: 1.25rem 0;
  border: 1px solid var(--border);
  border-radius: 0.65rem;
  overflow: hidden;
  background: var(--code-bg);
}

.code-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.35rem 0.75rem;
  background: #1e293b;
  color: #e2e8f0;
  font-size: 0.8rem;
}

.copy-code-btn {
  min-height: 44px;
  min-width: 88px;
  padding: 0.35rem 0.75rem;
  border-radius: 0.4rem;
  border: 1px solid #475569;
  background: #0f172a;
  color: #f8fafc;
  font-weight: 600;
  cursor: pointer;
}

.copy-code-btn:hover {
  border-color: #38bdf8;
}

.code-frame pre {
  margin: 0;
  padding: 1rem;
  overflow-x: auto;
  max-width: 100%;
  color: var(--code-fg);
  font-size: 0.86rem;
  line-height: 1.55;
}

.code-frame pre code {
  background: transparent;
  padding: 0;
  color: inherit;
}

.table-scroll {
  width: 100%;
  overflow-x: auto;
  margin: 1.25rem 0;
  border: 1px solid var(--border);
  border-radius: 0.5rem;
}

table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.92rem;
}

th, td {
  text-align: left;
  padding: 0.65rem 0.85rem;
  border-bottom: 1px solid var(--border);
  vertical-align: top;
}

th {
  background: var(--surface-subtle);
  font-weight: 700;
}

hr {
  border: 0;
  border-top: 1px solid var(--border);
  margin: 2rem 0;
}

.site-footer {
  border-top: 1px solid var(--border);
  background: var(--surface);
  padding: 1.75rem 0;
  margin-top: 2rem;
  color: var(--text-muted);
  font-size: 0.9rem;
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}
"""

THEME_AND_COPY_JS = """<script>
(function () {
  var root = document.documentElement;
  var saved = null;
  try { saved = localStorage.getItem('autopilot_theme'); } catch (e) {}
  if (saved === 'dark' || saved === 'light') {
    root.setAttribute('data-theme', saved);
  }
  var toggle = document.getElementById('theme-toggle');
  function syncPressed() {
    if (!toggle) return;
    var isDark = root.getAttribute('data-theme') === 'dark' ||
      (!root.getAttribute('data-theme') && window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
    toggle.setAttribute('aria-pressed', isDark ? 'true' : 'false');
    toggle.textContent = isDark ? 'Light Theme' : 'Dark Theme';
  }
  syncPressed();
  if (toggle) {
    toggle.addEventListener('click', function () {
      var current = root.getAttribute('data-theme');
      var prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
      var next = current ? (current === 'dark' ? 'light' : 'dark') : (prefersDark ? 'light' : 'dark');
      root.setAttribute('data-theme', next);
      try { localStorage.setItem('autopilot_theme', next); } catch (e) {}
      syncPressed();
    });
  }
  var announcer = document.getElementById('live-announcer');
  document.addEventListener('click', function (ev) {
    var btn = ev.target.closest('.copy-code-btn');
    if (!btn) return;
    var frame = btn.closest('.code-frame');
    var codeEl = frame ? frame.querySelector('pre code') : null;
    if (!codeEl) return;
    var text = codeEl.textContent || '';
    function markCopied() {
      var orig = btn.getAttribute('data-label') || 'Copy code';
      btn.textContent = 'Copied!';
      if (announcer) announcer.textContent = 'Code snippet copied to clipboard.';
      setTimeout(function () { btn.textContent = orig; }, 1800);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(markCopied).catch(markCopied);
    } else {
      markCopied();
    }
  });
  try {
    var inbound = new URLSearchParams(window.location.search || '');
    var inSrc = inbound.get('utm_source');
    if (inSrc) {
      var inMed = inbound.get('utm_medium') || 'referral';
      var inCamp = inbound.get('utm_campaign') || '30d_exp_2026_11';
      var inSlug = inbound.get('slug') || '';
      var anchors = document.querySelectorAll('a[href^="https://"]');
      for (var i = 0; i < anchors.length; i++) {
        var href = anchors[i].getAttribute('href') || '';
        if (href.indexOf('utm_source=canonical_site') !== -1) {
          var u = new URL(href);
          u.searchParams.set('utm_source', inSrc);
          u.searchParams.set('utm_medium', inMed);
          u.searchParams.set('utm_campaign', inCamp);
          if (inSlug && !u.searchParams.get('slug')) u.searchParams.set('slug', inSlug);
          if (u.hostname === 'lemonsqueezy.com' || u.hostname.slice(-17) === '.lemonsqueezy.com' || u.searchParams.has('checkout[custom][utm_source]')) {
            u.searchParams.set('checkout[custom][utm_source]', inSrc);
            u.searchParams.set('checkout[custom][utm_medium]', inMed);
            u.searchParams.set('checkout[custom][utm_campaign]', inCamp);
            var effSlug = u.searchParams.get('slug') || inSlug;
            if (effSlug) u.searchParams.set('checkout[custom][slug]', effSlug);
          }
          anchors[i].setAttribute('href', u.toString());
        }
      }
    }
  } catch (e) {}
})();
</script>"""


INLINE_CODE_RE = re.compile(r"`([^`]+)`")
INLINE_MARKUP_RE = re.compile(
    r"(\[[^\]]+\]\(https://[^)]+\)|\*\*[^*]+\*\*|\*[^*]+\*)"
)


def _render_inline_no_code(text: str) -> str:
    """Recursively render links, **bold**, and *italic* on text whose `code` spans are tokenized."""
    parts: list[str] = []
    last_idx = 0
    for match in INLINE_MARKUP_RE.finditer(text):
        if match.start() > last_idx:
            parts.append(html.escape(text[last_idx : match.start()]))
        tok = match.group(0)
        if tok.startswith("**") and tok.endswith("**"):
            parts.append(f"<strong>{_render_inline_no_code(tok[2:-2])}</strong>")
        elif tok.startswith("*") and tok.endswith("*"):
            parts.append(f"<em>{_render_inline_no_code(tok[1:-1])}</em>")
        elif tok.startswith("["):
            sep = tok.find("](")
            label = tok[1:sep]
            url = tok[sep + 2 : -1]
            parts.append(
                f'<a href="{html.escape(url, quote=True)}" rel="noopener">{_render_inline_no_code(label)}</a>'
            )
        last_idx = match.end()
    if last_idx < len(text):
        parts.append(html.escape(text[last_idx:]))
    return "".join(parts)


def _render_inline(text: str) -> str:
    """Safely render inline Markdown (`code`, **bold**, *italic*, [link](https://...)) including nested code."""
    sanitized = text.replace("\x00", "")
    code_spans: list[str] = []

    def _stash_code(match: re.Match[str]) -> str:
        idx = len(code_spans)
        code_spans.append(match.group(1))
        return f"\x00CODE{idx}\x00"

    tokenized = INLINE_CODE_RE.sub(_stash_code, sanitized)
    rendered = _render_inline_no_code(tokenized)
    for idx, raw_code in enumerate(code_spans):
        rendered = rendered.replace(
            f"\x00CODE{idx}\x00", f"<code>{html.escape(raw_code)}</code>"
        )
    return rendered


def render_markdown_to_html(markdown_text: str) -> str:
    """Deterministic zero-dependency Markdown to semantic HTML5 converter."""
    cleaned = re.sub(r"<!--.*?-->", "", markdown_text, flags=re.DOTALL)
    lines = cleaned.splitlines()
    out: list[str] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        # Fenced code block
        if stripped.startswith("```"):
            lang = stripped[3:].strip() or "text"
            code_lines: list[str] = []
            i += 1
            while i < n and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            if i < n:
                i += 1
            escaped_code = html.escape("\n".join(code_lines))
            safe_lang = html.escape(lang.upper())
            out.append(
                f'<div class="code-frame">'
                f'<div class="code-toolbar"><span>{safe_lang}</span>'
                f'<button type="button" class="copy-code-btn" data-label="Copy code" aria-label="Copy {safe_lang} code snippet">Copy code</button>'
                f"</div>"
                f"<pre><code>{escaped_code}</code></pre>"
                f"</div>"
            )
            continue

        # Horizontal rule
        if stripped in {"---", "***", "___"}:
            out.append("<hr />")
            i += 1
            continue

        # Headings (clamp # to h2 inside article body so h1.p-name stays unique)
        if stripped.startswith("#"):
            hashes = len(stripped) - len(stripped.lstrip("#"))
            level = min(4, max(2, hashes))
            heading_text = stripped[hashes:].strip()
            out.append(f"<h{level}>{_render_inline(heading_text)}</h{level}>")
            i += 1
            continue

        # Markdown table
        if stripped.startswith("|") and (i + 1) < n and re.match(r"^\|\s*[:\-]+", lines[i + 1].strip()):
            header_cells = [c.strip() for c in stripped.strip("|").split("|")]
            i += 2  # skip header + alignment separator
            body_rows: list[list[str]] = []
            while i < n and lines[i].strip().startswith("|"):
                body_rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            th_html = "".join(f'<th scope="col">{_render_inline(c)}</th>' for c in header_cells)
            trs_html: list[str] = []
            for r in body_rows:
                tds = "".join(f"<td>{_render_inline(c)}</td>" for c in r)
                trs_html.append(f"<tr>{tds}</tr>")
            out.append(
                f'<div class="table-scroll"><table><thead><tr>{th_html}</tr></thead>'
                f'<tbody>{"".join(trs_html)}</tbody></table></div>'
            )
            continue

        # Unordered list
        if stripped.startswith("- ") or stripped.startswith("* "):
            items: list[str] = []
            while i < n and (lines[i].strip().startswith("- ") or lines[i].strip().startswith("* ")):
                items.append(f"<li>{_render_inline(lines[i].strip()[2:].strip())}</li>")
                i += 1
            out.append(f'<ul>{"".join(items)}</ul>')
            continue

        # Ordered list
        if re.match(r"^\d+\.\s+", stripped):
            items = []
            while i < n and re.match(r"^\d+\.\s+", lines[i].strip()):
                item_text = re.sub(r"^\d+\.\s+", "", lines[i].strip())
                items.append(f"<li>{_render_inline(item_text)}</li>")
                i += 1
            out.append(f'<ol>{"".join(items)}</ol>')
            continue

        # Paragraph (join consecutive normal lines)
        para_lines: list[str] = []
        while (
            i < n
            and lines[i].strip()
            and not lines[i].strip().startswith(("```", "#", "|", "- ", "* "))
            and lines[i].strip() not in {"---", "***", "___"}
            and not re.match(r"^\d+\.\s+", lines[i].strip())
        ):
            para_lines.append(lines[i].strip())
            i += 1
        out.append(f"<p>{_render_inline(' '.join(para_lines))}</p>")

    return "\n".join(out)


def _canonical_store_url(
    url: str,
    config: AppConfig,
    *,
    medium: str = "hub",
    slug: str = "",
    sku: str = "",
) -> str:
    return append_storefront_utm(
        url,
        source="canonical_site",
        medium=medium,
        campaign="30d_exp_2026_11",
        slug=slug,
        sku=sku,
        configured_ls_url=config.lemonsqueezy_store_url,
    )


def _rewrite_html_utm_for_canonical_site(
    rendered_html: str,
    slug: str,
    config: AppConfig,
) -> str:
    def _replace_href(match: re.Match[str]) -> str:
        raw_url = html.unescape(match.group(1))
        if "utm_source=devto" not in raw_url:
            return match.group(0)
        rewritten = _canonical_store_url(raw_url, config, medium="article", slug=slug)
        return f'href="{html.escape(rewritten, quote=True)}"'

    return re.sub(r'href="([^"]+)"', _replace_href, rendered_html)


def _page_shell(
    *,
    title: str,
    description: str,
    canonical_url: str,
    css_href: str,
    home_href: str,
    feed_href: str,
    config: AppConfig,
    main_html: str,
) -> str:
    safe_title = html.escape(title)
    safe_desc = html.escape(description, quote=True)
    safe_canon = html.escape(canonical_url, quote=True)
    safe_author = html.escape(config.author_name)
    safe_kofi = html.escape(config.kofi_profile_url, quote=True)
    safe_store = html.escape(
        _canonical_store_url(config.paid_checkout_url, config, medium="nav"),
        quote=True,
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{safe_title}</title>
  <meta name="description" content="{safe_desc}" />
  <meta name="author" content="{safe_author}" />
  <link rel="canonical" href="{safe_canon}" />
  <link rel="alternate" type="application/rss+xml" title="{safe_author} — Verified Reliability RSS" href="{html.escape(feed_href, quote=True)}" />
  <meta property="og:title" content="{safe_title}" />
  <meta property="og:description" content="{safe_desc}" />
  <meta property="og:url" content="{safe_canon}" />
  <meta property="og:type" content="article" />
  <link rel="stylesheet" href="{html.escape(css_href, quote=True)}" />
</head>
<body>
  <a class="skip-link" href="#main-content">Skip to main content</a>
  <div id="live-announcer" class="sr-only" role="status" aria-live="polite" aria-atomic="true"></div>
  <header class="site-header">
    <div class="container header-inner">
      <a class="brand-link" href="{html.escape(home_href, quote=True)}">{safe_author} · Reliability Lab</a>
      <nav aria-label="Primary">
        <ul class="nav-list">
          <li><a class="nav-link" href="{html.escape(home_href, quote=True)}">Tutorials</a></li>
          <li><a class="nav-link" href="{html.escape(feed_href, quote=True)}">RSS Feed</a></li>
          <li><a class="nav-link btn-primary" href="{safe_store}" rel="noopener">Starter Kits &amp; Storefronts</a></li>
          <li><button type="button" id="theme-toggle" class="btn" aria-pressed="false" aria-label="Switch color theme">Dark Theme</button></li>
        </ul>
      </nav>
    </div>
  </header>
  <main id="main-content">
{main_html}
  </main>
  <footer class="site-footer">
    <div class="container">
      <p>Curated by <a href="{safe_kofi}" rel="noopener" style="color:var(--accent);font-weight:600;">{safe_author}</a> ({html.escape(config.timezone)}). Every published Python &amp; shell snippet is verified inside an isolated <code>python3 -I</code> sandbox before release.</p>
    </div>
  </footer>
  {THEME_AND_COPY_JS}
</body>
</html>
"""


def _render_article_page(article: ArticleRecord, config: AppConfig) -> str:
    meta = PILLAR_META.get(article.pillar, PILLAR_META["pillar_a"])
    pub_iso = article.created_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    pub_date_short = pub_iso[:10]
    rendered_body = _rewrite_html_utm_for_canonical_site(
        render_markdown_to_html(article.markdown_body),
        article.slug,
        config,
    )
    dur_ms = article.verification.get("total_duration_ms", 0)
    blocks_n = article.verification.get("code_blocks_tested", 0)

    tags_badges = "".join(
        f'<span class="badge">#{html.escape(t)}</span>' for t in article.tags[:4]
    )

    main_html = f"""    <div class="container article-shell">
      <article class="h-entry">
        <header class="article-header">
          <div class="meta-row">
            <span class="badge badge-verified">SANDBOX VERIFIED · {blocks_n} BLOCKS ({dur_ms} ms)</span>
            <span class="badge">{html.escape(meta["weekday"])} Pillar</span>
            <time class="dt-published tabular" datetime="{html.escape(pub_iso, quote=True)}">{html.escape(pub_date_short)}</time>
            <span>by <a class="p-author h-card" href="{html.escape(config.kofi_profile_url, quote=True)}" rel="author noopener" style="color:var(--accent);font-weight:600;">{html.escape(config.author_name)}</a></span>
          </div>
          <h1 class="p-name">{html.escape(article.title)}</h1>
          <p class="p-summary article-subtitle">{html.escape(article.subtitle)}</p>
          <div class="meta-row">{tags_badges}</div>
        </header>
        <div class="e-content">
{rendered_body}
        </div>
      </article>
    </div>"""

    return _page_shell(
        title=f"{article.title} — {config.author_name}",
        description=article.subtitle,
        canonical_url=article.canonical_url,
        css_href="../../style.css",
        home_href="../../index.html",
        feed_href="../../feed.xml",
        config=config,
        main_html=main_html,
    )


def _verdict_badge_class(verdict: str) -> str:
    if verdict.startswith("WORKING"):
        return "badge badge-verified"
    if verdict.startswith("BOTTLENECK"):
        return "badge badge-warn"
    return "badge"


def _render_index_page(
    articles: list[ArticleRecord],
    products: list[dict[str, Any]],
    config: AppConfig,
    *,
    experiment_report: Optional[dict[str, Any]] = None,
) -> str:
    cards_html: list[str] = []
    for idx, art in enumerate(reversed(articles), start=1):
        meta = PILLAR_META.get(art.pillar, PILLAR_META["pillar_a"])
        dur_ms = art.verification.get("total_duration_ms", 0)
        day_num = art.series_day or (len(articles) - idx + 1)
        pin_href = f"./pins/{html.escape(art.slug, quote=True)}.png"
        blogger_btn = (
            f'<a class="btn" href="{html.escape(art.blogger_url, quote=True)}" rel="noopener">Read on Blogger</a>'
            if art.blogger_url
            else ""
        )
        cards_html.append(
            f"""        <article class="card">
          <div>
            <div class="meta-row">
              <span class="badge badge-verified">DAY {day_num}/30 · VERIFIED ({dur_ms} ms)</span>
              <span class="badge">{html.escape(meta["weekday"])}</span>
              <span class="tabular">{art.word_count} words</span>
            </div>
            <h3><a href="./posts/{html.escape(art.slug, quote=True)}/index.html" style="color:var(--text);text-decoration:none;">{html.escape(art.title)}</a></h3>
            <p>{html.escape(art.subtitle)}</p>
          </div>
          <div>
            <a class="pin-thumb-link" href="{pin_href}" aria-label="Open 1000x1500 Pinterest infographic for {html.escape(art.title, quote=True)}">
              <img class="pin-thumb" src="{pin_href}" width="200" height="300" loading="lazy" alt="1000x1500 benchmark infographic for {html.escape(art.title, quote=True)}" />
            </a>
          </div>
          <div class="cta-row">
            <a class="btn btn-primary" href="./posts/{html.escape(art.slug, quote=True)}/index.html">Read Verified Benchmark</a>
            <a class="btn" href="{pin_href}">View 2:3 Pin PNG</a>
            {blogger_btn}
          </div>
        </article>"""
        )

    prod_cards: list[str] = []
    for prod in products:
        is_free = int(prod.get("price_usd", 0)) == 0
        price_label = "$0+ Pay-What-You-Want" if is_free else f"${prod['price_usd']} USD · MIT License"
        raw_store = config.free_checkout_url if is_free else config.paid_checkout_url
        target_store = _canonical_store_url(
            raw_store,
            config,
            medium="hub_sku",
            sku=str(prod.get("slug", "")),
        )
        store_tag = "Gumroad / Instant Lead Magnet" if is_free else "Lemon Squeezy / Merchant of Record"
        prod_cards.append(
            f"""        <div class="card">
          <div>
            <div class="meta-row">
              <span class="badge badge-verified">UNIT TESTED .ZIP</span>
              <span class="badge tabular">{html.escape(price_label)}</span>
              <span class="badge">{html.escape(store_tag)}</span>
            </div>
            <h3>{html.escape(str(prod.get("title", "")))}</h3>
            <p>Artifact <code>{html.escape(str(prod.get("zip_filename", "")))}</code> ({int(prod.get("size_bytes", 0)):,} bytes · SHA-256 <code>{html.escape(str(prod.get("sha256", ""))[:12])}</code>).</p>
          </div>
          <div class="cta-row">
            <a class="btn btn-primary" href="{html.escape(target_store, quote=True)}" rel="noopener">Get on Storefront ({html.escape(price_label)})</a>
          </div>
        </div>"""
        )

    exp = experiment_report or {}
    pt_meter = (exp.get("storefronts") or {}).get("patreon") or {
        "net_usd": 0.0,
        "threshold_usd": 25.0,
        "unlock_progress_pct": 0.0,
    }
    ls_meter = (exp.get("storefronts") or {}).get("lemonsqueezy") or {
        "net_usd": 0.0,
        "threshold_usd": 50.0,
        "unlock_progress_pct": 0.0,
    }
    gr_meter = (exp.get("storefronts") or {}).get("gumroad") or {
        "net_usd": 0.0,
        "threshold_usd": 100.0,
        "unlock_progress_pct": 0.0,
    }
    ch_rows_html: list[str] = []
    for ch_key, ch_data in (exp.get("channels") or {}).items():
        v_txt = str(ch_data.get("verdict", "NOT WORKING — PAUSE & REALLOCATE"))
        v_cls = _verdict_badge_class(v_txt)
        ch_rows_html.append(
            f"<tr>"
            f"<td><code>{html.escape(ch_key)}</code></td>"
            f'<td class="tabular">{int(ch_data.get("posts_prepared", 0))}</td>'
            f'<td class="tabular">{int(ch_data.get("views", 0))}</td>'
            f'<td class="tabular">{int(ch_data.get("clicks", 0))}</td>'
            f'<td class="tabular">${float(ch_data.get("net_usd", 0.0)):.2f}</td>'
            f'<td><span class="{v_cls}">{html.escape(v_txt)}</span></td>'
            f"</tr>"
        )
    scoreboard_table = (
        f"""        <div class="table-scroll">
          <table>
            <thead>
              <tr>
                <th scope="col">Channel</th>
                <th scope="col">Assets</th>
                <th scope="col">Views</th>
                <th scope="col">Clicks</th>
                <th scope="col">Net USD</th>
                <th scope="col">Month-2 Verdict</th>
              </tr>
            </thead>
            <tbody>
              {"".join(ch_rows_html)}
            </tbody>
          </table>
        </div>"""
        if ch_rows_html
        else ""
    )

    articles_section = (
        "\n".join(cards_html)
        if cards_html
        else '<p class="tabular">No verified tutorials built yet. Run <code>python3 autopilot.py run-daily</code>.</p>'
    )

    hero_paid_url = _canonical_store_url(
        config.paid_checkout_url,
        config,
        medium="hero",
        sku="production-agent-automation-kit",
    )
    hero_free_url = _canonical_store_url(
        config.free_checkout_url,
        config,
        medium="hero",
        sku="free-idempotent-cron-mini-kit",
    )
    meter_paid_url = _canonical_store_url(
        config.paid_checkout_url,
        config,
        medium="scoreboard",
        sku="production-agent-automation-kit",
    )
    meter_free_url = _canonical_store_url(
        config.free_checkout_url,
        config,
        medium="scoreboard",
        sku="free-idempotent-cron-mini-kit",
    )

    main_html = f"""    <section class="hero">
      <div class="container">
        <span class="badge badge-verified">30-DAY SERIES · 100% STDLIB SANDBOX VERIFIED</span>
        <h1>{html.escape(SERIES_30D_NAME)}</h1>
        <p>Daily Blogger, Patreon &amp; Pinterest 2:3 visual engineering series plus Mon/Wed/Fri DEV.to teardowns by {html.escape(config.author_name)} ({html.escape(config.experiment_start_date)} to {html.escape(config.experiment_end_date)}). Every snippet runs inside an isolated <code>python3 -I</code> sandbox before release.</p>
        <div class="cta-row">
          <a class="btn btn-primary" href="{html.escape(hero_paid_url, quote=True)}" rel="noopener">Get $14 Starter Kit</a>
          <a class="btn" href="{html.escape(hero_free_url, quote=True)}" rel="noopener">Download Free $0+ Mini-Kit</a>
          <a class="btn" href="./feed.xml">Article RSS 2.0</a>
          <a class="btn" href="./pinterest_feed.xml">Pinterest Auto-Publish RSS</a>
        </div>
      </div>
    </section>
    <section class="section-block" aria-labelledby="scoreboard-heading">
      <div class="container">
        <h2 id="scoreboard-heading" class="section-title">30-Day Experiment Scoreboard (Nov 18 → Dec 18, 2026)</h2>
        <div class="grid-cards">
          <div class="card">
            <div>
              <div class="meta-row">
                <span class="badge badge-verified">STOREFRONT #1 · PATREON</span>
                <span class="badge tabular">${float(pt_meter["net_usd"]):.2f} / ${float(pt_meter["threshold_usd"]):.0f} USD</span>
              </div>
              <h3>Patreon Payout Meter ($25 Payoneer)</h3>
              <div class="meter-track" role="progressbar" aria-label="Patreon $25 payout progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="{int(round(float(pt_meter['unlock_progress_pct'])))}">
                <div class="meter-fill" style="width:{min(100.0, max(0.0, float(pt_meter['unlock_progress_pct']))):.1f}%;"></div>
              </div>
              <p class="tabular">{float(pt_meter["unlock_progress_pct"]):.1f}% toward $25 USD Payoneer payout unlock ($5/mo Builder Tier &amp; $14 Shop).</p>
            </div>
            <div class="cta-row">
              <a class="btn btn-primary" href="{html.escape(config.patreon_url or meter_paid_url, quote=True)}" rel="noopener">Open Patreon Tiers &amp; Shop</a>
            </div>
          </div>
          <div class="card">
            <div>
              <div class="meta-row">
                <span class="badge badge-verified">MERCHANT OF RECORD #2</span>
                <span class="badge tabular">${float(ls_meter["net_usd"]):.2f} / ${float(ls_meter["threshold_usd"]):.0f} USD</span>
              </div>
              <h3>Lemon Squeezy Payout Meter ($50 Bank Wire)</h3>
              <div class="meter-track" role="progressbar" aria-label="Lemon Squeezy $50 payout progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="{int(round(float(ls_meter['unlock_progress_pct'])))}">
                <div class="meter-fill" style="width:{min(100.0, max(0.0, float(ls_meter['unlock_progress_pct']))):.1f}%;"></div>
              </div>
              <p class="tabular">{float(ls_meter["unlock_progress_pct"]):.1f}% toward $50 USD Sri Lanka bank wire unlock (4 × $14 sales).</p>
            </div>
            <div class="cta-row">
              <a class="btn btn-primary" href="{html.escape(meter_paid_url, quote=True)}" rel="noopener">Open Paid Checkout</a>
            </div>
          </div>
          <div class="card">
            <div>
              <div class="meta-row">
                <span class="badge badge-verified">MERCHANT OF RECORD #2</span>
                <span class="badge tabular">${float(gr_meter["net_usd"]):.2f} / ${float(gr_meter["threshold_usd"]):.0f} USD</span>
              </div>
              <h3>Gumroad Payout Meter ($100 Local Bank Deposit)</h3>
              <div class="meter-track" role="progressbar" aria-label="Gumroad $100 payout progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="{int(round(float(gr_meter['unlock_progress_pct'])))}">
                <div class="meter-fill" style="width:{min(100.0, max(0.0, float(gr_meter['unlock_progress_pct']))):.1f}%;"></div>
              </div>
              <p class="tabular">{float(gr_meter["unlock_progress_pct"]):.1f}% toward $100 USD Sri Lanka bank deposit unlock (instant $0+ lead magnet).</p>
            </div>
            <div class="cta-row">
              <a class="btn" href="{html.escape(meter_free_url, quote=True)}" rel="noopener">Open Free $0+ Kit</a>
            </div>
          </div>
        </div>
        {scoreboard_table}
      </div>
    </section>
    <section class="section-block" aria-labelledby="kits-heading">
      <div class="container">
        <h2 id="kits-heading" class="section-title">Downloadable MIT-Licensed Reliability Kits</h2>
        <div class="grid-cards">
{chr(10).join(prod_cards)}
        </div>
      </div>
    </section>
    <section class="section-block" aria-labelledby="tutorials-heading">
      <div class="container">
        <h2 id="tutorials-heading" class="section-title">Verified Engineering Teardowns &amp; 2:3 Pins (<span class="tabular">{len(articles)}</span> / 30)</h2>
        <div class="grid-cards">
{articles_section}
        </div>
      </div>
    </section>"""

    return _page_shell(
        title=f"{config.author_name} — Zero-Cost Production Reliability Lab",
        description="Empirically benchmarked Python, SQLite WAL, HMAC webhook, and AI circuit-breaker teardowns.",
        canonical_url=f"{config.canonical_base_url}/",
        css_href="./style.css",
        home_href="./index.html",
        feed_href="./feed.xml",
        config=config,
        main_html=main_html,
    )


def _iso_to_rfc2822(iso_str: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except ValueError:
        dt = datetime.now(timezone.utc)
    return format_datetime(dt)


def _build_rss_xml(articles: list[ArticleRecord], config: AppConfig) -> str:
    ET.register_namespace("atom", "http://www.w3.org/2005/Atom")
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = f"{config.author_name} — Zero-Cost Production Reliability"
    ET.SubElement(channel, "link").text = f"{config.canonical_base_url}/"
    ET.SubElement(channel, "description").text = (
        "Sandbox-verified Python, SQLite WAL, HMAC webhook, and fault-tolerant AI benchmarks."
    )
    ET.SubElement(channel, "language").text = "en-us"
    ET.SubElement(
        channel,
        "{http://www.w3.org/2005/Atom}link",
        {
            "href": f"{config.canonical_base_url}/feed.xml",
            "rel": "self",
            "type": "application/rss+xml",
        },
    )

    for art in reversed(articles):
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = art.title
        ET.SubElement(item, "link").text = art.canonical_url
        guid = ET.SubElement(item, "guid", {"isPermaLink": "true"})
        guid.text = art.canonical_url
        ET.SubElement(item, "pubDate").text = _iso_to_rfc2822(art.created_at)
        ET.SubElement(item, "description").text = art.subtitle

    xml_bytes = ET.tostring(rss, encoding="utf-8", xml_declaration=True)
    return xml_bytes.decode("utf-8") + "\n"


def _build_pinterest_rss_xml(articles: list[ArticleRecord], config: AppConfig) -> str:
    """Build valid Pinterest RSS 2.0 Auto-Publish feed with Media RSS 1000x1500 PNG enclosures."""
    ET.register_namespace("atom", "http://www.w3.org/2005/Atom")
    ET.register_namespace("media", "http://search.yahoo.com/mrss/")

    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = f"{config.author_name} — {SERIES_30D_NAME} (Pinterest Pins)"
    ET.SubElement(channel, "link").text = f"{config.canonical_base_url}/"
    ET.SubElement(channel, "description").text = (
        "Daily 1000x1500 (2:3) Python, SQLite WAL, and AI reliability benchmark infographics."
    )
    ET.SubElement(channel, "language").text = "en-us"
    ET.SubElement(
        channel,
        "{http://www.w3.org/2005/Atom}link",
        {
            "href": f"{config.canonical_base_url}/pinterest_feed.xml",
            "rel": "self",
            "type": "application/rss+xml",
        },
    )

    for art in reversed(articles):
        pin_file = config.site_dir / "pins" / f"{art.slug}.png"
        if not pin_file.is_file():
            generate_pin_assets(art, config)
        png_size = pin_file.stat().st_size if pin_file.is_file() else 0
        png_url = f"{config.canonical_base_url}/pins/{art.slug}.png"
        copy_fields = build_pinterest_pin_copy(art, config)

        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = copy_fields["title"][:100]
        ET.SubElement(item, "link").text = copy_fields["link"]
        guid = ET.SubElement(item, "guid", {"isPermaLink": "false"})
        guid.text = f"pinterest-pin-{art.slug}"
        ET.SubElement(item, "pubDate").text = _iso_to_rfc2822(art.created_at)
        ET.SubElement(item, "description").text = copy_fields["description"][:800]
        ET.SubElement(
            item,
            "enclosure",
            {
                "url": png_url,
                "length": str(png_size),
                "type": "image/png",
            },
        )
        ET.SubElement(
            item,
            "{http://search.yahoo.com/mrss/}content",
            {
                "url": png_url,
                "type": "image/png",
                "medium": "image",
                "width": str(PIN_WIDTH),
                "height": str(PIN_HEIGHT),
            },
        )

    xml_bytes = ET.tostring(rss, encoding="utf-8", xml_declaration=True)
    xml_text = xml_bytes.decode("utf-8")
    if 'xmlns:media="http://search.yahoo.com/mrss/"' not in xml_text:
        xml_text = xml_text.replace(
            '<rss version="2.0"',
            '<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"',
            1,
        )
    return xml_text + "\n"


def _build_sitemap_xml(articles: list[ArticleRecord], config: AppConfig) -> str:
    urlset = ET.Element("urlset", {"xmlns": "http://www.sitemaps.org/schemas/sitemap/0.9"})
    root_url = ET.SubElement(urlset, "url")
    ET.SubElement(root_url, "loc").text = f"{config.canonical_base_url}/"

    for art in articles:
        u = ET.SubElement(urlset, "url")
        ET.SubElement(u, "loc").text = art.canonical_url
        if art.updated_at:
            ET.SubElement(u, "lastmod").text = art.updated_at[:10]

    xml_bytes = ET.tostring(urlset, encoding="utf-8", xml_declaration=True)
    return xml_bytes.decode("utf-8") + "\n"


def build_static_site(
    articles: list[ArticleRecord],
    products: list[dict[str, Any]],
    config: AppConfig,
    *,
    experiment_report: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Generate `site/index.html`, `site/posts/<slug>/index.html`, `site/pins/<slug>.png`, `feed.xml`, `pinterest_feed.xml` & `sitemap.xml`."""
    site_dir = config.site_dir
    site_dir.mkdir(parents=True, exist_ok=True)
    pins_dir = site_dir / "pins"
    pins_dir.mkdir(parents=True, exist_ok=True)

    css_path = site_dir / "style.css"
    css_path.write_text(SITE_STYLESHEET, encoding="utf-8")

    if experiment_report is None:
        exp_json = config.exports_dir / "experiment_30d_report.json"
        if exp_json.is_file():
            try:
                experiment_report = json.loads(exp_json.read_text(encoding="utf-8"))
            except ValueError:
                experiment_report = None

    post_paths: list[str] = []
    pin_paths: list[str] = []
    for art in articles:
        pin_file = pins_dir / f"{art.slug}.png"
        if not pin_file.is_file():
            generate_pin_assets(art, config)
        pin_paths.append(str(pin_file))

        post_dir = site_dir / "posts" / art.slug
        post_dir.mkdir(parents=True, exist_ok=True)
        post_index = post_dir / "index.html"
        post_index.write_text(_render_article_page(art, config), encoding="utf-8")
        post_paths.append(str(post_index))

    index_path = site_dir / "index.html"
    index_path.write_text(
        _render_index_page(articles, products, config, experiment_report=experiment_report),
        encoding="utf-8",
    )

    feed_path = site_dir / "feed.xml"
    feed_path.write_text(_build_rss_xml(articles, config), encoding="utf-8")

    pin_feed_path = site_dir / "pinterest_feed.xml"
    pin_feed_path.write_text(_build_pinterest_rss_xml(articles, config), encoding="utf-8")

    sitemap_path = site_dir / "sitemap.xml"
    sitemap_path.write_text(_build_sitemap_xml(articles, config), encoding="utf-8")

    return {
        "site_dir": str(site_dir),
        "index_html": str(index_path),
        "feed_xml": str(feed_path),
        "pinterest_feed_xml": str(pin_feed_path),
        "sitemap_xml": str(sitemap_path),
        "post_pages": post_paths,
        "pin_images": pin_paths,
    }
