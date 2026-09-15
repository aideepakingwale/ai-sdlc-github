#!/usr/bin/env bash
# Dynamic SDLC workflow smoke: config format + validation + versioned
# persistence + parallel execution groups derived from the same saved structure.
set -uo pipefail
BASE=http://localhost:8080
fail() { echo "WORKFLOW SMOKE FAIL: $1"; exit 1; }
jar() { echo "/tmp/wjar_$1"; }
login() { curl -s -c "$(jar $1)" -X POST "$BASE/api/auth/login" -H 'content-type: application/json' \
  -d "{\"email\":\"$1@sdlc.local\",\"password\":\"Password123!\"}" >/dev/null; }
for U in pm po sa ta qa; do login $U; done

# 1. new project starts on the default linear 6-stage workflow, version 0
P=$(curl -s -b "$(jar pm)" -X POST "$BASE/api/projects" -H 'content-type: application/json' \
  -d '{"name":"Parallel Design Lab","techStack":"Python + FastAPI"}')
PID=$(echo "$P" | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
[ -n "$PID" ] || fail "project not created: $P"
for PAIR in "po:PO" "sa:SA" "ta:TA" "qa:QA"; do U=${PAIR%%:*}; R=${PAIR##*:}
  curl -s -b "$(jar pm)" -X POST "$BASE/api/projects/$PID/members" -H 'content-type: application/json' \
    -d "{\"email\":\"$U@sdlc.local\",\"role\":\"$R\"}" >/dev/null; done
WF=$(curl -s -b "$(jar pm)" "$BASE/api/projects/$PID/workflow")
echo "$WF" | grep -q '"version":0' || fail "default workflow should be version 0: $(echo "$WF" | head -c 200)"
echo "$WF" | grep -q '"levels":\[\[1\],\[2\],\[3\],\[4\],\[5\],\[6\]\]' || fail "default levels not linear: $WF"
echo "[1] default workflow: linear 6 stages, version 0"

# 2. validation endpoint rejects bad configs BEFORE save
CYCLE='{"stages":[{"key":"a","name":"Stage A","template":1,"reviewerRole":"PO","team":["PO"],"inputs":["requirements"],"outputs":["x"],"dependsOn":["b"]},{"key":"b","name":"Stage B","template":2,"reviewerRole":"SA","team":["SA"],"inputs":["x"],"outputs":["y"],"dependsOn":["a"]}]}'
V=$(curl -s -b "$(jar pm)" -X POST "$BASE/api/projects/$PID/workflow/validate" -H 'content-type: application/json' -d "$CYCLE")
echo "$V" | grep -q '"valid":false' || fail "cycle accepted: $V"
echo "$V" | grep -qi 'cycle' || fail "cycle error message missing: $V"

BADIN='{"stages":[{"key":"a","name":"Stage A","template":1,"reviewerRole":"PO","team":["PO"],"inputs":["requirements"],"outputs":["prd"],"dependsOn":[]},{"key":"b","name":"Stage B","template":2,"reviewerRole":"SA","team":["SA"],"inputs":["test_plan"],"outputs":["hld"],"dependsOn":["a"]}]}'
V=$(curl -s -b "$(jar pm)" -X POST "$BASE/api/projects/$PID/workflow/validate" -H 'content-type: application/json' -d "$BADIN")
echo "$V" | grep -q '"valid":false' || fail "unproduced input accepted: $V"
echo "$V" | grep -q 'not produced by any upstream stage' || fail "data-flow error missing: $V"

BADREV='{"stages":[{"key":"a","name":"Stage A","template":1,"reviewerRole":"QA","team":["PO"],"inputs":["requirements"],"outputs":["prd"],"dependsOn":[]}]}'
V=$(curl -s -b "$(jar pm)" -X POST "$BASE/api/projects/$PID/workflow/validate" -H 'content-type: application/json' -d "$BADREV")
echo "$V" | grep -q 'must be part of the team' || fail "reviewer-not-in-team accepted: $V"

SAVEBAD=$(curl -s -b "$(jar pm)" -o /dev/null -w '%{http_code}' -X PUT "$BASE/api/projects/$PID/workflow" -H 'content-type: application/json' -d "$CYCLE")
[ "$SAVEBAD" != "200" ] || fail "PUT accepted a cyclic workflow"
echo "[2] validation: cycle, unproduced input, reviewer-not-in-team all rejected (and PUT blocked)"

# 3. RBAC: phase member cannot save the workflow; PM (creator) can
CUSTOM='{"stages":[
 {"key":"discover","name":"Discovery & Stories","template":1,"reviewerRole":"PO","team":["PO"],"inputs":["requirements"],"outputs":["prd","user_stories"],"dependsOn":[]},
 {"key":"hld","name":"Solution HLD","template":2,"reviewerRole":"SA","team":["SA","TA"],"inputs":["prd"],"outputs":["hld"],"dependsOn":["discover"]},
 {"key":"testplan","name":"Test Strategy","template":4,"reviewerRole":"QA","team":["QA"],"inputs":["user_stories"],"outputs":["test_plan"],"dependsOn":["discover"]},
 {"key":"lld","name":"Detailed Design","template":3,"reviewerRole":"TA","team":["TA","SA"],"inputs":["hld","test_plan"],"outputs":["lld"],"dependsOn":["hld","testplan"]}]}'
