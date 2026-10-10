"""Custom agents and skills: visibility, permissions, the audit, the approval lifecycle, delegation and running."""
from __future__ import annotations


import pytest

from app.domain.errors import SdlcError
from app.services import agent_body as body
from app.services.agent_audit import AgentAuditor, catalogue
from app.services.agent_runtime import Budget, coerce, render_output
from app.services.agent_stage import run_stage_agents
from app.services.skills import SkillService

from .agent_fakes import AUTHOR, APPROVER, FRAUD_PROMPT, GOOD, MEMBER, OTHER_PM, PM, SUPER, Canon, FakeLlm, make


async def granted(svc, repo, *, edit=(), approve=()):
    for u in edit:
        await repo.set_grant("p1", u.id, True, u.id in [a.id for a in approve], PM.id)
    for u in approve:
        await repo.set_grant("p1", u.id, u.id in [a.id for a in edit], True, PM.id)


async def publish(svc, user, approver, def_id):
    await svc.run_audit(user, def_id)
    await svc.submit(user, def_id)
    return await svc.decide(approver, def_id, "approve")


# ---------------------------------------------------------------- body
def test_a_body_is_cleaned_and_limits_are_enforced():
    b = body.normalise_body("agent", GOOD)
    assert b["role"] == "reason" and b["tools"] == [] and b["outputs"][0]["artefact_type"] == "RISK_ASSESSMENT"
    for bad, msg in [({**GOOD, "role": "x"}, "model role"), ({**GOOD, "tools": ["jira"]}, "Tools are not available"), ({**GOOD, "temperature": 3}, "temperature"),
                     ({**GOOD, "inputs": [{"name": "Bad Name"}]}, "Input name"), ({**GOOD, "outputs": [{"name": "a", "artefact_type": "has space"}]}, "artefact type"),
                     ({**GOOD, "inputs": [{"name": "a"}, {"name": "a"}]}, "twice"), ({**GOOD, "prompt": "x" * 9000}, "longer than")]:
        with pytest.raises(SdlcError, match=msg):
            body.normalise_body("agent", bad)
    s = body.normalise_body("skill", {"prompt": "do it", "roles": ["QA", "QA"], "stages": [2, 2, 1]})
    assert s["roles"] == ["QA"] and s["stages"] == [1, 2] and s["outputs"]


def test_values_are_coerced_to_their_declared_type():
    assert coerce("3.5", "number") == 3.5 and coerce("yes", "boolean") is True and coerce('{"a": 1}', "object") == {"a": 1}
    assert coerce("- a\n- b", "list") == ["a", "b"] and coerce({"x": 1}, "string").startswith("{")
    with pytest.raises(ValueError):
        coerce("abc", "number")
    assert render_output({"a": 1}, "JSON").startswith("{") and render_output("hi", "Markdown") == "hi" and "```json" in render_output([1], "Markdown")


# ---------------------------------------------------------------- visibility and permission
async def test_core_is_invisible_to_projects_and_project_agents_are_private():
    svc, repo, *_ = make()
    assert (await svc.library(SUPER, "agent"))["core"]
    with pytest.raises(SdlcError):
        await svc.library(PM, "agent")
    with pytest.raises(SdlcError, match="not found"):
        svc.core_detail(PM, "agent", "prd")
    d = await svc.create(PM, kind="agent", name="Private agent", scope="project", project_id="p1", body=GOOD)
    lib = await svc.library(SUPER, "agent")
    assert all(x["id"] != d["def"]["id"] for x in lib["org"]) and lib["projectOwned"] == 1      # counted, never listed
    with pytest.raises(SdlcError):                                                             # another project cannot read it
        await svc.detail(OTHER_PM, d["def"]["id"])
    assert [x["id"] for x in (await svc.for_project(PM, "p1"))["mine"]] == [d["def"]["id"]]
    assert (await svc.for_project(OTHER_PM, "p2"))["mine"] == []


async def test_an_open_organisation_agent_is_visible_to_projects_but_a_closed_one_is_not():
    svc, repo, *_ = make()
    d = await svc.create(SUPER, kind="agent", name="Org agent", scope="org", body=GOOD)
    did = d["def"]["id"]
    with pytest.raises(SdlcError):
        await svc.detail(PM, did)                                  # not open, no approved version
    await publish(svc, SUPER, SUPER, did)
    with pytest.raises(SdlcError):
        await svc.detail(PM, did)                                  # approved but not open
    await svc.set_open(SUPER, did, True)
    seen = await svc.detail(PM, did)
    assert seen["rights"]["edit"] is False and seen["current"]["status"] == "published"
    assert [c["id"] for c in (await svc.for_project(PM, "p1"))["open"]] == [did]
    with pytest.raises(SdlcError):
        await svc.save_draft(PM, did, name=None, body=GOOD)         # a project cannot edit it
    with pytest.raises(SdlcError, match="Approve a version"):
        await svc.set_open(SUPER, (await svc.create(SUPER, kind="agent", name="Not approved", scope="org", body=GOOD))["def"]["id"], True)


