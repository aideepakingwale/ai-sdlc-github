"""Stack advisor: identifies, by layer, the technologies a stage's documents (or an uploaded codebase) already settle.

Three sources feed `projectconfig.json`: the LLM advisor (the quality path), a deterministic keyword extractor (the floor, used when
the model is unavailable or says nothing) and a codebase detector (file names and manifests, which are reliable). All of them only
*identify*; choosing is for the Technical Architect stage or a person.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from .agent_catalog import role_of
from .prompt_library import render as render_prompt
from .project_config import normalise_entry, render_entry
from .tech_catalog import get_layers, get_tech_catalog, layer_label

log = logging.getLogger("stack_advisor")

MAX_DOC_CHARS = 14_000
PER_DOC_CHARS = 5_000


class AdvisedLayer(BaseModel):
    layer: str
    technology: str = ""
    version: str = ""
    extras: list[str] = Field(default_factory=list)
    component: str = ""
    rationale: str = ""
    evidence: str = ""
    confidence: str = "medium"


class AdvisedProfile(BaseModel):
    kind: str
    value: str
    evidence: str = ""
    confidence: str = "medium"


class StackAdvice(BaseModel):
    layers: list[AdvisedLayer] = Field(default_factory=list)
    profile: list[AdvisedProfile] = Field(default_factory=list)
    notes: str = ""


# ---------------------------------------------------------------- deterministic extraction
def _term_re(term: str) -> re.Pattern[str]:
    return re.compile(r"(?<![\w.#+])" + re.escape(term.lower()) + r"(?![\w#+])", re.I)


def _canonical(layer: dict[str, Any]) -> list[tuple[str, str]]:
    """(spelling to look for, name to record) for a layer's options and aliases."""
    out: list[tuple[str, str]] = []
    for opt in layer.get("options") or []:
        name = str(opt)
        out.append((name, name))
        core = re.split(r"\s*\(", name)[0].strip()
        if core and core != name:
            out.append((core, name))
    options = [str(o) for o in layer.get("options") or []]
    for alias in layer.get("aliases") or []:
        # an alias records the option it spells, when there is one that contains it
        target = next((o for o in options if alias.lower() in o.lower() or o.lower() in alias.lower()), str(alias).title())
        out.append((str(alias), target))
    return out


def detect_from_text(text: str) -> list[dict[str, Any]]:
    """Layer entries for the technologies a text names. Keyword based, so only the common technologies; the most-mentioned wins per layer."""
    blob = text or ""
    if not blob.strip():
        return []
    found: list[dict[str, Any]] = []
    for layer in get_layers():
        if layer["id"] == "backend":
            continue
        counts: dict[str, int] = {}
        for term, name in _canonical(layer):
            n = len(_term_re(term).findall(blob))
            if n:
                counts[name] = counts.get(name, 0) + n
        if not counts:
            continue
        best = max(counts.items(), key=lambda kv: kv[1])[0]
        m = _term_re(re.split(r"\s*\(", best)[0]).search(blob)
        snippet = blob[max(0, (m.start() if m else 0) - 40):(m.end() if m else 0) + 60].replace("\n", " ").strip() if m else ""
        found.append({"layer": layer["id"], "technology": best, "evidence": snippet, "confidence": "medium", "rationale": "Named in the stage's documents."})
    from .stack import decide_from_text
    decided = decide_from_text(blob)
    if decided is not None and decided.language:
        found.append({"layer": "backend", "technology": decided.language, "version": decided.languageVersion, "extras": decided.frameworks,
                      "evidence": decided.raw or decided.language, "confidence": "medium", "rationale": "Language and framework named in the documents."})
    return found


_BULLET_RE = re.compile(r"(?im)^[ \t>*-]*\**\s*(?P<k>[A-Za-z /&]+?)\s*\**\s*:\s*\**\s*(?P<v>[^\n]{2,120})$")


