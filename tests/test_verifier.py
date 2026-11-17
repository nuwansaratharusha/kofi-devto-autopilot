"""Tests for isolated python3 -I subprocess code execution & anti-slop quality gate."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.composer import compose_article
from engine.config import AppConfig
from engine.harvester import DETERMINISTIC_PILLAR_SEEDS
from engine.models import ArticleRecord
from engine.verifier import verify_article


class TestSubprocessVerifier(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.config = AppConfig.load(
            Path(self._tmp.name),
            environ={
                "KOFI_PROFILE_URL": "https://ko-fi.com/tharushanuwansara",
                "PRIMARY_STORE_URL": "https://ko-fi.com/tharushanuwansara/shop",
                "CANONICAL_BASE_URL": "https://tharushanuwansara.github.io/kofi-devto-autopilot",
                "DEVTO_API_KEY": "super_secret_should_not_leak",
            },
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_all_three_pillars_compose_and_pass_isolated_sandbox_verification(self) -> None:
        pillars_seen: set[str] = set()
        for seed in DETERMINISTIC_PILLAR_SEEDS:
            if seed.pillar in pillars_seen:
                continue
            pillars_seen.add(seed.pillar)
            sig = seed.with_defaults("2026-01-05T13:00:00Z")
            draft = compose_article(sig, self.config)
            verified, report = verify_article(draft, config=self.config)
            self.assertTrue(
                report.passed,
                f"Pillar {seed.pillar} failed verification: {report.lint_errors}",
            )
            self.assertEqual(verified.status, "code_verified")
            self.assertGreaterEqual(report.word_count, 750)
            self.assertEqual(report.storefront_link_count, 2)
            self.assertNotIn("Pending isolated python3 -I verification", verified.markdown_body)
            self.assertIn("exit=0", verified.markdown_body)

    def test_rejects_failing_python_snippet_and_scrubs_secrets(self) -> None:
        filler = " ".join(["reliability"] * 800)
        bad_md = f"""## Broken Code Test

{filler}

[Grab the Mini-Kit](https://ko-fi.com/tharushanuwansara/shop?sku=free)

```python
import os
# Ensure DEVTO_API_KEY from parent process is never leaked into sandbox
assert "DEVTO_API_KEY" not in os.environ, "Secret leaked!"
raise ValueError("intentional failure")
```

*Executed inside an isolated `python3 -I` sandbox.*
"""
        os.environ["DEVTO_API_KEY"] = "leak_probe_value"
        try:
            rec = ArticleRecord(
                slug="broken-test",
                signal_fingerprint="fp1",
                pillar="pillar_a",
                scheduled_slot="Mon",
                title="Broken",
                subtitle="Broken",
                tags=["python"],
                canonical_url="https://tharushanuwansara.github.io/posts/broken-test/",
                markdown_body=bad_md,
            )
            verified, report = verify_article(rec, config=self.config)
            self.assertFalse(report.passed)
            self.assertEqual(verified.status, "verification_failed")
            self.assertNotIn("Secret leaked!", report.checks[0].stderr)
            self.assertIn("intentional failure", report.checks[0].stderr)
        finally:
            os.environ.pop("DEVTO_API_KEY", None)

    def test_rejects_slop_phrases_short_word_count_and_excessive_store_links(self) -> None:
        slop_md = """## Short Slop Post

As an AI language model, let us delve into code.
[Link 1](https://ko-fi.com/tharushanuwansara/shop?a=1)
[Link 2](https://ko-fi.com/tharushanuwansara/shop?a=2)
[Link 3](https://ko-fi.com/tharushanuwansara/shop?a=3)

```python
print("ok")
```

*Verified in an isolated `python3 -I` sandbox.*
"""
        rec = ArticleRecord(
            slug="slop-test",
            signal_fingerprint="fp2",
            pillar="pillar_a",
            scheduled_slot="Mon",
            title="Slop",
            subtitle="Slop",
            tags=["python"],
            canonical_url="https://tharushanuwansara.github.io/posts/slop-test/",
            markdown_body=slop_md,
        )
        _, report = verify_article(rec, config=self.config)
        self.assertFalse(report.passed)
        joined_errors = " | ".join(report.lint_errors)
        self.assertIn("below anti-slop minimum", joined_errors)
        self.assertIn("as an ai language model", joined_errors)
        self.assertIn("exceeds anti-spam cap", joined_errors)

    def test_enforces_subprocess_timeout_on_infinite_loop(self) -> None:
        filler = " ".join(["deterministic"] * 780)
        hang_md = f"""## Timeout Test

{filler}

[Storefront Kit](https://ko-fi.com/tharushanuwansara/shop)

```python
import time
time.sleep(5)
```

*Verified in an isolated `python3 -I` sandbox.*
"""
        rec = ArticleRecord(
            slug="hang-test",
            signal_fingerprint="fp3",
            pillar="pillar_b",
            scheduled_slot="Wed",
            title="Hang",
            subtitle="Hang",
            tags=["python"],
            canonical_url="https://tharushanuwansara.github.io/posts/hang-test/",
            markdown_body=hang_md,
        )
        verified, report = verify_article(rec, config=self.config, timeout_seconds=0.3)
        self.assertFalse(report.passed)
        self.assertEqual(verified.status, "verification_failed")
        self.assertTrue(any("timed out" in e for e in report.lint_errors))


if __name__ == "__main__":
    unittest.main()
