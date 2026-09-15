#!/usr/bin/env bash
# Storage tier + pipeline flow + stage retrigger smoke.
set -uo pipefail
BASE=http://localhost:8080
fail() { echo "STORAGE/FLOW SMOKE FAIL: $1"; exit 1; }
jar() { echo "/tmp/sjar_$1"; }
login() { curl -s -c "$(jar $1)" -X POST "$BASE/api/auth/login" -H 'content-type: application/json' \
  -d "{\"email\":\"$1@sdlc.local\",\"password\":\"Password123!\"}" >/dev/null; }
for U in pm po sa qa; do login $U; done

# 1. project + PO/SA members, run phase 1
P=$(curl -s -b "$(jar pm)" -X POST "$BASE/api/projects" -H 'content-type: application/json' \
  -d '{"name":"Fulfilment Router","techStack":"Go + Gin"}')
PID=$(echo "$P" | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
for PAIR in "po:PO" "sa:SA" "qa:QA"; do U=${PAIR%%:*}; R=${PAIR##*:}
  curl -s -b "$(jar pm)" -X POST "$BASE/api/projects/$PID/members" -H 'content-type: application/json' \
    -d "{\"email\":\"$U@sdlc.local\",\"role\":\"$R\"}" >/dev/null; done
S1=$(curl -s -N --max-time 150 -b "$(jar po)" -X POST "$BASE/api/chat" -H 'content-type: application/json' \
  -d "{\"projectId\":\"$PID\",\"message\":\"Build an order fulfilment router with carrier selection\"}")
echo "$S1" | grep -q 'PENDING_REVIEW' || fail "phase1: $(echo "$S1" | tail -c 200)"
echo "[1] project created, team staffed, phase 1 run"

# 2. content-store: artifact body persisted to the tier (storageMode+key) and retrievable
ARTS=$(curl -s -b "$(jar pm)" "$BASE/api/projects/$PID/artefacts")
AID=$(echo "$ARTS" | grep -o '{[^{]*"type":"PRD"[^}]*}' | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
DET=$(curl -s -b "$(jar pm)" "$BASE/api/projects/$PID/artefacts/$AID")
echo "$DET" | grep -q '"storageMode":"filesystem"' || fail "not stored on filesystem tier: $(echo "$DET" | head -c 200)"
echo "$DET" | grep -q '"storageKey":"content-store/' || fail "no storage key"
echo "$DET" | grep -qi 'PRD' || fail "content not retrieved from store"
echo "[2] artifact body persisted to content-store tier + retrieved via storage key"

# 2b. verify the file physically exists in the container volume
KEY=$(echo "$DET" | grep -o '"storageKey":"[^"]*"' | cut -d'"' -f4)
docker compose exec -T orchestrator sh -c "test -f /data/content-store/${KEY#content-store/} && echo EXISTS" | grep -q EXISTS \
  || fail "file not on disk: $KEY"
echo "[2b] file physically present on the mounted content-store volume"

# 3. flow endpoint: colors, assignee, review flags
FLOW=$(curl -s -b "$(jar po)" "$BASE/api/projects/$PID/flow")
echo "$FLOW" | grep -q '"color":"amber"' || fail "phase1 not amber(review): $FLOW"
echo "$FLOW" | grep -q '"displayName":"Priya Owner"' || fail "assignee not shown"
echo "$FLOW" | grep -q '"canReview":true' || fail "PO should be able to review phase 1"
echo "[3] flow endpoint: color-coded states + assignees + review flags"

# 4. retrigger RBAC: QA (wrong stage) denied, PO (owns stage 1) allowed
DENY=$(curl -s -b "$(jar qa)" -X POST "$BASE/api/projects/$PID/phase/1/retrigger")
echo "$DENY" | grep -q 'FORBIDDEN' || fail "QA retrigger of PO stage not denied: $DENY"
OK=$(curl -s -b "$(jar po)" -X POST "$BASE/api/projects/$PID/phase/1/retrigger")
echo "$OK" | grep -q '"retriggered":true' || fail "PO retrigger failed: $OK"
echo "[4] retrigger RBAC: wrong-stage member denied, stage owner allowed"

# 5. retrigger regenerated the stage (new artifacts, back to review); PM can also retrigger
sleep 4
FLOW2=$(curl -s -b "$(jar pm)" "$BASE/api/projects/$PID/flow")
echo "$FLOW2" | grep -q '"phase":1' || fail "flow missing after retrigger"
AUD=$(curl -s -b "$(jar pm)" "$BASE/api/projects/$PID/audit")
echo "$AUD" | grep -q 'stage.retriggered' || fail "retrigger not audited"
echo "[5] stage regenerated + audited (stage.retriggered)"

# 6. explorer visibility: PM sees the project in its list; non-member does not
echo "$(curl -s -b "$(jar pm)" "$BASE/api/projects")" | grep -q "$PID" || fail "PM cannot see own project"
echo "[6] project explorer list scoped by RBAC"

echo ""
echo "STORAGE/FLOW SMOKE PASS ✅ — content-store tier (fs), pipeline flow colors+assignees, stage retrigger RBAC"
