"""Rules and Templates: starter packs, organisation rules and opt-outs, drafting, hints, compliance evidence, template suggestions, presets."""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.domain.errors import SdlcError
from app.services import rule_assist as ra
from app.services import rule_packs
from app.services.canon import CanonService
from app.services.formworks import FormworkService, suggest_mapping
from app.services.project_config import ProjectConfigService
from app.services.rule_checks import RuleChecker

from .conftest import FakeAudit, make_user

NOW = datetime(2026, 10, 9, tzinfo=UTC)


class Db:
    def __init__(self):
        self.canon, self.org, self.optouts, self.checks, self.memory, self.presets = [], [], {}, [], {}, []
        self.cfg = None
        self.project = {"id": "p1", "created_by": "u-PROJECT_MANAGER", "tech_stack": "", "tech_stack_source": ""}
        self.artefacts = []
        self._n = 0

    def _id(self):
        self._n += 1
        return f"id{self._n}"

    async def list_canon(self, pid, active_only=True):
        return [r for r in self.canon if r["active"] or not active_only]

    async def insert_canon(self, *, project_id, category, priority, stage, title, body, user_id, origin=None):
        r = {"id": self._id(), "category": category, "priority": priority, "stage": stage, "title": title, "body": body, "active": True, "created_by": user_id,
             "origin": origin, "created_at": NOW, "updated_at": NOW}
        self.canon.append(r)
        return r

    async def list_org_canon(self, active_only=True):
        return [r for r in self.org if r["active"] or not active_only]

    async def insert_org_canon(self, *, category, priority, stage, title, body, user_id):
        r = {"id": self._id(), "category": category, "priority": priority, "stage": stage, "title": title, "body": body, "active": True, "created_by": user_id,
             "created_at": NOW, "updated_at": NOW}
        self.org.append(r)
        return r

    async def list_org_packs(self):
        return []

    async def list_org_optouts(self, pid):
        return [{"org_entry_id": k, "reason": v[0], "opted_out_by": v[1]} for k, v in self.optouts.items()]

    async def set_org_optout(self, pid, eid, reason, by):
        self.optouts[eid] = (reason, by)

    async def clear_org_optout(self, pid, eid):
        self.optouts.pop(eid, None)

    async def get_memory(self, mid):
        return self.memory.get(mid)

    async def replace_canon_checks(self, pid, phase, rows):
        self.checks = [c for c in self.checks if c["phase"] != phase] + [{**r, "phase": phase} for r in rows]

    async def list_canon_checks(self, pid):
        return self.checks

    async def get_project(self, pid):
        return self.project

    async def get_project_config(self, pid):
        return self.cfg

    async def save_project_config(self, pid, config, version, by):
        import copy
        self.cfg = {"config": copy.deepcopy(config), "version": version}

    async def set_project_stack(self, pid, s, src):
        self.project = {**self.project, "tech_stack": s, "tech_stack_source": src}

    async def list_stack_presets(self):
        return self.presets

    async def get_stack_preset(self, pid):
        return next((p for p in self.presets if p["id"] == pid), None)

    async def insert_stack_preset(self, *, name, description, layers, user_id):
        p = {"id": self._id(), "name": name, "description": description, "layers": layers}
        self.presets.append(p)
        return p

    async def delete_stack_preset(self, pid):
        n = len(self.presets)
        self.presets = [p for p in self.presets if p["id"] != pid]
        return len(self.presets) < n

    async def list_phase_artefacts(self, pid, phase):
        return [a for a in self.artefacts if a["phase"] == phase]

    async def artefact_usage_by_type(self, pid):
        return {"HLD": {"count": 2, "phases": [2]}}

    async def list_formworks(self, pid, include_platform=True):
        return []

    async def list_formwork_versions(self, pid, t, f):
        return []


class Authz:
    async def assert_project_access(self, pid, user):
        return None

    async def get_membership_role(self, pid, uid):
        return None


