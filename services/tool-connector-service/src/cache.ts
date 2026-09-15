import { createHash } from 'node:crypto';
import type { Redis } from 'ioredis';

/**
 * 24h Redis cache for deterministic tools (Module 1 §2: `mcp_cache:<tool_hash>`).
 * Key = sha256(tool + canonical args). Only tools flagged `cacheable` use this.
 */
export class ToolCache {
  constructor(
    private readonly redis: Redis,
    private readonly ttlSeconds: number,
  ) {}

  private key(tool: string, args: unknown): string {
    const hash = createHash('sha256').update(`${tool}\0${JSON.stringify(args)}`).digest('hex');
    return `mcp_cache:${hash}`;
  }

  async get(tool: string, args: unknown): Promise<unknown | null> {
    try {
      const raw = await this.redis.get(this.key(tool, args));
      return raw ? (JSON.parse(raw) as unknown) : null;
    } catch {
      return null; // cache is best-effort; never fail the tool call
    }
  }

  async set(tool: string, args: unknown, value: unknown): Promise<void> {
    try {
      await this.redis.set(this.key(tool, args), JSON.stringify(value), 'EX', this.ttlSeconds);
    } catch {
      /* best-effort */
    }
  }
}
