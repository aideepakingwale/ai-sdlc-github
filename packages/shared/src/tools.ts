import { z } from 'zod';

/**
 * Single registry of every MCP tool exposed by tool-connector-service (Module 4).
 * The server registers handlers from this registry; the orchestrator's executor
 * validates inputs/outputs against the same schemas — one source of truth.
 */
export interface ToolDefinition<I extends z.ZodTypeAny = z.ZodTypeAny, O extends z.ZodTypeAny = z.ZodTypeAny> {
  name: string;
  description: string;
  input: I;
  output: O;
  /** True for tools implemented as LLM personas rather than real vendor APIs. */
  persona?: boolean;
  /** Cacheable deterministic tools get a 24h Redis cache (Module 1 §2). */
  cacheable?: boolean;
}

function tool<I extends z.ZodTypeAny, O extends z.ZodTypeAny>(def: ToolDefinition<I, O>): ToolDefinition<I, O> {
  return def;
}

// ---------- Jira ----------
export const jiraCreateEpic = tool({
  name: 'jira_create_epic',
  description: 'Create a Jira Epic. Returns { epicId, epicKey }.',
  input: z.object({
    title: z.string().min(1),
    description: z.string(),
    priority: z.enum(['Highest', 'High', 'Medium', 'Low']).default('Medium'),
    // Optional per-request Jira project key; overrides JIRA_PROJECT_KEY.
    // Lets the requirements agent set a project-specific identifier (e.g. AQDP)
    // and honour reviewer feedback like "change the identifier from SDLC to AQDP".
    projectKey: z.string().optional(),
  }),
  output: z.object({ epicId: z.string(), epicKey: z.string(), url: z.string() }),
});

export const jiraCreateStory = tool({
  name: 'jira_create_story',
  description: 'Create a User Story under an Epic with Gherkin acceptance criteria.',
  input: z.object({
    epicKey: z.string(),
    storyText: z.string().min(1),
    gherkinCriteria: z.array(z.string()).min(1),
    storyPoints: z.number().int().positive().optional(),
    projectKey: z.string().optional(),
  }),
  output: z.object({ storyId: z.string(), storyKey: z.string(), url: z.string() }),
});

export const jiraCreateXrayTest = tool({
  name: 'jira_create_xray_test',
  description: 'Create an Xray Test issue linked to a story.',
  input: z.object({
    storyKey: z.string(),
    title: z.string(),
    steps: z.array(z.object({ action: z.string(), expectedResult: z.string() })).min(1),
  }),
  output: z.object({ xrayTestKey: z.string(), url: z.string() }),
});

// ---------- Jira: read / search / update ----------
/** Jira issue keys are interpolated into URLs, so they are validated strictly. */
const jiraIssueKey = z
  .string()
  .regex(/^[A-Z][A-Z0-9_]*-\d+$/, 'must be a Jira issue key such as PROJ-123');
const isoDateTime = z.string().datetime({ offset: true });

export const JiraIssueSchema = z.object({
  key: z.string(),
  id: z.string(),
  url: z.string(),
  summary: z.string(),
  /** Plain text (ADF flattened); the acceptance-criteria section is NOT included. */
  description: z.string(),
  type: z.string(),
  status: z.string(),
  statusCategory: z.enum(['todo', 'inprogress', 'done', 'unknown']),
  priority: z.string().nullable(),
  storyPoints: z.number().nullable(),
  labels: z.array(z.string()),
  epicKey: z.string().nullable(),
  sprint: z.object({ id: z.number(), name: z.string(), state: z.string() }).nullable(),
  assignee: z.string().nullable(),
  created: z.string(),
  updated: z.string(),
  acceptanceCriteria: z.array(z.string()),
});
export type JiraIssue = z.infer<typeof JiraIssueSchema>;

export const jiraSearchIssues = tool({
  name: 'jira_search_issues',
  description:
    'Search Jira issues (ordered by updated ASC) by project, update time, issue types and an optional raw JQL filter. Token-paginated.',
  input: z.object({
    projectKey: z.string().min(1).max(64).optional(),
    jql: z.string().min(1).max(2000).optional(),
    updatedSince: isoDateTime.optional(),
    issueTypes: z.array(z.string().min(1).max(64)).max(20).optional(),
    maxResults: z.number().int().min(1).max(100).default(50),
    nextPageToken: z.string().min(1).max(2048).optional(),
  }),
  output: z.object({
    issues: z.array(JiraIssueSchema),
    nextPageToken: z.string().nullable(),
    total: z.number().int().nullable(),
  }),
});

