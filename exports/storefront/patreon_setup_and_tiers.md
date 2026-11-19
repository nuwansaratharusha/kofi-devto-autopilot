# Patreon Storefront, Membership Tiers & API v2 Setup Guide

- **Creator:** Tharusha Nuwansara (`Asia/Colombo`)
- **Target Patreon URL:** `https://www.patreon.com/c/tharushanuwansara`
- **Sri Lanka Payout Path:** Patreon holds your earnings balance safely; withdraw via **Payoneer** (**$25 USD** Patreon minimum payout, ~$1 transfer fee; Payoneer -> Sri Lanka local bank minimum typically $50–$100 USD). Keep **Lemon Squeezy** active alongside Patreon for direct $50 Sri Lanka bank wires with zero annual wallet fees.

---

## 1. Ready-to-Paste Patreon Bio & About Copy

**Page Headline:**
`Production Python, SQLite & Fault-Tolerant AI Engineering Kits`

**About Section:**
> Hey, I'm Tharusha Nuwansara! I'm running **30 Days of Zero-Cost Production Python & AI Reliability** — shipping battle-tested, zero-dependency Python 3.11+ and TypeScript reliability modules where every single code block is executed and verified inside an isolated sandbox before release.
>
> - **Follow for Free:** Get daily `[Day N/30]` engineering benchmarks, failure-mode tables, and the **Free Idempotent Cron & Circuit-Breaker Mini-Kit**.
> - **Join the Production Builder Tier ($5/month) or Shop ($14 one-time):** Download full MIT-licensed `.zip` starter kits (`wal_queue.py`, `llm_circuit_breaker.py`, `webhook_hmac.py`, TypeScript guardrails, SQL double-entry schemas, and GitHub Actions CI workflows).

---

## 2. Recommended Membership Tier & Patreon Shop Items

### Tier 1 — `Production Reliability Builder` (`$5 / month`)
- **Benefits:**
  - Full downloadable `.zip` source code attachments on every `[Day N/30]` deep-dive post.
  - Instant access to `free-idempotent-cron-mini-kit.zip` + monthly reliability updates.
  - Commercial MIT license across all benchmark harnesses and SQL/CI blueprints.

### Patreon Shop Item 1 — `Free Idempotent Cron & API Circuit-Breaker Mini-Kit` (`$0` / Free Public Sample)
- **Upload File:** `products/dist/free-idempotent-cron-mini-kit.zip`
- **Summary:** Drop-in Python 3.11+ SQLite cron lease lock + HTTP retry circuit breaker (`6/6` unit tests passing, zero pip dependencies).

### Patreon Shop Item 2 — `Production AI Agent & Zero-Cost Backend Reliability Starter Kit` (`$14 USD`)
- **Upload File:** `products/dist/production-agent-automation-kit.zip`
- **Summary:** Complete 15-file Python + Node/TypeScript + SQL + GitHub Actions reliability kit (`wal_queue.py`, `llm_circuit_breaker.py`, `webhook_hmac.py`, `agent_guardrails.mjs`, double-entry ledger SQL, and 5-page architecture cheat sheet — MIT Commercial License).

---

## 3. Connect 100% Automated Patreon API v2 Sync (30 Seconds)

1. Open `https://www.patreon.com/portal/registration/register-clients` while logged into your Patreon creator account.
2. Click **Create Client** (Name: `Omnichannel Autopilot`, Redirect URI: `http://127.0.0.1:8765/`).
3. Expand your newly created client and copy **`Creator's Access Token`**.
4. Add these lines to `/home/runner/work/kofi-devto-autopilot/kofi-devto-autopilot/.env`:
   ```ini
   PATREON_URL=https://www.patreon.com/c/tharushanuwansara
   PATREON_CREATOR_ACCESS_TOKEN=paste_your_creators_access_token_here
   # Optional: leave PATREON_CAMPAIGN_ID blank and autopilot.py discovers it automatically!
   PATREON_CAMPAIGN_ID=
   ```
5. Run `python3 autopilot.py sync-stores` or `python3 autopilot.py run-daily` — active pledges, member revenue, and published `[Day N/30]` post URLs sync automatically into your 30-Day Experiment Scoreboard!
