# Project config, layered stack, rules and templates (final design)

Agreed with the project owner. This replaces the single-string technology stack and reworks the Canon / Formwork screens.

## 1. `projectconfig.json`

Every project has one config document, empty at creation and filled in over time:

```json
{ "schemaVersion": 1, "version": 3, "updatedAt": "...",
  "stack": { "summary": "Python 3.12 + FastAPI | PostgreSQL 16",
             "layers": [ { "id": "backend", "layer": "backend", "component": "", "technology": "Python", "version": "3.12",
                           "extras": ["FastAPI"], "notes": "", "status": "pinned", "source": "user", "sourceStage": null,
                           "rationale": "", "evidence": "", "confidence": "high", "updatedAt": "...", "updatedBy": "..." } ],
             "conflicts": [], "advisor": { "1": { "at": "...", "found": 4 } } } }
```

* Source of truth is the `project_config` table (migration `0044`); every write is mirrored to the content store as a real file,
  `content-store/{project}/projectconfig.json`, shown in the Files tab. It is created empty with the project (and lazily for old projects).
* `projects.tech_stack` stays as the rendered one-line **summary** (backend and database), so everything that reads one string keeps working.
* Layers (catalog in `tech_catalog.py`, overridable by `TECH_CATALOG_PATH`): frontend, backend, database, cache, messaging, search, hosting
  (AWS / Azure / GCP / on-prem / hybrid), compute, iac, cicd, observability, identity. A layer can have several entries (optionally labelled by
  `component`, e.g. an `orders-service`).

### Status and who wins

| Status | Meaning | Who sets it | Can the AI change it? |
|---|---|---|---|
| `pinned` | decided by a person (or applied from an organisation preset) | authorised person | never |
| `identified` | found in the project's own documents (stage 1, stage 2) or decided by the Technical Architect (stage 3), or detected in an uploaded codebase | the platform | yes, by a later stage |
| `open` | left on purpose for the Technical Architect | authorised person | yes, it fills it |

A later stage replaces an earlier `identified` value. If a document disagrees with a pinned layer, the pinned value stays and a **conflict** is
recorded and shown on the screen. Authorised = super-admin, the managing PM, and SA / TA members (the same people who author rules).

### How the AI advises

After stage 1 and stage 2 generate, and again after stage 3, the **stack advisor** (a light-role agent, `agents/pipeline/stack-advisor.md`) reads
the stage's documents and returns a structured list of layers: only technologies the documents state, require, or necessarily imply, each with
evidence and confidence. Low-confidence guesses are dropped. A deterministic extractor covers the case where the model is unavailable, and
an uploaded codebase is detected from its files (`pom.xml`, `package.json`, `Dockerfile`, Terraform, compose files).
Stage 3 (Technical Architect) fills the `open` and undecided layers and justifies them in its "Technology stack decision" section, one
`- Layer: value` line per layer.

### What each stage sees

Prompts get a stage-aware block, not the whole config: requirements gets hosting constraints only; architecture gets hosting, compute, data,
messaging and identity; technical design gets all layers; test engineering gets app layers, data and CI/CD; DevOps gets hosting, compute,
IaC, CI/CD, observability; development gets the application and data layers. Layers nobody has decided are listed as undecided and the stage
is told to stay neutral on them.

### Screen and API

Project Context -> **Stack** tab: layers grouped as the catalog, a chip for pinned / from stage 2 / open, edit, confirm (identified -> pinned),
"leave open", remove, "Re-check from documents", organisation presets, and the conflict list.
`GET /api/projects/{id}/config`, `PUT /api/projects/{id}/config/stack` (`{upsert, remove}`), `POST .../config/stack/advise`,
`POST .../config/stack/preset/{id}`, `GET .../config/file`. Every change is audited (`project.config_updated`).

## 2. Rules and Templates

"Canon" becomes **Rules** and "Formwork" becomes **Templates** (the old names stay as subtitles). One Project Context page with four tabs:
Stack, Rules, Templates, **What agents see**.

* **What agents see**: per stage, the exact stack, rules and template text injected, with a token estimate.
* **Starter packs** (`rule_packs/`): security baseline, API standards, naming and code style, data protection; one click adds them.
* **Organisation rules and stack presets** (admins, in Governance): projects inherit them, read-only, with a reasoned per-project opt-out.
* **Draft rules from a document**: paste or upload a standards document, the assistant proposes rules, a person accepts, edits or drops each.
* **Save as rule**: promote a suggested or confirmed memory to a binding rule.
* **Evidence the rules work**: after a stage generates, a light check compares each artefact with the must-rules; every rule shows applied /
  violated counts and every artefact shows a rules chip. Toggle `CANON_CHECK_ENABLED`.
* **Conflict hints** when adding a rule: duplicates, contradictions with other rules, clashes with a pinned layer. Non-blocking.
* **Templates**: drop a file, type and format are detected for you to confirm; section preview; which artefacts use it; versions; a project
  template overrides a platform one (badge); the plan's per-artefact layout picker already offers templates.
* Search, stage filter and priority sort on the rule list.

Migration `0045`: `org_canon`, `org_canon_optout`, `canon_checks`, `org_stack_presets`, `project_canon.pack_id`.

## 3. Phasing
1. Config + layered stack + advisor + prompts + screen. 2. Rules and Templates UX. 3. Code generation proposes one folder per application layer
(`apps/web`, `services/api`, `infra/`) when several layers exist; a full per-component polyglot generator is out of scope for now.
