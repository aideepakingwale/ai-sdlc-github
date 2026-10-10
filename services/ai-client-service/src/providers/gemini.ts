import type { GenerateRequest } from '@sdlc/shared';
import { classifyStatus, isAbortError, makeUsage, ProviderCallError, type LlmProvider, type ProviderResult } from './types.js';

/** Google Gemini Flash via the generateContent REST API. */
export function createGeminiProvider(opts: {
  apiKey: string | undefined;
  model: string;
  baseUrl?: string;
  timeoutMs?: number;
}): LlmProvider {
  const base = opts.baseUrl ?? 'https://generativelanguage.googleapis.com/v1beta';
  return {
    id: 'gemini',
    configured: Boolean(opts.apiKey),
    model: opts.model,
    vision: true, // Gemini Flash is natively multimodal (D-66)

    async generate(req: GenerateRequest, signal: AbortSignal): Promise<ProviderResult> {
      const model = req.model || opts.model; // per-call model override (D-68)
      const systemParts = req.messages.filter((m) => m.role === 'system').map((m) => ({ text: m.content }));
      const contents = req.messages
        .filter((m) => m.role !== 'system')
        .map((m) => ({
          role: m.role === 'assistant' ? 'model' : 'user',
          parts: [
            ...(m.content ? [{ text: m.content }] : []),
            // Inline images as base64 blobs (D-66).
            ...(m.images ?? []).map((img) => ({
              inlineData: { mimeType: img.mimeType, data: img.dataBase64 },
            })),
          ],
        }));

      const timeout = AbortSignal.timeout(opts.timeoutMs ?? 60_000);
      const combined = AbortSignal.any([signal, timeout]);
      const res = await fetch(`${base}/models/${model}:generateContent?key=${opts.apiKey}`, {
        method: 'POST',
        signal: combined,
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          ...(systemParts.length ? { systemInstruction: { parts: systemParts } } : {}),
          contents,
          generationConfig: {
            temperature: req.temperature,
            maxOutputTokens: req.maxTokens,
            ...(req.json ? { responseMimeType: 'application/json' } : {}),
          },
        }),
      }).catch((err: unknown) => {
        // D-103: abort (disconnect/timeout) → `canceled`, so the breaker isn't tripped.
        if (isAbortError(err, combined)) {
          throw new ProviderCallError('gemini', 'canceled', `gemini call canceled: ${String(err)}`);
        }
        throw new ProviderCallError('gemini', 'transient', `network error calling gemini: ${String(err)}`);
      });

      if (!res.ok) throw classifyStatus('gemini', res.status, await res.text().catch(() => ''));

      const data = (await res.json()) as {
        candidates?: Array<{ content?: { parts?: Array<{ text?: string }> }; finishReason?: string }>;
        usageMetadata?: {
          promptTokenCount?: number; candidatesTokenCount?: number;
          cachedContentTokenCount?: number; thoughtsTokenCount?: number;
        };
        responseId?: string;
      };
      const content = data.candidates?.[0]?.content?.parts?.map((p) => p.text ?? '').join('');
      // Gemini's promptTokenCount already INCLUDES cached content, and thinking
      // ("thoughts") tokens are billed as output but not counted in candidatesTokenCount.
      const um = data.usageMetadata;
      const cached = um?.cachedContentTokenCount ?? 0;
      const usage = makeUsage(
        Math.max(0, (um?.promptTokenCount ?? 0) - cached),
        (um?.candidatesTokenCount ?? 0) + (um?.thoughtsTokenCount ?? 0),
        { cacheRead: cached },
      );
      const finish = data.candidates?.[0]?.finishReason;
      if (!content) {
        throw new ProviderCallError('gemini', 'transient', 'gemini returned no content', undefined, {
          usage, providerRequestId: data.responseId, stopReason: finish,
        });
      }
      return {
        content,
        model,
        usage,
        providerRequestId: data.responseId,
        stopReason: finish,
        // D-103: `MAX_TOKENS` finishReason means the output was capped/truncated.
        truncated: data.candidates?.[0]?.finishReason === 'MAX_TOKENS',
      };
    },
  };
}
