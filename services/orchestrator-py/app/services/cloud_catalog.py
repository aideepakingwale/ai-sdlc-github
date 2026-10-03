"""Cloud service catalog: ONE place that knows which icon belongs to which cloud.

The architecture specs the agents emit use service keys ("database", "queue", "cdn",
"aks", "lambda" …). This module resolves a key to a CANONICAL service (vendor-neutral:
relational_db, queue, cdn …) and then to the icon of the solution's TARGET cloud — so an
Azure solution gets Azure SQL / Service Bus / Front Door icons, never AWS ones.

Deterministic and zero-token. Icons are the official-style assets bundled with the
`diagrams` package (AWS / Azure / GCP / on-prem), embedded in the diagrams as data so they
render identically in diagrams.net, the in-app preview, and exports. If a canonical service
has no icon for the target cloud, the result is None → a neutral labelled box. Another
cloud's icon is NEVER substituted.
"""

from __future__ import annotations

import base64
import io
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

Provider = Literal["aws", "azure", "gcp", "onprem", "generic"]
PROVIDERS: tuple[str, ...] = ("aws", "azure", "gcp", "onprem")

# canonical service → architecture tier (drives colour, left→right order and the legend)
TIER: dict[str, str] = {}
_TIER_MEMBERS = {
    "actor": "user users client mobile external_system",
    "edge": "cdn dns waf api_gateway load_balancer firewall",
    "network": "network nat internet_gateway vpn",
    "compute": "vm kubernetes container_service serverless_container function app_service container batch",
    "data": "relational_db nosql_db cache object_storage file_storage data_warehouse search postgres mysql",
    "integration": "queue pubsub event_bus stream workflow",
    "security": "identity secrets kms",
    "ops": "monitoring logging ci_cd container_registry repo",
}
for _tier, _names in _TIER_MEMBERS.items():
    for _n in _names.split():
        TIER[_n] = _tier

TIER_STYLE: dict[str, tuple[str, str, str]] = {   # tier → (label, fill, stroke)
    "actor": ("Users & external", "#F5F5F5", "#9E9E9E"),
    "edge": ("Edge & access", "#E3F2FD", "#1E88E5"),
    "network": ("Network", "#E8EAF6", "#5C6BC0"),
    "compute": ("Compute", "#FFF3E0", "#FB8C00"),
    "data": ("Data", "#E8F5E9", "#43A047"),
    "integration": ("Integration & messaging", "#F3E5F5", "#8E24AA"),
    "security": ("Identity & security", "#FFEBEE", "#E53935"),
    "ops": ("Operations", "#ECEFF1", "#607D8B"),
}

# provider → (display name, boundary stroke, boundary fill, header colour)
PROVIDER_THEME: dict[str, tuple[str, str, str, str]] = {
    "aws": ("AWS Cloud", "#232F3E", "#FAFAFA", "#232F3E"),
    "azure": ("Microsoft Azure", "#0078D4", "#F3F9FD", "#0078D4"),
    "gcp": ("Google Cloud", "#4285F4", "#F6F9FE", "#4285F4"),
    "onprem": ("On-premises / platform", "#5B6478", "#FAFAFA", "#5B6478"),
    "generic": ("Solution boundary", "#5B6478", "#FAFAFA", "#5B6478"),
}

# (resource path under the `diagrams` resources dir, official service name)
Icon = tuple[str, str]