def canon(db=None):
    db = db or Db()
    return CanonService(db, Authz(), FakeAudit()), db


PM, ADMIN, DEV = make_user("PROJECT_MANAGER"), make_user("SUPER_ADMIN"), make_user("DEV")


# ---------------------------------------------------------------- packs
def test_every_shipped_pack_is_valid():
    packs = rule_packs.rule_packs()
    assert {p["id"] for p in packs} >= {"security-baseline", "api-standards", "code-style-naming", "testing-quality", "gdpr", "pci-dss-4", "hipaa", "industry-banking", "bundle-banking-eu"}
    assert all(p["entries"] or p["includes"] for p in packs)
    assert len(rule_packs.stack_presets()) >= 8


async def test_a_pack_adds_its_rules_once():
    svc, db = canon()
    first = await svc.apply_pack("p1", PM, "security-baseline")
    again = await svc.apply_pack("p1", PM, "security-baseline")
    assert first["added"] >= 5 and first["skipped"] == 0 and again["added"] == 0 and again["skipped"] == first["added"]
    assert all(r["origin"] == "pack:security-baseline@1" for r in db.canon)
    with pytest.raises(SdlcError):
        await svc.apply_pack("p1", PM, "nope")
    with pytest.raises(SdlcError) as e:
        await svc.apply_pack("p1", DEV, "security-baseline")
    assert e.value.code == "FORBIDDEN"


# ---------------------------------------------------------------- organisation rules
async def test_organisation_rules_reach_every_project_until_it_opts_out_with_a_reason():
    svc, db = canon()
    org = await svc.org_create(ADMIN, {"category": "rule", "priority": "must", "title": "Encrypt everything", "body": "All data is encrypted."})
    await svc.create("p1", PM, {"title": "Own rule", "body": "Project only.", "category": "rule", "priority": "should"})
    block = await svc.render_block("p1", 3)
    assert "Encrypt everything" in block and "(organisation rule)" in block and "Own rule" in block
    assert block.index("Encrypt everything") < block.index("Own rule")                       # MUST before SHOULD
    inherited = await svc.inherited("p1", PM)
    assert inherited[0]["optedOut"] is False
    with pytest.raises(SdlcError):
        await svc.opt_out("p1", org["id"], PM, "no")                                         # a reason is required
    await svc.opt_out("p1", org["id"], PM, "Legacy system, covered by a waiver")
    assert "Encrypt everything" not in await svc.render_block("p1", 3)
    assert (await svc.inherited("p1", PM))[0]["optOutReason"].startswith("Legacy")
    await svc.opt_in("p1", org["id"], PM)
    assert "Encrypt everything" in await svc.render_block("p1", 3)


async def test_only_administrators_change_organisation_rules():
    svc, _ = canon()
    with pytest.raises(SdlcError) as e:
        await svc.org_create(PM, {"title": "Nope", "body": "x"})
    assert e.value.code == "FORBIDDEN"


async def test_a_memory_becomes_a_rule_but_a_private_one_cannot():
    svc, db = canon()
    db.memory = {"m1": {"id": "m1", "scope": "project", "project_id": "p1", "kind": "convention", "title": "Use SQS FIFO", "body": "Orders go through FIFO queues.", "stage": 3},
                 "m2": {"id": "m2", "scope": "user", "project_id": None, "kind": "working_style", "title": "Terse", "body": "x", "stage": None}}
    e = await svc.from_memory("p1", "m1", PM)
    assert e["title"] == "Use SQS FIFO" and db.canon[0]["origin"] == "memory:m1" and db.canon[0]["stage"] == 3
    with pytest.raises(SdlcError):
        await svc.from_memory("p1", "m2", PM)


