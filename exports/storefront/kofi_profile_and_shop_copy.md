# Ready-to-Paste Ko-fi Profile, Shop & Sri Lanka Payout Guide

**Target Profile:** [https://nuwansaratharusha.github.io/kofi-devto-autopilot](https://nuwansaratharusha.github.io/kofi-devto-autopilot)  
**Primary Shop URL (`PRIMARY_STORE_URL`):** `https://nuwansaratharusha.github.io/kofi-devto-autopilot`  
**Mirror Store Status:** Not set yet (leave `MIRROR_STORE_URL=` empty if using Ko-fi PayPal/Stripe, or set to your Polar.sh / Lemon Squeezy URL in `.env`).

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
- Free ($0+ Pay-What-You-Want): **Free Idempotent Cron & API Circuit-Breaker Mini-Kit (Python 3.11+ Zero-Dep)** (`free-idempotent-cron-mini-kit.zip`)
- Full Production Kit ($14 USD, MIT Commercial License): **Production AI Agent & Zero-Cost Backend Reliability Starter Kit (Python + Node/TS + SQL + CI)** (`production-agent-automation-kit.zip`)

Both kits are 100% stdlib-first, unit-tested, and ready in the Shop tab!
```

---

## 3. Ko-fi Shop Listing #1 — Free Lead Magnet ($0+ Pay-What-You-Want)

- **Upload File:** `products/dist/free-idempotent-cron-mini-kit.zip` (`9,859` bytes • SHA-256 `852daf37db2ba501...`)
- **Item Name:** `Free Idempotent Cron & API Circuit-Breaker Mini-Kit (Python 3.11+ Zero-Dep)`
- **Price:** `$0` (Enable *"Pay what you want"* • Suggested amount: `$3`)
- **Summary / Description:**
```markdown
Stop double-firing scheduled jobs and cascading 429/503 API outages without Redis or Celery.

A drop-in, stdlib-only Python 3.11+ mini-kit featuring an atomic SQLite WAL idempotent cron runner with stale-lock recovery plus an HTTP exponential-backoff & sliding-window circuit breaker—backed by deterministic unit tests.

### What You Get Inside (`free-idempotent-cron-mini-kit.zip`)
- Atomic SQLite WAL lease locking (`BEGIN IMMEDIATE`) that eliminates duplicate cron runs across overlapping triggers
- Zero-dependency HTTP retry & `CLOSED -> OPEN -> HALF_OPEN` circuit breaker respecting `Retry-After` headers
- 100% Python stdlib (`>=3.11`) with deterministic unit test suite (`python3 -m unittest -v`)
- MIT Commercial & Personal License — copy straight into production scripts, cron containers, or GitHub Actions

### Verify in 5 Seconds
Unzip and run:
`python3 -m unittest discover -v`
`python3 idempotent_cron.py --demo`
```

---

## 4. Ko-fi Shop Listing #2 — Flagship Revenue Driver ($14 USD • 4 Sales = ~$52 Net)

- **Upload File:** `products/dist/production-agent-automation-kit.zip` (`21,100` bytes • SHA-256 `bd7b8c8d7fd4b2b3...`)
- **Item Name:** `Production AI Agent & Zero-Cost Backend Reliability Starter Kit (Python + Node/TS + SQL + CI)`
- **Price:** `$14 USD` (Fixed or $14+ Pay-What-You-Want)
- **Summary / Description:**
```markdown
Ship fault-tolerant AI workflows, transactional SQLite WAL queues, HMAC webhooks, and double-entry ledgers on $0 infrastructure.

Complete MIT-licensed engineering starter kit combining a transactional SQLite WAL job queue + dead-letter CLI, Python & Node/TypeScript LLM structured-output circuit breakers, constant-time HMAC webhook replay guards, 4 production GitHub Actions workflows, and double-entry idempotent SQL schemas.

### Included Production Modules & Blueprints (`production-agent-automation-kit.zip`)
- Transactional SQLite WAL Job Queue + Dead-Letter CLI (`wal_queue.py`) achieving thousands of atomic leases/sec without Redis
- Python (`llm_circuit_breaker.py`) + Node/TypeScript (`agent_guardrails.mjs` & `.d.ts`) structured JSON schema validators, token-budget caps, and multi-provider failover routers
- Constant-time SHA-256 HMAC Webhook Verifier (`webhook_hmac.py`) with replay-window enforcement & SQLite nonce deduplication
- Double-entry idempotent ledger schema (`sql/idempotent_ledger.sql`) + 4 copy-paste GitHub Actions YAML automation blueprints
- Full automated unit test suites in both Python (`unittest`) and Node (`node --test`) + MIT Commercial License

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
