# Dual Storefront Setup Guide: Lemon Squeezy + Gumroad (Sri Lanka No-PayPal/No-Stripe Flow)

**Why Both Storefronts Run in Parallel (`Asia/Colombo` Creator Strategy):**
- **Gumroad (`https://<your-handle>.gumroad.com`)** — Instant 60-second go-live without waiting on store review; hosts the **$0+ Pay-What-You-Want Mini-Kit** (`free_checkout_url`) and mirrors the **$14 Flagship Kit**. Holds USD balance safely until **$100 USD** minimum Sri Lanka local bank deposit.
- **Lemon Squeezy (`https://<your-handle>.lemonsqueezy.com`)** — Merchant of Record handling global VAT/GST with **$50 USD** minimum Sri Lanka local bank wire payout (`4 × $14 = $56 gross`); hosts the **$14 Flagship Starter Kit** (`paid_checkout_url`) and mirrors the **$0+ Mini-Kit**.

---

## 1. Gumroad Listing Copy & Upload Steps (`https://app.gumroad.com/products`)

> **Tip:** Once `GUMROAD_ACCESS_TOKEN` is set in `.env`, running `python3 autopilot.py sync-stores --create-gumroad-drafts` automatically creates both draft products via `POST /v2/products` (`published=false`) so you only have to attach the `.zip` files and click Publish!

### Gumroad Product #1 — Free Lead Magnet (`$0+` Pay-What-You-Want)
- **Product Type:** Digital Product
- **Name:** `Free Idempotent Cron & API Circuit-Breaker Mini-Kit (Python 3.11+ Zero-Dep)`
- **Custom Permalink:** `free-idempotent-cron-mini-kit`
- **Price:** `$0` (Toggle **"Allow customers to pay what they want"** -> Minimum `$0`, Suggested `$3`)
- **Content File to Upload:** `products/dist/free-idempotent-cron-mini-kit.zip` (`9,859` bytes • SHA-256 `c2d03abcc0a0f346`)
- **Tags:** `python, sqlite, cron, reliability, backend`
- **Summary:** `Stop double-firing scheduled jobs and cascading 429/503 API outages without Redis or Celery.`
- **Description (Copy & Paste):**
~~~~markdown
A drop-in, stdlib-only Python 3.11+ mini-kit featuring an atomic SQLite WAL idempotent cron runner with stale-lock recovery plus an HTTP exponential-backoff & sliding-window circuit breaker—backed by deterministic unit tests.

### Included Zero-Dependency Modules (`free-idempotent-cron-mini-kit.zip`)
- Atomic SQLite WAL lease locking (`BEGIN IMMEDIATE`) that eliminates duplicate cron runs across overlapping triggers
- Zero-dependency HTTP retry & `CLOSED -> OPEN -> HALF_OPEN` circuit breaker respecting `Retry-After` headers
- 100% Python stdlib (`>=3.11`) with deterministic unit test suite (`python3 -m unittest -v`)
- MIT Commercial & Personal License — copy straight into production scripts, cron containers, or GitHub Actions

### Verify Locally in 5 Seconds
```bash
python3 -m unittest discover -v
python3 idempotent_cron.py --demo
```
~~~~

### Gumroad Product #2 — Flagship Production Kit (`$14 USD`)
- **Product Type:** Digital Product
- **Name:** `Production AI Agent & Zero-Cost Backend Reliability Starter Kit (Python + Node/TS + SQL + CI)`
- **Custom Permalink:** `production-agent-automation-kit`
- **Price:** `$14 USD` (Allow PWYW minimum `$14`, suggested `$19`)
- **Content File to Upload:** `products/dist/production-agent-automation-kit.zip` (`21,102` bytes • SHA-256 `c3f1cdcda9af6021`)
- **Tags:** `python, typescript, sqlite, ai-agents, webhooks`
- **Summary:** `Ship fault-tolerant AI workflows, transactional SQLite WAL queues, HMAC webhooks, and double-entry ledgers on $0 infrastructure.`
- **Description (Copy & Paste):**
~~~~markdown
Complete MIT-licensed engineering starter kit combining a transactional SQLite WAL job queue + dead-letter CLI, Python & Node/TypeScript LLM structured-output circuit breakers, constant-time HMAC webhook replay guards, 4 production GitHub Actions workflows, and double-entry idempotent SQL schemas.

