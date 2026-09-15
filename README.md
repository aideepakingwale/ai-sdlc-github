# AI-SDLC Platform

Enterprise **AI-powered SDLC automation platform**: a stateful LangGraph pipeline orchestrates six
specialised agents — Product Owner → Solution Architect → Technical Architect → QA Lead → DevOps
Engineer → Developer — from raw requirements to a green CI pipeline and an open pull request, with a
**Human-in-the-Loop gate between every phase**. No phase advances without an explicit, role-checked,
audited human sign-off.

Built from the spec pack in [`specs/`](specs/).

## What's inside

| Service | Role |
|---|---|
| `services/orchestrator-py` | **Python / FastAPI / LangGraph** orchestration hub, layered: API → services → LangGraph graph + phase agents → **RAG** + MCP/LLM integrations → repositories. Gate Controller, Build Recovery Loop, auth/RBAC, guardrails, S3 audit |
| `services/ai-client-service` | LLM-gateway microservice: Groq → Gemini Flash → Grok with a **Redis-backed circuit breaker** (429 backoff → open; 401/402/403 → disabled-for-lifetime) + deterministic mock provider |
| `services/tool-connector-service` | **MCP server** (Streamable HTTP) exposing 19 tools: Jira, Confluence, GitHub, Spectral-style OpenAPI lint, Amazon Q / Copilot personas — live or mock per connector |
| `apps/frontend` | React SPA: chat with live agent activity stream, HITL Gate Dashboard, team management, artifacts, audit trail, MCP skills drawer |
| `packages/shared` | Zod contracts for the TS services + frontend (env, errors, tool registry, API schemas) |

**Layered backend (Python):**

```
app/api          FastAPI routes (auth, chat SSE, gates, projects/members, KB, webhooks)
app/services     authz · gates · chat · build_monitor · audit · guardrails · rag · context
app/graph        LangGraph StateGraph: planner → executor → synthesizer → formatting → fact_check
app/agents       six phase agents + prompts + pydantic output schemas
app/integrations MCP client (tool-connector) · LLM-gateway client
app/repos        Postgres (asyncpg) · DynamoDB · S3 · Redis
```

**RAG:** enterprise standards and every approved artifact are embedded into
`kb_documents`; phase agents retrieve top-k snippets into their prompts, and
`/api/kb/search` queries the same store.

**Persistence:** Postgres (projects/sessions/artifacts/users + append-only audit index), DynamoDB
(PhaseState gate locks, BuildRecoveryTracker), Redis (sessions, tool cache, rate limits, breaker
state), S3/LocalStack (immutable audit log).

## Quickstart (zero API keys needed)

Everything runs locally against **deterministic mocks** (LLM + Jira/Confluence/GitHub + simulated CI):

```bash
cp .env.example .env
./scripts/gen-secret.sh          # generates JWT_SECRET into .env
docker compose up -d --build     # postgres, redis, dynamodb, localstack, 3 services, frontend
docker compose exec orchestrator node dist/db/seed.js   # seed demo users
```

