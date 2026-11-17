"""Core dataclasses for signals, sandbox verification checks, articles, and SKUs."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any, Optional


PILLAR_META: dict[str, dict[str, str]] = {
    "pillar_a": {
        "code": "pillar_a",
        "weekday": "Monday",
        "schedule_ist": "Mon 18:30 Asia/Colombo (13:00 UTC)",
        "label": "Fault-Tolerant AI & API Guardrails",
        "series": "Zero-Cost Production Reliability",
    },
    "pillar_b": {
        "code": "pillar_b",
        "weekday": "Wednesday",
        "schedule_ist": "Wed 18:30 Asia/Colombo (13:00 UTC)",
        "label": "Zero-Cost Backend, SQLite WAL Queues & Cron Automation",
        "series": "Zero-Cost Production Reliability",
    },
    "pillar_c": {
        "code": "pillar_c",
        "weekday": "Friday",
        "schedule_ist": "Fri 18:30 Asia/Colombo (13:00 UTC)",
        "label": "Idempotent Webhooks & Double-Entry Ledger Teardowns",
        "series": "Zero-Cost Production Reliability",
    },
}


def compute_topic_fingerprint(pillar: str, normalized_key: str) -> str:
    cleaned = " ".join(normalized_key.strip().lower().split())
    return hashlib.sha256(f"{pillar}:{cleaned}".encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class TopicSignal:
    pillar: str  # "pillar_a" | "pillar_b" | "pillar_c"
    title: str
    source: str
    source_url: str
    score: int
    tags: list[str]
    summary: str
    blueprint_key: str
    fingerprint: str = ""
    discovered_at: str = ""

    def with_defaults(self, now_iso: str) -> "TopicSignal":
        fp = self.fingerprint or compute_topic_fingerprint(self.pillar, self.title or self.blueprint_key)
        return TopicSignal(
            pillar=self.pillar,
            title=self.title,
            source=self.source,
            source_url=self.source_url,
            score=int(self.score),
            tags=list(self.tags),
            summary=self.summary,
            blueprint_key=self.blueprint_key,
            fingerprint=fp,
            discovered_at=self.discovered_at or now_iso,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CodeCheckResult:
    block_index: int
    language: str
    returncode: int
    duration_ms: float
    stdout: str
    stderr: str
    passed: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ArticleVerificationReport:
    passed: bool
    word_count: int
    code_blocks_tested: int
    storefront_link_count: int
    total_duration_ms: float
    checks: list[CodeCheckResult] = field(default_factory=list)
    lint_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "word_count": self.word_count,
            "code_blocks_tested": self.code_blocks_tested,
            "storefront_link_count": self.storefront_link_count,
            "total_duration_ms": round(self.total_duration_ms, 2),
            "checks": [c.to_dict() for c in self.checks],
            "lint_errors": list(self.lint_errors),
        }


@dataclass
class ArticleRecord:
    slug: str
    signal_fingerprint: str
    pillar: str
    scheduled_slot: str
    title: str
    subtitle: str
    tags: list[str]
    canonical_url: str
    markdown_body: str
    word_count: int = 0
    status: str = "composed"  # "composed" | "code_verified" | "verification_failed" | "staged_draft" | "published_live"
    verification: dict[str, Any] = field(default_factory=dict)
    devto_article_id: Optional[int] = None
    devto_url: Optional[str] = None
    published_mode: str = "draft"
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DigitalProductSpec:
    slug: str
    title: str
    tier: str
    price_usd: int
    suggested_price_usd: int
    zip_filename: str
    tagline: str
    summary: str
