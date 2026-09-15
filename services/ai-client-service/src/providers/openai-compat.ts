import type { GenerateRequest, LlmProviderId } from '@sdlc/shared';
import {
  classifyStatus,
  estimateTokens,
  fitMaxTokens,
  ProviderCallError,
  type LlmProvider,
  type ProviderResult,
} from './types.js';

interface OpenAiCompatOpts {
  id: LlmProviderId;
  baseUrl: string;
  apiKey: string | undefined;
  model: string;
  timeoutMs?: number;
  /** For self-hosted models (Ollama/vLLM) config is presence of a base URL,
   *  not an API key. */
  configuredWhen?: boolean;
  /** Per-request token budget (prompt + completion). 0 = unlimited. Caps the
   *  completion so large prompts fit a provider's TPM ceiling (e.g. Groq free
   *  tier = 12k) instead of 413-ing. */
  requestTokenBudget?: number;
}

/**
 * OpenAI-compatible `response_format: json_object` (Groq, xAI, OpenAI) is
 * REJECTED with HTTP 400 unless the word "json" appears somewhere in the
 * messages. Our phase agents ask for structured output but don't always say
 * "json" in the prose, so the call 400s → classified transient → silent mock
 * fallback (a second reason "everything ran as mock"). Guarantee the mention.
 */
function ensureJsonMention(messages: GenerateRequest['messages']): GenerateRequest['messages'] {
  if (messages.some((m) => /json/i.test(m.content))) return messages;
  return [{ role: 'system', content: 'Respond with a single valid JSON object and nothing else.' }, ...messages];
}

/**
 * Groq, Grok (xAI) and local Ollama/vLLM all speak the OpenAI chat.completions
 * dialect — one implementation, several configurations.
 */
export function createOpenAiCompatProvider(opts: OpenAiCompatOpts): LlmProvider {
  return {
    id: opts.id,
    configured: opts.configuredWhen ?? Boolean(opts.apiKey),
    model: opts.model,

    async generate(req: GenerateRequest, signal: AbortSignal): Promise<ProviderResult> {
      const model = req.model || opts.model; // per-call model override
      const messages = req.json ? ensureJsonMention(req.messages) : req.messages;
      const promptTokens = messages.reduce((n, m) => n + estimateTokens(m.content), 0);
      const maxTokens = fitMaxTokens(promptTokens, req.maxTokens, opts.requestTokenBudget ?? 0);

      const timeout = AbortSignal.timeout(opts.timeoutMs ?? 60_000);
      const res = await fetch(`${opts.baseUrl}/chat/completions`, {
        method: 'POST',
        signal: AbortSignal.any([signal, timeout]),
        headers: {
          'content-type': 'application/json',
          authorization: `Bearer ${opts.apiKey}`,
        },
        body: JSON.stringify({
          model,
          messages,
          temperature: req.temperature,
          max_tokens: maxTokens,
          ...(req.json ? { response_format: { type: 'json_object' } } : {}),
        }),
      }).catch((err: unknown) => {
        throw new ProviderCallError(opts.id, 'transient', `network error calling ${opts.id}: ${String(err)}`);
      });

      if (!res.ok) throw classifyStatus(opts.id, res.status, await res.text().catch(() => ''));

      const data = (await res.json()) as {
        choices?: Array<{ message?: { content?: string } }>;
        usage?: { prompt_tokens?: number; completion_tokens?: number };
        model?: string;
      };
      const content = data.choices?.[0]?.message?.content;
      if (typeof content !== 'string') {
        throw new ProviderCallError(opts.id, 'transient', `${opts.id} returned no content`);
      }
      return {
        content,
        model: data.model ?? model,
        usage: {
          promptTokens: data.usage?.prompt_tokens ?? 0,
          completionTokens: data.usage?.completion_tokens ?? 0,
        },
      };
    },
  };
}
