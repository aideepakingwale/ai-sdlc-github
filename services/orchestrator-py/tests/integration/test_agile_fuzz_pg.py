"""Randomised state-machine test: long random sequences of ceremonies against a real Postgres, with the structural
invariants of the engine checked after EVERY step. Failures are reproducible from the seed."""

import random

import pytest

from app.domain.errors import SdlcError

from .helpers import build_env

pytestmark = pytest.mark.asyncio


async def invariants(e):
    wf = await e.wf.view(e.pid)
    stages = wf["stages"]
    seqs = [s["seq"] for s in stages]
    keys = [s["key"] for s in stages]
    assert len(set(seqs)) == len(seqs), "duplicate stage slots"
    assert len(set(keys)) == len(keys), "duplicate stage keys"
    by_key = set(keys)
    for s in stages:
        for d in s["dependsOn"]:
            assert d in by_key, f"{s['key']} depends on missing {d}"
    flat = [q for lv in wf["levels"] for q in lv]
    assert sorted(flat) == sorted(seqs), "levels do not cover exactly the stages"
    lvl = {s["key"]: s["level"] for s in stages}
    for s in stages:
        for d in s["dependsOn"]:
            assert lvl[d] < lvl[s["key"]], f"{s['key']} is not after its dependency {d}"
    its = await e.pg.list_iterations(e.pid)
    assert sum(1 for i in its if i["status"] in ("planned", "active")) <= 1, "two open sprints"
    rels = await e.pg.list_releases(e.pid)
    live = [r for r in rels if r["status"] in ("open", "hardening")]
    assert len(live) == 1, f"exactly one live (open or hardening) release, got {[r['status'] for r in rels]}"
    if live[0]["status"] == "hardening":                       # no sprint may be open while a release hardens
        assert not any(i["status"] in ("planned", "active") for i in its)
    inst = await e.pg.list_stage_instances(e.pid)
    assert len({i["seq"] for i in inst}) == len(inst)
    base_top = max(s["seq"] for s in stages if s.get("scope", "project") == "project") if stages else 0
    assert all(i["seq"] >= 3 for i in inst) and base_top <= 2
    # a sprint's stages exist exactly once per base stage
    for it in its:
        mine = [i for i in inst if i["iteration_id"] == it["id"]]
        got = sorted(i["base_key"] for i in mine)
        full = sorted({i["base_key"] for i in inst if i["scope"] == "iteration"})
        if it["number"] == 1:        # sprint 1 reuses the base template's slots for some of its stages
            assert len(set(got)) == len(got) and set(got) <= set(full), (it["label"], got)
        else:
            assert got == full, (it["label"], got)
    return wf


async def next_stage_of_open_sprint(e, wf):
    it = await e.pg.get_open_iteration(e.pid)
    if not it:
        return None
    states = {s["SK"]: s for s in await e.dynamo.list_phase_states(e.pid)}
    for s in wf["stages"]:
        if s.get("iterationLabel") == it["label"] and (states.get(f"PHASE#{s['seq']}") or {}).get("status") not in ("APPROVED",):
            return s
    return None


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6])
async def test_random_ceremony_sequences_keep_every_invariant(pg, seed):
    e = await build_env(pg)
    rnd = random.Random(seed)
    await e.agile.enable(e.pid, e.pm, methodology=rnd.choice(["scrum", "kanban"]))
    for key in ("vision", "runway"):
        wf = await e.wf.view(e.pid)
        st = next(s for s in wf["stages"] if s["key"] == key)
        await e.dynamo.put_phase_state(project_id=e.pid, phase=st["seq"], status="PENDING_REVIEW", reviewer_role=st["reviewerRole"])
        await e.gates.review(project_id=e.pid, phase=st["seq"], decision="APPROVE", comments=None, user=e.admin)
    done = {"sprints": 0, "cancelled": 0, "releases": 0, "rejected": 0}
    for step in range(120):
        wf = await invariants(e)
        op = rnd.choice(["start", "start", "advance", "advance", "advance", "advance", "cancel", "harden", "approve_release"])
        user = rnd.choice([e.po, e.pm, e.admin])
        try:
            if op == "start":
                await e.agile.start_sprint(e.pid, user, goal=f"g{step}", capacity=rnd.choice([0, 5, 20]))
                done["sprints"] += 1
            elif op == "advance":
                st = await next_stage_of_open_sprint(e, wf)
                if st is None:
                    continue
                await e.dynamo.put_phase_state(project_id=e.pid, phase=st["seq"], status="PENDING_REVIEW", reviewer_role=st["reviewerRole"])
                by = e.admin if st["gateMode"] == "full" or rnd.random() < 0.3 else e.po
                await e.gates.review(project_id=e.pid, phase=st["seq"], decision="APPROVE", comments=None, user=by)
            elif op == "cancel":
                it = await e.pg.get_open_iteration(e.pid)
                if it:
                    await e.agile.cancel_sprint(e.pid, user, it["id"])
                    done["cancelled"] += 1
            elif op == "harden":
                await e.agile.start_release_hardening(e.pid, user)
                done["releases"] += 1
            elif op == "approve_release":
                states = {s["SK"]: s for s in await e.dynamo.list_phase_states(e.pid)}
                rel = next((s for s in wf["stages"] if s.get("scope") == "release"
                            and (states.get(f"PHASE#{s['seq']}") or {}).get("status") != "APPROVED"), None)
                if rel:
                    await e.dynamo.put_phase_state(project_id=e.pid, phase=rel["seq"], status="PENDING_REVIEW", reviewer_role="DEVOPS")
                    await e.gates.review(project_id=e.pid, phase=rel["seq"], decision="APPROVE", comments=None, user=e.admin)
        except SdlcError as err:
            assert err.code in ("GATE_CONFLICT", "VALIDATION_FAILED", "FORBIDDEN"), (op, err.code, err.message)
            done["rejected"] += 1
    await invariants(e)
    # the run actually exercised the machine (not just rejected everything)
    assert done["sprints"] >= 3 and done["rejected"] >= 1, done
    its = await e.pg.list_iterations(e.pid)
    assert [i["number"] for i in its] == list(range(1, len(its) + 1)), "sprint numbers must be gap-free"
