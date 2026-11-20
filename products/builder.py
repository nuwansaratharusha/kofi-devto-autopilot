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
        timeout=60,
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


def _render_dual_storefront_setup(
    catalog: dict[str, Any],
    artifacts: list[BuiltProductArtifact],
    *,
    lemonsqueezy_store_url: str = "",
    gumroad_store_url: str = "",
) -> str:
    free_art = next((a for a in artifacts if a.tier == "free_pwyw"), artifacts[0])
    paid_art = next((a for a in artifacts if a.tier == "flagship_paid"), artifacts[-1])
    free_spec = next(p for p in catalog["products"] if p["slug"] == free_art.slug)
    paid_spec = next(p for p in catalog["products"] if p["slug"] == paid_art.slug)

    free_bullets = "\n".join(f"- {b}" for b in free_spec["highlights"])
    paid_bullets = "\n".join(f"- {b}" for b in paid_spec["highlights"])

    ls_display = lemonsqueezy_store_url or "https://<your-handle>.lemonsqueezy.com"
    gr_display = gumroad_store_url or "https://<your-handle>.gumroad.com"

    return f"""# Dual Storefront Setup Guide: Lemon Squeezy + Gumroad (Sri Lanka No-PayPal/No-Stripe Flow)

**Why Both Storefronts Run in Parallel (`Asia/Colombo` Creator Strategy):**
- **Gumroad (`{gr_display}`)** — Instant 60-second go-live without waiting on store review; hosts the **$0+ Pay-What-You-Want Mini-Kit** (`free_checkout_url`) and mirrors the **$14 Flagship Kit**. Holds USD balance safely until **$100 USD** minimum Sri Lanka local bank deposit.
- **Lemon Squeezy (`{ls_display}`)** — Merchant of Record handling global VAT/GST with **$50 USD** minimum Sri Lanka local bank wire payout (`4 × $14 = $56 gross`); hosts the **$14 Flagship Starter Kit** (`paid_checkout_url`) and mirrors the **$0+ Mini-Kit**.

---

## 1. Gumroad Listing Copy & Upload Steps (`https://app.gumroad.com/products`)

> **Tip:** Once `GUMROAD_ACCESS_TOKEN` is set in `.env`, running `python3 autopilot.py sync-stores --create-gumroad-drafts` automatically creates both draft products via `POST /v2/products` (`published=false`) so you only have to attach the `.zip` files and click Publish!

### Gumroad Product #1 — Free Lead Magnet (`$0+` Pay-What-You-Want)
- **Product Type:** Digital Product
- **Name:** `{free_art.title}`
- **Custom Permalink:** `free-idempotent-cron-mini-kit`
- **Price:** `$0` (Toggle **"Allow customers to pay what they want"** -> Minimum `$0`, Suggested `$3`)
- **Content File to Upload:** `products/dist/{free_art.zip_filename}` (`{free_art.size_bytes:,}` bytes • SHA-256 `{free_art.sha256[:16]}`)
- **Tags:** `python, sqlite, cron, reliability, backend`
- **Summary:** `{free_spec["tagline"]}`
- **Description (Copy & Paste):**
~~~~markdown
{free_spec["summary"]}

### Included Zero-Dependency Modules (`{free_art.zip_filename}`)
{free_bullets}

### Verify Locally in 5 Seconds
```bash
python3 -m unittest discover -v
python3 idempotent_cron.py --demo
```
~~~~

### Gumroad Product #2 — Flagship Production Kit (`$14 USD`)
- **Product Type:** Digital Product
- **Name:** `{paid_art.title}`
- **Custom Permalink:** `production-agent-automation-kit`
- **Price:** `$14 USD` (Allow PWYW minimum `$14`, suggested `$19`)
- **Content File to Upload:** `products/dist/{paid_art.zip_filename}` (`{paid_art.size_bytes:,}` bytes • SHA-256 `{paid_art.sha256[:16]}`)
- **Tags:** `python, typescript, sqlite, ai-agents, webhooks`
- **Summary:** `{paid_spec["tagline"]}`
- **Description (Copy & Paste):**
~~~~markdown
{paid_spec["summary"]}

### Included Production Modules & CI Blueprints (`{paid_art.zip_filename}`)
{paid_bullets}

### Run Full Python + Node Test Suites Immediately
```bash
python3 -m unittest test_pro_kit -v
node --test test_guardrails.mjs
python3 wal_queue.py --demo
```
~~~~

---

## 2. Lemon Squeezy Listing Copy & Upload Steps (`https://app.lemonsqueezy.com/products`)

### Lemon Squeezy Product #1 — Flagship Revenue Driver (`$14 USD` • 4 Sales Unlocks `$50` Wire)
- **Name:** `{paid_art.title}`
- **Pricing Model:** Standard One-Time Payment — **`$14.00 USD`** (Tax Category: *Digital Goods / Software*)
- **Digital File Upload:** `products/dist/{paid_art.zip_filename}` (`{paid_art.size_bytes:,}` bytes • SHA-256 `{paid_art.sha256[:16]}`)
- **Description (Copy & Paste):**
```markdown
{paid_spec["tagline"]}

{paid_spec["summary"]}

### What You Receive (`{paid_art.zip_filename}` — MIT Commercial License)
{paid_bullets}
```

### Lemon Squeezy Product #2 — Free Lead Magnet Mirror (`$0+` PWYW)
- **Name:** `{free_art.title}`
- **Pricing Model:** Pay What You Want — Minimum **`$0.00`**, Suggested **`$3.00`**
- **Digital File Upload:** `products/dist/{free_art.zip_filename}` (`{free_art.size_bytes:,}` bytes • SHA-256 `{free_art.sha256[:16]}`)
- **Description:** Same summary & highlights as Gumroad Product #1 above.

---

## 3. Connect `.env` Keys for Automated Sales & UTM Sync

```ini
GUMROAD_STORE_URL={gr_display}
GUMROAD_ACCESS_TOKEN=<gumroad_settings_advanced_application_access_token>

LEMONSQUEEZY_STORE_URL={ls_display}
LEMONSQUEEZY_API_KEY=<lemonsqueezy_settings_api_key>
LEMONSQUEEZY_STORE_ID=<numeric_store_id>
```

Then run:
- `python3 autopilot.py sync-stores --json` — syncs Gumroad `/v2/sales` & Lemon Squeezy `/v1/orders` with `utm_source` / `utm_campaign` attribution into `state/pipeline.db`.
- `python3 autopilot.py report-30d` — inspects your `$50` Lemon Squeezy & `$100` Gumroad Sri Lanka payout unlock progress bars!
"""


