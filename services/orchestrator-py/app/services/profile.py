"""The project profile: industry, regulations, domains, data sensitivity and regions.

It is what makes advice specific. An organisation sets a default; each project may override it; and the platform can identify values from
the project's own documents for a person to confirm. The profile drives which rule packs are recommended (see rule_packs.recommend).

Items have the same lifecycle as the stack's layers: `pinned` (a person set it), `identified` (the platform found it in the documents,
awaiting confirmation) or `excluded` (a person removed an inherited organisation value for this project). Organisation values show as
`inherited`. Nothing the platform finds ever overrides a person's choice.
"""
from __future__ import annotations

from typing import Any

from ..domain.errors import SdlcError

SINGLE = ("industry", "sensitivity")
MULTI = ("regulation", "domain", "region")
KINDS = (*SINGLE, *MULTI)
STATUSES = ("pinned", "identified", "excluded")

# The vocabulary. Pack tags and the screen's choices come from here, so a recommendation can always be explained in these words.
VOCAB: dict[str, list[dict[str, str]]] = {
    "industry": [
        {"id": "banking", "label": "Banking and lending"}, {"id": "capital-markets", "label": "Capital markets and trading"},
        {"id": "payments-fintech", "label": "Payments and fintech"}, {"id": "insurance", "label": "Insurance"},
        {"id": "healthcare", "label": "Healthcare providers and health IT"}, {"id": "life-sciences", "label": "Life sciences and pharma"},
        {"id": "retail-ecommerce", "label": "Retail and e-commerce"}, {"id": "travel-hospitality", "label": "Travel and hospitality"},
        {"id": "public-sector", "label": "Public sector and government"}, {"id": "education", "label": "Education"},
        {"id": "telecom", "label": "Telecommunications"}, {"id": "energy-utilities", "label": "Energy and utilities"},
        {"id": "manufacturing", "label": "Manufacturing"}, {"id": "automotive", "label": "Automotive"},
        {"id": "logistics-transport", "label": "Logistics and transport"}, {"id": "media-entertainment", "label": "Media and entertainment"},
        {"id": "saas-technology", "label": "Software and SaaS"}, {"id": "aviation", "label": "Aviation and airlines"},
    ],
    "regulation": [
        {"id": "gdpr", "label": "GDPR"}, {"id": "uk-gdpr", "label": "UK GDPR"}, {"id": "ccpa-cpra", "label": "CCPA / CPRA"},
        {"id": "pci-dss", "label": "PCI DSS"}, {"id": "hipaa", "label": "HIPAA"}, {"id": "sox", "label": "SOX"},
        {"id": "soc2", "label": "SOC 2"}, {"id": "iso-27001", "label": "ISO 27001"}, {"id": "dora", "label": "DORA"},
        {"id": "nis2", "label": "NIS2"}, {"id": "psd2", "label": "PSD2"}, {"id": "gxp-part11", "label": "GxP / 21 CFR Part 11"},
        {"id": "fedramp", "label": "FedRAMP / NIST 800-53"}, {"id": "ferpa-coppa", "label": "FERPA / COPPA"},
        {"id": "iec-62443", "label": "IEC 62443"}, {"id": "unece-r155", "label": "UNECE R155 / ISO 21434"},
        {"id": "eu-ai-act", "label": "EU AI Act"}, {"id": "wcag", "label": "Accessibility (WCAG / Section 508)"},
        {"id": "easa-part-is", "label": "EASA Part-IS / DO-326A (aviation cybersecurity)"}, {"id": "do-178c", "label": "DO-178C (airborne software)"},
        {"id": "aviation-sms", "label": "Aviation safety management (ICAO Annex 19 / EASA)"}, {"id": "eu-261", "label": "Passenger rights (EC 261/2004)"},
    ],
    "domain": [
        {"id": "payments", "label": "Takes or moves payments"}, {"id": "customer-pii", "label": "Holds customer personal data"},
        {"id": "health-records", "label": "Holds health records"}, {"id": "public-web", "label": "Public website or API"},
        {"id": "mobile-app", "label": "Mobile app"}, {"id": "ai-ml", "label": "AI or machine-learning features"},
        {"id": "iot-devices", "label": "Connected devices"}, {"id": "ot-industrial", "label": "Industrial or operational technology"},
        {"id": "multi-tenant-saas", "label": "Multi-tenant SaaS"}, {"id": "data-platform", "label": "Data or analytics platform"},
        {"id": "event-driven", "label": "Event-driven or messaging"}, {"id": "regulatory-reporting", "label": "Regulatory or financial reporting"},
        {"id": "internal-tools", "label": "Internal tool"}, {"id": "flight-operations", "label": "Flight, crew or airline operations"},
        {"id": "aircraft-maintenance", "label": "Aircraft maintenance and airworthiness records"},
    ],
    "sensitivity": [
        {"id": "public", "label": "Public"}, {"id": "internal", "label": "Internal"}, {"id": "confidential", "label": "Confidential"},
        {"id": "restricted", "label": "Restricted (regulated or highly sensitive)"},
    ],
    "region": [
        {"id": "eu", "label": "European Union"}, {"id": "uk", "label": "United Kingdom"}, {"id": "us", "label": "United States"},
        {"id": "canada", "label": "Canada"}, {"id": "apac", "label": "Asia-Pacific"}, {"id": "middle-east", "label": "Middle East"},
        {"id": "latam", "label": "Latin America"}, {"id": "africa", "label": "Africa"},
    ],
}
KIND_LABEL = {"industry": "Industry", "regulation": "Regulations", "domain": "What it does", "sensitivity": "Data sensitivity", "region": "Regions"}


