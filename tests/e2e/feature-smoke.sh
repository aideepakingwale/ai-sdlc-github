#!/usr/bin/env bash
# Feature smoke: tech stack, mermaid diagram artifacts, brownfield upload + grounding, providers.
set -uo pipefail
BASE=http://localhost:8080
fail() { echo "FEAT SMOKE FAIL: $1"; exit 1; }
jar() { echo "/tmp/fjar_$1"; }
login() {
  curl -s -c "$(jar $1)" -X POST "$BASE/api/auth/login" -H 'content-type: application/json' \
    -d "{\"email\":\"$1@sdlc.local\",\"password\":\"Password123!\"}" >/dev/null
}
for U in pm po sa ta; do login $U; done

# 1. PM creates a Python-stack project + staffs po/sa/ta
P=$(curl -s -b "$(jar pm)" -X POST "$BASE/api/projects" -H 'content-type: application/json' \
  -d '{"name":"Warehouse Slotting Service","techStack":"Python + FastAPI"}')
echo "$P" | grep -q '"techStack":"Python + FastAPI"' || fail "techStack not persisted: $P"
PID=$(echo "$P" | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
for PAIR in "po:PO" "sa:SA" "ta:TA"; do
  U=${PAIR%%:*}; R=${PAIR##*:}
  curl -s -b "$(jar pm)" -X POST "$BASE/api/projects/$PID/members" -H 'content-type: application/json' \
    -d "{\"email\":\"$U@sdlc.local\",\"role\":\"$R\"}" >/dev/null
done
echo "[1] project created with Python + FastAPI stack, team staffed"

# 2. Brownfield upload (zip built in-memory by python — portable across OSes)
ZIP="$HOME/legacy-test.zip"; rm -f "$ZIP"
LEGACY_ZIP="$ZIP" python - <<'PYZ'
import os, zipfile
z = zipfile.ZipFile(os.environ['LEGACY_ZIP'], 'w')
z.writestr('src/inventory.py',
           'class InventoryService:\n'
           '    """Legacy slotting logic: aisle-based FIFO."""\n'
           '    def slot_for(self, sku: str) -> str:\n'
           '        return f"AISLE-{hash(sku) % 12:02d}"\n')
z.writestr('README.md',
           '# Legacy Warehouse Service\n'
           'Flask app serving slotting decisions. Known issue: no velocity-based slotting.\n')
z.close()
PYZ
ZIPPATH=$(cygpath -m "$ZIP" 2>/dev/null || echo "$ZIP")
UP=$(curl -s -b "$(jar pm)" -F "file=@$ZIPPATH;type=application/zip" "$BASE/api/projects/$PID/codebase")
echo "$UP" | grep -q '"files":2' || fail "upload: $UP"
LIST=$(curl -s -b "$(jar pm)" "$BASE/api/projects/$PID/codebase")
echo "$LIST" | grep -q 'src/inventory.py' || fail "codebase list: $LIST"
DETAIL=$(curl -s -b "$(jar pm)" "$BASE/api/projects/$PID")
echo "$DETAIL" | grep -q '"codebaseFiles":2' || fail "detail count: missing"
echo "[2] brownfield zip uploaded: 2 files stored + RAG-indexed"

# 3. RAG retrieval includes codebase
KB=$(curl -s -b "$(jar pm)" "$BASE/api/kb/search?q=slotting+inventory+aisle&projectId=$PID")
echo "$KB" | grep -q 'inventory.py' || fail "codebase not retrievable: $KB"
echo "[3] RAG retrieves uploaded source for relevant queries"

# 4. Phase 1 (PO) -> approve -> Phase 2 (SA) => HLD_DIAGRAM artifact
run() { curl -s -N --max-time 180 -b "$(jar $1)" -X POST "$BASE/api/chat" -H 'content-type: application/json' \
  -d "{\"projectId\":\"$PID\",\"message\":\"$2\"}"; }
approve() { curl -s -b "$(jar $1)" -X POST "$BASE/api/gates/$PID/phase/$2/review" -H 'content-type: application/json' -d '{"decision":"APPROVE"}'; }
S1=$(run po "Enhance the legacy warehouse service with velocity-based slotting recommendations")
echo "$S1" | grep -q 'PENDING_REVIEW' || fail "phase1: $(echo "$S1" | tail -c 200)"
approve po 1 >/dev/null
S2=$(run sa "Proceed with phase 2 based on approved artifacts")
echo "$S2" | grep -q 'HLD_DIAGRAM' || fail "phase2 no HLD_DIAGRAM: $(echo "$S2" | tail -c 300)"
approve sa 2 >/dev/null
S3=$(run ta "Proceed with phase 3")
echo "$S3" | grep -q 'LLD_DIAGRAM' || fail "phase3 no LLD_DIAGRAM"
ARTS=$(curl -s -b "$(jar pm)" "$BASE/api/projects/$PID/artefacts")
AID=$(echo "$ARTS" | grep -o '{[^{]*"type":"HLD_DIAGRAM"[^}]*}' | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
BODY=$(curl -s -b "$(jar pm)" "$BASE/api/projects/$PID/artefacts/$AID")
echo "$BODY" | grep -q 'flowchart' || fail "diagram content not mermaid: $(echo "$BODY" | head -c 200)"
echo "[4] HLD + LLD mermaid diagram artifacts generated and retrievable"

# 5. audit shows RAG grounding on this project's generations
AUD=$(curl -s -b "$(jar pm)" "$BASE/api/projects/$PID/audit")
echo "$AUD" | grep -q '"ragSnippets"' || fail "no ragSnippets in audit"
echo "[5] agent generations audited with RAG grounding counts"

# 6. providers endpoint
PROV=$(curl -s -b "$(jar pm)" "$BASE/api/providers")
echo "$PROV" | grep -q '"provider":"groq"' || fail "providers: $PROV"
echo "$PROV" | grep -q '"jira":"mock"' || fail "tool modes: $PROV"
echo "[6] provider/connector status endpoint OK (shows live-vs-mock)"

echo ""
echo "FEAT SMOKE PASS ✅ — tech stack, brownfield upload+RAG, mermaid diagrams, provider status"