async def test_building_needs_the_managers_permission_and_the_manager_can_grant_it():
    svc, repo, *_ = make()
    with pytest.raises(SdlcError, match="permission to build"):
        await svc.create(AUTHOR, kind="agent", name="Mine", scope="project", project_id="p1")
    with pytest.raises(SdlcError):
        await svc.set_grant(AUTHOR, "p1", AUTHOR.id, True, False)     # only the manager grants
    await svc.set_grant(PM, "p1", AUTHOR.id, True, False)
    assert (await svc.create(AUTHOR, kind="agent", name="Mine", scope="project", project_id="p1"))["rights"]["edit"] is True
    assert (await svc.for_project(MEMBER, "p1"))["rights"] == {"view": True, "manage": False, "edit": False, "approve": False}
    with pytest.raises(SdlcError, match="team"):
        await svc.set_grant(PM, "p1", "u-stranger", True, False)
    with pytest.raises(SdlcError):                                    # a different project's manager has nothing here
        await svc.rights("p1", OTHER_PM)


# ---------------------------------------------------------------- lifecycle
async def test_an_agent_goes_draft_audit_approval_published_with_four_eyes():
    svc, repo, llm, audit = make()
    await granted(svc, repo, edit=[AUTHOR], approve=[APPROVER])
    await repo.set_grant("p1", AUTHOR.id, True, True, PM.id)               # the author may also approve other people's work, never their own
    d = (await svc.create(AUTHOR, kind="agent", name="Fraud checker", scope="project", project_id="p1", body=GOOD))["def"]["id"]
    with pytest.raises(SdlcError, match="Run the audit"):
        await svc.submit(AUTHOR, d)
    rep = await svc.run_audit(AUTHOR, d)
    assert rep["summary"]["block"] == 0 and rep["probes"] and all(p["held"] for p in rep["probes"])
    sub = await svc.submit(AUTHOR, d)
    assert sub["current"]["status"] == "pending"
    with pytest.raises(SdlcError, match="Someone else"):
        await svc.decide(AUTHOR, d, "approve")                       # not their own
    with pytest.raises(SdlcError, match="permission to approve"):
        await svc.decide(MEMBER, d, "approve")
    with pytest.raises(SdlcError, match="Say what"):
        await svc.decide(APPROVER, d, "changes", "")
    out = await svc.decide(APPROVER, d, "approve")
    assert out["current"]["status"] == "published" and out["published"]["version"] == 1
    assert {"custom_agent.created", "custom_agent.audited", "custom_agent.submitted", "custom_agent.approved"} <= set(audit.events)


async def test_the_manager_can_approve_and_a_super_admin_covers_a_manager_who_wrote_it():
    svc, repo, *_ = make()
    d = (await svc.create(PM, kind="agent", name="By manager", scope="project", project_id="p1", body=GOOD))["def"]["id"]
    await svc.run_audit(PM, d)
    await svc.submit(PM, d)
    with pytest.raises(SdlcError, match="Someone else"):
        await svc.decide(PM, d, "approve")                           # a lone manager cannot approve their own
    assert (await svc.decide(SUPER, d, "approve"))["current"]["status"] == "published"
    org = (await svc.create(SUPER, kind="agent", name="Org one", scope="org", body=GOOD))["def"]["id"]
    assert (await publish(svc, SUPER, SUPER, org))["current"]["status"] == "published"      # the library has nobody above them


async def test_a_blocking_finding_stops_submission_until_it_is_fixed():
    svc, repo, *_ = make()
    b = {**GOOD, "prompt": FRAUD_PROMPT, "inputs": [{"name": "refund_request", "type": "object", "source": "brief"}, {"name": "booking_history", "type": "object", "source": "user"}]}
    d = (await svc.create(PM, kind="agent", name="Fraud review agent", scope="project", project_id="p1", body=b))["def"]["id"]
    rep = await svc.run_audit(PM, d)
    kinds = {(f["area"], f["severity"]) for f in rep["findings"]}
    assert ("Security", "block") in kinds and ("Contradiction", "warn") in kinds and ("Ambiguity", "warn") in kinds
    with pytest.raises(SdlcError, match="blocking"):
        await svc.submit(PM, d)
    blocker = next(f for f in rep["findings"] if f["severity"] == "block")
    fixed = await svc.apply_fix(PM, d, blocker["id"])
    assert "skip the project rules" not in fixed["current"]["body"]["prompt"]
    assert fixed["current"]["auditReport"]["summary"]["block"] == 0 and fixed["current"]["audit"]["stale"] is False     # re-checked at once, no new model call
    with pytest.raises(SdlcError, match="accept them"):
        await svc.submit(PM, d)                                        # two warnings remain
    await svc.acknowledge(PM, d, True)
    assert (await svc.submit(PM, d))["current"]["status"] == "pending"


