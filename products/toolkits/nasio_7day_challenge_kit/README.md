# 7-Day Fault-Tolerant Python & AI Agent Reliability Challenge Pack

**Python 3.11+ • 100% Standard Library (`0` Third-Party Dependencies) • MIT Commercial License**  
Created by **Tharusha Nuwansara** (`https://www.patreon.com/NuwansaraT` · `https://nuwansaratharusha.github.io/kofi-devto-autopilot/`)

---

## Included Daily Labs & Files

| File | Focus |
| :--- | :--- |
| `day01_retry_jitter.py` | `Retry-After` HTTP header parser + full-jitter exponential backoff + total sleep budget cap |
| `day02_llm_schema_validator.py` | Markdown JSON extractor + strict schema validator (`bool` vs `int` guard) + bounded repair loop |
| `day03_sqlite_wal_queue.py` | Transactional SQLite WAL job queue (`BEGIN IMMEDIATE` leases, idempotency keys, dead-letter quarantine) |
| `day04_idempotent_cron_lease.py` | Time-bucketed (`hourly` / `daily`) SQLite cron execution lock with automatic stale-lease recovery |
| `day05_hmac_webhook_guard.py` | Constant-time SHA-256 HMAC verifier, dual-key secret rotation, and SQLite nonce replay guard |
| `day06_token_budget_router.py` | Multi-provider LLM/API failover router with circuit breaker + hard token & USD cent ceilings |
| `day07_end_to_end_agent_pipeline.py` | End-to-end capstone pipeline connecting Days 1–6 into an auditable fulfillment workflow |
| `test_challenge_kit.py` | Deterministic `unittest` suite verifying all 7 daily modules and edge cases |
| `CHALLENGE_WORKBOOK.md` | Step-by-step 7-day implementation guide & failure-injection drills |

---

## Verify Everything in 5 Seconds

```bash
python3 -m unittest test_challenge_kit -v
python3 day07_end_to_end_agent_pipeline.py --demo
```