def parse_decision_layers(text: str) -> list[dict[str, Any]]:
    """The `- Hosting: AWS` lines the Technical Architect writes under "## Technology stack decision" (one per layer)."""
    m = re.search(r"(?im)^#{1,4}[ \t]*technology[ \t]+stack[ \t]+decision[ \t]*$", text or "")
    if not m:
        return []
    section = "\n".join((text[m.end():]).splitlines()[:40])
    nxt = re.search(r"(?m)^#{1,4}[ \t]+\S", section)
    if nxt:
        section = section[:nxt.start()]
    labels = {layer_label(layer["id"]).lower(): layer["id"] for layer in get_layers()}
    labels.update({layer["id"]: layer["id"] for layer in get_layers()})
    aliases = {"backend": "backend", "api": "backend", "messaging": "messaging", "events": "messaging", "infra": "hosting", "infrastructure": "hosting",
               "ci/cd": "cicd", "ci": "cicd", "monitoring": "observability", "auth": "identity", "sso": "identity", "ui": "frontend", "db": "database"}
    out: list[dict[str, Any]] = []
    for mm in _BULLET_RE.finditer(section):
        key = re.sub(r"\s+", " ", mm.group("k")).strip().lower()
        lid = labels.get(key) or aliases.get(key) or next((v for k, v in labels.items() if key and (k.startswith(key) or key.startswith(k))), None)
        value = re.sub(r"[*_`]+", "", mm.group("v")).strip(" .")
        if not lid or not value or value.lower().startswith(("tbd", "n/a", "none", "not ")):
            continue
        parts = [p.strip() for p in re.split(r"\s*\+\s*", value) if p.strip()]
        v = re.match(r"^(?P<t>.*?)[ ]+(?P<v>v?\d[\w.\-]*(?:\s*\(LTS\))?)$", parts[0])
        tech, ver = (v.group("t"), v.group("v")) if v else (parts[0], "")
        out.append({"layer": lid, "technology": tech, "version": ver, "extras": parts[1:], "evidence": mm.group(0).strip()[:200],
                    "confidence": "high", "rationale": "Decided by the Technical Architect."})
    return out


