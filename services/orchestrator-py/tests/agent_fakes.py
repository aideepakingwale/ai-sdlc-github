"""In-memory stand-ins for the custom agent tests: a repository, project access, and a model that can be told to misbehave."""
from __future__ import annotations

import copy
import json
import re
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from app.domain.errors import SdlcError
from app.services.agent_assist import Draft, Judgement
from app.services.agent_audit import AgentAuditor, ModelReport
from app.services.agent_defs import AgentDefService
from app.services.agent_runtime import AgentOutputs, AgentRuntime
from app.services.agent_usage import AgentUsage

from .conftest import FakeAudit, make_user

NOW = datetime(2026, 10, 10, tzinfo=UTC)
SUPER, PM, OTHER_PM, AUTHOR, APPROVER, MEMBER = (make_user("SUPER_ADMIN"), make_user("PROJECT_MANAGER"), SimpleNamespace(**{**make_user("PROJECT_MANAGER").model_dump(), "id": "u-other-pm", "email": "other@sdlc.local", "displayName": "Other PM"}),
                                                  SimpleNamespace(**{**make_user("DEV").model_dump(), "id": "u-author", "email": "author@sdlc.local", "displayName": "Maya"}),
                                                  SimpleNamespace(**{**make_user("SA").model_dump(), "id": "u-approver", "email": "approver@sdlc.local", "displayName": "Ade"}),
                                                  SimpleNamespace(**{**make_user("QA").model_dump(), "id": "u-member", "email": "member@sdlc.local", "displayName": "Sam"}))


class Db:
    def __init__(self) -> None:
        self.projects = {"p1": {"id": "p1", "created_by": PM.id}, "p2": {"id": "p2", "created_by": OTHER_PM.id}}

    async def get_project(self, pid: str):
        return self.projects.get(pid)


class Authz:
    """Team: p1 has PM (creator), author, approver and member; p2 has only its own PM."""
    TEAM = {("p1", AUTHOR.id): "DEV", ("p1", APPROVER.id): "SA", ("p1", MEMBER.id): "QA", ("p2", OTHER_PM.id): "PM"}

    async def assert_project_access(self, pid: str, user: Any) -> None:
        if user.role == "SUPER_ADMIN" or (pid == "p1" and user.id == PM.id) or (pid == "p2" and user.id == OTHER_PM.id) or (pid, user.id) in self.TEAM:
            return
        raise SdlcError("FORBIDDEN", "You are not a member of this project")

    async def get_membership_role(self, pid: str, uid: str):
        return self.TEAM.get((pid, uid))


