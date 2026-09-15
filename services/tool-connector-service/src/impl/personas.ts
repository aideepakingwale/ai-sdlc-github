import type { ToolsEnv } from '@sdlc/shared';
import { callLlm, parseJsonLoose } from '../llm-client.js';

/**
 * "Amazon Q" / "GitHub Copilot" tools implemented as LLM personas over the
 * router — the spec's tool names are preserved, the backing model is
 * whatever the ai-client chain provides (mock in offline dev).
 */
export function personaImpl(env: ToolsEnv) {
  return {
    async generateCloudcraft(input: { hldNarrative: string }): Promise<{ cloudcraftJson: string }> {
      const content = await callLlm(env.AI_CLIENT_URL, {
        intent: 'architecture',
        tag: 'amazonq_generate_cloudcraft',
        json: true,
        temperature: 0.1,
        maxTokens: 4096,
        messages: [
          {
            role: 'system',
            content:
              'You are Amazon Q, an AWS architecture assistant. #mock:cloudcraft\n' +
              'Given an HLD narrative, produce Cloudcraft-compatible topology JSON.\n' +
              'Respond with strict JSON: {"cloudcraftJson": "<stringified topology JSON>"}',
          },
          { role: 'user', content: input.hldNarrative },
        ],
      });
      const parsed = parseJsonLoose<{ cloudcraftJson: string }>(content);
      return { cloudcraftJson: parsed.cloudcraftJson ?? JSON.stringify(parsed) };
    },

    async analyseFailure(input: { rawLogText: string; failedStep: string }) {
      const content = await callLlm(env.AI_CLIENT_URL, {
        intent: 'standard',
        tag: 'amazonq_analyse_failure',
        json: true,
        temperature: 0,
        maxTokens: 2048,
        messages: [
          {
            role: 'system',
            content:
              'You are Amazon Q analysing a CI failure. #mock:failure_analysis\n' +
              'Classify the root cause. Respond with strict JSON:\n' +
              '{"rootCauseClass":"type-error|test-failure|snyk-cve|snyk-iac|inspector-image|unknown",' +
              '"rootCause":"<one sentence>","affectedFiles":["path", ...]}',
          },
          { role: 'user', content: `Failed step: ${input.failedStep}\n\nLogs:\n${input.rawLogText.slice(0, 12_000)}` },
        ],
      });
      return parseJsonLoose<{ rootCauseClass: string; rootCause: string; affectedFiles?: string[] }>(content);
    },

    async generateFix(input: {
      rootCauseClass: string;
      rootCause: string;
      affectedFiles: string[];
      currentFiles: Array<{ path: string; content: string }>;
    }) {
      const filesBlock = input.currentFiles
        .map((f) => `--- ${f.path} ---\n${f.content}`)
        .join('\n\n')
        .slice(0, 24_000);
      const content = await callLlm(env.AI_CLIENT_URL, {
        intent: 'generation',
        tag: 'ghcopilot_generate_fix',
        json: true,
        temperature: 0,
        maxTokens: 8192,
        messages: [
          {
            role: 'system',
            content:
              'You are GitHub Copilot producing a minimal targeted fix. #mock:fix\n' +
              'Return complete corrected file contents (not diffs). Respond with strict JSON:\n' +
              '{"files":[{"path":"...","content":"..."}],"message":"fix(ai): ..."}',
          },
          {
            role: 'user',
            content: `Root cause class: ${input.rootCauseClass}\nRoot cause: ${input.rootCause}\nAffected: ${input.affectedFiles.join(', ')}\n\nCurrent files:\n${filesBlock}`,
          },
        ],
      });
      return parseJsonLoose<{ files: Array<{ path: string; content: string }>; message: string }>(content);
    },
  };
}
