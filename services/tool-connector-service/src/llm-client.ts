import { GenerateRequestSchema, GenerateResponseSchema, SdlcError } from '@sdlc/shared';
import type { z } from 'zod';
export { parseJsonLoose } from '@sdlc/shared';

/** Request shape as callers provide it — schema defaults (tier, json, …) optional. */
export type GenerateRequestInput = z.input<typeof GenerateRequestSchema>;

// D-103: env-tunable so a large LLM-backed tool op isn't aborted at a fixed 120s.
const TOOL_LLM_TIMEOUT_MS = Number(process.env.TOOL_LLM_TIMEOUT_MS) || 120_000;

/** Thin client for ai-client-service, used by persona tools (D-05). */
export async function callLlm(baseUrl: string, req: GenerateRequestInput): Promise<string> {
  const res = await fetch(`${baseUrl}/v1/generate`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(req),
    signal: AbortSignal.timeout(TOOL_LLM_TIMEOUT_MS),
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