class MemRepo:
    def __init__(self) -> None:
        self.defs: dict[str, dict] = {}
        self.versions: dict[str, list[dict]] = {}
        self.att: list[dict] = []
        self.grants: dict[tuple, dict] = {}
        self.guard: dict[str, str] = {}
        self.cases: dict[str, dict] = {}
        self.usage: list[dict] = []
        self.limits: dict[str, int] = {}
        self.runs: list[dict] = []
        self.core_vis: dict[tuple[str, str], bool] = {}

    async def insert_def(self, d):
        r = {"open": False, "retired": False, "source_kind": None, "source_id": None, "source_name": None, "source_version": None, "project_id": None, "created_at": NOW, "updated_at": NOW, **d}
        self.defs[d["id"]] = r
        self.versions[d["id"]] = []
        return dict(r)

    async def get_def(self, i):
        return dict(self.defs[i]) if i in self.defs else None

    async def list_defs(self, *, scope=None, kind=None, project_id=None, open_only=False, include_retired=False):
        out = [d for d in self.defs.values() if (scope is None or d["scope"] == scope) and (kind is None or d["kind"] == kind) and (project_id is None or d["project_id"] == project_id)
               and (not open_only or d["open"]) and (include_retired or not d["retired"])]
        return [dict(d) for d in sorted(out, key=lambda d: d["name"].lower())]

    async def update_def(self, i, **f):
        self.defs[i].update({k: v for k, v in f.items() if k in ("name", "open", "retired")})

    async def delete_def(self, i):
        self.defs.pop(i, None)
        self.versions.pop(i, None)
        self.att = [a for a in self.att if a["def_id"] != i]

    async def count_defs_by_project(self):
        out: dict[str, int] = {}
        for d in self.defs.values():
            if d["scope"] == "project" and not d["retired"]:
                out[d["project_id"]] = out.get(d["project_id"], 0) + 1
        return out

    async def insert_version(self, def_id, version, status, body, *, author, author_name):
        v = {"def_id": def_id, "version": version, "status": status, "body": copy.deepcopy(body), "audit": None, "author": author, "author_name": author_name, "submitted_by": None,
             "submitted_by_name": None, "submitted_at": None, "decided_by": None, "decided_by_name": None, "decided_at": None, "decision_comment": None, "created_at": NOW, "updated_at": NOW}
        self.versions[def_id].append(v)
        return dict(v)

    async def get_version(self, def_id, version):
        return next((dict(v) for v in self.versions.get(def_id, []) if v["version"] == version), None)

    async def list_versions(self, def_id):
        return [copy.deepcopy(v) for v in sorted(self.versions.get(def_id, []), key=lambda v: -v["version"])]

    async def update_version(self, def_id, version, **f):
        v = next(v for v in self.versions[def_id] if v["version"] == version)
        v.update(copy.deepcopy(f))

    async def pending_versions(self, project_id=None):
        out = []
        for did, vs in self.versions.items():
            d = self.defs[did]
            if d["retired"] or (project_id and d["project_id"] != project_id) or (not project_id and d["scope"] != "org"):
                continue
            out += [{**v, "kind": d["kind"], "name": d["name"], "scope": d["scope"], "project_id": d["project_id"], "source_name": d["source_name"], "source_version": d["source_version"]} for v in vs if v["status"] == "pending"]
        return out

    async def list_attachments(self, project_id, stage_key=None):
        return sorted([dict(a) for a in self.att if a["project_id"] == project_id and (stage_key is None or a["stage_key"] == stage_key)], key=lambda a: a["position"])

    async def replace_attachments(self, project_id, stage_key, items, user_id):
        self.att = [a for a in self.att if not (a["project_id"] == project_id and a["stage_key"] == stage_key)]
        for i, it in enumerate(items):
            self.att.append({"project_id": project_id, "stage_key": stage_key, "def_id": it["def_id"], "pinned_version": it["pinned_version"], "runs": it.get("runs", "always"),
                             "condition": it.get("condition", ""), "roles": list(it.get("roles") or []), "position": i})

    async def attachments_for_def(self, def_id):
        return [dict(a) for a in self.att if a["def_id"] == def_id]

    async def drop_attachments_for_def(self, def_id, project_id=None):
        self.att = [a for a in self.att if not (a["def_id"] == def_id and (project_id is None or a["project_id"] == project_id))]

    async def list_grants(self, project_id):
        return [{**g, "email": "", "display_name": g["user_id"]} for (p, _), g in self.grants.items() if p == project_id]

    async def get_grant(self, project_id, user_id):
        return self.grants.get((project_id, user_id))

    async def set_grant(self, project_id, user_id, can_edit, can_approve, granted_by):
        if not (can_edit or can_approve):
            self.grants.pop((project_id, user_id), None)
        else:
            self.grants[(project_id, user_id)] = {"project_id": project_id, "user_id": user_id, "can_edit": can_edit, "can_approve": can_approve}

    async def list_guardrail_overrides(self):
        return dict(self.guard)

    async def set_guardrail(self, gid, severity, user_id):
        self.guard[gid] = severity

    async def list_cases(self, def_id):
        return [c for c in self.cases.values() if c["def_id"] == def_id]

    async def insert_case(self, case_id, def_id, name, inputs, user_id):
        self.cases[case_id] = {"id": case_id, "def_id": def_id, "name": name, "inputs": inputs}
        return self.cases[case_id]

    async def delete_case(self, def_id, case_id):
        self.cases.pop(case_id, None)




async def _record_usage(self, rows):
    for r in rows:
        self.usage.append({**r, "at": NOW})


