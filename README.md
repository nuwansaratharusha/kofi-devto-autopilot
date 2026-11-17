# Ko-fi & DEV.to Verified Content Autopilot

**Zero-Capital (`$0/mo`) • Python 3.11+ Stdlib-Only (`< 5 MB` Disk Footprint) • Isolated `python3 -I` Code Verification**

Built for **[Tharusha Nuwansara](https://ko-fi.com/tharushanuwansara)** (`Asia/Colombo`, `+05:30`) to automate ~90–95% of an engineering publication and digital-product funnel without paid SaaS subscriptions, third-party pip/npm bloat, or unverified AI slop.

---

## Revenue Architecture (How This Reaches ~$50 in 30 Days on $0 Capital)

Instead of relying on blocked paywalls (Medium Partner Program requires a paid $5/mo membership and bans paywalled AI text; Gumroad bank transfers hold funds until $100), this pipeline pairs **two unit-tested downloadable `.zip` developer toolkits** with a **Mon / Wed / Fri (`18:30 Asia/Colombo` = `13:00 UTC`) empirical tutorial engine**:

1. **`free-idempotent-cron-mini-kit.zip` ($0+ Pay-What-You-Want, Suggested $3 Tip)** — Lead magnet & tip driver containing a standalone SQLite WAL idempotent cron runner + HTTP `Retry-After` circuit breaker + unit tests.
2. **`production-agent-automation-kit.zip` ($14 USD Flagship Kit, MIT Commercial License)** — Complete transactional SQLite WAL job queue & dead-letter CLI, Python + Node/TypeScript structured-output circuit breakers, constant-time SHA-256 HMAC webhook replay guard, double-entry SQL ledger schema, and 4 GitHub Actions YAML blueprints (**4 sales = $56 gross / ~$52 net**).

---

## 5-Minute Operator Setup Checklist

### Step 1: Configure Your Ko-fi Storefront (`https://ko-fi.com/tharushanuwansara`)
1. Open [`exports/storefront/kofi_profile_and_shop_copy.md`](./exports/storefront/kofi_profile_and_shop_copy.md) (or run `python3 autopilot.py serve` and click **Copy Ko-fi Profile & Shop Copy**).
2. Paste the ready-made Bio, $50 Monthly Goal, and Pinned Welcome Post onto `https://ko-fi.com/tharushanuwansara`.
3. Upload the two verified archives from [`products/dist/`](./products/dist/) to your **Ko-fi Shop**:
   - `products/dist/free-idempotent-cron-mini-kit.zip` ($0+ Pay-What-You-Want, suggested $3)
   - `products/dist/production-agent-automation-kit.zip` ($14 USD)

> **Sri Lanka (`Asia/Colombo`) Payout Note:** Ko-fi passes payments directly to a receive-enabled **PayPal** or **Stripe Standard** account with **$0 minimum payout**. If you prefer direct Sri Lanka bank payouts via a Merchant of Record, upload the exact same two `.zip` files to **Polar.sh** ($10 minimum payout via Stripe Express) or **Lemon Squeezy** ($50 minimum direct bank wire) and set `MIRROR_STORE_URL=https://polar.sh/<handle>` in `.env`. All paid kit links update automatically with zero code edits.

### Step 2: Connect Your Free DEV.to API Key
1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```
2. Visit `https://dev.to/settings/extensions`, generate a free Forem API key, and set `DEVTO_API_KEY=...` in `.env`.
3. By default, `PUBLISH_MODE=draft` stages tutorials on DEV.to with `"published": false` so you can do a 60-second review before clicking Publish (set `PUBLISH_MODE=live` for hands-free live publishing).

---

## CLI Reference

```bash
# 1. Run the full offline deterministic unit test suite
python3 -m unittest discover -s tests -v

# 2. Self-test both toolkits and package deterministic SHA-256 .zip archives
python3 autopilot.py build-products --json

# 3. Harvest trends, compose Mon/Wed/Fri tutorials, execute every snippet in python3 -I,
#    build the canonical static site (site/), export syndication packs (exports/), and stage drafts
python3 autopilot.py run-pipeline --limit 3 --push-draft

# 4. Push a specific verified article slug to DEV.to (POST new or idempotent PUT update)
python3 autopilot.py push-devto --slug <slug>

# 5. Inspect SQLite WAL ledger, built .zip artifacts, and staging health
python3 autopilot.py status

# 6. Launch the accessible localhost Operator Review & 1-Click Copy Dashboard
python3 autopilot.py serve --port 8765
```

---

## 60-Second Cross-Posting Workflow (Medium, Substack, Ko-fi, LinkedIn)

Every verified article automatically generates:
- **Canonical HTML5 Page (`site/posts/<slug>/index.html`)** with `<article class="h-entry">` and `<link rel="canonical">` so pasting the canonical URL into **`https://medium.com/p/import`** imports clean formatting and sets your canonical SEO tag automatically.
- **Ready-to-Copy Syndication Packs (`exports/<slug>/`)** for **Ko-fi Posts**, **Medium**, **Substack**, and **LinkedIn**—accessible via 1-click **"Copy"** buttons at `http://127.0.0.1:8765`.