# ---------------------------------------------------------------- drafting and hints
def test_keyword_draft_reads_modal_sentences():
    doc = "Services must validate all input. We like tea. Secrets should never be committed to the repository. API endpoints shall be versioned."
    rules = ra.deterministic_draft(doc)
    assert [r["priority"] for r in rules] == ["must", "must", "must"] and rules[0]["title"].startswith("Services must validate")
    assert ra.deterministic_draft("nothing binding here at all, just prose about a day out") == []


class Llm:
    def __init__(self, rules=None, fail=False, verdicts=None):
        self.rules, self.fail, self.verdicts = rules, fail, verdicts

    async def generate_json(self, *, schema, **kw):
        if self.fail:
            raise SdlcError("PROVIDER_ERROR", "down")
        if schema.__name__ == "RuleDraft":
            return schema(rules=self.rules or []), SimpleNamespace(usage={}, model="m", provider="p")
        return schema(results=self.verdicts or []), SimpleNamespace(usage={}, model="m", provider="p")


async def test_draft_uses_the_model_then_falls_back_to_keywords_and_saves_nothing():
    svc, db = canon()
    doc = "Every API must be versioned in the path. Passwords should never appear in logs. " * 2
    good = ra.RuleAssist(Llm([{"category": "rule", "priority": "must", "stage": 3, "title": "Version APIs", "body": "Version in the path."}]), svc)
    out = await good.draft("p1", PM, doc)
    assert out["via"] == "model" and out["rules"][0]["stage"] == 3 and db.canon == []
    out = await ra.RuleAssist(Llm(fail=True), svc).draft("p1", PM, doc)
    assert out["via"] == "keywords" and len(out["rules"]) == 2
    with pytest.raises(SdlcError):
        await good.draft("p1", PM, "too short")


async def test_hints_find_duplicates_contradictions_and_stack_clashes():
    svc, db = canon()
    await svc.create("p1", PM, {"title": "Use Redis for caching", "body": "Use Redis as the cache for session data.", "category": "rule", "priority": "must"})
    cfg = ProjectConfigService(db, SimpleNamespace(put=lambda *a: _noop()), Authz(), FakeAudit(), svc)
    await cfg.update("p1", PM, upsert=[{"layer": "database", "technology": "PostgreSQL"}], remove=[])
    assist = ra.RuleAssist(Llm(), svc, cfg)
    dup = await assist.check("p1", PM, {"title": "Use Redis for caching", "body": "Use Redis as the cache for session data."})
    assert dup["duplicates"] and not dup["ok"]
    clash = await assist.check("p1", PM, {"title": "No Redis", "body": "Do not use Redis anywhere; sessions live in the database."})
    assert clash["conflicts"] and "redis" in clash["conflicts"][0]["why"].lower()
    stack = await assist.check("p1", PM, {"title": "Primary store", "body": "All order data lives in MySQL."})
    assert stack["stack"] == [{"layer": "database", "rule": "MySQL", "pinned": "PostgreSQL"}]
    assert (await assist.check("p1", PM, {"title": "Brand new rule", "body": "Document every public endpoint."}))["ok"]


async def _noop():
    return None


# ---------------------------------------------------------------- compliance
class Content:
    async def get(self, key):
        return None


async def test_compliance_check_records_the_latest_verdicts_per_stage():
    svc, db = canon()
    r1 = await svc.create("p1", PM, {"title": "Encrypt data", "body": "Encrypt at rest.", "category": "rule", "priority": "must"})
    await svc.create("p1", PM, {"title": "Nice to have", "body": "x", "category": "preference", "priority": "should"})
    db.artefacts = [{"id": "a1", "phase": 3, "title": "LLD", "storage_key": None, "content": "Data is stored in plain text.", "is_latest": True}]
    verdicts = [{"rule_id": r1["entry"]["id"] if "entry" in r1 else r1["id"], "status": "violated", "artefact": "LLD", "evidence": "plain text storage"},
                {"rule_id": "unknown", "status": "complied"}]
    checker = RuleChecker(db, Content(), Llm(verdicts=verdicts), svc, SimpleNamespace(CANON_CHECK_ENABLED=True))
    assert await checker.check_stage("p1", 3, 3) == 1                                         # the unknown rule is ignored
    summary = await checker.summary("p1")
    assert summary["rules"][r1["id"]]["violated"] == 1 and summary["rules"][r1["id"]]["violations"][0]["artefact"] == "LLD"
    assert summary["artefacts"]["a1"]["items"][0]["status"] == "violated"
    off = RuleChecker(db, Content(), Llm(), svc, SimpleNamespace(CANON_CHECK_ENABLED=False))
    assert await off.check_stage("p1", 3, 3) == 0
    broken = RuleChecker(db, Content(), Llm(fail=True), svc, SimpleNamespace(CANON_CHECK_ENABLED=True))
    assert await broken.check_stage("p1", 3, 3) == 0                                          # never raises


