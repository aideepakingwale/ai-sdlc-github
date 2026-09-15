#!/usr/bin/env bash
# AI observability smoke: spans recorded for LLM + tool executions,
# admin aggregates, RBAC, and the Prometheus text endpoint.
set -uo pipefail
BASE=http://localhost:8080
fail() { echo "OBSERVABILITY SMOKE FAIL: $1"; exit 1; }
jar() { echo "/tmp/ojar_$1"; }
login() { curl -s -c "$(jar $1)" -X POST "$BASE/api/auth/login" -H 'content-type: application/json' \
  -d "{\"email\":\"$1@sdlc.local\",\"password\":\"Password123!\"}" >/dev/null; }
for U in superadmin pm po; do login $U; done

# generate activity: one stage run = LLM spans (planner/agent/fact-check) + MCP tool spans
P=$(curl -s -b "$(jar pm)" -X POST "$BASE/api/projects" -H 'content-type: application/json' \
  -d '{"name":"Observability Probe","techStack":"Go + Gin"}')
PID=$(echo "$P" | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
[ -n "$PID" ] || fail "project not created"
curl -s -b "$(jar pm)" -X POST "$BASE/api/projects/$PID/members" -H 'content-type: application/json' \
  -d '{"email":"po@sdlc.local","role":"PO"}' >/dev/null
S1=$(curl -s -N --max-time 150 -b "$(jar po)" -X POST "$BASE/api/chat" -H 'content-type: application/json' \
  -d "{\"projectId\":\"$PID\",\"message\":\"Build a parcel tracking API\"}")
echo "$S1" | grep -q 'PENDING_REVIEW' || fail "stage 1 run failed"
echo "[0] activity generated (one full stage run)"

# 1. admin summary aggregates LLM + tool spans
SUM=$(curl -s -b "$(jar superadmin)" "$BASE/api/observability/summary?days=1")
LLM=$(echo "$SUM" | grep -o '"llm_calls": *[0-9]*' | head -1 | grep -o '[0-9]*$')
TOOL=$(echo "$SUM" | grep -o '"tool_calls": *[0-9]*' | head -1 | grep -o '[0-9]*$')
[ "${LLM:-0}" -ge 2 ] || fail "expected >=2 llm spans, got '$LLM': $(echo "$SUM" | head -c 300)"
[ "${TOOL:-0}" -ge 2 ] || fail "expected >=2 tool spans, got '$TOOL'"
echo "$SUM" | grep -q '"provider": *"mock"' || fail "provider breakdown missing mock: $SUM"
echo "$SUM" | grep -q '"daily"' || fail "daily series missing"
echo "$SUM" | grep -q 'jira_create_epic' || fail "tool breakdown missing jira_create_epic"
echo "[1] summary: llm=$LLM tool=$TOOL spans, provider + daily + tool breakdowns present"

# 2. trace feed carries attribution (project, stage, tag, latency, status)
TR=$(curl -s -b "$(jar superadmin)" "$BASE/api/observability/traces?limit=50&projectId=$PID")
echo "$TR" | grep -q '"kind":"llm"' || fail "no llm traces: $(echo "$TR" | head -c 300)"
echo "$TR" | grep -q '"kind":"tool"' || fail "no tool traces"
echo "$TR" | grep -q "\"projectId\":\"$PID\"" || fail "project attribution missing"
echo "$TR" | grep -q '"stage":1' || fail "stage attribution missing"
echo "$TR" | grep -q '"tag":"stage1_template1_agent"' || fail "agent tag missing"
echo "$TR" | grep -q '"latencyMs":' || fail "latency missing"
echo "[2] traces: llm+tool spans attributed to project/stage with tags + latency"

# 3. RBAC: only SUPER_ADMIN sees the dashboard APIs
for U in pm po; do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' -b "$(jar $U)" "$BASE/api/observability/summary")
  [ "$CODE" = "403" ] || fail "$U allowed on observability ($CODE)"
done
echo "[3] RBAC: PM and phase roles denied (403), SUPER_ADMIN only"

# 4. Prometheus endpoint for local monitoring servers
M=$(curl -s "$BASE/metrics")
echo "$M" | grep -q '^sdlc_llm_calls_total [0-9]' || fail "metrics missing llm counter: $(echo "$M" | head -c 300)"
echo "$M" | grep -q 'sdlc_llm_tokens_total{direction="prompt"}' || fail "token metric missing"
echo "$M" | grep -q 'sdlc_llm_provider_calls_total{provider="mock"}' || fail "provider metric missing"
echo "$M" | grep -q 'sdlc_llm_latency_ms{stat="p95"}' || fail "p95 metric missing"
echo "[4] /metrics: Prometheus text exposition with calls/tokens/latency/provider series"

echo ""
echo "OBSERVABILITY SMOKE PASS ✅ — spans traced + attributed, admin aggregates, RBAC, Prometheus endpoint"
