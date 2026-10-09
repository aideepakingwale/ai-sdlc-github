"""Project-aware artifact applicability (deterministic, zero-token).

The stage templates are a fixed "harness": e.g. the QA stage always lists a Playwright UI
suite. A harness must not force artifacts onto a project that cannot use them — an
API-only service has no UI to automate. This module infers a few project TRAITS from the
project configuration, requirements, upstream outputs and the user's own instructions,
then maps artifact types to the trait they need. An artifact whose required trait is
confidently ABSENT is "not applicable": the plan shows it as not recommended (with the
reason), generation skips it, and the prompt is told it is out of scope.

Conservative by design: an unknown trait never excludes anything, and anything the user
explicitly asks for ("Produce ONLY …") always wins over inference."""

from __future__ import annotations

import re
from typing import Any

# Artifact types actually produced per stage template — the declared outputs PLUS the
# toolchain-derived extras the stage generates (so the plan can show and gate them too).
GENERATED_ARTIFACTS: dict[int, list[str]] = {
    1: ["EPIC", "FEATURE", "USER_STORY", "PRD"],
    2: ["HLD", "ADR", "STRUCTURIZR_DSL", "HLD_DIAGRAM", "ARCH_DIAGRAM", "CLOUDCRAFT_JSON"],
    3: ["LLD", "LLD_DIAGRAM", "PLANTUML", "OPENAPI", "DBML", "CDK", "COMPONENT_DIAGRAM"],
    4: ["TEST_STRATEGY", "XRAY_TESTS", "K6_SCRIPT", "POSTMAN_COLLECTION", "RTM",
        "REST_ASSURED", "PLAYWRIGHT_SPEC", "JMETER_PLAN", "LOCUSTFILE"],
    5: ["GITHUB_ACTIONS", "DOCKERFILE", "GRAFANA_DASHBOARD", "PIPELINE_DESIGN", "SECURITY_SCAN"],
    6: ["APP_CODE", "UNIT_TESTS", "PULL_REQUEST"],
}

# Which project traits an artifact type needs. ALL listed traits must not be confidently
# absent. Types not listed apply to every project (core deliverables of the stage).
# Derived trait `service` = the project exposes an API or a UI (something to load-test,
# scan or monitor at runtime) — false for libraries, CLIs and batch jobs.
REQUIRES_TRAIT: dict[str, tuple[str, ...]] = {
    # Stage 2 — solution architecture
    "CLOUDCRAFT_JSON": ("aws",),            # AWS topology
    # Stage 3 — technical design
    "OPENAPI": ("api",),
    "DBML": ("database",),
    "CDK": ("aws",),                        # AWS CDK stack
    # Stage 4 — test engineering
    "POSTMAN_COLLECTION": ("api",),
    "REST_ASSURED": ("api",),
    "PLAYWRIGHT_SPEC": ("ui",),
    "K6_SCRIPT": ("service",),
    "JMETER_PLAN": ("service",),
    "LOCUSTFILE": ("service",),
    # Stage 5 — CI/CD & observability
    "DOCKERFILE": ("container",),
    "GRAFANA_DASHBOARD": ("service",),
    # Stage 6 — steps of the delivery run that are gated like artifacts
    "ZAP_SCAN": ("service",),               # DAST needs a running web target
    "AWS_SECRETS_CHECK": ("aws",),
}
# Gated items that are tool steps rather than saved artifacts (never listed as outputs).
EXTRA_GATED: dict[int, list[str]] = {5: ["AWS_SECRETS_CHECK"], 6: ["ZAP_SCAN"]}

# Tools that only make sense for an artifact type / gated step (skipped together with it).
TOOL_ARTIFACT: dict[str, str] = {
    "amazonq_generate_cloudcraft": "CLOUDCRAFT_JSON",
    "spectral_lint_openapi": "OPENAPI",
    "restassured_generate_tests": "REST_ASSURED",
    "playwright_generate_tests": "PLAYWRIGHT_SPEC",
    "playwright_run_tests": "PLAYWRIGHT_SPEC",
    "jmeter_generate_plan": "JMETER_PLAN",
    "locust_generate_test": "LOCUSTFILE",
    "k6_run_test": "K6_SCRIPT",
    "postman_run_collection": "POSTMAN_COLLECTION",
    "zap_baseline_scan": "ZAP_SCAN",
    "aws_secrets_check": "AWS_SECRETS_CHECK",
}

_REASON = {
    "ui": "no user interface in this project (API/service only)",
    "api": "no API surface in this project",
    "database": "no persistent data store in this project",
    "cloud": "no cloud target in this project",
    "aws": "the project does not target AWS",
    "service": "not a running service (no API or UI to exercise at runtime)",
    "container": "the project is not containerised (serverless, library or CLI)",
}