export const jiraGetIssue = tool({
  name: 'jira_get_issue',
  description: 'Fetch one Jira issue by key.',
  input: z.object({ key: jiraIssueKey }),
  output: z.object({ issue: JiraIssueSchema }),
});

export const jiraUpdateIssue = tool({
  name: 'jira_update_issue',
  description:
    'Update summary/description/priority/story points/labels/acceptance criteria. With expectedUpdated, fails with a conflict (no write) if the issue changed since.',
  input: z.object({
    key: jiraIssueKey,
    fields: z
      .object({
        summary: z.string().min(1).max(255).optional(),
        description: z.string().max(30_000).optional(),
        priority: z.string().min(1).max(50).optional(),
        storyPoints: z.number().min(0).max(1000).optional(),
        labels: z.array(z.string().regex(/^\S{1,255}$/, 'labels cannot contain whitespace')).max(50).optional(),
        acceptanceCriteria: z.array(z.string().min(1).max(5000)).max(100).optional(),
      })
      .strict()
      .refine((f) => Object.values(f).some((v) => v !== undefined), { message: 'at least one field is required' }),
    expectedUpdated: isoDateTime.optional(),
  }),
  output: z.object({ issue: JiraIssueSchema }),
});

export const jiraTransitionIssue = tool({
  name: 'jira_transition_issue',
  description: 'Move an issue to the workflow status named toStatus (case-insensitive).',
  input: z.object({ key: jiraIssueKey, toStatus: z.string().min(1).max(100) }),
  output: z.object({ issue: JiraIssueSchema }),
});

export const jiraAddComment = tool({
  name: 'jira_add_comment',
  description: 'Add a plain-text comment to an issue. Returns { id, url }.',
  input: z.object({ key: jiraIssueKey, body: z.string().min(1).max(30_000) }),
  output: z.object({ id: z.string(), url: z.string() }),
});

// ---------- Confluence ----------
export const confluencePublishPrd = tool({
  name: 'confluence_publish_prd',
  description: 'Publish a PRD page linked to Jira issues. Returns { pageId, url }.',
  input: z.object({
    title: z.string(),
    content: z.string().describe('Page body (markdown; converted/stored as-is in mock mode)'),
    jiraLinks: z.array(z.string()).default([]),
  }),
  output: z.object({ pageId: z.string(), url: z.string() }),
});

export const confluencePublishHld = tool({
  name: 'confluence_publish_hld',
  description: 'Publish the HLD page embedding Structurizr diagrams and ADR links.',
  input: z.object({
    title: z.string(),
    hldContent: z.string(),
    structurizrDsl: z.string().optional(),
    adrLinks: z.array(z.string()).default([]),
  }),
  output: z.object({ pageId: z.string(), url: z.string() }),
});

export const confluencePublishLld = tool({
  name: 'confluence_publish_lld',
  description: 'Publish an LLD page with PlantUML macros, OpenAPI embed and ERD link.',
  input: z.object({
    serviceName: z.string(),
    lldContent: z.string(),
    plantumlSources: z.array(z.string()).default([]),
    openapiYaml: z.string().optional(),
    erdLink: z.string().optional(),
  }),
  output: z.object({ pageId: z.string(), url: z.string() }),
});

// ---------- GitHub ----------
const commitResult = z.object({ commitSha: z.string(), branch: z.string(), htmlUrl: z.string() });

export const githubCommitDiagrams = tool({
  name: 'github_commit_diagrams',
  description: 'Commit Structurizr DSL / draw.io XML / Cloudcraft JSON to the repo (triggers render Action).',
  input: z.object({
    branch: z.string().default('main'),
    files: z.array(z.object({ path: z.string(), content: z.string() })).min(1),
    message: z.string().default('docs(architecture): add generated diagrams'),
  }),
  output: commitResult,
});

