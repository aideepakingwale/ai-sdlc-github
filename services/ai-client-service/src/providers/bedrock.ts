import { AnthropicBedrock } from '@anthropic-ai/bedrock-sdk';
import type { GenerateRequest } from '@sdlc/shared';
import {
  classifyStatus, estimateTokens, isAbortError, makeUsage, ProviderCallError,
  type FailureInfo, type LlmProvider, type ProviderResult,
} from './types.js';

/**
 * AWS Bedrock provider (D-33): Claude via the Bedrock Mantle client — the
 * Messages-API Bedrock endpoint. This is the primary frontier provider for
 * the AWS cloud deployment: auth is the standard AWS credential chain (ECS
 * task role / EKS IRSA / env keys), no API key to manage, and billing goes
 * through the AWS account.
 *
 * Opt-in via BEDROCK_MODEL_ID (Bedrock model IDs carry the `anthropic.`
 * prefix, e.g. `anthropic.claude-opus-4-8`). Region from BEDROCK_REGION or
 * the ambient AWS_REGION.
 *
 * Note: current Claude models (Opus 4.7+/Sonnet 5) reject `temperature`, so
 * this provider intentionally never forwards the request's sampling params —
 * behaviour is steered by the prompt, which is how the phase agents already
 * work.
 */
/** Narrow an arbitrary image MIME to the set Claude accepts (D-66). The
 *  orchestrator downscales to JPEG before sending, so this is a safety net. */
function mediaType(mime: string): 'image/jpeg' | 'image/png' | 'image/gif' | 'image/webp' {
  const m = mime.toLowerCase();
  if (m === 'image/png') return 'image/png';
  if (m === 'image/gif') return 'image/gif';
  if (m === 'image/webp') return 'image/webp';
  return 'image/jpeg';
}