def _render_nasio_setup_and_challenge_guide(
    catalog: dict[str, Any],
    artifacts: list[BuiltProductArtifact],
    *,
    nasio_store_url: str = "",
    patreon_url: str = "",
) -> str:
    free_art = next((a for a in artifacts if a.tier == "free_pwyw"), artifacts[0])
    paid_art = next((a for a in artifacts if a.tier == "flagship_paid"), artifacts[-1])
    chal_art = next(
        (a for a in artifacts if a.slug == "nasio-7day-ai-automation-challenge-pack"),
        paid_art,
    )
    chal_spec = next(
        (p for p in catalog["products"] if p["slug"] == chal_art.slug),
        catalog["products"][-1],
    )
    chal_bullets = "\n".join(f"- {b}" for b in chal_spec.get("highlights", []))

    nasio_display = nasio_store_url or "https://nas.io/nuwansarat"
    patreon_display = patreon_url or "https://www.patreon.com/NuwansaraT"

    return f"""# Nas.io Storefront, 7-Day Challenge & Sri Lanka Payout Strategy Guide

- **Target Nas.io Hub (`NASIO_STORE_URL`):** `{nasio_display}`
- **Paired Patreon Hub (`PATREON_URL`):** `{patreon_display}`
- **Monthly Revenue Target:** `$50.00+ USD` net within 30 days (`$0` upfront subscription cost on Nas.io Free Plan)

---

## 1. Critical Sri Lanka (`Asia/Colombo`) Fee & Payout Settings

1. **Stay on Nas.io Free Plan (`$0/month`):** Zero fixed overhead while validating Month-1 demand.
2. **Turn `Pass fees to buyers = ON` (`Settings -> Payments / Pricing`):**
   - By default, Nas.io Free charges a platform + gateway fee (`~5.9% + processing`).
   - Toggling **`Pass fees to buyers = ON`** shifts checkout fees onto the buyer receipt so a **`$15.00` Challenge seat settles `~$15.00` net** into your creator wallet!
3. **Payoneer USD (`$10` Min) vs Direct Sri Lanka Bank Wire (`$65` LKR Min):**
   - **Recommended Month-1 Route — Payoneer USD (`$10 USD` minimum payout):** Link the **exact same Payoneer USD account** connected to your Patreon (`$25 USD` minimum). Pooling `$25+` from Patreon + `$25+` from Nas.io inside one Payoneer USD balance crosses Payoneer's local Sri Lanka bank transfer minimum (`$50 USD`) immediately in Month 1!
   - **Month-2 High-Volume Route — Direct Sri Lanka LKR Bank Wire (`$65 USD` minimum payout):** Switch to or enable direct LKR wire once single-platform Nas.io balance exceeds `$65 USD`.

---

## 2. 3-Product Nas.io Funnel Setup (One-Time 15-Minute Upload)

### Step A — Free Lead Magnet & Community Entry (`$0` Digital Resource -> Magic Reach List)
- **Upload File:** `products/dist/{free_art.zip_filename}` (`{free_art.size_bytes:,}` bytes • SHA-256 `{free_art.sha256[:16]}`)
- **Title:** `{free_art.title}`
- **Price:** `Free ($0)` — Every downloader joins your Nas.io community and becomes reachable via **Magic Reach** email/chat broadcasts at `$0` ESP cost.

### Step B — Flagship `$15 USD` 7-Day Cohort / Self-Paced Challenge (`4 Seats = $60 Net`)
- **Feature to Use:** `Nas.io -> Challenges -> Create Challenge`
- **Challenge Title:** `{chal_art.title}`
- **Entry Price:** **`$15.00 USD`** (`Pass fees to buyers = ON`)
- **Upload Resource Bundle:** `products/dist/{chal_art.zip_filename}` (`{chal_art.size_bytes:,}` bytes • SHA-256 `{chal_art.sha256[:16]}`)
- **Challenge Copy (Ready to Paste):**
```markdown
{chal_spec["tagline"]}

{chal_spec["summary"]}

### What You Build Across 7 Days (`{chal_art.zip_filename}`)
{chal_bullets}

### Verify Locally in 5 Seconds
python3 -m unittest test_challenge_kit -v
python3 day07_end_to_end_agent_pipeline.py --demo
```

### Step C — Complete Pro Architecture Upsell (`$14 USD` Digital Product)
- **Upload File:** `products/dist/{paid_art.zip_filename}` (`{paid_art.size_bytes:,}` bytes • SHA-256 `{paid_art.sha256[:16]}`)
- **Title:** `{paid_art.title}`
- **Price:** **`$14.00 USD`** (Includes Node/TypeScript guardrails, double-entry SQL ledger schemas, and 4 production GitHub Actions workflows).

---

## 3. Realistic `$50+ USD` Month-1 Target Math (`1 Hour/Day` Nas.io + Automated Patreon)

| Mix | Unit Math | Net Settled (`Pass fees = ON`) |
| :--- | :--- | :--- |
| **Path A (Challenge-First)** | `4 × $15` Nas.io 7-Day Challenge seats | **`$60.00 USD`** (`120%` of `$50` target) |
| **Path B (Hybrid Nas.io + Patreon)** | `2 × $15` Challenge + `1 × $14` Pro Kit + `2 × $5/mo` Patreon Builders | **`~$52.60 USD`** (`> $50` combined Payoneer payout!) |

---

## 4. Daily 60-Minute Nas.io Execution SOP (`exports/<slug>/nasio_daily_pack.md`)

Every `python3 autopilot.py run-daily` run generates a tailored 3-block pack for your 1-hour Nas.io block (and exposes **`Copy Nas.io 1-Hr Pack`** on `http://127.0.0.1:8765`):
1. **`00–15 min` — Nas.io Feed + Magic Reach Broadcast:** Paste Block 1 into your Nas.io community feed and toggle Magic Reach email notification.
2. **`15–40 min` — External Value-Hook Post (`<= 2` Links):** Post Block 2 on LinkedIn / X / Python developer threads and leave 3 concrete engineering replies on active reliability discussions.
3. **`40–60 min` — Warm Lead & Challenge Invite DMs:** Send the non-spammy 3-sentence diagnostic welcome from Block 3 to every new Free Mini-Kit downloader and active follower.

---

## 5. Automated Order Sync (`POST /api/webhooks/nasio` & `log-order`)

1. Set in `.env`:
   ```ini
   NASIO_STORE_URL={nasio_display}
   NASIO_WEBHOOK_SECRET=choose_a_random_32_char_secret
   ```
2. **Option A — Instant CLI Order Logging (3 Seconds):**
   ```bash
   python3 autopilot.py log-order --storefront nasio --order-id nas_001 --gross-usd 15.0 --utm-source nasio --sku nasio-7day-ai-automation-challenge-pack
   ```
3. **Option B — Zapier / Nas.io Webhook Receiver:**
   Point Zapier's *"New Purchase Transaction in Nas.io"* -> *"Webhooks by Zapier (POST)"* to `http://127.0.0.1:8765/api/webhooks/nasio` (or your tunnel URL) with header `X-Webhook-Secret: <NASIO_WEBHOOK_SECRET>`. Orders upsert straight into `state/pipeline.db` and update your live `$50` scoreboard!
"""