# ---------------------------------------------------------------- the project profile (industry, regulations, what the system does)
_INDUSTRY_WORDS = {
    "banking": r"\b(bank|banking|lender|lending|mortgage|loan|retail banking|core banking)\b", "insurance": r"\b(insur\w*|policyholder|underwrit\w*|claims? handling)\b",
    "capital-markets": r"\b(trading|broker|capital markets?|order book|securities)\b", "payments-fintech": r"\b(fintech|payment (provider|service|processor)|wallet|acquirer)\b",
    "healthcare": r"\b(patient|clinical|hospital|clinician|ehr|electronic health)\b", "life-sciences": r"\b(pharma\w*|biotech|clinical trial|gxp|laborator\w+)\b",
    "retail-ecommerce": r"\b(e-?commerce|retail\w*|shopper|checkout|online store|basket)\b", "travel-hospitality": r"\b(hotel|booking engine|travel|hospitality)\b",
    "public-sector": r"\b(citizen|government|public sector|ministry|council|municipal\w*)\b", "education": r"\b(student|school|universit\w+|learner|teacher|edtech)\b",
    "telecom": r"\b(telecom\w*|subscriber|mobile network|5g|carrier|msisdn)\b", "energy-utilities": r"\b(utility|utilities|power grid|smart meter|energy supplier|scada)\b",
    "manufacturing": r"\b(manufactur\w+|factory|production line|mes|bill of materials)\b", "automotive": r"\b(vehicle|automotive|oem|connected car)\b",
    "logistics-transport": r"\b(logistics|shipment|freight|carrier tracking|fleet|warehouse)\b", "media-entertainment": r"\b(streaming|broadcast\w*|publisher|content rights|media company)\b",
    "saas-technology": r"\b(saas|multi-?tenant|software as a service)\b",
    "aviation": r"\b(airlines?|aircraft|airports?|flight (operations?|crew|plans?|schedules?)|pilots?|cabin crew|air traffic|easa|icao|iata)\b",
}
_REGULATION_WORDS = {
    "gdpr": r"\bgdpr\b|general data protection", "uk-gdpr": r"\buk gdpr\b|data protection act 2018", "ccpa-cpra": r"\bccpa\b|\bcpra\b", "pci-dss": r"\bpci[- ]?dss\b|\bpci\b",
    "hipaa": r"\bhipaa\b", "sox": r"\bsox\b|sarbanes", "soc2": r"\bsoc ?2\b", "iso-27001": r"\biso[ /]?(iec )?27001\b", "dora": r"\bdora\b|digital operational resilience",
    "nis2": r"\bnis ?2\b", "psd2": r"\bpsd ?2\b|open banking", "gxp-part11": r"\bgxp\b|21 cfr|part 11", "fedramp": r"\bfedramp\b|nist 800-53", "ferpa-coppa": r"\bferpa\b|\bcoppa\b",
    "iec-62443": r"\b62443\b", "unece-r155": r"\br155\b|iso/sae 21434", "eu-ai-act": r"\bai act\b", "wcag": r"\bwcag\b|section 508|accessib\w+",
    "easa-part-is": r"\bpart-is\b|\bdo-?326a?\b|\bed-?202a?\b", "do-178c": r"\bdo-?178c?\b|\bed-?12c?\b",
    "aviation-sms": r"safety management system|\bannex 19\b", "eu-261": r"\b261/2004\b|\b(ec|eu) ?261\b|denied boarding",
}
_DOMAIN_WORDS = {
    "payments": r"\b(payments?|card payments?|credit card|checkout)\b", "customer-pii": r"\b(personal data|customer data|pii|personally identifiable)\b",
    "health-records": r"\b(health records?|patient records?|phi|medical records?)\b", "public-web": r"\b(public website|web portal|public api|customer portal|public-facing)\b",
    "mobile-app": r"\b(mobile app|ios|android)\b", "ai-ml": r"\b(machine learning|ml model|llm|generative ai|ai feature)\b", "iot-devices": r"\b(iot|connected devices?|telemetry from devices)\b",
    "ot-industrial": r"\b(scada|plc|operational technology|industrial control)\b", "multi-tenant-saas": r"\b(multi-?tenant|tenants?)\b",
    "event-driven": r"\b(event-driven|message queue|kafka|event bus)\b", "regulatory-reporting": r"\bregulatory (reports?|reporting)\b",
    "flight-operations": r"\b(crew (scheduling|rostering|pairing)|rostering|operations control|ops control|flight operations|disruption management)\b",
    "aircraft-maintenance": r"\b(continuing airworthiness|part-?145|camo|maintenance records?|airworthiness|mro)\b",
}
_REGION_WORDS = {"eu": r"\b(european union|eu member|\beu\b|eea)\b", "uk": r"\b(united kingdom|\buk\b)\b", "us": r"\b(united states|u\.s\.|\bus\b|usa)\b",
                 "canada": r"\bcanad\w+\b", "apac": r"\b(apac|asia[- ]pacific|singapore|australia)\b", "middle-east": r"\b(middle east|gcc|uae|saudi)\b"}


