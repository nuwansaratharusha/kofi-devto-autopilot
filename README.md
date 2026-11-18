# Omnichannel Verified Content & Storefront Autopilot (30-Day Experiment Edition)

**Zero-Capital (`$0/mo`) • Python 3.11+ Stdlib-Only (`< 5 MB` Disk Footprint) • Isolated `python3 -I` Code Verification**

Built for **[Tharusha Nuwansara](https://ko-fi.com/tharushanuwansara)** (`Asia/Colombo`, `+05:30`) to run a full **30-Day Experiment (`2026-11-18` → `2026-12-18`)** across **Lemon Squeezy**, **Gumroad**, **Google Blogger**, **Pinterest (`1000x1500` `2:3` Pins + RSS Auto-Publish)**, and **DEV.to**, measuring which channels and pillars drive conversions.

---

## Dual Merchant-of-Record Storefronts (No PayPal / No Stripe Needed in Sri Lanka)

1. **Gumroad (`GUMROAD_STORE_URL`, `$100 USD` min Sri Lanka bank deposit)** — Instant go-live storefront hosting the **$0+ Pay-What-You-Want Mini-Kit** (`free-idempotent-cron-mini-kit.zip`) and mirroring the $14 kit.
2. **Lemon Squeezy (`LEMONSQUEEZY_STORE_URL`, `$50 USD` min Sri Lanka bank wire)** — Merchant of Record hosting the **$14 USD Flagship Starter Kit** (`production-agent-automation-kit.zip`; **4 sales = $56 gross > $50 wire unlock**).
3. Copy-paste product listings, tags, and upload steps live in [`exports/storefront/lemonsqueezy_and_gumroad_setup.md`](./exports/storefront/lemonsqueezy_and_gumroad_setup.md).

---

## Daily & Mon/Wed/Fri Omnichannel Flow (`18:30 Asia/Colombo` = `13:00 UTC`)

- **Daily (Days 1–30):**
  - **Google Blogger API v3 (`engine/blogger_client.py`)**: Publishes/stages `[Day N/30] <Title>` with series header, `utm_source=blogger&utm_medium=series&utm_campaign=30d_exp_2026_11`, and dynamic series index (`exports/<slug>/blogger_post.html`).
  - **Pinterest `1000x1500` (`2:3`) Infographic (`engine/pin_renderer.py`, `engine/pinterest_client.py`)**: Renders a `< 35 KB` stdlib indexed-color PNG (`site/pins/<slug>.png`) + SVG (`exports/<slug>/pinterest_pin.svg`), pushes via Pinterest API v5 (`POST /v5/pins`) when credentials are set, and updates `site/pinterest_feed.xml` for zero-key Pinterest RSS Auto-Publish.
- **Mon / Wed / Fri:**
  - **DEV.to Forem API v1 (`engine/devto_client.py`)**: Stages/publishes canonical technical teardowns without triggering daily spam filters.

---

## CLI Reference

```bash
# 1. Run the full offline deterministic unit test suite
python3 -m unittest discover -s tests -v

# 2. Package deterministic SHA-256 .zip toolkits & storefront setup guides
python3 autopilot.py build-products --json

# 3. Authorize Google Blogger OAuth2 PKCE (one-time loopback helper)
python3 autopilot.py auth-blogger

# 4. Run daily Blogger + Pinterest (+ Mon/Wed/Fri DEV.to) + 30-day report
python3 autopilot.py run-daily --date 2026-11-18 --offline

# 5. Sync Gumroad v2 & Lemon Squeezy v1 orders (+ optional Gumroad draft SKU creation)
python3 autopilot.py sync-stores --create-gumroad-drafts

# 6. Log channel impressions/clicks & inspect Month-2 scale/fix/pause verdicts
python3 autopilot.py log-metrics --slug <slug> --channel pinterest --views 450 --clicks 28
python3 autopilot.py report-30d --as-of 2026-12-18

# 7. Launch localhost Omnichannel Control Center & 1-Click Copy Dashboard
python3 autopilot.py serve --port 8765
```
