import { loadEnv, ToolsEnvSchema, type ToolsEnv } from '@sdlc/shared';
import type { HttpPolicy } from '../impl/http.js';

/** Build a validated ToolsEnv for tests. */
export function testEnv(overrides: Record<string, string> = {}): ToolsEnv {
  return loadEnv(ToolsEnvSchema, {
    TOOLS_MODE: 'mock',
    AI_CLIENT_URL: 'http://localhost:9',
    REDIS_URL: 'redis://localhost:6379',
    ...overrides,
  });
}

/** Retry policy with no real waiting, optionally recording requested sleeps. */
export function fastPolicy(sleeps: number[] = [], extra: Partial<HttpPolicy> = {}): Partial<HttpPolicy> {
  return {
    baseDelayMs: 10,
    maxDelayMs: 100,
    random: () => 0.5,
    timeoutMs: 5_000,
    sleep: async (ms: number) => {
      sleeps.push(ms);
    },
    ...extra,
  };
}