def detect_profile_from_text(text: str) -> list[dict[str, Any]]:
    """Profile values a text names, by keyword. A fallback for the model, and a floor: the single most-mentioned industry, and every
    regulation, domain and region that is named at least once."""
    blob = text or ""
    if not blob.strip():
        return []
    found: list[dict[str, Any]] = []

    def snippet(rx: str) -> str:
        m = re.search(rx, blob, re.I)
        return blob[max(0, m.start() - 40):m.end() + 60].replace("\n", " ").strip() if m else ""

    counts = {k: len(re.findall(rx, blob, re.I)) for k, rx in _INDUSTRY_WORDS.items()}
    counts = {k: v for k, v in counts.items() if v}
    if counts:
        best = max(counts.items(), key=lambda kv: kv[1])
        if best[1] >= 2:
            found.append({"kind": "industry", "value": best[0], "evidence": snippet(_INDUSTRY_WORDS[best[0]]), "confidence": "medium"})
    for kind, words in (("regulation", _REGULATION_WORDS), ("domain", _DOMAIN_WORDS), ("region", _REGION_WORDS)):
        for value, rx in words.items():
            if re.search(rx, blob, re.I):
                found.append({"kind": kind, "value": value, "evidence": snippet(rx), "confidence": "medium"})
    return found


# ---------------------------------------------------------------- codebase detection
_FILES = (
    (r"(^|/)pom\.xml$", "backend", "Java", "Maven project"), (r"(^|/)build\.gradle(\.kts)?$", "backend", "Java", "Gradle project"),
    (r"(^|/)(requirements\.txt|pyproject\.toml|setup\.py|Pipfile)$", "backend", "Python", "Python project"),
    (r"(^|/)go\.mod$", "backend", "Go", "Go module"), (r"(^|/)Cargo\.toml$", "backend", "Rust", "Cargo crate"),
    (r"(^|/)[^/]+\.csproj$", "backend", "C#", ".NET project"), (r"(^|/)Gemfile$", "backend", "Ruby", "Bundler project"),
    (r"(^|/)composer\.json$", "backend", "PHP", "Composer project"),
    (r"(^|/)[^/]+\.tf$", "iac", "Terraform", "Terraform files"), (r"(^|/)cdk\.json$", "iac", "AWS CDK", "cdk.json"),
    (r"(^|/)template\.ya?ml$", "iac", "CloudFormation", "SAM / CloudFormation template"),
    (r"(^|/)\.github/workflows/[^/]+\.ya?ml$", "cicd", "GitHub Actions", "workflow files"), (r"(^|/)\.gitlab-ci\.ya?ml$", "cicd", "GitLab CI", ".gitlab-ci.yml"),
    (r"(^|/)Jenkinsfile$", "cicd", "Jenkins", "Jenkinsfile"), (r"(^|/)azure-pipelines\.ya?ml$", "cicd", "Azure DevOps", "azure-pipelines.yml"),
    (r"(^|/)Dockerfile(\.[\w-]+)?$", "compute", "Containers", "Dockerfile"),
)
_PKG_HINTS = (("react", "frontend", "React"), ("next", "frontend", "Next.js"), ("@angular/core", "frontend", "Angular"), ("vue", "frontend", "Vue"),
              ("svelte", "frontend", "Svelte"), ("express", "backend", "Node.js"), ("@nestjs/core", "backend", "Node.js"))
_TEXT_HINTS = (("database", "PostgreSQL", r"postgres(ql)?"), ("database", "MySQL", r"\bmysql\b"), ("database", "MongoDB", r"\bmongo(db)?\b"),
               ("cache", "Redis", r"\bredis\b"), ("messaging", "Kafka", r"\bkafka\b"), ("messaging", "RabbitMQ", r"\brabbitmq\b"),
               ("hosting", "AWS", r"\bhashicorp/aws\b|\bamazonaws\.com\b|aws-cdk"), ("hosting", "Azure", r"\bazurerm\b"), ("hosting", "GCP", r"\bgoogle_[a-z_]+\b|\bhashicorp/google\b"))