def _render_whop_setup_and_challenge_guide(
    catalog: dict[str, Any],
    artifacts: list[BuiltProductArtifact],
    *,
    whop_company_id: str = "biz_Af4z3lgivbct7t",
    whop_store_url: str = "",
    patreon_url: str = "",
) -> str:
    free_art = next((a for a in artifacts if a.tier == "free_pwyw"), artifacts[0])
    paid_art = next((a for a in artifacts if a.tier == "flagship_paid"), artifacts[-1])
    chal_art = next(
        (a for a in artifacts if a.slug == "nasio-7day-ai-automation-challenge-pack"),
        paid_art,
    )
    free_spec = next(
        (p for p in catalog["products"] if p["slug"] == free_art.slug),
        catalog["products"][0],
    )
    paid_spec = next(
        (p for p in catalog["products"] if p["slug"] == paid_art.slug),
        catalog["products"][1],
    )
    chal_spec = next(
        (p for p in catalog["products"] if p["slug"] == chal_art.slug),
        catalog["products"][-1],
    )
    free_bullets = "\n".join(f"- {b}" for b in free_spec.get("highlights", []))
    paid_bullets = "\n".join(f"- {b}" for b in paid_spec.get("highlights", []))
    chal_bullets = "\n".join(f"- {b}" for b in chal_spec.get("highlights", []))

    eff_cid = whop_company_id.strip() or "biz_Af4z3lgivbct7t"
    dash_url = f"https://whop.com/dashboard/{eff_cid}/"
    dev_url = f"https://whop.com/dashboard/{eff_cid}/developer/"
    whop_display = whop_store_url or f"https://whop.com/<your-store-slug> (Dashboard: {dash_url})"
    patreon_display = patreon_url or "https://www.patreon.com/NuwansaraT"

    return f"""# Whop Storefront, 7-Day Challenge, Discover Marketplace & API Setup Guide

- **Whop Business Dashboard:** `{dash_url}` (Company ID: `{eff_cid}`)
- **Whop Developer & API Keys Portal:** `{dev_url}`
- **Public Whop Storefront (`WHOP_STORE_URL`):** `{whop_display}`
- **Paired Patreon Hub (`PATREON_URL`):** `{patreon_display}` (`$25 USD` Payoneer payout)
- **Month-1 Net Target:** `$50.00+ USD` net (`$0/mo` upfront subscription · `$10 USD` Whop minimum payout · `1 hour/day` execution)

---

## 1. Why Whop + Patreon Wins for `$0`-Capital Sri Lanka (`Asia/Colombo`) Creators

1. **`$0/Month` Fixed Cost (Replaces Discontinued `$29/mo` Nas.io Plan):** Creating products, attaching Whop apps (**Files**, **Courses**, **Announcements**, **Forum**, **Chat**), and listing on **Whop Discover** costs `$0.00` upfront. Fees (`~3%` Whop platform + `~2.7% + $0.30` card processing) apply *only* when a buyer pays.
2. **Built-In Organic Buyer Traffic (`Whop Discover Marketplace`):** Unlike standalone checkout links that start with zero eyeballs, publishing your hub to **Whop Discover** indexes your free mini-kit and `$15` 7-Day Challenge directly in front of developers, AI builders, and automation buyers browsing Whop.
3. **Safe Balance Holding & `$10 USD` Minimum Payout:** Whop holds every settled USD sale safely inside your `{eff_cid}` ledger balance with **zero expiration** while you cross the low **`$10.00 USD` minimum payout** and accumulate `$50+ USD` before linking **Payoneer USD** or your **Sri Lanka local bank account** via Whop Payouts.

---

## 2. Create Your 3 Whop Products / Offers Inside `{dash_url}` (15-Minute One-Time Setup)

Open **`{dash_url}`** and configure three clean offers inside your Whop hub:

### Offer #1 — Free `$0` Lead Magnet + Community Entry (`Free Idempotent Cron Mini-Kit`)
1. Inside `{dash_url}`, click **Products / Offers -> Create Product** (Visibility: **Public / Listed**).
2. **Product Name:** `{free_art.title}`
3. **Pricing:** **Free (`$0.00`)** — One-time free access.
4. **Attach Whop Apps to This Product:**
   - **Files App:** Upload `products/dist/{free_art.zip_filename}` (`{free_art.size_bytes:,}` bytes • SHA-256 `{free_art.sha256[:16]}`).
   - **Announcements App:** Broadcast daily `[Day N/30]` benchmarks to every free member (triggers Whop push + email notifications at `$0` ESP cost).
   - **Chat App:** Public welcome channel to greet new downloaders and invite warm leads into the `$15` Challenge.
5. **Ready-to-Paste Description:**
```markdown
{free_spec["tagline"]}

{free_spec["summary"]}

### Included Zero-Dependency Modules (`{free_art.zip_filename}`)
{free_bullets}

### Verify Locally in 5 Seconds
python3 -m unittest discover -v
python3 idempotent_cron.py --demo
```

### Offer #2 — Flagship `$15 USD` 7-Day Python & AI Reliability Challenge (`4 Sales = ~$55.36 Net`)
1. Click **Create Product** in `{dash_url}` -> Name: `{chal_art.title}`
2. **Pricing:** **`$15.00 USD`** One-Time Payment (`~$13.84` net after `~3% + 2.7% + $0.30` -> `4` Challenge seats = **`$55.36 USD` net**!).
3. **Attach Whop Apps to This Product:**
   - **Files App (and/or Courses App):** Upload `products/dist/{chal_art.zip_filename}` (`{chal_art.size_bytes:,}` bytes • SHA-256 `{chal_art.sha256[:16]}`), and optionally create 7 Course lessons (`Day 1` → `Day 7`) pasting the failure-injection drills from `CHALLENGE_WORKBOOK.md`.
   - **Forum + Chat Apps:** Private Challenge Builders thread where buyers post their `python3 day07_end_to_end_agent_pipeline.py --demo` JSON output for review.
4. **Ready-to-Paste Challenge Description:**
```markdown
{chal_spec["tagline"]}

{chal_spec["summary"]}

### What You Build & Ship Across 7 Days (`{chal_art.zip_filename}`)
{chal_bullets}

### Verify Locally in 5 Seconds
python3 -m unittest test_challenge_kit -v
python3 day07_end_to_end_agent_pipeline.py --demo
```

### Offer #3 — `$14 USD` Complete Production Starter Kit (`Python + Node/TS + SQL + GitHub Actions`)
1. Click **Create Product** in `{dash_url}` -> Name: `{paid_art.title}`
2. **Pricing:** **`$14.00 USD`** One-Time Payment (MIT Commercial License).
3. **Attach Whop Apps:** **Files App** (`products/dist/{paid_art.zip_filename}` — `{paid_art.size_bytes:,}` bytes • SHA-256 `{paid_art.sha256[:16]}`) + **Announcements App**.
4. **Ready-to-Paste Description:**
```markdown
{paid_spec["tagline"]}

{paid_spec["summary"]}

### Included Production Modules & CI Blueprints (`{paid_art.zip_filename}`)
{paid_bullets}
```

---

## 3. List on **Whop Discover Marketplace** for Free Organic Buyer Traffic

1. Inside `{dash_url}`, open **Store Page / Discover Settings**.
2. **Store Headline:** `Zero-Dependency Python, SQLite WAL & Fault-Tolerant AI Reliability Labs`
3. **Category & Discovery Tags:** Category `Software / Coding & AI` · Tags `python, ai-agents, backend, automation, sqlite`.
4. **Gallery Visuals:** Upload 2–3 benchmark infographics directly from `site/pins/*.png` showing empirical `python3 -I` throughput and `429` retry reduction telemetry.
5. **Copy Your Public Store URL** (e.g. `https://whop.com/reliability-lab/`) and paste it into `.env`:
   ```ini
   WHOP_COMPANY_ID={eff_cid}
   WHOP_STORE_URL=https://whop.com/<your-public-slug>
   ```

---

## 4. Connect Native Whop REST API & Webhooks (`{dev_url}`)

1. **Generate `WHOP_API_KEY` (30 Seconds):**
   - Open **`{dev_url}`**.
   - Under **API Keys**, click **Create API Key** (Company Key scoped to `{eff_cid}` with read access to Payments & Memberships).
   - Add to `.env`:
     ```ini
     WHOP_COMPANY_ID={eff_cid}
     WHOP_API_KEY=paste_your_whop_company_api_key_here
     ```
   - Run `python3 autopilot.py sync-stores` or `python3 autopilot.py run-daily` — `engine/whop_client.py` automatically paginates `https://api.whop.com/api/v1/payments` & `https://api.whop.com/api/v1/memberships` and updates your `$50` scoreboard!
2. **Configure Real-Time Webhooks (`POST /api/webhooks/whop`):**
   - Inside **`{dev_url}` -> Webhooks -> Create Webhook**, point the endpoint URL to `https://<your-tunnel>/api/webhooks/whop` (local receiver: `http://127.0.0.1:8765/api/webhooks/whop`).
   - Subscribe to events: **`payment.succeeded`**, **`membership.went_valid`**, and **`refund.created` / `refund.updated` (`payment.refunded`)**.
   - Copy the webhook signing secret (`ws_...` or `whsec_...`) into `.env` as `WHOP_WEBHOOK_SECRET=...`.
3. **Manual 3-Second CLI Order Fallback:**
   ```bash
   python3 autopilot.py log-order --storefront whop --order-id pay_001 --gross-usd 15.0 --utm-source whop --sku nasio-7day-ai-automation-challenge-pack
   ```

---

## 5. Safe Balance Holding & Payout Path (`Asia/Colombo`)

- **Before First Payout:** You can start selling immediately **without** attaching a bank account upfront. Whop holds all settled USD earnings inside `{dash_url}` -> **Payouts / Balance**.
- **At `$10+ USD` / `$50+ USD` Settled:** Open `{dash_url}` -> **Payouts**, complete standard KYC verification, and withdraw either to your **Payoneer USD receiving account** (pooling seamlessly with your `$25+` Patreon Payoneer withdrawals into one USD balance) or via **Whop local Sri Lanka bank wire**.
"""


