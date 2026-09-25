import { pino } from 'pino';
import { describe, expect, it } from 'vitest';
import type { Redis } from 'ioredis';
import { loadEnv, ToolsEnvSchema } from '@sdlc/shared';
import { githubImpl } from './impl/github.js';
import { lintOpenapi } from './impl/openapi-lint.js';
import { createToolRuntime } from './registry.js';

/** Minimal in-memory Redis fake covering get/set/incr used by the mock connectors. */
function fakeRedis(): Redis {
  const data = new Map<string, string>();
  let seq = 0;
  const impl = {
    async get(key: string) {
      return data.get(key) ?? null;
    },
    async set(key: string, value: string) {
      data.set(key, value);
      return 'OK';
    },
    async incr(key: string) {
      const raw = key.includes('seq') ? ++seq : Number(data.get(key) ?? 0) + 1;
      data.set(key, String(raw));
      return raw;
    },
    async ping() {
      return 'PONG';
    },
  };
  return impl as unknown as Redis;
}

const env = loadEnv(ToolsEnvSchema, {
  TOOLS_MODE: 'mock',
  AI_CLIENT_URL: 'http://localhost:9',
  REDIS_URL: 'redis://localhost:6379',
});
const log = pino({ level: 'silent' });

const VALID_OAS = `openapi: 3.0.3
info:
  title: Demo API
  version: 1.0.0
  description: demo
paths:
  /v1/items:
    get:
      operationId: listItems
      summary: List
      responses:
        '200':
          description: OK
`;

describe('openapi linter', () => {
  it('passes a valid document', () => {
    expect(lintOpenapi(VALID_OAS).result).toBe('PASS');
  });

  it('fails on missing operationId, duplicate ids and missing responses', () => {
    const bad = `openapi: 3.0.3
info: { title: X, version: '1' }
paths:
  /a:
    get:
      operationId: dup
      responses: { '200': { description: ok } }
    post:
      operationId: dup
  /b:
    get: {}
`;
    const res = lintOpenapi(bad);
    expect(res.result).toBe('FAIL');
    const codes = res.violations.map((v) => v.code);
    expect(codes).toContain('operation-operationId-unique');
    expect(codes).toContain('operation-operationId');
    expect(codes).toContain('operation-responses');
  });

  it('fails on invalid yaml', () => {
    expect(lintOpenapi('{{{{').result).toBe('FAIL');
  });
});

describe('mock GitHub CI simulation (build recovery substrate)', () => {
  it('fails while BUG-MARKER present, succeeds after fix commit', async () => {
    const gh = githubImpl({ env, redis: fakeRedis(), live: false });
    await gh.createBranch({ branch: 'feat/x', from: 'main' });

    const push = await gh.commitCode({
      branch: 'feat/x',
      files: [{ path: 'src/a.ts', content: 'const x = 1;\n// BUG-MARKER: const wrong: number = "oops";' }],
      message: 'feat: initial',
    });
    const run1 = await gh.pollRunStatus({ runId: push.runId });
    expect(run1.conclusion).toBe('failure');
    const logs = await gh.fetchBuildLogs({ runId: push.runId });
    expect(logs.rawLogText).toContain('TS2322');
    expect(logs.failedStep).toBe('typecheck');

    const fix = await gh.commitCode({
      branch: 'feat/x',
      files: [{ path: 'src/a.ts', content: 'const x = 1;' }],
      message: 'fix(ai): iter-1',
    });
    const run2 = await gh.pollRunStatus({ runId: fix.runId });
    expect(run2.conclusion).toBe('success');
  });
});

describe('tool runtime', () => {
  it('validates input, executes mock jira, validates output', async () => {
    const runtime = createToolRuntime(env, fakeRedis(), log);
    await expect(runtime.execute('jira_create_epic', { description: 'missing title' })).rejects.toMatchObject({
      code: 'VALIDATION_FAILED',
    });
    const out = (await runtime.execute('jira_create_epic', { title: 'Epic', description: 'd' })) as {
      epicKey: string;
    };
    expect(out.epicKey).toMatch(/^SDLC-\d+$/);
  });

  it('honours a per-request Jira projectKey and keeps stories under it', async => {
    const runtime = createToolRuntime(env, fakeRedis(), log);
    const epic = (await runtime.execute('jira_create_epic', {
      title: 'Epic', description: 'd', projectKey: 'aqdp',
    })) as { epicKey: string };
    expect(epic.epicKey).toMatch(/^AQDP-\d+$/); // sanitised to uppercase
    const story = (await runtime.execute('jira_create_story', {
      epicKey: epic.epicKey, storyText: 'As a user...', gherkinCriteria: ['Given...'],
    })) as { storyKey: string };
    expect(story.storyKey).toMatch(/^AQDP-\d+$/); // inherits the epic's project
  });

  it('caches cacheable tools (spectral lint)', async () => {
    const redis = fakeRedis();
    const runtime = createToolRuntime(env, redis, log);
    const a = await runtime.execute('spectral_lint_openapi', { openapiYaml: VALID_OAS });
    const b = await runtime.execute('spectral_lint_openapi', { openapiYaml: VALID_OAS });
    expect(b).toEqual(a);
  });

  it('resolves all connectors to mock when TOOLS_MODE=mock', () => {
    const runtime = createToolRuntime(env, fakeRedis(), log);
    expect(runtime.modes).toEqual({ jira: 'mock', confluence: 'mock', github: 'mock', aws: 'mock' });
  });
});