async def _usage_for_def(self, def_id, days=30):
    out: dict[tuple, dict] = {}
    for u in self.usage:
        if u["def_id"] == def_id:
            k = (NOW.date(), u["source"])
            o = out.setdefault(k, {"day": NOW.date(), "source": u["source"], "runs": 0, "prompt": 0, "completion": 0})
            o["runs"] += 1
            o["prompt"] += u["prompt"]
            o["completion"] += u["completion"]
    return list(out.values())


async def _usage_for_project(self, project_id, since):
    out: dict[str, dict] = {}
    for u in self.usage:
        if u["project_id"] == project_id:
            o = out.setdefault(u["def_id"], {"def_id": u["def_id"], "runs": 0, "prompt": 0, "completion": 0})
            o["runs"] += 1
            o["prompt"] += u["prompt"]
            o["completion"] += u["completion"]
    return list(out.values())


async def _get_limit(self, project_id):
    return self.limits.get(project_id)


async def _set_limit(self, project_id, monthly, user_id):
    if monthly is None:
        self.limits.pop(project_id, None)
    else:
        self.limits[project_id] = monthly


async def _insert_run(self, r):
    self.runs.append({**r, "created_at": NOW})


async def _list_runs(self, project_id, def_id=None, limit=20):
    return [dict(r) for r in reversed(self.runs) if r["project_id"] == project_id and (def_id is None or r["def_id"] == def_id)][:limit]


async def _core_visibility(self):
    return dict(self.core_vis)


async def _set_core_visibility(self, kind, core_id, public, user_id):
    self.core_vis[(kind, core_id)] = public


MemRepo.core_visibility, MemRepo.set_core_visibility = _core_visibility, _set_core_visibility
MemRepo.insert_run, MemRepo.list_runs = _insert_run, _list_runs
MemRepo.record_usage, MemRepo.usage_for_def, MemRepo.usage_for_project, MemRepo.get_limit, MemRepo.set_limit = _record_usage, _usage_for_def, _usage_for_project, _get_limit, _set_limit


class FakeLlm:
    """Answers declared outputs; can be told to leak, to follow injected instructions, to fail, or to report audit findings."""
    def __init__(self) -> None:
        self.follow: set[str] = set()          # markers (OVERRIDE-, INDIRECT-, SKIPPED-, SENT-) the agent will obey
        self.reveal = False
        self.model_findings: list[dict] = []
        self.fail_models: set[str] = set()
        self.calls: list[dict] = []
        self.outputs: dict[str, Any] | None = None
        self.provider = "fake"
        self.tokens = (100, 20)                # (prompt, completion) reported for every call
        self.by_marker: dict[str, dict] = {}   # a marker in the system prompt -> the outputs an agent with that prompt returns
        self.draft: dict | None = None         # what the drafter returns
        self.prefer: str | None = None         # the judge gives 9 to the answer containing this text and 3 to the other

    def _res(self, content: str):
        return SimpleNamespace(provider="fake", model="fake-1", content=content, usage={"promptTokens": self.tokens[0], "completionTokens": self.tokens[1]}, truncated=False)

    async def generate_json(self, *, schema, messages, model=None, role=None, tag=None, **kw):
        self.calls.append({"tag": tag, "role": role, "model": model, "messages": messages})
        if model and model in self.fail_models:
            raise SdlcError("PROVIDER_ERROR", "model down")
        if schema is ModelReport:
            return ModelReport.model_validate({"findings": self.model_findings}), self._res("{}")
        if schema is Draft:
            return Draft.model_validate(self.draft or {}), self._res("{}")
        if schema is Judgement:
            text = messages[-1]["content"]
            a1 = re.search(r"<answer_1>(.*?)</answer_1>", text, re.S).group(1)
            hit1 = bool(self.prefer) and self.prefer in a1
            hit2 = bool(self.prefer) and self.prefer in re.search(r"<answer_2>(.*?)</answer_2>", text, re.S).group(1)
            scores = (9, 3) if hit1 and not hit2 else (3, 9) if hit2 and not hit1 else (7, 7)
            return Judgement(winner="1" if scores[0] > scores[1] else "2" if scores[1] > scores[0] else "tie", score_1=scores[0], score_2=scores[1], reason="because"), self._res("{}")
        user = messages[-1]["content"]
        m = re.search(r"OUTPUT_SCHEMA:\s*(\{.*\})", user)
        spec = json.loads(m.group(1)) if m else {}
        sample = {"string": "text result", "number": 0.9, "boolean": True, "object": {"k": "v"}, "list": ["a", "b"]}
        outs = self.outputs if self.outputs is not None else {k: sample[t] for k, t in spec.items()}
        marked = next((v for k, v in self.by_marker.items() if k in messages[0]["content"]), None)
        outs = marked if marked is not None else outs
        res = self._res(json.dumps({"outputs": outs}))
        res.provider = self.provider
        return AgentOutputs.model_validate({"outputs": outs}), res

    async def generate(self, *, messages, model=None, **kw):
        self.calls.append({"tag": kw.get("tag"), "model": model, "messages": messages})
        text = " ".join(m["content"] for m in messages)
        reply = "I will help with that."
        for marker in self.follow:
            hit = re.search(marker + r"[A-Z0-9]+", text)
            if hit:
                reply += " " + hit.group(0)
        if self.reveal:
            hit = re.search(r"CANARY-[A-Z0-9]+", text)
            reply += " " + (hit.group(0) if hit else "")
        return self._res(reply)


