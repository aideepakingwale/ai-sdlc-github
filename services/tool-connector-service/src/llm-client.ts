import { GenerateRequestSchema, GenerateResponseSchema, SdlcError } from '@sdlc/shared';
import type { z } from 'zod';
export { parseJsonLoose } from '@sdlc/shared';

/** Request shape as callers provide it — schema defaults (tier, json, …) optional. */
export type GenerateRequestInput = z.input<typeof GenerateRequestSchema>;

/** Thin client for ai-client-service, used by persona tools. */
export async function callLlm(baseUrl: string, req: GenerateRequestInput): Promise<string> {
  const res = await fetch(`${baseUrl}/v1/generate`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(req),
    signal: AbortSignal.timeout(120_000),
  }).catch((err: unknown) => {
    throw new SdlcError('PROVIDER_ERROR', `ai-client unreachable: ${String(err)}`);
  });
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new SdlcError('PROVIDER_ERROR', `ai-client ${res.status}: ${body.slice(0, 300)}`);
  }
  const parsed = GenerateResponseSchema.safeParse(await res.json());
  if (!parsed.success) throw new SdlcError('PROVIDER_ERROR', 'ai-client returned malformed response');
  return parsed.data.content;
}
