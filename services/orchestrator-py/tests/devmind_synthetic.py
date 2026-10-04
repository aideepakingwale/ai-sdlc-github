"""Synthetic multi-sprint project used to prove the index stays bounded and consistent."""

from __future__ import annotations

from app.devmind_index.schema import Charter, Decision, ReleaseIndex, SprintDigest, Story

COMPONENTS = ["auth", "orders", "payments", "catalog", "search", "notifications", "billing",
              "reporting", "gateway", "inventory", "shipping", "profile"]
NOUNS = ["checkout", "refund", "coupon", "invoice", "wishlist", "tracking", "export", "audit", "webhook", "cache"]


class MemStore:
    """In-memory IndexStore (same surface as the filesystem/S3 content stores)."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.fail_on: str | None = None

    async def put(self, key: str, body: str) -> None:
        if self.fail_on and key.endswith(self.fail_on):
            raise RuntimeError("simulated crash")
        self.data[key] = body

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def delete(self, key: str) -> None:
        self.data.pop(key, None)

    async def list_prefix(self, prefix: str) -> list[str]:
        return sorted(k for k in self.data if k.startswith(prefix.rstrip("/") + "/"))


def charter() -> Charter:
    return Charter(
        name="Shop", vision="An online shop for small merchants.", techStack="Java 21 / Spring Boot / PostgreSQL",
        constraints=["PCI-DSS scope minimised", "99.9% availability"], definitionOfDone=["tests pass", "docs updated", "PO accepted"],
        keyDecisions=["Event-driven integration", "PostgreSQL as system of record"],
    )


def release_id(n: int) -> str:  # sprint n -> release (4 sprints each)
    return f"R-{2026 + (n - 1) // 48:04d}-{((n - 1) // 4) % 12 + 1:02d}-{(n - 1) // 4 + 1:02d}"


def sprint(n: int) -> SprintDigest:
    comps = [COMPONENTS[(n + i) % len(COMPONENTS)] for i in range(3)]
    stories = [
        Story(id=f"ST-{n:03d}-{i}", title=f"{NOUNS[(n + i) % len(NOUNS)]} handling for {comps[i % 3]} flow {n}",
              status="done" if i < 7 else "carried", jiraKey=f"SHOP-{n * 10 + i}", components=[comps[i % 3]],
              artifacts=[f"art-{n}-{i}"])
        for i in range(8)
    ]
    return SprintDigest(
        id=f"S-{n:03d}", release=release_id(n), goal=f"Deliver {NOUNS[n % len(NOUNS)]} improvements", stories=stories,
        decisions=[Decision(id=f"D-{n:03d}-{j}", title=f"Decision {n}.{j} on {comps[j]}", rationale="Because of load and cost. " * 6,
                            components=[comps[j]]) for j in range(2)],
        deltas=[f".devmind/sprints/S-{n:03d}/delta-hld.md"], openIssues=[f"issue {n}"],
        summary=("Sprint summary with context about the work delivered and risks noted. " * 8)[:590],
    )


def release(rid: str, sprints: list[str], closed: bool) -> ReleaseIndex:
    return ReleaseIndex(
        id=rid, name=f"Release {rid}", goal="Ship the next increment of the shop", sprints=sprints, closed=closed,
        specPointers={c: f"specs/{c}.md@{rid}" for c in COMPONENTS}, summary="Release summary. " * 20,
        decisions=[Decision(id=f"RD-{rid}", title="Release scope fixed", rationale="Capacity")],
        openItems=["tech debt: cache invalidation"],
    )


async def build_project(builder, n_sprints: int):
    """Close sprints 1..n in order, closing a release every 4 sprints."""
    await builder.update(charter=charter())
    for n in range(1, n_sprints + 1):
        d = sprint(n)
        rid = d.release
        group = [x for x in range(1, n + 1) if release_id(x) == rid]
        closed = n % 4 == 0
        await builder.update(sprint=d, release=release(rid, [f"S-{x:03d}" for x in group], closed),
                             current_release=rid, current_sprint=d.id)
