import { createHash } from 'node:crypto';
import { estimateTokens, type GenerateRequest } from '@sdlc/shared';
import * as corpus from './mock-corpus.js';
import type { LlmProvider, ProviderResult } from './types.js';

/**
 * Deterministic mock LLM (D-06). Orchestrator prompts embed a directive line
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
    vision: true, // handles image requests deterministically so offline vision never dead-ends (D-66)

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
      // Data-driven custom phase (D-77): a generic, schema-valid deliverable set.
      return JSON.stringify({
        deliverables: [{
          output: 'DELIVERABLE',
          content: `# ${topic}\n\nThis deliverable was produced by a custom phase (mock provider, ref ${seed}). `
            + 'It is structurally identical to production output but deterministic offline.\n\n'
            + '## Summary\nKey findings and recommendations for the stage.\n',
        }],
        toolCalls: [],
      });

    case 'clarify':
      // D-108/D-112: structured clarifying questions WITH predefined, selectable
      // options (the UI adds its own 'Other'). Deterministic offline sample so the
      // options UI is exercisable without a real model; real providers tailor these.
      return JSON.stringify({
        needs_clarification: true,
        questions: [
          {
            id: 'target-cloud', header: 'Cloud', multiSelect: false,
            question: `Which target cloud/platform should the ${topic} solution assume?`,
            rationale: 'Materially changes architecture, cost and security.',
            options: [
              { label: 'AWS', description: 'Deploy on Amazon Web Services' },
              { label: 'Azure', description: 'Deploy on Microsoft Azure' },
              { label: 'GCP', description: 'Deploy on Google Cloud Platform' },
              { label: 'On-prem / Kubernetes', description: 'Self-hosted or private cluster' },
            ],
          },
          {
            id: 'compliance', header: 'Compliance', multiSelect: true,
            question: 'Which compliance regimes apply?',
            rationale: 'Drives data handling, retention and audit controls.',
            options: [
              { label: 'GDPR', description: 'EU personal-data protection' },
              { label: 'PCI-DSS', description: 'Payment card data' },
              { label: 'HIPAA', description: 'Health information' },
              { label: 'None', description: 'No specific regime applies' },
            ],
          },
        ],
      });

    case 'text_diagram':
      // A text drawing redrawn as standard Mermaid source (plain text, no fences).
      return 'flowchart LR\n  A[Source system] --> B[Integration layer] --> C[Target system]';

    case 'custom_format':
      // D-112: a single document mirroring a user-attached format. PLAIN markdown
      // (text mode, not JSON) so the pipeline persists ONE document and no backlog;
      // real providers reproduce the attached structure.
      return `# ${topic} — Requirement Specification\n\n`
        + `_Produced following the requester's attached format (mock provider, ref ${seed})._\n\n`
        + '## 1. Document Reference\n| Document | Version |\n|---|---|\n| This spec | v0.1 |\n\n'
        + '## 2. Background\nContext for the requirement.\n\n'
        + '## 3. Application Details\nSource and target systems.\n\n'
        + '## 4. Non-Functional Requirements\nAvailability, performance, security.\n\n'
        + '## 5. Change History\n| Version | Date | Author | Description |\n|---|---|---|---|\n';

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

    case 'code_structure': {
      // Step 1 of code generation: a small, valid repository plan (no code yet).
      const slug = topic.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'service';
      const f = (path: string, purpose: string, kind = 'source', layer = '') => ({ path, purpose, kind, layer, covers: [] });
      return JSON.stringify({
        summary: `Layered service for ${topic}: HTTP API, domain services, persistence adapters and tests.`,
        conventions: ['Modules and files use snake_case; classes PascalCase.', 'One public class or concern per module.', 'Tests mirror the source tree under tests/ and are named test_<module>.py.'],
        directories: [{ path: 'src', purpose: 'Application source' }, { path: 'src/app/api', purpose: 'HTTP routes' }, { path: 'src/app/domain', purpose: 'Business rules' }, { path: 'tests', purpose: 'Automated tests' }],
        files: [
          f('README.md', 'How to build, run and test the service', 'docs'),
          f('src/app/__init__.py', 'Package marker', 'source', 'app'),
          f('src/app/main.py', 'Application entry point and wiring', 'source', 'api'),
          f('src/app/api/routes.py', 'HTTP endpoints for the integration', 'source', 'api'),
          f('src/app/domain/service.py', 'Core integration rules (idempotency, retries)', 'source', 'domain'),
          f('tests/test_service.py', 'Unit tests for the integration rules', 'test', 'domain'),
        ],
        branch: `feature/${slug}`, commitMessage: `feat: initial ${topic} implementation`,
        prTitle: `Implement ${topic}`, prBody: `Implements ${topic} per the approved design.`, checklist: ['Tests pass', 'Lint clean'],
      });
    }

    case 'code_batch': {
      // Step 2: contents for exactly the files named in the prompt's `<!-- files: a | b -->` marker.
      const m = /<!--\s*files:\s*([^>]*?)\s*-->/.exec(userText);
      const paths = (m?.[1] ?? '').split('|').map((x) => x.trim()).filter(Boolean);
      const body = (path: string): string => {
        if (path.endsWith('.md')) return `# ${topic}\n\nGenerated for the approved structure (mock).\n`;
        if (path.endsWith('.py')) return `"""${path} - generated for ${topic} (mock provider)."""\n\n\ndef handle(payload: dict) -> dict:\n    return {"ok": True, "echo": payload}\n`;
        return `# ${path}\n`;
      };
      return JSON.stringify({ files: paths.map((path) => ({ path, content: body(path) })), designNotes: 'Mock implementation of the agreed files.' });
    }

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
      // Validation agent (D-52): the deterministic corpus is well-formed, so the
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
