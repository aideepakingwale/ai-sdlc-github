import type { Logger } from 'pino';
import {
  SdlcError,
  type GenerateRequest,
  type GenerateResponse,
  type LlmIntent,
  type LlmProviderId,
  type ProviderHealth,
} from '@sdlc/shared';
import type { CircuitBreaker } from './breaker.js';
import { ProviderCallError, type LlmProvider } from './providers/types.js';

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/**
 * Intent-based provider chains (Module 3 §2):
 *   recommendation | architecture → Groq, Grok, Gemini
 *   generation | standard         → Groq, Gemini, Grok
 * When AWS Bedrock is configured (cloud deployment) it LEADS every
 * frontier chain — Claude on Bedrock is the primary AI service platform and
 * the key-based providers become the fallback tier.
 * The mock provider is appended as a last resort when `allowMockFallback`
 * (non-production) so local runs never dead-end.
 */
export function chainFor(intent: LlmIntent): LlmProviderId[] {
  return intent === 'recommendation' || intent === 'architecture'
    ? ['bedrock', 'groq', 'grok', 'gemini']
    : ['bedrock', 'groq', 'gemini', 'grok'];
}

export class LlmRouter {
  private readonly providers: Map<LlmProviderId, LlmProvider>;

  constructor(
    providers: LlmProvider[],
    private readonly breaker: CircuitBreaker,
    private readonly log: Logger,
    private readonly opts: { forceMock: boolean; allowMockFallback: boolean },
  ) {
    this.providers = new Map(providers.map((p) => [p.id, p]));
  }

  async health(): Promise<ProviderHealth[]> {
    const out: ProviderHealth[] = [];
    for (const p of this.providers.values()) {
      out.push({
        provider: p.id,
        configured: p.configured,
        breaker: p.id === 'mock' ? 'closed' : await this.breaker.state(p.id),
        retryInSeconds: p.id === 'mock' ? null : await this.breaker.retryInSeconds(p.id),
        model: p.model,
        vision: p.vision === true,
      });
    }
    return out;
  }

  /**
   * Resolve a pinned model to a concrete provider + bare model id.
   * Accepts `"<provider>/<modelId>"`, or a bare modelId matching a configured
   * provider's default. Returns null when no pin is requested.
   */
  private resolvePinned(req: GenerateRequest): { providerId: LlmProviderId; modelId: string } | null {
    if (!req.model) return null;
    const raw = req.model.trim();
    const slash = raw.indexOf('/');
    if (slash > 0) {
      const prefix = raw.slice(0, slash) as LlmProviderId;
      if (this.providers.has(prefix)) return { providerId: prefix, modelId: raw.slice(slash + 1) };
    }
    // No provider prefix: match a configured provider whose default model is this id.
    const match = [...this.providers.values()].find((p) => p.id !== 'mock' && p.configured && p.model === raw);
    return match ? { providerId: match.id, modelId: raw } : null;
  }