export const githubCommitLldArtefacts = tool({
  name: 'github_commit_lld_artefacts',
  description: 'Commit PlantUML files, OpenAPI YAML, DBML and CDK stacks.',
  input: z.object({
    branch: z.string().default('main'),
    files: z.array(z.object({ path: z.string(), content: z.string() })).min(1),
    message: z.string().default('docs(design): add LLD artefacts'),
  }),
  output: commitResult,
});

export const githubCommitPipelineConfig = tool({
  name: 'github_commit_pipeline_config',
  description: 'Commit GitHub Actions workflow YAML, Dockerfiles and env configs.',
  input: z.object({
    branch: z.string().default('main'),
    files: z.array(z.object({ path: z.string(), content: z.string() })).min(1),
    message: z.string().default('ci: add generated pipeline configuration'),
  }),
  output: commitResult,
});

export const githubCreateBranch = tool({
  name: 'github_create_branch',
  description: 'Create a feature branch from the default branch.',
  input: z.object({ branch: z.string(), from: z.string().default('main') }),
  output: z.object({ branch: z.string(), baseSha: z.string() }),
});

export const githubCommitCode = tool({
  name: 'github_commit_code',
  description: 'Commit application code + unit tests to a branch. Push triggers the CI pipeline.',
  input: z.object({
    branch: z.string(),
    files: z.array(z.object({ path: z.string(), content: z.string() })).min(1),
    message: z.string(),
  }),
  output: commitResult.extend({ runId: z.string().describe('CI run id triggered by this push') }),
});

export const githubFetchBuildLogs = tool({
  name: 'github_fetch_build_logs',
  description: 'Fetch GitHub Actions logs for a failed run.',
  input: z.object({ runId: z.string(), failedJobIds: z.array(z.string()).default([]) }),
  output: z.object({ rawLogText: z.string(), failedStep: z.string() }),
});

export const githubCommitFix = tool({
  name: 'github_commit_fix',
  description: 'Commit an AI-generated fix diff to the build branch.',
  input: z.object({
    branch: z.string(),
    files: z.array(z.object({ path: z.string(), content: z.string() })).min(1),
    message: z.string(),
  }),
  output: commitResult.extend({ runId: z.string() }),
});

export const githubPollRunStatus = tool({
  name: 'github_poll_run_status',
  description: 'Poll a GitHub Actions run. Returns status/conclusion and failed job ids.',
  input: z.object({ runId: z.string() }),
  output: z.object({
    status: z.enum(['queued', 'in_progress', 'completed']),
    conclusion: z.enum(['success', 'failure', 'cancelled']).nullable(),
    failedJobIds: z.array(z.string()).default([]),
  }),
});

export const githubCreatePullRequest = tool({
  name: 'github_create_pull_request',
  description: 'Open a pull request with a review checklist.',
  input: z.object({
    branch: z.string(),
    title: z.string(),
    body: z.string(),
    checklist: z.array(z.string()).default([]),
  }),
  output: z.object({ prNumber: z.number(), url: z.string() }),
});

// ---------- GitHub: atomic index commits, reads, tree listing, idempotent PRs ----------
export const githubCommitIndex = tool({
  name: 'github_commit_index',
  description:
    'Atomically commit many files (and deletions) to a branch via the Git Data API. Creates the branch from baseBranch if missing; returns noop=true (no commit) when the resulting tree is unchanged.',
  input: z.object({
    branch: z.string().min(1).max(255),
    baseBranch: z.string().min(1).max(255).default('main'),
    files: z.array(z.object({ path: z.string().min(1).max(1024), content: z.string() })).max(500),
    deletions: z.array(z.string().min(1).max(1024)).max(500).default([]),
    message: z.string().min(1).max(10_000),
    createBranchIfMissing: z.boolean().default(true),
    expectedHeadSha: z
      .string()
      .regex(/^([0-9a-fA-F]{40}|[0-9a-fA-F]{64})$/, 'must be a full commit sha')
      .optional(),
    requiredPrefix: z.string().min(1).max(256).default('.devmind/'),
  }),
  output: z.object({
    commitSha: z.string(),
    parentSha: z.string().nullable(),
    treeSha: z.string(),
    branch: z.string(),
    htmlUrl: z.string(),
    noop: z.boolean(),
  }),
});

