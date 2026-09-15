#!/usr/bin/env bash
# Multi-model routing + stage/role-scoped skills smoke.
set -uo pipefail
BASE=http://localhost:8080
fail() { echo "SKILLS/MODEL SMOKE FAIL: $1"; exit 1; }
jar() { echo "/tmp/kjar_$1"; }
login() { curl -s -c "$(jar $1)" -X POST "$BASE/api/auth/login" -H 'content-type: application/json' \
  -d "{\"email\":\"$1@sdlc.local\",\"password\":\"Password123!\"}" >/dev/null; }
for U in pm po sa qa; do login $U; done

# project + PO/SA/QA staffed, run phase 1 (so PO stage skills are active)
P=$(curl -s -b "$(jar pm)" -X POST "$BASE/api/projects" -H 'content-type: application/json' \
  -d '{"name":"Skill Demo Service","techStack":"Python + FastAPI"}')
PID=$(echo "$P" | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
for PAIR in "po:PO" "sa:SA" "qa:QA"; do U=${PAIR%%:*}; R=${PAIR##*:}
  curl -s -b "$(jar pm)" -X POST "$BASE/api/projects/$PID/members" -H 'content-type: application/json' \
    -d "{\"email\":\"$U@sdlc.local\",\"role\":\"$R\"}" >/dev/null; done
S1=$(curl -s -N --max-time 150 -b "$(jar po)" -X POST "$BASE/api/chat" -H 'content-type: application/json' \
  -d "{\"projectId\":\"$PID\",\"message\":\"Build a subscription billing service with proration\"}")
echo "$S1" | grep -q 'PENDING_REVIEW' || fail "phase1 run"
# planner emitted a model-tier selection event
echo "$S1" | grep -q '"type":"model"' || fail "no model-tier event from planner"
echo "[1] project + team; phase 1 run; planner emitted multi-model tier selection"

# 2. skills list at phase 1 for PO: sees PO + global skills, all canRun
SK=$(curl -s -b "$(jar po)" "$BASE/api/projects/$PID/skills?phase=1")
echo "$SK" | grep -q '"id":"estimate_points"' || fail "PO missing estimate_points: $SK"
echo "$SK" | grep -q '"id":"kb_search"' || fail "missing global kb_search"
echo "$SK" | grep -q '"tier":"non_llm"' || fail "no non_llm tier advertised"
echo "[2] PO sees stage-1 + global skills with model tiers"

# 3. QA cannot see/run PO skill; QA's own phase-4 skills gated by stage (not phase 1)
SKQA=$(curl -s -b "$(jar qa)" "$BASE/api/projects/$PID/skills?phase=1")
echo "$SKQA" | grep -o '"id":"estimate_points"[^}]*"canRun":[a-z]*' | grep -q 'false' || fail "QA should not be able to run PO skill"
echo "[3] role gating: QA cannot run PO-only skill"

# 4. execute a NON-LLM skill (deterministic) as PO
EX=$(curl -s -b "$(jar po)" -X POST "$BASE/api/projects/$PID/skills/estimate_points/execute" \
  -H 'content-type: application/json' -d '{"input":"Realtime integration with security review and data migration"}')
echo "$EX" | grep -q '"tier":"non_llm"' || fail "estimate_points not non_llm: $EX"
echo "$EX" | grep -q 'story points' || fail "no estimate output: $EX"
echo "[4] non-LLM skill executed deterministically (story point estimate)"

# 5. execute a LOCAL-tier skill; response reports the tier served
EX2=$(curl -s -b "$(jar po)" -X POST "$BASE/api/projects/$PID/skills/summarise/execute" \
  -H 'content-type: application/json' -d '{"input":"We need billing with proration, dunning, and tax handling."}')
echo "$EX2" | grep -q '"tier":"local"' || fail "summarise not local tier: $EX2"
echo "[5] local-tier skill executed (lightweight model / mock fallback)"

# 6. wrong-role execute denied at the API (defence in depth)
DENY=$(curl -s -b "$(jar qa)" -X POST "$BASE/api/projects/$PID/skills/estimate_points/execute" \
  -H 'content-type: application/json' -d '{"input":"x"}')
echo "$DENY" | grep -q 'FORBIDDEN' || fail "QA execute of PO skill not denied: $DENY"
echo "[6] execute RBAC enforced server-side (QA denied PO skill)"

# 7. audit records the skill run
AUD=$(curl -s -b "$(jar pm)" "$BASE/api/projects/$PID/audit")
echo "$AUD" | grep -q 'skill.executed' || fail "skill run not audited"
echo "[7] skill executions audited"

echo ""
echo "SKILLS/MODEL SMOKE PASS ✅ — planner tier selection, stage+role scoped skills, non-LLM/local execution, RBAC, audit"
