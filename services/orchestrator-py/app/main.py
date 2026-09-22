"""AI-SDLC Orchestration Hub — Python/FastAPI/LangGraph.
Composition root: builds every layer once, exposes the REST + SSE API."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from .agents.phase_agents import AgentDeps
from .api import auth_routes, chat_routes, project_routes
from .api.deps import Container
from .auth.keycloak import KeycloakAuth
from .config import get_settings
from .domain.errors import SdlcError
from .domain.models import UserPublic
from .integrations.llm import LlmClient
from .integrations.mcp_client import McpServer, McpToolClient
from .repos.aws import DynamoStore, S3Store
from .repos.pg import Database
from .services.audit import AuditService
from .services.authz import AuthzService
from .services.build_monitor import BuildMonitor
from .services.canon import CanonService
from .services.chat import ChatService
from .services.codebase import CodebaseService
from .services.content_store import build_content_store
from .services.flow import FlowService
from .services.formworks import FormworkService
from .services.gates import GateService
from .services.publisher import PublishService
from .services.rag import RagService
from .services.skills import SkillService
from .services.telemetry import TelemetryService
from .services.workflow import WorkflowService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("orchestrator")

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def _external_mcp_servers(settings) -> list[McpServer]:  # noqa: ANN001
    """Build the list of external MCP servers to leverage alongside the in-house
    tool-connector, from config. GitHub uses a PAT bearer token; Atlassian's
    own container holds its Jira/Confluence credentials."""
    servers: list[McpServer] = []
    if settings.GITHUB_MCP_ENABLED:
        headers = {"Authorization": f"Bearer {settings.GITHUB_MCP_TOKEN}"} if settings.GITHUB_MCP_TOKEN else None
        servers.append(McpServer(name="github", url=settings.GITHUB_MCP_URL, prefix="github", headers=headers))
    if settings.ATLASSIAN_MCP_ENABLED:
        servers.append(McpServer(name="atlassian", url=settings.ATLASSIAN_MCP_URL, prefix="atlassian"))
    return servers


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    container = Container(settings=settings)
    app.state.container = container

    # ---- repository layer ----
    db = Database(settings.DATABASE_URL)
    await db.connect()
    redis = Redis.from_url(settings.REDIS_URL)
    dynamo = DynamoStore(settings)
    s3 = S3Store(settings)
    await dynamo.ensure_tables()
    try:
        await s3.ensure_bucket()
    except Exception as err:
        log.warning("audit bucket ensure failed (retried per write): %s", err)

    # ---- service + integration layers ----
    audit = AuditService(db, s3)
    authz = AuthzService(db)
    keycloak = KeycloakAuth(settings) if settings.AUTH_MODE == "keycloak" else None
    llm = LlmClient(settings.AI_CLIENT_URL)
    mcp = McpToolClient(settings.TOOLS_MCP_URL, extra_servers=_external_mcp_servers(settings))
    for s in mcp._servers[1:]:
        log.info("external MCP server enabled: %s -> %s", s.prefix, s.url)
    rag = RagService(db, settings)
    await rag.ensure_standards()
    content = await build_content_store(settings)
    log.info("content-store tier: %s", content.mode)
    monitor = BuildMonitor(dynamo, redis, mcp, db, audit, settings)
    telemetry = TelemetryService(db) # AI observability
    llm.telemetry = telemetry
    canon = CanonService(db, authz, audit) #
    formworks = FormworkService(db, authz, audit, content, canon)
    agent_deps = AgentDeps(
        llm=llm, mcp=mcp, db=db, audit=audit, rag=rag, content=content, monitor=monitor,
        settings=settings, telemetry=telemetry, canon=canon, formworks=formworks,
    )
    workflow = WorkflowService(db, dynamo, audit)
    # Deferred external publication: external writes are queued during
    # generation and replayed by this service only after the gate is approved.
    publisher = PublishService(db, content, mcp, audit) if settings.PUBLISH_ON_APPROVAL else None
    chat = ChatService(db, redis, dynamo, audit, authz, workflow, agent_deps, settings, publisher)

    async def regenerate(project_id: str, phase: int, reviewer: UserPublic) -> None:
        log.info("amend regeneration started project=%s phase=%s by=%s", project_id, phase, reviewer.email)
        # Version, don't destroy: supersede the phase's current artifacts (kept as
        # history, bodies retained), then regenerate — the new set becomes the
        # latest versions, with prior generations still viewable.
        superseded = await db.supersede_phase_artefacts(project_id, phase)
        if superseded:
            log.info("superseded %s prior artifact(s) as history before regeneration", superseded)
        await chat.handle(
            user=reviewer, project_id=project_id,
            message="Regenerate this phase's artifacts, fully addressing the gate reviewer's feedback.",
            emit=lambda _e: None,
        )

    gates = GateService(db, dynamo, audit, authz, workflow, regenerate, publisher)
    flow = FlowService(db, dynamo, audit, authz, content, workflow, regenerate)
    monitor.start_polling()

    container.db, container.redis, container.dynamo, container.s3 = db, redis, dynamo, s3
    container.audit, container.authz, container.keycloak = audit, authz, keycloak
    container.llm, container.mcp, container.rag = llm, mcp, rag
    container.monitor, container.chat, container.gates = monitor, chat, gates
    container.codebase = CodebaseService(db, rag)
    container.content, container.flow = content, flow
    container.skills = SkillService(db, authz, agent_deps, workflow)
    container.workflow = workflow
    container.telemetry = telemetry
    container.extras["publisher"] = publisher
    container.canon, container.formworks = canon, formworks

    log.info("orchestrator (python/langgraph) ready on :%s", settings.ORCHESTRATOR_PORT)
    yield

    monitor.stop_polling()
    await audit.flush()
    await llm.close()
    if keycloak:
        await keycloak.close()
    await redis.aclose()
    await db.close()


app = FastAPI(title="AI-SDLC Orchestrator", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")


@app.exception_handler(SdlcError)
async def sdlc_error_handler(_request: Request, err: SdlcError) -> JSONResponse:
    return JSONResponse(status_code=err.http_status, content={"error": {"code": err.code, "message": err.message}})


@app.exception_handler(RequestValidationError)
async def validation_handler(_request: Request, err: RequestValidationError) -> JSONResponse:
    message = "; ".join(f"{'.'.join(str(p) for p in e['loc'][1:])}: {e['msg']}" for e in err.errors()[:5])
    return JSONResponse(status_code=400, content={"error": {"code": "VALIDATION_FAILED", "message": message}})


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    container: Container = request.app.state.container
    checks: dict[str, str] = {}
    healthy = True
    for name, probe in (
        ("postgres", container.db.ping),
        ("redis", container.redis.ping),
        ("dynamodb", container.dynamo.ping),
    ):
        try:
            await probe()
            checks[name] = "ok"
        except Exception:
            checks[name] = "down"
            healthy = False
    try:
        await container.s3.ping()
        checks["s3"] = "ok"
    except Exception:
        checks["s3"] = "degraded"
    return JSONResponse(status_code=200 if healthy else 503,
                        content={"status": "ok" if healthy else "degraded", "checks": checks})


app.include_router(auth_routes.router)
app.include_router(chat_routes.router)
app.include_router(project_routes.router)