def build_all_products(
    project_root: Path,
    *,
    kofi_profile_url: str = "https://ko-fi.com/tharushanuwansara",
    primary_store_url: str = "https://ko-fi.com/tharushanuwansara/shop",
    mirror_store_url: str = "",
    lemonsqueezy_store_url: str = "",
    gumroad_store_url: str = "",
    whop_company_id: str = "biz_Af4z3lgivbct7t",
    whop_store_url: str = "",
    nasio_store_url: str = "",
    patreon_url: str = "",
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

    dual_setup_path = project_root / "exports" / "storefront" / "lemonsqueezy_and_gumroad_setup.md"
    dual_setup_md = _render_dual_storefront_setup(
        catalog,
        built_artifacts,
        lemonsqueezy_store_url=lemonsqueezy_store_url,
        gumroad_store_url=gumroad_store_url,
    )
    dual_setup_path.write_text(dual_setup_md, encoding="utf-8")

    whop_setup_path = project_root / "exports" / "storefront" / "whop_setup_and_challenge_guide.md"
    whop_setup_md = _render_whop_setup_and_challenge_guide(
        catalog,
        built_artifacts,
        whop_company_id=whop_company_id,
        whop_store_url=whop_store_url,
        patreon_url=patreon_url,
    )
    whop_setup_path.write_text(whop_setup_md, encoding="utf-8")

    nasio_setup_path = project_root / "exports" / "storefront" / "nasio_setup_and_challenge_guide.md"
    nasio_setup_md = _render_nasio_setup_and_challenge_guide(
        catalog,
        built_artifacts,
        nasio_store_url=nasio_store_url,
        patreon_url=patreon_url,
    )
    nasio_setup_path.write_text(nasio_setup_md, encoding="utf-8")

    return {
        "manifest_path": str(manifest_path),
        "storefront_copy_path": str(storefront_copy_path),
        "dual_storefront_setup_path": str(dual_setup_path),
        "whop_setup_path": str(whop_setup_path),
        "nasio_setup_path": str(nasio_setup_path),
        "products": [asdict(a) for a in built_artifacts],
    }
