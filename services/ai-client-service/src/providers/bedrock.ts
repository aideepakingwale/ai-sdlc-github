import { AnthropicBedrockMantle } from '@anthropic-ai/bedrock-sdk';
import type { GenerateRequest } from '@sdlc/shared';
import { classifyStatus, ProviderCallError, type LlmProvider, type ProviderResult } from './types.js';

/**
 * AWS Bedrock provider: Claude via the Bedrock Mantle client — the
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
/** Narrow an arbitrary image MIME to the set Claude accepts. The
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
  let client: AnthropicBedrockMantle | null = null;

  return {
    id: 'bedrock',
    configured,
    model: opts.modelId ?? 'anthropic.claude-opus-4-8',
    vision: true, // Claude on Bedrock accepts image content blocks

    async generate(req: GenerateRequest, signal: AbortSignal): Promise<ProviderResult> {
      if (!configured) throw new ProviderCallError('bedrock', 'transient', 'bedrock not configured');
      client ??= new AnthropicBedrockMantle({ awsRegion: opts.region });
      const modelId = req.model || opts.modelId!; // per-call model override

      const system = req.messages
        .filter((m) => m.role === 'system')
        .map((m) => m.content)
        .join('\n\n');
      // Claude content is a string for text-only turns, or an array of typed
      // blocks (text + image) when the message carries inline images.
      const messages = req.messages
        .filter((m) => m.role !== 'system')
        .map((m) => {
          const role = m.role as 'user' | 'assistant';
          const images = m.images ?? [];
          if (images.length === 0) return { role, content: m.content };
          const blocks = [
            ...(m.content ? [{ type: 'text' as const, text: m.content }] : []),
            ...images.map((img) => ({
              type: 'image' as const,
              source: { type: 'base64' as const, media_type: mediaType(img.mimeType), data: img.dataBase64 },
            })),
          ];
          return { role, content: blocks };
        });
      if (messages.length === 0) messages.push({ role: 'user', content: '(no user input)' });

      const timeout = AbortSignal.timeout(opts.timeoutMs ?? 120_000);
      try {
        const res = await client.messages.create(
          {
            model: modelId,
            max_tokens: req.maxTokens,
            ...(system ? { system } : {}),
            messages,
          },
          { signal: AbortSignal.any([signal, timeout]) },
        );
        const content = res.content
          .filter((b): b is Extract<(typeof res.content)[number], { type: 'text' }> => b.type === 'text')
          .map((b) => b.text)
          .join('');
        if (!content || res.stop_reason === 'refusal') {
          throw new ProviderCallError('bedrock', 'transient', `bedrock returned no text (stop: ${res.stop_reason})`);
        }
        return {
          content,
          model: res.model ?? modelId,
          usage: {
            promptTokens: res.usage?.input_tokens ?? 0,
            completionTokens: res.usage?.output_tokens ?? 0,
          },
        };
      } catch (err) {
        if (err instanceof ProviderCallError) throw err;
        // The Anthropic SDK throws typed APIError subclasses carrying `status`;
        // duck-type so we don't depend on instanceof across package instances.
        const status = (err as { status?: unknown }).status;
        if (typeof status === 'number') {
          throw classifyStatus('bedrock', status, (err as Error).message ?? '');
        }
        throw new ProviderCallError('bedrock', 'transient', `bedrock call failed: ${String(err)}`);
      }
    },
  };
}
