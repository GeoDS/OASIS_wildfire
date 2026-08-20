"""Graph assembly.

START -> requirement_understanding -> task_compiler -+-> ambiguity_resolution -+
                                                     |         ^         |       |
                                                     |         +---------+       |
                                                     +-> analysis_contract <-----+
                                                                 |
                    ..... User Goal Agent | Planning Agent ......|.............
                                                                 |
                                          (ready?) -> planning -> execution -> END
                                          (not ready) ---------------------> END
"""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph

from . import nodes
from .state import GoalAgentState

#: The checkpointer msgpacks the Pydantic models held in state. LangGraph allows
#: arbitrary types by default but warns on every deserialisation, and future
#: versions will refuse outright - so register them explicitly. Add any new
#: model that ends up in state.
_ALLOWED_MSGPACK_MODULES = [
    ("wildfire_agent.contract", "AnalysisContract"),
    ("wildfire_agent.contract", "ScalarSlot"),
    ("wildfire_agent.contract", "SpatialSlot"),
    ("wildfire_agent.contract", "ResolvedLocation"),
    ("wildfire_agent.contract", "ClarificationQuestion"),
    ("wildfire_agent.contract", "ClarificationOption"),
    ("wildfire_agent.graph.models", "RequirementUnderstanding"),
    ("wildfire_agent.planning.models", "ExecutionPlan"),
    ("wildfire_agent.planning.models", "PlannedLayer"),
    ("wildfire_agent.planning.models", "UnmetNeed"),
    ("wildfire_agent.planning.models", "LayerResult"),
]


def make_serde() -> JsonPlusSerializer:
    return JsonPlusSerializer(allowed_msgpack_modules=_ALLOWED_MSGPACK_MODULES)


def build_graph(checkpointer=None):
    """Build and compile the User Goal Agent.

    Args:
        checkpointer: defaults to an in-process `MemorySaver`. A checkpointer is
            **required**: without one `interrupt()` has nowhere to persist state
            and multi-turn clarification degrades into replaying the graph.
            In production swap in `langgraph.checkpoint.postgres.PostgresSaver`.
    """
    builder = StateGraph(GoalAgentState)

    builder.add_node("requirement_understanding", nodes.requirement_understanding)
    builder.add_node("task_compiler", nodes.task_compiler)
    builder.add_node("ambiguity_resolution", nodes.ambiguity_resolution)
    builder.add_node("analysis_contract", nodes.analysis_contract)
    builder.add_node("planning", nodes.planning)
    builder.add_node("execution", nodes.execution)

    builder.add_edge(START, "requirement_understanding")
    builder.add_edge("requirement_understanding", "task_compiler")

    builder.add_conditional_edges(
        "task_compiler",
        nodes.route_after_compile,
        {
            "ambiguity_resolution": "ambiguity_resolution",
            "analysis_contract": "analysis_contract",
        },
    )
    builder.add_conditional_edges(
        "ambiguity_resolution",
        nodes.route_after_clarify,
        {
            "ambiguity_resolution": "ambiguity_resolution",
            "analysis_contract": "analysis_contract",
        },
    )
    builder.add_conditional_edges(
        "analysis_contract",
        nodes.route_after_contract,
        {"planning": "planning", "skip": END},
    )
    builder.add_edge("planning", "execution")
    builder.add_edge("execution", END)

    return builder.compile(checkpointer=checkpointer or MemorySaver(serde=make_serde()))


def export_mermaid() -> str:
    """Mermaid source for the architecture diagram - paste straight into a slide."""
    return build_graph().get_graph().draw_mermaid()
