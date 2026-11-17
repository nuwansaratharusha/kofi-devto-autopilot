# Production AI Agent & Zero-Cost Backend Reliability Starter Kit

**Python 3.11+ & Node.js 18+ • Zero Third-Party Dependencies • MIT Commercial License**  
Created by **Tharusha Nuwansara** (`https://ko-fi.com/tharushanuwansara`)

---

## What's Inside This Kit

| File | Stack | Purpose |
| :--- | :--- | :--- |
| `wal_queue.py` | Python 3.11+ | Transactional SQLite WAL job queue with atomic visibility leases, exponential backoff, and dead-letter CLI (`enqueue`, `stats`, `requeue-dlq`, `--demo`). |
| `llm_circuit_breaker.py` | Python 3.11+ | Multi-provider structured JSON validator, repair-prompt loop, hard token budget guard, and circuit breaker. |
| `webhook_hmac.py` | Python 3.11+ | Constant-time SHA-256 HMAC webhook signer/verifier with timestamp window + SQLite WAL replay-nonce guard. |
| `agent_guardrails.mjs` + `.d.ts` | Node 18+ / TS | Zero-dependency ES module + TypeScript types for `CircuitBreaker`, `extractStructuredJson`, and `verifyWebhookSignature`. |
| `sql/idempotent_ledger.sql` | SQLite / Postgres | Double-entry integer-cent financial ledger & webhook event deduplication schema. |
| `github_actions/*.yml` | GitHub Actions | 4 ready-to-drop CI/cron workflows (idempotent cron, SQLite state snapshot, polyglot guardrail CI, canary probe). |
| `CHEATSHEET_ARCHITECTURE.md` | Reference | Production invariants for WAL locking, LLM schema repair, HMAC replay defense, and double-entry accounting. |

---

## Verify Everything Locally in 5 Seconds

```bash
# Run Python unit test suite (wal_queue, llm_circuit_breaker, webhook_hmac)
python3 -m unittest test_pro_kit -v

# Run Node.js zero-dependency guardrail test suite
node --test test_guardrails.mjs

# Run interactive SQLite WAL queue CLI demo
python3 wal_queue.py --demo
```

---

## License

Released under the **MIT License** (`LICENSE`) — unrestricted use in personal, client, SaaS, and commercial applications.
