#!/usr/bin/env bash
# File-explorer smoke: storage-layer file tree, typed extensions,
# codebase file content, on-disk layout parity, RBAC.
set -uo pipefail
BASE=http://localhost:8080
fail() { echo "FILES SMOKE FAIL: $1"; exit 1; }
jar() { echo "/tmp/fljar_$1"; }
login() { curl -s -c "$(jar $1)" -X POST "$BASE/api/auth/login" -H 'content-type: application/json' \
  -d "{\"email\":\"$1@sdlc.local\",\"password\":\"Password123!\"}" >/dev/null; }
for U in pm po sa; do login $U; done

P=$(curl -s -b "$(jar pm)" -X POST "$BASE/api/projects" -H 'content-type: application/json' \
  -d '{"name":"Files Explorer Demo","techStack":"Python + FastAPI"}')
PID=$(echo "$P" | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
curl -s -b "$(jar pm)" -X POST "$BASE/api/projects/$PID/members" -H 'content-type: application/json' \
  -d '{"email":"po@sdlc.local","role":"PO"}' >/dev/null

# upload a small codebase zip
ZIP="$HOME/files-demo.zip"; rm -f "$ZIP"
FILES_ZIP="$ZIP" python - <<'PYZ'
import os, zipfile
z = zipfile.ZipFile(os.environ['FILES_ZIP'], 'w')
z.writestr('app/main.py', 'print("legacy service")\n')
z.close()
PYZ
ZIPPATH=$(cygpath -m "$ZIP" 2>/dev/null || echo "$ZIP")
curl -s -b "$(jar pm)" -F "file=@$ZIPPATH;type=application/zip" "$BASE/api/projects/$PID/codebase" >/dev/null

# run phase 1 to create artifact files
curl -s -N --max-time 150 -b "$(jar po)" -X POST "$BASE/api/chat" -H 'content-type: application/json' \
  -d "{\"projectId\":\"$PID\",\"message\":\"Build a files explorer demo API\"}" >/dev/null
echo "[setup] project, codebase, phase-1 artifacts ready"

# 1. file tree: phase-1 folder with typed files + codebase folder
TREE=$(curl -s -b "$(jar pm)" "$BASE/api/projects/$PID/files")
echo "$TREE" | grep -q '"root":"content-store/' || fail "no root: $TREE"
echo "$TREE" | grep -q '"name":"phase-1"' || fail "no phase-1 folder"
echo "$TREE" | grep -q 'PRD-.*\.md' || fail "no PRD .md file in tree"
echo "$TREE" | grep -q '"name":"codebase"' || fail "no codebase folder"
echo "$TREE" | grep -q 'app/main.py' || fail "codebase file missing from tree"
echo "[1] file tree: phase folders with typed files + codebase folder"

# 2. tree names match the actual on-disk layout in the container volume
KEY=$(echo "$TREE" | grep -o '"storageKey":"[^"]*PRD[^"]*"' | head -1 | cut -d'"' -f4)
docker compose exec -T orchestrator sh -c "test -f /data/$KEY && echo EXISTS" | grep -q EXISTS \
  || fail "tree file not on disk: $KEY"
echo "[2] tree entries match real files on the storage volume"

# 3. codebase file content endpoint
CBID=$(echo "$TREE" | grep -o '"codebaseFileId":"[^"]*"' | head -1 | cut -d'"' -f4)
CB=$(curl -s -b "$(jar pm)" "$BASE/api/projects/$PID/codebase/$CBID")
echo "$CB" | grep -q 'legacy service' || fail "codebase content: $CB"
echo "[3] codebase file opens with content"

# 4. artifact detail carries storageKey/Mode for ext-driven viewer
AID=$(echo "$TREE" | grep -o '"artefactId":"[^"]*"' | head -1 | cut -d'"' -f4)
DET=$(curl -s -b "$(jar pm)" "$BASE/api/projects/$PID/artefacts/$AID")
echo "$DET" | grep -q '"storageKey":"content-store/' || fail "detail missing storageKey"
echo "[4] artifact detail exposes file path for type-driven viewer"

# 5. RBAC: non-member denied files + codebase content
login qa
[ "$(curl -s -o /dev/null -w '%{http_code}' -b "$(jar qa)" "$BASE/api/projects/$PID/files")" = "403" ] || fail "non-member files not denied"
[ "$(curl -s -o /dev/null -w '%{http_code}' -b "$(jar qa)" "$BASE/api/projects/$PID/codebase/$CBID")" = "403" ] || fail "non-member codebase not denied"
echo "[5] non-member denied file tree + file content"

echo ""
echo "FILES SMOKE PASS ✅ — storage-layer file tree, on-disk parity, typed viewers data, RBAC"
