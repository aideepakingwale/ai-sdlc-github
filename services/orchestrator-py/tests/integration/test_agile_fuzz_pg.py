"""Randomised state-machine test: long random sequences of ceremonies against a real Postgres, with the structural
invariants of the engine checked after EVERY step. Failures are reproducible from the seed."""

import random

import pytest

from app.domain.errors import SdlcError

from .helpers import build_env

pytestmark = pytest.mark.asyncio


def project_cfg(e, wf):
    return wf["config"]["stages"]


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
    open_by_rel: dict[str, int] = {}
    for i in its:
        if i["status"] in ("planned", "active"):
            open_by_rel[i["release_id"]] = open_by_rel.get(i["release_id"], 0) + 1
    assert all(n == 1 for n in open_by_rel.values()), f"a release has two open sprints: {open_by_rel}"
    rels = await e.pg.list_releases(e.pid)
    live = [r for r in rels if r["status"] in ("open", "hardening")]
    assert live, f"a project must always have a live release, got {[r['status'] for r in rels]}"
    for r in rels:
        if r["status"] == "hardening":                          # no sprint may be open while THAT release hardens
            assert r["id"] not in open_by_rel
    inst = await e.pg.list_stage_instances(e.pid)
    assert len({i["seq"] for i in inst}) == len(inst)
    # a sprint materialises exactly its release's iteration stage set, once each
    for it in its:
        rel = next(r for r in rels if r["id"] == it["release_id"])
        want = sorted(x["key"] for x in (rel["workflow"]["stages"] if rel["workflow"] else project_cfg(e, wf)) if x["scope"] == "iteration")
        got = sorted(i["base_key"] for i in inst if i["iteration_id"] == it["id"])
        assert got == want, (it["label"], rel["code"], got, want)
    return wf


async def next_stage_of(e, wf, it):
    states = {s["SK"]: s for s in await e.dynamo.list_phase_states(e.pid)}
    mine = sorted((s for s in wf["stages"] if s.get("iterationId") == it["id"]), key=lambda s: s["seq"])
    for s in mine:
        if (states.get(f"PHASE#{s['seq']}") or {}).get("status") != "APPROVED":
            return s
    return None


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6, 7, 8])
async def test_random_ceremony_sequences_keep_every_invariant(pg, seed):
    e = await build_env(pg)
    rnd = random.Random(seed)
    await e.agile.enable(e.pid, e.pm, methodology=rnd.choice(["scrum", "kanban"]))
    for key in ("vision", "runway"):
        wf = await e.wf.view(e.pid)
        st = next(s for s in wf["stages"] if s["key"] == key)
        await e.dynamo.put_phase_state(project_id=e.pid, phase=st["seq"], status="PENDING_REVIEW", reviewer_role=st["reviewerRole"])
        await e.gates.review(project_id=e.pid, phase=st["seq"], decision="APPROVE", comments=None, user=e.admin)
    done = {"sprints": 0, "cancelled": 0, "releases": 0, "rejected": 0, "created": 0, "parallel": 0}
    for step in range(160):
        wf = await invariants(e)
        rels = await e.pg.list_releases(e.pid)
        op = rnd.choice(["start", "start", "advance", "advance", "advance", "advance", "advance", "cancel",
                         "harden", "approve_release", "create_release", "create_release"])
        user = rnd.choice([e.po, e.pm, e.admin])
        try:
            if op == "create_release":
                kind = rnd.choice(["inherit", "lean", "hotfix", "build-only"])
                src = rnd.choice(rels) if rnd.random() < 0.5 else None
                await e.agile.create_release(e.pid, user, name=f"r{step}", stage_preset=kind,
                                             forked_from=src["id"] if src else None,
                                             intake_rule=rnd.choice(["pool", "epic"]))
                done["created"] += 1
            elif op == "start":
                open_rels = [r for r in rels if r["status"] == "open"]
                pick = rnd.choice(open_rels) if open_rels else None
                await e.agile.start_sprint(e.pid, user, goal=f"g{step}", capacity=rnd.choice([0, 5, 20]),
                                           release_id=pick["id"] if pick and rnd.random() < 0.8 else None)
                done["sprints"] += 1
                if len(await e.pg.list_open_iterations(e.pid)) > 1:
                    done["parallel"] += 1
            elif op == "advance":
                opens = await e.pg.list_open_iterations(e.pid)
                if not opens:
                    continue
                st = await next_stage_of(e, wf, rnd.choice(opens))
                if st is None:
                    continue
                await e.dynamo.put_phase_state(project_id=e.pid, phase=st["seq"], status="PENDING_REVIEW", reviewer_role=st["reviewerRole"])
                by = e.admin if st["gateMode"] == "full" or rnd.random() < 0.3 else e.po
                await e.gates.review(project_id=e.pid, phase=st["seq"], decision="APPROVE", comments=None, user=by)
            elif op == "cancel":
                opens = await e.pg.list_open_iterations(e.pid)
                if opens:
                    await e.agile.cancel_sprint(e.pid, user, rnd.choice(opens)["id"])
                    done["cancelled"] += 1
            elif op == "harden":
                pick = rnd.choice([r for r in rels if r["status"] == "open"] or [None])
                await e.agile.start_release_hardening(e.pid, user, pick["id"] if pick else None)
                done["releases"] += 1
            elif op == "approve_release":
                states = {s["SK"]: s for s in await e.dynamo.list_phase_states(e.pid)}
                cand = [s for s in wf["stages"] if s.get("scope") == "release"
                        and (states.get(f"PHASE#{s['seq']}") or {}).get("status") != "APPROVED"]
                if cand:
                    rel = rnd.choice(cand)
                    await e.dynamo.put_phase_state(project_id=e.pid, phase=rel["seq"], status="PENDING_REVIEW", reviewer_role="DEVOPS")
                    await e.gates.review(project_id=e.pid, phase=rel["seq"], decision="APPROVE", comments=None, user=e.admin)
        except SdlcError as err:
            assert err.code in ("GATE_CONFLICT", "VALIDATION_FAILED", "FORBIDDEN", "NOT_FOUND"), (op, err.code, err.message)
            done["rejected"] += 1
    await invariants(e)
    # the run actually exercised the machine (not just rejected everything)
    print("FUZZ", seed, done)
    assert done["sprints"] >= 3 and done["rejected"] >= 1 and done["created"] >= 1, done
    its = await e.pg.list_iterations(e.pid)
    assert [i["number"] for i in its] == list(range(1, len(its) + 1)), "sprint numbers must be gap-free"
