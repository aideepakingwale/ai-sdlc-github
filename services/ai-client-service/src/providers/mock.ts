import { createHash } from 'node:crypto';
import { estimateTokens, type GenerateRequest } from '@sdlc/shared';
import * as corpus from './mock-corpus.js';
import type { LlmProvider, ProviderResult } from './types.js';

/**
 * Deterministic mock LLM. Orchestrator prompts embed a directive line
 * `#mock:<kind>` (real providers treat it as prompt noise); the mock switches
 * on it to return schema-valid JSON for every pipeline node, so all six phases,
 * gates and the build-recovery loop are exercisable offline. Same input ⇒ same
 * output (content hash used for ids), which also makes tests reproducible.
 */
export function createMockProvider(): LlmProvider {
  return {
    id: 'mock',
    configured: true,
    model: 'mock-sdlc-1',
    vision: true, // handles image requests deterministically so offline vision never dead-ends

    async generate(req: GenerateRequest): Promise<ProviderResult> {
      const all = req.messages.map((m) => m.content).join('\n');
      // Vision request with no real provider configured: return a deterministic
      // placeholder. The orchestrator sees provider==='mock' and falls back to
      // OCR, so a mock run never masquerades as a real image understanding.
      const imageCount = req.messages.reduce((n, m) => n + (m.images?.length ?? 0), 0);
      if (imageCount > 0) {
        const content = `[mock-vision] received ${imageCount} image(s); no vision provider configured — deterministic placeholder.`;
        return { content, model: 'mock-sdlc-1', usage: { promptTokens: estimateTokens(all), completionTokens: estimateTokens(content) } };
      }
      const kind = /#mock:([a-z0-9_]+)/.exec(all)?.[1] ?? 'chat';
      const userText = [...req.messages].reverse().find((m) => m.role === 'user')?.content ?? '';
      const topic = extractTopic(userText, all);
      const seed = createHash('sha256').update(all).digest('hex').slice(0, 8);

      const content = render(kind, topic, seed, userText);
      return {
        content,
        model: 'mock-sdlc-1',
        usage: {
          promptTokens: estimateTokens(all),
          completionTokens: estimateTokens(content),
        },
      };
    },
  };
}

/**
 * Subject of the work. Later stages are driven by short continuations ("proceed"),
 * so when the user turn carries no subject we recover it from the approved
 * context already in the prompt — keeping every artifact named consistently
 * across the whole pipeline.
 */
function extractTopic(userText: string, fullPrompt = ''): string {
  const clean = (s: string) => s.replace(/[^\w\s.,-]/g, '').trim().slice(0, 80);
  const CONTINUATIONS = /^(proceed|continue|go ahead|next|ok|yes|regenerate\b.*)$/i;

  const fromUser = userText
    .split('\n')
    .map((l) => l.trim())
    .find((l) => l.length > 8 && !l.startsWith('#') && !l.startsWith('{') && !CONTINUATIONS.test(l));
  if (fromUser) return clean(fromUser);

  // Recover from prior-phase context titles, e.g. "SDLC-12: Deliver <topic>"
  // or the PRD/HLD headings carried in the approved-context block.
  for (const rx of [
    /Deliver ([^\n|]{6,70})/,
    /Product Requirements Document\s*[—-]\s*([^\n|]{3,70})/,
    /High-Level Design\s*[—-]\s*([^\n|]{3,70})/,
    /###\s*\[Phase \d\]\s*\w+:\s*([^\n|]{6,70})/,
  ]) {
    const hit = rx.exec(fullPrompt)?.[1];
    if (hit) return clean(hit);
  }
  return 'Platform Feature';
}

/**
 * The path CI reported as failing. The simulated pipeline embeds it in the log
 * (`path(line,col): error TS2322`) and the analyse/fix personas resolve it from
 * there, so a fix always lands on the file that actually carries the defect.
 */
function failingFile(text: string): string {
  // The path reaches the personas in three shapes: the CI log
  // (`path(line,col): error`), the analyser's rootCause prose, and the
  // affectedFiles array. Match any source path in the text.
  return (
    /([\w./-]+\.[a-z]{2,4})\(\d+,\d+\):\s*error/.exec(text)?.[1] ??
    /\b((?:src|app|lib)\/[\w./-]+\.[a-z]{2,4})\b/.exec(text)?.[1] ??
    'src/items/service.ts'
  );
}