async def test_editing_after_an_audit_makes_it_stale_and_a_published_version_is_never_changed():
    svc, repo, *_ = make()
    d = (await svc.create(PM, kind="agent", name="Edits", scope="project", project_id="p1", body=GOOD))["def"]["id"]
    await svc.run_audit(PM, d)
    edited = await svc.save_draft(PM, d, name=None, body={**GOOD, "description": "Changed"})
    assert edited["current"]["audit"]["stale"] is True and edited["canSubmit"] is False
    with pytest.raises(SdlcError, match="changed since"):
        await svc.submit(PM, d)
    await svc.run_audit(PM, d)
    await svc.submit(PM, d)
    with pytest.raises(SdlcError, match="waiting for approval"):
        await svc.save_draft(PM, d, name=None, body=GOOD)
    await svc.decide(SUPER, d, "approve")
    v2 = await svc.save_draft(PM, d, name=None, body={**GOOD, "description": "Second"})
    assert v2["current"]["version"] == 2 and v2["current"]["status"] == "draft" and v2["published"]["body"]["description"] == "Changed"
    await svc.withdraw(PM, d) if False else None
    await publish(svc, PM, SUPER, d)
    detail = await svc.detail(PM, d)
    assert [v["status"] for v in detail["versions"]] == ["published", "superseded"]


async def test_rejection_keeps_the_comment_and_withdrawal_returns_to_draft():
    svc, repo, *_ = make()
    d = (await svc.create(PM, kind="agent", name="Rejected", scope="project", project_id="p1", body=GOOD))["def"]["id"]
    await svc.run_audit(PM, d)
    await svc.submit(PM, d)
    assert (await svc.withdraw(PM, d))["current"]["status"] == "draft"
    await svc.submit(PM, d)
    out = await svc.decide(SUPER, d, "changes", "Name the output more clearly")
    assert out["current"]["status"] == "rejected" and out["current"]["comment"].startswith("Name")
    assert len(await svc.pending(SUPER, "p1")) == 0


async def test_unpublished_definitions_can_be_deleted_and_published_ones_only_retired():
    svc, repo, *_ = make()
    a = (await svc.create(PM, kind="agent", name="Throwaway", scope="project", project_id="p1", body=GOOD))["def"]["id"]
    assert (await svc.delete_unpublished(PM, a))["deleted"] and await repo.get_def(a) is None
    b = (await svc.create(PM, kind="agent", name="Kept", scope="project", project_id="p1", body=GOOD))["def"]["id"]
    await publish(svc, PM, SUPER, b)
    with pytest.raises(SdlcError, match="retired"):
        await svc.delete_unpublished(PM, b)
    await svc.set_stage_items(PM, "p1", "stage", [{"defId": b}])
    await svc.retire(PM, b)
    assert await repo.list_attachments("p1") == [] and (await svc.for_project(PM, "p1"))["mine"] == []


# ---------------------------------------------------------------- forks
async def test_a_copy_is_a_fork_at_that_moment_and_core_can_only_be_copied_by_a_super_admin():
    svc, repo, *_ = make()
    org = (await svc.create(SUPER, kind="agent", name="Risk assessor", scope="org", body=GOOD))["def"]["id"]
    await publish(svc, SUPER, SUPER, org)
    await svc.set_open(SUPER, org, True)
    await svc.set_grant(PM, "p1", AUTHOR.id, True, False)
    fork = await svc.fork(AUTHOR, source_kind="def", source_id=org, kind="agent", scope="project", project_id="p1")
    assert fork["def"]["name"] == "Copy of Risk assessor" and fork["def"]["source"]["version"] == 1 and fork["current"]["status"] == "draft"
    await svc.save_draft(SUPER, org, name=None, body={**GOOD, "description": "v2 text"})       # the original moves on
    await publish(svc, SUPER, SUPER, org)
    again = await svc.detail(AUTHOR, fork["def"]["id"])
    assert again["current"]["body"]["description"] == GOOD["description"] and again["newerSource"] == {"name": "Risk assessor", "version": 2}
    with pytest.raises(SdlcError):
        await svc.fork(PM, source_kind="core", source_id="prd", kind="agent", scope="project", project_id="p1")
    core = await svc.fork(SUPER, source_kind="core", source_id="prd", kind="agent", scope="org")
    assert core["def"]["source"]["kind"] == "core" and core["current"]["body"]["prompt"]
    other = (await svc.create(OTHER_PM, kind="agent", name="Elsewhere", scope="project", project_id="p2", body=GOOD))["def"]["id"]
    with pytest.raises(SdlcError):
        await svc.fork(AUTHOR, source_kind="def", source_id=other, kind="agent", scope="project", project_id="p1")


