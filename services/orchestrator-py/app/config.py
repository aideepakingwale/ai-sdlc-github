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

    # Attachment image understanding. Uploaded images are turned into
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

    # External MCP servers the platform can leverage in addition to the in-house
    # tool-connector. Disabled by default; enable per server and supply
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
    # Governance: external side-effecting writes — Jira tickets, Confluence
    # pages, GitHub doc/design/config commits — are DEFERRED during generation and
    # executed only after the phase's HITL gate is APPROVED, by the approver. Set
    # False to revert to the legacy publish-during-generation behaviour.
    PUBLISH_ON_APPROVAL: bool = True

    # Workflow engine ( fork): v2 adds the data-driven custom phase type and
    # is a backward-compatible superset of v1; set v1 to roll back to the original.
    WORKFLOW_ENGINE: Literal["v1", "v2"] = "v2"

    CONTEXT_TOKEN_THRESHOLD: int = 4_000
    BUILD_LOOP_MAX_ITERATIONS: int = 5
    BUILD_POLL_INTERVAL_MS: int = 30_000
    GITHUB_WEBHOOK_SECRET: str = "dev-webhook-secret"

    AUTH_MODE: Literal["keycloak", "local"] = "local"
    KEYCLOAK_INTERNAL_URL: str | None = None
    KEYCLOAK_PUBLIC_URL: str | None = None
    KEYCLOAK_REALM: str = "sdlc"
    KEYCLOAK_CLIENT_ID: str = "sdlc-orchestrator"
    KEYCLOAK_CLIENT_SECRET: str | None = None
    APP_PUBLIC_URL: str = "http://localhost:3000"

    # RAG: top-k snippets retrieved into every phase-agent prompt.
    RAG_TOP_K: int = 4
    RAG_EMBED_DIM: int = 256

    # Content-store tier for stage artifacts:
    #   filesystem = folder tree on a mounted volume (local); s3 = S3/MinIO (prod).
    CONTENT_STORE_MODE: Literal["filesystem", "s3"] = "filesystem"
    CONTENT_STORE_PATH: str = "/data/content-store"
    CONTENT_BUCKET: str = "sdlc-content-store"
    # Prod hardening. In prod the bucket is pre-provisioned by IaC with
    # Block Public Access, default SSE-KMS, versioning and lifecycle rules, so
    # auto-create stays OFF (the workload role needs no s3:CreateBucket). Set it
    # true only for local/dev against MinIO/LocalStack.
    CONTENT_BUCKET_AUTO_CREATE: bool = False
    # When set, writes request SSE-KMS with this key; otherwise the object
    # inherits the bucket's default encryption (recommended).
    CONTENT_KMS_KEY_ID: str | None = None

    # Validation agent: after a phase generates, validate the output for
    # syntactic correctness (diagrams/structured content) and alignment with the
    # user's intent + amend feedback; on failure re-invoke the phase agent with
    # concrete modification instructions, up to VALIDATION_MAX_REPAIRS times.
    # Set VALIDATION_ENABLED=false to skip (e.g. to conserve free-tier tokens).
    VALIDATION_ENABLED: bool = True
    VALIDATION_MAX_REPAIRS: int = 1


def _load_secrets_manager() -> None:
    """Secret-manager integration: when AWS_SECRETS_MANAGER_SECRET_ID is
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
