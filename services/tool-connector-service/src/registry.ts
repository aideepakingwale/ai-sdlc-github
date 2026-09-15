import type { Redis } from 'ioredis';
import type { Logger } from 'pino';
import { getTool, SdlcError, toSdlcError, type ToolsEnv } from '@sdlc/shared';
import { ToolCache } from './cache.js';
import { atlassianImpl } from './impl/atlassian.js';
import { githubImpl } from './impl/github.js';
import { lintOpenapi } from './impl/openapi-lint.js';
import { personaImpl } from './impl/personas.js';
import { sdlcToolchainImpl } from './impl/sdlc-toolchain.js';

export interface ToolRuntime {
  execute(name: string, args: unknown): Promise<unknown>;
  modes: { jira: 'live' | 'mock'; confluence: 'live' | 'mock'; github: 'live' | 'mock'; aws: 'live' | 'mock' };
}

/**
 * Wires the shared TOOL_REGISTRY names to implementations, with per-connector
 * live/mock selection (TOOLS_MODE=auto|mock|live), zod input/output
 * validation on both edges, and a 24h cache for cacheable deterministic tools.
 */
export function createToolRuntime(env: ToolsEnv, redis: Redis, log: Logger): ToolRuntime {
  const jiraCreds = Boolean(env.JIRA_BASE_URL && env.JIRA_EMAIL && env.JIRA_API_TOKEN);
  const confluenceCreds = Boolean(env.CONFLUENCE_BASE_URL && env.JIRA_EMAIL && env.JIRA_API_TOKEN);
  const githubCreds = Boolean(env.GITHUB_TOKEN && env.GITHUB_REPO);

  const decide = (creds: boolean, connector: string): 'live' | 'mock' => {
    if (env.TOOLS_MODE === 'mock') return 'mock';
    if (env.TOOLS_MODE === 'live') {
      if (!creds) throw new SdlcError('INTERNAL', `TOOLS_MODE=live but ${connector} credentials are missing`);
      return 'live';
    }
    return creds ? 'live' : 'mock';
  };

  const modes = {
    jira: decide(jiraCreds, 'jira'),
    confluence: decide(confluenceCreds, 'confluence'),
    github: decide(githubCreds, 'github'),
    aws: decide(Boolean(env.TOOLS_AWS_S3_BUCKET), 'aws'),
  } as const;

  const atlassian = atlassianImpl({ env, redis, live: modes.jira === 'live' });
  const confluence = atlassianImpl({ env, redis, live: modes.confluence === 'live' });
  const github = githubImpl({ env, redis, live: modes.github === 'live' });
  const personas = personaImpl(env);
  const toolchain = sdlcToolchainImpl({ env, redis, awsLive: modes.aws === 'live' });
  const cache = new ToolCache(redis, env.TOOL_CACHE_TTL_SECONDS);

  // Handlers receive schema-validated input and must return schema-valid output.
  const handlers: Record<string, (input: never) => Promise<unknown>> = {
    jira_create_epic: (i) => atlassian.createEpic(i),
    jira_create_story: (i) => atlassian.createStory(i),
    jira_create_xray_test: (i) => atlassian.createXrayTest(i),
    confluence_publish_prd: async (i: { title: string; content: string; jiraLinks: string[] }) =>
      confluence.publishPage({
        title: i.title,
        body: `${i.content}\n\nLinked Jira issues: ${i.jiraLinks.join(', ') || 'none'}`,
        kind: 'prd',
      }),
    confluence_publish_hld: async (i: { title: string; hldContent: string; structurizrDsl?: string; adrLinks: string[] }) =>
      confluence.publishPage({
        title: i.title,
        body: `${i.hldContent}\n\n## Structurizr DSL\n${i.structurizrDsl ?? '(committed to git)'}\n\nADRs: ${i.adrLinks.join(', ') || 'none'}`,
        kind: 'hld',
      }),
    confluence_publish_lld: async (i: {
      serviceName: string;
      lldContent: string;
      plantumlSources: string[];
      openapiYaml?: string;
      erdLink?: string;
    }) =>
      confluence.publishPage({
        title: `LLD — ${i.serviceName}`,
        body: `${i.lldContent}\n\n## Diagrams\n${i.plantumlSources.join('\n\n')}\n\n## OpenAPI\n${i.openapiYaml ?? ''}\n\nERD: ${i.erdLink ?? 'n/a'}`,
        kind: 'lld',
      }),
    github_commit_diagrams: (i) => github.commitFiles(i),
    github_commit_lld_artefacts: (i) => github.commitFiles(i),
    github_commit_pipeline_config: (i) => github.commitFiles(i),
    github_create_branch: (i) => github.createBranch(i),
    github_commit_code: (i) => github.commitCode(i),
    github_commit_fix: (i) => github.commitCode(i),
    github_fetch_build_logs: (i) => github.fetchBuildLogs(i),
    github_poll_run_status: (i) => github.pollRunStatus(i),
    github_create_pull_request: (i) => github.createPullRequest(i),
    spectral_lint_openapi: async (i: { openapiYaml: string }) => lintOpenapi(i.openapiYaml),
    amazonq_generate_cloudcraft: (i) => personas.generateCloudcraft(i),
    amazonq_analyse_failure: async (i: { rawLogText: string; failedStep: string }) => {
      const out = await personas.analyseFailure(i);
      const valid = ['type-error', 'test-failure', 'snyk-cve', 'snyk-iac', 'inspector-image', 'unknown'];
      return {
        rootCauseClass: valid.includes(out.rootCauseClass) ? out.rootCauseClass : 'unknown',
        rootCause: out.rootCause,
        affectedFiles: out.affectedFiles ?? [],
      };
    },
    ghcopilot_generate_fix: (i) => personas.generateFix(i),
    // ---- SDLC toolchain ----
    aws_s3_put_object: (i) => toolchain.s3PutObject(i),
    aws_secrets_check: (i) => toolchain.secretsCheck(i),
    postman_run_collection: (i) => toolchain.postmanRun(i),
    restassured_generate_tests: async (i) => toolchain.restassuredGenerate(i),
    playwright_generate_tests: async (i) => toolchain.playwrightGenerate(i),
    playwright_run_tests: async (i) => toolchain.playwrightRun(i),
    k6_run_test: async (i) => toolchain.k6Run(i),
    jmeter_generate_plan: async (i) => toolchain.jmeterGenerate(i),
    locust_generate_test: async (i) => toolchain.locustGenerate(i),
    zap_baseline_scan: async (i) => toolchain.zapScan(i),
    trivy_scan_image: async (i) => toolchain.trivyScan(i),
    sonarqube_analyse: async (i) => toolchain.sonarAnalyse(i),
  };

  return {
    modes,
    async execute(name, args) {
      const def = getTool(name);
      const handler = handlers[name];
      if (!handler) throw new SdlcError('TOOL_ERROR', `Tool ${name} has no handler`);

      const input = def.input.safeParse(args);
      if (!input.success) {
        throw new SdlcError('VALIDATION_FAILED', `Invalid input for ${name}: ${input.error.issues.map((i) => `${i.path.join('.')}: ${i.message}`).join('; ')}`);
      }

      if (def.cacheable) {
        const hit = await cache.get(name, input.data);
        if (hit !== null) {
          log.debug({ tool: name }, 'tool cache hit');
          return hit;
        }
      }

      const started = Date.now();
      let raw: unknown;
      try {
        raw = await handler(input.data as never);
      } catch (err) {
        const wrapped = toSdlcError(err, `Tool ${name} failed`);
        log.error({ tool: name, err: wrapped.message }, 'tool execution failed');
        throw wrapped;
      }

      const output = def.output.safeParse(raw);
      if (!output.success) {
        throw new SdlcError('TOOL_ERROR', `Tool ${name} produced invalid output: ${output.error.issues.map((i) => i.message).join('; ')}`);
      }
      log.info({ tool: name, ms: Date.now() - started }, 'tool ok');

      if (def.cacheable) await cache.set(name, input.data, output.data);
      return output.data;
    },
  };
}
