"""Environment configuration — single validated source (mirrors the platform env contract)."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    NODE_ENV: Literal["development", "test", "production"] = "development"
    LOG_LEVEL: str = "info"

    ORCHESTRATOR_PORT: int = 8080
    DATABASE_URL: str
    REDIS_URL: str = "redis://localhost:6379"

    DYNAMO_ENDPOINT: str
    AWS_REGION: str = "eu-west-2"
    AWS_ACCESS_KEY_ID: str = "local"
    AWS_SECRET_ACCESS_KEY: str = "local"
    S3_ENDPOINT: str | None = None
    AUDIT_BUCKET: str = "sdlc-audit-logs"

    JWT_SECRET: str
    SESSION_TTL_HOURS: int = 24

    AI_CLIENT_URL: str
    TOOLS_MCP_URL: str
    # Read timeout (seconds) for the orchestrator's generate call to the ai-client
    # gateway. Generation is a decoupled background job (D-99) — nobody is waiting on
    # this HTTP call — so it just has to OUTLAST the gateway's own provider streaming
    # ceiling (LLM_STREAM_TIMEOUT_MS, 1h) and never drop the connection first, which
    # the gateway would read as a client abort and cancel the Bedrock stream (D-102).
    # Default = 1h + 2min buffer. Raise this and LLM_STREAM_TIMEOUT_MS together for
    # artifacts that legitimately need longer.
    LLM_HTTP_TIMEOUT_SECONDS: float = 3720.0

    # Attachment image understanding (D-66). Uploaded images are turned into
    # context two ways: a vision LLM (routed multi-model through ai-client:
    # Bedrock Claude → Gemini) transcribes + describes them, and deterministic
    # OCR (pytesseract) is the offline fallback.
    #   auto → try the vision LLM; fall back to OCR if none is available/served
    #   llm  → vision LLM only (still falls back to OCR if the call fails)
    #   ocr  → deterministic OCR only, never call the LLM
    ATTACHMENT_VISION: Literal["auto", "llm", "ocr"] = "auto"
    # Longest edge (px) images are downscaled to before sending to the vision
    # model — caps the request payload and token cost without hurting legibility.
    ATTACHMENT_VISION_MAX_EDGE: int = 1536

    # Document analysis (PDF / Word / PowerPoint / Excel / diagrams). Every bound
    # exists so one hostile or enormous upload cannot exhaust the server.
    ATTACHMENT_MAX_BYTES: int = 50_000_000       # per file (nginx client_max_body_size must be >= this)
    ATTACHMENT_MAX_PAGES: int = 200              # pages / slides read per document
    ATTACHMENT_MAX_FIGURES: int = 24             # pictures / diagram pages sent to the vision model per document
    ATTACHMENT_FIGURE_CONCURRENCY: int = 3       # parallel vision calls per upload
    ATTACHMENT_PARSE_SECONDS: int = 90           # wall-clock budget for parsing one file
    ATTACHMENT_ANALYSIS_SECONDS: int = 120       # wall-clock budget for describing its figures
    ATTACHMENT_STORE_CHARS: int = 300_000        # extracted Markdown kept per attachment
    # Render slides / diagram pages to images for the vision model through
    # LibreOffice (when installed). 'off' keeps the structural text extraction only.
    ATTACHMENT_RENDER_PAGES: Literal["auto", "off"] = "auto"
    # Budget (characters) for ALL attached material in one stage prompt. Large
    # documents are condensed section by section to fit - never cut off after page 1.
    ATTACHMENT_CONTEXT_CHARS: int = 80_000

    # External MCP servers the platform can leverage in addition to the in-house
    # tool-connector (D-61). Disabled by default; enable per server and supply
    # credentials in .env. Their tools are exposed namespaced (github.* / atlassian.*).
    #   GitHub — github/github-mcp-server (hosted remote by default; a PAT bearer token).
    #   Atlassian — sooperset/mcp-atlassian (Jira + Confluence), run with streamable-http.
    GITHUB_MCP_ENABLED: bool = False
    GITHUB_MCP_URL: str = "https://api.githubcopilot.com/mcp/"
    GITHUB_MCP_TOKEN: str | None = None  # GitHub PAT; paste into .env, never in code
    ATLASSIAN_MCP_ENABLED: bool = False
    ATLASSIAN_MCP_URL: str = "http://mcp-atlassian:9000/mcp"

    # Compress the context window once it exceeds this many tokens. Kept modest
    # so the assembled phase prompt (context + instructions + completion) fits a
    # free-tier provider's per-request token ceiling (e.g. Groq's 12k TPM) and
    # real generation succeeds instead of 413-ing into the mock fallback. Raise
    # it when using a higher-tier key (Bedrock/paid) to feed agents more context.
    # Governance (D-67): external side-effecting writes — Jira tickets, Confluence
    # pages, GitHub doc/design/config commits — are DEFERRED during generation and
    # executed only after the phase's HITL gate is APPROVED, by the approver. Set
    # False to revert to the legacy publish-during-generation behaviour.
    PUBLISH_ON_APPROVAL: bool = True

    # Workflow engine (D-73 fork): v2 adds the data-driven custom phase type and
    # is a backward-compatible superset of v1; set v1 to roll back to the original.
    WORKFLOW_ENGINE: Literal["v1", "v2"] = "v2"

    CONTEXT_TOKEN_THRESHOLD: int = 4_000
    # Max OUTPUT tokens for a phase agent's structured generation (D-95). The
    # built-in phases (esp. Solution Architect) emit several large artifacts in one
    # schema (HLD/ADR/DSL/JSON/diagram); the old 8192 default truncated them, so the
    # JSON failed to parse and the stage produced nothing. Sonnet 4.x supports far
    # more — raise this if a phase still truncates (watch cost/latency).
    PHASE_MAX_TOKENS: int = 16_000
    # Per-artifact parallel generation (D-98 v1): generate each top-level output of a
    # phase in its own call — anchor first (warms the cached prefix), the rest in
    # parallel — then assemble. Focused prompts + no shared truncation + per-field
    # repair. Falls back to the single combined call on any failure. Opt-in: validate
    # output quality on real Bedrock before enabling in production.
    PER_ARTIFACT_GENERATION: bool = False
    PER_ARTIFACT_MAX_PARALLEL: int = 4
    # Number of background workers consuming the stage-generation queue (D-99).
    # Bounds how many stage runs execute concurrently across the platform.
    GENERATION_WORKERS: int = 4
    # Role-based model routing (latency). Small judging / summarising calls - the
    # validator, fact-check, clarification check, trait detection and context
    # compression - do not need the large generation model; pin them to a fast one.
    # Value: 'provider/model', e.g. 'bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0'.
    # Empty = use the normal chain (no change). A failing light model is bypassed for
    # a few minutes and the call falls back to the normal chain, so a wrong id degrades
    # to the old speed instead of breaking runs.
    LIGHT_MODEL: str = ""
    # Model for the stage planner (the 'Review plan' proposal). Empty = normal chain.
    PLAN_MODEL: str = ""
    BUILD_LOOP_MAX_ITERATIONS: int = 5
    BUILD_POLL_INTERVAL_MS: int = 30_000
    GITHUB_WEBHOOK_SECRET: str = "dev-webhook-secret"

    # Live container-log viewer (D-92): URL of the read-only docker-socket-proxy
    # (tecnativa/docker-socket-proxy, CONTAINERS=1/POST=0). Empty = logs disabled.
    # The orchestrator never mounts the raw Docker socket — only the proxy does.
    DOCKER_PROXY_URL: str = ""

    AUTH_MODE: Literal["keycloak", "local"] = "local"
    KEYCLOAK_INTERNAL_URL: str | None = None
    KEYCLOAK_PUBLIC_URL: str | None = None
    KEYCLOAK_REALM: str = "sdlc"
    KEYCLOAK_CLIENT_ID: str = "sdlc-orchestrator"
    KEYCLOAK_CLIENT_SECRET: str | None = None
    APP_PUBLIC_URL: str = "http://localhost:3000"

    # RAG (D-19): top-k snippets retrieved into every phase-agent prompt.
    RAG_TOP_K: int = 4
    RAG_EMBED_DIM: int = 256

    # Debug tracing (D-104): when enabled, every LLM span also stores the actual
    # request (assembled messages) and response (content) in llm_traces, viewable
    # in the Observability panel. Off by default; the effective switch is the
    # runtime `llm_debug_trace` setting (Super-Admin toggle, mirrored via Redis) OR
    # this env baseline. Bodies are capped to keep the DB/table light.
    LLM_DEBUG_TRACE: bool = False
    LLM_DEBUG_TRACE_MAX_CHARS: int = 200_000

    # Content-store tier for stage artifacts (D-23):
    #   filesystem = folder tree on a mounted volume (local); s3 = S3/MinIO (prod).
    CONTENT_STORE_MODE: Literal["filesystem", "s3"] = "filesystem"
    CONTENT_STORE_PATH: str = "/data/content-store"
    CONTENT_BUCKET: str = "sdlc-content-store"
    # Prod hardening (D-59). In prod the bucket is pre-provisioned by IaC with
    # Block Public Access, default SSE-KMS, versioning and lifecycle rules, so
    # auto-create stays OFF (the workload role needs no s3:CreateBucket). Set it
    # true only for local/dev against MinIO/LocalStack.
    CONTENT_BUCKET_AUTO_CREATE: bool = False
    # When set, writes request SSE-KMS with this key; otherwise the object
    # inherits the bucket's default encryption (recommended).
    CONTENT_KMS_KEY_ID: str | None = None

    # Validation agent (D-52): after a phase generates, validate the output for
    # syntactic correctness (diagrams/structured content) and alignment with the
    # user's intent + amend feedback; on failure re-invoke the phase agent with
    # concrete modification instructions, up to VALIDATION_MAX_REPAIRS times.
    # Set VALIDATION_ENABLED=false to skip (e.g. to conserve free-tier tokens).
    VALIDATION_ENABLED: bool = True
    # Ambiguity pre-check (#1): when a stage is triggered with no curated plan and
    # the inputs are ambiguous, ask clarifying questions (written into the plan
    # overlay for the reviewer to answer) instead of assuming and generating.
    # Intelligent stage planning (D-105): when on, the Review-plan preview is built
    # by an LLM planner from the full context (input, tech stack, project profile,
    # canon, prior artifacts, available+configured tools/skills/outputs, persona) —
    # producing a tailored approach, per-step rationale + tier, and tool/skill
    # recommendations. Advisory (doesn't change the generated schema). Cached until
    # the inputs change; falls back to the deterministic plan on any planner failure
    # (incl. mock mode). Set False to force the deterministic plan.
    INTELLIGENT_PLANNING: bool = True
    # Output budget for the stage planner's proposal (understood / willProduce /
    # format / recommendation + steps + tool/skill/assumption/risk lists). Too small
    # truncates the JSON and the whole proposal is lost, so this is generous and
    # tunable: raise it for stages with many outputs/tools (D-112).
    PLAN_MAX_TOKENS: int = 8_000
    CLARIFY_ENABLED: bool = True
    CLARIFY_MAX_QUESTIONS: int = 6
    # Requirement analysis is the most crucial stage; allow a deeper holistic
    # elicitation sweep (incl. compliance/legal/data-privacy gaps) to ask more.
    CLARIFY_MAX_QUESTIONS_REQUIREMENTS: int = 10
    # AI quality validator: overall score (0-100) below this floor is flagged for
    # the human reviewer (and drives rework within the repair budget).
    QUALITY_MIN_SCORE: int = 70
    # Coverage + lint quality gate: the code, test and CI/CD stages must target at
    # least this unit-test coverage and produce lint-clean code, and the generated
    # pipeline must BLOCK below it. Enforced in the prompts and checked by the
    # validator. Set QUALITY_GATE_ENABLED=false to disable the gate entirely.
    QUALITY_GATE_ENABLED: bool = True
    COVERAGE_MIN_PERCENT: int = 80
    LINT_REQUIRED: bool = True
    # Optional override for the project-creation technology catalog (language →
    # version → frameworks). Point at a JSON file to reconfigure without a
    # rebuild; unset uses the built-in default. Hot-reloaded on mtime change.
    TECH_CATALOG_PATH: str | None = None
    VALIDATION_MAX_REPAIRS: int = 1
    # When the validator flags specific artifacts, rework ONLY those (the others are reused
    # from the first run's saved parts) instead of regenerating the whole stage. Falls back
    # to a full regeneration when the issues cannot be localised.
    VALIDATION_LOCALISED_REWORK: bool = True


def _load_secrets_manager() -> None:
    """Secret-manager integration (D-22): when AWS_SECRETS_MANAGER_SECRET_ID is
    set, fetch that secret (a JSON object of env overrides) and inject any keys
    not already present in the environment BEFORE settings parse. In EKS/ECS
    pair this with an IAM task/pod role — no static AWS keys required."""
    import json
    import os

    secret_id = os.environ.get("AWS_SECRETS_MANAGER_SECRET_ID")
    if not secret_id:
        return
    import boto3

    client = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "eu-west-2"))
    payload = client.get_secret_value(SecretId=secret_id)["SecretString"]
    for key, value in json.loads(payload).items():
        os.environ.setdefault(key, str(value))


@lru_cache
def get_settings() -> Settings:
    _load_secrets_manager()
    return Settings()  # type: ignore[call-arg]