  /**
   * Provider candidates for a request. Tier selects the pool:
   *   - local:    the local lightweight model, then mock fallback
   *   - frontier: the intent-based frontier chain
   *   - auto:     legacy intent-based chain
   *
   * A configured LOCAL model (Ollama/vLLM) is a genuine LLM, so it serves as
   * the last real fallback for EVERY tier before the deterministic mock —
   * this lets the whole pipeline run on a real model offline with no API keys
   *. The mock is only reached when nothing real is configured (or
   * `allowMockFallback` in non-production).
   */
  private candidates(req: GenerateRequest): LlmProvider[] {
    const local = this.providers.get('local');
    const localConfigured = Boolean(local?.configured);

    if (this.opts.forceMock) {
      const mock = this.providers.get('mock');
      return mock ? [mock] : [];
    }

    // Pinned model: an authorised user chose a specific model — try only
    // that provider, then the mock fallback (non-prod) so the run never dead-ends.
    const pin = this.resolvePinned(req);
    if (pin) {
      const p = this.providers.get(pin.providerId);
      const pinned = p && p.configured ? [p] : [];
      if (this.opts.allowMockFallback || pinned.length === 0) {
        const mock = this.providers.get('mock');
        if (mock) pinned.push(mock);
      }
      const needsVisionPin = req.messages.some((m) => (m.images?.length ?? 0) > 0);
      return needsVisionPin ? pinned.filter((x) => x.vision === true) : pinned;
    }

    let chain: LlmProvider[];
    if (req.tier === 'local') {
      chain = localConfigured ? [local as LlmProvider] : [];
    } else {
      chain = chainFor(req.intent)
        .map((id) => this.providers.get(id))
        .filter((p): p is LlmProvider => Boolean(p?.configured));
      // Real local model backs the frontier/auto chain before the mock.
      if (localConfigured && !chain.includes(local as LlmProvider)) {
        chain.push(local as LlmProvider);
      }
    }

    if (this.opts.allowMockFallback || chain.length === 0) {
      const mock = this.providers.get('mock');
      if (mock) chain.push(mock);
    }

    // Vision: a request carrying inline images can only be served by a
    // vision-capable provider. Filter the chain down to those — the order is
    // preserved, so it stays a genuine multi-model failover (e.g. Bedrock →
    // Gemini) rather than pinning a single model. The mock is vision-capable so
    // offline runs still resolve.
    const needsVision = req.messages.some((m) => (m.images?.length ?? 0) > 0);
    if (needsVision) return chain.filter((p) => p.vision === true);

    return chain;
  }

  async generate(req: GenerateRequest, signal: AbortSignal): Promise<GenerateResponse> {
    const attempts: string[] = [];
    const candidates = this.candidates(req);
    if (candidates.length === 0) {
      throw new SdlcError('PROVIDER_EXHAUSTED', 'No LLM providers configured');
    }
    // When a model is pinned, hand each provider the BARE model id (prefix
    // stripped) so it calls the exact model the user chose.
    const pin = this.resolvePinned(req);
    const callReq: GenerateRequest = pin ? { ...req, model: pin.modelId } : req;

    for (const provider of candidates) {
      if (provider.id !== 'mock') {
        const state = await this.breaker.state(provider.id);
        if (state !== 'closed') {
          attempts.push(`${provider.id}:skipped(${state})`);
          continue;
        }
      }

      try {
        const result = await this.callWithRetry(provider, callReq, signal);
        return {
          provider: provider.id,
          model: result.model,
          content: result.content,
          usage: result.usage,
          attempts,
          tier: req.tier,
        };
      } catch (err) {
        if (!(err instanceof ProviderCallError)) throw err;
        attempts.push(`${provider.id}:${err.kind}`);
        if (err.kind === 'auth_or_billing') {
          this.log.warn({ provider: provider.id, status: err.status }, 'provider disabled (auth/billing)');
          await this.breaker.disable(provider.id);
        } else {
          this.log.warn({ provider: provider.id, kind: err.kind }, 'provider tripped open');
          await this.breaker.trip(provider.id);
        }
      }
    }

    throw new SdlcError('PROVIDER_EXHAUSTED', 'All LLM providers failed or are disabled', {
      details: { attempts },
    });
  }

  /** 429 → exponential backoff up to 3 retries; transient → 1 retry (Module 3 §2). */
  private async callWithRetry(provider: LlmProvider, req: GenerateRequest, signal: AbortSignal) {
    let lastErr: ProviderCallError | undefined;
    for (let attempt = 0; attempt < 4; attempt++) {
      try {
        return await provider.generate(req, signal);
      } catch (err) {
        if (!(err instanceof ProviderCallError)) throw err;
        lastErr = err;
        if (err.kind === 'auth_or_billing') throw err;
        if (err.kind === 'transient' && attempt >= 1) throw err;
        if (err.kind === 'rate_limit' && attempt >= 3) throw err;
        const backoff = Math.min(250 * 2 ** attempt, 2_000) + Math.floor(Math.random() * 100);
        await sleep(backoff);
      }
    }
    throw lastErr ?? new ProviderCallError(provider.id, 'transient', 'retry loop exhausted');
  }
}