_AWS: dict[str, Icon] = {
    "user": ("aws/general/user.png", "Users"), "users": ("aws/general/users.png", "Users"),
    "client": ("aws/general/client.png", "Client"), "mobile": ("aws/general/mobile-client.png", "Mobile client"),
    "external_system": ("aws/general/office-building.png", "External system"),
    "cdn": ("aws/network/cloudfront.png", "Amazon CloudFront"), "dns": ("aws/network/route-53.png", "Amazon Route 53"),
    "waf": ("aws/security/waf.png", "AWS WAF"), "api_gateway": ("aws/network/api-gateway.png", "Amazon API Gateway"),
    "load_balancer": ("aws/network/elb-application-load-balancer.png", "Application Load Balancer"),
    "firewall": ("aws/network/network-firewall.png", "AWS Network Firewall"),
    "network": ("aws/network/vpc.png", "Amazon VPC"), "nat": ("aws/network/nat-gateway.png", "NAT Gateway"),
    "internet_gateway": ("aws/network/internet-gateway.png", "Internet Gateway"),
    "vpn": ("aws/network/site-to-site-vpn.png", "Site-to-Site VPN"),
    "vm": ("aws/compute/ec2.png", "Amazon EC2"), "kubernetes": ("aws/compute/elastic-kubernetes-service.png", "Amazon EKS"),
    "container_service": ("aws/compute/elastic-container-service.png", "Amazon ECS"),
    "serverless_container": ("aws/compute/fargate.png", "AWS Fargate"), "function": ("aws/compute/lambda.png", "AWS Lambda"),
    "app_service": ("aws/compute/elastic-beanstalk.png", "AWS Elastic Beanstalk"),
    "container": ("aws/compute/elastic-container-service-container.png", "Container"),
    "batch": ("aws/compute/batch.png", "AWS Batch"),
    "relational_db": ("aws/database/rds.png", "Amazon RDS"), "postgres": ("aws/database/rds-postgresql-instance.png", "Amazon RDS for PostgreSQL"),
    "mysql": ("aws/database/rds-mysql-instance.png", "Amazon RDS for MySQL"),
    "nosql_db": ("aws/database/dynamodb.png", "Amazon DynamoDB"), "cache": ("aws/database/elasticache.png", "Amazon ElastiCache"),
    "object_storage": ("aws/storage/simple-storage-service-s3.png", "Amazon S3"),
    "file_storage": ("aws/storage/elastic-file-system-efs.png", "Amazon EFS"),
    "data_warehouse": ("aws/database/redshift.png", "Amazon Redshift"),
    "search": ("aws/analytics/amazon-opensearch-service.png", "Amazon OpenSearch Service"),
    "queue": ("aws/integration/simple-queue-service-sqs.png", "Amazon SQS"),
    "pubsub": ("aws/integration/simple-notification-service-sns.png", "Amazon SNS"),
    "event_bus": ("aws/integration/eventbridge.png", "Amazon EventBridge"),
    "stream": ("aws/analytics/kinesis-data-streams.png", "Amazon Kinesis"),
    "workflow": ("aws/integration/step-functions.png", "AWS Step Functions"),
    "identity": ("aws/security/cognito.png", "Amazon Cognito"), "secrets": ("aws/security/secrets-manager.png", "AWS Secrets Manager"),
    "kms": ("aws/security/key-management-service.png", "AWS KMS"),
    "monitoring": ("aws/management/cloudwatch.png", "Amazon CloudWatch"),
    "logging": ("aws/management/cloudwatch-logs.png", "CloudWatch Logs"),
    "ci_cd": ("aws/devtools/codepipeline.png", "AWS CodePipeline"),
    "container_registry": ("aws/compute/ec2-container-registry.png", "Amazon ECR"),
    "repo": ("aws/devtools/codecommit.png", "AWS CodeCommit"),
}

_ACTORS: dict[str, Icon] = {   # neutral actors for providers without their own
    "user": ("onprem/client/user.png", "Users"), "users": ("onprem/client/users.png", "Users"),
    "client": ("onprem/client/client.png", "Client"), "mobile": ("onprem/client/client.png", "Mobile client"),
}

