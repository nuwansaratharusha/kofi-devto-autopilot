"""Deterministic digital product verifier, .zip packager, and storefront copy generator."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

DETERMINISTIC_ZIP_TIMESTAMP = (2026, 1, 1, 0, 0, 0)
EXCLUDED_NAMES = frozenset({"__pycache__", ".DS_Store", "Thumbs.db"})
EXCLUDED_SUFFIXES = (".pyc", ".pyo", ".db", ".db-wal", ".db-shm")


@dataclass(frozen=True)
class BuiltProductArtifact:
    slug: str
    title: str
    tier: str
    price_usd: int
    suggested_price_usd: int
    zip_path: str
    zip_filename: str
    size_bytes: int
    sha256: str
    file_count: int
    python_tests_passed: bool
    node_tests_passed: Optional[bool]


class ProductBuildError(RuntimeError):
    """Raised when toolkit validation, unit testing, or deterministic packaging fails."""


def load_catalog(project_root: Path) -> dict[str, Any]:
    catalog_path = project_root / "products" / "catalog.json"
    if not catalog_path.is_file():
        raise ProductBuildError(f"Missing product catalog: {catalog_path}")
    return json.loads(catalog_path.read_text(encoding="utf-8"))


def _clean_subprocess_env() -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    return env


def _run_toolkit_tests(toolkit_dir: Path, spec: dict[str, Any]) -> tuple[bool, Optional[bool]]:
    py_mod = spec.get("python_test_module")
    if not py_mod:
        raise ProductBuildError(f"Product {spec['slug']} missing python_test_module")

    test_script = toolkit_dir / f"{py_mod}.py"
    if not test_script.is_file():
        raise ProductBuildError(f"Missing test script {test_script} for {spec['slug']}")

    py_proc = subprocess.run(
        [sys.executable, "-E", "-s", "-m", "unittest", "discover", "-s", str(toolkit_dir), "-p", f"{py_mod}.py", "-v"],
        cwd=str(toolkit_dir),
        env=_clean_subprocess_env(),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if py_proc.returncode != 0:
        raise ProductBuildError(
            f"Python unit tests failed for {spec['slug']}:\nSTDOUT:\n{py_proc.stdout}\nSTDERR:\n{py_proc.stderr}"
        )

    node_test_file = spec.get("node_test_file")
    node_passed: Optional[bool] = None
    if node_test_file:
        node_bin = shutil.which("node")
        if node_bin:
            node_proc = subprocess.run(
                [node_bin, "--test", str(node_test_file)],
                cwd=str(toolkit_dir),
                env=_clean_subprocess_env(),
                capture_output=True,
                text=True,
                timeout=30,
            )
            if node_proc.returncode != 0:
                raise ProductBuildError(
                    f"Node unit tests failed for {spec['slug']}:\nSTDOUT:\n{node_proc.stdout}\nSTDERR:\n{node_proc.stderr}"
                )
            node_passed = True
        else:
            node_passed = None

    return True, node_passed


def _collect_deterministic_files(toolkit_dir: Path, required_files: list[str]) -> list[Path]:
    for req in required_files:
        candidate = toolkit_dir / req
        if not candidate.is_file():
            raise ProductBuildError(f"Required toolkit file missing: {candidate}")

    collected: list[Path] = []
    for root, dirs, files in os.walk(toolkit_dir):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_NAMES and not d.startswith("."))
        for fname in sorted(files):
            if fname in EXCLUDED_NAMES or fname.startswith(".") or fname.endswith(EXCLUDED_SUFFIXES):
                continue
            collected.append(Path(root) / fname)

    collected.sort(key=lambda p: p.relative_to(toolkit_dir).as_posix())
    return collected


def _write_deterministic_zip(slug: str, toolkit_dir: Path, files: list[Path], out_zip: Path) -> tuple[int, str]:
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    tmp_zip = out_zip.with_suffix(".zip.tmp")
    with zipfile.ZipFile(tmp_zip, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for file_path in files:
            rel_posix = file_path.relative_to(toolkit_dir).as_posix()
            arcname = f"{slug}/{rel_posix}"
            info = zipfile.ZipInfo(filename=arcname, date_time=DETERMINISTIC_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            # Preserve executable bit for scripts while normalizing deterministic permissions
            mode = 0o755 if file_path.suffix == ".py" else 0o644
            info.external_attr = (mode & 0xFFFF) << 16
            info.create_system = 3  # Unix
            data = file_path.read_bytes()
            zf.writestr(info, data)

    tmp_zip.replace(out_zip)
    raw = out_zip.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    return len(raw), digest


def _render_storefront_copy(
    catalog: dict[str, Any],
    artifacts: list[BuiltProductArtifact],
    *,
    kofi_profile_url: str,
    primary_store_url: str,
    mirror_store_url: str,
) -> str:
    free_art = next((a for a in artifacts if a.tier == "free_pwyw"), artifacts[0])
    paid_art = next((a for a in artifacts if a.tier == "flagship_paid"), artifacts[-1])
    free_spec = next(p for p in catalog["products"] if p["slug"] == free_art.slug)
    paid_spec = next(p for p in catalog["products"] if p["slug"] == paid_art.slug)

    mirror_note = (
        f"Configured Mirror Checkout (`MIRROR_STORE_URL`): `{mirror_store_url}`"
        if mirror_store_url
        else "Not set yet (leave `MIRROR_STORE_URL=` empty if using Ko-fi PayPal/Stripe, or set to your Polar.sh / Lemon Squeezy URL in `.env`)."
    )

    free_bullets = "\n".join(f"- {b}" for b in free_spec["highlights"])
    paid_bullets = "\n".join(f"- {b}" for b in paid_spec["highlights"])

    return f"""# Ready-to-Paste Ko-fi Profile, Shop & Sri Lanka Payout Guide

