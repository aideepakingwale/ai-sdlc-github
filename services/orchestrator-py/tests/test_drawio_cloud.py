"""Provider-correct, professionally laid-out draw.io diagrams (deterministic, zero-token)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import pytest

from app.services import cloud_catalog as cat
from app.services.drawio import _route, cloud_arch_to_drawio, validate_drawio

GENERIC_KEYS = ["user", "cdn", "dns", "waf", "api", "loadbalancer", "service", "function", "database", "postgres",
                "nosql", "cache", "queue", "topic", "stream", "storage", "identity", "secrets", "monitoring",
                "registry", "kubernetes", "vm", "container"]


def spec(**over):
    base = {
        "title": "Orders", "direction": "LR",
        "clusters": [{"id": "net", "label": "Virtual network", "parent": ""},
                     {"id": "priv", "label": "Private subnet", "parent": "net"}],
        "nodes": [{"id": "user", "label": "Customer", "service": "user", "group": ""},
                  {"id": "cdn", "label": "Edge", "service": "cdn", "group": ""},
                  {"id": "gw", "label": "API", "service": "api", "group": "net"},
                  {"id": "svc", "label": "Orders", "service": "service", "group": "priv"},
                  {"id": "db", "label": "Orders DB", "service": "database", "group": "priv"},
                  {"id": "q", "label": "Events", "service": "queue", "group": ""}],
        "edges": [{"fromId": "user", "toId": "cdn", "label": "HTTPS"}, {"fromId": "cdn", "toId": "gw", "label": ""},
                  {"fromId": "gw", "toId": "svc", "label": "REST"}, {"fromId": "svc", "toId": "db", "label": "SQL"},
                  {"fromId": "svc", "toId": "q", "label": "publish event"}],
    }
    base.update(over)
    return base


def cells(xml: str) -> dict[str, ET.Element]:
    return {c.get("id"): c for c in ET.fromstring(xml).iter("mxCell")}


def absolute(by_id, cid):
    c = by_id[cid]
    g = c.find("mxGeometry")
    x, y = float(g.get("x")), float(g.get("y"))
    p = c.get("parent")
    while p in by_id and p not in ("0", "1"):
        pg = by_id[p].find("mxGeometry")
        x, y, p = x + float(pg.get("x")), y + float(pg.get("y")), by_id[p].get("parent")
    return x, y, float(g.get("width")), float(g.get("height"))


# ------------------------------------------------------------------ catalog
@pytest.mark.parametrize(("provider", "key", "icon"), [(p, k, v) for p, m in cat.ICONS.items() for k, v in m.items()])
def test_every_catalog_icon_exists(provider, key, icon):
    assert cat.icon_file(icon[0]), f"{provider}/{key}: missing asset {icon[0]}"
    assert cat.icon_data_uri(icon[0]).startswith("data:image/png,")


def test_a_cloud_never_gets_another_clouds_icon():
    for provider in ("aws", "azure", "gcp"):
        for key in GENERIC_KEYS:
            r = cat.resolve(provider, key)
            if r.icon_path:
                assert r.icon_path.split("/")[0] in (provider, "onprem"), (provider, key, r.icon_path)
                if r.icon_path.startswith("onprem/"):
                    assert r.tier == "actor"          # only neutral actors (user/client) may be shared
    # a service the cloud has no icon for is a neutral box, not a substitute
    assert cat.resolve("azure", "nat").icon_path is None
    assert cat.resolve("gcp", "igw").icon_path is None


@pytest.mark.parametrize(("declared", "texts", "keys", "expected"), [
    ("azure", ["Built on AWS"], ["lambda"], "azure"),                          # declaration wins
    (None, [], ["aks", "cosmosdb", "servicebus"], "azure"),                      # vendor service keys
    (None, ["Order platform on Microsoft Azure with AKS"], ["database", "queue", "cdn"], "azure"),
    (None, ["Deployed on AWS (ECS Fargate, DynamoDB)"], ["service"], "aws"),
    (None, ["Runs on Google Cloud: GKE and BigQuery"], [], "gcp"),
    (None, ["on-prem VMware data centre"], ["database"], "onprem"),
    (None, ["Order platform"], ["database", "queue", "cdn"], "generic"),         # nothing indicated
    (None, ["Azure or AWS — undecided"], [], "generic"),                         # ambiguous → never guess
    ("auto", ["Azure"], [], "azure"),
])
def test_provider_inference(declared, texts, keys, expected):
    assert cat.infer_provider(declared, texts, keys).provider == expected


def test_vendor_keys_resolve_to_the_same_canonical_service():
    for key, canon in {"cosmosdb": "nosql_db", "dynamodb": "nosql_db", "firestore": "nosql_db", "servicebus": "queue",
                       "sqs": "queue", "keyvault": "secrets", "secretsmanager": "secrets", "aks": "kubernetes",
                       "eks": "kubernetes", "gke": "kubernetes", "azure_sql": "relational_db", "aws-lambda": "function"}.items():
        assert cat.canonical(key) == canon, key


# ------------------------------------------------------------------ the diagram
def uris(provider):
    return {cat.icon_data_uri(v[0]) for v in cat.ICONS[provider].values() if not v[0].startswith("onprem/")}


def test_azure_solution_gets_azure_icons_and_no_aws_icons():
    xml = cloud_arch_to_drawio(spec(), context=["Order platform on Microsoft Azure"])
    assert 'provider="azure"' in xml and "Microsoft Azure" in xml
    azure_used = {u for u in uris("azure") if u in xml}
    assert len(azure_used) >= 4                                   # cdn/api/service/db/queue icons
    assert not any(u in xml for u in uris("aws")) and not any(u in xml for u in uris("gcp"))
    assert "Azure Cosmos DB" not in xml and "Azure SQL Database" in xml and "Azure Service Bus" in xml
    assert "mxgraph.aws4" not in xml


def test_aws_and_gcp_get_their_own_icons():
    for provider, label, other in (("aws", "AWS Cloud", "azure"), ("gcp", "Google Cloud", "aws")):
        xml = cloud_arch_to_drawio(spec(provider=provider))
        assert f'provider="{provider}"' in xml and label in xml
        assert any(u in xml for u in uris(provider)) and not any(u in xml for u in uris(other))


def test_no_cloud_indicated_means_neutral_shapes_not_a_guessed_cloud():
    xml = cloud_arch_to_drawio(spec())
    assert 'provider="generic"' in xml and "neutral shapes" in xml
    assert not any(u in xml for p in ("aws", "azure", "gcp") for u in uris(p))
    by = cells(xml)
    assert "fillColor=#E8F5E9" in by["n_db"].get("style")          # data tier colour, plain box


def test_layout_follows_the_request_flow_not_a_grid():
    by = cells(cloud_arch_to_drawio(spec(provider="azure")))
    xs = [absolute(by, f"n_{n}")[0] for n in ("user", "cdn", "gw", "svc")]
    assert xs == sorted(xs) and len(set(xs)) == 4                  # left → right along the path
    tb = cells(cloud_arch_to_drawio(spec(provider="azure", direction="TB")))
    ys = [absolute(tb, f"n_{n}")[1] for n in ("user", "cdn", "gw", "svc")]
    assert ys == sorted(ys) and len(set(ys)) == 4                  # top → down


def test_users_sit_outside_the_cloud_frame_and_clusters_are_real_containers():
    by = cells(cloud_arch_to_drawio(spec(provider="azure")))
    fx, fy, fw, fh = absolute(by, "frame")
    ux, _, uw, _ = absolute(by, "n_user")
    assert ux + uw <= fx                                            # outside, to the left
    assert by["n_db"].get("parent") == "c_priv" and by["c_priv"].get("parent") == "c_net"
    assert by["c_net"].get("parent") == "frame"
    for cid in ("n_db", "n_svc", "c_priv", "c_net"):                # children lie inside the frame
        x, y, w, h = absolute(by, cid)
        assert fx <= x and x + w <= fx + fw and fy <= y and y + h <= fy + fh
    assert "container=1" in by["frame"].get("style")


def test_title_legend_labels_and_async_edges():
    xml = cloud_arch_to_drawio(spec(provider="azure"))
    by = cells(xml)
    assert "Target platform: Microsoft Azure" in by["title"].get("value")
    assert "Legend" in by["legend"].get("value")
    assert "Azure SQL Database" in by["n_db"].get("value")          # name + official service name
    publish = next(c for c in by.values() if c.get("value") == "publish event")
    assert "dashed=1" in publish.get("style")                        # async flow dashed
    sync = next(c for c in by.values() if c.get("value") == "REST")
    assert "dashed=1" not in sync.get("style")


def test_generated_diagram_validates_clean():
    v = validate_drawio(cloud_arch_to_drawio(spec(provider="azure")))
    assert v["ok"] and v["warnings"] == [] and v["stats"]["overlaps"] == 0


# ------------------------------------------------------------------ routing
def test_edges_are_routed_around_shapes():
    blocker = (180, 60, 60, 80)
    path = _route((100, 100), (400, 100), [blocker], (600, 300))
    assert path and path[0] == (100, 100) and path[-1] == (400, 100)
    for (x1, y1), (x2, y2) in zip(path, path[1:], strict=False):
        assert x1 == x2 or y1 == y2                                   # orthogonal segments only
        for xx in range(min(x1, x2), max(x1, x2) + 1, 5):
            for yy in range(min(y1, y2), max(y1, y2) + 1, 5):
                assert not (blocker[0] < xx < blocker[0] + blocker[2] and blocker[1] < yy < blocker[1] + blocker[3])
    assert len(path) >= 4                                             # it detours


def test_routed_waypoints_are_written_into_the_file():
    xml = cloud_arch_to_drawio(spec(provider="azure"))
    assert '<Array as="points">' in xml


# ------------------------------------------------------------------ validator
def test_validator_rejects_mixed_cloud_icon_libraries():
    xml = ('<mxfile><diagram name="Arch"><mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
           '<mxCell id="a" value="A" style="shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.ec2;" vertex="1" parent="1">'
           '<mxGeometry x="0" y="0" width="60" height="60" as="geometry"/></mxCell>'
           '<mxCell id="b" value="B" style="image;image=img/lib/azure2/databases/SQL_Database.svg;" vertex="1" parent="1">'
           '<mxGeometry x="200" y="0" width="60" height="60" as="geometry"/></mxCell>'
           '<mxCell id="e" value="x" style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="1" source="a" target="b">'
           '<mxGeometry relative="1" as="geometry"/></mxCell></root></mxGraphModel></diagram></mxfile>')
    r = validate_drawio(xml)
    assert not r["ok"] and any("Mixed cloud icon libraries (aws + azure)" in e for e in r["errors"])
    assert validate_drawio(xml.replace('name="Arch"', 'name="Hybrid multi-cloud"'))["ok"]   # explicit multi-cloud allowed


def test_validator_flags_declared_provider_vs_icons():
    xml = ('<mxfile provider="azure"><diagram name="Arch"><mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
           '<mxCell id="a" value="A" style="shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.ec2;" vertex="1" parent="1">'
           '<mxGeometry x="0" y="0" width="60" height="60" as="geometry"/></mxCell></root></mxGraphModel></diagram></mxfile>')
    assert any("targets azure but uses aws" in e for e in validate_drawio(xml)["errors"])


def test_validator_uses_page_coordinates_for_nested_shapes():
    # two shapes inside a container at the same RELATIVE spot but different parents must not "overlap"
    xml = ('<mxfile><diagram name="d"><mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
           '<mxCell id="c1" value="C1" style="container=1;" vertex="1" parent="1"><mxGeometry x="0" y="0" width="200" height="200" as="geometry"/></mxCell>'
           '<mxCell id="c2" value="C2" style="container=1;" vertex="1" parent="1"><mxGeometry x="400" y="0" width="200" height="200" as="geometry"/></mxCell>'
           '<mxCell id="a" value="A" style="rounded=1;" vertex="1" parent="c1"><mxGeometry x="20" y="20" width="80" height="40" as="geometry"/></mxCell>'
           '<mxCell id="b" value="B" style="rounded=1;" vertex="1" parent="c2"><mxGeometry x="20" y="20" width="80" height="40" as="geometry"/></mxCell>'
           '</root></mxGraphModel></diagram></mxfile>')
    assert validate_drawio(xml)["stats"]["overlaps"] == 0


# ------------------------------------------------------------------ SVG renderer shares the catalog
def test_svg_renderer_picks_icons_from_the_target_cloud(monkeypatch):
    import diagrams.custom as custom
    import diagrams.generic.blank as blank

    from app.services import diagram_render

    made: list[tuple[str, str | None]] = []
    monkeypatch.setattr(custom, "Custom", lambda label, icon: made.append((label, icon)) or object())
    monkeypatch.setattr(blank, "Blank", lambda label: made.append((label, None)) or object())
    make = diagram_render._node_factory("azure")
    make({"id": "db", "label": "Orders DB", "service": "database"})
    make({"id": "nat", "label": "Egress", "service": "nat"})                  # no Azure icon → neutral box
    assert re.search(r"resources/azure/database/sql-databases\.png$", made[0][1]) and "Azure SQL Database" in made[0][0]
    assert made[1] == ("Egress", None)


# ------------------------------------------------------------------ the skill: LLM decides, code enforces
from types import SimpleNamespace  # noqa: E402

from app.agents.schemas import CloudArchitecture  # noqa: E402
from app.services.skills import SkillContext, _drawio_architecture  # noqa: E402


class SpecLlm:
    def __init__(self, result=None, fail=False):
        self.result, self.fail, self.calls = result, fail, []

    async def generate_json(self, *, schema, messages, **kw):
        self.calls.append((schema, messages))
        if self.fail:
            raise RuntimeError("provider down")
        return schema(**self.result), SimpleNamespace(provider="p", model="m", tier="frontier")


def _ctx(llm, tech="Spring Boot on Microsoft Azure"):
    return SkillContext(project_id="p", phase=2, tech_stack=tech, user=None, user_input="Draw the order platform",
                        deps=SimpleNamespace(llm=llm))


async def test_drawio_skill_draws_the_models_spec_with_the_right_cloud_icons():
    llm = SpecLlm(spec(provider="azure"))
    out = await _drawio_architecture(_ctx(llm))
    assert out["output"].startswith("```drawio\n<mxfile") and "Target platform: **azure**" in out["output"]
    assert out["meta"]["valid"] is True and out["meta"]["cloud"] == "azure"
    assert any(u in out["output"] for u in uris("azure")) and not any(u in out["output"] for u in uris("aws"))
    assert llm.calls[0][0] is CloudArchitecture                      # the model returns a spec, never XML
    assert "One cloud per diagram" in llm.calls[0][1][0]["content"]  # the skill instruction is what it was given


async def test_drawio_skill_infers_the_cloud_from_the_projects_stack_when_the_model_does_not_declare_it():
    out = await _drawio_architecture(_ctx(SpecLlm(spec()), tech="Java on Microsoft Azure with AKS"))
    assert out["meta"]["cloud"] == "azure"
    neutral = await _drawio_architecture(_ctx(SpecLlm(spec()), tech="Node.js + TypeScript"))
    assert neutral["meta"]["cloud"] == "generic"                       # no cloud indicated → no guessed icons


async def test_drawio_skill_reports_a_model_failure_instead_of_raising():
    out = await _drawio_architecture(_ctx(SpecLlm(fail=True)))
    assert "could not produce" in out["output"] and out["meta"]["valid"] is False