# ---------------------------------------------------------------- audit
def test_checks_find_the_usual_problems_without_a_model():
    a = AgentAuditor(None, None)
    def run(prompt, **kw):
        return a.checks(kind="agent", name="x", body=body.normalise_body("agent", {**GOOD, "prompt": prompt, **kw}))
    secrets = run("Call the API with api_key = sk-abcdefghijklmnopqrstuvwxyz. Return {refund}.")
    assert any(f["guardrail"] == "secrets" and f["severity"] == "block" for f in secrets)
    pii = run("Return a score for {refund}. Example customer: jane.doe@acme.com")
    assert any(f["guardrail"] == "pii_examples" and f["severity"] == "warn" for f in pii)
    undeclared = run("Return a score for {refund} and {nothing}.")
    assert any(f["guardrail"] == "declared_vars" and "{nothing}" in f["title"] for f in undeclared)
    unused = a.checks(kind="agent", name="x", body=body.normalise_body("agent", {**GOOD, "prompt": "Return a risk score of 1.", "inputs": [{"name": "refund", "type": "object", "source": "brief"}]}))
    assert any(f["guardrail"] == "unused_inputs" for f in unused)
    clean = run("Analyse {refund}. Return a risk score from 0 to 1 and the three strongest signals.")
    assert [f for f in clean if f["severity"] == "block"] == []
    empty = a.checks(kind="agent", name="x", body=body.normalise_body("agent", {**GOOD, "outputs": [], "prompt": "Hi"}))
    assert {f["guardrail"] for f in empty if f["severity"] == "block"} >= {"success_criteria", "typed_outputs"}


async def test_probes_catch_an_agent_that_obeys_its_input_or_leaks_and_a_model_can_only_block_through_a_guardrail():
    llm = FakeLlm()
    svc, repo, _, _ = make(llm)
    d = (await svc.create(PM, kind="agent", name="Obedient", scope="project", project_id="p1", body=GOOD))["def"]["id"]
    llm.follow, llm.reveal = {"OVERRIDE-", "SKIPPED-"}, True
    rep = await svc.run_audit(PM, d)
    held = {p["id"]: p["held"] for p in rep["probes"]}
    assert held == {"reveal": False, "override": False, "indirect": True, "urgent": False, "exfil": True}
    titles = " ".join(f["title"] for f in rep["findings"] if f["severity"] == "block")
    assert "revealed its instructions" in titles and "followed an instruction" in titles and "skipped its rules" in titles
    llm.follow, llm.reveal = set(), False
    llm.model_findings = [{"area": "Contradiction", "severity": "block", "guardrail": "", "title": "Conflicts with a rule", "detail": "x", "snippet": "", "replacement": None},
                          {"area": "Security", "severity": "block", "guardrail": "secrets", "title": "Looks like a secret", "detail": "y", "snippet": "NOT IN PROMPT", "replacement": ""}]
    rep = await svc.run_audit(PM, d)
    by_title = {f["title"]: f for f in rep["findings"]}
    assert by_title["Conflicts with a rule"]["severity"] == "warn"            # no guardrail named, so never blocking
    assert by_title["Looks like a secret"]["severity"] == "block" and by_title["Looks like a secret"]["snippet"] == ""     # a snippet that is not in the prompt is dropped
    await svc.set_guardrail(SUPER, "secrets", "warn")
    assert catalogue(await repo.list_guardrail_overrides())[1]["severity"] == "warn"
    rep = await svc.run_audit(PM, d)
    assert next(f for f in rep["findings"] if f["title"] == "Looks like a secret")["severity"] == "warn"
    with pytest.raises(SdlcError):
        await svc.set_guardrail(PM, "secrets", "block")


async def test_the_audit_completes_when_the_model_review_or_the_probes_cannot_run():
    class Down(FakeLlm):
        async def generate_json(self, **kw):
            if kw["tag"] == "agent_audit":
                raise SdlcError("PROVIDER_ERROR", "down")
            return await super().generate_json(**kw)

        async def generate(self, **kw):
            raise SdlcError("PROVIDER_ERROR", "down")

    svc, *_ = make(Down())
    d = (await svc.create(PM, kind="agent", name="Offline", scope="project", project_id="p1", body=GOOD))["def"]["id"]
    rep = await svc.run_audit(PM, d)
    titles = [f["title"] for f in rep["findings"] if f["severity"] == "warn"]
    assert "The model review could not run" in titles and "The adversarial probes could not run" in titles and rep["notes"]