def detect_from_codebase(files: dict[str, str]) -> list[dict[str, Any]]:
    """Layer entries from an uploaded codebase: file names and a few manifests. `files` is {path: content (may be empty)}."""
    found: dict[tuple[str, str], dict[str, Any]] = {}

    def add(layer: str, tech: str, why: str, *, version: str = "", extras: list[str] | None = None) -> None:
        found.setdefault((layer, tech), {"layer": layer, "technology": tech, "version": version, "extras": extras or [], "evidence": why,
                                         "confidence": "high", "rationale": "Detected in the uploaded codebase."})

    for path in files:
        for rx, layer, tech, why in _FILES:
            if re.search(rx, path):
                add(layer, tech, f"{path} ({why})")
    for path, text in files.items():
        low = (text or "")[:60_000].lower()
        if path.endswith("package.json"):
            for key, layer, tech in _PKG_HINTS:
                if re.search(rf'"{re.escape(key)}"\s*:', low):
                    add(layer, tech, f"{path}: dependency {key}")
            if re.search(r'"typescript"\s*:', low):
                add("backend", "TypeScript", f"{path}: dependency typescript")
        if re.search(r"(\.tf|docker-compose[^/]*\.ya?ml|compose\.ya?ml|requirements\.txt|pom\.xml|application\.(ya?ml|properties))$", path) or path.endswith("package.json"):
            for layer, tech, rx in _TEXT_HINTS:
                if re.search(rx, low):
                    add(layer, tech, f"{path}: mentions {tech}")
        if path.endswith(("pom.xml",)) and "spring-boot" in low:
            add("backend", "Java", f"{path}: spring-boot", extras=["Spring Boot"])
        if path.endswith(("requirements.txt", "pyproject.toml")):
            for fw, name in (("fastapi", "FastAPI"), ("django", "Django"), ("flask", "Flask")):
                if fw in low:
                    add("backend", "Python", f"{path}: {fw}", extras=[name])
    # a framework-specific Python or Java entry replaces the generic one
    for lang in ("Python", "Java"):
        if ("backend", lang) in found and any(k[0] == "backend" and k[1] == lang and found[k]["extras"] for k in found):
            pass
    return list(found.values())


# ---------------------------------------------------------------- the advisor
def _layers_help() -> str:
    return "\n".join(f"- {layer['id']}: {layer.get('label', layer['id'])} ({layer.get('hint', '')})" for layer in get_layers())


def _profile_help() -> str:
    from . import profile as prof

    return "\n".join(f"- {kind} ({prof.KIND_LABEL[kind]}): " + ", ".join(f"{x['id']}" for x in prof.VOCAB[kind]) for kind in prof.KINDS)


def _digest(texts: list[tuple[str, str]]) -> str:
    out, used = [], 0
    for title, body in texts:
        chunk = f"### {title}\n{(body or '')[:PER_DOC_CHARS]}"
        if used + len(chunk) > MAX_DOC_CHARS:
            break
        out.append(chunk)
        used += len(chunk)
    return "\n\n".join(out)


