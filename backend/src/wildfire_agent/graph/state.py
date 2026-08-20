"""LangGraph state definition."""

from __future__ import annotations

from typing import Annotated, Literal, TypedDict

from ..contract import AnalysisContract, ClarificationQuestion, SpatialSlot
from ..planning import ExecutionPlan, LayerResult
from ..taxonomy import ExpertiseLevel
from .models import RequirementUnderstanding

Stage = Literal[
    "requirement_understanding",
    "task_compiler",
    "ambiguity_resolution",
    "analysis_contract",
    "planning",
    "execution",
    "done",
]

#: Display names for every stage, used by the pipeline stepper in the UI.
#: The first four belong to the User Goal Agent, the last two to the Planning
#: Agent; `STAGE_AGENTS` records which is which so the UI can show the handoff.
STAGE_LABELS: dict[str, str] = {
    "requirement_understanding": "Requirement Understanding",
    "task_compiler": "Task Compiler",
    "ambiguity_resolution": "Ambiguity Resolution",
    "analysis_contract": "Analysis Contract",
    "planning": "Layer Selection",
    "execution": "Fetch & Render",
}

STAGE_AGENTS: dict[str, str] = {
    "requirement_understanding": "User Goal Agent",
    "task_compiler": "User Goal Agent",
    "ambiguity_resolution": "User Goal Agent",
    "analysis_contract": "User Goal Agent",
    "planning": "Planning Agent",
    "execution": "Planning Agent",
}

#: Hard cap on clarification rounds. Past this the contract ships with its
#: unresolved gaps recorded - never an endless interrogation.
MAX_CLARIFICATION_ROUNDS = 3


def _append(left: list, right: list) -> list:
    return (left or []) + (right or [])


class TranscriptEntry(TypedDict):
    role: Literal["user", "agent"]
    content: str


class GoalAgentState(TypedDict, total=False):
    """State for the User Goal Agent.

    `total=False` lets each node return only the keys it changed.
    """

    original_request: str
    #: Standalone wording produced by the session context resolver. The raw
    #: original request remains separate for the contract audit trail.
    resolved_request: str | None
    context_resolution: dict | None
    transcript: Annotated[list[TranscriptEntry], _append]

    #: Manual expertise pick from the UI. When set, it overrides the inference.
    expertise_override: ExpertiseLevel | None

    understanding: RequirementUnderstanding | None
    contract: AnalysisContract | None

    #: Last completed spatial subject, supplied by the API for conversational
    #: follow-ups such as "is there fire nearby?".
    prior_location: SpatialSlot | None

    #: Last completed TS-SatFire event is separate from location. Keeping the
    #: event id and selected date prevents "this fire" from being routed as a
    #: city name on the following turn.
    prior_fire_event_id: str | None
    prior_fire_day: str | None

    pending_questions: list[ClarificationQuestion]
    clarification_rounds: int

    #: Produced downstream of the contract, by the Planning Agent.
    plan: ExecutionPlan | None
    layers: list[LayerResult]
    layer_summary: str

    stage: Stage