_AZURE: dict[str, Icon] = {
    **_ACTORS,
    "cdn": ("azure/network/front-doors.png", "Azure Front Door"), "dns": ("azure/network/dns-zones.png", "Azure DNS"),
    "waf": ("azure/network/application-gateway.png", "Application Gateway (WAF)"),
    "api_gateway": ("azure/integration/api-management-services.png", "Azure API Management"),
    "load_balancer": ("azure/network/application-gateway.png", "Application Gateway"),
    "firewall": ("azure/network/firewall.png", "Azure Firewall"),
    "network": ("azure/network/virtual-networks.png", "Virtual Network"),
    "vpn": ("azure/network/virtual-network-gateways.png", "VPN Gateway"),
    "vm": ("azure/compute/vm.png", "Virtual Machine"), "kubernetes": ("azure/compute/kubernetes-services.png", "Azure Kubernetes Service"),
    "container_service": ("azure/compute/container-instances.png", "Azure Container Instances"),
    "serverless_container": ("azure/compute/container-apps.png", "Azure Container Apps"),
    "function": ("azure/compute/function-apps.png", "Azure Functions"), "app_service": ("azure/compute/app-services.png", "Azure App Service"),
    "container": ("azure/compute/container-instances.png", "Container"), "batch": ("azure/compute/batch-accounts.png", "Azure Batch"),
    "relational_db": ("azure/database/sql-databases.png", "Azure SQL Database"),
    "postgres": ("azure/database/database-for-postgresql-servers.png", "Azure Database for PostgreSQL"),
    "mysql": ("azure/database/database-for-mysql-servers.png", "Azure Database for MySQL"),
    "nosql_db": ("azure/database/cosmos-db.png", "Azure Cosmos DB"), "cache": ("azure/database/cache-for-redis.png", "Azure Cache for Redis"),
    "object_storage": ("azure/storage/blob-storage.png", "Azure Blob Storage"),
    "file_storage": ("azure/storage/azure-fileshares.png", "Azure Files"),
    "data_warehouse": ("azure/database/synapse-analytics.png", "Azure Synapse Analytics"),
    "search": ("azure/web/cognitive-search.png", "Azure AI Search"),
    "queue": ("azure/integration/service-bus.png", "Azure Service Bus"),
    "pubsub": ("azure/integration/event-grid-topics.png", "Azure Event Grid"),
    "event_bus": ("azure/integration/event-grid-topics.png", "Azure Event Grid"),
    "stream": ("azure/analytics/event-hubs.png", "Azure Event Hubs"),
    "workflow": ("azure/integration/logic-apps.png", "Azure Logic Apps"),
    "identity": ("azure/identity/azure-active-directory.png", "Microsoft Entra ID"),
    "secrets": ("azure/security/key-vaults.png", "Azure Key Vault"), "kms": ("azure/security/key-vaults.png", "Azure Key Vault"),
    "monitoring": ("azure/monitor/monitor.png", "Azure Monitor"),
    "logging": ("azure/analytics/log-analytics-workspaces.png", "Log Analytics"),
    "ci_cd": ("azure/devops/pipelines.png", "Azure Pipelines"),
    "container_registry": ("azure/compute/container-registries.png", "Azure Container Registry"),
    "repo": ("azure/devops/repos.png", "Azure Repos"),
}

_GCP: dict[str, Icon] = {
    **_ACTORS,
    "cdn": ("gcp/network/cdn.png", "Cloud CDN"), "dns": ("gcp/network/dns.png", "Cloud DNS"),
    "waf": ("gcp/network/armor.png", "Cloud Armor"), "api_gateway": ("gcp/api/api-gateway.png", "API Gateway"),
    "load_balancer": ("gcp/network/load-balancing.png", "Cloud Load Balancing"),
    "firewall": ("gcp/network/firewall-rules.png", "Firewall rules"),
    "network": ("gcp/network/virtual-private-cloud.png", "VPC network"), "nat": ("gcp/network/nat.png", "Cloud NAT"),
    "vpn": ("gcp/network/vpn.png", "Cloud VPN"),
    "vm": ("gcp/compute/compute-engine.png", "Compute Engine"), "kubernetes": ("gcp/compute/kubernetes-engine.png", "Google Kubernetes Engine"),
    "container_service": ("gcp/compute/run.png", "Cloud Run"), "serverless_container": ("gcp/compute/run.png", "Cloud Run"),
    "function": ("gcp/compute/functions.png", "Cloud Functions"), "app_service": ("gcp/compute/app-engine.png", "App Engine"),
    "container": ("gcp/compute/run.png", "Container"),
    "relational_db": ("gcp/database/sql.png", "Cloud SQL"), "postgres": ("gcp/database/sql.png", "Cloud SQL for PostgreSQL"),
    "mysql": ("gcp/database/sql.png", "Cloud SQL for MySQL"),
    "nosql_db": ("gcp/database/firestore.png", "Firestore"), "cache": ("gcp/database/memorystore.png", "Memorystore"),
    "object_storage": ("gcp/storage/storage.png", "Cloud Storage"), "file_storage": ("gcp/storage/filestore.png", "Filestore"),
    "data_warehouse": ("gcp/analytics/bigquery.png", "BigQuery"),
    "queue": ("gcp/analytics/pubsub.png", "Pub/Sub"), "pubsub": ("gcp/analytics/pubsub.png", "Pub/Sub"),
    "event_bus": ("gcp/analytics/pubsub.png", "Pub/Sub"), "stream": ("gcp/analytics/pubsub.png", "Pub/Sub"),
    "workflow": ("gcp/analytics/composer.png", "Cloud Composer"),
    "identity": ("gcp/security/iam.png", "Cloud IAM"), "secrets": ("gcp/security/secret-manager.png", "Secret Manager"),
    "kms": ("gcp/security/key-management-service.png", "Cloud KMS"),
    "monitoring": ("gcp/operations/monitoring.png", "Cloud Monitoring"), "logging": ("gcp/operations/logging.png", "Cloud Logging"),
    "ci_cd": ("gcp/devtools/build.png", "Cloud Build"),
    "container_registry": ("gcp/devtools/container-registry.png", "Artifact/Container Registry"),
    "repo": ("gcp/devtools/source-repositories.png", "Cloud Source Repositories"),
}