Open **http://localhost:3000** and sign in (password `Password123!` for every demo account).
Identity is managed by **Keycloak** (admin console: http://localhost:8180, `admin`/`admin_local_pw`).

Then walk the golden path:

1. Sign in as **`pm@sdlc.local`** (Project Manager) → create the project and add the six
   phase-role members in the **Team** tab.
2. Sign in as **`po@sdlc.local`** → describe a feature → the **PO agent** creates Jira
   epics/stories + Confluence PRD → gate pauses → **Approve as PO**.
3. Each subsequent phase is driven and signed off by its own role (`sa@`, `ta@`, `qa@`,
   `devops@`, `dev@sdlc.local`) — C4/ADRs, LLD/OpenAPI, tests, CI/CD, code + build loop.
3. Try **Amend** with feedback — the agent regenerates artifacts addressing your comments.
4. In Phase 6 the Developer agent pushes code that intentionally fails CI once: watch the
   **Build Recovery Loop** fetch logs, classify the root cause, commit an AI fix (`fix(ai): iter-1`),
   go green and open the PR — then the final gate.
5. The **Audit** tab shows every generation (provider, model, tokens, artifact hash) and every
   human decision, backed by immutable S3 objects.

## Local development (no Docker for the app code)

```bash
docker compose up -d postgres redis dynamodb localstack
pnpm install
pnpm db:migrate && pnpm seed
pnpm dev        # orchestrator :8080, ai-client :8081, tools :8082, vite :5173
```

## Going live

All integrations switch from mock to live via `.env` alone (`TOOLS_MODE=auto`):

- **LLMs:** set any of `GROQ_API_KEY`, `GEMINI_API_KEY`, `XAI_API_KEY`.
- **Jira/Confluence:** `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `CONFLUENCE_BASE_URL`.
- **GitHub:** `GITHUB_TOKEN`, `GITHUB_REPO` (+ point a `workflow_run` webhook at
  `POST /api/webhooks/github` with `GITHUB_WEBHOOK_SECRET`).

**Cloud:** images are stateless 12-factor containers — push to a registry and map 1:1 onto ECS
Fargate / AKS / GKE; swap `DATABASE_URL`/`REDIS_URL`/`DYNAMO_ENDPOINT`/`S3_ENDPOINT` to managed
services (Aurora, ElastiCache, DynamoDB, S3). No code changes.

## Verification

```bash
pnpm typecheck && pnpm lint && pnpm test && pnpm build   # all green
```

34 unit/integration specs cover: guardrails (PII + prompt injection), scrypt auth, gate RBAC +
optimistic-lock conflicts, AMEND regeneration, circuit-breaker failover (429/401 paths), mock CI
build-recovery (fix → success, and 5-iteration escalation), OpenAPI linter, tool registry
validation + caching, and the deterministic mock LLM.

## Authentication & RBAC (Keycloak)

**Identity:** Keycloak container (realm `sdlc`, auto-imported) is the source of truth for
credentials and roles. The orchestrator is an OIDC **confidential client (BFF)**: the SPA form
uses the password grant, `/api/auth/oidc/login` runs the full Authorization Code SSO flow, and
either way tokens are verified (JWKS) and exchanged **server-side only** — the browser holds an
httpOnly session cookie, never a token. Users are JIT-provisioned into Postgres on first login.
`AUTH_MODE=local` keeps an offline scrypt path for dev/tests.

**Role model:**

| Role | Scope | Authority |
|---|---|---|
| `SUPER_ADMIN` | platform | everything; gate approval only as an audited break-glass override |
| `PROJECT_MANAGER` | platform | create projects, staff teams; **never approves gates** (segregation of duties) |
| `PO` | Phase 01 | Product Owner — Epics & User Stories gate |
| `SA` | Phase 02 | Solution Architect — High-Level Design gate |
| `TA` | Phase 03 | Technical Architect — Low-Level Design gate |
| `QA` | Phase 04 | QA Lead — Test Strategy & Cases gate |
| `DEVOPS` | Phase 05 | DevOps Engineer — CI/CD Pipeline gate |
| `DEV` | Phase 06 | Developer — App Code & Build Loop gate |

**Project scoping:** holding a phase role is necessary but not sufficient. The PM assigns
members to a project (`project_members`); chat/artifacts/audit require membership, and a gate
can only be approved by *that project's* member holding the matching phase role. Membership
role must equal the user's Keycloak role — a PM cannot grant authority the IdP didn't.

## Architecture notes

- **Gate integrity:** DynamoDB conditional writes are the single source of truth for phase
  status; Postgres holds a denormalised read-model. Concurrent APPROVE/AMEND cannot double-fire.
- **Breaker state:** lives in Redis, shared across replicas, surviving restarts.
- **Typed state:** `AgentState`, plans, artifacts and all API bodies are zod schemas in
  `packages/shared`; LLM JSON passes schema validation with one repair retry.
- **Build loop:** webhook-primary, poll-fallback; at most 5 AI fix iterations, then
  `ESCALATED` (project flagged, humans notified via gate state).
- **Audit:** every AI generation and human decision → S3 JSON (immutable) + Postgres index;
  the audit table has a trigger forbidding UPDATE/DELETE.