**Target Profile:** [{kofi_profile_url}]({kofi_profile_url})  
**Primary Shop URL (`PRIMARY_STORE_URL`):** `{primary_store_url}`  
**Mirror Store Status:** {mirror_note}

---

## 1. Ko-fi Profile Setup (Copy & Paste)

- **Display Name:** `Tharusha Nuwansara`
- **Headline / Tagline:** `Systems & Reliability Engineer • Zero-Dependency Python, SQLite WAL & Fault-Tolerant AI Toolkits`
- **About / Bio (<= 500 chars):**
  > I build zero-bloat, unit-tested reliability primitives for Python & TypeScript engineers—SQLite WAL queues, idempotent cron runners, HMAC webhook guards, and fault-tolerant AI agent routers. Every snippet I publish is benchmarked in an isolated sandbox before going live. Grab the free Idempotent Cron Mini-Kit in my Shop or pick up the full Production Starter Kit!
- **Ko-fi Goal Widget (`$50` Monthly Open-Source Reliability Goal):**
  - **Goal Title:** `First $50 — Open-Source Reliability Benchmarks & Zero-Dep Kits`
  - **Target Amount:** `$50 USD`
  - **Description:** `Funds 3x/week empirically verified backend & AI reliability teardowns + new MIT-licensed starter kits. 4 copies of the $14 Flagship Kit = 100% funded!`

---

## 2. Pinned Welcome Post on Ko-fi

**Post Title:** `Welcome! Grab the Free Python Idempotent Cron & Circuit-Breaker Mini-Kit (+ Full Reliability Kit)`

**Post Body:**
```markdown
Hey! I'm Tharusha. Every Monday, Wednesday, and Friday I publish code-verified systems teardowns covering:
1. Fault-tolerant AI & external API circuit breakers
2. Zero-cost SQLite WAL background queues & idempotent cron runners
3. Constant-time HMAC webhooks & double-entry SQL ledgers

Every tutorial code block is executed inside an isolated `python3 -I` sandbox before publication—zero untested AI filler.

Start here:
- Free ($0+ Pay-What-You-Want): **{free_art.title}** (`{free_art.zip_filename}`)
- Full Production Kit (${paid_art.price_usd} USD, MIT Commercial License): **{paid_art.title}** (`{paid_art.zip_filename}`)

Both kits are 100% stdlib-first, unit-tested, and ready in the Shop tab!
```

---

## 3. Ko-fi Shop Listing #1 — Free Lead Magnet ($0+ Pay-What-You-Want)

- **Upload File:** `products/dist/{free_art.zip_filename}` (`{free_art.size_bytes:,}` bytes • SHA-256 `{free_art.sha256[:16]}...`)
- **Item Name:** `{free_art.title}`
- **Price:** `$0` (Enable *"Pay what you want"* • Suggested amount: `$3`)
- **Summary / Description:**
```markdown
{free_spec["tagline"]}

{free_spec["summary"]}

### What You Get Inside (`{free_art.zip_filename}`)
{free_bullets}

### Verify in 5 Seconds
Unzip and run:
`python3 -m unittest discover -v`
`python3 idempotent_cron.py --demo`
```

---

## 4. Ko-fi Shop Listing #2 — Flagship Revenue Driver ($14 USD • 4 Sales = ~$52 Net)