# Self-hosted: only technologies the spec NAMES get a product icon (no invented product choices).
_ONPREM: dict[str, Icon] = {
    **_ACTORS,
    "postgres": ("onprem/database/postgresql.png", "PostgreSQL"), "mysql": ("onprem/database/mysql.png", "MySQL"),
    "relational_db": ("onprem/database/postgresql.png", "PostgreSQL"),
    "nosql_db": ("onprem/database/mongodb.png", "MongoDB"), "cache": ("onprem/inmemory/redis.png", "Redis"),
    "queue": ("onprem/queue/rabbitmq.png", "RabbitMQ"), "stream": ("onprem/queue/kafka.png", "Apache Kafka"),
    "container": ("onprem/container/docker.png", "Container"),
    "load_balancer": ("onprem/network/nginx.png", "NGINX"),
    "monitoring": ("onprem/monitoring/prometheus.png", "Prometheus"),
}

ICONS: dict[str, dict[str, Icon]] = {"aws": _AWS, "azure": _AZURE, "gcp": _GCP, "onprem": _ONPREM, "generic": {**_ACTORS}}

# --- aliases: the keys the agents/LLM actually write → canonical service -----------------------
_NEUTRAL_ALIASES: dict[str, str] = {
    "user": "user", "users": "users", "customer": "users", "customers": "users", "actor": "user", "person": "user",
    "client": "client", "browser": "client", "webclient": "client", "frontend": "client", "spa": "client",
    "mobile": "mobile", "mobileapp": "mobile", "externalsystem": "external_system", "external": "external_system",
    "thirdparty": "external_system", "partner": "external_system",
    "cdn": "cdn", "dns": "dns", "waf": "waf", "api": "api_gateway", "apigateway": "api_gateway", "gateway": "api_gateway",
    "apim": "api_gateway", "loadbalancer": "load_balancer", "lb": "load_balancer", "alb": "load_balancer",
    "nlb": "load_balancer", "elb": "load_balancer", "applicationgateway": "load_balancer", "firewall": "firewall",
    "vpc": "network", "vnet": "network", "network": "network", "virtualnetwork": "network", "nat": "nat",
    "natgateway": "nat", "igw": "internet_gateway", "internetgateway": "internet_gateway", "vpn": "vpn",
    "vm": "vm", "server": "vm", "ec2": "vm", "virtualmachine": "vm", "computeengine": "vm",
    "kubernetes": "kubernetes", "k8s": "kubernetes", "eks": "kubernetes", "aks": "kubernetes", "gke": "kubernetes",
    "ecs": "container_service", "containerservice": "container_service", "containerinstances": "container_service",
    "fargate": "serverless_container", "containerapps": "serverless_container", "cloudrun": "serverless_container",
    "serverlesscontainer": "serverless_container", "service": "serverless_container", "microservice": "serverless_container",
    "worker": "serverless_container", "container": "container", "docker": "container",
    "lambda": "function", "function": "function", "functions": "function", "functionapp": "function",
    "azurefunctions": "function", "cloudfunctions": "function", "serverless": "function",
    "appservice": "app_service", "webapp": "app_service", "appengine": "app_service", "beanstalk": "app_service",
    "batch": "batch",
    "database": "relational_db", "db": "relational_db", "sql": "relational_db", "rds": "relational_db",
    "aurora": "relational_db", "azuresql": "relational_db", "sqldatabase": "relational_db", "cloudsql": "relational_db",
    "spanner": "relational_db", "relationaldb": "relational_db",
    "postgres": "postgres", "postgresql": "postgres", "mysql": "mysql", "mariadb": "mysql",
    "dynamodb": "nosql_db", "cosmos": "nosql_db", "cosmosdb": "nosql_db", "nosql": "nosql_db", "mongodb": "nosql_db",
    "mongo": "nosql_db", "firestore": "nosql_db", "bigtable": "nosql_db", "documentdb": "nosql_db",
    "cache": "cache", "redis": "cache", "elasticache": "cache", "memcached": "cache", "memorystore": "cache",
    "azurecacheforredis": "cache",
    "storage": "object_storage", "s3": "object_storage", "bucket": "object_storage", "blob": "object_storage",
    "blobstorage": "object_storage", "storageaccount": "object_storage", "gcs": "object_storage",
    "objectstorage": "object_storage", "filestorage": "file_storage", "efs": "file_storage", "fileshare": "file_storage",
    "datawarehouse": "data_warehouse", "redshift": "data_warehouse", "bigquery": "data_warehouse",
    "synapse": "data_warehouse", "search": "search", "opensearch": "search", "elasticsearch": "search",
    "queue": "queue", "sqs": "queue", "servicebus": "queue", "mq": "queue", "rabbitmq": "queue",
    "topic": "pubsub", "pubsub": "pubsub", "sns": "pubsub", "eventgrid": "pubsub", "notification": "pubsub",
    "eventbridge": "event_bus", "eventbus": "event_bus", "stream": "stream", "kafka": "stream", "kinesis": "stream",
    "eventhub": "stream", "eventhubs": "stream", "msk": "stream", "workflow": "workflow", "stepfunctions": "workflow",
    "logicapps": "workflow", "composer": "workflow",
    "identity": "identity", "auth": "identity", "cognito": "identity", "entra": "identity", "entraid": "identity",
    "azuread": "identity", "aad": "identity", "iam": "identity", "idp": "identity", "sso": "identity",
    "secrets": "secrets", "secretsmanager": "secrets", "keyvault": "secrets", "secretmanager": "secrets",
    "vault": "secrets", "kms": "kms",
    "monitoring": "monitoring", "cloudwatch": "monitoring", "azuremonitor": "monitoring", "monitor": "monitoring",
    "observability": "monitoring", "prometheus": "monitoring", "logging": "logging", "logs": "logging",
    "loganalytics": "logging", "cicd": "ci_cd", "pipeline": "ci_cd", "containerregistry": "container_registry",
    "registry": "container_registry", "acr": "container_registry", "ecr": "container_registry", "repo": "repo",
}

