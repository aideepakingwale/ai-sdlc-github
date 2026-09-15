#!/usr/bin/env bash
# Run visualizer smoke: plan-preview endpoint + plan SSE event.
set -uo pipefail
BASE=http://localhost:8080
fail() { echo "VIZ SMOKE FAIL: $1"; exit 1; }
jar() { echo "/tmp/vjar_$1"; }
login() { curl -s -c "$(jar $1)" -X POST "$BASE/api/auth/login" -H 'content-type: application/json' \
  -d "{\"email\":\"$1@sdlc.local\",\"password\":\"Password123!\"}" >/dev/null; }
for U in pm po qa; do login $U; done

# 1. project on default workflow → preview shows stage 1 plan with steps, tools, skills, tier
P=$(curl -s -b "$(jar pm)" -X POST "$BASE/api/projects" -H 'content-type: application/json' \
  -d '{"name":"Viz Probe","techStack":"Go + Gin"}')
PID=$(echo "$P" | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
[ -n "$PID" ] || fail "project not created: $P"
curl -s -b "$(jar pm)" -X POST "$BASE/api/projects/$PID/members" -H 'content-type: application/json' \
  -d '{"email":"po@sdlc.local","role":"PO"}' >/dev/null

PV=$(curl -s -b "$(jar pm)" "$BASE/api/projects/$PID/plan-preview?message=Build%20a%20billing%20API")
echo "$PV" | grep -q '"nodes":\["guardrail","planner","executor","synthesizer","formatter","fact_check"\]' \
  || fail "node path missing: $(echo "$PV" | head -c 300)"
echo "$PV" | grep -q '"expectedTools":\["jira_create_epic","jira_create_story","confluence_publish_prd"\]' \
  || fail "expected tools missing: $PV"
echo "$PV" | grep -q '"tier":"frontier"' || fail "tier missing: $PV"
echo "$PV" | grep -q '"id":"generate"' || fail "generate step missing"
echo "$PV" | grep -q '"id":"gate"' || fail "gate step missing"
echo "$PV" | grep -q '"id":"estimate_points"' || fail "stage skill missing"
echo "$PV" | grep -q '"id":"kb_search"' || fail "global skill missing"
echo "[1] plan-preview: node path + steps + expected tools + skills + tier"

# 2. RBAC: non-member cannot preview
DENY=$(curl -s -b "$(jar qa)" -o /dev/null -w '%{http_code}' "$BASE/api/projects/$PID/plan-preview")
[ "$DENY" = "403" ] || fail "non-member preview allowed (got $DENY)"
echo "[2] plan-preview RBAC: non-member 403"

# 3. chat run emits the plan SSE event before execution
S1=$(curl -s -N --max-time 150 -b "$(jar po)" -X POST "$BASE/api/chat" -H 'content-type: application/json' \
  -d "{\"projectId\":\"$PID\",\"message\":\"Build a billing API with invoicing\"}")
echo "$S1" | grep -q '"type":"plan"' || fail "plan event not emitted: $(echo "$S1" | head -c 300)"
PLANLINE=$(echo "$S1" | grep '"type":"plan"' | head -1)
echo "$PLANLINE" | grep -q '"expectedTools":\["jira_create_epic"' || fail "plan event tools: $PLANLINE"
echo "$PLANLINE" | grep -q '"nodes":\["guardrail"' || fail "plan event nodes: $PLANLINE"
echo "$S1" | grep -q '"type":"tool_call".*"tool":"jira_create_epic".*"status":"success"' \
  || echo "$S1" | grep -q '"tool":"jira_create_epic"' || fail "planned tool never called"
echo "[3] chat emits plan event (steps+tools+nodes) and the planned tools then execute"

# 4. after the run, preview reports the pending gate instead of runnable stages
PV2=$(curl -s -b "$(jar pm)" "$BASE/api/projects/$PID/plan-preview")
echo "$PV2" | grep -q '"stages":\[\]' || fail "stages should be empty while gate pending: $(echo "$PV2" | head -c 300)"
echo "$PV2" | grep -q '"pendingGates":\[{"seq":1' || fail "pending gate missing: $PV2"
echo "[4] preview reflects gate state: nothing runnable, pending gate listed"

# 5. status fast-path plans as non_llm with no tools (fresh project: no gate blocking)
P2J=$(curl -s -b "$(jar pm)" -X POST "$BASE/api/projects" -H 'content-type: application/json' \
  -d '{"name":"Viz Status Probe","techStack":"Go + Gin"}')
PID2=$(echo "$P2J" | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
S2=$(curl -s -N --max-time 60 -b "$(jar pm)" -X POST "$BASE/api/chat" -H 'content-type: application/json' \
  -d "{\"projectId\":\"$PID2\",\"message\":\"status\"}")
P2=$(echo "$S2" | grep '"type":"plan"' | head -1)
echo "$P2" | grep -q '"tier":"non_llm"' || fail "status plan not non_llm: $P2"
echo "$P2" | grep -q '"expectedTools":\[\]' || fail "status plan should expect no tools: $P2"
echo "[5] status fast-path: non-LLM plan, no tools"

echo ""
echo "VIZ SMOKE PASS ✅ — plan-preview endpoint (steps/tools/skills/tier/RBAC) + live plan SSE event"