- **Upload File:** `products/dist/{paid_art.zip_filename}` (`{paid_art.size_bytes:,}` bytes • SHA-256 `{paid_art.sha256[:16]}...`)
- **Item Name:** `{paid_art.title}`
- **Price:** `$14 USD` (Fixed or $14+ Pay-What-You-Want)
- **Summary / Description:**
```markdown
{paid_spec["tagline"]}

{paid_spec["summary"]}

### Included Production Modules & Blueprints (`{paid_art.zip_filename}`)
{paid_bullets}

### Instant Local Verification
Unzip and run:
`python3 -m unittest test_pro_kit -v`
`node --test test_guardrails.mjs`
`python3 wal_queue.py --demo`
```

---

## 5. Payout Gateway Note (`Asia/Colombo` / Sri Lanka Creators)

1. **Ko-fi Direct Pass-Through (`$0` minimum payout, `0%` platform fee on tips):**
   - Connect a **receive-enabled PayPal** or **Stripe Standard** account under `Ko-fi Settings -> Payment`. Funds settle directly to your account immediately per sale.
2. **No Receive-Enabled PayPal/Stripe in Sri Lanka? Use `MIRROR_STORE_URL` (Zero Code Edits):**
   - Upload the exact same two `.zip` files from `products/dist/` onto either:
     - **Polar.sh (`https://polar.sh`)** — Developer-first Merchant of Record supporting Sri Lanka payouts via Stripe Connect Express with a low **$10 USD minimum payout**.
     - **Lemon Squeezy (`https://lemonsqueezy.com`)** — Merchant of Record supporting direct **Sri Lanka local bank wire/ACH payouts** ($50 USD minimum payout).
   - Set `MIRROR_STORE_URL=https://polar.sh/tharushanuwansara` in `.env` and re-run `python3 autopilot.py run-pipeline --limit 3`. All Paid Starter Kit checkout links automatically route to your mirror store while keeping your Ko-fi profile active!
"""


def build_all_products(
    project_root: Path,
    *,
    kofi_profile_url: str = "https://ko-fi.com/tharushanuwansara",
    primary_store_url: str = "https://ko-fi.com/tharushanuwansara/shop",
    mirror_store_url: str = "",
) -> dict[str, Any]:
    """Validate, unit-test, and deterministically package all digital products."""
    catalog = load_catalog(project_root)
    dist_dir = project_root / "products" / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)

    built_artifacts: list[BuiltProductArtifact] = []
    for spec in catalog.get("products", []):
        slug = str(spec["slug"])
        toolkit_dir = project_root / str(spec["source_dir"])
        if not toolkit_dir.is_dir():
            raise ProductBuildError(f"Toolkit source directory does not exist: {toolkit_dir}")

        files = _collect_deterministic_files(toolkit_dir, list(spec.get("required_files", [])))
        py_ok, node_ok = _run_toolkit_tests(toolkit_dir, spec)
        zip_filename = str(spec["zip_filename"])
        out_zip = dist_dir / zip_filename
        size_bytes, sha256_hex = _write_deterministic_zip(slug, toolkit_dir, files, out_zip)

        built_artifacts.append(
            BuiltProductArtifact(
                slug=slug,
                title=str(spec["title"]),
                tier=str(spec["tier"]),
                price_usd=int(spec["price_usd"]),
                suggested_price_usd=int(spec["suggested_price_usd"]),
                zip_path=str(out_zip),
                zip_filename=zip_filename,
                size_bytes=size_bytes,
                sha256=sha256_hex,
                file_count=len(files),
                python_tests_passed=py_ok,
                node_tests_passed=node_ok,
            )
        )

    manifest_path = dist_dir / "manifest.json"
    manifest_payload = {
        "version": catalog.get("version", "1.0.0"),
        "author": catalog.get("author", "Tharusha Nuwansara"),
        "products": [asdict(a) for a in built_artifacts],
    }
    manifest_path.write_text(json.dumps(manifest_payload, indent=2) + "\n", encoding="utf-8")

    storefront_copy_path = project_root / "exports" / "storefront" / "kofi_profile_and_shop_copy.md"
    storefront_copy_path.parent.mkdir(parents=True, exist_ok=True)
    storefront_md = _render_storefront_copy(
        catalog,
        built_artifacts,
        kofi_profile_url=kofi_profile_url,
        primary_store_url=primary_store_url,
        mirror_store_url=mirror_store_url,
    )
    storefront_copy_path.write_text(storefront_md, encoding="utf-8")

    return {
        "manifest_path": str(manifest_path),
        "storefront_copy_path": str(storefront_copy_path),
        "products": [asdict(a) for a in built_artifacts],
    }