# Provider-SPECIFIC keys: seeing one is strong evidence of the target cloud.
_AWS_KEYS = set("ec2 ecs eks fargate lambda rds aurora dynamodb elasticache s3 sqs sns eventbridge cloudfront route53 "
                "cognito secretsmanager cloudwatch kinesis msk redshift opensearch stepfunctions efs ecr nat natgateway "
                "igw internetgateway alb nlb elb beanstalk".split())
_AZURE_KEYS = set("aks appservice webapp functionapp azurefunctions containerapps cosmos cosmosdb azuresql sqldatabase "
                  "servicebus eventgrid eventhub eventhubs keyvault entra entraid azuread aad blob blobstorage "
                  "storageaccount synapse logicapps azuremonitor loganalytics acr apim vnet applicationgateway "
                  "azurecacheforredis".split())
_GCP_KEYS = set("gke cloudrun cloudfunctions cloudsql spanner bigtable firestore memorystore gcs bigquery pubsub "
                "composer appengine computeengine secretmanager".split())

_PROVIDER_TEXT: dict[str, re.Pattern[str]] = {
    "azure": re.compile(r"\b(azure|microsoft|aks|entra|cosmos ?db|service ?bus|app ?service|key ?vault|front ?door|"
                        r"bicep|arm template|\.net on azure)\b", re.I),
    "aws": re.compile(r"\b(aws|amazon|ec2|fargate|lambda|dynamodb|s3|cloudfront|cdk|cloudformation|eks|rds|sqs|sns)\b", re.I),
    "gcp": re.compile(r"\b(gcp|google cloud|gke|bigquery|cloud ?run|cloud ?sql|firestore|pub/?sub|anthos)\b", re.I),
    "onprem": re.compile(r"\b(on-?prem(?:ise|ises)?|bare[- ]metal|vmware|data ?cent(?:er|re)|self-hosted)\b", re.I),
}


