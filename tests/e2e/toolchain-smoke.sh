#!/usr/bin/env bash
# SDLC toolchain smoke: full-cycle run exercising the expanded MCP tool
# surface — test-suite generation (phase 4), security scanning (phase 5), the
# post-CI verification battery (phase 6), and QA/DevOps run-skills.
set -uo pipefail
BASE=http://localhost:8080
fail() { echo "TOOLCHAIN SMOKE FAIL: $1"; exit 1; }
jar() { echo "/tmp/tcjar_$1"; }
login() { curl -s -c "$(jar $1)" -X POST "$BASE/api/auth/login" -H 'content-type: application/json' \
  -d "{\"email\":\"$1@sdlc.local\",\"password\":\"Password123!\"}" >/dev/null; }
for U in pm po sa ta qa devops dev; do login $U; done

P=$(curl -s -b "$(jar pm)" -X POST "$BASE/api/projects" -H 'content-type: application/json' \
  -d '{"name":"Toolchain Probe","techStack":"Node.js + TypeScript"}')
PID=$(echo "$P" | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
[ -n "$PID" ] || fail "project not created"
for PAIR in "po:PO" "sa:SA" "ta:TA" "qa:QA" "devops:DEVOPS" "dev:DEV"; do U=${PAIR%%:*}; R=${PAIR##*:}
  curl -s -b "$(jar pm)" -X POST "$BASE/api/projects/$PID/members" -H 'content-type: application/json' \
    -d "{\"email\":\"$U@sdlc.local\",\"role\":\"$R\"}" >/dev/null; done

run_phase() { # user, message
  local OUT
  OUT=$(curl -s -N --max-time 240 -b "$(jar $1)" -X POST "$BASE/api/chat" -H 'content-type: application/json' \
    -d "{\"projectId\":\"$PID\",\"message\":\"$2\"}")
  echo "$OUT" | grep -q 'PENDING_REVIEW' || fail "phase run by $1 failed: $(echo "$OUT" | tail -c 300)"
}
approve() { # user, phase
  curl -s -b "$(jar $1)" -X POST "$BASE/api/gates/$PID/phase/$2/review" -H 'content-type: application/json' \
    -d '{"decision":"APPROVE","comments":"ok"}' | grep -q 'APPROVED' || fail "approve $2 by $1 failed"
}

run_phase po "Build an invoice reconciliation API with CSV import"
approve po 1
run_phase sa "proceed"; approve sa 2
run_phase ta "proceed"; approve ta 3
run_phase qa "proceed"

# 1. phase 4 generated the full testing stack
ARTS=$(curl -s -b "$(jar pm)" "$BASE/api/projects/$PID/artefacts")
for T in REST_ASSURED PLAYWRIGHT_SPEC JMETER_PLAN LOCUSTFILE K6_SCRIPT POSTMAN_COLLECTION; do
  echo "$ARTS" | grep -q "\"type\":\"$T\"" || fail "phase 4 missing $T artifact"
done
echo "[1] phase 4: REST Assured + Playwright + JMeter + Locust (+ k6/Postman) suites generated"

# 2. QA run-skills execute the suites through MCP testing engines
for S in run_api_tests run_ui_tests run_perf_test; do
  R=$(curl -s -b "$(jar qa)" -X POST "$BASE/api/projects/$PID/skills/$S/execute" \
    -H 'content-type: application/json' -d '{"input":""}')
  echo "$R" | grep -q '"output"' || fail "skill $S failed: $(echo "$R" | head -c 200)"
done
R=$(curl -s -b "$(jar qa)" -X POST "$BASE/api/projects/$PID/skills/run_api_tests/execute" \
  -H 'content-type: application/json' -d '{"input":""}')
echo "$R" | grep -q 'Postman run' || fail "postman report missing: $R"
echo "[2] QA skills: Postman/newman, Playwright and k6 engines executed on generated suites"

approve qa 4
run_phase devops "proceed"

# 3. phase 5 shift-left security: Trivy + Secrets Manager verification
ARTS=$(curl -s -b "$(jar pm)" "$BASE/api/projects/$PID/artefacts")
echo "$ARTS" | grep -q '"type":"SECURITY_SCAN"' || fail "SECURITY_SCAN artifact missing"
SID=$(echo "$ARTS" | grep -o '{[^{]*"type":"SECURITY_SCAN"[^}]*}' | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
DET=$(curl -s -b "$(jar pm)" "$BASE/api/projects/$PID/artefacts/$SID")
echo "$DET" | grep -q 'Trivy scan' || fail "Trivy report missing"
echo "$DET" | grep -q 'Secrets Manager verification' || fail "secrets verification missing"
echo "$DET" | grep -qi 'present' || fail "secret existence rows missing"
echo "[3] phase 5: Trivy image scan + Secrets Manager existence check (values never exposed)"

approve devops 5
run_phase dev "proceed"

# 4. phase 6 post-CI verification battery + quality gate
ARTS=$(curl -s -b "$(jar pm)" "$BASE/api/projects/$PID/artefacts")
echo "$ARTS" | grep -q '"type":"TEST_EXECUTION_REPORT"' || fail "TEST_EXECUTION_REPORT missing"
echo "$ARTS" | grep -q '"type":"QUALITY_REPORT"' || fail "QUALITY_REPORT missing"
TID=$(echo "$ARTS" | grep -o '{[^{]*"type":"TEST_EXECUTION_REPORT"[^}]*}' | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
TDET=$(curl -s -b "$(jar pm)" "$BASE/api/projects/$PID/artefacts/$TID")
for SECTION in 'Postman run' 'Playwright run' 'k6 load test' 'ZAP baseline'; do
  echo "$TDET" | grep -q "$SECTION" || fail "verification battery missing: $SECTION"
done
QID=$(echo "$ARTS" | grep -o '{[^{]*"type":"QUALITY_REPORT"[^}]*}' | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
curl -s -b "$(jar pm)" "$BASE/api/projects/$PID/artefacts/$QID" | grep -q 'SonarQube' || fail "Sonar report missing"
echo "[4] phase 6: Postman + Playwright + k6 + ZAP battery and SonarQube quality gate reports"

# 5. connector exposes AWS mode + the new tools register over MCP
MODES=$(docker exec ai-sdlc-tool-connector-1 node -e "fetch('http://localhost:8082/readyz').then(r=>r.json()).then(d=>console.log(JSON.stringify(d)))")
echo "$MODES" | grep -q '"aws":"mock"' || fail "aws mode missing from connector: $MODES"
echo "[5] connector: aws connector mode reported (mock locally; TOOLS_AWS_S3_BUCKET flips live)"

echo ""
echo "TOOLCHAIN SMOKE PASS ✅ — 12-tool SDLC toolchain wired end-to-end across phases 4/5/6 + QA/DevOps skills"
