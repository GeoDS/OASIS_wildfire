"""Structured, session-scoped context for conversational spatial analysis.

The analysis graph deliberately starts fresh for every user turn.  This module
keeps the small amount of *analytical* memory that must survive those runs:
the current subject, time, previous result, visible layers, and recent prose.
It also resolves a conversational utterance into a standalone request before
the normal requirement-understanding pipeline sees it.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal

from pydantic import BaseModel, Field

from .external_sources import KNOWN_SOURCES, KNOWN_VARIABLES, catalogue, variable_catalogue
from .llm import is_mock, structured

#: Never carried into a prompt: bulk geometry, which is what a layer mostly is.
_GEOMETRY_KEYS = frozenset({"geojson", "geometry", "features", "bounds"})

#: The request filter and the resolver prompt both read the shared catalogue, so
#: the model is never told about a source the backend cannot honour.
_KNOWN_SOURCES = tuple(KNOWN_SOURCES)


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

    #: Place variables the current question is about, as the resolver named them.
    #: The answer opens with these, and a fetch is only worth offering when the
    #: reply would read one. Replaced every resolved turn, including with an
    #: empty list, so a question about income does not keep leading three turns
    #: after it was asked.
    active_variables: list[str] = Field(default_factory=list)

    #: What approved external fetches have returned this session, by source id.
    #: `last_result` holds one turn and the next one overwrites it, so data
    #: fetched three turns ago was gone by the time anyone asked about it and
    #: the session reported "no ACS data" while holding the figures. A fetch the
    #: user was stopped and asked to authorise belongs to the session.
    fetched: dict[str, Any] = Field(default_factory=dict)

    #: What historical fire events this deployment holds, and what each supports.
    #: Asked "what fires do you have data for", the session answered about the
    #: fire on screen and said it could not enumerate the rest - the catalogue
    #: was served to the sidebar and unreachable from the conversation.
    archive: list[dict[str, Any]] = Field(default_factory=list)

    def remember_archive(self, events: list[dict[str, Any]]) -> None:
        """Record what the local archive holds, for questions about the archive."""
        self.archive = list(events)

    def remember_fetch(self, source_id: str, payload: dict[str, Any]) -> None:
        """Record what one source returned, keyed so a re-fetch replaces it.

        Geometry is dropped on the way in. A place layer's GeoJSON is ~54 KB
        against ~1.4 KB of structured attributes, and this payload rides every
        resolver and discussion prompt for the rest of the session.
        """
        self.fetched[source_id] = {
            key: value for key, value in payload.items() if key not in _GEOMETRY_KEYS
        }

    def remember(self, role: Literal["user", "agent"], content: str) -> None:
        content = content.strip()
        if not content:
            return
        self.recent_turns.append(TurnRecord(role=role, content=content))
        self.recent_turns = self.recent_turns[-8:]

    def prompt_payload(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json", exclude_none=True)
        if not payload.get("archive"):
            payload.pop("archive", None)
        if not payload.get("fetched"):
            # An empty dict in the prompt reads as "we looked and found
            # nothing", which is a different claim from "nothing was fetched".
            payload.pop("fetched", None)
        return payload


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
    requested_sources: list[str] = Field(
        default_factory=list,
        description=(
            "External source ids the user asked for in plain words - 'fetch ACS data', "
            "'get the debris flow layer'. Empty unless they named or plainly described "
            "one. Never a guess about what would be useful: this is what they asked for."
        ),
    )
    requested_variables: list[str] = Field(
        default_factory=list,
        description=(
            "Place variables this turn is asking about, by their exact ids from the list "
            "in the system prompt. Judge by meaning, not by matching words: 'how wealthy "
            "are those places' is medianHouseholdIncome. Empty when the turn asks about "
            "none of them - a question about which cities burned asks about no variable."
        ),
    )
    reason: str


def variables_to_lead(resolution: ConversationResolution) -> tuple[str, ...]:
    """The variables this turn is about, filtered to ones the fill can supply.

    A model may name anything, so an id this deployment does not attach is
    dropped rather than trusted. Order is the model's, because it reflects what
    the question led with, and the answer opens with the same thing.
    """
    return tuple(
        name for name in dict.fromkeys(resolution.requested_variables) if name in KNOWN_VARIABLES
    )


def sources_to_fetch(
    resolution: ConversationResolution,
    context: ConversationContext,
    known: Iterable[str] = (),
) -> tuple[str, ...]:
    """Which requested sources this deployment has and the session lacks.

    Two filters, and both matter. A source this deployment does not offer is
    dropped rather than attempted, because the model may name anything. A source
    already in `fetched` is dropped too: the session is holding it, and going
    back out to the network would spend a call to learn nothing.
    """
    available = set(known) or set(_KNOWN_SOURCES)
    return tuple(
        source
        for source in dict.fromkeys(resolution.requested_sources)
        if source in available and source not in context.fetched
    )


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
11. Asking in plain words for data to be fetched is an instruction, not a remark.
   "fetch ACS data", "check the census figures", "get the debris flow layer" name a
   source; put its id in requested_sources. Leave requested_sources empty when the
   turn names no source, and never put one there because it would be useful - this
   field is what the user asked for, not what you would recommend.
12. Look at `fetched` in the context before you fill requested_sources. It lists what
   approved fetches have already returned this session. A source listed there is
   already in hand, so the turn is discussion answered from those figures - not a
   request to go and get them a second time.

13. requested_variables names which place variables the turn is about, from the list
   below. Judge by meaning. "How wealthy are those places" is medianHouseholdIncome
   even though it says neither "median" nor "income"; "who could not drive out" is
   householdsWithoutVehicle. A question that asks about none of them - which cities
   the fire reached, what the weather is - leaves it empty. This decides which figure
   the answer opens with, so naming one the user did not ask about is a real cost.

External sources this deployment can fetch:
{sources}

Place variables, with the wording that asks for each:
{variables}
"""


def _resolver_prompt() -> str:
    """The resolver prompt with the live source catalogue folded in.

    Built rather than written out, so a source added to `KNOWN_SOURCES` becomes
    requestable without a second edit here - and the model is never told about
    one the backend cannot honour.
    """
    return _RESOLVER_PROMPT.format(sources=catalogue(), variables=variable_catalogue())


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
                ("system", _resolver_prompt()),
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
