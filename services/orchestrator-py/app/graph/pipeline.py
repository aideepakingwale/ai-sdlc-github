"""LangGraph orchestration pipeline (Module 3 §3, Python/langgraph per):

    START → planner → executor → synthesizer → response_formatting → fact_check → END

- planner: heuristic fast-path for status queries (no LLM), else JSON plan
- executor: dispatches the phase agent + MCP tools (Leader-agent delegation)
- synthesizer: composes the response from step outputs
- response_formatting: markdown styling
- fact_check: advisory verification against approved context/NFRs
"""

from __future__ import annotations

import re
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from ..agents.phase_agents import TEMPLATE_TOOLS, AgentDeps, PhaseAgentResult, run_phase_agent
from ..agents.schemas import FactCheck, PlannerOutput
from ..domain.models import AgentState, PlanStep, get_phase
from ..services.model_router import classify_tier
from ..services.prompt_library import render as render_prompt

PIPELINE_NODES = ["guardrail", "planner", "executor", "validator", "synthesizer", "formatter", "fact_check"]


def _emit_plan(emit: Any, state: AgentState, plan: list[PlanStep], tier: str) -> None:
    """Viz: one structured event describing what is ABOUT to run — plan
    steps, model tier, expected MCP tools and the LangGraph node path — so the
    UI can draw the execution path before it happens."""
    emit({
        "type": "plan",
        "stage": state.current_phase,
        "stageName": state.stage_name or get_phase(state.stage_template).name,
        "template": state.stage_template,
        "persona": get_phase(state.stage_template).agent_persona,
        "tier": tier,
        "steps": [{"id": s.id, "tool": s.tool, "description": s.description} for s in plan],
        "expectedTools": [] if any(s.tool == "status" for s in plan)
        else TEMPLATE_TOOLS.get(state.stage_template, []),
        "nodes": PIPELINE_NODES,
    })


class GraphState(TypedDict):
    state: AgentState
    phase_result: PhaseAgentResult | None


def _cfg(config: RunnableConfig) -> dict[str, Any]:
    return config["configurable"]  # type: ignore[return-value]


_STATUS_RE = re.compile(r"\b(status|progress|where are we|which phase)\b", re.I)


