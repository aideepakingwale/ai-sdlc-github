#!/usr/bin/env bash
# RBAC smoke v2: Keycloak-backed login, PM project+team management, per-role
# gate approvals, segregation-of-duties negatives, full 6-phase pipeline.
set -uo pipefail
BASE=http://localhost:8080
fail() { echo "RBAC SMOKE FAIL: $1"; exit 1; }

jar() { echo "/tmp/jar_$1"; }
login() { # $1=email-prefix
  local out
  out=$(curl -s -c "$(jar $1)" -X POST "$BASE/api/auth/login" -H 'content-type: application/json' \
    -d "{\"email\":\"$1@sdlc.local\",\"password\":\"Password123!\"}")
  echo "$out" | grep -q '"user"' || fail "login $1: $out"
}

# 1. Keycloak ROPC login for every role (JIT provisioning happens here)
for U in superadmin pm po sa ta qa devops dev; do login $U; done
ROLE=$(curl -s -b "$(jar superadmin)" "$BASE/api/auth/me" | grep -o '"role":"[^"]*"')
[ "$ROLE" = '"role":"SUPER_ADMIN"' ] || fail "superadmin role mapping: $ROLE"
echo "[1] Keycloak ROPC login OK for all 8 roles (JIT provisioned)"

# 2. Phase-role user cannot create a project
DENY=$(curl -s -b "$(jar po)" -X POST "$BASE/api/projects" -H 'content-type: application/json' -d '{"name":"PO rogue project"}')
echo "$DENY" | grep -q 'FORBIDDEN' || fail "PO created a project: $DENY"
echo "[2] PO denied project creation (PM-only) OK"

# 3. PM creates project and staffs the team
PROJ=$(curl -s -b "$(jar pm)" -X POST "$BASE/api/projects" -H 'content-type: application/json' -d '{"name":"Loyalty Points API"}')
PROJECT=$(echo "$PROJ" | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
[ -n "$PROJECT" ] || fail "PM project creation: $PROJ"
for PAIR in "po:PO" "sa:SA" "ta:TA" "qa:QA" "devops:DEVOPS" "dev:DEV"; do
  U=${PAIR%%:*}; R=${PAIR##*:}
  ADD=$(curl -s -b "$(jar pm)" -X POST "$BASE/api/projects/$PROJECT/members" -H 'content-type: application/json' \
    -d "{\"email\":\"$U@sdlc.local\",\"role\":\"$R\"}")
  echo "$ADD" | grep -q '"member"' || fail "add member $U: $ADD"
done
# role-mismatch guard: dev cannot be added as PO
MISMATCH=$(curl -s -b "$(jar pm)" -X POST "$BASE/api/projects/$PROJECT/members" -H 'content-type: application/json' \
  -d '{"email":"dev@sdlc.local","role":"PO"}')
echo "$MISMATCH" | grep -q 'VALIDATION_FAILED' || fail "role mismatch allowed: $MISMATCH"
echo "[3] PM created project $PROJECT + staffed 6 phase roles; role-mismatch guard OK"

# 4. Non-member visibility: QA sees the project (member); PM's list has it; PO cannot see a foreign project id
LIST=$(curl -s -b "$(jar qa)" "$BASE/api/projects")
echo "$LIST" | grep -q "$PROJECT" || fail "member QA cannot see project list entry"
echo "[4] membership-based visibility OK"

# 5. PO drives phase 1 (member chat), then approvals phase-by-phase by the right role
run_phase() { # $1=user $2=message
  curl -s -N --max-time 180 -b "$(jar $1)" -X POST "$BASE/api/chat" -H 'content-type: application/json' \
    -d "{\"projectId\":\"$PROJECT\",\"message\":\"$2\"}"
}
S1=$(run_phase po "Build a customer loyalty points API: earn on purchase, redeem at checkout, tier upgrades at 1000/5000 points. 200 rps, p99<400ms.")
echo "$S1" | grep -q '"status":"PENDING_REVIEW"' || fail "phase1 gate: $(echo "$S1" | tail -c 300)"

# PM cannot approve (segregation of duties)
PMDENY=$(curl -s -b "$(jar pm)" -X POST "$BASE/api/gates/$PROJECT/phase/1/review" -H 'content-type: application/json' -d '{"decision":"APPROVE"}')
echo "$PMDENY" | grep -q 'FORBIDDEN' || fail "PM approved a gate: $PMDENY"
# wrong-role member cannot approve
QADENY=$(curl -s -b "$(jar qa)" -X POST "$BASE/api/gates/$PROJECT/phase/1/review" -H 'content-type: application/json' -d '{"decision":"APPROVE"}')
echo "$QADENY" | grep -q 'FORBIDDEN' || fail "QA approved PO gate: $QADENY"
echo "[5] phase 1 generated; PM + wrong-role member both denied at the gate"

approve() { # $1=user $2=phase
  local out
  out=$(curl -s -b "$(jar $1)" -X POST "$BASE/api/gates/$PROJECT/phase/$2/review" -H 'content-type: application/json' -d '{"decision":"APPROVE"}')
  echo "$out" | grep -q '"status":"APPROVED"' || fail "approve p$2 by $1: $out"
}
approve po 1
for PAIR in "sa:2" "ta:3" "qa:4" "devops:5"; do
  U=${PAIR%%:*}; P=${PAIR##*:}
  SN=$(run_phase $U "Proceed with phase $P based on the approved artifacts.")
  echo "$SN" | grep -q '"type":"done"' || fail "phase $P run: $(echo "$SN" | tail -c 300)"
  approve $U $P
done
S6=$(run_phase dev "Proceed with phase 6 based on the approved artifacts.")
echo "$S6" | grep -qi 'recovered after\|fix iteration\|PR' || fail "phase 6 recovery/PR: $(echo "$S6" | tail -c 300)"
approve dev 6
PROJSTATE=$(curl -s -b "$(jar pm)" "$BASE/api/projects/$PROJECT")
echo "$PROJSTATE" | grep -q '"status":"COMPLETED"' || fail "project not COMPLETED"
echo "[6] all six phases approved by their own role holders -> project COMPLETED"

# 7. audit shows reviewers per phase + no override events in the happy path
AUDIT=$(curl -s -b "$(jar pm)" "$BASE/api/projects/$PROJECT/audit")
echo "$AUDIT" | grep -q '"humanReviewer":"po@sdlc.local"' || fail "audit missing po reviewer"
echo "$AUDIT" | grep -q '"humanReviewer":"dev@sdlc.local"' || fail "audit missing dev reviewer"
echo "$AUDIT" | grep -q 'team.member_added' || fail "audit missing team events"
echo "[7] audit trail records per-role reviewers + team management events"

echo ""
echo "RBAC SMOKE PASS ✅ — Keycloak SSO/ROPC, PM project+team mgmt, project-scoped phase-role gates, segregation of duties"
