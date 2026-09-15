import { z } from 'zod';

/** "true"/"1"/"yes" → true; anything else → false. */
const boolString = z
  .string()
  .default('false')
  .transform((v) => ['true', '1', 'yes'].includes(v.toLowerCase()));

const common = {
  NODE_ENV: z.enum(['development', 'test', 'production']).default('development'),
  LOG_LEVEL: z.enum(['trace', 'debug', 'info', 'warn', 'error']).default('info'),
  REDIS_URL: z.string().url().default('redis://localhost:6379'),
};

export const OrchestratorEnvSchema = z.object({
  ...common,
  ORCHESTRATOR_PORT: z.coerce.number().int().default(8080),
  DATABASE_URL: z.string().min(1),
  DYNAMO_ENDPOINT: z.string().url(),
  AWS_REGION: z.string().default('eu-west-2'),
  AWS_ACCESS_KEY_ID: z.string().default('local'),
  AWS_SECRET_ACCESS_KEY: z.string().default('local'),
  S3_ENDPOINT: z.string().url().optional(),
  AUDIT_BUCKET: z.string().default('sdlc-audit-logs'),
  JWT_SECRET: z.string().min(32),
  SESSION_TTL_HOURS: z.coerce.number().int().positive().default(24),
  AI_CLIENT_URL: z.string().url(),
  TOOLS_MCP_URL: z.string().url(),
  CONTEXT_TOKEN_THRESHOLD: z.coerce.number().int().positive().default(12_000),
  BUILD_LOOP_MAX_ITERATIONS: z.coerce.number().int().positive().default(5),
  BUILD_POLL_INTERVAL_MS: z.coerce.number().int().positive().default(30_000),
  GITHUB_WEBHOOK_SECRET: z.string().default('dev-webhook-secret'),
  // --- AuthN: keycloak = OIDC BFF against the Keycloak container;
  //     local = scrypt hashes in Postgres (offline dev / unit tests). ---
  AUTH_MODE: z.enum(['keycloak', 'local']).default('local'),
  /** Backchannel base URL (compose-internal), e.g. http://keycloak:8080 */
  KEYCLOAK_INTERNAL_URL: z.string().url().optional(),
  /** Browser-facing base URL, e.g. http://localhost:8180 */
  KEYCLOAK_PUBLIC_URL: z.string().url().optional(),
  KEYCLOAK_REALM: z.string().default('sdlc'),
  KEYCLOAK_CLIENT_ID: z.string().default('sdlc-orchestrator'),
  KEYCLOAK_CLIENT_SECRET: z.string().optional(),
  /** Where the code-flow callback redirects the browser after session issue. */
  APP_PUBLIC_URL: z.string().url().default('http://localhost:3000'),
});
export type OrchestratorEnv = z.infer<typeof OrchestratorEnvSchema>;

export const AiClientEnvSchema = z.object({
  ...common,
  AI_CLIENT_PORT: z.coerce.number().int().default(8081),
  // Master generation-mode switch:
  //   mock — always the deterministic offline generator (demos, tests).
  //   llm  — real models only; boot FAILS if no provider is configured and
  //          the deterministic mock is NEVER used as a silent fallback.
  //   auto — real when a provider is configured, else mock (default; back-compat).
  // Multimodel: within `llm`/`auto`, every provider whose credentials exist is
  // used, routed by task tier/intent (Bedrock → Groq → Gemini → Grok → local).
  GENERATION_MODE: z.enum(['mock', 'llm', 'auto']).default('auto'),
  // Per-request token budget (prompt + completion) for OpenAI-compatible
  // providers (Groq/Grok/local). The completion is capped so a large prompt
  // fits under a provider's tokens-per-minute ceiling instead of a 413 that
  // silently degrades to mock. Default 12000 matches Groq's free tier; raise
  // it for paid tiers, or set 0 to disable capping.
  LLM_REQUEST_TOKEN_BUDGET: z.coerce.number().int().nonnegative().default(12_000),
  // AWS Bedrock (cloud deployment): opt-in via BEDROCK_MODEL_ID. Auth is the
  // standard AWS credential chain (task role / IRSA / env keys) — no API key.
  BEDROCK_MODEL_ID: z.string().optional(),
  BEDROCK_REGION: z.string().optional(),
  GROQ_API_KEY: z.string().optional(),
  GROQ_MODEL: z.string().default('llama-3.3-70b-versatile'),
  GEMINI_API_KEY: z.string().optional(),
  GEMINI_MODEL: z.string().default('gemini-2.0-flash'),
  XAI_API_KEY: z.string().optional(),
  XAI_MODEL: z.string().default('grok-2-latest'),
  // Local lightweight model: Ollama / vLLM OpenAI-compatible endpoint.
  LOCAL_LLM_BASE_URL: z.string().optional(),
  LOCAL_LLM_MODEL: z.string().default('qwen2.5:3b-instruct'),
  LOCAL_LLM_API_KEY: z.string().default('ollama'),
  LLM_FORCE_MOCK: boolString,
});
export type AiClientEnv = z.infer<typeof AiClientEnvSchema>;

export const ToolsEnvSchema = z.object({
  ...common,
  TOOLS_PORT: z.coerce.number().int().default(8082),
  TOOLS_MODE: z.enum(['auto', 'mock', 'live']).default('auto'),
  TOOL_CACHE_TTL_SECONDS: z.coerce.number().int().positive().default(86_400),
  AI_CLIENT_URL: z.string().url(),
  JIRA_BASE_URL: z.string().optional(),
  JIRA_EMAIL: z.string().optional(),
  JIRA_API_TOKEN: z.string().optional(),
  JIRA_PROJECT_KEY: z.string().default('SDLC'),
  CONFLUENCE_BASE_URL: z.string().optional(),
  CONFLUENCE_SPACE_KEY: z.string().default('SDLC'),
  GITHUB_TOKEN: z.string().optional(),
  GITHUB_REPO: z.string().optional(),
  // opt-in switch for LIVE AWS tools (S3 store + Secrets Manager check);
  // unset = deterministic simulated engines. Auth via the AWS credential chain.
  TOOLS_AWS_S3_BUCKET: z.string().optional(),
});
export type ToolsEnv = z.infer<typeof ToolsEnvSchema>;

/**
 * Validate process.env against a schema. Throws with EVERY missing/invalid var
 * listed so a misconfigured container fails fast with a actionable message.
 */
export function loadEnv<S extends z.ZodTypeAny>(schema: S, source: NodeJS.ProcessEnv = process.env): z.infer<S> {
  const result = schema.safeParse(source);
  if (!result.success) {
    const lines = result.error.issues.map((i) => `  - ${i.path.join('.')}: ${i.message}`);
    throw new Error(`Environment validation failed:\n${lines.join('\n')}`);
  }
  return result.data as z.infer<S>;
}
