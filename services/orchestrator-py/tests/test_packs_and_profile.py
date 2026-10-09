"""The pack library, organisation packs, the project profile (organisation default, project override, identified from documents) and the advice
offered when a project reaches solution architecture and technical design."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.domain.errors import SdlcError
from app.services import profile as prof
from app.services import rule_packs as rp
from app.services import stack_advisor as sa
from app.services.canon import CanonService
from app.services.pack_admin import PackAdmin
from app.services.project_config import ProjectConfigService
from app.services.rule_advice import RuleAdvisor

from .conftest import FakeAudit, make_user
from .test_rules_and_templates import Authz, Db as BaseDb

PM, ADMIN, DEV = make_user("PROJECT_MANAGER"), make_user("SUPER_ADMIN"), make_user("DEV")


class Db(BaseDb):
    def __init__(self):
        super().__init__()
        self.packs, self.org_items = {}, []

    async def list_org_packs(self):
        return [dict(p) for p in self.packs.values()]

    async def upsert_org_pack(self, pack, uid):
        self.packs[pack["id"]] = {**pack, "active": pack.get("active", True)}
        return self.packs[pack["id"]]

    async def delete_org_pack(self, pid):
        return self.packs.pop(pid, None) is not None

    async def get_org_profile(self):
        return list(self.org_items)

    async def set_org_profile(self, items, uid):
        self.org_items = list(items)


class Content:
    async def put(self, key, body):
        return None


def setup():
    db = Db()
    canon = CanonService(db, Authz(), FakeAudit())
    cfg = ProjectConfigService(db, Content(), Authz(), FakeAudit(), canon)
    return db, canon, cfg


# ---------------------------------------------------------------- the library
def test_the_library_is_broad_and_internally_consistent():
    packs = rp.builtin_packs()
    by_kind = {k: [p for p in packs if p["kind"] == k] for k in rp.KINDS}
    assert len(packs) >= 60 and all(len(by_kind[k]) >= 10 for k in ("practice", "regulation", "industry", "bundle"))
    cat = {p["id"]: p for p in packs}
    for p in packs:
        for t in p["entries"]:
            assert len(t["body"]) > 40, (p["id"], t["title"])
        titles = [_t["title"].lower() for _t in p["entries"]]
        assert len(titles) == len(set(titles)), f"duplicate rule title in {p['id']}"
        assert rp.resolve_entries(cat, p["id"]), p["id"]
        if p["kind"] == "bundle":
            assert all(i in cat and cat[i]["kind"] != "bundle" for i in p["includes"]), p["id"]
    assert {p["id"] for p in packs if p["baseline"]} >= {"security-baseline", "api-standards", "testing-quality", "code-style-naming"}


def test_every_regulation_and_industry_in_the_vocabulary_has_a_pack():
    packs = rp.builtin_packs()
    tagged = {k: {t for p in packs for t in p["tags"][k]} for k in ("industries", "regulations")}
    assert {i["id"] for i in prof.VOCAB["industry"]} <= tagged["industries"]
    assert {r["id"] for r in prof.VOCAB["regulation"]} <= tagged["regulations"]


def test_bundles_expand_in_order_without_repeating_a_rule():
    cat = {p["id"]: p for p in rp.builtin_packs()}
    ents = rp.resolve_entries(cat, "bundle-banking-eu")
    titles = [e["title"].lower() for e in ents]
    assert len(titles) == len(set(titles)) and {e["pack"] for e in ents} >= {"industry-banking", "gdpr", "dora", "psd2-open-banking"}
    assert rp.leaf_ids(cat, "bundle-banking-eu")[0] == "industry-banking"


def test_pack_validation_explains_what_is_wrong():
    ok = {"id": "my-pack", "name": "My pack", "kind": "practice", "entries": [{"category": "rule", "priority": "must", "stage": None, "title": "Do it", "body": "Do the thing."}]}
    assert rp.normalise_pack(ok)["version"] == 1
    for bad, msg in (({**ok, "id": "X"}, "id is"), ({**ok, "kind": "nope"}, "kind"), ({**ok, "tags": {"industries": ["nowhere"]}}, "unknown industries"),
                     ({**ok, "entries": []}, "at least one rule"), ({**ok, "kind": "bundle", "entries": [], "includes": []}, "include at least one"),
                     ({**ok, "kind": "bundle", "includes": ["x-pack"]}, "not rules of its own")):
        with pytest.raises(ValueError, match=msg):
            rp.normalise_pack(bad)
    with pytest.raises(ValueError, match="unknown packs"):
        rp.normalise_pack({"id": "set-a", "name": "Set A", "kind": "bundle", "includes": ["missing"]}, known_ids={"security-baseline"})


# ---------------------------------------------------------------- organisation packs
async def test_an_administrator_adds_replaces_hides_and_restores_packs():
    db, canon, _ = setup()
    admin = PackAdmin(db, FakeAudit(), canon.catalog)
    with pytest.raises(SdlcError) as e:
        await admin.save(PM, {"id": "our-pack", "name": "Our pack", "kind": "practice", "entries": []})
    assert e.value.code == "FORBIDDEN"
    saved = await admin.save(ADMIN, {"id": "our-pack", "name": "Our pack", "kind": "practice", "tags": {"industries": ["banking"]},
                                      "entries": [{"category": "rule", "priority": "must", "stage": 3, "title": "Name owners", "body": "Every service has a named owner."}]})
    assert saved["version"] == 1 and (await canon.catalog.all())["our-pack"]["source"] == "org"
    again = await admin.save(ADMIN, {**saved, "entries": [*saved["entries"], {"category": "rule", "priority": "should", "stage": None, "title": "Be kind", "body": "Be kind in reviews."}]})
    assert again["version"] == 2
    # customise a built-in: same id, the organisation's rules win for everyone
    custom = await admin.save(ADMIN, {"id": "security-baseline", "name": "Our security rules", "kind": "practice", "baseline": True,
                                      "entries": [{"category": "rule", "priority": "must", "stage": None, "title": "Own rule", "body": "Our own security rule."}]})
    cat = await canon.catalog.all()
    assert cat["security-baseline"]["name"] == "Our security rules" and cat["security-baseline"]["source"] == "customised"
    assert (await admin.delete_or_restore(ADMIN, "security-baseline")) == "restored"
    assert (await canon.catalog.all())["security-baseline"]["name"].startswith("Secure engineering")
    await admin.hide(ADMIN, "gdpr")
    assert "gdpr" not in await canon.catalog.all()
    assert (await admin.delete_or_restore(ADMIN, "gdpr")) == "restored" and "gdpr" in await canon.catalog.all()
    assert custom["id"] == "security-baseline"


async def test_a_loop_between_sets_and_a_hidden_included_pack_are_handled():
    db, canon, _ = setup()
    admin = PackAdmin(db, FakeAudit(), canon.catalog)
    await admin.save(ADMIN, {"id": "set-one", "name": "Set one", "kind": "bundle", "includes": ["security-baseline", "api-standards"]})
    await admin.hide(ADMIN, "api-standards")
    ents = await canon.catalog.entries("set-one")                 # the hidden pack is simply left out
    assert ents and {e["pack"] for e in ents} == {"security-baseline"}
    with pytest.raises(SdlcError):
        await admin.save(ADMIN, {"id": "set-one", "name": "Set one", "kind": "bundle", "includes": ["set-one"]})


async def test_export_and_import_round_trip():
    db, canon, _ = setup()
    admin = PackAdmin(db, FakeAudit(), canon.catalog)
    text = await admin.export_text("gdpr")
    assert text.startswith("id: gdpr") and "entries:" in text
    again = await admin.import_text(ADMIN, text.replace("id: gdpr", "id: gdpr-lite").replace("name: GDPR", "name: GDPR lite"))
    assert again["id"] == "gdpr-lite" and len(again["entries"]) == len(rp.builtin_packs()[[p["id"] for p in rp.builtin_packs()].index("gdpr")]["entries"])
    with pytest.raises(SdlcError):
        await admin.import_text(ADMIN, "- just\n- a list")
    with pytest.raises(SdlcError):
        await admin.import_text(ADMIN, "id: ok-pack\nname: Ok pack\nkind: practice\nentries: []")


async def test_applying_a_bundle_adds_every_included_rule_once_with_its_own_origin():
    db, canon, _ = setup()
    out = await canon.apply_pack("p1", PM, "bundle-saas-b2b")
    assert out["added"] > 40 and out["skipped"] == 0
    origins = {r["origin"].split("@")[0] for r in db.canon}
    assert {"pack:industry-saas-multi-tenant", "pack:soc2", "pack:gdpr"} <= origins
    assert (await canon.apply_pack("p1", PM, "soc2"))["added"] == 0


# ---------------------------------------------------------------- the profile
def test_effective_profile_is_organisation_default_then_project_override():
    org = [prof.normalise_item({"kind": "industry", "value": "banking"}, status="pinned", source="user"),
           prof.normalise_item({"kind": "regulation", "value": "sox"}, status="pinned", source="user"),
           prof.normalise_item({"kind": "regulation", "value": "gdpr"}, status="pinned", source="user")]
    mine = [prof.normalise_item({"kind": "industry", "value": "retail-ecommerce"}, status="identified", source="llm", stage=1),
            prof.normalise_item({"kind": "regulation", "value": "sox"}, status="excluded", source="user"),
            prof.normalise_item({"kind": "regulation", "value": "pci-dss"}, status="identified", source="llm", stage=2)]
    eff = prof.effective(org, mine)
    assert eff["values"]["industry"] == ["retail-ecommerce"]
    assert set(eff["values"]["regulation"]) == {"pci-dss", "gdpr"}                    # sox excluded, gdpr inherited
    states = {i["value"]: i["state"] for i in eff["items"]}
    assert states["retail-ecommerce"] == "identified" and states["gdpr"] == "inherited"
    assert prof.effective(org, [])["values"]["industry"] == ["banking"]
    assert "Retail" in prof.describe(eff)


def test_found_values_never_override_a_person():
    pinned = prof.normalise_item({"kind": "industry", "value": "insurance"}, status="pinned", source="user")
    gone = prof.normalise_item({"kind": "regulation", "value": "hipaa"}, status="excluded", source="user")
    items = [pinned, gone]
    found = [prof.normalise_item({"kind": "industry", "value": "banking"}, status="identified", source="llm", stage=2),
             prof.normalise_item({"kind": "regulation", "value": "hipaa"}, status="identified", source="llm", stage=2),
             prof.normalise_item({"kind": "regulation", "value": "gdpr"}, status="identified", source="llm", stage=2)]
    changes = prof.merge_found(items, found, stage=2)
    assert [c["value"] for c in changes] == ["gdpr"] and pinned in items and gone in items
    later = prof.merge_found(items, [prof.normalise_item({"kind": "regulation", "value": "gdpr"}, status="identified", source="llm", stage=3)], stage=3)
    assert later == []


def test_profile_values_are_validated():
    with pytest.raises(SdlcError):
        prof.normalise_item({"kind": "industry", "value": "space-pirates"}, status="pinned", source="user")
    with pytest.raises(SdlcError):
        prof.normalise_item({"kind": "colour", "value": "red"}, status="pinned", source="user")


async def test_organisation_default_project_override_and_confirmation():
    db, canon, cfg = setup()
    with pytest.raises(SdlcError):
        await cfg.set_org_profile(PM, [{"kind": "industry", "value": "banking"}])
    await cfg.set_org_profile(ADMIN, [{"kind": "industry", "value": "banking"}, {"kind": "regulation", "value": "gdpr"}, {"kind": "industry", "value": "insurance"}])
    assert [i["value"] for i in await cfg.org_profile() if i["kind"] == "industry"] == ["insurance"]     # one industry only
    eff = await cfg.effective_profile("p1")
    assert eff["values"]["industry"] == ["insurance"] and {i["state"] for i in eff["items"]} == {"inherited"}
    await cfg.apply_profile_found("p1", [{"kind": "industry", "value": "banking"}, {"kind": "regulation", "value": "psd2"}], stage=2, source="llm")
    eff = await cfg.effective_profile("p1")
    assert eff["values"]["industry"] == ["banking"] and {i["value"]: i["state"] for i in eff["items"]}["psd2"] == "identified"
    with pytest.raises(SdlcError):
        await cfg.update_profile("p1", DEV, upsert=[], remove=[], confirm=["industry:banking"])
    out = await cfg.update_profile("p1", PM, upsert=[{"kind": "regulation", "value": "gdpr", "status": "excluded"}], remove=[], confirm=["industry:banking", "regulation:psd2"])
    eff = out["effective"]
    assert {i["value"]: i["state"] for i in eff["items"]}["banking"] == "pinned" and "gdpr" not in eff["values"]["regulation"]
    back = await cfg.update_profile("p1", PM, upsert=[], remove=["regulation:gdpr"], confirm=[])
    assert "gdpr" in back["effective"]["values"]["regulation"]


def test_keyword_profile_detection():
    text = ("The bank offers retail banking and mortgage products. Card payments are processed in line with PCI DSS and GDPR. "
            "The bank must also meet DORA. A customer portal and mobile app serve customers in the EU. Bank staff use it daily.")
    got = {(f["kind"], f["value"]) for f in sa.detect_profile_from_text(text)}
    assert {("industry", "banking"), ("regulation", "pci-dss"), ("regulation", "gdpr"), ("regulation", "dora"), ("domain", "payments"),
            ("domain", "mobile-app"), ("region", "eu")} <= got
    assert sa.detect_profile_from_text("nothing relevant") == []


class Llm:
    def __init__(self, profile):
        self.profile = profile

    async def generate_json(self, *, schema, **kw):
        return schema(layers=[], profile=self.profile), SimpleNamespace(usage={}, model="m", provider="p")


async def test_the_advisor_identifies_the_profile_with_the_stack():
    db, canon, cfg = setup()
    adv = sa.StackAdvisor(Llm([{"kind": "regulation", "value": "hipaa", "evidence": "handles PHI", "confidence": "high"},
                               {"kind": "industry", "value": "healthcare", "evidence": "hospital", "confidence": "high"},
                               {"kind": "domain", "value": "ai-ml", "confidence": "low"}]), cfg, db)
    await adv.after_stage("p1", seq=1, template=1, documents=[("PRD", "A hospital system handling PHI.")])
    eff = await cfg.effective_profile("p1")
    assert eff["values"]["industry"] == ["healthcare"] and eff["values"]["regulation"] == ["hipaa"] and eff["values"]["domain"] == []   # low confidence dropped
    assert (await cfg.profile("p1"))["items"][0]["status"] == "identified"


# ---------------------------------------------------------------- recommendations and the advice at stages 2 and 3
def test_recommendation_prefers_the_best_ready_made_set_and_explains_why():
    cat = {p["id"]: p for p in rp.builtin_packs()}
    values = {"industry": ["banking"], "regulation": ["gdpr", "dora", "psd2"], "domain": ["payments"], "sensitivity": [], "region": ["eu"]}
    rec = rp.recommend(cat, values, set())
    assert rec["bundles"][0]["id"] == "bundle-banking-eu" and "Banking and lending" in rec["bundles"][0]["reasons"] and rec["bundles"][0]["missing"] > 40
    covered = {i["id"] for i in rec["bundles"][0]["includes"]}
    assert not covered & {p["id"] for p in rec["packs"]}                             # a pack the set covers is not repeated
    assert {b["id"] for b in rec["baseline"]} <= {p["id"] for p in cat.values() if p["baseline"]}
    assert [b["id"] for b in rec["baseline"]] == ["bundle-engineering-essentials"] or "security-baseline" in covered
    none = rp.recommend(cat, {"industry": [], "regulation": [], "domain": [], "sensitivity": [], "region": []}, set())
    assert none["bundles"] == [] and none["hasProfile"] is False and [b["id"] for b in none["baseline"]] == ["bundle-engineering-essentials"]


async def test_advice_is_due_on_stages_2_and_3_until_applied_or_dismissed():
    db, canon, cfg = setup()
    advisor = RuleAdvisor(canon, cfg)
    await cfg.update_profile("p1", PM, upsert=[{"kind": "industry", "value": "healthcare"}, {"kind": "regulation", "value": "hipaa"}], remove=[], confirm=[])
    r1 = await advisor.recommend("p1", PM, 1)
    assert r1["due"] is False                                                          # stage 1 is not when we advise
    r2 = await advisor.recommend("p1", PM, 2)
    assert r2["due"] is True and r2["primary"]["id"] == "bundle-healthcare-us" and r2["profile"]["text"].startswith("Healthcare")
    await advisor.dismiss("p1", PM, 2)
    assert (await advisor.recommend("p1", PM, 2))["due"] is False
    assert (await advisor.recommend("p1", PM, 3))["due"] is True                       # stage 3 asks again
    await canon.apply_packs("p1", PM, r2["todo"])
    done = await advisor.recommend("p1", PM, 3)
    assert done["due"] is False and done["primary"]["missing"] == 0                    # nothing left to advise
    with pytest.raises(SdlcError):
        await advisor.dismiss("p1", DEV, 3)


async def test_with_no_profile_the_advice_asks_for_one_and_offers_the_essentials():
    db, canon, cfg = setup()
    r = await RuleAdvisor(canon, cfg).recommend("p1", PM, 2)
    assert r["profile"]["empty"] is True and r["due"] is True and r["primary"] is None and [b["id"] for b in r["baseline"]] == ["bundle-engineering-essentials"]