function render(kind: string, topic: string, seed: string, userText: string): string {
  switch (kind) {
    case 'plan':
      return JSON.stringify({
        steps: [
          { id: 'generate', tool: 'llm', description: `Generate phase artifacts for: ${topic}`, args: {} },
          { id: 'publish', tool: 'auto', description: 'Publish artifacts to enterprise tools', args: {} },
        ],
      });

    case 'custom':
      // Data-driven custom phase: a generic, schema-valid deliverable set.
      return JSON.stringify({
        deliverables: [{
          output: 'DELIVERABLE',
          content: `# ${topic}\n\nThis deliverable was produced by a custom phase (mock provider, ref ${seed}). `
            + 'It is structurally identical to production output but deterministic offline.\n\n'
            + '## Summary\nKey findings and recommendations for the stage.\n',
        }],
        toolCalls: [],
      });

    case 'phase1':
      return corpus.phase1(topic, seed);

    case 'phase2':
      return corpus.phase2(topic, seed);

    case 'phase3':
      return corpus.phase3(topic, seed);

    case 'phase4':
      return corpus.phase4(topic, seed);

    case 'phase5':
      return corpus.phase5(topic, seed);

    case 'phase6':
      return corpus.phase6(topic, seed);
    case 'cloudcraft':
      return JSON.stringify({
        cloudcraftJson: JSON.stringify({
          grid: 'standard',
          nodes: [
            { type: 'alb', name: 'public-alb' },
            { type: 'ecs', name: 'api-service' },
            { type: 'rds', name: 'aurora-pg' },
            { type: 'elasticache', name: 'redis' },
            { type: 's3', name: 'artifacts' },
          ],
          edges: [
            ['public-alb', 'api-service'],
            ['api-service', 'aurora-pg'],
            ['api-service', 'redis'],
            ['api-service', 's3'],
          ],
        }),
      });

    case 'failure_analysis': {
      const isTypeError = userText.includes('TS2322') || userText.includes('BUG-MARKER') || userText.includes('type-error');
      return JSON.stringify(
        isTypeError
          ? {
              rootCauseClass: 'type-error',
              // Resolve the offending file from the CI log rather than assuming
              // a fixed path, so the recovery loop converges on real output.
              rootCause: `TS2322: invalid type assignment in ${failingFile(userText)}`,
              affectedFiles: [failingFile(userText)],
            }
          : {
              rootCauseClass: 'test-failure',
              rootCause: 'Unit test assertion failed',
              affectedFiles: ['src/items/service.test.ts'],
            },
      );
    }

    case 'fix': {
      const target = failingFile(userText);
      return JSON.stringify({
        files: [{ path: target, content: corpus.repairedFile(target) }],
        message: 'fix(ai): remove invalid type assignment flagged by CI',
      });
    }

    case 'fix_legacy':
      return JSON.stringify({
        files: [
          {
            path: failingFile(userText),
            content: `export interface Item { id: string; name: string; status: 'ACTIVE' | 'ARCHIVED' }\n\nexport function createItem(name: string): Item {\n  if (!name.trim()) throw new Error('name required');\n  return { id: crypto.randomUUID(), name, status: 'ACTIVE' };\n}\n`,
          },
        ],
        message: 'fix(ai): remove invalid type assignment flagged by CI',
      });

    case 'fact_check':
      return JSON.stringify({ ok: true, issues: [] });

    case 'validate':
      // Validation agent: the deterministic corpus is well-formed, so the
      // offline verdict always passes; real providers do the real judgement.
      return JSON.stringify({ ok: true, issues: [], reworkInstructions: '' });

    case 'compress':
      return `Summary (${seed}): ${topic} — key decisions and requirements retained; older artifact bodies elided.`;

    case 'intent': {
      const lower = userText.toLowerCase();
      const intent =
        /recommend|should we|which (option|approach)/.test(lower)
          ? 'recommendation'
          : /architect|design|hld|topology/.test(lower)
            ? 'architecture'
            : 'generation';
      return JSON.stringify({ intent });
    }

    default:
      return `Understood. Proceeding with **${topic}**.\n\nThis is the mock LLM provider (no API keys configured) — outputs are deterministic but structurally identical to production. Ref ${seed}.`;
  }
}
