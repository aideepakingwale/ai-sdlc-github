#!/usr/bin/env bash
# File-first artifacts smoke: download endpoints serve real files with
# proper filename + MIME so the desktop opens the right application.
set -uo pipefail
BASE=http://localhost:8080
fail() { echo "DOWNLOAD SMOKE FAIL: $1"; exit 1; }
jar() { echo "/tmp/djar_$1"; }
login() { curl -s -c "$(jar $1)" -X POST "$BASE/api/auth/login" -H 'content-type: application/json' \
  -d "{\"email\":\"$1@sdlc.local\",\"password\":\"Password123!\"}" >/dev/null; }
for U in pm po qa; do login $U; done

# reuse the Viz Probe-style flow: fresh project, run phase 1 to mint artifacts
P=$(curl -s -b "$(jar pm)" -X POST "$BASE/api/projects" -H 'content-type: application/json' \
  -d '{"name":"Download Probe","techStack":"Python + FastAPI"}')
PID=$(echo "$P" | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
[ -n "$PID" ] || fail "project not created: $P"
curl -s -b "$(jar pm)" -X POST "$BASE/api/projects/$PID/members" -H 'content-type: application/json' \
  -d '{"email":"po@sdlc.local","role":"PO"}' >/dev/null
S1=$(curl -s -N --max-time 150 -b "$(jar po)" -X POST "$BASE/api/chat" -H 'content-type: application/json' \
  -d "{\"projectId\":\"$PID\",\"message\":\"Build a subscriptions API\"}")
echo "$S1" | grep -q PENDING_REVIEW || fail "phase 1 run failed: $(echo "$S1" | tail -c 200)"

ARTS=$(curl -s -b "$(jar pm)" "$BASE/api/projects/$PID/artefacts")
AID=$(echo "$ARTS" | grep -o '{[^{]*"type":"PRD"[^}]*}' | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
[ -n "$AID" ] || fail "no PRD artifact: $ARTS"

# 1. markdown artifact downloads as .md with text/markdown + attachment filename
HDR=$(curl -s -D - -o /tmp/dl_prd.md -b "$(jar pm)" "$BASE/api/projects/$PID/artefacts/$AID/download")
echo "$HDR" | grep -qi 'content-type: text/markdown' || fail "PRD MIME wrong: $HDR"
echo "$HDR" | grep -qi 'content-disposition: attachment; filename="PRD-' || fail "PRD disposition wrong: $HDR"
echo "$HDR" | grep -qi 'filename="PRD-.*\.md"' || fail "PRD filename ext wrong: $HDR"
grep -qi '# PRD' /tmp/dl_prd.md || fail "downloaded PRD body empty/wrong"
echo "[1] markdown artifact → attachment PRD-<id>.md, text/markdown, real body"

# 2. RBAC: non-member download denied
DENY=$(curl -s -o /dev/null -w '%{http_code}' -b "$(jar qa)" "$BASE/api/projects/$PID/artefacts/$AID/download")
[ "$DENY" = "403" ] || fail "non-member download allowed ($DENY)"
echo "[2] download RBAC: non-member 403"

# 3. epic/story artifacts also stored as files (no mock-link dependency to read them)
EID=$(echo "$ARTS" | grep -o '{[^{]*"type":"EPIC"[^}]*}' | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
EHDR=$(curl -s -D - -o /dev/null -b "$(jar pm)" "$BASE/api/projects/$PID/artefacts/$EID/download")
echo "$EHDR" | grep -qi 'filename="EPIC-.*\.md"' || fail "EPIC not a file: $EHDR"
echo "[3] jira-linked artifact types (EPIC) download as real .md files too"

echo ""
echo "DOWNLOAD SMOKE PASS ✅ — artifacts are files: correct filename, MIME for desktop apps, RBAC enforced"
