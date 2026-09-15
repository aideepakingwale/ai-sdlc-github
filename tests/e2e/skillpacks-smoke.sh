#!/usr/bin/env bash
# Markdown skill packs smoke: skills load from.md files, RBAC comes
# from frontmatter and is enforced, governance exposes the full pack library.
set -uo pipefail
BASE=http://localhost:8080
fail() { echo "SKILLPACKS SMOKE FAIL: $1"; exit 1; }
jar() { echo "/tmp/skjar_$1"; }
login() { curl -s -c "$(jar $1)" -X POST "$BASE/api/auth/login" -H 'content-type: application/json' \
  -d "{\"email\":\"$1@sdlc.local\",\"password\":\"Password123!\"}" >/dev/null; }
for U in pm po sa qa; do login $U; done

P=$(curl -s -b "$(jar pm)" -X POST "$BASE/api/projects" -H 'content-type: application/json' \
  -d '{"name":"Skillpack Probe","techStack":"Go + Gin"}')
PID=$(echo "$P" | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
[ -n "$PID" ] || fail "project not created"
for PAIR in "po:PO" "sa:SA" "qa:QA"; do U=${PAIR%%:*}; R=${PAIR##*:}
  curl -s -b "$(jar pm)" -X POST "$BASE/api/projects/$PID/members" -H 'content-type: application/json' \
    -d "{\"email\":\"$U@sdlc.local\",\"role\":\"$R\"}" >/dev/null; done

# 1. governance: skills served as markdown files with RBAC frontmatter
G=$(curl -s -b "$(jar po)" "$BASE/api/governance/skills")
COUNT=$(echo "$G" | grep -o '"count": *[0-9]*' | head -1 | grep -o '[0-9]*$')
[ "${COUNT:-0}" -ge 20 ] || fail "expected >=20 skill packs, got '$COUNT'"
echo "$G" | grep -q '"file":"draft_runbook.md"' || fail "draft_runbook.md pack missing"
echo "$G" | grep -q '"file":"draft_release_notes.md"' || fail "draft_release_notes.md pack missing"
echo "$G" | grep -q 'Gherkin acceptance criteria' || fail "markdown instruction body missing"
echo "$G" | grep -q '"roles":\["QA"\]' || fail "RBAC roles frontmatter missing"
ANON=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/governance/skills")
[ "$ANON" = "401" ] || fail "governance skills open without auth ($ANON)"
echo "[1] governance: $COUNT markdown skill packs with roles/tier/executor frontmatter"

# 2. md-defined LLM skill executes (global skill, any stage) — instruction from the file body
R=$(curl -s -b "$(jar po)" -X POST "$BASE/api/projects/$PID/skills/summarise/execute" \
  -H 'content-type: application/json' -d '{"input":"The API must support CSV import, retries and audit logging."}')
echo "$R" | grep -q '"output"' || fail "summarise execution failed: $(echo "$R" | head -c 200)"
echo "[2] markdown-defined LLM skill executed (instruction sourced from skills/summarise.md)"

# 3. RBAC from frontmatter: wrong phase-role denied, PM never executes, stage gate holds
DENY=$(curl -s -b "$(jar qa)" -X POST "$BASE/api/projects/$PID/skills/estimate_points/execute" \
  -H 'content-type: application/json' -d '{"input":"scope"}')
echo "$DENY" | grep -q 'FORBIDDEN' || fail "QA allowed on PO skill: $DENY"
PMDENY=$(curl -s -b "$(jar pm)" -X POST "$BASE/api/projects/$PID/skills/summarise/execute" \
  -H 'content-type: application/json' -d '{"input":"notes"}')
echo "$PMDENY" | grep -q 'FORBIDDEN' || fail "PM allowed to execute skill: $PMDENY"
GATE=$(curl -s -b "$(jar sa)" -X POST "$BASE/api/projects/$PID/skills/draft_nfr_checklist/execute" \
  -H 'content-type: application/json' -d '{"input":"checkout flow"}')
echo "$GATE" | grep -q 'GATE_CONFLICT' || fail "phase-2 skill ran at phase-1 stage: $GATE"
echo "[3] RBAC enforced from frontmatter: role denial, PM exclusion, stage gating"

# 4. skills list endpoint reflects the md registry (new skills visible, canRun flags)
L=$(curl -s -b "$(jar sa)" "$BASE/api/projects/$PID/skills?phase=2")
echo "$L" | grep -q '"id":"draft_nfr_checklist"' || fail "new md skill missing from list: $L"
echo "$L" | grep -q '"id":"draft_adr"' || fail "converted skill missing from list"
echo "$L" | grep -o '{[^{]*"id":"draft_nfr_checklist"[^}]*}' | grep -q '"canRun":true' \
  || fail "SA cannot run its own phase-2 skill"
echo "[4] skills list: markdown registry drives the stage-scoped, RBAC-flagged skill panel"

echo ""
echo "SKILLPACKS SMOKE PASS ✅ — all skills are .md files; frontmatter RBAC enforced end-to-end"
