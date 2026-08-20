"""Structured, session-scoped context for conversational spatial analysis.

The analysis graph deliberately starts fresh for every user turn.  This module
keeps the small amount of *analytical* memory that must survive those runs:
the current subject, time, previous result, visible layers, and recent prose.
It also resolves a conversational utterance into a standalone request before
the normal requirement-understanding pipeline sees it.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .llm import is_mock, structured


class SubjectRef(BaseModel):
    type: Literal["fire_event", "place"]
    id: str | None = None
    name: str
    location: dict[str, Any] | None = None


class TimeRef(BaseModel):
    selected: str | None = None
    start: str | None = None
    end: str | None = None


class AnalysisRef(BaseModel):
    id: str
    operation: str
    description: str
    plan: dict[str, Any] | None = None
    layer_ids: list[str] = Field(default_factory=list)


class TurnRecord(BaseModel):
    role: Literal["user", "agent"]
    content: str


class ConversationContext(BaseModel):
    active_subject: SubjectRef | None = None
    active_time: TimeRef | None = None
    active_analysis: AnalysisRef | None = None
    active_layer_ids: list[str] = Field(default_factory=list)
    recent_turns: list[TurnRecord] = Field(default_factory=list)

    #: Facts behind the result currently on screen - the same status dict the UI
    #: was given. A discussion turn is answered from these rather than by
    #: recomputing, so "why is that number what it is?" has something to cite.
    last_result: dict[str, Any] | None = None

    def remember(self, role: Literal["user", "agent"], content: str) -> None:
        content = content.strip()
        if not content:
            return
        self.recent_turns.append(TurnRecord(role=role, content=content))
        self.recent_turns = self.recent_turns[-8:]

    def prompt_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


class ConversationResolution(BaseModel):
    relation: Literal["new_request", "follow_up", "correction"]
    kind: Literal["analysis", "discussion"] = Field(
        default="analysis",
        description=(
            "analysis: the turn asks for data to be selected, computed, or drawn. "
            "discussion: the turn asks about work already on screen - what a term "
            "means, why a method behaves that way, how to read a result - and "
            "needs no new data."
        ),
    )
    standalone_request: str = Field(
        min_length=1,
        description="A complete request that can be understood without the earlier turns.",
    )
    inherited_subject: bool = False
    inherited_time: bool = False
    inherited_analysis: bool = False
    reason: str


class ConversationResolutionError(RuntimeError):
    """The model could not safely resolve a context-dependent user turn."""


_RESOLVER_PROMPT = """You resolve conversational references for a wildfire geospatial analyst.

Return a standalone request for the downstream analysis planner. Use the supplied structured
context, but do not answer the question and do not invent a place, fire, date, layer, or result.

Rules:
1. A short continuation such as "what changed?", "which cities are those?", "compare it with
   the first day", or "only inside the burned area" is a follow-up when the context supplies
   the omitted subject or previous analysis.
2. An explicit new fire or place starts a new request and replaces the previous subject.
3. "Nearby" inherits a preceding place. It does not inherit an older historical fire when the
   immediately active subject is a place.
4. Preserve the user's requested operation exactly. Context may fill omitted references only.
5. When a follow-up modifies the previous analysis, include the previous operation, inputs,
   dates, and the requested modification in the standalone request.
6. Keep uncertainty visible. If context cannot resolve a reference, leave it unresolved instead
   of choosing a subject.
7. Your relation and inherited_* fields are the sole authority for conversational inheritance.
   When you inherit a subject or time, make it explicit in the standalone request.
8. Set kind="discussion" when the turn asks about the analysis already on screen rather
   than for new data: "why does a lower NDVI mean it burned?", "what does BA stand for?",
   "explain that more simply", "how confident is this?", "what does the caveat mean?".
   Set kind="analysis" when answering requires selecting, computing, or drawing data:
   a different fire, a different date, another metric, another place. If the turn asks
   both, choose analysis - the explanation can accompany the new result.
9. Asking for a figure that the displayed result already contains is discussion, not
   analysis: "what is the current NDVI value?", "how many pixels was that?", "what was
   the area again?". Re-running the pipeline to reread a number that is already on
   screen costs the user twenty seconds and redraws the map to tell them what they can
   already see.
10. But a question about a *different property* of the same fire is analysis, however
   conversational it sounds. Land cover, burn severity, spread direction, fire weather,
   terrain and vegetation change are each a separate computation: if the displayed
   result does not already contain the answer, asking for it needs new data. Answering
   "what kind of land did it burn?" out of a spread result on screen would hand the user
   the wrong analysis in a confident voice. When in doubt between the two, choose
   analysis - a slow correct answer beats a fast misdirected one.
"""


async def resolve_turn(text: str, context: ConversationContext) -> ConversationResolution:
    """Resolve one raw user turn against structured session context."""
    if context.active_subject is None:
        return ConversationResolution(
            relation="new_request",
            standalone_request=text,
            reason="This is the first request with an analytical subject.",
        )
    if is_mock():
        raise ConversationResolutionError(
            "Conversational follow-ups require a configured LLM; the mock provider does not "
            "guess references with keyword rules."
        )

    try:
        resolver = structured(ConversationResolution)
        resolution: ConversationResolution = await resolver.ainvoke(
            [
                ("system", _RESOLVER_PROMPT),
                (
                    "human",
                    (
                        "Current user message:\n"
                        f"{text}\n\nStructured conversation context:\n"
                        f"{context.prompt_payload()}"
                    ),
                ),
            ]
        )
    except Exception as exc:
        raise ConversationResolutionError(
            "The conversation model could not resolve this follow-up safely. Please retry; "
            "no keyword or regex fallback was applied."
        ) from exc

    if resolution.kind == "discussion":
        # A discussion turn is answered from context and never reaches the
        # planner, so inheritance flags carry no downstream meaning. Leave the
        # standalone wording intact for the narrator's benefit.
        return resolution
    if resolution.relation == "new_request":
        # The model is authoritative about whether this is a new request. The
        # backend only prevents stale context from rewriting independent text.
        resolution.standalone_request = text
        resolution.inherited_subject = False
        resolution.inherited_time = False
        resolution.inherited_analysis = False
    elif resolution.inherited_subject and context.active_subject:
        # This is validation, not classification: once the model says it
        # inherited the subject, ensure downstream planners receive its exact id.
        subject = context.active_subject
        if subject.name.casefold() not in resolution.standalone_request.casefold():
            anchor = (
                f"For {subject.name} (TS-SatFire event {subject.id})"
                if subject.type == "fire_event"
                else f"For {subject.name}"
            )
            resolution.standalone_request = f"{anchor}: {resolution.standalone_request.strip()}"
    return resolution