### Included Production Modules & CI Blueprints (`production-agent-automation-kit.zip`)
- Transactional SQLite WAL Job Queue + Dead-Letter CLI (`wal_queue.py`) achieving thousands of atomic leases/sec without Redis
- Python (`llm_circuit_breaker.py`) + Node/TypeScript (`agent_guardrails.mjs` & `.d.ts`) structured JSON schema validators, token-budget caps, and multi-provider failover routers
- Constant-time SHA-256 HMAC Webhook Verifier (`webhook_hmac.py`) with replay-window enforcement & SQLite nonce deduplication
- Double-entry idempotent ledger schema (`sql/idempotent_ledger.sql`) + 4 copy-paste GitHub Actions YAML automation blueprints
- Full automated unit test suites in both Python (`unittest`) and Node (`node --test`) + MIT Commercial License

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
- **Name:** `Production AI Agent & Zero-Cost Backend Reliability Starter Kit (Python + Node/TS + SQL + CI)`
- **Pricing Model:** Standard One-Time Payment — **`$14.00 USD`** (Tax Category: *Digital Goods / Software*)
- **Digital File Upload:** `products/dist/production-agent-automation-kit.zip` (`21,102` bytes • SHA-256 `c3f1cdcda9af6021`)
- **Description (Copy & Paste):**
```markdown
Ship fault-tolerant AI workflows, transactional SQLite WAL queues, HMAC webhooks, and double-entry ledgers on $0 infrastructure.

Complete MIT-licensed engineering starter kit combining a transactional SQLite WAL job queue + dead-letter CLI, Python & Node/TypeScript LLM structured-output circuit breakers, constant-time HMAC webhook replay guards, 4 production GitHub Actions workflows, and double-entry idempotent SQL schemas.

### What You Receive (`production-agent-automation-kit.zip` — MIT Commercial License)
- Transactional SQLite WAL Job Queue + Dead-Letter CLI (`wal_queue.py`) achieving thousands of atomic leases/sec without Redis
- Python (`llm_circuit_breaker.py`) + Node/TypeScript (`agent_guardrails.mjs` & `.d.ts`) structured JSON schema validators, token-budget caps, and multi-provider failover routers
- Constant-time SHA-256 HMAC Webhook Verifier (`webhook_hmac.py`) with replay-window enforcement & SQLite nonce deduplication
- Double-entry idempotent ledger schema (`sql/idempotent_ledger.sql`) + 4 copy-paste GitHub Actions YAML automation blueprints
- Full automated unit test suites in both Python (`unittest`) and Node (`node --test`) + MIT Commercial License
```

### Lemon Squeezy Product #2 — Free Lead Magnet Mirror (`$0+` PWYW)
- **Name:** `Free Idempotent Cron & API Circuit-Breaker Mini-Kit (Python 3.11+ Zero-Dep)`
- **Pricing Model:** Pay What You Want — Minimum **`$0.00`**, Suggested **`$3.00`**
- **Digital File Upload:** `products/dist/free-idempotent-cron-mini-kit.zip` (`9,859` bytes • SHA-256 `c2d03abcc0a0f346`)
- **Description:** Same summary & highlights as Gumroad Product #1 above.

---

## 3. Connect `.env` Keys for Automated Sales & UTM Sync

```ini
GUMROAD_STORE_URL=https://<your-handle>.gumroad.com
GUMROAD_ACCESS_TOKEN=<gumroad_settings_advanced_application_access_token>

LEMONSQUEEZY_STORE_URL=https://<your-handle>.lemonsqueezy.com
LEMONSQUEEZY_API_KEY=<lemonsqueezy_settings_api_key>
LEMONSQUEEZY_STORE_ID=<numeric_store_id>
```

Then run:
- `python3 autopilot.py sync-stores --json` — syncs Gumroad `/v2/sales` & Lemon Squeezy `/v1/orders` with `utm_source` / `utm_campaign` attribution into `state/pipeline.db`.
- `python3 autopilot.py report-30d` — inspects your `$50` Lemon Squeezy & `$100` Gumroad Sri Lanka payout unlock progress bars!