export const githubReadFiles = tool({
  name: 'github_read_files',
  description: 'Read files at a ref (branch, tag or commit sha) through the Git Data API. Missing files have null content.',
  input: z.object({
    ref: z.string().min(1).max(255),
    paths: z.array(z.string().min(1).max(1024)).min(1).max(200),
  }),
  output: z.object({
    files: z.array(z.object({ path: z.string(), content: z.string().nullable(), sha: z.string().nullable() })),
  }),
});

export const githubListTree = tool({
  name: 'github_list_tree',
  description: 'List the repository tree at a ref, optionally restricted to a path prefix. Complete even for very large trees.',
  input: z.object({
    ref: z.string().min(1).max(255),
    prefix: z.string().max(1024).default(''),
    recursive: z.boolean().default(true),
  }),
  output: z.object({
    entries: z.array(
      z.object({
        path: z.string(),
        type: z.enum(['blob', 'tree']),
        sha: z.string(),
        size: z.number().int().nullable(),
      }),
    ),
    truncated: z.boolean(),
  }),
});

export const githubOpenPullRequest = tool({
  name: 'github_open_pull_request',
  description: 'Open a pull request, or return the already-open one for the same head/base when reuseExisting is true.',
  input: z.object({
    head: z.string().min(1).max(255),
    base: z.string().min(1).max(255).default('main'),
    title: z.string().min(1).max(256),
    body: z.string().max(65_536).default(''),
    reuseExisting: z.boolean().default(true),
  }),
  output: z.object({ number: z.number().int(), url: z.string(), created: z.boolean() }),
});

// ---------- Validation & persona tools ----------
export const spectralLintOpenapi = tool({
  name: 'spectral_lint_openapi',
  description: 'Lint an OpenAPI 3.x document. Returns PASS/FAIL with violations.',
  cacheable: true,
  input: z.object({ openapiYaml: z.string() }),
  output: z.object({
    result: z.enum(['PASS', 'FAIL']),
    violations: z.array(z.object({ code: z.string(), message: z.string(), path: z.string(), severity: z.string() })),
  }),
});

export const amazonqGenerateCloudcraft = tool({
  name: 'amazonq_generate_cloudcraft',
  description: 'Generate Cloudcraft AWS topology JSON from an HLD narrative (LLM persona).',
  persona: true,
  cacheable: true,
  input: z.object({ hldNarrative: z.string() }),
  output: z.object({ cloudcraftJson: z.string() }),
});

export const amazonqAnalyseFailure = tool({
  name: 'amazonq_analyse_failure',
  description: 'Analyse CI build logs and classify the root cause (LLM persona).',
  persona: true,
  input: z.object({ rawLogText: z.string(), failedStep: z.string() }),
  output: z.object({
    rootCauseClass: z.enum(['type-error', 'test-failure', 'snyk-cve', 'snyk-iac', 'inspector-image', 'unknown']),
    rootCause: z.string(),
    affectedFiles: z.array(z.string()).default([]),
  }),
});

export const ghcopilotGenerateFix = tool({
  name: 'ghcopilot_generate_fix',
  description: 'Generate a targeted code fix for a classified build failure (LLM persona).',
  persona: true,
  input: z.object({
    rootCauseClass: z.string(),
    rootCause: z.string(),
    affectedFiles: z.array(z.string()).default([]),
    currentFiles: z.array(z.object({ path: z.string(), content: z.string() })).default([]),
  }),
  output: z.object({
    files: z.array(z.object({ path: z.string(), content: z.string() })),
    message: z.string(),
  }),
});

// ---------- AWS ----------
export const awsS3PutObject = tool({
  name: 'aws_s3_put_object',
  description: 'Store generated content in S3 (live when TOOLS_AWS_S3_BUCKET is set; deterministic simulated store otherwise).',
  input: z.object({
    key: z.string().min(1),
    content: z.string(),
    contentType: z.string().default('text/plain'),
  }),
  output: z.object({ bucket: z.string(), key: z.string(), etag: z.string(), url: z.string(), mode: z.enum(['live', 'mock']) }),
});

export const awsSecretsCheck = tool({
  name: 'aws_secrets_check',
  description: 'Verify a Secrets Manager secret EXISTS (metadata only — the value is NEVER returned).',
  input: z.object({ secretId: z.string().min(1) }),
  output: z.object({
    exists: z.boolean(),
    arn: z.string().nullable(),
    lastChangedDate: z.string().nullable(),
    mode: z.enum(['live', 'mock']),
  }),
});

