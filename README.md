# Omnichannel Verified Content & Storefront Autopilot (Nas.io + Patreon Edition)

**Zero-Capital (`$0/mo`) • Python 3.11+ Stdlib-Only (`< 5 MB` Disk Footprint) • Isolated `python3 -I` Code Verification**

Built for **[Tharusha Nuwansara](https://www.patreon.com/NuwansaraT)** (`Asia/Colombo`, `+05:30`) to run a focused **30-Day Experiment (`2026-11-18` → `2026-12-18`)** across **Nas.io**, **Patreon**, **Google Blogger**, **DEV.to**, and the **GitHub Pages Canonical Hub**, measuring which channels and pillars drive real conversions.

---

## Balance-Holding Storefronts (No Stripe / No Receive-PayPal Needed in Sri Lanka)

1. **Nas.io (`NASIO_STORE_URL` + `NASIO_WEBHOOK_SECRET`, `$10 USD` Payoneer USD min / `$65 USD` LKR wire)** — Hosts the **$0 Free Mini-Kit** (Magic Reach email capture), the **$15 USD 7-Day AI & Python Reliability Challenge Pack** (`nasio-7day-ai-automation-challenge-pack.zip`, `Pass fees to buyers = ON`), and the **$14 Pro Kit**. Full strategy & setup guide lives in [`exports/storefront/nasio_setup_and_challenge_guide.md`](./exports/storefront/nasio_setup_and_challenge_guide.md).
2. **Patreon (`PATREON_URL` + `PATREON_CREATOR_ACCESS_TOKEN`, `$25 USD` min Payoneer payout)** — Hosts Free Public posts, `$5/mo` Builder Tier, and `$14–$15` Patreon Shop `.zip` releases, backed by a **7-Day Batch Scheduler Generator** (`batch-patreon`) and 100% automated API v2 member/pledge & post reconciliation (`engine/patreon_client.py`). Ready-to-paste copy lives in [`exports/storefront/patreon_setup_and_tiers.md`](./exports/storefront/patreon_setup_and_tiers.md).
3. **Optional MoR Mirrors (`LEMONSQUEEZY_STORE_URL` `$50` wire / `GUMROAD_STORE_URL` `$100` bank deposit)** — Documented in [`exports/storefront/lemonsqueezy_and_gumroad_setup.md`](./exports/storefront/lemonsqueezy_and_gumroad_setup.md).

---

## Automated Patreon + 1-Hour/Day Nas.io Flow (`18:30 Asia/Colombo` = `13:00 UTC`)

- **Weekly (5 Minutes):**
  - Run `python3 autopilot.py batch-patreon --start-date 2026-11-18 --days 7` to generate 7 consecutive verified `[Day N/30]` Public + Paid/Shop Markdown posts and `.zip` attachment manifest in `exports/patreon_weekly_batch/` for Patreon's native Post Scheduler.
- **Daily Automated (`0 13 * * *` GitHub Actions + `run-daily`):**
  - **Patreon API v2 Reconciliation**: Syncs active member pledges and reconciles newly published `[Day N/30]` post URLs into `state/pipeline.db`.
  - **Nas.io Daily 60-Min Execution Pack**: Generates `exports/<slug>/nasio_daily_pack.md` (`0-15m` Magic Reach broadcast, `15-40m` `<= 2`-link value hook, `40-60m` warm Challenge DM script).
  - **Google Blogger API v3 & Mon/Wed/Fri DEV.to Forem v1**: Publishes/stages canonical verified teardowns.

---

## CLI Reference

```bash
# 1. Run the full offline deterministic unit test suite
python3 -m unittest discover -s tests -v

# 2. Self-test and package all 3 deterministic SHA-256 .zip toolkits & storefront guides
python3 autopilot.py build-products --json

# 3. Generate a 7-day verified Patreon Post Scheduler batch + manifest
python3 autopilot.py batch-patreon --start-date 2026-11-18 --days 7 --json

# 4. Run daily Blogger + Patreon + Nas.io 1-hr pack (+ Mon/Wed/Fri DEV.to) + 30d report
python3 autopilot.py run-daily --date 2026-11-20 --offline --json

# 5. Log a Nas.io or Patreon sale / free lead (or POST JSON to /api/webhooks/nasio)
python3 autopilot.py log-order --storefront nasio --order-id nas_101 --gross-usd 15 --utm-source nasio --sku nasio-7day-ai-automation-challenge-pack

# 6. Sync Patreon v2, Gumroad v2 & Lemon Squeezy v1 orders/members
python3 autopilot.py sync-stores

# 7. Log channel impressions/clicks & inspect Month-2 scale/fix/pause verdicts
python3 autopilot.py log-metrics --slug <slug> --channel nasio --views 320 --clicks 28
python3 autopilot.py report-30d --as-of 2026-12-18

# 8. Launch localhost Control Center, 1-Click Copy Dashboard & /api/webhooks/nasio receiver
python3 autopilot.py serve --port 8765
```
