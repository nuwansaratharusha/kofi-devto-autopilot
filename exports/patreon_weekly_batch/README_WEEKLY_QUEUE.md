# 7-Day Patreon Batch Queue (`2026-11-18` → `2026-11-24`)

- **Target Creator Hub:** `https://www.patreon.com/NuwansaraT` (Campaign `16519195`)
- **Why This Flow Exists:** Official Patreon API v2 supports 100% automated `GET` syncing for members, pledges, and published post URLs, while post creation uses Patreon's native web **Post Scheduler**. Queue all `7` days below in **one 5-minute weekly session**, and GitHub Actions (`0 13 * * *`) automatically reconciles live URLs and revenue every evening!

| Day | Schedule (`Asia/Colombo`) | Public Post File | Paid / Shop Post File | `.zip` Attachment | API v2 Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Day `1/30` | `2026-11-18` (`18:30 IST`) | `day01_2026-11-18_public.md` | `day01_2026-11-18_paid_tier_and_shop.md` | `products/dist/production-agent-automation-kit.zip` | `READY TO SCHEDULE` |
| Day `2/30` | `2026-11-19` (`18:30 IST`) | `day02_2026-11-19_public.md` | `day02_2026-11-19_paid_tier_and_shop.md` | `products/dist/production-agent-automation-kit.zip` | `READY TO SCHEDULE` |
| Day `3/30` | `2026-11-20` (`18:30 IST`) | `day03_2026-11-20_public.md` | `day03_2026-11-20_paid_tier_and_shop.md` | `products/dist/nasio-7day-ai-automation-challenge-pack.zip` | `READY TO SCHEDULE` |
| Day `4/30` | `2026-11-21` (`18:30 IST`) | `day04_2026-11-21_public.md` | `day04_2026-11-21_paid_tier_and_shop.md` | `products/dist/production-agent-automation-kit.zip` | `READY TO SCHEDULE` |
| Day `5/30` | `2026-11-22` (`18:30 IST`) | `day05_2026-11-22_public.md` | `day05_2026-11-22_paid_tier_and_shop.md` | `products/dist/production-agent-automation-kit.zip` | `READY TO SCHEDULE` |
| Day `6/30` | `2026-11-23` (`18:30 IST`) | `day06_2026-11-23_public.md` | `day06_2026-11-23_paid_tier_and_shop.md` | `products/dist/nasio-7day-ai-automation-challenge-pack.zip` | `READY TO SCHEDULE` |
| Day `7/30` | `2026-11-24` (`18:30 IST`) | `day07_2026-11-24_public.md` | `day07_2026-11-24_paid_tier_and_shop.md` | `products/dist/production-agent-automation-kit.zip` | `READY TO SCHEDULE` |

## 5-Minute Weekly Patreon Scheduler Steps
1. Open `https://www.patreon.com/NuwansaraT` -> **Create -> Post**.
2. Paste each `dayXX_*_public.md` file, set **Who can see this post = Free members / Public**, and click **Schedule** for `18:30` (`Asia/Colombo`).
3. For your weekly deep-dive / Shop drops, paste `dayXX_*_paid_tier_and_shop.md`, attach the `.zip` from `products/dist/`, set **Paid members** (or **$14 / $15 Shop price**), and click **Schedule**.
