export class CircuitOpenError extends Error {}

export interface CircuitBreakerOptions {
  failureThreshold?: number;
  recoveryTimeoutMs?: number;
  clock?: () => number;
}

export class CircuitBreaker {
  constructor(options?: CircuitBreakerOptions);
  readonly state: "CLOSED" | "OPEN" | "HALF_OPEN";
  execute<T>(fn: () => Promise<T> | T): Promise<T>;
}

export function extractStructuredJson<T = Record<string, unknown>>(
  rawText: string,
  requiredKeys?: string[]
): T;

export interface SignWebhookOptions {
  timestamp?: number;
  nonce?: string;
}

export function signWebhook(
  secret: string | Buffer,
  rawBody: string | Buffer,
  options?: SignWebhookOptions
): string;

export interface VerifyWebhookOptions {
  toleranceSeconds?: number;
  nowSeconds?: number;
  seenNonces?: Set<string> | null;
}

export function verifyWebhookSignature(
  secret: string | Buffer,
  headerValue: string,
  rawBody: string | Buffer,
  options?: VerifyWebhookOptions
): { verified: true; timestamp: number; nonce: string };