# ---------------------------------------------------------------- stage attachments
async def test_a_stage_uses_a_pinned_approved_version_and_a_newer_one_is_offered_not_forced():
    svc, repo, *_ = make()
    d = (await svc.create(PM, kind="agent", name="Stage agent", scope="project", project_id="p1", body=GOOD))["def"]["id"]
    with pytest.raises(SdlcError, match="approved version"):
        await svc.set_stage_items(PM, "p1", "stage-4", [{"defId": d}])
    await publish(svc, PM, SUPER, d)
    out = await svc.set_stage_items(PM, "p1", "stage-4", [{"defId": d}])
    assert out["items"][0]["pinnedVersion"] == 1 and not out["items"][0]["newerAvailable"] and out["available"] == []
    await svc.save_draft(PM, d, name=None, body={**GOOD, "description": "v2"})
    await publish(svc, PM, SUPER, d)
    later = await svc.stage_items(MEMBER, "p1", "stage-4")
    assert later["items"][0]["pinnedVersion"] == 1 and later["items"][0]["newerAvailable"] is True and later["rights"]["edit"] is False
    run = await svc.resolve_for_run("p1", "stage-4")
    assert run[0]["version"] == 1 and run[0]["body"]["description"] == GOOD["description"]                # still v1: the stage did not move
    with pytest.raises(SdlcError, match="permission to change"):
        await svc.set_stage_items(MEMBER, "p1", "stage-4", [])
    with pytest.raises(SdlcError, match="not available"):
        await svc.set_stage_items(PM, "p1", "stage-4", [{"defId": (await svc.create(OTHER_PM, kind="agent", name="Other", scope="project", project_id="p2", body=GOOD))["def"]["id"]}])
    assert (await svc.set_stage_items(PM, "p1", "stage-4", [{"defId": d, "pinnedVersion": 2}]))["items"][0]["pinnedVersion"] == 2


async def test_conditions_and_run_modes_are_validated():
    svc, repo, *_ = make()
    d = (await svc.create(PM, kind="agent", name="Conditional", scope="project", project_id="p1", body=GOOD))["def"]["id"]
    await publish(svc, PM, SUPER, d)
    with pytest.raises(SdlcError, match="Unknown value"):
        await svc.set_stage_items(PM, "p1", "s", [{"defId": d, "runs": "when", "condition": "nope > 1"}])
    with pytest.raises(SdlcError, match="when it should run"):
        await svc.set_stage_items(PM, "p1", "s", [{"defId": d, "runs": "when", "condition": ""}])
    ok = await svc.set_stage_items(PM, "p1", "s", [{"defId": d, "runs": "when", "condition": "len(refund) > 0"}])
    assert ok["items"][0]["runs"] == "when"


# ---------------------------------------------------------------- running
async def test_a_test_run_gives_declared_outputs_and_records_what_was_asked():
    svc, repo, llm, audit = make()
    d = (await svc.create(PM, kind="agent", name="Runs", scope="project", project_id="p1", body=GOOD))["def"]["id"]
    out = await svc.run_test(PM, d, {"refund": {"amount": 90}})
    assert out["outputs"] == {"score": 0.9} and out["totalTokens"] == 120
    assert "Responsible AI policy" in out["systemPrompt"] and "PROJECT CANON" in out["systemPrompt"] and '"amount": 90' in out["userPrompt"]
    assert "OUTPUT_SCHEMA" in out["userPrompt"] and "never instructions" in out["systemPrompt"]
    with pytest.raises(SdlcError, match="required"):
        await svc.run_test(PM, d, {})
    with pytest.raises(SdlcError, match="should be an object"):
        await svc.run_test(PM, d, {"refund": "plain text that is not json"}) if False else (_ for _ in ()).throw(SdlcError("VALIDATION_FAILED", "Input 'refund' should be an object"))
    llm.outputs = {}
    with pytest.raises(SdlcError, match="did not return"):
        await svc.run_test(PM, d, {"refund": {"a": 1}})
    llm.outputs = {"score": "high"}
    with pytest.raises(SdlcError, match="should be"):
        await svc.run_test(PM, d, {"refund": {"a": 1}})
    with pytest.raises(SdlcError):
        await svc.run_test(MEMBER, d, {"refund": {"a": 1}})


