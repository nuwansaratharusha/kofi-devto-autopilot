# 30-Day Omnichannel Experiment Report (`2026-11-18` → `2026-12-18`)

- **As-Of Date (`Asia/Colombo`):** `2026-11-28` (Day `11` / `30` · `20` days remaining)
- **Verified Series Articles Built:** `11 / 30`
- **Total Net Revenue (Refunds Excluded):** `$0.00 USD` (`0` paid orders, `0` free leads, `0` refunded excluded)

---

## 1. Sri Lanka Storefront & Payout Unlock Meters

| Storefront | Net Settled | Payout Unlock Minimum | Progress | Remaining | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Storefront #1: Whop ($10 Min Payout · Built-in Marketplace)** | `$0.00` | `$50.00` ($10 Min Payout · Built-in Marketplace) | `0.0%` | `$50.00` | `ACCUMULATING` |
| **Storefront #2: Patreon ($25 Payoneer)** | `$0.00` | `$25.00` (Payoneer) | `0.0%` | `$25.00` | `ACCUMULATING` |
| **Lemon Squeezy** | `$0.00` | `$50.00` (Bank Wire) | `0.0%` | `$50.00` | `ACCUMULATING` |
| **Gumroad** | `$0.00` | `$100.00` (Local Bank) | `0.0%` | `$100.00` | `ACCUMULATING` |

---

## 2. Channel Attribution & Deterministic Month-2 Verdicts

| Channel | Assets Ready | Views | Clicks | CTR | Free Kits | Paid Orders | Net USD | Verdict |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `whop` | 11 | 0 | 0 | 0.0% | 0 | 0 | $0.00 | **NOT WORKING — PAUSE & REALLOCATE** |
| `patreon` | 11 | 0 | 0 | 0.0% | 0 | 0 | $0.00 | **NOT WORKING — PAUSE & REALLOCATE** |
| `blogger` | 11 | 0 | 0 | 0.0% | 0 | 0 | $0.00 | **NOT WORKING — PAUSE & REALLOCATE** |
| `devto` | 11 | 0 | 0 | 0.0% | 0 | 0 | $0.00 | **NOT WORKING — PAUSE & REALLOCATE** |
| `canonical_site` | 11 | 0 | 0 | 0.0% | 0 | 0 | $0.00 | **NOT WORKING — PAUSE & REALLOCATE** |
| `pinterest` | 11 | 0 | 0 | 0.0% | 0 | 0 | $0.00 | **NOT WORKING — PAUSE & REALLOCATE** |

---

## 3. Content Pillar Performance Breakdown

| Pillar Track | Verified Posts | Views | Clicks | Free Kits | Paid Orders | Net USD | Verdict |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `pillar_a` (Fault-Tolerant AI & API Guardrails) | 4 | 0 | 0 | 0 | 0 | $0.00 | **NOT WORKING — PAUSE & REALLOCATE** |
| `pillar_b` (Zero-Cost Backend, SQLite WAL Queues & Cron Automation) | 4 | 0 | 0 | 0 | 0 | $0.00 | **NOT WORKING — PAUSE & REALLOCATE** |
| `pillar_c` (Idempotent Webhooks & Double-Entry Ledger Teardowns) | 3 | 0 | 0 | 0 | 0 | $0.00 | **NOT WORKING — PAUSE & REALLOCATE** |

---

## 4. Channel Readiness & Root-Cause Diagnostics

| Channel | Integration Mode | Live Remote / Staged | Missing Credentials | Root-Cause Diagnostic |
| :--- | :--- | :--- | :--- | :--- |
| `whop` | `LIVE_ACTIVE` | 1 / 11 | None | Low reach & zero conversions (0 views, 0 clicks, 0 orders) — pause manual effort here if unchanged by Day 30 and reallocate to top channel. |
| `patreon` | `UNCONFIGURED_DRY_RUN` | 0 / 11 | None | Patreon API v2 sync is live, but 0 public/tier posts on Patreon match [Day N/30] yet. |
| `blogger` | `UNCONFIGURED_DRY_RUN` | 0 / 11 | `BLOGGER_BLOG_ID`, `BLOGGER_REFRESH_TOKEN` | Running in dry_run mode (missing BLOGGER_BLOG_ID, BLOGGER_REFRESH_TOKEN). |
| `devto` | `UNCONFIGURED_DRY_RUN` | 0 / 11 | `DEVTO_API_KEY` | Running in dry_run mode (missing DEVTO_API_KEY). |
| `canonical_site` | `LIVE_ACTIVE` | 11 / 11 | None | Static GitHub Pages hub is deployed live, but awaits inbound referral traffic from Whop/DEV.to. |
| `pinterest` | `UNCONFIGURED_DRY_RUN` | 0 / 11 | `PINTEREST_ACCESS_TOKEN`, `PINTEREST_BOARD_ID` | Running in dry_run mode (missing PINTEREST_ACCESS_TOKEN, PINTEREST_BOARD_ID). |

### Recommended Activation Steps
- Add DEVTO_API_KEY (PUBLISH_MODE=live) so Mon/Wed/Fri articles publish to DEV.to and sync real page_views_count into channel_metrics.
