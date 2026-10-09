"""projectconfig.json: the stack by layer, who wins, what each stage is told, and how the advisor fills it in."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agents.prompts import render_stack
from app.domain.errors import SdlcError
from app.services import project_config as pc
from app.services import stack_advisor as sa
from app.services.project_config import ProjectConfigService, legacy_entries, merge_identified, normalise_entry, render_layers, summary_of

from .conftest import FakeAudit, make_user


def entry(layer, tech, version="", status="identified", stage=2, **kw):
    return normalise_entry({"layer": layer, "technology": tech, "version": version, **kw}, status=status, source="user" if status == "pinned" else "llm", stage=stage)


def cfg_with(*layers):
    c = pc.empty_config()
    c["stack"]["layers"] = list(layers)
    return c


def test_empty_config_and_summary():
    c = pc.empty_config()
    assert c["stack"]["layers"] == [] and summary_of([]) == ""
    layers = [entry("backend", "Python", "3.12", extras=["FastAPI"]), entry("database", "PostgreSQL", "16"), entry("hosting", "AWS")]
    assert summary_of(layers) == "Python 3.12 + FastAPI | PostgreSQL 16"
    assert summary_of([entry("hosting", "AWS")]) == ""                   # no language yet: still "undecided" for everything that reads one string


def test_entries_are_validated():
    with pytest.raises(SdlcError):
        normalise_entry({"layer": "nonsense", "technology": "x"}, status="pinned", source="user")
    with pytest.raises(SdlcError):
        normalise_entry({"layer": "cache", "technology": ""}, status="pinned", source="user")
    assert normalise_entry({"layer": "cache", "technology": ""}, status="open", source="user")["status"] == "open"
    assert normalise_entry({"layer": "backend", "technology": "Java", "component": "Orders Service"}, status="pinned", source="user")["id"] == "backend/orders-service"


def test_a_pinned_value_is_never_changed_and_a_disagreement_is_recorded():
    c = cfg_with(entry("hosting", "AWS", status="pinned"))
    changes = merge_identified(c, [entry("hosting", "Azure")], stage=2, source="llm")
    assert changes == [] and c["stack"]["layers"][0]["technology"] == "AWS"
    assert c["stack"]["conflicts"][0]["found"] == "Azure" and c["stack"]["conflicts"][0]["pinned"] == "AWS"
    merge_identified(c, [entry("hosting", "Azure")], stage=2, source="llm")
    assert len(c["stack"]["conflicts"]) == 1                              # not repeated
    assert merge_identified(c, [entry("hosting", "aws")], stage=2, source="llm") == []   # agreeing is not a conflict


def test_open_layers_are_filled_and_later_stages_replace_earlier_ones():
    c = cfg_with(normalise_entry({"layer": "database", "technology": ""}, status="open", source="user"))
    ch = merge_identified(c, [entry("database", "DynamoDB")], stage=2, source="llm")
    assert ch[0]["action"] == "filled" and c["stack"]["layers"][0]["technology"] == "DynamoDB"
    ch = merge_identified(c, [entry("database", "PostgreSQL")], stage=1, source="llm")        # an earlier stage cannot undo a later one
    assert ch == [] and c["stack"]["layers"][0]["technology"] == "DynamoDB"
    ch = merge_identified(c, [entry("database", "PostgreSQL", "16")], stage=3, source="ta", decider=True)
    assert ch[0]["action"] == "revised" and c["stack"]["layers"][0]["technology"] == "PostgreSQL"


def test_each_stage_is_told_only_the_layers_it_needs():
    layers = [entry("hosting", "AWS", status="pinned"), entry("frontend", "React", "18"), entry("backend", "Python", "3.12"),
              entry("iac", "Terraform"), entry("cicd", "GitHub Actions")]
    one, _ = render_layers(layers, 1)
    assert "AWS" in one and "React" not in one and "Terraform" not in one                    # requirements: constraints only
    five, _ = render_layers(layers, 5)
    assert "Terraform" in five and "GitHub Actions" in five and "React" not in five          # devops
    six, undecided6 = render_layers(layers, 6)
    assert "React" in six and "Python 3.12" in six and "Terraform" not in six and "Database" in undecided6
    three, _ = render_layers(layers, 3)
    assert all(x in three for x in ("AWS", "React", "Python", "Terraform", "GitHub Actions"))


def test_render_stack_by_layer():
    layers = [entry("backend", "Python", "3.12", extras=["FastAPI"], status="pinned"), entry("hosting", "AWS")]
    other = render_stack("Python 3.12 + FastAPI", owner=False, layers=layers, stage=5)
    assert "AWS [identified in the project's documents]" in other and "Not decided yet" in other
    ta = render_stack("Python 3.12 + FastAPI", owner=True, layers=layers, stage=3)
    assert "YOU decide it" in ta and "[pinned by the team]" in ta and "Still undecided" in ta
    assert "NOT DECIDED YET" in render_stack("", owner=False, layers=[], stage=2)
    assert "Target technology stack: Go 1.22" in render_stack("Go 1.22", owner=False)         # no layers: the one-line stack, as before


def test_legacy_one_line_stack_becomes_entries():
    e = legacy_entries("Python 3.12 + FastAPI, Pytest | PostgreSQL 16", "user")
    assert [(x["layer"], x["technology"], x["version"], x["status"]) for x in e] == [("backend", "Python", "3.12", "pinned"), ("database", "PostgreSQL", "16", "pinned")]
    assert e[0]["extras"] == ["FastAPI, Pytest"]
    assert legacy_entries("", "ta") == [] and legacy_entries("Go 1.22 + Gin", "ta")[0]["status"] == "identified"


# ---------------------------------------------------------------- the service
class Db:
    def __init__(self, project=None):
        self.cfg = None
        self.project = project or {"id": "p1", "created_by": "u-PROJECT_MANAGER", "tech_stack": "", "tech_stack_source": ""}
        self.stack_sets: list[tuple] = []

    async def get_project(self, pid):
        return self.project

    async def get_project_config(self, pid):
        return self.cfg

    async def save_project_config(self, pid, config, version, updated_by):
        import copy
        self.cfg = {"config": copy.deepcopy(config), "version": version}

    async def set_project_stack(self, pid, stack, source):
        self.stack_sets.append((stack, source))
        self.project = {**self.project, "tech_stack": stack, "tech_stack_source": source}


class Content:
    def __init__(self):
        self.k = {}

    async def put(self, key, body):
        self.k[key] = body


class Canon:
    async def assert_can_author(self, pid, user):
        if user.role not in ("SUPER_ADMIN", "PROJECT_MANAGER"):
            raise SdlcError("FORBIDDEN", "no")


def service(db=None):
    db = db or Db()
    content = Content()
    return ProjectConfigService(db, content, SimpleNamespace(assert_project_access=lambda *a: _none()), FakeAudit(), Canon()), db, content


async def _none():
    return None


async def test_created_empty_mirrored_to_a_file_and_legacy_stack_migrated():
    svc, db, content = service(Db({"id": "p1", "created_by": "x", "tech_stack": "Go 1.22 + Gin", "tech_stack_source": "ta"}))
    cfg = await svc.get("p1")
    assert cfg["stack"]["layers"][0]["technology"] == "Go" and cfg["stack"]["summary"] == "Go 1.22 + Gin"
    assert "projectconfig.json" in next(iter(content.k)) and '"schemaVersion": 1' in next(iter(content.k.values()))
    svc2, _, _ = service()
    assert (await svc2.get("p1"))["stack"]["layers"] == []                                   # a new project: empty


async def test_a_person_pins_and_the_platform_cannot_overwrite_it():
    svc, db, content = service()
    pm = make_user("PROJECT_MANAGER")
    await svc.update("p1", pm, upsert=[{"layer": "hosting", "technology": "AWS"}, {"layer": "backend", "technology": "Python", "version": "3.12", "extras": ["FastAPI"]},
                                       {"layer": "database", "status": "open"}], remove=[])
    assert db.project["tech_stack"] == "Python 3.12 + FastAPI" and db.project["tech_stack_source"] == "user"
    changes = await svc.apply_found("p1", [{"layer": "hosting", "technology": "Azure"}, {"layer": "database", "technology": "PostgreSQL"}], stage=2, source="llm")
    assert [c["layer"] for c in changes] == ["database"]                                      # hosting is pinned, the open database was filled
    cfg = await svc.get("p1")
    assert {e["layer"]: (e["technology"], e["status"]) for e in cfg["stack"]["layers"]}["hosting"] == ("AWS", "pinned")
    assert cfg["stack"]["conflicts"][0]["found"] == "Azure"
    assert "project.config_updated" in svc._audit.events


async def test_confirm_pins_identified_values_and_only_authorised_people_edit():
    svc, db, _ = service()
    await svc.apply_found("p1", [{"layer": "messaging", "technology": "SQS"}], stage=2, source="llm")
    assert (await svc.get("p1"))["stack"]["layers"][0]["status"] == "identified"
    with pytest.raises(SdlcError) as e:
        await svc.confirm("p1", make_user("DEV"), ["messaging"])
    assert e.value.code == "FORBIDDEN"
    cfg = await svc.confirm("p1", make_user("PROJECT_MANAGER"), ["messaging"])
    assert cfg["stack"]["layers"][0]["status"] == "pinned"


async def test_clearing_the_one_line_stack_removes_backend_and_database():
    svc, db, _ = service()
    await svc.set_backend_text("p1", "Python 3.12 + FastAPI | PostgreSQL 16", source="user", actor="a@b")
    assert db.project["tech_stack"] == "Python 3.12 + FastAPI | PostgreSQL 16"
    await svc.set_backend_text("p1", "", source="user", actor="a@b")
    assert db.project["tech_stack"] == "" and (await svc.get("p1"))["stack"]["layers"] == []


# ---------------------------------------------------------------- the advisor
def test_deterministic_extractor_finds_common_layers():
    text = "The service runs on AWS using SQS for messaging and Redis as a cache. Data lives in PostgreSQL 16. Deployed with Terraform and GitHub Actions."
    found = {f["layer"]: f["technology"] for f in sa.detect_from_text(text)}
    assert found["hosting"] == "AWS" and found["messaging"] == "SQS" and found["cache"] == "Redis" and found["database"] == "PostgreSQL"
    assert found["iac"] == "Terraform" and found["cicd"] == "GitHub Actions"
    assert sa.detect_from_text("") == [] and sa.detect_from_text("nothing technical here") == []


def test_technical_architect_layer_lines_are_parsed():
    lld = "# LLD\n\n## Technology stack decision\n**Stack:** Python 3.12 + FastAPI | PostgreSQL 16\n- Hosting: AWS\n- Messaging and events: SQS\n- Frontend: React 18 + TypeScript\n- Observability: n/a\n\n## Next\n- Cache: nonsense\n"
    got = {f["layer"]: (f["technology"], f["version"], f["extras"]) for f in sa.parse_decision_layers(lld)}
    assert got["hosting"][0] == "AWS" and got["messaging"][0] == "SQS" and got["frontend"] == ("React", "18", ["TypeScript"])
    assert "observability" not in got and "cache" not in got               # n/a is skipped; a line after the section is not read


def test_codebase_detection_from_manifests():
    files = {"pom.xml": "<parent>spring-boot-starter-parent</parent>", "infra/main.tf": 'provider "aws" { source = "hashicorp/aws" }',
             ".github/workflows/ci.yml": "", "Dockerfile": "", "web/package.json": '{"dependencies": {"react": "18", "next": "14"}}',
             "docker-compose.yml": "services:\n  db:\n    image: postgres:16\n  cache:\n    image: redis:7"}
    found = {(f["layer"], f["technology"]) for f in sa.detect_from_codebase(files)}
    assert {("backend", "Java"), ("iac", "Terraform"), ("hosting", "AWS"), ("cicd", "GitHub Actions"), ("compute", "Containers"),
            ("frontend", "React"), ("frontend", "Next.js"), ("database", "PostgreSQL"), ("cache", "Redis")} <= found


class Llm:
    def __init__(self, layers=None, fail=False):
        self.layers, self.fail, self.calls = layers or [], fail, 0

    async def generate_json(self, *, schema, **kw):
        self.calls += 1
        if self.fail:
            raise SdlcError("PROVIDER_ERROR", "down")
        return schema(layers=self.layers), SimpleNamespace(usage={}, model="m", provider="p")


async def test_advisor_uses_the_model_and_drops_low_confidence_guesses():
    svc, db, _ = service()
    llm = Llm([{"layer": "hosting", "technology": "AWS", "evidence": "must run on AWS", "confidence": "high"},
               {"layer": "cache", "technology": "Redis", "confidence": "low"}])
    adv = sa.StackAdvisor(llm, svc, db)
    changes = await adv.after_stage("p1", seq=2, template=2, documents=[("HLD", "The platform runs on AWS.")])
    assert [c["layer"] for c in changes] == ["hosting"]
    layers = (await svc.get("p1"))["stack"]["layers"]
    assert layers[0]["status"] == "identified" and layers[0]["sourceStage"] == 2 and "AWS" in layers[0]["evidence"]
    assert (await svc.get("p1"))["stack"]["advisor"]["2"]["found"] == 1


async def test_advisor_falls_back_to_keywords_when_the_model_fails_or_says_nothing():
    for llm in (Llm(fail=True), Llm([])):
        svc, db, _ = service()
        changes = await sa.StackAdvisor(llm, svc, db).after_stage("p1", seq=1, template=1, documents=[("PRD", "Messages go through Kafka and data is stored in MongoDB.")])
        assert {c["layer"] for c in changes} == {"messaging", "database"}


async def test_technical_architect_decisions_win_over_earlier_identification():
    svc, db, _ = service()
    adv = sa.StackAdvisor(Llm([]), svc, db)
    await adv.after_stage("p1", seq=2, template=2, documents=[("HLD", "Orders are queued on RabbitMQ.")])
    await adv.after_stage("p1", seq=3, template=3, documents=[("LLD", "## Technology stack decision\n**Stack:** Java 21 + Spring Boot | PostgreSQL 16\n- Messaging and events: SQS\n")], decider=True)
    got = {e["layer"]: e["technology"] for e in (await svc.get("p1"))["stack"]["layers"]}
    assert got["messaging"] == "SQS"
