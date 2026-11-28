# Whop Storefront, 7-Day Challenge, Discover Marketplace & API Setup Guide

- **Whop Business Dashboard:** `https://whop.com/dashboard/biz_Af4z3lgivbct7t/` (Company ID: `biz_Af4z3lgivbct7t`)
- **Whop Developer & API Keys Portal:** `https://whop.com/dashboard/biz_Af4z3lgivbct7t/developer/`
- **Public Whop Storefront (`WHOP_STORE_URL`):** `https://whop.com/tharusha-nuwansara`
- **Paired Patreon Hub (`PATREON_URL`):** `https://www.patreon.com/NuwansaraT` (`$25 USD` Payoneer payout)
- **Month-1 Net Target:** `$50.00+ USD` net (`$0/mo` upfront subscription · `$10 USD` Whop minimum payout · `1 hour/day` execution)

---

## 1. Why Whop + Patreon Wins for `$0`-Capital Sri Lanka (`Asia/Colombo`) Creators

1. **`$0/Month` Fixed Cost (Replaces Discontinued `$29/mo` Nas.io Plan):** Creating products, attaching Whop apps (**Files**, **Courses**, **Announcements**, **Forum**, **Chat**), and listing on **Whop Discover** costs `$0.00` upfront. Fees (`~3%` Whop platform + `~2.7% + $0.30` card processing) apply *only* when a buyer pays.
2. **Built-In Organic Buyer Traffic (`Whop Discover Marketplace`):** Unlike standalone checkout links that start with zero eyeballs, publishing your hub to **Whop Discover** indexes your free mini-kit and `$15` 7-Day Challenge directly in front of developers, AI builders, and automation buyers browsing Whop.
3. **Safe Balance Holding & `$10 USD` Minimum Payout:** Whop holds every settled USD sale safely inside your `biz_Af4z3lgivbct7t` ledger balance with **zero expiration** while you cross the low **`$10.00 USD` minimum payout** and accumulate `$50+ USD` before linking **Payoneer USD** or your **Sri Lanka local bank account** via Whop Payouts.

---

## 2. Create Your 3 Whop Products / Offers Inside `https://whop.com/dashboard/biz_Af4z3lgivbct7t/` (15-Minute One-Time Setup)

Open **`https://whop.com/dashboard/biz_Af4z3lgivbct7t/`** and configure three clean offers inside your Whop hub:

### Offer #1 — Free `$0` Lead Magnet + Community Entry (`Free Idempotent Cron Mini-Kit`)
1. Inside `https://whop.com/dashboard/biz_Af4z3lgivbct7t/`, click **Products / Offers -> Create Product** (Visibility: **Public / Listed**).
2. **Product Name:** `Free Idempotent Cron & API Circuit-Breaker Mini-Kit (Python 3.11+ Zero-Dep)`
3. **Pricing:** **Free (`$0.00`)** — One-time free access.
4. **Attach Whop Apps to This Product:**
   - **Files App:** Upload `products/dist/free-idempotent-cron-mini-kit.zip` (`9,859` bytes • SHA-256 `c2d03abcc0a0f346`).
   - **Announcements App:** Broadcast daily `[Day N/30]` benchmarks to every free member (triggers Whop push + email notifications at `$0` ESP cost).
   - **Chat App:** Public welcome channel to greet new downloaders and invite warm leads into the `$15` Challenge.
5. **Ready-to-Paste Description:**
```markdown
Stop double-firing scheduled jobs and cascading 429/503 API outages without Redis or Celery.

A drop-in, stdlib-only Python 3.11+ mini-kit featuring an atomic SQLite WAL idempotent cron runner with stale-lock recovery plus an HTTP exponential-backoff & sliding-window circuit breaker—backed by deterministic unit tests.

### Included Zero-Dependency Modules (`free-idempotent-cron-mini-kit.zip`)
- Atomic SQLite WAL lease locking (`BEGIN IMMEDIATE`) that eliminates duplicate cron runs across overlapping triggers
- Zero-dependency HTTP retry & `CLOSED -> OPEN -> HALF_OPEN` circuit breaker respecting `Retry-After` headers
- 100% Python stdlib (`>=3.11`) with deterministic unit test suite (`python3 -m unittest -v`)
- MIT Commercial & Personal License — copy straight into production scripts, cron containers, or GitHub Actions

### Verify Locally in 5 Seconds
python3 -m unittest discover -v
python3 idempotent_cron.py --demo
```