def norm(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (key or "").lower())


def canonical(service_key: str) -> str | None:
    """Vendor-neutral service id for any service key the agents write (None if unknown)."""
    k = norm(service_key)
    if k in _NEUTRAL_ALIASES:
        return _NEUTRAL_ALIASES[k]
    # tolerate "azure_sql_database", "aws-lambda" → strip a leading vendor word
    stripped = re.sub(r"^(aws|amazon|azure|microsoft|gcp|google)", "", k)
    return _NEUTRAL_ALIASES.get(stripped)


def key_provider(service_key: str) -> str | None:
    k = norm(service_key)
    if k in _AWS_KEYS or k.startswith(("aws", "amazon")):
        return "aws"
    if k in _AZURE_KEYS or k.startswith(("azure", "microsoft")):
        return "azure"
    if k in _GCP_KEYS or k.startswith(("gcp", "google")):
        return "gcp"
    return None


@dataclass(frozen=True)
class ProviderChoice:
    provider: str
    reason: str


def infer_provider(
    declared: str | None, texts: list[str], service_keys: list[str],
) -> ProviderChoice:
    """Which cloud is this solution for? Explicit declaration wins; then the vendor-specific
    service keys the spec uses; then vendor words in the project's own text (tech stack,
    requirements, titles). Ties/none → "generic" (neutral boxes — never a guessed cloud)."""
    d = (declared or "").strip().lower()
    if d in PROVIDERS:
        return ProviderChoice(d, "declared by the architecture spec")
    votes = {p: 0 for p in PROVIDERS}
    for key in service_keys:
        p = key_provider(key)
        if p:
            votes[p] += 3
    blob = "\n".join(t for t in texts if t)
    for p, rx in _PROVIDER_TEXT.items():
        votes[p] += min(len(rx.findall(blob)), 5)
    best = max(votes.values())
    if best <= 0:
        return ProviderChoice("generic", "no cloud indicated")
    winners = [p for p, v in votes.items() if v == best]
    if len(winners) > 1:
        return ProviderChoice("generic", f"ambiguous between {', '.join(sorted(winners))}")
    return ProviderChoice(winners[0], f"{votes[winners[0]]} signal(s) from service keys / project text")


@dataclass(frozen=True)
class Resolved:
    canonical: str | None
    tier: str
    icon_path: str | None
    service_name: str | None


def resolve(provider: str, service_key: str) -> Resolved:
    """Icon + official name for `service_key` on `provider`; None icon ⇒ neutral box."""
    can = canonical(service_key)
    if can is None:
        return Resolved(None, "compute", None, None)
    icon = ICONS.get(provider, {}).get(can)
    return Resolved(can, TIER.get(can, "compute"), icon[0] if icon else None, icon[1] if icon else None)


# ---------------------------------------------------------------- icon files
def resources_dir() -> Path:
    import diagrams  # the package ships the icon assets next to itself
    return Path(diagrams.__file__).resolve().parent.parent / "resources"


@lru_cache(maxsize=512)
def icon_data_uri(rel_path: str, size: int = 96) -> str | None:
    """A compact PNG data URI (draw.io's `image=data:image/png,<base64>` form), or None."""
    path = resources_dir() / rel_path
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    try:
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as im:
            im = im.convert("RGBA")
            im.thumbnail((size, size))
            buf = io.BytesIO()
            im.save(buf, format="PNG", optimize=True)
            raw = buf.getvalue()
    except Exception:  # noqa: BLE001 — original asset is still valid
        pass
    return "data:image/png," + base64.b64encode(raw).decode("ascii")


def icon_file(rel_path: str) -> str | None:
    p = resources_dir() / rel_path
    return str(p) if p.exists() else None
