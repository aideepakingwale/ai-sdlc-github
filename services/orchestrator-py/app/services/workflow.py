"""Workflow engine selector ( fork).

Two engines coexist in-tree:
  - ``workflow_v1`` — the original dynamic workflow engine, preserved
    intact as a rollback copy.
  - ``workflow_v2`` — the fork that adds the data-driven CUSTOM phase type
    (template 7): a PM can define any SDLC phase type by config (persona,
    prompt, tool sequence) without code, while the six built-in engines stay.

The rest of the app imports the workflow API from THIS module, so switching
engines is a single config change (`WORKFLOW_ENGINE`) and v1 stays available.
v2 is a backward-compatible superset — the six built-in templates behave
identically — so it is the default.
"""

from __future__ import annotations

from ..config import get_settings
from . import workflow_v1, workflow_v2

_ENGINES = {"v1": workflow_v1, "v2": workflow_v2}
_ACTIVE = _ENGINES.get(getattr(get_settings(), "WORKFLOW_ENGINE", "v2"), workflow_v2)
ACTIVE_ENGINE = "v1" if _ACTIVE is workflow_v1 else "v2"

# Re-export the active engine's public API — existing imports are unchanged.
WorkflowConfig = _ACTIVE.WorkflowConfig
StageConfig = _ACTIVE.StageConfig
WorkflowService = _ACTIVE.WorkflowService
TEMPLATES = _ACTIVE.TEMPLATES
MAX_STAGES = _ACTIVE.MAX_STAGES
ENTRY_INPUT = _ACTIVE.ENTRY_INPUT
default_workflow = _ACTIVE.default_workflow
validate_workflow = _ACTIVE.validate_workflow
derive = _ACTIVE.derive
# Custom phase template id (v2 only; None under v1).
CUSTOM_TEMPLATE = getattr(_ACTIVE, "CUSTOM_TEMPLATE", None)

__all__ = [
    "WorkflowConfig", "StageConfig", "WorkflowService", "TEMPLATES", "MAX_STAGES",
    "ENTRY_INPUT", "default_workflow", "validate_workflow", "derive",
    "CUSTOM_TEMPLATE", "ACTIVE_ENGINE",
]