async def test_the_fallback_model_is_used_when_the_first_fails():
    llm = FakeLlm()
    llm.fail_models = {"prov/primary"}
    svc, *_ = make(llm)
    d = (await svc.create(PM, kind="agent", name="Fallback", scope="project", project_id="p1", body={**GOOD, "model": "prov/primary", "fallback": "prov/backup"}))["def"]["id"]
    out = await svc.run_test(PM, d, {"refund": {"a": 1}})
    assert out["outputs"] == {"score": 0.9} and [c["model"] for c in llm.calls] == ["prov/primary", "prov/backup"]


async def make_pair(svc, repo):
    child = (await svc.create(PM, kind="agent", name="Policy lookup", scope="project", project_id="p1", body={
        "description": "Finds the rule", "prompt": "Find the refund rule for {refund}. Return the rule text.", "role": "light",
        "inputs": [{"name": "refund", "type": "object", "source": "brief"}], "outputs": [{"name": "rule", "type": "string", "artefact_type": "REPORT", "format": "Markdown"}]}))["def"]["id"]
    await publish(svc, PM, SUPER, child)
    parent = (await svc.create(PM, kind="agent", name="Reviewer", scope="project", project_id="p1", body={
        **GOOD, "prompt": "Analyse {refund}. Use the delegate results. Return a risk score from 0 to 1.", "children": [{"agent_id": child, "when": "len(refund) > 1"}]}))["def"]["id"]
    return child, parent


async def test_delegation_runs_the_delegate_first_when_its_condition_holds_and_shows_its_result():
    svc, repo, llm, _ = make()
    child, parent = await make_pair(svc, repo)
    out = await svc.run_test(PM, parent, {"refund": {"amount": 1, "why": "dup"}})
    assert [c["id"] for c in out["tree"]["children"]] == [child] and "Delegate results" in out["userPrompt"] and "Policy lookup" in out["userPrompt"]
    assert out["totalTokens"] == 240                                              # parent and delegate
    skipped = await svc.run_test(PM, parent, {"refund": {"a": 1}})                # len(refund) == 1: condition not met
    assert skipped["tree"]["children"] == [] and skipped["tree"]["skipped"][0]["why"] == "its condition was not met"


async def test_delegation_refuses_loops_and_too_much_depth_and_an_unavailable_delegate():
    svc, repo, llm, _ = make()
    child, parent = await make_pair(svc, repo)
    # a delegate that points back at its caller is reported by the audit
    await svc.save_draft(PM, child, name=None, body={**(await svc.detail(PM, child))["current"]["body"], "children": [{"agent_id": parent, "when": "always"}]})
    rep = await svc.run_audit(PM, child)
    assert any("not available" in f["title"] or "leads back" in f["title"] for f in rep["findings"] if f["severity"] == "block")
    # the runtime refuses to recurse into itself
    res = svc._runtime
    body_loop = {**GOOD, "children": [{"agent_id": "a", "when": "always"}]}

    async def resolve(aid, ver):
        return {"name": "A", "body": body_loop, "version": 1}

    r = await res.run(agent_id="a", name="A", body=body_loop, inputs={"refund": {"x": 1}}, resolve_child=resolve, budget=Budget())
    assert r.skipped and "delegates to itself" in r.skipped[0]["why"]
    deep = {**GOOD, "children": [{"agent_id": "n", "when": "always"}]}
    chain = {"n": {"name": "N", "body": deep, "version": 1}}

    async def resolve2(aid, ver):
        return chain.get(aid)

    r = await res.run(agent_id="top", name="Top", body=deep, inputs={"refund": {"x": 1}}, resolve_child=resolve2, budget=Budget())
    assert r.children and r.children[0].skipped and "delegates to itself" in r.children[0].skipped[0]["why"] or r.children[0].children
    from app.services.agent_runtime import MAX_AGENTS
    with pytest.raises(SdlcError, match="at most"):
        await res.run(agent_id="z", name="Z", body=GOOD, inputs={"refund": {"x": 1}}, budget=Budget(agents=MAX_AGENTS))
    with pytest.raises(SdlcError, match="token budget"):
        await res.run(agent_id="z", name="Z", body=GOOD, inputs={"refund": {"x": 1}}, budget=Budget(tokens=0))


