import type { Redis } from 'ioredis';
import type { ToolsEnv } from '@sdlc/shared';

/**
 * SDLC toolchain connectors: AWS (S3 / Secrets Manager), API testing
 * (Postman/newman, REST Assured), UI testing (Playwright), performance
 * (k6, JMeter, Locust) and security/quality (OWASP ZAP, Trivy, SonarQube).
 *
 * Mock-first like every connector: the simulated engines are
 * DETERMINISTIC — metrics derive from an FNV hash of the input, and the
 * `BUG-MARKER` sentinel forces failures so e2e suites can exercise both
 * outcomes. AWS goes live when TOOLS_AWS_S3_BUCKET is set (SDK loaded
 * lazily; auth via the standard AWS credential chain).
 *
 * Responsible AI note: `aws_secrets_check` returns EXISTENCE METADATA ONLY —
 * secret values never flow through the agent pipeline.
 */

const fnv = (text: string): number => {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h;
};

/** Pull `GET /path`-style operations out of an OpenAPI YAML without a parser. */
function extractOperations(openapiYaml: string): Array<{ method: string; path: string }> {
  const ops: Array<{ method: string; path: string }> = [];
  let currentPath: string | null = null;
  for (const line of openapiYaml.split('\n')) {
    const pathMatch = /^ {2}(\/[^\s:]*):\s*$/.exec(line);
    if (pathMatch) {
      currentPath = pathMatch[1]!;
      continue;
    }
    const methodMatch = /^ {4}(get|post|put|patch|delete)\s*:/i.exec(line);
    if (methodMatch && currentPath) ops.push({ method: methodMatch[1]!.toUpperCase(), path: currentPath });
  }
  return ops.length > 0 ? ops : [{ method: 'GET', path: '/healthz' }];
}

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '');