# ---------------------------------------------------------------- templates
def test_a_dropped_template_is_recognised():
    s = suggest_mapping("High-Level-Design.md", "# {{service}} High-Level Design\n\n## Context\n\n## Solution architecture\n\n## Risks\n")
    assert s["artefactType"] == "HLD" and s["outputFormat"] == "markdown" and "Context" in s["sections"] and "service" in s["placeholders"]
    assert suggest_mapping("adr-template.md", "# ADR\n## Status\n## Decision\n## Consequences\n")["artefactType"] == "ADR"
    assert suggest_mapping("notes.txt", "just some words with nothing to go on")["artefactType"] is None
    assert suggest_mapping("api.yaml", "openapi: 3.0.0\npaths:\n  /x: {}\n")["outputFormat"] == "yaml"


async def test_template_list_shows_usage_and_versions():
    class FwDb(Db):
        async def list_formworks(self, pid, include_platform=True):
            base = {"id": "f1", "artefact_type": "HLD", "output_format": "markdown", "name": "House HLD", "template": "# x", "analysis": {}, "storage_key": None,
                    "created_by": "u", "created_at": NOW}
            return [{**base, "project_id": "p1"}, {**base, "id": "f2", "project_id": None}]

        async def list_formwork_versions(self, pid, t, f):
            return [1, 2, 3]

    db = FwDb()
    fw = FormworkService(db, Authz(), FakeAudit(), Content(), SimpleNamespace(assert_can_author=lambda *a: _noop()))
    rows = await fw.list("p1", PM)
    mine = next(r for r in rows if r["scope"] == "project")
    assert mine["overrides"] is True and mine["usage"]["count"] == 2 and mine["versions"] == 3
    assert next(r for r in rows if r["scope"] == "platform")["overrides"] is False


# ---------------------------------------------------------------- stack presets
async def test_a_preset_pins_its_layers_and_only_admins_add_organisation_presets():
    db = Db()
    cfg = ProjectConfigService(db, SimpleNamespace(put=lambda *a: _noop()), Authz(), FakeAudit(), CanonService(db, Authz(), FakeAudit()))
    names = [p["name"] for p in await cfg.presets()]
    assert "AWS serverless, Python" in names
    out = await cfg.apply_preset("p1", PM, "pack:aws-serverless-python")
    got = {e["layer"]: (e["technology"], e["status"], e["source"]) for e in out["stack"]["layers"]}
    assert got["hosting"] == ("AWS", "pinned", "preset") and got["backend"][0] == "Python"
    assert db.project["tech_stack"].startswith("Python 3.12")
    with pytest.raises(SdlcError):
        await cfg.create_preset(PM, "My preset", "", [{"layer": "hosting", "technology": "AWS"}])
    p = await cfg.create_preset(ADMIN, "Our standard", "d", [{"layer": "hosting", "technology": "Azure"}])
    assert p["name"] == "Our standard" and any(x["name"] == "Our standard" for x in await cfg.presets())
    with pytest.raises(SdlcError):
        await cfg.apply_preset("p1", PM, "missing")
