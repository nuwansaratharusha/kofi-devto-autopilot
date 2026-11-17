import test from "node:test";
import assert from "node:assert/strict";
import {
  CircuitBreaker,
  CircuitOpenError,
  extractStructuredJson,
  signWebhook,
  verifyWebhookSignature,
} from "./agent_guardrails.mjs";

test("CircuitBreaker opens after threshold and recovers after timeout", async () => {
  let now = 1000;
  const breaker = new CircuitBreaker({
    failureThreshold: 2,
    recoveryTimeoutMs: 5000,
    clock: () => now,
  });

  await assert.rejects(() => breaker.execute(() => Promise.reject(new Error("boom 1"))));
  assert.equal(breaker.state, "CLOSED");
  await assert.rejects(() => breaker.execute(() => Promise.reject(new Error("boom 2"))));
  assert.equal(breaker.state, "OPEN");
  await assert.rejects(() => breaker.execute(() => "blocked"), CircuitOpenError);

  now = 7000;
  assert.equal(breaker.state, "HALF_OPEN");
  const ok = await breaker.execute(() => "recovered");
  assert.equal(ok, "recovered");
  assert.equal(breaker.state, "CLOSED");
});

test("extractStructuredJson strips markdown fences and enforces required keys", () => {
  const parsed = extractStructuredJson('```json\n{"action":"deploy","confidence":0.97}\n```', [
    "action",
    "confidence",
  ]);
  assert.deepEqual(parsed, { action: "deploy", confidence: 0.97 });
  assert.throws(() => extractStructuredJson('{"action":"deploy"}', ["action", "confidence"]));
});

test("verifyWebhookSignature validates constant-time HMAC and blocks replays", () => {
  const secret = "whsec_node_test";
  const body = '{"order_id":"ord_99","amount":1400}';
  const nonces = new Set();
  const header = signWebhook(secret, body, { timestamp: 1760000000, nonce: "evt_abc" });

  const verified = verifyWebhookSignature(secret, header, body, {
    nowSeconds: 1760000020,
    toleranceSeconds: 300,
    seenNonces: nonces,
  });
  assert.equal(verified.verified, true);

  // Replay must fail
  assert.throws(() =>
    verifyWebhookSignature(secret, header, body, {
      nowSeconds: 1760000025,
      toleranceSeconds: 300,
      seenNonces: nonces,
    })
  );

  // Tampered trailing non-hex suffix must be rejected (never truncated by Buffer.from(..., 'hex'))
  const freshHeader = signWebhook(secret, body, { timestamp: 1760000010, nonce: "evt_fresh" });
  assert.throws(() =>
    verifyWebhookSignature(secret, `${freshHeader}zzzz_tampered`, body, {
      nowSeconds: 1760000020,
      toleranceSeconds: 300,
    })
  );

  // Valid signed nonces containing '=' (e.g. base64/composite IDs) must verify cleanly
  const eqNonceHeader = signWebhook(secret, body, { timestamp: 1760000010, nonce: "evt=part2=ok" });
  const eqVerified = verifyWebhookSignature(secret, eqNonceHeader, body, {
    nowSeconds: 1760000020,
    toleranceSeconds: 300,
  });
  assert.equal(eqVerified.nonce, "evt=part2=ok");
});
