"""The Analysis Contract - what Task 1 produces, and the interface between the
problem-definition stage and the downstream planning stage.

One definition serves four consumers: structured LLM output, runtime validation,
the FastAPI OpenAPI document, and the frontend TypeScript types (via
`GET /api/schema/contract`).

Field-by-field, this is what the downstream agent routes on - see the mapping
in `AnalysisContract` below - plus the six revisions that came out of the
walkthroughs in `docs/02-walkthroughs.md`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, computed_field

from .taxonomy import ExpertiseLevel, TaskIntent, UserRole

Provenance = Literal["user_stated", "agent_inferred", "default"]


# ══════════════════════════════════════════════════════════════════
# Slots
# ══════════════════════════════════════════════════════════════════


class SlotBase(BaseModel):
    """A single information slot.

    `is_blocking` decides whether the gap is worth interrupting the user for.
    The test is not "is the field empty" but "would the emptiness materially
    change the analysis". See `docs/01-taxonomy.md` section 3.
    """

    value: str | None = None
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    source: Provenance = "default"
    is_blocking: bool = Field(
        False,
        description=(
            "Whether a missing value would materially change the analysis. "
            "Only a blocking slot that is also empty triggers clarification."
        ),
    )
    blocking_reason: str | None = Field(
        None,
        description=(
            "Why this slot is or is not blocking. Mandatory whenever the matrix "
            "baseline was promoted or demoted, so the decision stays auditable."
        ),
    )

    @property
    def is_filled(self) -> bool:
        return self.value is not None and self.value.strip() != ""

    @property
    def needs_clarification(self) -> bool:
        return self.is_blocking and not self.is_filled


class ScalarSlot(SlotBase):
    kind: Literal["scalar"] = "scalar"


class ResolvedLocation(BaseModel):
    """Geocoding result. `confirmed_by_user` exists because echoing the spatial
    scope back for confirmation is an explicit Task 1 responsibility."""

    display_name: str | None = None
    center: tuple[float, float] | None = Field(
        None, description="[lon, lat] in WGS84 - GeoJSON order, do not swap."
    )
    buffer_km: float | None = None
    bbox: tuple[float, float, float, float] | None = Field(
        None, description="[west, south, east, north]"
    )
    geocoder: str | None = Field(None, description="nominatim / user_provided / fallback_gazetteer")
    confirmed_by_user: bool = False

    alternatives: list[str] = Field(
        default_factory=list,
        description=(
            "Same-name candidates. There are a dozen places called Madison in the "
            "US; the user has to be able to see which one was picked."
        ),
    )
    ambiguous: bool = Field(
        False,
        description=(
            "Candidates are both far apart and comparably prominent. This does "
            "**not** trigger a question: the map echo already lets the user see "
            "the circle landed in the wrong state. It only raises the emphasis "
            "in the UI and records an assumption."
        ),
    )


class SpatialSlot(SlotBase):
    """The spatial slot - the only place in Task 1 that needs real geocoding."""

    kind: Literal["spatial"] = "spatial"
    raw: str | None = Field(None, description="The user's own words, e.g. 'near my house'")
    resolved: ResolvedLocation | None = None

    @property
    def needs_clarification(self) -> bool:
        # Two kinds of gap interrupt the user here:
        #   1. nothing was said at all
        #   2. something was said but it does not geocode ("near my house")
        # Same-name ambiguity (`resolved.ambiguous`) is deliberately excluded:
        # the map already draws the chosen place, so a wrong state is visible at
        # a glance. Rule 2 - if it can be confirmed visually, do not ask.
        if not self.is_blocking:
            return False
        if not self.is_filled:
            return True
        return self.resolved is None or self.resolved.center is None


Slot = Annotated[ScalarSlot | SpatialSlot, Field(discriminator="kind")]


# ══════════════════════════════════════════════════════════════════
# Clarification
# ══════════════════════════════════════════════════════════════════


class ClarificationOption(BaseModel):
    """A concrete choice. The `general` expertise level must offer options
    rather than open questions - see the register table in `docs/01-taxonomy.md`."""

    label: str
    #: How this choice changes the conclusion. Plain language at `general`,
    #: parameter semantics at `expert`.
    implication: str | None = None
    recommended: bool = False


class ClarificationQuestion(BaseModel):
    slot: str = Field(description="Which slot this question fills")
    question: str
    options: list[ClarificationOption] = Field(
        default_factory=list,
        description="Should be non-empty at the general and practitioner levels",
    )
    allow_free_text: bool = True


# ══════════════════════════════════════════════════════════════════
# Analysis Contract
# ══════════════════════════════════════════════════════════════════


class AnalysisContract(BaseModel):
    """The structured task specification handed to the Planning Agent.

    What each field routes downstream:
      task_intent    -> analytical goal and workflow type
      hazard_objects -> required variables and candidate dataset/API families
      slots          -> whether information suffices, or a targeted question is needed
      user_role      -> priorities and preferred output
      expertise      -> terminology, explanation depth, technical detail
    """

    schema_version: Literal["1.0"] = "1.0"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    original_request: str = Field(description="The user's original wording, kept for audit")
    resolved_request: str | None = Field(
        None,
        description=(
            "Conversation-aware standalone wording used by planners. The original wording "
            "remains unchanged for audit."
        ),
    )
    restatement: str | None = Field(
        None, description="One-sentence restatement so the user can confirm the agent got it"
    )

    # ── The three orthogonal dimensions (independent, not fixed combinations) ──
    user_role: UserRole = "unknown"
    expertise: ExpertiseLevel = "general"
    task_intent: list[TaskIntent] = Field(default_factory=list)

    # ── Data needs: family level only, never a concrete API ──
    hazard_objects: list[str] = Field(default_factory=list)

    # ── Slots: a dynamic set determined by intent (walkthrough scenario 2) ──
    slots: dict[str, Slot] = Field(default_factory=dict)

    # ── Confidence and auditability: the core of reducing errors ──
    assumptions: list[str] = Field(
        default_factory=list,
        description="Decisions the agent made on the user's behalf; must stay visible",
    )
    unresolved: list[str] = Field(
        default_factory=list, description="Ambiguities that are still open"
    )
    clarification_rounds: int = 0

    # ── Derived ──────────────────────────────────────────────────

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ready_for_planning(self) -> bool:
        """True once no blocking gap remains.

        Note this is a different question from "can the downstream agent
        actually do it" (walkthrough scenario 3). A complete contract is ready;
        insufficient capability is a conclusion for downstream capability
        matching, and Task 1 does not pre-empt it.
        """
        return not self.pending_slots

    @property
    def pending_slots(self) -> list[str]:
        return [name for name, slot in self.slots.items() if slot.needs_clarification]

    def spatial(self) -> SpatialSlot | None:
        slot = self.slots.get("location")
        return slot if isinstance(slot, SpatialSlot) else None

    def analysis_request(self) -> str:
        """Return the context-resolved wording used for routing and execution."""
        return self.resolved_request or self.original_request

    def filled_ratio(self) -> float:
        """Drives the progress bar in the UI."""
        if not self.slots:
            return 0.0
        return sum(s.is_filled for s in self.slots.values()) / len(self.slots)