### Offer #2 — Flagship `$15 USD` 7-Day Python & AI Reliability Challenge (`4 Sales = ~$55.36 Net`)
1. Click **Create Product** in `https://whop.com/dashboard/biz_Af4z3lgivbct7t/` -> Name: `7-Day Fault-Tolerant Python & AI Agent Reliability Challenge Pack (7 Daily Labs + Workbook)`
2. **Pricing:** **`$15.00 USD`** One-Time Payment (`~$13.84` net after `~3% + 2.7% + $0.30` -> `4` Challenge seats = **`$55.36 USD` net**!).
3. **Attach Whop Apps to This Product:**
   - **Files App (and/or Courses App):** Upload `products/dist/nasio-7day-ai-automation-challenge-pack.zip` (`23,678` bytes • SHA-256 `ec8804dbbbe5e3fb`), and optionally create 7 Course lessons (`Day 1` → `Day 7`) pasting the failure-injection drills from `CHALLENGE_WORKBOOK.md`.
   - **Forum + Chat Apps:** Private Challenge Builders thread where buyers post their `python3 day07_end_to_end_agent_pipeline.py --demo` JSON output for review.
4. **Ready-to-Paste Challenge Description:**
```markdown
Master production AI & Python reliability in 7 self-tested daily labs—zero pip packages, zero cloud bills.

A structured 7-day hands-on reliability challenge pack featuring 7 runnable, unit-tested Python 3.11+ labs (Retry-After full jitter, strict LLM JSON schema repair, SQLite WAL job queues, time-bucketed cron leases, dual-key HMAC webhook guards, token-budgeted model routers, and an end-to-end capstone pipeline) plus a step-by-step participant workbook.

### What You Build & Ship Across 7 Days (`nasio-7day-ai-automation-challenge-pack.zip`)
- 7 self-contained daily Python 3.11+ labs (`day01_retry_jitter.py` through `day07_end_to_end_agent_pipeline.py`) with interactive `--demo` telemetry
- Eliminates thundering-herd 429 retries, `isinstance(True, int)` LLM JSON bugs, overlapping cron double-runs, and webhook replay vulnerabilities
- Includes `CHALLENGE_WORKBOOK.md` with daily 45-minute failure-injection drills designed for Whop Challenges / Courses / Files & Patreon Shop builders
- 100% Python standard library (`0` third-party dependencies), deterministic `unittest` suite (`test_challenge_kit.py`), and MIT Commercial License

### Verify Locally in 5 Seconds
python3 -m unittest test_challenge_kit -v
python3 day07_end_to_end_agent_pipeline.py --demo
```

### Offer #3 — `$14 USD` Complete Production Starter Kit (`Python + Node/TS + SQL + GitHub Actions`)
1. Click **Create Product** in `https://whop.com/dashboard/biz_Af4z3lgivbct7t/` -> Name: `Production AI Agent & Zero-Cost Backend Reliability Starter Kit (Python + Node/TS + SQL + CI)`
2. **Pricing:** **`$14.00 USD`** One-Time Payment (MIT Commercial License).
3. **Attach Whop Apps:** **Files App** (`products/dist/production-agent-automation-kit.zip` — `21,102` bytes • SHA-256 `c3f1cdcda9af6021`) + **Announcements App**.
4. **Ready-to-Paste Description:**
```markdown
Ship fault-tolerant AI workflows, transactional SQLite WAL queues, HMAC webhooks, and double-entry ledgers on $0 infrastructure.

Complete MIT-licensed engineering starter kit combining a transactional SQLite WAL job queue + dead-letter CLI, Python & Node/TypeScript LLM structured-output circuit breakers, constant-time HMAC webhook replay guards, 4 production GitHub Actions workflows, and double-entry idempotent SQL schemas.

### Included Production Modules & CI Blueprints (`production-agent-automation-kit.zip`)
- Transactional SQLite WAL Job Queue + Dead-Letter CLI (`wal_queue.py`) achieving thousands of atomic leases/sec without Redis
- Python (`llm_circuit_breaker.py`) + Node/TypeScript (`agent_guardrails.mjs` & `.d.ts`) structured JSON schema validators, token-budget caps, and multi-provider failover routers
- Constant-time SHA-256 HMAC Webhook Verifier (`webhook_hmac.py`) with replay-window enforcement & SQLite nonce deduplication
- Double-entry idempotent ledger schema (`sql/idempotent_ledger.sql`) + 4 copy-paste GitHub Actions YAML automation blueprints
- Full automated unit test suites in both Python (`unittest`) and Node (`node --test`) + MIT Commercial License
```