DENY=$(curl -s -b "$(jar po)" -o /dev/null -w '%{http_code}' -X PUT "$BASE/api/projects/$PID/workflow" -H 'content-type: application/json' -d "$CUSTOM")
[ "$DENY" = "403" ] || fail "PO allowed to save workflow (got $DENY)"
SAVED=$(curl -s -b "$(jar pm)" -X PUT "$BASE/api/projects/$PID/workflow" -H 'content-type: application/json' -d "$CUSTOM")
echo "$SAVED" | grep -q '"version":1' || fail "save did not bump to version 1: $(echo "$SAVED" | head -c 300)"
echo "$SAVED" | grep -q '"levels":\[\[1\],\[2,3\],\[4\]\]' || fail "parallel levels not derived: $SAVED"
echo "[3] RBAC (member 403) + PM saved custom 4-stage workflow with a parallel group → v1"

# 4. amendment updates the SAME persisted structure (version 2) + audited
AMEND=$(echo "$CUSTOM" | sed 's/"name":"Detailed Design"/"name":"Detailed Design v2"/')
SAVED2=$(curl -s -b "$(jar pm)" -X PUT "$BASE/api/projects/$PID/workflow" -H 'content-type: application/json' -d "$AMEND")
echo "$SAVED2" | grep -q '"version":2' || fail "amendment did not bump version: $(echo "$SAVED2" | head -c 200)"
echo "$SAVED2" | grep -q 'Detailed Design v2' || fail "amendment not persisted"
AUD=$(curl -s -b "$(jar pm)" "$BASE/api/projects/$PID/audit")
echo "$AUD" | grep -q 'workflow.updated' || fail "workflow.updated not audited"
echo "[4] amendment → version 2, persisted config updated, workflow.updated audited"

# 5. the same saved structure renders the visualization (flow endpoint)
FLOW=$(curl -s -b "$(jar pm)" "$BASE/api/projects/$PID/flow")
echo "$FLOW" | grep -q '"workflowVersion":2' || fail "flow not on workflow v2: $(echo "$FLOW" | head -c 300)"
echo "$FLOW" | grep -q '"levels":\[\[1\],\[2,3\],\[4\]\]' || fail "flow levels mismatch: $FLOW"
echo "$FLOW" | grep -q '"key":"testplan"' || fail "custom stage missing from flow"
echo "$FLOW" | grep -q '"inputs":\["hld","test_plan"\]' || fail "stage inputs not rendered"
echo "[5] flow visualization rendered from the saved config structure (stages, inputs, levels)"

# 6. runtime: chat run executes stage 1; approving it activates the PARALLEL level (2+3)
S1=$(curl -s -N --max-time 150 -b "$(jar po)" -X POST "$BASE/api/chat" -H 'content-type: application/json' \
  -d "{\"projectId\":\"$PID\",\"message\":\"Design a warehouse slotting optimiser\"}")
echo "$S1" | grep -q 'PENDING_REVIEW' || fail "stage 1 run failed: $(echo "$S1" | tail -c 300)"
R1=$(curl -s -b "$(jar po)" -X POST "$BASE/api/gates/$PID/phase/1/review" -H 'content-type: application/json' \
  -d '{"decision":"APPROVE","comments":"good"}')
echo "$R1" | grep -q 'APPROVED' || fail "stage 1 approval failed: $R1"
S23=$(curl -s -N --max-time 300 -b "$(jar sa)" -X POST "$BASE/api/chat" -H 'content-type: application/json' \
  -d "{\"projectId\":\"$PID\",\"message\":\"proceed\"}")
GATES=$(curl -s -b "$(jar pm)" "$BASE/api/gates/$PID")
OPEN=$(echo "$GATES" | grep -o '"status":"PENDING_REVIEW"' | wc -l)
[ "$OPEN" -eq 2 ] || fail "expected 2 open parallel gates, got $OPEN: $GATES"
echo "$GATES" | grep -q '"phase":2' || fail "stage 2 gate missing: $GATES"
echo "$GATES" | grep -q '"phase":3' || fail "stage 3 gate missing: $GATES"
echo "[6] one chat turn ran BOTH parallel stages (hld + testplan) → two gates open simultaneously"

# 7. level gating: approving only ONE of the parallel gates does NOT advance; both do
RA=$(curl -s -b "$(jar sa)" -X POST "$BASE/api/gates/$PID/phase/2/review" -H 'content-type: application/json' \
  -d '{"decision":"APPROVE","comments":"hld ok"}')
echo "$RA" | grep -q 'APPROVED' || fail "stage 2 approval failed: $RA"
CUR=$(curl -s -b "$(jar pm)" "$BASE/api/projects/$PID" | grep -o '"currentPhase":[0-9]*')
echo "$CUR" | grep -q '"currentPhase":2\|"currentPhase":3' || fail "advanced past level with a gate still open: $CUR"
RB=$(curl -s -b "$(jar qa)" -X POST "$BASE/api/gates/$PID/phase/3/review" -H 'content-type: application/json' \
  -d '{"decision":"APPROVE","comments":"tests ok"}')
echo "$RB" | grep -q 'APPROVED' || fail "stage 3 approval failed: $RB"
CUR2=$(curl -s -b "$(jar pm)" "$BASE/api/projects/$PID" | grep -o '"currentPhase":[0-9]*')
echo "$CUR2" | grep -q '"currentPhase":4' || fail "level complete but did not advance to stage 4: $CUR2"
echo "[7] level gating: half-approved level holds; full approval advances to the next level (stage 4)"

echo ""
echo "WORKFLOW SMOKE PASS ✅ — config format, pre-save validation, versioned amendments, RBAC, parallel execution + level gating, visualization from the same structure"
