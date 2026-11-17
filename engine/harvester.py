"""Zero-key developer trend harvester (HN Algolia, GitHub REST, PyPI RSS + Pillar Seeds)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from engine.db import utc_now_iso
from engine.models import TopicSignal

USER_AGENT = "KofiDevtoAutopilot/1.0 (+https://ko-fi.com/tharushanuwansara)"

# 9 deterministic, evergreen buyer-intent seeds (3 weeks of Mon/Wed/Fri publishing offline)
DETERMINISTIC_PILLAR_SEEDS: list[TopicSignal] = [
    # Monday — Pillar A: Fault-Tolerant AI & API Guardrails
    TopicSignal(
        pillar="pillar_a",
        blueprint_key="pillar_a_circuit_breaker_json",
        title="Zero-Dependency Python Circuit Breakers & Self-Repairing JSON Routers for Unreliable LLM APIs",
        source="deterministic_seed",
        source_url="https://docs.python.org/3/library/urllib.request.html",
        score=240,
        tags=["python", "ai", "architecture", "backend"],
        summary="Benchmarking deterministic CLOSED -> OPEN -> HALF_OPEN state machines, strict JSON repair loops, and hard token budgets without heavy agent frameworks.",
    ),
    TopicSignal(
        pillar="pillar_a",
        blueprint_key="pillar_a_retry_after_jitter",
        title="Why Naive Exponential Backoff Causes Retry Storms (And Fixing HTTP 429 With Full Jitter in Pure Python)",
        source="deterministic_seed",
        source_url="https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/",
        score=215,
        tags=["python", "api", "reliability", "webdev"],
        summary="Empirical simulation comparing synchronized retries against Retry-After-aware full jitter and sliding-window circuit breakers.",
    ),
    TopicSignal(
        pillar="pillar_a",
        blueprint_key="pillar_a_token_budget_failover",
        title="Enforcing Deterministic Token Ceilings and Multi-Provider Failover in Tool-Calling Loops",
        source="deterministic_seed",
        source_url="https://docs.python.org/3/library/dataclasses.html",
        score=195,
        tags=["python", "ai", "security", "programming"],
        summary="Preventing runaway overnight API bills with strict scalar schema validation and instant secondary-provider failover.",
    ),
    # Wednesday — Pillar B: Zero-Cost Backend, SQLite WAL Queues & Cron Automation
    TopicSignal(
        pillar="pillar_b",
        blueprint_key="pillar_b_sqlite_wal_queue",
        title="Replacing Redis Queue With a 180-Line Transactional SQLite WAL Job Broker & Dead-Letter CLI",
        source="deterministic_seed",
        source_url="https://www.sqlite.org/wal.html",
        score=260,
        tags=["python", "sqlite", "database", "backend"],
        summary="Benchmarking atomic BEGIN IMMEDIATE visibility-timeout leases, crash recovery, and dead-letter replay on a single $0 disk volume.",
    ),
    TopicSignal(
        pillar="pillar_b",
        blueprint_key="pillar_b_idempotent_cron_leases",
        title="Eliminating Overlapping Cron Double-Execution With SQLite WAL Leases and Stale-Lock Takeover",
        source="deterministic_seed",
        source_url="https://www.sqlite.org/lang_transaction.html",
        score=225,
        tags=["python", "devops", "sqlite", "automation"],
        summary="How to stop overlapping GitHub Actions and crontab triggers from double-sending emails while recovering automatically after worker OOM kills.",
    ),
    TopicSignal(
        pillar="pillar_b",
        blueprint_key="pillar_b_priority_backoff_dlq",
        title="Poison-Pill Isolation: Designing Exponential NACK Backoff and Dead-Letter Replay in SQLite",
        source="deterministic_seed",
        source_url="https://www.sqlite.org/pragma.html",
        score=200,
        tags=["python", "sqlite", "architecture", "devops"],
        summary="Isolating malformed background payloads so 1 failing webhook never stalls 10,000 healthy jobs.",
    ),
    # Friday — Pillar C: Idempotent Webhooks & Double-Entry Ledger Teardowns
    TopicSignal(
        pillar="pillar_c",
        blueprint_key="pillar_c_hmac_ledger",
        title="Constant-Time HMAC Webhook Verification & Integer-Cent Double-Entry SQL Ledgers in Python",
        source="deterministic_seed",
        source_url="https://docs.python.org/3/library/hmac.html",
        score=250,
        tags=["python", "security", "sql", "backend"],
        summary="Defending payment webhooks against timing side-channels, 5-minute replay windows, and floating-point rounding drift.",
    ),
    TopicSignal(
        pillar="pillar_c",
        blueprint_key="pillar_c_replay_nonce_dedup",
        title="Why Timestamp Windows Alone Fail Webhook Security (Atomic SQLite Nonce Deduplication Under Load)",
        source="deterministic_seed",
        source_url="https://docs.python.org/3/library/sqlite3.html",
        score=220,
        tags=["security", "python", "api", "database"],
        summary="Blocking captured-payload replay attacks inside the 300-second tolerance window using atomic WAL nonce tables.",
    ),
    TopicSignal(
        pillar="pillar_c",
        blueprint_key="pillar_c_reconciliation_audit",
        title="Zero-Drift Payment Reconciliation: Auditing Payment Webhooks Against Immutable SQL Postings",
        source="deterministic_seed",
        source_url="https://www.sqlite.org/stricttables.html",
        score=190,
        tags=["sql", "python", "fintech", "architecture"],
        summary="Detecting missing webhooks and unbalanced journal entries with pure SQLite views and deterministic invariants.",
    ),
]


def _default_http_get(url: str, timeout: float = 4.0) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json, application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.1",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(262_144)  # Cap read at 256 KiB for memory & disk hygiene


def _fetch_with_backoff(
    url: str,
    http_get: Callable[[str, float], bytes],
    *,
    retries: int = 2,
    timeout: float = 4.0,
    sleeper: Callable[[float], None] = time.sleep,
) -> Optional[bytes]:
    for attempt in range(1, retries + 1):
        try:
            return http_get(url, timeout)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            if attempt < retries:
                sleeper(0.2 * attempt)
    return None


def _classify_pillar(text: str) -> tuple[str, str, list[str]]:
    lowered = text.lower()
    if any(k in lowered for k in ("webhook", "hmac", "ledger", "payment", "stripe", "idempoten", "reconcil", "nonce")):
        if any(k in lowered for k in ("reconcil", "audit", "immutab", "accounting")):
            return ("pillar_c", "pillar_c_reconciliation_audit", ["sql", "python", "fintech", "architecture"])
        if any(k in lowered for k in ("replay", "nonce", "window")):
            return ("pillar_c", "pillar_c_replay_nonce_dedup", ["security", "python", "api", "database"])
        return ("pillar_c", "pillar_c_hmac_ledger", ["python", "security", "sql", "backend"])
    if any(k in lowered for k in ("sqlite", "wal", "cron", "queue", "worker", "scheduler", "dlq", "dead-letter")):
        if any(k in lowered for k in ("cron", "scheduler", "lock", "overlap")):
            return ("pillar_b", "pillar_b_idempotent_cron_leases", ["python", "devops", "sqlite", "automation"])
        if any(k in lowered for k in ("dlq", "dead-letter", "poison", "nack")):
            return ("pillar_b", "pillar_b_priority_backoff_dlq", ["python", "sqlite", "architecture", "devops"])
        return ("pillar_b", "pillar_b_sqlite_wal_queue", ["python", "sqlite", "database", "backend"])
    if any(k in lowered for k in ("jitter", "429", "rate limit", "retry-after", "backoff")):
        return ("pillar_a", "pillar_a_retry_after_jitter", ["python", "api", "reliability", "webdev"])
    if any(k in lowered for k in ("token", "budget", "cost", "failover", "router")):
        return ("pillar_a", "pillar_a_token_budget_failover", ["python", "ai", "security", "programming"])
    return ("pillar_a", "pillar_a_circuit_breaker_json", ["python", "ai", "architecture", "backend"])


def fetch_hn_signals(
    http_get: Callable[[str, float], bytes] = _default_http_get,
    sleeper: Callable[[float], None] = time.sleep,
) -> list[TopicSignal]:
    query = urllib.parse.quote("sqlite wal OR idempotent webhook OR llm circuit breaker")
    url = f"https://hn.algolia.com/api/v1/search?tags=story&numericFilters=points%3E30&hitsPerPage=5&query={query}"
    raw = _fetch_with_backoff(url, http_get, sleeper=sleeper)
    if not raw:
        return []
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return []

    results: list[TopicSignal] = []
    now_iso = utc_now_iso()
    for hit in payload.get("hits", [])[:4]:
        raw_title = str(hit.get("title") or "").strip()
        if len(raw_title) < 15:
            continue
        pillar, bp_key, tags = _classify_pillar(raw_title)
        obj_id = str(hit.get("objectID") or "0")
        ext_url = str(hit.get("url") or f"https://news.ycombinator.com/item?id={obj_id}")
        if not ext_url.startswith("https://"):
            ext_url = f"https://news.ycombinator.com/item?id={obj_id}"
        points = int(hit.get("points") or 50)
        results.append(
            TopicSignal(
                pillar=pillar,
                blueprint_key=bp_key,
                title=f"Production Teardown: {raw_title[:88]}",
                source="hn_algolia",
                source_url=ext_url,
                score=min(235, 100 + points // 4),
                tags=tags,
                summary=f"Empirical reliability benchmark inspired by live community discussion ({raw_title}).",
                discovered_at=now_iso,
            ).with_defaults(now_iso)
        )
    return results


def fetch_github_trending_signals(
    http_get: Callable[[str, float], bytes] = _default_http_get,
    sleeper: Callable[[float], None] = time.sleep,
) -> list[TopicSignal]:
    since_date = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    q = urllib.parse.quote(f"language:python sqlite queue OR circuit breaker created:>{since_date}")
    url = f"https://api.github.com/search/repositories?q={q}&sort=stars&order=desc&per_page=3"
    raw = _fetch_with_backoff(url, http_get, sleeper=sleeper)
    if not raw:
        return []
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return []

    results: list[TopicSignal] = []
    now_iso = utc_now_iso()
    for item in payload.get("items", [])[:3]:
        full_name = str(item.get("full_name") or "").strip()
        desc = str(item.get("description") or "Zero-dependency Python reliability pattern").strip()
        html_url = str(item.get("html_url") or "https://github.com")
        if not full_name or not html_url.startswith("https://"):
            continue
        pillar, bp_key, tags = _classify_pillar(f"{full_name} {desc}")
        stars = int(item.get("stargazers_count") or 20)
        results.append(
            TopicSignal(
                pillar=pillar,
                blueprint_key=bp_key,
                title=f"Zero-Dep Architecture Notes ({full_name}): {desc[:68]}",
                source="github_rest",
                source_url=html_url,
                score=min(230, 110 + stars // 5),
                tags=tags,
                summary=f"Benchmarked Python stdlib implementation inspired by {full_name}: {desc[:140]}",
                discovered_at=now_iso,
            ).with_defaults(now_iso)
        )
    return results


def fetch_pypi_rss_signals(
    http_get: Callable[[str, float], bytes] = _default_http_get,
    sleeper: Callable[[float], None] = time.sleep,
) -> list[TopicSignal]:
    url = "https://pypi.org/rss/updates.xml"
    raw = _fetch_with_backoff(url, http_get, sleeper=sleeper)
    if not raw:
        return []
    try:
        root = ET.fromstring(raw.decode("utf-8", errors="replace"))
    except ET.ParseError:
        return []

    results: list[TopicSignal] = []
    now_iso = utc_now_iso()
    for item in root.findall("./channel/item")[:8]:
        title = (item.findtext("title") or "").strip()
        desc = (item.findtext("description") or "").strip()
        link = (item.findtext("link") or "").strip()
        combined = f"{title} {desc}"
        if not any(k in combined.lower() for k in ("sqlite", "retry", "webhook", "queue", "async", "agent")):
            continue
        if not link.startswith("https://"):
            continue
        pillar, bp_key, tags = _classify_pillar(combined)
        results.append(
            TopicSignal(
                pillar=pillar,
                blueprint_key=bp_key,
                title=f"Stdlib First vs Package Bloat: Benchmarking {title[:60]} Patterns",
                source="pypi_rss",
                source_url=link,
                score=175,
                tags=tags,
                summary=f"Evaluating pure-stdlib reliability alternatives alongside ecosystem update {title}.",
                discovered_at=now_iso,
            ).with_defaults(now_iso)
        )
        if len(results) >= 2:
            break
    return results


def harvest_signals(
    *,
    offline: bool = False,
    http_get: Callable[[str, float], bytes] = _default_http_get,
    sleeper: Callable[[float], None] = time.sleep,
) -> list[TopicSignal]:
    """Return deterministic pillar seeds enriched with zero-key live trends when online."""
    now_iso = utc_now_iso()
    collected: list[TopicSignal] = [seed.with_defaults(now_iso) for seed in DETERMINISTIC_PILLAR_SEEDS]

    if not offline:
        collected.extend(fetch_hn_signals(http_get=http_get, sleeper=sleeper))
        collected.extend(fetch_github_trending_signals(http_get=http_get, sleeper=sleeper))
        collected.extend(fetch_pypi_rss_signals(http_get=http_get, sleeper=sleeper))

    # Deduplicate by fingerprint preserving highest-priority entry first
    deduped: dict[str, TopicSignal] = {}
    for sig in collected:
        if sig.fingerprint not in deduped:
            deduped[sig.fingerprint] = sig
    return list(deduped.values())
