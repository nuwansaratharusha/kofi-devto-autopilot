"""Isolated subprocess code-execution sandbox & anti-slop quality gate."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from engine.config import AppConfig
from engine.models import ArticleRecord, ArticleVerificationReport, CodeCheckResult

MIN_WORD_COUNT = 750
MAX_STOREFRONT_LINKS = 2
SUBPROCESS_TIMEOUT_SECONDS = 10.0

BANNED_SLOP_PHRASES = (
    "as an ai language model",
    "in today's fast-paced",
    "delve into",
    "lorem ipsum",
    "todo:",
    "insert link here",
    "your_url_here",
    "https://example.com",
    "http://example.com",
)

UNSAFE_SHELL_PATTERNS = (
    "rm -rf /",
    "sudo ",
    "curl ",
    "wget ",
    "mkfs",
    "shutdown",
)

CODE_FENCE_RE = re.compile(r"```(python|bash|sh)\n(.*?)```", re.DOTALL)
MD_LINK_RE = re.compile(r"(?<!!)\[([^\]]*)\]\(([^)]*)\)")
EMPIRICAL_BLOCK_RE = re.compile(
    r"<!-- EMPIRICAL_OUTPUT_START -->.*?<!-- EMPIRICAL_OUTPUT_END -->",
    re.DOTALL,
)

STOREFRONT_HOSTS = frozenset(
    {
        "nas.io",
        "www.nas.io",
        "patreon.com",
        "www.patreon.com",
        "ko-fi.com",
        "www.ko-fi.com",
        "polar.sh",
        "www.polar.sh",
        "lemonsqueezy.com",
        "www.lemonsqueezy.com",
        "gumroad.com",
        "www.gumroad.com",
    }
)

STOREFRONT_DOMAIN_SUFFIXES = (
    ".lemonsqueezy.com",
    ".gumroad.com",
    ".nas.io",
)


def _is_recognized_storefront_host(hostname: str, extra_hosts: set[str]) -> bool:
    low = hostname.strip().lower()
    if not low:
        return False
    if low in extra_hosts:
        return True
    return any(low.endswith(sfx) for sfx in STOREFRONT_DOMAIN_SUFFIXES)


def count_article_words(markdown_text: str) -> int:
    """Count prose and technical tokens conservatively."""
    without_comments = re.sub(r"<!--.*?-->", " ", markdown_text, flags=re.DOTALL)
    tokens = re.findall(r"[A-Za-z0-9_'-]+", without_comments)
    return len(tokens)


def count_prose_words(markdown_text: str) -> int:
    """Count non-code prose words outside fenced code blocks."""
    without_fences = re.sub(r"```.*?```", " ", markdown_text, flags=re.DOTALL)
    without_comments = re.sub(r"<!--.*?-->", " ", without_fences, flags=re.DOTALL)
    tokens = re.findall(r"[A-Za-z0-9_'-]+", without_comments)
    return len(tokens)


def _scrubbed_sandbox_env(temp_dir: Path) -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin"),
        "HOME": str(temp_dir),
        "TMPDIR": str(temp_dir),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def _extract_executable_blocks(markdown_text: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    for match in CODE_FENCE_RE.finditer(markdown_text):
        lang = match.group(1).strip().lower()
        code = match.group(2)
        blocks.append((lang, code))
    return blocks


def _inject_empirical_stdout(markdown_text: str, checks: list[CodeCheckResult]) -> str:
    if not EMPIRICAL_BLOCK_RE.search(markdown_text):
        return markdown_text

    sections: list[str] = []
    for chk in checks:
        clean_out = chk.stdout.strip() or "(completed with exit code 0)"
        sections.append(
            f"# Block {chk.block_index} ({chk.language}, exit={chk.returncode}, {chk.duration_ms:.1f} ms)\n{clean_out}"
        )
    combined = "\n\n".join(sections)
    replacement = (
        "<!-- EMPIRICAL_OUTPUT_START -->\n"
        "```text\n"
        f"{combined}\n"
        "```\n"
        "<!-- EMPIRICAL_OUTPUT_END -->"
    )
    return EMPIRICAL_BLOCK_RE.sub(lambda _m: replacement, markdown_text, count=1)


def _lint_links_and_slop(
    markdown_text: str,
    config: Optional[AppConfig] = None,
) -> tuple[int, list[str]]:
    errors: list[str] = []
    lowered = markdown_text.lower()
    for phrase in BANNED_SLOP_PHRASES:
        if phrase in lowered:
            errors.append(f"Banned slop/placeholder phrase detected: '{phrase}'")

    extra_store_hosts: set[str] = set(STOREFRONT_HOSTS)
    if config is not None:
        for url_candidate in (
            config.primary_store_url,
            config.mirror_store_url,
            config.kofi_profile_url,
            config.lemonsqueezy_store_url,
            config.gumroad_store_url,
            config.patreon_url,
            config.nasio_store_url,
        ):
            if url_candidate:
                parsed_host = urlparse(url_candidate).netloc.lower()
                if parsed_host:
                    extra_store_hosts.add(parsed_host)

    prose_without_fences = re.sub(r"```.*?```", " ", markdown_text, flags=re.DOTALL)
    prose_without_code = re.sub(r"`[^`\n]+`", " ", prose_without_fences)

    storefront_links = 0
    for match in MD_LINK_RE.finditer(prose_without_code):
        label = match.group(1).strip()
        target = match.group(2).strip()
        if not label:
            errors.append(f"Empty link label for target '{target}'")
        if label.lower() in {"click here", "here", "link"}:
            errors.append(f"Non-descriptive link text '{label}' violates accessibility hygiene")
        if not target:
            errors.append(f"Empty link URL for label '{label}'")
            continue
        parsed = urlparse(target)
        if parsed.scheme not in {"https"}:
            errors.append(f"Link '{target}' must use https:// scheme")
            continue
        if not parsed.netloc:
            errors.append(f"Link '{target}' missing hostname")
            continue
        if _is_recognized_storefront_host(parsed.netloc, extra_store_hosts):
            storefront_links += 1

    if storefront_links > MAX_STOREFRONT_LINKS:
        errors.append(
            f"Storefront link count ({storefront_links}) exceeds anti-spam cap ({MAX_STOREFRONT_LINKS})"
        )
    if storefront_links == 0:
        errors.append("Article must include at least 1 contextual storefront link")

    if "isolated `python3 -i` sandbox" not in lowered and "isolated python sandbox" not in lowered:
        errors.append("Missing transparent empirical sandbox disclosure footer")

    return storefront_links, errors


def verify_article(
    article: ArticleRecord,
    *,
    config: Optional[AppConfig] = None,
    timeout_seconds: float = SUBPROCESS_TIMEOUT_SECONDS,
) -> tuple[ArticleRecord, ArticleVerificationReport]:
    """Execute all Python & shell snippets in an isolated sandbox and lint the article."""
    blocks = _extract_executable_blocks(article.markdown_body)
    lint_errors: list[str] = []
    checks: list[CodeCheckResult] = []

    python_blocks = [b for b in blocks if b[0] == "python"]
    if not python_blocks:
        lint_errors.append("Article must contain at least one runnable ```python code block")

    total_duration_ms = 0.0
    with tempfile.TemporaryDirectory(prefix="autopilot_verify_") as tmp_str:
        tmp_dir = Path(tmp_str)
        env = _scrubbed_sandbox_env(tmp_dir)

        for idx, (lang, code) in enumerate(blocks, start=1):
            t0 = time.perf_counter()
            if lang == "python":
                script_file = tmp_dir / f"snippet_{idx:02d}.py"
                script_file.write_text(code, encoding="utf-8")
                cmd = [sys.executable, "-I", str(script_file)]
            else:
                lowered_code = code.lower()
                unsafe_hit = next((pat for pat in UNSAFE_SHELL_PATTERNS if pat in lowered_code), None)
                if unsafe_hit is not None:
                    checks.append(
                        CodeCheckResult(
                            block_index=idx,
                            language=lang,
                            returncode=126,
                            duration_ms=0.0,
                            stdout="",
                            stderr=f"Blocked unsafe shell pattern: {unsafe_hit}",
                            passed=False,
                        )
                    )
                    lint_errors.append(f"Block {idx} ({lang}) contained unsafe shell command: {unsafe_hit}")
                    continue
                script_file = tmp_dir / f"snippet_{idx:02d}.sh"
                script_file.write_text(code, encoding="utf-8")
                cmd = ["/bin/sh", "-e", str(script_file)]

            try:
                proc = subprocess.run(
                    cmd,
                    cwd=str(tmp_dir),
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                )
                dur_ms = round((time.perf_counter() - t0) * 1000.0, 2)
                total_duration_ms += dur_ms
                ok = proc.returncode == 0
                if not ok:
                    lint_errors.append(
                        f"Block {idx} ({lang}) exited with code {proc.returncode}: {proc.stderr.strip()[:240]}"
                    )
                checks.append(
                    CodeCheckResult(
                        block_index=idx,
                        language=lang,
                        returncode=int(proc.returncode),
                        duration_ms=dur_ms,
                        stdout=proc.stdout[:4000],
                        stderr=proc.stderr[:2000],
                        passed=ok,
                    )
                )
            except subprocess.TimeoutExpired as exc:
                dur_ms = round((time.perf_counter() - t0) * 1000.0, 2)
                total_duration_ms += dur_ms
                lint_errors.append(f"Block {idx} ({lang}) timed out after {timeout_seconds}s")
                checks.append(
                    CodeCheckResult(
                        block_index=idx,
                        language=lang,
                        returncode=124,
                        duration_ms=dur_ms,
                        stdout=(exc.stdout or "")[:1000] if isinstance(exc.stdout, str) else "",
                        stderr=f"TimeoutExpired after {timeout_seconds}s",
                        passed=False,
                    )
                )

    updated_markdown = article.markdown_body
    if checks and all(c.passed for c in checks):
        updated_markdown = _inject_empirical_stdout(updated_markdown, checks)

    prose_words = count_prose_words(updated_markdown)
    total_words = count_article_words(updated_markdown)
    if prose_words < MIN_WORD_COUNT:
        lint_errors.append(
            f"Prose word count ({prose_words}) is below anti-slop minimum ({MIN_WORD_COUNT})"
        )

    storefront_count, hygiene_errors = _lint_links_and_slop(updated_markdown, config=config)
    lint_errors.extend(hygiene_errors)

    passed = len(lint_errors) == 0 and len(checks) > 0 and all(c.passed for c in checks)
    report = ArticleVerificationReport(
        passed=passed,
        word_count=prose_words,
        code_blocks_tested=len(checks),
        storefront_link_count=storefront_count,
        total_duration_ms=round(total_duration_ms, 2),
        checks=checks,
        lint_errors=lint_errors,
    )

    article.markdown_body = updated_markdown
    article.word_count = prose_words
    article.verification = {
        **report.to_dict(),
        "total_tokens_including_code": total_words,
    }
    article.status = "code_verified" if passed else "verification_failed"
    return article, report
