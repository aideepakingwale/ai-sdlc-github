"""Typed model of the `.devmind/` index. Facts are structured fields and IDs; prose is short and
length-capped so summaries cannot grow without bound."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1
ROOT = ".devmind"


class Tier(str, Enum):
    ACTIVE = "active"      # current release and the one before it
    CLOSED = "closed"      # older releases: files kept, loaded only by pointer
    ARCHIVED = "archived"  # rolled into one file per release; per-sprint files leave the tree


class Story(BaseModel):
    id: str
    title: str = Field(max_length=200)
    status: Literal["done", "carried", "dropped"] = "done"
    jiraKey: str | None = None
    components: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)  # artifact ids / repo paths (pointers, never bodies)


class Decision(BaseModel):
    id: str
    title: str = Field(max_length=160)
    rationale: str = Field(default="", max_length=300)
    components: list[str] = Field(default_factory=list)
    artifact: str | None = None


class SprintDigest(BaseModel):
    id: str                       # S-014
    release: str                  # R-2026-03
    goal: str = Field(default="", max_length=240)
    stories: list[Story] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    deltas: list[str] = Field(default_factory=list)       # paths of design-delta files
    openIssues: list[str] = Field(default_factory=list)
    summary: str = Field(default="", max_length=600)


class ReleaseIndex(BaseModel):
    id: str                       # R-2026-03
    name: str = Field(max_length=120)
    forkedFrom: str | None = None                      # provenance only: nothing is merged back
    forkBaseline: dict[str, str] = Field(default_factory=dict)  # {"release": "R-001", "sprint": "S-004"}
    goal: str = Field(default="", max_length=300)
    sprints: list[str] = Field(default_factory=list)
    closed: bool = False
    decisions: list[Decision] = Field(default_factory=list)
    specPointers: dict[str, str] = Field(default_factory=dict)  # component -> current spec ref
    openItems: list[str] = Field(default_factory=list)
    summary: str = Field(default="", max_length=800)


CarryKind = Literal["spec-section", "requirement", "decision", "learning"]
CarryState = Literal["carried", "modified", "new", "retired"]


class CarriedFrom(BaseModel):
    release: str
    path: str                     # where it lived in the source release
    ref: str = ""                 # section name / item id / decision id
    hash: str = ""                # content hash at fork time (the "merge base")


class CarriedItem(BaseModel):
    id: str                       # stable id inside this release, e.g. spec:orders-api/Endpoints
    kind: CarryKind
    title: str = Field(max_length=200)
    text: str = Field(default="", max_length=8000)   # the carried definition (spec sections live in the spec file)
    state: CarryState = "carried"
    carriedFrom: CarriedFrom | None = None
    hash: str = ""                # hash of the CURRENT definition in this release
    component: str | None = None  # spec sections: which spec file
    reason: str = Field(default="", max_length=300)  # why it was chosen (AI suggestion / person)


class ForkRecord(BaseModel):
    """`fork.json`: the record of how a release was started. Provenance + the carry set."""
    schema_: int = Field(default=SCHEMA_VERSION, alias="schema")
    release: str
    forkedFrom: str | None = None
    baseline: dict[str, str] = Field(default_factory=dict)
    stagePreset: str = "inherit"
    stageKeys: list[str] = Field(default_factory=list)
    intakeRule: str = "pool"
    usePool: bool = True
    carried: list[CarriedItem] = Field(default_factory=list)
    model_config = {"populate_by_name": True}


class Charter(BaseModel):
    name: str
    vision: str = Field(default="", max_length=600)
    constraints: list[str] = Field(default_factory=list)
    techStack: str = ""
    definitionOfDone: list[str] = Field(default_factory=list)
    keyDecisions: list[str] = Field(default_factory=list)


class FileEntry(BaseModel):
    path: str
    kind: str                     # charter | release | sprint | archive | lookup | readme
    tier: Tier = Tier.ACTIVE
    sha256: str
    bytes: int
    release: str | None = None
    sprint: str | None = None
    stale: bool = False           # validity flag, independent of tier
    sourceCommit: str | None = None


class Manifest(BaseModel):
    schemaVersion: int = SCHEMA_VERSION
    projectId: str
    currentRelease: str | None = None
    currentSprint: str | None = None
    files: dict[str, FileEntry] = Field(default_factory=dict)
    # publish state: commit written to the repo + the hash of each file at that commit
    publishedCommit: str | None = None
    publishedFiles: dict[str, str] = Field(default_factory=dict)
    generation: int = 0           # bumps on every staged write


def charter_path() -> str:
    return f"{ROOT}/charter.json"


def release_path(release_id: str) -> str:
    return f"{ROOT}/releases/{release_id}/index.json"


def release_spec_path(release_id: str, component: str) -> str:
    return f"{ROOT}/releases/{release_id}/specs/{component}.md"


def fork_path(release_id: str) -> str:
    return f"{ROOT}/releases/{release_id}/fork.json"


def carried_path(release_id: str) -> str:
    return f"{ROOT}/releases/{release_id}/carried.md"


def sprint_path(sprint_id: str) -> str:
    return f"{ROOT}/sprints/{sprint_id}/digest.json"


def archive_path(release_id: str) -> str:
    return f"{ROOT}/archive/{release_id}.json"


LOOKUP_PATH = f"{ROOT}/lookup.json"
MANIFEST_PATH = f"{ROOT}/manifest.json"
README_PATH = f"{ROOT}/README.md"
