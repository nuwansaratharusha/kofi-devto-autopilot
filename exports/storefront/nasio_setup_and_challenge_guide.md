# Nas.io Storefront, 7-Day Challenge & Sri Lanka Payout Strategy Guide

- **Target Nas.io Hub (`NASIO_STORE_URL`):** `https://nas.io/nuwansarat`
- **Paired Patreon Hub (`PATREON_URL`):** `https://www.patreon.com/NuwansaraT`
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
- **Upload File:** `products/dist/free-idempotent-cron-mini-kit.zip` (`9,859` bytes • SHA-256 `852daf37db2ba501`)
- **Title:** `Free Idempotent Cron & API Circuit-Breaker Mini-Kit (Python 3.11+ Zero-Dep)`
- **Price:** `Free ($0)` — Every downloader joins your Nas.io community and becomes reachable via **Magic Reach** email/chat broadcasts at `$0` ESP cost.

### Step B — Flagship `$15 USD` 7-Day Cohort / Self-Paced Challenge (`4 Seats = $60 Net`)
- **Feature to Use:** `Nas.io -> Challenges -> Create Challenge`
- **Challenge Title:** `7-Day Fault-Tolerant Python & AI Agent Reliability Challenge Pack (7 Daily Labs + Workbook)`
- **Entry Price:** **`$15.00 USD`** (`Pass fees to buyers = ON`)
- **Upload Resource Bundle:** `products/dist/nasio-7day-ai-automation-challenge-pack.zip` (`23,678` bytes • SHA-256 `ec8804dbbbe5e3fb`)
- **Challenge Copy (Ready to Paste):**
```markdown
Master production AI & Python reliability in 7 self-tested daily labs—zero pip packages, zero cloud bills.

A structured 7-day hands-on reliability challenge pack featuring 7 runnable, unit-tested Python 3.11+ labs (Retry-After full jitter, strict LLM JSON schema repair, SQLite WAL job queues, time-bucketed cron leases, dual-key HMAC webhook guards, token-budgeted model routers, and an end-to-end capstone pipeline) plus a step-by-step participant workbook.

### What You Build Across 7 Days (`nasio-7day-ai-automation-challenge-pack.zip`)
- 7 self-contained daily Python 3.11+ labs (`day01_retry_jitter.py` through `day07_end_to_end_agent_pipeline.py`) with interactive `--demo` telemetry
- Eliminates thundering-herd 429 retries, `isinstance(True, int)` LLM JSON bugs, overlapping cron double-runs, and webhook replay vulnerabilities
- Includes `CHALLENGE_WORKBOOK.md` with daily 45-minute failure-injection drills designed for Nas.io Cohort Challenges & Patreon builders
- 100% Python standard library (`0` third-party dependencies), deterministic `unittest` suite (`test_challenge_kit.py`), and MIT Commercial License

### Verify Locally in 5 Seconds
python3 -m unittest test_challenge_kit -v
python3 day07_end_to_end_agent_pipeline.py --demo
```

### Step C — Complete Pro Architecture Upsell (`$14 USD` Digital Product)
- **Upload File:** `products/dist/production-agent-automation-kit.zip` (`21,100` bytes • SHA-256 `bd7b8c8d7fd4b2b3`)
- **Title:** `Production AI Agent & Zero-Cost Backend Reliability Starter Kit (Python + Node/TS + SQL + CI)`
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
   NASIO_STORE_URL=https://nas.io/nuwansarat
   NASIO_WEBHOOK_SECRET=choose_a_random_32_char_secret
   ```
2. **Option A — Instant CLI Order Logging (3 Seconds):**
   ```bash
   python3 autopilot.py log-order --storefront nasio --order-id nas_001 --gross-usd 15.0 --utm-source nasio --sku nasio-7day-ai-automation-challenge-pack
   ```
3. **Option B — Zapier / Nas.io Webhook Receiver:**
   Point Zapier's *"New Purchase Transaction in Nas.io"* -> *"Webhooks by Zapier (POST)"* to `http://127.0.0.1:8765/api/webhooks/nasio` (or your tunnel URL) with header `X-Webhook-Secret: <NASIO_WEBHOOK_SECRET>`. Orders upsert straight into `state/pipeline.db` and update your live `$50` scoreboard!
