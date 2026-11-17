// Zero-dependency Node.js (>=18) Circuit Breaker, Structured JSON Validator & HMAC Webhook Guard
import { createHmac, timingSafeEqual } from "node:crypto";

export class CircuitOpenError extends Error {
  constructor(message) {
    super(message);
    this.name = "CircuitOpenError";
  }
}

export class CircuitBreaker {
  #failureThreshold;
  #recoveryTimeoutMs;
  #clock;
  #state = "CLOSED";
  #failures = 0;
  #openedAt = 0;

  constructor({ failureThreshold = 3, recoveryTimeoutMs = 30_000, clock = () => Date.now() } = {}) {
    if (failureThreshold < 1) throw new RangeError("failureThreshold must be >= 1");
    this.#failureThreshold = failureThreshold;
    this.#recoveryTimeoutMs = recoveryTimeoutMs;
    this.#clock = clock;
  }

  get state() {
    if (this.#state === "OPEN" && this.#clock() - this.#openedAt >= this.#recoveryTimeoutMs) {
      this.#state = "HALF_OPEN";
    }
    return this.#state;
  }

  async execute(fn) {
    if (this.state === "OPEN") {
      throw new CircuitOpenError("Circuit breaker is OPEN");
    }
    try {
      const result = await fn();
      this.#failures = 0;
      this.#state = "CLOSED";
      this.#openedAt = 0;
      return result;
    } catch (err) {
      this.#failures += 1;
      if (this.#state === "HALF_OPEN" || this.#failures >= this.#failureThreshold) {
        this.#state = "OPEN";
        this.#openedAt = this.#clock();
      }
      throw err;
    }
  }
}

export function extractStructuredJson(rawText, requiredKeys = []) {
  const fence = /```(?:json)?\s*(\{[\s\S]*?\}|\[[\s\S]*?\])\s*```/i.exec(rawText);
  const candidate = fence ? fence[1] : rawText.trim();
  const parsed = JSON.parse(candidate);
  if (requiredKeys.length > 0 && (typeof parsed !== "object" || parsed === null || Array.isArray(parsed))) {
    throw new TypeError("Expected JSON object");
  }
  for (const key of requiredKeys) {
    if (!(key in parsed)) {
      throw new Error(`Missing required field: ${key}`);
    }
  }
  return parsed;
}

export function signWebhook(secret, rawBody, { timestamp = Math.floor(Date.now() / 1000), nonce = "evt_1" } = {}) {
  const bodyBuf = Buffer.isBuffer(rawBody) ? rawBody : Buffer.from(String(rawBody), "utf8");
  const prefix = Buffer.from(`${timestamp}.${nonce}.`, "utf8");
  const digest = createHmac("sha256", secret).update(Buffer.concat([prefix, bodyBuf])).digest("hex");
  return `t=${timestamp},id=${nonce},v1=${digest}`;
}

export function verifyWebhookSignature(
  secret,
  headerValue,
  rawBody,
  { toleranceSeconds = 300, nowSeconds = Math.floor(Date.now() / 1000), seenNonces = null } = {}
) {
  const parts = {};
  for (const rawItem of String(headerValue).split(",")) {
    const item = rawItem.trim();
    const eqIdx = item.indexOf("=");
    if (eqIdx > 0) {
      parts[item.slice(0, eqIdx).trim()] = item.slice(eqIdx + 1).trim();
    }
  }
  const rawTs = parts.t ?? "";
  if (!/^\d+$/.test(rawTs)) {
    throw new Error("Malformed signature header");
  }
  const ts = Number.parseInt(rawTs, 10);
  const nonce = parts.id ?? "";
  const sigHex = parts.v1 ?? "";
  if (!Number.isSafeInteger(ts) || !nonce || !sigHex) {
    throw new Error("Malformed signature header");
  }
  if (!/^[0-9a-f]{64}$/i.test(sigHex)) {
    throw new Error("HMAC signature mismatch");
  }
  if (Math.abs(nowSeconds - ts) > toleranceSeconds) {
    throw new Error("Timestamp outside replay window");
  }
  const bodyBuf = Buffer.isBuffer(rawBody) ? rawBody : Buffer.from(String(rawBody), "utf8");
  const prefix = Buffer.from(`${ts}.${nonce}.`, "utf8");
  const expectedBuf = createHmac("sha256", secret).update(Buffer.concat([prefix, bodyBuf])).digest();
  const providedBuf = Buffer.from(sigHex, "hex");
  if (expectedBuf.length !== providedBuf.length || !timingSafeEqual(expectedBuf, providedBuf)) {
    throw new Error("HMAC signature mismatch");
  }
  if (seenNonces) {
    if (seenNonces.has(nonce)) {
      throw new Error(`Replay nonce rejected: ${nonce}`);
    }
    seenNonces.add(nonce);
  }
  return { verified: true, timestamp: ts, nonce };
}