// ---------- API testing ----------
export const postmanRunCollection = tool({
  name: 'postman_run_collection',
  description: 'Run a Postman collection (newman engine; deterministic simulation in mock mode). Returns per-request results.',
  input: z.object({
    collectionJson: z.string(),
    environment: z.record(z.string(), z.string()).default({}),
  }),
  output: z.object({
    total: z.number().int(),
    passed: z.number().int(),
    failed: z.number().int(),
    durationMs: z.number().int(),
    failures: z.array(z.object({ request: z.string(), reason: z.string() })),
    reportMarkdown: z.string(),
  }),
});

export const restassuredGenerateTests = tool({
  name: 'restassured_generate_tests',
  description: 'Generate a Java REST Assured test class from an OpenAPI contract (deterministic, non-LLM).',
  cacheable: true,
  input: z.object({ openapiYaml: z.string(), serviceName: z.string() }),
  output: z.object({ javaClass: z.string(), path: z.string(), testCount: z.number().int() }),
});

// ---------- UI testing ----------
export const playwrightGenerateTests = tool({
  name: 'playwright_generate_tests',
  description: 'Generate a Playwright spec from user stories + acceptance criteria (deterministic, non-LLM).',
  cacheable: true,
  input: z.object({
    stories: z.array(z.object({ key: z.string(), text: z.string(), criteria: z.array(z.string()).default([]) })).min(1),
    baseUrl: z.string().default('http://localhost:3000'),
  }),
  output: z.object({ specTs: z.string(), path: z.string(), testCount: z.number().int() }),
});

export const playwrightRunTests = tool({
  name: 'playwright_run_tests',
  description: 'Execute a Playwright spec (headless engine; deterministic simulation in mock mode).',
  input: z.object({ specTs: z.string(), baseUrl: z.string().default('http://localhost:3000') }),
  output: z.object({
    total: z.number().int(),
    passed: z.number().int(),
    failed: z.number().int(),
    skipped: z.number().int(),
    durationMs: z.number().int(),
    reportMarkdown: z.string(),
  }),
});

// ---------- performance testing ----------
export const k6RunTest = tool({
  name: 'k6_run_test',
  description: 'Run a k6 load test script (deterministic simulation in mock mode). Returns latency percentiles + thresholds.',
  input: z.object({
    script: z.string(),
    vus: z.number().int().positive().default(20),
    durationSeconds: z.number().int().positive().default(60),
  }),
  output: z.object({
    rps: z.number(),
    p95Ms: z.number(),
    p99Ms: z.number(),
    errorRate: z.number(),
    thresholdsPassed: z.boolean(),
    reportMarkdown: z.string(),
  }),
});

export const jmeterGeneratePlan = tool({
  name: 'jmeter_generate_plan',
  description: 'Generate a JMeter test plan (.jmx XML) from an OpenAPI contract (deterministic, non-LLM).',
  cacheable: true,
  input: z.object({
    openapiYaml: z.string(),
    serviceName: z.string(),
    vus: z.number().int().positive().default(50),
    rampSeconds: z.number().int().positive().default(30),
  }),
  output: z.object({ jmxXml: z.string(), path: z.string(), samplerCount: z.number().int() }),
});

export const locustGenerateTest = tool({
  name: 'locust_generate_test',
  description: 'Generate a Locust load-test file (locustfile.py) from an OpenAPI contract (deterministic, non-LLM).',
  cacheable: true,
  input: z.object({ openapiYaml: z.string(), serviceName: z.string() }),
  output: z.object({ locustfile: z.string(), path: z.string(), taskCount: z.number().int() }),
});

// ---------- security & quality ----------
export const zapBaselineScan = tool({
  name: 'zap_baseline_scan',
  description: 'OWASP ZAP baseline scan of the service endpoints (deterministic simulation in mock mode).',
  input: z.object({ targetUrl: z.string(), openapiYaml: z.string().optional() }),
  output: z.object({
    result: z.enum(['PASS', 'WARN', 'FAIL']),
    high: z.number().int(),
    medium: z.number().int(),
    low: z.number().int(),
    alerts: z.array(z.object({ risk: z.string(), name: z.string(), url: z.string() })),
    reportMarkdown: z.string(),
  }),
});

