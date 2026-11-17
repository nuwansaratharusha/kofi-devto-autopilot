# Free Idempotent Cron & API Circuit-Breaker Mini-Kit

**Python 3.11+ • Zero Third-Party Dependencies • MIT Licensed**  
Created by **Tharusha Nuwansara** (`https://ko-fi.com/tharushanuwansara`)

---

## Why This Exists

Overlapping cron schedules (`crontab`, GitHub Actions `schedule`, systemd timers, container restarts) routinely double-charge webhooks or hammer rate-limited APIs during transient `429`/`503` spikes. You should not need a Redis cluster or Celery broker just to run a deterministic scheduled script safely.

This mini-kit gives you two single-file, stdlib-only building blocks:

1. **`idempotent_cron.py`** — Atomic SQLite WAL execution lease (`BEGIN IMMEDIATE`) with deterministic window keys (`minutely`, `hourly`, `daily`) and automatic stale-lease takeover if a previous worker OOM-killed mid-flight.
2. **`api_guard.py`** — Bounded HTTP retry client with `Retry-After` header parsing, full-jitter exponential backoff, and a `CLOSED -> OPEN -> HALF_OPEN` circuit breaker.

---

## 30-Second Quickstart

```bash
# 1. Run the deterministic unit test suite
python3 -m unittest discover -v

# 2. Run both interactive demos
python3 idempotent_cron.py --demo
python3 api_guard.py --demo
```

## Drop-In Usage

```python
from api_guard import GuardedHttpClient
from idempotent_cron import IdempotentCronRunner, compute_window_key

runner = IdempotentCronRunner("./state/cron.db", lease_seconds=300)
http = GuardedHttpClient(max_attempts=3)

def sync_orders() -> dict:
    resp = http.request("GET", "https://httpbin.org/json")
    return {"status": resp.status}

outcome = runner.run_once(
    job_name="hourly-order-sync",
    execution_key=compute_window_key("hourly-order-sync", cadence="hourly"),
    fn=sync_orders,
)
print(outcome)
```

---

## Need Transactional Queues, Dead-Letter CLI, Node/TS Guardrails & HMAC Webhooks?

Upgrade to the full **Production AI Agent & Zero-Cost Backend Reliability Starter Kit** (`production-agent-automation-kit.zip`) at [`https://ko-fi.com/tharushanuwansara/shop`](https://ko-fi.com/tharushanuwansara/shop) for transactional SQLite WAL queues, dead-letter replay CLI, Python + Node/TypeScript LLM structured-output circuit breakers, constant-time HMAC webhook verification, double-entry SQL ledgers, and 4 production GitHub Actions workflows.