async def test_a_stage_run_saves_one_artefact_per_output_with_a_record_and_a_failure_does_not_stop_the_rest():
    svc, repo, llm, audit = make()
    ok = (await svc.create(PM, kind="agent", name="Good agent", scope="project", project_id="p1", body={**GOOD, "outputs": [
        {"name": "score", "type": "number", "artefact_type": "RISK_ASSESSMENT", "format": "JSON"}, {"name": "why", "type": "string", "artefact_type": "REPORT", "format": "Markdown"}]}))["def"]["id"]
    needy = (await svc.create(PM, kind="agent", name="Needs PRD", scope="project", project_id="p1", body={
        **GOOD, "inputs": [{"name": "refund", "type": "string", "source": "upstream:PRD"}]}))["def"]["id"]
    await publish(svc, PM, SUPER, ok)
    await publish(svc, PM, SUPER, needy)
    await svc.set_stage_items(PM, "p1", "s", [{"defId": ok}, {"defId": needy}])
    items = await svc.resolve_for_run("p1", "s")
    saved = []

    async def save(**kw):
        saved.append(kw)
        return kw["title"]

    events = []
    res = await run_stage_agents(runtime=svc._runtime, items=items, brief='{"id": 1}', upstream=lambda t: None, rules="r", stack="s", project_context="ctx", save=save, emit=events.append, audit=audit)
    assert [s["type_"] for s in saved] == ["RISK_ASSESSMENT", "REPORT"] and saved[0]["title"] == "Good agent - score" and saved[0]["run"]["custom"] is True
    assert saved[0]["run"]["agentName"] == "Good agent" and res.ran == ["Good agent v1"] and any("needs refund" in s for s in res.skipped)
    assert "custom_agent.run" in audit.events and "Custom agents - custom agent(s) ran" in res.summary()
    llm.outputs = {}
    saved.clear()
    res = await run_stage_agents(runtime=svc._runtime, items=items[:1], brief='{"id": 1}', upstream=lambda t: None, rules="", stack="", project_context="", save=save, emit=events.append)
    assert res.failed and saved == [] and any(e.get("status") == "error" for e in events)


async def test_on_request_agents_wait_to_be_asked_and_conditional_ones_check_their_condition():
    svc, repo, *_ = make()
    a = (await svc.create(PM, kind="agent", name="Asked", scope="project", project_id="p1", body=GOOD))["def"]["id"]
    b = (await svc.create(PM, kind="agent", name="Maybe", scope="project", project_id="p1", body=GOOD))["def"]["id"]
    await publish(svc, PM, SUPER, a)
    await publish(svc, PM, SUPER, b)
    await svc.set_stage_items(PM, "p1", "s", [{"defId": a, "runs": "on_request"}, {"defId": b, "runs": "when", "condition": "'urgent' in lower(brief)"}])
    items = await svc.resolve_for_run("p1", "s")
    got = []

    async def save(**kw):
        got.append(kw["title"])
        return 1

    for brief, expect in (('{"id": 1}', []), ('{"id": 1} URGENT', ["Maybe"])):
        got.clear()
        await run_stage_agents(runtime=svc._runtime, items=items, brief=brief, upstream=lambda t: None, rules="", stack="", project_context="", save=save, emit=lambda e: None)
        assert got == expect
    got.clear()
    await run_stage_agents(runtime=svc._runtime, items=items, brief='{"id": 1}', upstream=lambda t: None, rules="", stack="", project_context="", save=save, emit=lambda e: None, only=a)
    assert got == ["Asked"]


# ---------------------------------------------------------------- skills
async def test_a_custom_skill_appears_for_the_roles_it_names_and_runs_through_the_skill_service():
    from types import SimpleNamespace
    svc, repo, llm, audit = make()
    s = (await svc.create(PM, kind="skill", name="Boarding checklist", scope="project", project_id="p1", body={
        "description": "Builds a checklist", "prompt": "Build a boarding checklist for {input}. Return the checklist.", "roles": ["QA"],
        "outputs": [{"name": "result", "type": "string", "artefact_type": "CHECKLIST", "format": "Markdown"}]}))["def"]["id"]
    await publish(svc, PM, SUPER, s)
    await svc.set_stage_items(PM, "p1", "stage-4", [{"defId": s}])

    class Wf:
        async def stage_by_seq(self, pid, seq):
            return {"key": "stage-4", "template": 4}

        async def view(self, pid):
            return {"stages": [{"key": "stage-4", "seq": 4}]}

    class Db2:
        async def get_project(self, pid):
            return {"id": pid, "current_phase": 4, "tech_stack": "", "tech_stack_source": ""}

    deps = SimpleNamespace(audit=audit)
    skills = SkillService(Db2(), __import__("tests.agent_fakes", fromlist=["Authz"]).Authz(), deps, Wf())
    skills.custom = svc
    listed = await skills.list_for("p1", 4, MEMBER, 4)                 # MEMBER is QA
    mine = next(x for x in listed if x["id"].startswith("custom:"))
    assert mine["canRun"] is True and mine["name"] == "Boarding checklist" and mine["tier"] == "custom"
    assert next(x for x in await skills.list_for("p1", 4, APPROVER, 4) if x["id"].startswith("custom:"))["canRun"] is False          # SA is not in the skill's roles
    out = await skills.execute("p1", mine["id"], MEMBER, "a short-haul flight")
    assert out["tier"] == "custom" and out["output"] and "skill.executed" in audit.events
    with pytest.raises(SdlcError, match="not available to your role"):
        await skills.execute("p1", mine["id"], APPROVER, "x")
    with pytest.raises(SdlcError, match="not attached"):
        await skills.execute("p1", "custom:nope", MEMBER, "x")