export function createBedrockProvider(opts: {
  modelId: string | undefined;
  region: string | undefined;
  timeoutMs?: number;
}): LlmProvider {
  const configured = Boolean(opts.modelId && opts.region);
  let client: AnthropicBedrock | null = null;

  return {
    id: 'bedrock',
    configured,
    model: opts.modelId ?? 'anthropic.claude-opus-4-8',
    vision: true, // Claude on Bedrock accepts image content blocks (D-66)

    async generate(req: GenerateRequest, signal: AbortSignal, onText?: (delta: string) => void): Promise<ProviderResult> {
      if (!configured) throw new ProviderCallError('bedrock', 'transient', 'bedrock not configured');
      // Standard Bedrock client: authenticates via the AWS default credential
      // chain (EC2 instance role / ECS task role / IRSA / env) and calls the
      // InvokeModel path. NB: the `Mantle` variant uses a different endpoint that
      // requires extra IAM authorization and 403s under a plain bedrock:InvokeModel
      // role (D-93) — do not switch back without updating the role.
      client ??= new AnthropicBedrock({ awsRegion: opts.region });
      const modelId = req.model || opts.modelId!; // per-call model override (D-68)

      // Bedrock rejects max_tokens above the model's output ceiling (400: "Number
      // must be less than or equal to 32768"). A high PHASE_MAX_TOKENS/PLAN_MAX_TOKENS
      // therefore 400s the whole call. Clamp to a safe ceiling so a generous budget
      // never fails the request — it is a ceiling, not a target, so output is unaffected
      // for anything short of ~130KB of text. (D-112)
      const BEDROCK_MAX_OUTPUT = 32_768;
      const maxTokens = Math.min(Math.max(1, req.maxTokens || BEDROCK_MAX_OUTPUT), BEDROCK_MAX_OUTPUT);

      // D-98 prompt caching: mark stable prefixes with `cache_control: ephemeral`
      // so Bedrock reuses them across calls (~10% read cost) — the key to making a
      // per-artifact split affordable. Cache is prefix-based; only messages the
      // caller flagged `cache` are turned into cached content blocks.
      const CACHE = { type: 'ephemeral' as const };
      const sysMsgs = req.messages.filter((m) => m.role === 'system');
      const sysText = sysMsgs.map((m) => m.content).join('\n\n');
      const sysCache = sysMsgs.some((m) => m.cache);
      const system: string | Array<Record<string, unknown>> | undefined = !sysText
        ? undefined
        : sysCache
          ? [{ type: 'text', text: sysText, cache_control: CACHE }]
          : sysText;

      // Claude content is a string for text-only turns, or an array of typed
      // blocks (text + image, D-66; + cache_control, D-98) otherwise.
      const messages = req.messages
        .filter((m) => m.role !== 'system')
        .map((m) => {
          const role = m.role as 'user' | 'assistant';
          const images = m.images ?? [];
          if (images.length === 0 && !m.cache) return { role, content: m.content };
          const blocks: Array<Record<string, unknown>> = [];
          if (m.content) blocks.push({ type: 'text', text: m.content });
          for (const img of images) {
            blocks.push({
              type: 'image',
              source: { type: 'base64', media_type: mediaType(img.mimeType), data: img.dataBase64 },
            });
          }
          if (blocks.length === 0) blocks.push({ type: 'text', text: '(no user input)' });
          if (m.cache) blocks[blocks.length - 1]!.cache_control = CACHE;  // cache up to this block
          return { role, content: blocks };
        });
      if (messages.length === 0) messages.push({ role: 'user', content: '(no user input)' });

      // D-96: large enterprise artifacts can take minutes to generate. Stream the
      // response so tokens flow continuously (no idle-timeout resets, no aborts
      // mid-generation) and allow a generous overall ceiling. `.finalMessage()`
      // still returns the complete assembled Message.
      const timeout = AbortSignal.timeout(opts.timeoutMs ?? 600_000);
      const combined = AbortSignal.any([signal, timeout]);

      // Token governance: a call that fails after Bedrock started generating is still
      // billed, so keep what the stream knows. `currentMessage` is the SDK's running
      // snapshot (input + cache usage arrive with message_start; the final output
      // count only at the end, so a mid-stream failure estimates the output from the
      // characters received and says so via `estimated`).
      const cl = client;
      const open = () => cl.messages.stream(
        {
          model: modelId,
          max_tokens: maxTokens,
          // system/messages carry optional cache_control blocks (D-98) that this
          // SDK version's param types don't model; cast at this adapter boundary.
          ...(system ? { system: system as never } : {}),
          messages: messages as never,
        },
        { signal: combined },
      );
      let stream: ReturnType<typeof open> | undefined;
      let streamedChars = 0;
      const requestChars = JSON.stringify(system ?? '').length + JSON.stringify(messages).length;
      const idOf = (fallback?: string): string | undefined => {
        const h = stream?.response?.headers?.get?.('x-amzn-requestid');
        return h || stream?.request_id || fallback || undefined;
      };
      const failure = (): FailureInfo => {
        const snap = stream?.currentMessage;
        const u = snap?.usage;
        const base = { providerRequestId: idOf(snap?.id), stopReason: snap?.stop_reason ?? undefined, region: opts.region };
        const hasInput = Boolean(u && (u.input_tokens > 0 || (u.cache_read_input_tokens ?? 0) > 0 || (u.cache_creation_input_tokens ?? 0) > 0));
        if (u && hasInput) {
          const est = Math.ceil(streamedChars / 4);
          const out = Math.max(u.output_tokens ?? 0, est);
          return {
            ...base,
            usage: makeUsage(u.input_tokens, out, {
              cacheRead: u.cache_read_input_tokens ?? 0,
              cacheWrite: u.cache_creation_input_tokens ?? 0,
              estimated: out !== (u.output_tokens ?? 0),
            }),
          };
        }
        if (streamedChars > 0) {
          return { ...base, usage: makeUsage(estimateTokens('x'.repeat(requestChars)), Math.ceil(streamedChars / 4), { estimated: true }) };
        }
        return base; // never started generating (throttled, auth, bad request): nothing billed
      };

      try {
        stream = open();
        // D-112 live streaming: forward each text delta to the caller as it arrives.
        stream.on('text', (delta: string) => {
          streamedChars += delta.length;
          if (onText) { try { onText(delta); } catch { /* ignore */ } }
        });
        const res = await stream.finalMessage();
        const content = res.content
          .filter((b): b is Extract<(typeof res.content)[number], { type: 'text' }> => b.type === 'text')
          .map((b) => b.text)
          .join('');
        // `input_tokens` is only the UNCACHED part when prompt caching is used; cache
        // reads and writes are reported separately and must be added back (F1).
        const usage = makeUsage(res.usage?.input_tokens ?? 0, res.usage?.output_tokens ?? 0, {
          cacheRead: res.usage?.cache_read_input_tokens ?? 0,
          cacheWrite: res.usage?.cache_creation_input_tokens ?? 0,
        });
        const providerRequestId = idOf(res.id);
        if (!content || res.stop_reason === 'refusal') {
          // Billed even though unusable: carry the real usage on the error.
          throw new ProviderCallError('bedrock', 'transient', `bedrock returned no text (stop: ${res.stop_reason})`, undefined, {
            usage, providerRequestId, stopReason: res.stop_reason ?? undefined, region: opts.region,
          });
        }
        return {
          content,
          model: res.model ?? modelId,
          usage,
          providerRequestId,
          stopReason: res.stop_reason ?? undefined,
          region: opts.region,
          // D-103: the model hit the output-token cap — content is incomplete.
          truncated: res.stop_reason === 'max_tokens',
        };
      } catch (err) {
        if (err instanceof ProviderCallError) throw err;
        const info = failure();
        // D-103: an abort (caller disconnect or our own timeout) is not a provider
        // fault — raise `canceled` so the router doesn't trip the breaker.
        if (isAbortError(err, combined)) {
          throw new ProviderCallError('bedrock', 'canceled', `bedrock call canceled: ${(err as Error).message ?? String(err)}`, undefined, info);
        }
        // The Anthropic SDK throws typed APIError subclasses carrying `status`;
        // duck-type so we don't depend on instanceof across package instances.
        const status = (err as { status?: unknown }).status;
        if (typeof status === 'number') {
          const e = classifyStatus('bedrock', status, (err as Error).message ?? '');
          throw new ProviderCallError(e.provider, e.kind, e.message, e.status, info);
        }
        throw new ProviderCallError('bedrock', 'transient', `bedrock call failed: ${String(err)}`, undefined, info);
      }
    },
  };
}
