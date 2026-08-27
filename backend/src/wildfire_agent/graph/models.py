"""Structured input/output models exchanged between the nodes and the LLM.

Deliberately separate from `contract.py`: the contract is the deliverable handed
downstream, these are internal work orders. Each node asks the model for one
small flat structure rather than the whole contract in a single shot - narrow
outputs are steadier, easier to tune, and pinpoint which step went wrong.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..contract import ClarificationQuestion, Provenance
from ..taxonomy import ExpertiseLevel, TaskIntent, UserRole


class RequirementUnderstanding(BaseModel):
    """Stage 1 output: the three orthogonal dimensions, hazard objects, and the
    raw wording for each slot.

    Every judgement carries a matching `*_reason`. The whole point of this agent
    is reducing errors, so the reasoning has to be auditable, not just the
    conclusion.
    """

    restatement: str = Field(description="One sentence: what problem does the user want solved?")

    task_intent: list[TaskIntent] = Field(
        min_length=1,
        description="One or more. Prefer over-selecting to missing an obvious second intent.",
    )
    intent_reason: str

    expertise: ExpertiseLevel
    expertise_reason: str

    user_role: UserRole = Field(
        description="Must be 'unknown' when there is no clear signal. Do not guess."
    )
    role_reason: str
    role_materially_changes_analysis: bool = Field(
        description=(
            "If the role stays unknown, would that materially change the analysis? "
            "Only True justifies asking the user about it."
        )
    )

    hazard_objects: list[str] = Field(
        description="Pick from the supplied list. Declare data families, never a specific API."
    )
    hazard_reason: str

    # Raw slot wording - not grounded, not defaulted; stage 2 compiles these.
    raw_location: str | None = None
    raw_time_horizon: str | None = None
    raw_target: str | None = None
    raw_requested_output: str | None = None
    raw_threshold: str | None = None
    raw_comparison_basis: str | None = None
    raw_intervention: str | None = None
    raw_scenario: str | None = None

    def raw_slot_values(self) -> dict[str, str | None]:
        return {
            "location": self.raw_location,
            "time_horizon": self.raw_time_horizon,
            "target": self.raw_target,
            "requested_output": self.raw_requested_output,
            "threshold": self.raw_threshold,
            "comparison_basis": self.raw_comparison_basis,
            "intervention": self.raw_intervention,
            "scenario": self.raw_scenario,
        }


class SlotDecision(BaseModel):
    """Stage 2's verdict on one slot."""

    slot: str
    value: str | None = Field(None, description="Leave empty when the information is absent")
    source: Provenance = "default"
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    is_blocking: bool
    blocking_reason: str = Field(
        description=(
            "Why it is or is not blocking. If this differs from the supplied "
            "baseline, name the override rule you applied."
        )
    )


class CompiledAssumption(BaseModel):
    """One default the agent fell back on, tagged with the slot it concerns.

    The tag is what lets an assumption be *retracted*. If `target` is later
    promoted to blocking, "no receptor was specified, so we cover everything"
    is no longer true, and a contract that still carries it is lying to the
    downstream agent while the UI shows the same slot as unanswered.
    """

    slot: str | None = Field(
        None, description="The slot this assumption is about; null if it concerns none"
    )
    text: str


class CompiledTask(BaseModel):
    """Stage 2 output."""

    slots: list[SlotDecision]
    assumptions: list[CompiledAssumption] = Field(
        default_factory=list, description="One entry for every neutral default you fell back on"
    )


class ClarificationBatch(BaseModel):
    """Stage 3 output: every gap asked at once, never drip-fed."""

    preamble: str = Field(description="One lead-in sentence, pitched at the expertise level")
    questions: list[ClarificationQuestion]


class SlotUpdate(BaseModel):
    slot: str
    value: str
    confidence: float = Field(1.0, ge=0.0, le=1.0)


class ClarificationInterpretation(BaseModel):
    """The second half of stage 3: map the user's free-text reply back onto slots."""

    updates: list[SlotUpdate] = Field(default_factory=list)
    still_unresolved: list[str] = Field(
        default_factory=list,
        description="Slots the user could not answer, or answered too vaguely to use",
    )
    user_declined: bool = Field(
        False,
        description=(
            "The user explicitly handed the decision back ('you decide', "
            "'I don't know'). Stop asking and use documented defaults."
        ),
    )