async def _planner(gs: GraphState, config: RunnableConfig) -> dict[str, Any]:
    deps: AgentDeps = _cfg(config)["deps"]
    emit = _cfg(config)["emit"]
    state = gs["state"]
    text = state.user_input.strip()

    # Heuristic fast-path (Module 3 §3): short status queries skip the LLM.
    if len(text) < 50 and _STATUS_RE.search(text):
        emit({"type": "node", "node": "planner", "label": "Fast-path: status query (no LLM)"})
        plan = [PlanStep(id="status", tool="status", description="Report pipeline status")]
        _emit_plan(emit, state, plan, "non_llm")
        return {"state": state.model_copy(update={"plan": plan})}

    emit({"type": "node", "node": "planner", "label": "Planning execution steps"})

    # Multi-model routing: the planner classifies the task weight and
    # picks the model tier deterministically. Phase generation itself is
    # heavy (design/codegen) so it targets the frontier chain, but the decision
    # is surfaced so the UI/audit can show which brain handled the work.
    chosen_tier = classify_tier(
        text, has_tools=True, context_tokens=sum(len(a.summary) // 4 for a in state.context_window)
    )
    emit({"type": "model", "tier": chosen_tier, "label": f"Model tier selected: {chosen_tier}"})

    try:
        data, _ = await deps.llm.generate_json(
            intent="standard", tag="planner_node", temperature=0, max_tokens=1024, schema=PlannerOutput,
            messages=[
                {
                    "role": "system",
                    "content": render_prompt(
                        "planner.system",
                        stage_seq=state.current_phase,
                        stage_name=state.stage_name or get_phase(state.stage_template).name,
                    ),
                },
                {"role": "user", "content": text},
            ],
        )
        plan = [PlanStep(**s.model_dump()) for s in data.steps]
    except Exception:
        # Planner failure is non-fatal: fall back to the canonical phase plan.
        plan = [PlanStep(id="run_phase", tool="auto",
                         description=f"Execute phase {state.current_phase} agent")]
    _emit_plan(emit, state, plan, chosen_tier)
    return {"state": state.model_copy(update={"plan": plan})}


async def _executor(gs: GraphState, config: RunnableConfig) -> dict[str, Any]:
    deps: AgentDeps = _cfg(config)["deps"]
    emit = _cfg(config)["emit"]
    state = gs["state"]

    if any(step.tool == "status" for step in state.plan):
        emit({"type": "node", "node": "executor", "label": "Reporting status"})
        summary: str = _cfg(config)["phase_states_summary"]
        return {
            "state": state.model_copy(update={"step_outputs": {**state.step_outputs, "status": summary}}),
            "phase_result": None,
        }

    emit({"type": "node", "node": "executor",
          "label": f"Dispatching Phase {state.current_phase} agent + MCP tools"})
    result = await run_phase_agent(deps, state, emit)
    return {
        "state": state.model_copy(update={
            "context_window": [*state.context_window, *result.new_artifacts],
            "gate_status": result.gate_status,
            "step_outputs": {**state.step_outputs, "phaseSummary": result.summary},
        }),
        "phase_result": result,
    }


async def _synthesizer(gs: GraphState, config: RunnableConfig) -> dict[str, Any]:
    emit = _cfg(config)["emit"]
    emit({"type": "node", "node": "synthesizer", "label": "Composing response"})
    state = gs["state"]

    status = state.step_outputs.get("status")
    if status:
        return {"state": state.model_copy(update={"final_response": str(status)})}

    result = gs["phase_result"]
    reviewer = state.stage_reviewer or get_phase(state.stage_template).reviewer_role
    lines: list[str] = []
    if result:
        lines.extend([result.summary, ""])
        if result.new_artifacts:
            lines.append("**Artifacts produced:**")
            for a in result.new_artifacts:
                link = f" ([link]({a.ref.url}))" if a.ref and a.ref.url else ""
                lines.append(f"- {a.type} — {a.title}{link}")
        if result.gate_status == "PENDING_REVIEW":
            lines.extend(["", f"⛔ **Gate:** awaiting {reviewer} sign-off before the next level can begin."])
        elif result.gate_status == "ESCALATED":
            lines.extend(["", "🚨 **Escalated to a human developer** — automated recovery limit reached."])
    return {"state": state.model_copy(update={"final_response": "\n".join(lines)})}


async def _response_formatting(gs: GraphState, config: RunnableConfig) -> dict[str, Any]:
    emit = _cfg(config)["emit"]
    emit({"type": "node", "node": "formatter", "label": "Applying markdown styling"})
    state = gs["state"]
    template = get_phase(state.stage_template)
    stage_name = state.stage_name or template.name
    body = state.final_response
    if not body.startswith("##"):
        body = f"## Stage {state.current_phase} — {stage_name}\n_Agent: {template.agent_persona}_\n\n{body}"
    return {"state": state.model_copy(update={"final_response": body})}


async def _fact_check(gs: GraphState, config: RunnableConfig) -> dict[str, Any]:
    if gs["phase_result"] is None:
        return {}  # status queries need no verification
    deps: AgentDeps = _cfg(config)["deps"]
    emit = _cfg(config)["emit"]
    state = gs["state"]
    emit({"type": "node", "node": "fact_check", "label": "Validating against approved context & NFRs"})
    try:
        context_summary = "\n".join(
            f"[P{a.phase}] {a.type}: {a.summary[:150]}" for a in state.context_window
        )[:8_000]
        data, _ = await deps.llm.generate_json(
            intent="standard", tag="fact_check_node", temperature=0, max_tokens=1024, schema=FactCheck,
            messages=[
                {"role": "system", "content": render_prompt("fact_check.system")},
                {
                    "role": "user",
                    "content": render_prompt(
                        "fact_check.user",
                        context_summary=context_summary,
                        response=state.final_response[:6_000],
                    ),
                },
            ],
        )
        if not data.ok and data.issues:
            caveat = f"\n\n> ⚠️ **Fact-check flags:** {'; '.join(data.issues)}"
            return {"state": state.model_copy(update={
                "final_response": state.final_response + caveat,
                "errors": [*state.errors, *data.issues],
            })}
    except Exception:
        pass  # advisory only — never fail the pipeline
    return {}


def build_pipeline():
    builder = StateGraph(GraphState)
    builder.add_node("planner", _planner)
    builder.add_node("executor", _executor)
    builder.add_node("synthesizer", _synthesizer)
    builder.add_node("response_formatting", _response_formatting)
    builder.add_node("fact_check", _fact_check)
    builder.add_edge(START, "planner")
    builder.add_edge("planner", "executor")
    builder.add_edge("executor", "synthesizer")
    builder.add_edge("synthesizer", "response_formatting")
    builder.add_edge("response_formatting", "fact_check")
    builder.add_edge("fact_check", END)
    return builder.compile()


async def run_pipeline(
    pipeline: Any, state: AgentState, *, deps: AgentDeps, emit: Any, phase_states_summary: str,
) -> tuple[AgentState, PhaseAgentResult | None]:
    out: GraphState = await pipeline.ainvoke(
        {"state": state, "phase_result": None},
        config={
            "configurable": {"deps": deps, "emit": emit, "phase_states_summary": phase_states_summary},
            "recursion_limit": 25,
        },
    )
    return out["state"], out["phase_result"]