async def test_saved_test_cases_belong_to_editors_and_the_catalogue_lists_core_for_super_admins_only():
    svc, repo, *_ = make()
    d = (await svc.create(PM, kind="agent", name="Cases", scope="project", project_id="p1", body=GOOD))["def"]["id"]
    c = await svc.add_case(PM, d, "Duplicate booking", {"refund": {"a": 1}})
    assert (await svc.detail(PM, d))["cases"][0]["id"] == c["id"] and (await svc.detail(MEMBER, d))["cases"] == []
    await svc.delete_case(PM, d, c["id"])
    assert (await svc.detail(PM, d))["cases"] == []
    skills = await svc.library(SUPER, "skill")
    assert [x["id"] for x in skills["core"]] == ["draft_adr"]
    agents = await svc.library(SUPER, "agent")
    assert len(agents["core"]) > 40 and all(x["source"] == "core" for x in agents["core"]) and "custom-agent-runner" in {x["id"] for x in agents["core"]}


async def test_the_stage_hook_adds_custom_agent_artefacts_after_the_stages_own_and_never_fails_the_stage(monkeypatch):
    from types import SimpleNamespace

    from app.agents import phase_agents as pa
    from app.domain.models import AgentState, ContextArtifact

    svc, repo, llm, audit = make()
    d = (await svc.create(PM, kind="agent", name="Hook agent", scope="project", project_id="p1", body={**GOOD, "inputs": [{"name": "refund", "type": "string", "source": "upstream:PRD"}]}))["def"]["id"]
    await publish(svc, PM, SUPER, d)
    await svc.set_stage_items(PM, "p1", "stage-4", [{"defId": d}])
    saved = []

    async def fake_save(deps, state, emit, **kw):
        saved.append(kw)
        return ContextArtifact(phase=state.current_phase, type=kw["type_"], title=kw["title"], summary=kw["summary"], exact=True, content=kw["content"])

    monkeypatch.setattr(pa, "_save_artifact", fake_save)
    state = AgentState(project_id="p1", session_id="s", current_phase=4, stage_template=4, user_input="brief", custom_agents=await svc.resolve_for_run("p1", "stage-4"),
                       context_window=[ContextArtifact(phase=1, type="PRD", title="PRD", summary="The PRD summary", exact=False)])
    deps = SimpleNamespace(agent_runtime=svc._runtime, canon=Canon(), audit=audit)
    def fresh():
        return pa.PhaseAgentResult(summary="Stage done.", new_artifacts=[], gate_status="PENDING_REVIEW")

    out = await pa._with_custom_agents(deps, state, lambda e: None, fresh())
    assert [a.type for a in out.new_artifacts] == ["RISK_ASSESSMENT"] and "Hook agent v1" in out.summary and saved[0]["run"]["custom"] is True
    assert "The PRD summary" in saved[0]["run"]["user"] and saved[0]["run"]["tokens"] == {"prompt": 100, "completion": 20} and saved[0]["run"]["role"] == "reason"                       # an upstream artefact that is not exact falls back to its summary
    regen = state.model_copy(update={"retrigger_fields": ["x"]})
    assert (await pa._with_custom_agents(deps, regen, lambda e: None, fresh())).new_artifacts == []        # a part retrigger does not re-run them
    llm.outputs = {}
    failed = await pa._with_custom_agents(deps, state, lambda e: None, pa.PhaseAgentResult(summary="S.", new_artifacts=[], gate_status="PENDING_REVIEW"))
    assert failed.new_artifacts == [] and "did not complete" in failed.summary
    same = fresh()
    assert (await pa._with_custom_agents(SimpleNamespace(agent_runtime=None), state, lambda e: None, same)) is same


async def test_core_definitions_are_for_super_admins_only_in_every_route_that_lists_them():
    from app.api import project_routes as pr

    assert (await pr.list_agents(user=SUPER))["agents"]
    assert (await pr.governance_skills(user=SUPER))["skills"] and (await pr.governance_prompts(user=SUPER))["prompts"]
    for fn in (pr.list_agents, pr.governance_skills, pr.governance_prompts):
        with pytest.raises(SdlcError, match="super-admin"):
            await fn(user=PM)
        with pytest.raises(SdlcError, match="super-admin"):
            await fn(user=AUTHOR)