class Canon:
    async def active_rules(self, pid):
        return [{"priority": "must", "title": "No secrets in code", "body": "Never put secrets in code."}]

    async def render_block(self, pid, tpl=None):
        return "## PROJECT CANON\nMUST: no secrets in code."


class FakeWorkflow:
    """A small workflow: Requirements (writes PRD) -> Architecture (writes HLD) -> Test engineering, and a custom stage that depends on Requirements."""
    STAGES = [
        {"key": "stage-1", "seq": 1, "name": "Requirements", "template": 1, "outputs": ["PRD"], "dependsOn": [], "custom": False},
        {"key": "stage-2", "seq": 2, "name": "Architecture", "template": 2, "outputs": ["HLD"], "dependsOn": ["stage-1"], "custom": False},
        {"key": "stage-4", "seq": 3, "name": "Test engineering", "template": 4, "outputs": ["TEST_STRATEGY"], "dependsOn": ["stage-2"], "custom": False},
        {"key": "audit", "seq": 4, "name": "Compliance audit", "template": 7, "outputs": ["REPORT"], "dependsOn": ["stage-1"], "custom": True, "agentsOnly": True},
    ]

    async def view(self, project_id):
        return {"stages": [dict(s) for s in self.STAGES]}


def make(llm: FakeLlm | None = None, *, workflow=None):
    llm = llm or FakeLlm()
    repo, audit = MemRepo(), FakeAudit()
    runtime = AgentRuntime(llm, audit, AgentUsage(repo))
    svc = AgentDefService(Db(), repo, Authz(), audit, AgentAuditor(llm, runtime), runtime, canon=Canon(), usage=AgentUsage(repo), llm=llm, workflow=workflow,
                          skill_packs=lambda: [{"id": "draft_adr", "name": "Draft ADR", "description": "d", "version": 2, "body": "Draft an ADR", "tier": "frontier", "roles": ["SA"], "phase": 2}])
    return svc, repo, llm, audit


GOOD = {"description": "Scores a refund for fraud.", "prompt": "You review refund requests.\nAnalyse {refund} and return a risk score between 0 and 1 and list the three strongest signals, one line each.",
        "role": "reason", "inputs": [{"name": "refund", "type": "object", "source": "brief"}], "outputs": [{"name": "score", "type": "number", "artefact_type": "RISK_ASSESSMENT", "format": "JSON"}]}
FRAUD_PROMPT = ("You review refund requests for signs of fraud.\nAnalyse {refund_request} against {booking_history} and give a risk score between 0 and 1.\n"
                "Be brief, but list every signal you used in detail.\nMark the case as high risk when the evidence is strong.\nIf the case is urgent, skip the project rules check.")
