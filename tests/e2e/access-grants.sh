#!/usr/bin/env bash
# Positive-grant access matrix: PM + every phase-role member CAN access
# the project and each phase's tasks+artifacts they are authorised for; a
# non-member is denied; the phase bundle returns only that phase's artifacts.
set -uo pipefail
BASE=http://localhost:8080
PASS=0; FAIL=0
ok() { PASS=$((PASS+1)); echo "  ✓ $1"; }
bad() { FAIL=$((FAIL+1)); echo "  ✗ $1"; }
jar() { echo "/tmp/gjar_$1"; }
login() { curl -s -c "$(jar $1)" -X POST "$BASE/api/auth/login" -H 'content-type: application/json' \
  -d "{\"email\":\"$1@sdlc.local\",\"password\":\"Password123!\"}" >/dev/null; }
code() { curl -s -o /dev/null -w '%{http_code}' -b "$(jar $1)" "$BASE$2"; }
get() { curl -s -b "$(jar $1)" "$BASE$2"; }
for U in superadmin pm po sa ta qa devops dev; do login $U; done

# PM project, staffed with all six phase roles; run phases 1 & 2 to create artifacts.
P=$(curl -s -b "$(jar pm)" -X POST "$BASE/api/projects" -H 'content-type: application/json' -d '{"name":"Grant Matrix","techStack":"Java + Spring Boot"}')
PID=$(echo "$P" | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
for PAIR in "po:PO" "sa:SA" "ta:TA" "qa:QA" "devops:DEVOPS" "dev:DEV"; do U=${PAIR%%:*}; R=${PAIR##*:}
  curl -s -b "$(jar pm)" -X POST "$BASE/api/projects/$PID/members" -H 'content-type: application/json' -d "{\"email\":\"$U@sdlc.local\",\"role\":\"$R\"}" >/dev/null; done
curl -s -N --max-time 150 -b "$(jar po)" -X POST "$BASE/api/chat" -H 'content-type: application/json' -d "{\"projectId\":\"$PID\",\"message\":\"Build a grant matrix service\"}" >/dev/null
curl -s -b "$(jar po)" -X POST "$BASE/api/gates/$PID/phase/1/review" -H 'content-type: application/json' -d '{"decision":"APPROVE"}' >/dev/null
curl -s -N --max-time 150 -b "$(jar sa)" -X POST "$BASE/api/chat" -H 'content-type: application/json' -d "{\"projectId\":\"$PID\",\"message\":\"Proceed with phase 2\"}" >/dev/null
echo "project $PID staffed; phases 1 (approved) and 2 have artifacts"

echo "== project-level grants (PM + every phase member get access) =="
for U in superadmin pm po sa ta qa devops dev; do
  for RES in "" "/artefacts" "/audit" "/flow" "/skills" "/members"; do
    c=$(code $U "/api/projects/$PID$RES")
    [ "$c" = "200" ] && ok "$U → project$RES" || bad "$U → project$RES = $c (want 200)"
  done
done

echo "== phase-scoped grants (that particular phase: tasks + artifacts) =="
for U in pm po sa ta qa devops dev superadmin; do
  for PH in 1 2 3; do
    c=$(code $U "/api/projects/$PID/phases/$PH")
    [ "$c" = "200" ] && ok "$U → phase $PH bundle" || bad "$U → phase $PH bundle = $c (want 200)"
  done
done

echo "== phase bundle returns only that phase's artifacts =="
B1=$(get pm "/api/projects/$PID/phases/1")
echo "$B1" | grep -q '"phase":1' || bad "phase 1 bundle malformed"
# every artefact in the phase-1 bundle must be phase 1
if echo "$B1" | grep -o '"phase":[0-9]' | grep -v '"phase":1' | grep -q .; then bad "phase 1 bundle leaked other phases"; else ok "phase 1 bundle scoped to phase 1"; fi
echo "$B1" | grep -q '"tasks"' && echo "$B1" | grep -q 'ai.generation' && ok "phase 1 bundle includes tasks (activity)" || bad "phase 1 bundle missing tasks"

echo "== non-member denial holds =="
P2=$(curl -s -b "$(jar pm)" -X POST "$BASE/api/projects" -H 'content-type: application/json' -d '{"name":"No Members Project","techStack":"Go + Gin"}')
PID2=$(echo "$P2" | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
[ "$(code po /api/projects/$PID2)" = "403" ] && ok "non-member PO denied project" || bad "non-member not denied"
[ "$(code po /api/projects/$PID2/phases/1)" = "403" ] && ok "non-member PO denied phase bundle" || bad "non-member phase not denied"

echo ""
echo "RESULT: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] && echo "ACCESS-GRANT MATRIX PASS ✅" || { echo "ACCESS-GRANT MATRIX FAIL ❌"; exit 1; }