_KEYWORDS: dict[str, tuple[str, ...]] = {
    "ui": (r"ui", r"ux", r"front-?end", r"web ?app(?:lication)?", r"react", r"angular", r"vue", r"svelte",
           r"next\.?js", r"screens?", r"pages?", r"dashboard", r"portal", r"browser", r"spa", r"mobile app",
           r"ios", r"android", r"html", r"css", r"user interface", r"web ?site", r"form", r"playwright",
           r"selenium", r"cypress"),
    "api": (r"apis?", r"rest(?:ful)?", r"graphql", r"grpc", r"endpoints?", r"micro-?services?", r"openapi",
            r"swagger", r"web ?services?", r"backend", r"back-end", r"service"),
    "database": (r"databases?", r"db", r"sql", r"postgres(?:ql)?", r"mysql", r"mariadb", r"oracle", r"dynamo(?:db)?",
                 r"mongo(?:db)?", r"redis", r"persist\w*", r"orm", r"data ?store", r"data ?model", r"schema",
                 r"aurora", r"rds"),
    "cloud": (r"aws", r"azure", r"gcp", r"cloud", r"kubernetes", r"k8s", r"terraform", r"cdk", r"lambda",
              r"ecs", r"eks", r"fargate", r"serverless", r"cloudformation"),
    "aws": (r"aws", r"amazon", r"cdk", r"lambda", r"ecs", r"eks", r"fargate", r"dynamo(?:db)?", r"s3",
            r"cloudformation", r"bedrock", r"aurora", r"rds", r"sqs", r"sns"),
    "container": (r"docker\w*", r"containers?", r"containeri[sz]ed", r"kubernetes", r"k8s", r"ecs", r"eks",
                  r"fargate", r"helm", r"podman"),
}
# Explicit statements that a trait is ABSENT.
_NEGATIONS: dict[str, tuple[str, ...]] = {
    "ui": (r"no (?:ui|user interface|front-?end|screens?|gui)", r"(?:api|service|backend|back-end)[- ]only",
           r"headless", r"without (?:a )?(?:ui|front-?end|user interface)", r"no web ?ui",
           r"only (?:an? )?(?:rest )?apis?"),
    "api": (r"no apis?", r"without (?:an? )?apis?"),
    "database": (r"no (?:database|db|persistence|data ?store)", r"stateless", r"without (?:a )?(?:database|db)"),
    "cloud": (r"on-?prem(?:ise|ises)?", r"no cloud", r"without cloud", r"bare[- ]metal"),
    "aws": (r"no aws", r"not aws", r"without aws"),
    "container": (r"no (?:docker|containers?)", r"without (?:docker|containers?)", r"not containeri[sz]ed"),
}
# Other clouds / IaC that, with no AWS mention, mean the project does not target AWS.
_OTHER_CLOUD = (r"azure", r"gcp", r"google cloud", r"terraform", r"pulumi", r"on-?prem(?:ise|ises)?",
                r"bare[- ]metal")
# Delivery shapes that are not containerised services by default.
_NON_CONTAINER = (r"serverless", r"lambda", r"static site", r"library", r"sdk", r"cli", r"command[- ]line",
                  r"npm package", r"python package", r"batch job")
_NON_SERVICE = (r"library", r"sdk", r"cli", r"command[- ]line", r"batch job", r"npm package", r"python package",
                r"etl", r"script")


def _has(text: str, pats: tuple[str, ...]) -> bool:
    return any(re.search(rf"(?<![a-z0-9]){p}(?![a-z0-9])", text) for p in pats)


def strip_scope_block(text: str) -> str:
    """Drop the machine-generated scope/clarification blocks so inference reads only the
    user's own words (the scope block names artifact types like 'DBML', not project traits)."""
    return re.split(r"##\s*(?:Production scope|Clarifications)", text or "", maxsplit=1)[0]


def derive_traits(*, corpus: str) -> dict[str, bool | None]:
    """True = present, False = confidently absent, None = unknown (never excludes).

    ui is False when the text says API/service-only, or it describes an API service and
    never mentions any UI concept. aws is False when another cloud/IaC (or on-prem) is
    named and AWS is not. container is False for serverless/library/CLI shapes with no
    container mention. service (derived) is False for libraries/CLIs/batch jobs, or when
    both api and ui are absent."""
    text = (corpus or "").lower()
    traits: dict[str, bool | None] = {}
    for trait in ("ui", "api", "database", "cloud", "aws", "container"):
        if _has(text, _NEGATIONS[trait]):
            traits[trait] = False
        elif _has(text, _KEYWORDS[trait]):
            traits[trait] = True
        else:
            traits[trait] = None
    if traits["ui"] is None and traits["api"] is True:
        traits["ui"] = False           # an API service with no UI signal anywhere
    if traits["cloud"] is False:
        traits["aws"] = False
    if traits["aws"] is None and _has(text, _OTHER_CLOUD):
        traits["aws"] = False
    if traits["container"] is None and _has(text, _NON_CONTAINER):
        traits["container"] = False
    if traits["api"] or traits["ui"]:
        traits["service"] = True
    elif _has(text, _NON_SERVICE) or (traits["api"] is False and traits["ui"] is False):
        traits["service"] = False
    else:
        traits["service"] = None
    return traits


