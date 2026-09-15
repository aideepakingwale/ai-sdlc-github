"""RAG layer: enterprise standards + approved artifacts are embedded into
kb_documents; every phase agent gets top-k retrieved snippets in its prompt, and
GET /api/kb/search is served from the same store.

The embedder is pluggable. Offline default: deterministic feature-hashing
(bag-of-words → signed hash buckets, L2-normalised) — no external calls, stable
across runs, adequate for keyword-heavy SDLC text. Swap in an API embedder
(Gemini/OpenAI) behind the same interface for semantic quality at scale.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

from ..config import Settings
from ..domain.models import ContextArtifact
from ..repos.pg import Database

_TOKEN_RE = re.compile(r"[a-z0-9]{2,}")


class Embedder(Protocol):
    dim: int

    def embed(self, text: str) -> list[float]: ...


class FeatureHashEmbedder:
    """Deterministic signed feature hashing; cosine-comparable, dependency-free."""

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in _TOKEN_RE.findall(text.lower()):
            digest = hashlib.sha256(token.encode()).digest()
            index = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[index] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        return [v / norm for v in vec] if norm > 0 else vec


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=False))


ENTERPRISE_STANDARDS: list[dict[str, str]] = [
    {
        "id": "kb-std-aws-allowlist",
        "title": "Approved AWS services",
        "content": "ECS Fargate, ALB, Aurora PostgreSQL, ElastiCache Redis, S3, EventBridge, SQS, "
        "CloudWatch, Secrets Manager, DynamoDB. EKS requires an architecture exception.",
    },
    {
        "id": "kb-std-nfr-latency",
        "title": "Standard NFR: latency",
        "content": "Interactive API endpoints must meet p99 < 500ms at nominal load; batch endpoints p99 < 5s.",
    },
    {
        "id": "kb-std-nfr-coverage",
        "title": "Standard NFR: test coverage",
        "content": "Unit test line coverage >= 80% enforced in CI; new services must ship k6 smoke profiles.",
    },
    {
        "id": "kb-std-sec-pipeline",
        "title": "Pipeline security stages",
        "content": "Every CI pipeline includes snyk-scan (SAST/SCA, fail on high) and inspector-scan "
        "(image CVEs) before deploy.",
    },
    {
        "id": "kb-std-api",
        "title": "API standards",
        "content": "OpenAPI 3.x contract-first; RFC7807 problem+json errors; idempotency keys on mutating "
        "endpoints; kebab-case paths, camelCase fields.",
    },
]


class RagService:
    def __init__(self, db: Database, settings: Settings, embedder: Embedder | None = None) -> None:
        self._db = db
        self._settings = settings
        self.embedder = embedder or FeatureHashEmbedder(settings.RAG_EMBED_DIM)

    async def ensure_standards(self) -> None:
        """Idempotent boot-time indexing of the enterprise KB."""
        for std in ENTERPRISE_STANDARDS:
            await self._db.upsert_kb_doc(
                doc_id=std["id"],
                scope="global",
                source="standard",
                title=std["title"],
                content=std["content"],
                embedding=self.embedder.embed(f"{std['title']}\n{std['content']}"),
            )

    async def index_artifact(self, project_id: str, artefact_id: str, artifact: ContextArtifact) -> None:
        """Approved-phase artifacts become project-scoped retrieval corpus."""
        body = f"{artifact.type} {artifact.title}\n{artifact.summary}\n{(artifact.content or '')[:4000]}"
        await self._db.upsert_kb_doc(
            doc_id=f"art-{artefact_id}",
            scope=project_id,
            source="artifact",
            title=f"[P{artifact.phase}] {artifact.type}: {artifact.title}",
            content=body[:6000],
            embedding=self.embedder.embed(body),
        )

    async def index_codebase_file(self, project_id: str, path: str, content: str) -> None:
        """Uploaded source files join the project retrieval corpus."""
        import hashlib as _hashlib

        doc_id = f"code-{project_id}-{_hashlib.sha256(path.encode()).hexdigest()[:16]}"
        body = f"Source file: {path}\n{content[:6000]}"
        await self._db.upsert_kb_doc(
            doc_id=doc_id, scope=project_id, source="codebase",
            title=f"[code] {path}", content=body,
            embedding=self.embedder.embed(body),
        )

    async def retrieve(self, query: str, project_id: str | None, top_k: int | None = None) -> list[dict]:
        """Top-k snippets across the enterprise KB + this project's artifacts."""
        k = top_k or self._settings.RAG_TOP_K
        scopes = ["global"] + ([project_id] if project_id else [])
        docs = await self._db.fetch_kb_docs(scopes)
        query_vec = self.embedder.embed(query)
        scored = sorted(
            (
                {
                    "id": d["id"],
                    "title": d["title"],
                    "source": d["source"],
                    "content": d["content"],
                    "score": round(cosine(query_vec, list(d["embedding"])), 4),
                }
                for d in docs
            ),
            key=lambda item: item["score"],
            reverse=True,
        )
        return [s for s in scored[:k] if s["score"] > 0.02]

    def render_block(self, snippets: list[dict]) -> str:
        if not snippets:
            return ""
        lines = ["## Retrieved knowledge (enterprise standards & approved artifacts)"]
        for s in snippets:
            lines.append(f"### {s['title']} (relevance {s['score']})\n{s['content']}")
        return "\n".join(lines)