export function sdlcToolchainImpl(opts: { env: ToolsEnv; redis: Redis; awsLive: boolean }) {
  const { env, redis, awsLive } = opts;
  const bucket = (env as { TOOLS_AWS_S3_BUCKET?: string }).TOOLS_AWS_S3_BUCKET || 'sdlc-tool-store';

  return {
    // ------------------------------------------------------------- AWS
    async s3PutObject(i: { key: string; content: string; contentType: string }) {
      if (awsLive) {
        const { S3Client, PutObjectCommand } = await import('@aws-sdk/client-s3');
        const client = new S3Client({});
        const res = await client.send(new PutObjectCommand({
          Bucket: bucket, Key: i.key, Body: i.content, ContentType: i.contentType,
        }));
        return {
          bucket, key: i.key, etag: res.ETag ?? 'unknown',
          url: `s3://${bucket}/${i.key}`, mode: 'live' as const,
        };
      }
      await redis.set(`mock:s3:${bucket}:${i.key}`, i.content, 'EX', 86_400);
      return {
        bucket, key: i.key, etag: `"${fnv(i.content).toString(16)}"`,
        url: `s3://${bucket}/${i.key}`, mode: 'mock' as const,
      };
    },

    async secretsCheck(i: { secretId: string }) {
      if (awsLive) {
        const { SecretsManagerClient, DescribeSecretCommand } = await import('@aws-sdk/client-secrets-manager');
        const client = new SecretsManagerClient({});
        try {
          const res = await client.send(new DescribeSecretCommand({ SecretId: i.secretId }));
          return {
            exists: true, arn: res.ARN ?? null,
            lastChangedDate: res.LastChangedDate?.toISOString() ?? null, mode: 'live' as const,
          };
        } catch {
          return { exists: false, arn: null, lastChangedDate: null, mode: 'live' as const };
        }
      }
      const exists = !i.secretId.includes('missing');
      return {
        exists,
        arn: exists ? `arn:aws:secretsmanager:eu-west-2:000000000000:secret:${i.secretId}-mock` : null,
        lastChangedDate: exists ? '2026-07-01T00:00:00.000Z' : null,
        mode: 'mock' as const,
      };
    },

    // ------------------------------------------------------------- Postman / newman
    async postmanRun(i: { collectionJson: string; environment: Record<string, string> }) {
      let names: string[] = [];
      try {
        const parsed = JSON.parse(i.collectionJson) as { item?: Array<{ name?: string }> };
        names = (parsed.item ?? []).map((it, n) => it.name ?? `request-${n + 1}`);
      } catch {
        names = ['collection-parse-fallback'];
      }
      if (names.length === 0) names = ['empty-collection'];
      const failures = names
        .filter((n) => n.includes('BUG-MARKER'))
        .map((n) => ({ request: n, reason: 'Assertion failed: expected 200, got 500' }));
      const passed = names.length - failures.length;
      const durationMs = 400 + (fnv(i.collectionJson) % 1200);
      const lines = names.map((n) => `| ${n} | ${n.includes('BUG-MARKER') ? '❌ fail' : '✅ pass'} |`);
      return {
        total: names.length, passed, failed: failures.length, durationMs, failures,
        reportMarkdown: `# Postman run (newman)\n\n${failures.length === 0 ? '**ALL PASSED**' : `**${failures.length} FAILED**`} — ${names.length} requests in ${durationMs}ms\n\n| Request | Result |\n|---|---|\n${lines.join('\n')}`,
      };
    },

    // ------------------------------------------------------------- REST Assured
    restassuredGenerate(i: { openapiYaml: string; serviceName: string }) {
      const ops = extractOperations(i.openapiYaml);
      const className = `${i.serviceName.replace(/[^A-Za-z0-9]/g, '')}ApiTest`;
      const methods = ops.map(({ method, path }) => {
        const name = `test${method.charAt(0)}${method.slice(1).toLowerCase()}${slug(path).replace(/-([a-z0-9])/g, (_, c: string) => c.toUpperCase()) || 'Root'}`;
        return [
          '    @Test',
          `    public void ${name}() {`,
          '        given().contentType(ContentType.JSON)',
          `        .when().${method.toLowerCase()}("${path}")`,
          `        .then().statusCode(anyOf(is(200), is(201), is(204)))`,
          '        .time(lessThan(2000L));',
          '    }',
        ].join('\n');
      });
      const javaClass = [
        `package com.sdlc.generated;`, '',
        'import io.restassured.http.ContentType;',
        'import org.junit.jupiter.api.Test;',
        'import static io.restassured.RestAssured.given;',
        'import static org.hamcrest.Matchers.*;', '',
        `/** Generated by the AI-SDLC platform from the OpenAPI contract of ${i.serviceName}. */`,
        `public class ${className} {`,
        methods.join('\n\n'),
        '}',
      ].join('\n');
      return { javaClass, path: `src/test/java/com/sdlc/generated/${className}.java`, testCount: ops.length };
    },

    // ------------------------------------------------------------- Playwright
    playwrightGenerate(i: { stories: Array<{ key: string; text: string; criteria: string[] }>; baseUrl: string }) {
      const tests = i.stories.map((s) => {
        const steps = (s.criteria.length > 0 ? s.criteria : ['Given the app is open\nThen the flow completes'])
          .map((c, n) => `    // AC${n + 1}: ${c.split('\n').join(' / ')}`)
          .join('\n');
        return [
          `test('${s.key}: ${s.text.replace(/'/g, "\\'").slice(0, 80)}', async ({ page }) => {`,
          `  await page.goto('${i.baseUrl}');`,
          steps,
          `  await expect(page).toHaveTitle(/.+/);`,
          '});',
        ].join('\n');
      });
      const specTs = [
        `import { expect, test } from '@playwright/test';`, '',
        `// Generated by the AI-SDLC platform from user stories + Gherkin acceptance criteria.`,
        ...tests,
      ].join('\n\n');
      return { specTs, path: 'e2e/generated.spec.ts', testCount: i.stories.length };
    },

    playwrightRun(i: { specTs: string; baseUrl: string }) {
      const total = Math.max(1, (i.specTs.match(/^test\(/gm) ?? []).length);
      const failed = i.specTs.includes('BUG-MARKER') ? 1 : 0;
      const durationMs = 900 + (fnv(i.specTs) % 4000);
      return {
        total, passed: total - failed, failed, skipped: 0, durationMs,
        reportMarkdown: `# Playwright run\n\n${failed === 0 ? '**ALL PASSED**' : `**${failed} FAILED**`} — ${total} tests, ${durationMs}ms (chromium, headless)`,
      };
    },

    // ------------------------------------------------------------- k6 / JMeter / Locust
    k6Run(i: { script: string; vus: number; durationSeconds: number }) {
      const h = fnv(i.script);
      const degraded = i.script.includes('BUG-MARKER');
      const p95 = degraded ? 800 + (h % 600) : 120 + (h % 250);
      const p99 = p95 + 80 + (h % 120);
      const errorRate = degraded ? 0.08 : Number(((h % 90) / 10_000).toFixed(4));
      const rps = Number((i.vus * (6 + (h % 5))).toFixed(1));
      const passed = p95 < 500 && errorRate < 0.01;
      return {
        rps, p95Ms: p95, p99Ms: p99, errorRate, thresholdsPassed: passed,
        reportMarkdown: `# k6 load test\n\n${passed ? '✅ thresholds PASSED' : '❌ thresholds FAILED'} — ${i.vus} VUs for ${i.durationSeconds}s\n\n| metric | value |\n|---|---|\n| rps | ${rps} |\n| p95 | ${p95}ms |\n| p99 | ${p99}ms |\n| error rate | ${(errorRate * 100).toFixed(2)}% |`,
      };
    },

    jmeterGenerate(i: { openapiYaml: string; serviceName: string; vus: number; rampSeconds: number }) {
      const ops = extractOperations(i.openapiYaml);
      const samplers = ops.map(({ method, path }) =>
        [
          `      <HTTPSamplerProxy guiclass="HttpTestSampleGui" testname="${method} ${path}">`,
          `        <stringProp name="HTTPSampler.path">${path}</stringProp>`,
          `        <stringProp name="HTTPSampler.method">${method}</stringProp>`,
          '      </HTTPSamplerProxy>',
        ].join('\n'),
      );
      const jmxXml = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<jmeterTestPlan version="1.2" properties="5.0">',
        `  <TestPlan testname="${i.serviceName} load plan (generated)">`,
        `    <ThreadGroup testname="Users"><stringProp name="ThreadGroup.num_threads">${i.vus}</stringProp>`,
        `      <stringProp name="ThreadGroup.ramp_time">${i.rampSeconds}</stringProp>`,
        samplers.join('\n'),
        '    </ThreadGroup>',
        '  </TestPlan>',
        '</jmeterTestPlan>',
      ].join('\n');
      return { jmxXml, path: `perf/${slug(i.serviceName)}-plan.jmx`, samplerCount: ops.length };
    },

    locustGenerate(i: { openapiYaml: string; serviceName: string }) {
      const ops = extractOperations(i.openapiYaml);
      const tasks = ops.map(({ method, path }, n) =>
        [
          '    @task',
          `    def op_${n + 1}_${slug(path).replace(/-/g, '_') || 'root'}(self):`,
          `        self.client.${method.toLowerCase()}("${path}")`,
        ].join('\n'),
      );
      const locustfile = [
        'from locust import HttpUser, between, task', '',
        '',
        `class ${i.serviceName.replace(/[^A-Za-z0-9]/g, '')}User(HttpUser):`,
        '    """Generated by the AI-SDLC platform from the OpenAPI contract."""',
        '    wait_time = between(0.5, 2)', '',
        tasks.join('\n\n'),
      ].join('\n');
      return { locustfile, path: 'perf/locustfile.py', taskCount: ops.length };
    },

    // ------------------------------------------------------------- security & quality
    zapScan(i: { targetUrl: string; openapiYaml?: string }) {
      const h = fnv(i.targetUrl + (i.openapiYaml ?? ''));
      const insecure = (i.openapiYaml ?? '').includes('BUG-MARKER');
      const high = insecure ? 1 : 0;
      const medium = h % 3;
      const low = 1 + (h % 4);
      const alerts = [
        ...(insecure ? [{ risk: 'High', name: 'SQL Injection (simulated)', url: `${i.targetUrl}/api/items` }] : []),
        ...(medium > 0 ? [{ risk: 'Medium', name: 'Missing CSP header', url: i.targetUrl }] : []),
        { risk: 'Low', name: 'X-Content-Type-Options missing', url: i.targetUrl },
      ];
      const result = high > 0 ? ('FAIL' as const) : medium > 0 ? ('WARN' as const) : ('PASS' as const);
      return {
        result, high, medium, low, alerts,
        reportMarkdown: `# OWASP ZAP baseline scan\n\n**${result}** — ${high} high / ${medium} medium / ${low} low\n\n${alerts.map((a) => `- [${a.risk}] ${a.name} @ ${a.url}`).join('\n')}`,
      };
    },

    trivyScan(i: { dockerfile: string; imageTag: string }) {
      const h = fnv(i.dockerfile);
      const outdatedBase = /FROM\s+\S+:(latest|16|14-alpine)/i.test(i.dockerfile);
      const critical = i.dockerfile.includes('BUG-MARKER') ? 1 : 0;
      const high = outdatedBase ? 1 + (h % 2) : 0;
      const medium = h % 5;
      const vulnerabilities = [
        ...(critical ? [{ id: 'CVE-2026-0001', severity: 'CRITICAL', pkg: 'openssl' }] : []),
        ...(high > 0 ? [{ id: 'CVE-2025-48174', severity: 'HIGH', pkg: 'busybox' }] : []),
        ...(medium > 0 ? [{ id: 'CVE-2025-31498', severity: 'MEDIUM', pkg: 'c-ares' }] : []),
      ];
      const result = critical + high > 0 ? ('FAIL' as const) : ('PASS' as const);
      return {
        result, critical, high, medium, vulnerabilities,
        reportMarkdown: `# Trivy scan — ${i.imageTag}\n\n**${result}** — ${critical} critical / ${high} high / ${medium} medium\n\n${vulnerabilities.map((v) => `- ${v.id} [${v.severity}] in ${v.pkg}`).join('\n') || 'No findings.'}`,
      };
    },

    sonarAnalyse(i: { files: Array<{ path: string; content: string }> }) {
      const body = i.files.map((f) => f.content).join('\n');
      const h = fnv(body);
      const loc = body.split('\n').length;
      const bugs = body.includes('BUG-MARKER') ? 2 : 0;
      const vulnerabilities = 0;
      const codeSmells = Math.max(1, Math.floor(loc / 120)) + (h % 3);
      const coveragePct = Number((70 + (h % 250) / 10).toFixed(1));
      const duplicationPct = Number(((h % 60) / 10).toFixed(1));
      const qualityGate = bugs > 0 ? ('ERROR' as const) : coveragePct < 75 ? ('WARN' as const) : ('OK' as const);
      return {
        qualityGate, bugs, vulnerabilities, codeSmells, coveragePct, duplicationPct,
        reportMarkdown: `# SonarQube analysis\n\nQuality gate: **${qualityGate}**\n\n| metric | value |\n|---|---|\n| files | ${i.files.length} |\n| lines | ${loc} |\n| bugs | ${bugs} |\n| vulnerabilities | ${vulnerabilities} |\n| code smells | ${codeSmells} |\n| coverage | ${coveragePct}% |\n| duplication | ${duplicationPct}% |`,
      };
    },
  };
}