class StackAdvisor:
    def __init__(self, llm: Any, config: Any, db: Any = None) -> None:
        self._llm, self._config, self._db = llm, config, db
        self.workflow: Any = None       # set after the workflow service exists (see main.py)

    async def from_codebase(self, project_id: str) -> list[dict[str, Any]]:
        """Identify the stack of an uploaded codebase from its file names and manifests. Never raises."""
        try:
            rows = list(await self._db.list_codebase_files(project_id))
            files: dict[str, str] = {r["path"]: "" for r in rows}
            manifests = [r for r in rows if re.search(
                r"(^|/)(package\.json|pom\.xml|requirements\.txt|pyproject\.toml|docker-compose[^/]*\.ya?ml|compose\.ya?ml|application\.(ya?ml|properties))$|\.tf$", r["path"])][:30]
            for r in manifests:
                full = await self._db.get_codebase_file(project_id, r["id"])
                if full:
                    files[r["path"]] = full["content"] or ""
            found = detect_from_codebase(files)
            return await self._config.apply_found(project_id, found, stage=0, source="detected", actor="codebase", run={"codebase": len(files)}) if found else []
        except Exception:  # noqa: BLE001
            log.warning("could not detect the stack of the uploaded codebase", exc_info=True)
            return []

    async def recheck(self, project_id: str, content: Any) -> list[dict[str, Any]]:
        """"Re-check from documents": the codebase and the finished stages 1 to 3, in order."""
        changes = await self.from_codebase(project_id)
        if self.workflow is None:
            return changes
        wf = await self.workflow.view(project_id)
        for stage in wf["stages"]:
            template = int(stage.get("template") or 0)
            if template not in (1, 2, 3):
                continue
            rows = [r for r in await self._db.list_phase_artefacts(project_id, int(stage["seq"])) if r["is_latest"]]
            docs: list[tuple[str, str]] = []
            for r in rows[:12]:
                body = (await content.get(r["storage_key"])) if r["storage_key"] else None
                docs.append((r["title"], body or r["content"] or ""))
            if docs:
                changes += await self.after_stage(project_id, seq=int(stage["seq"]), template=template, documents=docs, decider=template == 3)
        return changes

    async def after_stage(self, project_id: str, *, seq: int, template: int, documents: list[tuple[str, str]], decider: bool = False,
                          emit: Any = None) -> list[dict[str, Any]]:
        """Identify the stack from a finished stage's documents and record it. Returns the changes. Never raises."""
        try:
            docs = [(t, b) for t, b in documents if b and b.strip()]
            if not docs:
                return []
            current = await self._config.layers(project_id)
            found: list[dict[str, Any]] = []
            run: dict[str, Any] = {"template": template}
            blob = "\n\n".join(b for _, b in docs)
            if decider:
                found += parse_decision_layers(blob)
            llm_found, llm_profile = await self._ask_model(docs, current, template)
            run["model"] = bool(llm_found is not None)
            if llm_found:
                found += llm_found
            elif llm_found is None or not found:
                found += detect_from_text(blob)         # the floor: the model failed or said nothing
            seen: set[str] = set()
            unique = []
            for f in found:
                key = f"{f['layer']}/{f.get('component', '')}"
                if key in seen:
                    continue
                seen.add(key)
                unique.append(f)
            changes = await self._config.apply_found(project_id, unique, stage=seq, source="ta" if decider else "llm", decider=decider, run=run)
            profile_found = llm_profile or detect_profile_from_text(blob)
            if profile_found:
                await self._config.apply_profile_found(project_id, profile_found, stage=seq, source="llm", run={"template": template})
            if emit and changes:
                emit({"type": "node", "node": "agent", "label": "Technology stack identified: " + "; ".join(
                    f"{layer_label(c['layer'])}: {c['to']}" for c in changes[:6])})
            return changes
        except Exception:  # noqa: BLE001
            log.warning("stack advisor failed", exc_info=True)
            return []

    async def _ask_model(self, docs: list[tuple[str, str]], current: list[dict[str, Any]], template: int) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]]]:
        """The model's layer and profile findings: ([], []) when it found nothing, (None, []) when the call failed."""
        try:
            have = "\n".join(f"- {layer_label(e['layer'])}: {render_entry(e)} [{e['status']}]" for e in current if render_entry(e)) or "(nothing recorded yet)"
            system = render_prompt("stack_advisor.system", layers=_layers_help(), profile_vocab=_profile_help())
            user = render_prompt("stack_advisor.user", stage=str(template), current=have, documents=_digest(docs))
            advice, _res = await self._llm.generate_json(
                intent="standard", tag="stack_advisor", schema=StackAdvice, temperature=0, max_tokens=2000, max_attempts=2,
                role=role_of("stack-advisor", "light"),
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
        except Exception:  # noqa: BLE001
            log.warning("stack advisor model call failed", exc_info=True)
            return None, []
        profile = [p.model_dump() for p in advice.profile if p.confidence.lower() != "low" and p.value.strip()]
        out = []
        for a in advice.layers:
            if a.confidence.lower() == "low" or not a.technology.strip():
                continue
            out.append(a.model_dump())
        return out, profile