---

## 3. List on **Whop Discover Marketplace** for Free Organic Buyer Traffic

1. Inside `https://whop.com/dashboard/biz_Af4z3lgivbct7t/`, open **Store Page / Discover Settings**.
2. **Store Headline:** `Zero-Dependency Python, SQLite WAL & Fault-Tolerant AI Reliability Labs`
3. **Category & Discovery Tags:** Category `Software / Coding & AI` · Tags `python, ai-agents, backend, automation, sqlite`.
4. **Gallery Visuals:** Upload 2–3 benchmark infographics directly from `site/pins/*.png` showing empirical `python3 -I` throughput and `429` retry reduction telemetry.
5. **Copy Your Public Store URL** (e.g. `https://whop.com/reliability-lab/`) and paste it into `.env`:
   ```ini
   WHOP_COMPANY_ID=biz_Af4z3lgivbct7t
   WHOP_STORE_URL=https://whop.com/<your-public-slug>
   ```

---

## 4. Connect Native Whop REST API & Webhooks (`https://whop.com/dashboard/biz_Af4z3lgivbct7t/developer/`)

1. **Generate `WHOP_API_KEY` (30 Seconds):**
   - Open **`https://whop.com/dashboard/biz_Af4z3lgivbct7t/developer/`**.
   - Under **API Keys**, click **Create API Key** (Company Key scoped to `biz_Af4z3lgivbct7t` with read access to Payments & Memberships).
   - Add to `.env`:
     ```ini
     WHOP_COMPANY_ID=biz_Af4z3lgivbct7t
     WHOP_API_KEY=paste_your_whop_company_api_key_here
     ```
   - Run `python3 autopilot.py sync-stores` or `python3 autopilot.py run-daily` — `engine/whop_client.py` automatically paginates `https://api.whop.com/api/v1/payments` & `https://api.whop.com/api/v1/memberships` and updates your `$50` scoreboard!
2. **Configure Real-Time Webhooks (`POST /api/webhooks/whop`):**
   - Inside **`https://whop.com/dashboard/biz_Af4z3lgivbct7t/developer/` -> Webhooks -> Create Webhook**, point the endpoint URL to `https://<your-tunnel>/api/webhooks/whop` (local receiver: `http://127.0.0.1:8765/api/webhooks/whop`).
   - Subscribe to events: **`payment.succeeded`**, **`membership.went_valid`**, and **`refund.created` / `refund.updated` (`payment.refunded`)**.
   - Copy the webhook signing secret (`ws_...` or `whsec_...`) into `.env` as `WHOP_WEBHOOK_SECRET=...`.
3. **Manual 3-Second CLI Order Fallback:**
   ```bash
   python3 autopilot.py log-order --storefront whop --order-id pay_001 --gross-usd 15.0 --utm-source whop --sku nasio-7day-ai-automation-challenge-pack
   ```

---

## 5. Safe Balance Holding & Payout Path (`Asia/Colombo`)

- **Before First Payout:** You can start selling immediately **without** attaching a bank account upfront. Whop holds all settled USD earnings inside `https://whop.com/dashboard/biz_Af4z3lgivbct7t/` -> **Payouts / Balance**.
- **At `$10+ USD` / `$50+ USD` Settled:** Open `https://whop.com/dashboard/biz_Af4z3lgivbct7t/` -> **Payouts**, complete standard KYC verification, and withdraw either to your **Payoneer USD receiving account** (pooling seamlessly with your `$25+` Patreon Payoneer withdrawals into one USD balance) or via **Whop local Sri Lanka bank wire**.
