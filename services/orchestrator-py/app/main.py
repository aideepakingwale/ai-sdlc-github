"""DevMind Orchestration Hub — Python/FastAPI/LangGraph (D-18).
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
from .api import agents_routes, auth_routes, chat_routes, config_routes, project_routes, rules_routes
from .api.deps import Container
from .auth.keycloak import KeycloakAuth
from .config import get_settings
from .domain.errors import SdlcError
from .domain.models import UserPublic
from .services.model_routes import env_defaults
from .integrations.llm import LlmClient
from .integrations.mcp_client import McpServer, McpToolClient
from .repos.aws import DynamoStore, S3Store
from .repos.pg import Database
from .services.audit import AuditService
from .services.authz import AuthzService
from .services.build_monitor import BuildMonitor
from .services.canon import CanonService
from .services.memory import MemoryService
from .services.connections import ConnectionService
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
    tool-connector (D-61), from config. GitHub uses a PAT bearer token; Atlassian's
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
    llm = LlmClient(
        settings.AI_CLIENT_URL,
        generate_timeout_seconds=settings.LLM_HTTP_TIMEOUT_SECONDS,
        redis=redis,  # D-104: read the runtime llm_debug_trace toggle
        debug_env_default=settings.LLM_DEBUG_TRACE,
        debug_max_chars=settings.LLM_DEBUG_TRACE_MAX_CHARS,
        role_models=env_defaults(settings),
    )
    if env_defaults(settings):
        log.info("model routes from env: %s (an admin's live routes override these)", env_defaults(settings))
    mcp = McpToolClient(settings.TOOLS_MCP_URL, extra_servers=_external_mcp_servers(settings))
    _targets: dict[str, tuple[float, dict[str, str]]] = {}

    async def _project_target(project_id: str) -> dict[str, str]:
        """Where THIS project publishes (its repository, Confluence space, Jira project). Short cache: it is read per tool call."""
        import time as _t
        hit = _targets.get(project_id)
        if hit and _t.monotonic() - hit[0] < 30:
            return hit[1]
        p = await db.get_project(project_id)
        target = {k: v for k, v in {"githubRepo": (p or {}).get("github_repo"), "confluenceSpaceKey": (p or {}).get("confluence_space_key"),
                                     "jiraProjectKey": (p or {}).get("jira_project_key")}.items() if v}
        _targets[project_id] = (_t.monotonic(), target)
        return target

    from .services.telemetry import _run_context as _rc
    mcp.bind_targets(_project_target, lambda: (_rc.get() or {}).get("projectId"))
    mcp.bind_credentials(lambda pid: connections.credentials_for(pid))
    for s in mcp._servers[1:]:
        log.info("external MCP server enabled: %s -> %s", s.prefix, s.url)
    rag = RagService(db, settings)
    connections = ConnectionService(db, authz, audit, settings, rag)
    await rag.ensure_standards()
    content = await build_content_store(settings)
    log.info("content-store tier: %s", content.mode)
    monitor = BuildMonitor(dynamo, redis, mcp, db, audit, settings)
    telemetry = TelemetryService(db)  # AI observability (D-35)
    llm.telemetry = telemetry
    canon = CanonService(db, authz, audit)                      # D-38
    formworks = FormworkService(db, authz, audit, content, canon)
    memory = MemoryService(db, authz, audit)
    from .services.project_config import ProjectConfigService
    from .services.stack_advisor import StackAdvisor
    project_config = ProjectConfigService(db, content, authz, audit, canon)   # projectconfig.json
    stack_advisor = StackAdvisor(llm, project_config, db)
    from .services.rule_assist import RuleAssist
    from .services.rule_checks import RuleChecker
    rule_assist = RuleAssist(llm, canon, project_config)
    from .services.pack_admin import PackAdmin
    from .services.rule_advice import RuleAdvisor
    pack_admin = PackAdmin(db, audit, canon.catalog)
    rule_advisor = RuleAdvisor(canon, project_config)
    rule_checker = RuleChecker(db, content, llm, canon, settings)
    from .repos.agent_repo import AgentRepo
    from .services.agent_audit import AgentAuditor
    from .services.agent_defs import AgentDefService
    from .services.agent_runtime import AgentRuntime
    from .services.agent_usage import AgentUsage
    agent_repo = AgentRepo(db)
    agent_usage = AgentUsage(agent_repo)
    agent_runtime = AgentRuntime(llm, audit, agent_usage)
    agent_deps = AgentDeps(
        llm=llm, mcp=mcp, db=db, audit=audit, rag=rag, content=content, monitor=monitor,
        settings=settings, telemetry=telemetry, canon=canon, formworks=formworks, memory=memory,
        project_config=project_config, stack_advisor=stack_advisor, rule_checker=rule_checker, agent_runtime=agent_runtime,
    )
    workflow = WorkflowService(db, dynamo, audit)
    stack_advisor.workflow = workflow
    # Deferred external publication (D-67): external writes are queued during
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

    # Two-step code generation: structure approval -> implementation -> commit on code approval.
    async def _enqueue_generation(project_id: str, phase: int, actor: str):  # noqa: ANN202
        return await container.gen_jobs.enqueue(project_id, phase, actor)

    from .services.code_gen import CodeGenService
    code_gen = CodeGenService(db, dynamo, audit, authz, content, workflow, _enqueue_generation, chat.can_write_stage,
                              enabled=getattr(settings, "CODE_TWO_STEP_ENABLED", True))
    gates = GateService(db, dynamo, audit, authz, workflow, regenerate, publisher, code_gen, settings=get_settings())
    flow = FlowService(db, dynamo, audit, authz, content, workflow, regenerate)
    monitor.start_polling()

    container.db, container.redis, container.dynamo, container.s3 = db, redis, dynamo, s3
    container.audit, container.authz, container.keycloak = audit, authz, keycloak
    container.llm, container.mcp, container.rag = llm, mcp, rag
    container.monitor, container.chat, container.gates = monitor, chat, gates
    container.codebase = CodebaseService(db, rag)
    container.content, container.flow = content, flow
    container.skills = SkillService(db, authz, agent_deps, workflow)
    from .services.skills import SKILL_PACKS
    agent_defs = AgentDefService(db, agent_repo, authz, audit, AgentAuditor(llm, agent_runtime), agent_runtime, canon=canon, usage=agent_usage, llm=llm,
                                 project_config=project_config, workflow=workflow, skill_packs=lambda: SKILL_PACKS)
    container.agent_defs = agent_defs
    from .services.agent_runs import AgentRunService
    container.agent_runs = AgentRunService(agent_defs, agent_repo, db, dynamo, workflow, chat, agent_deps, audit)
    container.skills.custom = agent_defs
    chat.agent_defs = agent_defs
    container.workflow = workflow
    container.telemetry = telemetry
    container.extras["publisher"] = publisher
    container.code_gen = code_gen
    container.canon, container.formworks = canon, formworks
    container.memory = memory
    container.project_config, container.stack_advisor = project_config, stack_advisor
    container.rule_assist, container.rule_checker = rule_assist, rule_checker
    container.pack_admin, container.rule_advisor = pack_admin, rule_advisor
    from .services.code_edit import CodeEditService
    container.code_edit = CodeEditService(db, content, rag, audit, authz, llm, chat, workflow, dynamo, canon=canon, memory=memory)
    container.connections = connections
    gates.memory = memory
    # Durable background stage generation (D-97 L2): jobs survive disconnects, are
    # recorded across restarts, and stream reconnectable progress via Redis.
    from .services.generation_jobs import GenerationJobs

    async def _run_stage(project_id: str, phase: int, actor_email: str, emit) -> None:  # noqa: ANN001
        urow = await db.get_user_by_email(actor_email)
        if not urow:
            raise SdlcError("NOT_FOUND", f"generation actor '{actor_email}' not found")
        actor = UserPublic(id=urow["id"], email=urow["email"], displayName=urow["display_name"], role=urow["role"])
        try:
            await chat.trigger_stage(project_id=project_id, phase=phase, user=actor, emit=emit)
        except Exception:
            # Safety net (D-111): never leave the stage stuck IN_PROGRESS when the job
            # fails for any reason — reset it so it's re-triggerable, then re-raise so
            # the job is still recorded failed and the error reaches the stream.
            await chat.reset_stage_if_in_progress(project_id, phase)
            raise

    container.gen_jobs = GenerationJobs(db, redis, runner=_run_stage)
    _reconciled = await container.gen_jobs.reconcile()
    if _reconciled:
        log.info("reconciled %s stale generation job(s) on boot", _reconciled)
    container.gen_jobs.start_workers(getattr(settings, "GENERATION_WORKERS", 2))

    # D-91/D-92: mirror persisted LLM settings to Redis so ai-client picks them up
    # after a restart (Redis is the cross-service channel).
    try:
        from .api.project_routes import LLM_SETTING_KEYS  # single source of truth

        stored = await db.list_settings(list(LLM_SETTING_KEYS))
        for k in LLM_SETTING_KEYS:
            rk = f"sdlc:settings:{k}"
            if stored.get(k):
                await redis.set(rk, stored[k])
            else:
                await redis.delete(rk)
        if stored:
            log.info("LLM settings mirrored to Redis: %s", sorted(stored.keys()))
    except Exception as err:  # table may not exist yet on a fresh DB pre-migrate
        log.warning("LLM settings mirror skipped: %s", err)

    log.info("orchestrator (python/langgraph) ready on :%s", settings.ORCHESTRATOR_PORT)
    yield

    monitor.stop_polling()
    if container.gen_jobs is not None:
        await container.gen_jobs.stop_workers()
    await audit.flush()
    await llm.close()
    if keycloak:
        await keycloak.close()
    await redis.aclose()
    await db.close()


app = FastAPI(title="DevMind Orchestrator", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")


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
app.include_router(config_routes.router)
app.include_router(rules_routes.router)
app.include_router(agents_routes.router)