export const trivyScanImage = tool({
  name: 'trivy_scan_image',
  description: 'Trivy container-image vulnerability scan from a Dockerfile (deterministic simulation in mock mode).',
  cacheable: true,
  input: z.object({ dockerfile: z.string(), imageTag: z.string().default('app:latest') }),
  output: z.object({
    result: z.enum(['PASS', 'FAIL']),
    critical: z.number().int(),
    high: z.number().int(),
    medium: z.number().int(),
    vulnerabilities: z.array(z.object({ id: z.string(), severity: z.string(), pkg: z.string() })),
    reportMarkdown: z.string(),
  }),
});

export const sonarqubeAnalyse = tool({
  name: 'sonarqube_analyse',
  description: 'SonarQube-style static quality analysis of generated code (deterministic simulation in mock mode).',
  input: z.object({ files: z.array(z.object({ path: z.string(), content: z.string() })).min(1) }),
  output: z.object({
    qualityGate: z.enum(['OK', 'WARN', 'ERROR']),
    bugs: z.number().int(),
    vulnerabilities: z.number().int(),
    codeSmells: z.number().int(),
    coveragePct: z.number(),
    duplicationPct: z.number(),
    reportMarkdown: z.string(),
  }),
});

/** Every tool, keyed by MCP tool name. */
export const TOOL_REGISTRY = {
  [jiraCreateEpic.name]: jiraCreateEpic,
  [jiraCreateStory.name]: jiraCreateStory,
  [jiraCreateXrayTest.name]: jiraCreateXrayTest,
  [jiraSearchIssues.name]: jiraSearchIssues,
  [jiraGetIssue.name]: jiraGetIssue,
  [jiraUpdateIssue.name]: jiraUpdateIssue,
  [jiraTransitionIssue.name]: jiraTransitionIssue,
  [jiraAddComment.name]: jiraAddComment,
  [confluencePublishPrd.name]: confluencePublishPrd,
  [confluencePublishHld.name]: confluencePublishHld,
  [confluencePublishLld.name]: confluencePublishLld,
  [githubCommitDiagrams.name]: githubCommitDiagrams,
  [githubCommitLldArtefacts.name]: githubCommitLldArtefacts,
  [githubCommitPipelineConfig.name]: githubCommitPipelineConfig,
  [githubCreateBranch.name]: githubCreateBranch,
  [githubCommitCode.name]: githubCommitCode,
  [githubFetchBuildLogs.name]: githubFetchBuildLogs,
  [githubCommitFix.name]: githubCommitFix,
  [githubPollRunStatus.name]: githubPollRunStatus,
  [githubCreatePullRequest.name]: githubCreatePullRequest,
  [githubCommitIndex.name]: githubCommitIndex,
  [githubReadFiles.name]: githubReadFiles,
  [githubListTree.name]: githubListTree,
  [githubOpenPullRequest.name]: githubOpenPullRequest,
  [spectralLintOpenapi.name]: spectralLintOpenapi,
  [amazonqGenerateCloudcraft.name]: amazonqGenerateCloudcraft,
  [amazonqAnalyseFailure.name]: amazonqAnalyseFailure,
  [ghcopilotGenerateFix.name]: ghcopilotGenerateFix,
  [awsS3PutObject.name]: awsS3PutObject,
  [awsSecretsCheck.name]: awsSecretsCheck,
  [postmanRunCollection.name]: postmanRunCollection,
  [restassuredGenerateTests.name]: restassuredGenerateTests,
  [playwrightGenerateTests.name]: playwrightGenerateTests,
  [playwrightRunTests.name]: playwrightRunTests,
  [k6RunTest.name]: k6RunTest,
  [jmeterGeneratePlan.name]: jmeterGeneratePlan,
  [locustGenerateTest.name]: locustGenerateTest,
  [zapBaselineScan.name]: zapBaselineScan,
  [trivyScanImage.name]: trivyScanImage,
  [sonarqubeAnalyse.name]: sonarqubeAnalyse,
} as const satisfies Record<string, ToolDefinition>;

export type ToolName = keyof typeof TOOL_REGISTRY;

export function getTool(name: string): ToolDefinition {
  const def = (TOOL_REGISTRY as Record<string, ToolDefinition>)[name];
  if (!def) throw new RangeError(`Unknown MCP tool: ${name}`);
  return def;
}
