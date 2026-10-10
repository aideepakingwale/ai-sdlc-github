"""Running a stage's attached custom agents.

After a stage's own agents have written their artefacts, the agents attached to that stage (and pinned to an approved version) run. Each
reads inputs from the stage (the brief, an upstream artefact, the project rules or stack), may delegate, and writes one artefact per
declared output, with a "How this was made" record. One agent failing never stops the others or the stage: it is reported, and the
person reviewing the stage sees it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..domain.errors import SdlcError
from . import safe_expr
from .agent_runtime import AgentRuntime, render_output, resolve_inputs

log = logging.getLogger("agent_stage")

Save = Callable[..., Awaitable[Any]]
Emit = Callable[[dict[str, Any]], None]


@dataclass
class StageAgentsResult:
    artifacts: list[Any] = field(default_factory=list)
    ran: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    def summary(self) -> str:
        bits = []
        if self.ran:
            bits.append(f"custom agent(s) ran: {', '.join(self.ran)}")
        if self.failed:
            bits.append(f"did not complete: {'; '.join(self.failed)}")
        return ("Custom agents - " + "; ".join(bits) + ".") if bits else ""


def _resolver(resolved: dict[str, Any]):
    async def resolve(agent_id: str, version: int | None) -> dict[str, Any] | None:
        return resolved.get(agent_id)
    return resolve


async def run_stage_agents(
    *, runtime: AgentRuntime, items: list[dict[str, Any]], brief: str, upstream: Callable[[str], "str | None"], rules: str, stack: str,
    project_context: str, save: Save, emit: Emit, audit: Any = None, project_id: str = "", phase: int = 0, only: str | None = None,
    extra_inputs: dict[str, Any] | None = None,
) -> StageAgentsResult:
    out = StageAgentsResult()
    made: dict[str, str] = {}        # what agents earlier in this stage wrote, by artefact type: a later agent can read it as `upstream:<TYPE>`

    def read(t: str) -> "str | None":
        return made.get(t.upper()) or upstream(t)

    for it in items:
        if only and it["def_id"] != only:
            continue
        name, body = it["name"], it["body"]
        if it["runs"] == "on_request" and not only:
            continue
        vals, missing = await resolve_inputs(body, brief=brief, upstream=read, rules=rules, stack=stack)
        for k, v in (extra_inputs or {}).items():
            if k in {i["name"] for i in body.get("inputs", [])}:
                vals[k] = v
                if k in missing:
                    missing.remove(k)
        if missing:
            msg = f"{name} was not run: it needs {', '.join(missing)}, which this stage does not have yet"
            out.skipped.append(msg)
            emit({"type": "node", "node": "agent", "label": f"⏭ {msg}"})
            continue
        if it["runs"] == "when" and not only:
            try:
                if not safe_expr.truthy(it["condition"], {**vals, "brief": brief}):
                    out.skipped.append(f"{name} was not run: its condition was not met")
                    emit({"type": "node", "node": "agent", "label": f"⏭ {name}: its condition was not met"})
                    continue
            except safe_expr.ExprError as err:
                out.failed.append(f"{name}: its condition could not be read ({err})")
                continue
        emit({"type": "node", "node": "agent", "label": f"Custom agent '{name}' (v{it['version']}) working"})
        try:
            res = await runtime.run(agent_id=it["def_id"], name=name, body=body, inputs=vals, project_context=project_context,
                                    resolve_child=_resolver(it.get("resolved") or {}), tag="custom_agent_stage", project_id=project_id or None, source="stage")
        except SdlcError as err:
            out.failed.append(f"{name}: {err.message}")
            emit({"type": "node", "node": "guardrail", "status": "error", "label": f"✗ Custom agent '{name}' failed: {err.message[:160]}"})
            continue
        except Exception as err:  # noqa: BLE001 - never sink the stage
            log.warning("custom agent %s failed", name, exc_info=True)
            out.failed.append(f"{name}: {str(err)[:120]}")
            emit({"type": "node", "node": "guardrail", "status": "error", "label": f"✗ Custom agent '{name}' failed: {str(err)[:160]}"})
            continue
        outs = body.get("outputs", [])
        for o in outs:
            title = name if len(outs) == 1 else f"{name} - {o['name']}"
            content = render_output(res.outputs.get(o["name"]), o["format"])
            made[o["artefact_type"].upper()] = content
            art = await save(type_=o["artefact_type"], title=title, content=content, summary=content[:300], run=res.record())
            if art is not None:
                out.artifacts.append(art)
        out.ran.append(f"{name} v{it['version']}")
        if audit is not None:
            try:
                audit.record(project_id=project_id, phase=phase, agent_role=name, event="custom_agent.run", provider=res.provider, model=res.model,
                             prompt_tokens=res.usage["promptTokens"], completion_tokens=res.usage["completionTokens"],
                             detail={"def": it["def_id"], "version": it["version"], "delegates": [c.agent_id for c in res.children], "outputs": [o["name"] for o in outs]})
            except Exception:  # noqa: BLE001
                log.warning("could not audit a custom agent run", exc_info=True)
    return out