describe('SDLC toolchain tools', () => {
  const run = (name: string, args: unknown) => createToolRuntime(env, fakeRedis(), log).execute(name, args);

  it('generates REST Assured tests, a JMeter plan and a locustfile from OpenAPI', async () => {
    const oas = 'openapi: 3.0.3\npaths:\n  /items:\n    get:\n      summary: list\n    post:\n      summary: create\n';
    const ra = (await run('restassured_generate_tests', { openapiYaml: oas, serviceName: 'Cart API' })) as {
      javaClass: string; testCount: number; path: string;
    };
    expect(ra.testCount).toBe(2);
    expect(ra.javaClass).toContain('RestAssured.given');
    expect(ra.path).toContain('CartAPIApiTest.java');

    const jm = (await run('jmeter_generate_plan', { openapiYaml: oas, serviceName: 'Cart API' })) as {
      jmxXml: string; samplerCount: number;
    };
    expect(jm.samplerCount).toBe(2);
    expect(jm.jmxXml).toContain('<jmeterTestPlan');

    const lo = (await run('locust_generate_test', { openapiYaml: oas, serviceName: 'Cart API' })) as {
      locustfile: string; taskCount: number;
    };
    expect(lo.taskCount).toBe(2);
    expect(lo.locustfile).toContain('HttpUser');
  });

  it('runs postman/playwright/k6 engines deterministically with BUG-MARKER failures', async () => {
    const collection = JSON.stringify({ item: [{ name: 'list items' }, { name: 'BUG-MARKER broken call' }] });
    const pm = (await run('postman_run_collection', { collectionJson: collection })) as {
      total: number; passed: number; failed: number;
    };
    expect(pm).toMatchObject({ total: 2, passed: 1, failed: 1 });

    const spec = "test('a', async ({ page }) => {});\ntest('b', async ({ page }) => {});";
    const pw = (await run('playwright_run_tests', { specTs: spec })) as { total: number; failed: number };
    expect(pw.total).toBe(2);
    expect(pw.failed).toBe(0);

    const k6a = (await run('k6_run_test', { script: 'export default fn' })) as { p95Ms: number };
    const k6b = (await run('k6_run_test', { script: 'export default fn' })) as { p95Ms: number };
    expect(k6a.p95Ms).toBe(k6b.p95Ms); // deterministic
    const bad = (await run('k6_run_test', { script: 'BUG-MARKER slow endpoint' })) as { thresholdsPassed: boolean };
    expect(bad.thresholdsPassed).toBe(false);
  });

  it('security tools report findings and the secrets check never returns values', async () => {
    const trivy = (await run('trivy_scan_image', { dockerfile: 'FROM node:16\nRUN npm ci' })) as {
      result: string; high: number;
    };
    expect(trivy.result).toBe('FAIL'); // outdated base image
    expect(trivy.high).toBeGreaterThan(0);

    const zap = (await run('zap_baseline_scan', { targetUrl: 'http://app.local' })) as { result: string };
    expect(['PASS', 'WARN', 'FAIL']).toContain(zap.result);

    const secret = (await run('aws_secrets_check', { secretId: 'sdlc/jwt-secret' })) as Record<string, unknown>;
    expect(secret.exists).toBe(true);
    expect(secret.mode).toBe('mock');
    expect(JSON.stringify(secret)).not.toContain('value'); // metadata only

    const missing = (await run('aws_secrets_check', { secretId: 'sdlc/missing-key' })) as { exists: boolean };
    expect(missing.exists).toBe(false);
  });

  it('sonarqube quality gate fails on BUG-MARKER code and s3 put stores in mock mode', async () => {
    const sonar = (await run('sonarqube_analyse', {
      files: [{ path: 'src/a.ts', content: 'const x = 1; // BUG-MARKER' }],
    })) as { qualityGate: string; bugs: number };
    expect(sonar.qualityGate).toBe('ERROR');
    expect(sonar.bugs).toBeGreaterThan(0);

    const s3 = (await run('aws_s3_put_object', { key: 'reports/run-1.md', content: '# report' })) as {
      mode: string; url: string;
    };
    expect(s3.mode).toBe('mock');
    expect(s3.url).toContain('s3://');
  });
});