def valid_ids(kind: str) -> set[str]:
    return {x["id"] for x in VOCAB.get(kind, [])}


def label_of(kind: str, value: str) -> str:
    return next((x["label"] for x in VOCAB.get(kind, []) if x["id"] == value), value)


def item_id(kind: str, value: str) -> str:
    return f"{kind}:{value}"


def normalise_item(raw: dict[str, Any], *, status: str, source: str, actor: str = "", stage: int | None = None) -> dict[str, Any]:
    kind, value = str(raw.get("kind") or "").strip(), str(raw.get("value") or "").strip().lower()
    if kind not in KINDS:
        raise SdlcError("VALIDATION_FAILED", f"Unknown profile field '{raw.get('kind')}'")
    if status not in STATUSES:
        raise SdlcError("VALIDATION_FAILED", "Invalid profile status")
    if value not in valid_ids(kind):
        raise SdlcError("VALIDATION_FAILED", f"'{raw.get('value')}' is not a known {KIND_LABEL[kind].lower()} value")
    from .project_config import now_iso

    return {"id": item_id(kind, value), "kind": kind, "value": value, "status": status, "source": source, "sourceStage": stage,
            "evidence": " ".join(str(raw.get("evidence") or "").split())[:240], "confidence": str(raw.get("confidence") or "high"),
            "updatedAt": now_iso(), "updatedBy": actor}


def merge_found(items: list[dict[str, Any]], found: list[dict[str, Any]], *, stage: int) -> list[dict[str, Any]]:
    """Fold what the advisor found into a project's profile items (in place). A pinned or excluded value is never touched. A single-valued
    field is only filled when nothing is pinned; a later stage replaces an earlier identified value."""
    changes: list[dict[str, Any]] = []
    for f in found:
        f = {**f, "sourceStage": stage}
        if f["kind"] in SINGLE:
            cur = next((i for i in items if i["kind"] == f["kind"] and i["status"] in ("pinned", "identified")), None)
            if cur is not None:
                if cur["status"] == "pinned" or cur["value"] == f["value"] or (cur.get("sourceStage") or 0) > stage:
                    continue
                items.remove(cur)
        else:
            cur = next((i for i in items if i["id"] == f["id"]), None)
            if cur is not None:
                continue                                        # already pinned, identified or excluded: leave it
        items.append(f)
        changes.append({"kind": f["kind"], "value": f["value"], "action": "identified", "stage": stage})
    return changes


def effective(org_items: list[dict[str, Any]], project_items: list[dict[str, Any]]) -> dict[str, Any]:
    """What applies to a project: the organisation's default, overridden by the project's own values.

    Returns {"items": [{kind, value, label, state, origin, evidence, sourceStage}], "values": {kind: [value, ...]}}, where state is
    `pinned`, `identified` (awaiting confirmation) or `inherited`, and an excluded organisation value is left out."""
    out: list[dict[str, Any]] = []
    excluded = {i["id"] for i in project_items if i["status"] == "excluded"}
    for kind in KINDS:
        mine = [i for i in project_items if i["kind"] == kind and i["status"] in ("pinned", "identified")]
        theirs = [i for i in org_items if i["kind"] == kind]
        if kind in SINGLE:
            pick = next((i for i in mine if i["status"] == "pinned"), None) or next((i for i in mine), None)
            if pick is not None:
                out.append(_view(pick, pick["status"], "project"))
            elif theirs and theirs[0]["id"] not in excluded:
                out.append(_view(theirs[0], "inherited", "organisation"))
        else:
            seen: set[str] = set()
            for i in mine:
                out.append(_view(i, i["status"], "project"))
                seen.add(i["value"])
            for i in theirs:
                if i["value"] not in seen and i["id"] not in excluded:
                    out.append(_view(i, "inherited", "organisation"))
    values: dict[str, list[str]] = {k: [] for k in KINDS}
    for i in out:
        values[i["kind"]].append(i["value"])
    return {"items": out, "values": values}


def _view(i: dict[str, Any], state: str, origin: str) -> dict[str, Any]:
    return {"id": i["id"], "kind": i["kind"], "value": i["value"], "label": label_of(i["kind"], i["value"]), "state": state, "origin": origin,
            "evidence": i.get("evidence", ""), "sourceStage": i.get("sourceStage")}


def describe(eff: dict[str, Any]) -> str:
    """The profile in one line for a prompt or a screen: `Banking and lending; GDPR, DORA; takes or moves payments`."""
    parts = []
    for kind in KINDS:
        labels = [i["label"] for i in eff["items"] if i["kind"] == kind]
        if labels:
            parts.append(", ".join(labels))
    return "; ".join(parts)
