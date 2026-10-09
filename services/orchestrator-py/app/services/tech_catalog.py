"""Configurable technology catalog for project creation.

The New Project form builds a *structured* stack — programming language →
version → framework(s) — from this catalog instead of a hardcoded list.

Ships a sensible default; override it WITHOUT a rebuild by pointing
``TECH_CATALOG_PATH`` at a JSON file of the same shape. The file is
hot-reloaded when its mtime changes (same live-edit spirit as the prompt/skill
packs), so an admin can add languages, versions or frameworks on the fly.

Shape::

    {"languages": [
       {"name": "Python", "versions": ["3.12", "3.11"],
        "frameworks": ["FastAPI", "Django"]},
       ...
    ]}
"""
from __future__ import annotations

import json
import os
from typing import Any

from ..config import get_settings

DEFAULT_TECH_CATALOG: dict[str, Any] = {
    "languages": [
        {"name": "Python", "versions": ["3.12", "3.11", "3.10"],
         "frameworks": ["FastAPI", "Django", "Flask"]},
        {"name": "TypeScript", "versions": ["5.x"],
         "frameworks": ["Node.js / Express", "NestJS", "React", "Next.js"]},
        {"name": "JavaScript", "versions": ["ES2023"],
         "frameworks": ["Node.js / Express", "React", "Vue"]},
        {"name": "Java", "versions": ["21 (LTS)", "17 (LTS)"],
         "frameworks": ["Spring Boot", "Quarkus", "Micronaut"]},
        {"name": "C#", "versions": [".NET 8", ".NET 6"],
         "frameworks": ["ASP.NET Core", "Minimal API"]},
        {"name": "Go", "versions": ["1.22", "1.21"],
         "frameworks": ["Gin", "Echo", "Fiber"]},
        {"name": "Rust", "versions": ["1.79"],
         "frameworks": ["Axum", "Actix Web"]},
    ],
}

# The layers a project's stack is described in. `options` feed the screen's suggestions; `aliases` are extra spellings the deterministic
# extractor recognises. Override the whole catalog (languages and layers) with TECH_CATALOG_PATH.
DEFAULT_LAYERS: list[dict[str, Any]] = [
    {"id": "frontend", "label": "Frontend", "hint": "What users see: web, mobile or desktop UI",
     "options": ["React", "Next.js", "Angular", "Vue", "Svelte", "Flutter", "React Native", "Thymeleaf"],
     "aliases": ["reactjs", "react.js", "nextjs", "angularjs", "vue.js", "vuejs"]},
    {"id": "backend", "label": "Backend / API", "hint": "Language, runtime and framework of the services",
     "options": [], "aliases": []},
    {"id": "database", "label": "Database", "hint": "Primary system of record",
     "options": ["PostgreSQL", "MySQL", "Oracle", "SQL Server", "MongoDB", "DynamoDB", "Aurora", "Cassandra", "Cosmos DB"],
     "aliases": ["postgres", "mssql", "sql server", "mariadb"]},
    {"id": "cache", "label": "Cache", "hint": "In-memory or distributed cache",
     "options": ["Redis", "Memcached", "ElastiCache", "Hazelcast"], "aliases": []},
    {"id": "messaging", "label": "Messaging and events", "hint": "Queues, topics, streaming",
     "options": ["Kafka", "RabbitMQ", "SQS", "SNS", "EventBridge", "Azure Service Bus", "ActiveMQ", "IBM MQ"],
     "aliases": ["amazon sqs", "amazon sns", "service bus"]},
    {"id": "search", "label": "Search", "hint": "Full-text and analytics search",
     "options": ["OpenSearch", "Elasticsearch", "Solr"], "aliases": []},
    {"id": "hosting", "label": "Hosting", "hint": "Where it runs: cloud, on-premises or hybrid",
     "options": ["AWS", "Azure", "GCP", "On-premises", "Hybrid", "OpenShift"],
     "aliases": ["amazon web services", "google cloud", "on-prem", "on prem", "onprem", "private cloud", "data centre", "data center"]},
    {"id": "compute", "label": "Compute", "hint": "How the code is run",
     "options": ["Serverless (Lambda)", "Containers (ECS)", "Kubernetes (EKS)", "Virtual machines", "Bare metal"],
     "aliases": ["lambda", "serverless", "kubernetes", "eks", "ecs", "fargate", "docker swarm"]},
    {"id": "iac", "label": "Infrastructure as code", "hint": "How infrastructure is defined",
     "options": ["Terraform", "AWS CDK", "CloudFormation", "Pulumi", "Ansible", "Bicep"], "aliases": ["cdk"]},
    {"id": "cicd", "label": "CI/CD", "hint": "Build and deployment pipeline",
     "options": ["GitHub Actions", "GitLab CI", "Jenkins", "Azure DevOps", "CircleCI", "ArgoCD"], "aliases": ["github action"]},
    {"id": "observability", "label": "Observability", "hint": "Logs, metrics, traces, alerting",
     "options": ["CloudWatch", "Datadog", "Prometheus and Grafana", "ELK", "OpenTelemetry", "Splunk"], "aliases": ["prometheus", "grafana"]},
    {"id": "identity", "label": "Identity and access", "hint": "Authentication and single sign-on",
     "options": ["Keycloak", "Okta", "Auth0", "Cognito", "Entra ID", "OIDC", "SAML"], "aliases": ["azure ad", "active directory", "oauth"]},
]

_cache: dict[str, Any] = {}


def get_layers() -> list[dict[str, Any]]:
    """The stack layers: from the override catalog when it defines `layers`, else the built-in list."""
    data = get_tech_catalog()
    layers = data.get("layers")
    if isinstance(layers, list) and layers and all(isinstance(x, dict) and x.get("id") for x in layers):
        return layers
    return DEFAULT_LAYERS


def layer_ids() -> list[str]:
    return [str(x["id"]) for x in get_layers()]


def layer_label(layer_id: str) -> str:
    return next((str(x.get("label") or layer_id) for x in get_layers() if x["id"] == layer_id), layer_id)


def get_tech_catalog() -> dict[str, Any]:
    """Return the tech catalog — the default, or a hot-reloaded override file
    when ``TECH_CATALOG_PATH`` is set and valid. Never raises: an unreadable or
    malformed override falls back to the default so project creation never breaks."""
    path = get_settings().TECH_CATALOG_PATH
    if not path:
        return DEFAULT_TECH_CATALOG
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return DEFAULT_TECH_CATALOG
    if _cache.get("path") == path and _cache.get("mtime") == mtime:
        return _cache["data"]
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict) or not isinstance(data.get("languages"), list):
            raise ValueError("catalog must be an object with a 'languages' array")
        _cache.update(path=path, mtime=mtime, data=data)
        return data
    except (OSError, ValueError, json.JSONDecodeError):
        return DEFAULT_TECH_CATALOG


def compose_stack(
    language: str | None,
    version: str | None,
    frameworks: list[str] | None,
    fallback: str = "",
) -> str:
    """Compose the free-text ``tech_stack`` string the agents' prompts consume
    from the structured selection, e.g. ``"Python 3.12 + FastAPI, Pytest"``.
    Falls back to the legacy single-string value when no language is given."""
    language = (language or "").strip()
    if not language:
        return (fallback or "").strip()
    version = (version or "").strip()
    fw = [f.strip() for f in (frameworks or []) if f and f.strip()]
    head = f"{language} {version}".strip()
    return f"{head} + {', '.join(fw)}" if fw else head
