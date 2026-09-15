#!/usr/bin/env bash
# Project Canon + Formwork Library smoke: authorised users author
# binding rules and attach output templates; both reach the agent prompts and
# are stored with type→template mapping. RBAC enforced throughout.
set -uo pipefail
BASE=http://localhost:8080
fail() { echo "CANON/FORMWORK SMOKE FAIL: $1"; exit 1; }
jar() { echo "/tmp/cfjar_$1"; }
login() { curl -s -c "$(jar $1)" -X POST "$BASE/api/auth/login" -H 'content-type: application/json' \
  -d "{\"email\":\"$1@sdlc.local\",\"password\":\"Password123!\"}" >/dev/null; }
for U in superadmin pm po sa qa dev; do login $U; done

P=$(curl -s -b "$(jar pm)" -X POST "$BASE/api/projects" -H 'content-type: application/json' \
  -d '{"name":"Canon Probe","techStack":"Java + Spring Boot"}')
PID=$(echo "$P" | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
[ -n "$PID" ] || fail "project not created"
for PAIR in "po:PO" "sa:SA" "qa:QA" "dev:DEV"; do U=${PAIR%%:*}; R=${PAIR##*:}
  curl -s -b "$(jar pm)" -X POST "$BASE/api/projects/$PID/members" -H 'content-type: application/json' \
    -d "{\"email\":\"$U@sdlc.local\",\"role\":\"$R\"}" >/dev/null; done

# 1. authoring RBAC: managing PM and architects can write; other roles cannot
R=$(curl -s -b "$(jar pm)" -X POST "$BASE/api/projects/$PID/canon" -H 'content-type: application/json' \
  -d '{"title":"Hexagonal architecture only","body":"All services follow ports-and-adapters; no framework types in the domain layer.","category":"rule","priority":"must"}')
echo "$R" | grep -q '"entry"' || fail "PM could not author canon: $R"
R=$(curl -s -b "$(jar sa)" -X POST "$BASE/api/projects/$PID/canon" -H 'content-type: application/json' \
  -d '{"title":"Glossary: Settlement","body":"Settlement means the end-of-day netting run, never the payment capture step.","category":"glossary","priority":"context"}')
echo "$R" | grep -q '"entry"' || fail "SA (architect) could not author canon: $R"
R=$(curl -s -b "$(jar sa)" -X POST "$BASE/api/projects/$PID/canon" -H 'content-type: application/json' \
  -d '{"title":"Stage-2 diagrams use C4 level 2","body":"Container diagrams only; no component-level detail in the HLD.","category":"constraint","priority":"should","stage":2}')
echo "$R" | grep -q '"entry"' || fail "stage-scoped canon rejected: $R"
DENY=$(curl -s -b "$(jar dev)" -X POST "$BASE/api/projects/$PID/canon" -H 'content-type: application/json' \
  -d '{"title":"Dev rule","body":"should not be allowed","category":"rule","priority":"must"}')
echo "$DENY" | grep -q 'FORBIDDEN' || fail "DEV allowed to author canon: $DENY"
echo "[1] canon authoring RBAC: managing PM + SA/TA architects allowed, DEV denied"

# 2. read access for any member + canAuthor flag reflects role
L=$(curl -s -b "$(jar qa)" "$BASE/api/projects/$PID/canon")
echo "$L" | grep -q 'Hexagonal architecture only' || fail "QA cannot read canon: $L"
echo "$L" | grep -q '"canAuthor":false' || fail "QA wrongly flagged as author"
L=$(curl -s -b "$(jar sa)" "$BASE/api/projects/$PID/canon")
echo "$L" | grep -q '"canAuthor":true' || fail "SA not flagged as author"
echo "[2] canon readable by all members; canAuthor flag role-accurate"

# 3. formwork upload: template analysed and mapped to artefact type + format
cat > /tmp/cf_hld.json <<'JSON'
{"artefactType":"HLD","outputFormat":"markdown","name":"House HLD template",
 "template":"# {{service_name}} — High-Level Design\n\n## Context\n{{context}}\n\n## Container View\n{{containers}}\n\n## Decisions\n| ID | Decision | Rationale |\n|---|---|---|\n\n## Risks\n{{risks}}\n"}
JSON
R=$(curl -s -b "$(jar sa)" -X POST "$BASE/api/projects/$PID/formworks" \
  -H 'content-type: application/json' --data-binary @/tmp/cf_hld.json)
echo "$R" | grep -q '"artefactType":"HLD"' || fail "formwork upload failed: $R"
echo "$R" | grep -q '"sections"' || fail "template analysis missing sections: $R"
echo "$R" | grep -q 'Container View' || fail "analysed sections wrong: $R"
echo "$R" | grep -q '"placeholders"' || fail "placeholders not extracted"
echo "$R" | grep -q 'service_name' || fail "placeholder token not detected"
echo "$R" | grep -q '"storageKey":"formworks/' || fail "template not filed in the storage tier"
echo "[3] formwork analysed (sections + placeholders) and mapped HLD→markdown"

# 4. platform-wide library is SUPER_ADMIN only; project scope shadows it
cat > /tmp/cf_adr.json <<'JSON'
{"artefactType":"ADR","outputFormat":"markdown","name":"Corporate ADR standard",
 "template":"# ADR-{{id}}: {{title}}\n\n## Status\n{{status}}\n\n## Context\n{{context}}\n\n## Decision\n{{decision}}\n\n## Consequences\n{{consequences}}\n"}
JSON
DENY=$(curl -s -b "$(jar sa)" -X POST "$BASE/api/formworks" \
  -H 'content-type: application/json' --data-binary @/tmp/cf_adr.json)
echo "$DENY" | grep -q 'FORBIDDEN' || fail "SA allowed to publish platform formwork: $DENY"
R=$(curl -s -b "$(jar superadmin)" -X POST "$BASE/api/formworks" \
  -H 'content-type: application/json' --data-binary @/tmp/cf_adr.json)
echo "$R" | grep -q '"scope":"platform"' || fail "platform formwork not published: $R"
L=$(curl -s -b "$(jar sa)" "$BASE/api/projects/$PID/formworks")
echo "$L" | grep -q '"artefactType":"ADR"' || fail "platform formwork not visible to project: $L"
echo "$L" | grep -q '"artefactType":"HLD"' || fail "project formwork missing from list"
echo "[4] platform library SUPER_ADMIN-only; project sees both scopes"

# 5. prompt preview shows exactly what agents receive
PV=$(curl -s -b "$(jar po)" "$BASE/api/projects/$PID/canon/preview?stage=2")
echo "$PV" | grep -q 'PROJECT CANON' || fail "canon block missing from preview: $(echo "$PV" | head -c 200)"
echo "$PV" | grep -q 'MUST (non-negotiable)' || fail "priority ordering missing"
echo "$PV" | grep -q 'Hexagonal architecture only' || fail "must-entry missing from block"
echo "$PV" | grep -q 'C4 level 2' || fail "stage-2 scoped entry missing at stage 2"
echo "$PV" | grep -q 'OUTPUT FORMWORKS' || fail "formwork block missing"
echo "$PV" | grep -q 'Required sections, in order' || fail "formwork section directive missing"
PV1=$(curl -s -b "$(jar po)" "$BASE/api/projects/$PID/canon/preview?stage=1")
echo "$PV1" | grep -q 'C4 level 2' && fail "stage-2 entry leaked into stage 1"
echo "[5] prompt preview: priority-ordered canon + formwork directives, stage scoping honoured"

# 6. a real stage run consumes them (SSE shows the agent applying both)
S1=$(curl -s -N --max-time 180 -b "$(jar po)" -X POST "$BASE/api/chat" -H 'content-type: application/json' \
  -d "{\"projectId\":\"$PID\",\"message\":\"Build a settlement reconciliation service\"}")
echo "$S1" | grep -q 'PENDING_REVIEW' || fail "stage 1 run failed: $(echo "$S1" | tail -c 200)"
echo "$S1" | grep -q 'binding rules and decisions' || fail "canon not applied during generation: $(echo "$S1" | head -c 400)"
AUD=$(curl -s -b "$(jar pm)" "$BASE/api/projects/$PID/audit")
echo "$AUD" | grep -q 'canon.created' || fail "canon authoring not audited"
echo "$AUD" | grep -q 'formwork.published' || fail "formwork publication not audited"
echo "[6] agent run applies Canon during generation; authoring is audited"

# 7. lifecycle: disable an entry and it leaves the injected block
EID=$(curl -s -b "$(jar sa)" "$BASE/api/projects/$PID/canon" | grep -o '{[^{]*"title":"Glossary: Settlement"[^}]*}' | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
curl -s -b "$(jar sa)" -X PATCH "$BASE/api/projects/$PID/canon/$EID" -H 'content-type: application/json' \
  -d '{"active":false}' | grep -q '"active":false' || fail "canon disable failed"
PV2=$(curl -s -b "$(jar po)" "$BASE/api/projects/$PID/canon/preview")
echo "$PV2" | grep -q 'Glossary: Settlement' && fail "disabled entry still injected"
echo "[7] disabled canon entries drop out of the injected block"

echo ""
echo "CANON/FORMWORK SMOKE PASS ✅ — binding project rules + mapped output templates shape generation, RBAC enforced"
