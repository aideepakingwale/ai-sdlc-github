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

# Which project trait an artifact type needs (absent trait → not applicable).
REQUIRES_TRAIT: dict[str, str] = {
    "PLAYWRIGHT_SPEC": "ui",
    "REST_ASSURED": "api",
    "OPENAPI": "api",
    "POSTMAN_COLLECTION": "api",
    "DBML": "database",
    "CDK": "cloud",
}

# Tools that only make sense for an artifact type (skipped together with it).
TOOL_ARTIFACT: dict[str, str] = {
    "playwright_generate_tests": "PLAYWRIGHT_SPEC",
    "playwright_run_tests": "PLAYWRIGHT_SPEC",
    "restassured_generate_tests": "REST_ASSURED",
    "spectral_lint_openapi": "OPENAPI",
    "postman_run_collection": "POSTMAN_COLLECTION",
}

_REASON = {
    "ui": "no user interface in this project (API/service only) — nothing for UI automation or screens",
    "api": "no API surface in this project",
    "database": "no persistent data store in this project",
    "cloud": "no cloud/IaC target in this project",
}

_KEYWORDS: dict[str, tuple[str, ...]] = {
    "ui": (r"ui", r"ux", r"front-?end", r"web ?app(?:lication)?", r"react", r"angular", r"vue", r"svelte",
           r"next\.?js", r"screens?", r"pages?", r"dashboard", r"portal", r"browser", r"spa", r"mobile app",
           r"ios", r"android", r"html", r"css", r"user interface", r"web ?site", r"form"),
    "api": (r"apis?", r"rest(?:ful)?", r"graphql", r"grpc", r"endpoints?", r"micro-?services?", r"openapi",
            r"swagger", r"web ?services?", r"backend", r"back-end", r"service"),
    "database": (r"databases?", r"db", r"sql", r"postgres(?:ql)?", r"mysql", r"mariadb", r"oracle", r"dynamo(?:db)?",
                 r"mongo(?:db)?", r"redis", r"persist\w*", r"orm", r"data ?store", r"data ?model", r"schema",
                 r"aurora", r"rds"),
    "cloud": (r"aws", r"azure", r"gcp", r"cloud", r"kubernetes", r"k8s", r"terraform", r"cdk", r"lambda",
              r"ecs", r"eks", r"fargate", r"serverless", r"cloudformation"),
}
# Explicit statements that a trait is ABSENT.
_NEGATIONS: dict[str, tuple[str, ...]] = {
    "ui": (r"no (?:ui|user interface|front-?end|screens?|gui)", r"(?:api|service|backend|back-end)[- ]only",
           r"headless", r"without (?:a )?(?:ui|front-?end|user interface)", r"no web ?ui",
           r"only (?:an? )?(?:rest )?apis?"),
    "api": (r"no apis?", r"without (?:an? )?apis?"),
    "database": (r"no (?:database|db|persistence|data ?store)", r"stateless", r"without (?:a )?(?:database|db)"),
    "cloud": (r"on-?prem(?:ise|ises)?", r"no cloud", r"without cloud", r"bare[- ]metal"),
}


def _has(text: str, pats: tuple[str, ...]) -> bool:
    return any(re.search(rf"(?<![a-z0-9]){p}(?![a-z0-9])", text) for p in pats)


def strip_scope_block(text: str) -> str:
    """Drop the machine-generated scope/clarification blocks so inference reads only the
    user's own words (the scope block names artifact types like 'DBML', not project traits)."""
    return re.split(r"##\s*(?:Production scope|Clarifications)", text or "", maxsplit=1)[0]


def derive_traits(*, corpus: str) -> dict[str, bool | None]:
    """True = present, False = confidently absent, None = unknown (never excludes).

    UI is False when the text says API/service-only, or describes an API service and
    never mentions any UI concept (the typical headless-service project)."""
    text = (corpus or "").lower()
    traits: dict[str, bool | None] = {}
    for trait in ("ui", "api", "database", "cloud"):
        if _has(text, _NEGATIONS[trait]):
            traits[trait] = False
        elif _has(text, _KEYWORDS[trait]):
            traits[trait] = True
        else:
            traits[trait] = None
    if traits["ui"] is None and traits["api"] is True:
        traits["ui"] = False           # an API service with no UI signal anywhere
    return traits


def inapplicable_types(traits: dict[str, bool | None], template: int) -> dict[str, str]:
    """Artifact types of this stage whose required trait is confidently absent → reason."""
    out: dict[str, str] = {}
    for t in GENERATED_ARTIFACTS.get(template, []):
        need = REQUIRES_TRAIT.get(t)
        if need and traits.get(need) is False:
            out[t] = _REASON[need]
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