def inapplicable_types(traits: dict[str, bool | None], template: int) -> dict[str, str]:
    """Items of this stage (artifacts and gated tool steps) whose required trait is
    confidently absent → reason. Items with no requirement are never excluded."""
    out: dict[str, str] = {}
    for t in [*GENERATED_ARTIFACTS.get(template, []), *EXTRA_GATED.get(template, [])]:
        for need in REQUIRES_TRAIT.get(t, ()):
            if traits.get(need) is False:
                out[t] = _REASON[need]
                break
    return out


def project_corpus(*, project: dict[str, Any], user_text: str, upstream: list[str]) -> str:
    """Everything that tells us what kind of project this is."""
    return "\n".join([
        str(project.get("name") or ""), str(project.get("tech_stack") or ""),
        strip_scope_block(user_text), *upstream,
    ])


def constraints_block(excluded: dict[str, str]) -> str:
    """Prompt text that overrides the harness template's default artifact list."""
    if not excluded:
        return ""
    lines = [f"- {t}: {why}" for t, why in excluded.items()]
    return (
        "## Applicability constraints (validated against the project's configuration and the requester's intent)\n"
        "The default template lists artifacts that do NOT apply to this project. Do not produce, "
        "reference or design for any of these; where the schema still needs a field, return one "
        "minimal placeholder:\n" + "\n".join(lines)
    )


# --------------------------------------------------------------------------------------
# "LLM decides, code enforces": the model JUDGES the traits (with evidence); this code
# decides what each judgement means. Humans can override a trait and always win.
TRAIT_NAMES: tuple[str, ...] = ("ui", "api", "database", "cloud", "aws", "container", "service")
MIN_CONFIDENCE = 0.7   # a judgement below this is treated as unknown (never excludes)


def resolve_traits(
    llm: dict[str, Any] | None, overrides: dict[str, str], rules: dict[str, bool | None],
) -> dict[str, dict[str, Any]]:
    """Merge the three sources per trait. Precedence: human override > AI judgement >
    keyword rules. When the AI answered, its `unknown`/low-confidence calls stay unknown
    (the brittle keyword rules are only used when the AI is unavailable)."""
    out: dict[str, dict[str, Any]] = {}
    for name in TRAIT_NAMES:
        if name in overrides:
            out[name] = {"value": overrides[name] == "present", "source": "override",
                         "evidence": "Set by a project lead", "confidence": 1.0}
        elif llm is not None:
            j = llm.get(name) or {}
            conf = float(j.get("confidence") or 0.0)
            val = j.get("value", "unknown")
            known = val in ("present", "absent") and conf >= MIN_CONFIDENCE
            out[name] = {"value": (val == "present") if known else None, "source": "ai",
                         "evidence": j.get("evidence") or "not stated in the project inputs", "confidence": conf}
        else:
            out[name] = {"value": rules.get(name), "source": "rules", "confidence": 0.0,
                         "evidence": "keyword rules (AI classification unavailable)"}
    # Consistency (only fills unknowns): no cloud ⇒ not AWS; an API or UI ⇒ a running service.
    if out["aws"]["value"] is None and out["cloud"]["value"] is False:
        out["aws"].update(value=False, source="derived", evidence="no cloud target")
    if out["service"]["value"] is None and (out["api"]["value"] or out["ui"]["value"]):
        out["service"].update(value=True, source="derived", evidence="has an API or UI")
    return out


def trait_values(detail: dict[str, dict[str, Any]]) -> dict[str, bool | None]:
    return {k: v["value"] for k, v in detail.items()}


def traits_prompt(*, project: dict[str, Any], user_text: str, upstream: list[str]) -> tuple[str, str]:
    """The trait classifier's prompts (agents/pipeline/trait-classifier.md)."""
    from .prompt_library import render
    context = "\n".join(u[:300] for u in upstream[:30] if u.strip())[:4000]
    return render("traits.system"), render(
        "traits.user", project_name=project.get("name") or "(unnamed)", tech_stack=project.get("tech_stack") or "(not stated)",
        instructions=strip_scope_block(user_text)[:3000] or "(none)", context=context or "(none)")
