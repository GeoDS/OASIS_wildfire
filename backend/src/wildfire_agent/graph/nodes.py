"""The pipeline, one node per stage:

User Prompt -> 1. Requirement Understanding -> 2. Task Compiler
            -> 3. Ambiguity Resolution -> 4. Analysis Contract -> Planning Agent
"""

from __future__ import annotations

import re

from langgraph.types import interrupt

from ..contract import (
    AnalysisContract,
    ClarificationQuestion,
    ResolvedLocation,
    ScalarSlot,
    SpatialSlot,
)
from ..geocoding import DEFAULT_BUFFER_KM, resolve_local_fire, resolve_location
from ..llm import structured
from ..planning import build_plan, execute, summarise
from ..request_intent import requests_fire
from ..taxonomy import (
    HAZARD_OBJECTS,
    DataFamilyChoice,
    family_choices_for,
    required_slots,
    slot_default,
)
from . import prompts
from .models import (
    ClarificationBatch,
    ClarificationInterpretation,
    CompiledTask,
    RequirementUnderstanding,
)
from .state import MAX_CLARIFICATION_ROUNDS, GoalAgentState

_BUFFER_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(km|kilomet(?:er|re)s?|mi\b|miles?)", re.IGNORECASE)


def _extract_buffer_km(text: str | None) -> float | None:
    """Pull a buffer radius out of free text, normalised to kilometres."""
    if not text:
        return None
    match = _BUFFER_RE.search(text)
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2).lower()
    if unit.startswith("mi"):
        value *= 1.609344
    return value


# ══════════════════════════════════════════════════════════════════
# 1. Requirement Understanding
# ══════════════════════════════════════════════════════════════════


async def requirement_understanding(state: GoalAgentState) -> dict:
    request = state.get("resolved_request") or state["original_request"]

    llm = structured(RequirementUnderstanding)
    understanding: RequirementUnderstanding = await llm.ainvoke(
        [
            ("system", prompts.requirement_understanding_prompt()),
            (
                "human",
                (
                    f"Current user wording:\n\n{state['original_request']}\n\n"
                    f"Conversation-resolved standalone request:\n\n{request}"
                ),
            ),
        ]
    )

    # Drop hazard objects the model invented. Fewer is recoverable; wrong is not.
    understanding.hazard_objects = [h for h in understanding.hazard_objects if h in HAZARD_OBJECTS]

    # A manual pick in the UI outranks the agent's inference. This powers the
    # demo where one question is replayed at all three expertise levels.
    override = state.get("expertise_override")
    if override:
        understanding.expertise = override
        understanding.expertise_reason = "Set manually by the user in the interface."

    return {"understanding": understanding, "stage": "task_compiler"}


# ══════════════════════════════════════════════════════════════════
# 2. Task Compiler
# ══════════════════════════════════════════════════════════════════


async def task_compiler(state: GoalAgentState) -> dict:
    understanding: RequirementUnderstanding = state["understanding"]
    intents = list(understanding.task_intent)
    baseline = required_slots(intents)

    raw = understanding.raw_slot_values()
    stated = (
        "\n".join(f"- {slot}: {value!r}" for slot, value in raw.items() if value)
        or "(the user stated none of the slots explicitly)"
    )

    llm = structured(CompiledTask)
    compiled: CompiledTask = await llm.ainvoke(
        [
            ("system", prompts.task_compiler_prompt(intents, understanding.expertise)),
            (
                "human",
                (
                    f"Current user wording:\n{state['original_request']}\n\n"
                    f"Standalone request:\n"
                    f"{state.get('resolved_request') or state['original_request']}\n\n"
                    f"Restatement: {understanding.restatement}\n"
                    f"User role: {understanding.user_role} ({understanding.role_reason})\n"
                    f"Role materially changes analysis: "
                    f"{understanding.role_materially_changes_analysis}\n"
                    f"Hazard objects: {understanding.hazard_objects}\n\n"
                    f"Raw slot values the user actually stated:\n{stated}"
                ),
            ),
        ]
    )

    slots: dict[str, ScalarSlot | SpatialSlot] = {}
    for decision in compiled.slots:
        # The model can invent slots; the slot set is determined by intent alone.
        if decision.slot not in baseline:
            continue
        common = {
            "value": decision.value,
            "confidence": decision.confidence,
            "source": decision.source,
            "is_blocking": decision.is_blocking,
            "blocking_reason": decision.blocking_reason,
        }
        if decision.slot == "location":
            slots["location"] = SpatialSlot(raw=raw.get("location"), **common)
        else:
            slots[decision.slot] = ScalarSlot(**common)

    # Back-fill anything the model skipped, otherwise the UI loses a row.
    for name, req in baseline.items():
        if name in slots:
            continue
        common = {
            "value": None,
            "confidence": 0.0,
            "source": "default",
            "is_blocking": req.requirement == "B",
            "blocking_reason": (
                f"Matrix baseline {req.requirement} (from intent: {', '.join(req.from_intents)})"
            ),
        }
        slots[name] = (
            SpatialSlot(raw=raw.get("location"), **common)
            if name == "location"
            else ScalarSlot(**common)
        )

    contract = AnalysisContract(
        original_request=state["original_request"],
        resolved_request=state.get("resolved_request"),
        restatement=understanding.restatement,
        user_role=understanding.user_role,
        expertise=understanding.expertise,
        task_intent=intents,
        hazard_objects=understanding.hazard_objects,
        slots=slots,
        assumptions=[a.text for a in compiled.assumptions],
        clarification_rounds=state.get("clarification_rounds", 0),
    )

    enforce_family_disambiguation(contract)

    # Retract any assumption about a slot that is now waiting on the user. The
    # model wrote "no receptor was specified, so we cover everything" before
    # enforcement promoted `target`; keeping it would contradict the very
    # question we are about to ask.
    still_pending = set(contract.pending_slots)
    contract.assumptions = [a.text for a in compiled.assumptions if a.slot not in still_pending]
    context_resolution = state.get("context_resolution") or {}
    inherits_subject = bool(
        context_resolution.get("relation") in {"follow_up", "correction"}
        and context_resolution.get("inherited_subject")
    )
    _inherit_prior_fire_context(
        contract,
        state.get("prior_fire_event_id"),
        state.get("prior_fire_day"),
        inherit_subject=inherits_subject,
        inherit_time=bool(context_resolution.get("inherited_time")),
    )

    inherited_location = _inherit_prior_location(
        contract,
        state.get("prior_location"),
        inherit_subject=inherits_subject,
    )

    if not inherited_location:
        await _ground_location(contract)

    return {"contract": contract, "stage": "ambiguity_resolution"}


def _inherit_prior_fire_context(
    contract: AnalysisContract,
    prior_event_id: str | None,
    prior_day: str | None,
    *,
    inherit_subject: bool,
    inherit_time: bool,
) -> bool:
    """Apply only the inheritance explicitly authorized by the resolver model."""
    if not prior_event_id or not inherit_subject:
        return False

    time_horizon = contract.slots.get("time_horizon")
    selected_time: str | None = None
    if isinstance(time_horizon, ScalarSlot):
        current_time = (time_horizon.value or "").strip()
        is_default_time = (
            not time_horizon.is_filled
            or time_horizon.source == "default"
            or current_time.casefold() in {"now", "current", "today"}
        )
        if inherit_time and prior_day and is_default_time:
            time_horizon.value = prior_day
            time_horizon.source = "agent_inferred"
            time_horizon.confidence = 0.99
            time_horizon.is_blocking = False
            time_horizon.blocking_reason = (
                "The conversation model inherited the selected date from the preceding "
                "historical fire-event turn."
            )
            selected_time = prior_day
        elif time_horizon.is_filled and not is_default_time:
            # The compiler has already interpreted the model-resolved standalone
            # request (for example, a first-day-to-last-day comparison). Preserve
            # that richer range instead of re-parsing dates here.
            selected_time = current_time

    target = contract.slots.get("target")
    if isinstance(target, ScalarSlot):
        target.value = "TS-SatFire active fire + burned area historical labels"
        target.source = "agent_inferred"
        target.confidence = 0.99
        target.is_blocking = False
        target.blocking_reason = (
            "This follow-up refers to the preceding TS-SatFire event, so AF and BA "
            "labels remain the evidence products."
        )

    contract.assumptions = [
        assumption
        for assumption in contract.assumptions
        if not assumption.startswith("Fire evidence selected automatically:")
        and not (
            "time" in assumption.lower()
            and any(word in assumption.lower() for word in ("now", "current"))
        )
    ]
    context_note = "Historical fire context inherited from the preceding turn" + (
        f": event {prior_event_id}, time {selected_time}." if selected_time else "."
    )
    contract.assumptions.append(context_note)
    return True


def _inherit_prior_location(
    contract: AnalysisContract,
    prior_location: SpatialSlot | None,
    *,
    inherit_subject: bool,
) -> bool:
    """Carry location only when the resolver model authorized subject inheritance."""
    if not inherit_subject:
        return False
    spatial = contract.spatial()
    if spatial is None or prior_location is None or prior_location.resolved is None:
        return False
    if prior_location.resolved.center is None:
        return False

    resolved = prior_location.resolved.model_copy(deep=True)
    spatial.value = prior_location.value or resolved.display_name
    spatial.resolved = resolved
    spatial.source = "agent_inferred"
    spatial.confidence = max(spatial.confidence, 0.95)
    spatial.blocking_reason = (
        "The follow-up did not name a new place, so it inherits the resolved location "
        "from the preceding turn."
    )
    _replace_assumptions(
        contract,
        _SPATIAL_ASSUMPTION_PREFIXES,
        [
            (
                f"{_INHERITED_LOCATION_PREFIX}: using "
                f"{resolved.display_name or spatial.value} because this follow-up "
                "did not specify a different place."
            ),
        ],
    )
    return True


# ── Non-interchangeable data families ─────────────────────────────


def names_a_family(value: str | None, choices: list[DataFamilyChoice]) -> bool:
    """Has the user actually named one of these data families?

    Uses the keywords declared on each choice. An earlier version derived them
    from the choice id, which made 'fire' a keyword of
    `official_fire_perimeters` - so the phrase "active fires" counted as an
    answer even though it is exactly the ambiguity we are trying to resolve.
    """
    if not value:
        return False
    lowered = value.lower()
    return any(any(keyword in lowered for keyword in choice.keywords) for choice in choices)


def enforce_family_disambiguation(contract: AnalysisContract) -> list[DataFamilyChoice]:
    """Resolve data-family choices deterministically or mark them blocking.

    Active-fire evidence is chosen by backend policy so the fire remains the
    user's subject and product names never become UI prerequisites. Other
    genuinely different questions, such as smoke overhead versus air breathed
    at a ground monitor, still require clarification.

    Returns the choices that apply, so the clarification stage can render them.
    """
    choices = family_choices_for(contract.hazard_objects)
    if not choices:
        return []

    target = contract.slots.get("target")
    if target is None:
        target = ScalarSlot()
        contract.slots["target"] = target

    if names_a_family(target.value, choices):
        return choices

    # An exact local TS-SatFire event is a different evidence product from a
    # live/official perimeter query. Once the user names one of those archived
    # events, make the AF + BA labels explicit in the contract instead of
    # leaving the UI to claim that the lifecycle view is an official perimeter.
    local_fire = (
        resolve_local_fire(contract.analysis_request())
        if "active_fire" in contract.hazard_objects
        else None
    )
    if local_fire is not None and not names_a_family(contract.analysis_request(), choices):
        label = "TS-SatFire active fire + burned area historical labels"
        target.value = label
        target.source = "agent_inferred"
        target.confidence = 0.99
        target.is_blocking = False
        target.blocking_reason = (
            "The request names an event in the local TS-SatFire subset, so the backend "
            "uses its AF and BA labels rather than silently substituting a current product."
        )
        note = f"Fire evidence selected automatically: {label}."
        contract.assumptions = [
            assumption
            for assumption in contract.assumptions
            if not assumption.startswith("Fire evidence selected automatically:")
        ] + [note]
        return choices

    # Fire questions are event-centred in this demo. The backend chooses the
    # evidence family instead of asking a non-technical user to understand
    # WFIGS versus VIIRS before seeing a map. Explicit source wording still
    # wins above. For an unspecified fire observation, confirmed perimeters are
    # the conservative default; satellite evidence is selected only when the
    # request actually asks for heat/hotspots/near-real-time observations.
    if "active_fire" in contract.hazard_objects:
        text = " ".join(
            filter(
                None,
                [
                    contract.analysis_request(),
                    contract.restatement,
                    target.value,
                ],
            )
        ).lower()
        satellite = next(choice for choice in choices if choice.id == "satellite_hotspots")
        official = next(choice for choice in choices if choice.id == "official_fire_perimeters")
        chosen = satellite if any(keyword in text for keyword in satellite.keywords) else official
        target.value = chosen.label
        target.source = "agent_inferred"
        target.confidence = 0.82
        target.is_blocking = False
        target.blocking_reason = (
            f"Backend selected {chosen.label.lower()} from the fire question; "
            "the user does not need to choose a data product."
        )
        note = f"Fire evidence selected automatically: {chosen.label}."
        contract.assumptions = [
            assumption
            for assumption in contract.assumptions
            if not assumption.startswith("Fire evidence selected automatically:")
        ] + [note]
        return choices

    if "smoke_plume" in contract.hazard_objects:
        text = " ".join(
            filter(None, [contract.analysis_request(), contract.restatement, target.value])
        ).lower()
        ground = next(choice for choice in choices if choice.id == "ground_air_quality")
        plume = next(choice for choice in choices if choice.id == "satellite_plume_extent")
        chosen = (
            ground
            if any(token in text for token in ("air quality", "aqi", "pm2.5", "pm25", "breathe"))
            else plume
        )
        target.value = chosen.label
        target.source = "agent_inferred"
        target.confidence = 0.82
        target.is_blocking = False
        target.blocking_reason = (
            f"Backend selected {chosen.label.lower()} from the city context request."
        )
        return choices

    # Clear any neutral default: a default here is exactly the silent wrong pick.
    target.value = None
    target.source = "default"
    target.confidence = 0.0
    target.is_blocking = True
    target.blocking_reason = (
        "The requested hazard objects are served by data families that are not "
        "semantically interchangeable ("
        + " / ".join(choice.label for choice in choices)
        + "). Choosing without asking would invalidate the answer rather than "
        "merely coarsen it."
    )
    return choices


async def _ground_location(contract: AnalysisContract) -> None:
    """Geocode the location slot in place.

    Leaving `resolved` as None when nothing geocodes is not an error - it is the
    signal that Ambiguity Resolution should step in (see
    `SpatialSlot.needs_clarification`).
    """
    spatial = contract.spatial()
    if spatial is None:
        return

    fire_request = requests_fire(contract.analysis_request())
    query = spatial.value or spatial.raw
    if not query:
        if not fire_request:
            return
        resolved = resolve_local_fire(contract.analysis_request())
        if resolved is None:
            return
        spatial.value = resolved.display_name
        spatial.raw = resolved.display_name
        spatial.source = "agent_inferred"
        spatial.confidence = max(spatial.confidence, 0.95)
        spatial.blocking_reason = "Named fire was resolved from the local fire catalog; no separate place input is needed."
        spatial.resolved = resolved
        _replace_assumptions(
            contract,
            _SPATIAL_ASSUMPTION_PREFIXES,
            _spatial_assumptions(resolved, buffer_was_stated=False),
        )
        return

    buffer_km = _extract_buffer_km(spatial.value) or _extract_buffer_km(spatial.raw)
    resolved = await resolve_location(query, buffer_km=buffer_km)

    if resolved is None:
        return

    spatial.resolved = resolved

    # Grounding runs repeatedly (once at compile time, again after every
    # clarification round), so these notes are **recomputed** rather than
    # appended. Once the user supplies a radius, "assuming 25 km" is no longer
    # true, and leaving it in place would misreport the contract downstream.
    _replace_assumptions(
        contract,
        _SPATIAL_ASSUMPTION_PREFIXES,
        _spatial_assumptions(resolved, buffer_was_stated=buffer_km is not None),
    )


_DEFAULT_BUFFER_PREFIX = "Radius not specified"
_FALLBACK_GEOCODER_PREFIX = "Online geocoder unreachable"
_UNCONFIRMED_PREFIX = "Spatial scope resolved by the agent"
_AMBIGUOUS_PLACE_PREFIX = "Place name is ambiguous"
_INHERITED_LOCATION_PREFIX = "Location inherited from the preceding turn"

_SPATIAL_ASSUMPTION_PREFIXES = (
    _DEFAULT_BUFFER_PREFIX,
    _FALLBACK_GEOCODER_PREFIX,
    _UNCONFIRMED_PREFIX,
    _AMBIGUOUS_PLACE_PREFIX,
    _INHERITED_LOCATION_PREFIX,
)


def _spatial_assumptions(resolved: ResolvedLocation, *, buffer_was_stated: bool) -> list[str]:
    notes: list[str] = []
    if resolved.ambiguous and resolved.alternatives:
        notes.append(
            f"{_AMBIGUOUS_PLACE_PREFIX}: picked '{resolved.display_name}'; other "
            f"candidates include {', '.join(resolved.alternatives[:2])}. "
            f"Say so in the chat if this is the wrong one."
        )
    if not buffer_was_stated:
        notes.append(
            f"{_DEFAULT_BUFFER_PREFIX}: using a {DEFAULT_BUFFER_KM:.0f} km buffer, "
            "only as a hidden backend retrieval envelope; it is not displayed or "
            "interpreted as the subject or an impact zone."
        )
    if resolved.geocoder == "fallback_gazetteer":
        notes.append(
            f"{_FALLBACK_GEOCODER_PREFIX}: fell back to the built-in gazetteer, "
            f"so the coordinates are approximate."
        )
    if not resolved.confirmed_by_user:
        notes.append(
            f"{_UNCONFIRMED_PREFIX}: the selected place or fire identity has not "
            "been explicitly confirmed; correct it in the chat if needed."
        )
    return notes


def _replace_assumptions(
    contract: AnalysisContract, prefixes: tuple[str, ...], fresh: list[str]
) -> None:
    contract.assumptions = [a for a in contract.assumptions if not a.startswith(prefixes)] + fresh


# ══════════════════════════════════════════════════════════════════
# 3. Ambiguity Resolution (multi-turn, via interrupt)
# ══════════════════════════════════════════════════════════════════


async def ambiguity_resolution(state: GoalAgentState) -> dict:
    contract: AnalysisContract = state["contract"]
    pending = contract.pending_slots
    choices = family_choices_for(contract.hazard_objects)

    family_block = ""
    if choices and "target" in pending:
        family_block = "\n\nFor the `target` slot, these are the candidate data families. " + (
            "Offer exactly these as options, in this order, and carry each caveat over "
            "into the option's `implication`:\n"
            + "\n".join(f"- {c.label}: {c.caveat}" for c in choices)
            + "\nAlso offer a final option that uses all of them together, and mark it "
            "as recommended when they can cross-check each other."
        )

    batch: ClarificationBatch = await structured(ClarificationBatch).ainvoke(
        [
            ("system", prompts.clarification_prompt(contract.expertise, pending)),
            (
                "human",
                f"Original request: {state['original_request']}\n"
                f"Restatement: {contract.restatement}\n"
                f"Task intents: {contract.task_intent}\n"
                f"User role: {contract.user_role}\n\n"
                "Blocking slots and why they are blocking:\n"
                + "\n".join(f"- {name}: {contract.slots[name].blocking_reason}" for name in pending)
                + family_block,
            ),
        ]
    )

    questions = [q for q in batch.questions if q.slot in pending] or batch.questions

    # ── Stop here and hand control back to the user ─────────────────
    # LangGraph's interrupt() checkpoints the whole state; after the user
    # answers, execution resumes on the next line rather than replaying the
    # graph. This is the decisive reason for choosing LangGraph over a plain
    # agent loop, which would otherwise need a hand-rolled conversation state
    # machine.
    answer: str = interrupt(
        {
            "type": "clarification",
            "preamble": batch.preamble,
            "questions": [q.model_dump() for q in questions],
            "pending_slots": pending,
        }
    )

    rounds = state.get("clarification_rounds", 0) + 1
    interpretation: ClarificationInterpretation = await structured(
        ClarificationInterpretation
    ).ainvoke(
        [
            (
                "system",
                prompts.interpretation_prompt(
                    pending,
                    list(contract.slots),
                    [c.id for c in choices] if choices else None,
                ),
            ),
            (
                "human",
                "Questions asked:\n"
                + "\n".join(f"- [{q.slot}] {q.question}" for q in questions)
                + f"\n\nUser reply:\n{answer}",
            ),
        ]
    )

    for update in interpretation.updates:
        slot = contract.slots.get(update.slot)
        if slot is None:
            continue
        slot.value = update.value
        slot.confidence = update.confidence
        slot.source = "user_stated"

    if interpretation.user_declined:
        # The user handed the decision back. Stop asking and fall back to
        # documented defaults - pressing on would annoy them without improving
        # the contract.
        for name in contract.pending_slots:
            contract.slots[name].is_blocking = False
            contract.slots[
                name
            ].blocking_reason = (
                "User asked the agent to decide; falling back to the documented default."
            )

    await _ground_location(contract)

    contract.clarification_rounds = rounds
    contract.unresolved = [s for s in interpretation.still_unresolved if s in contract.slots]

    transcript = [
        {"role": "agent", "content": _render_questions(batch.preamble, questions)},
        {"role": "user", "content": answer},
    ]
    return {
        "contract": contract,
        "pending_questions": questions,
        "clarification_rounds": rounds,
        "transcript": transcript,
        "stage": "ambiguity_resolution",
    }


def _render_questions(preamble: str, questions: list[ClarificationQuestion]) -> str:
    lines = [preamble]
    for idx, q in enumerate(questions, 1):
        lines.append(f"\n{idx}. {q.question}")
        for opt in q.options:
            mark = "  [recommended]" if opt.recommended else ""
            imp = f" - {opt.implication}" if opt.implication else ""
            lines.append(f"   > {opt.label}{mark}{imp}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
# 4. Analysis Contract (finalise)
# ══════════════════════════════════════════════════════════════════


async def analysis_contract(state: GoalAgentState) -> dict:
    contract: AnalysisContract = state["contract"]

    for name, slot in contract.slots.items():
        if slot.is_filled:
            continue
        # Never default a blocking slot. Filling it would make the contract look
        # complete while the gap that actually changes the answer is still open.
        if slot.is_blocking:
            continue
        default = slot_default(name, contract.task_intent)
        if default is None:
            continue
        slot.value = default
        slot.source = "default"
        slot.confidence = 0.5
        note = f"Slot '{name}' not specified; using the neutral default '{default}'."
        if note not in contract.assumptions:
            contract.assumptions.append(note)

    # Blocking slots still empty after the round limit are reported honestly
    # rather than papered over. That is the whole point of reducing errors.
    for name in contract.pending_slots:
        note = (
            f"Slot '{name}' still undetermined after "
            f"{contract.clarification_rounds} clarification round(s)."
        )
        if note not in contract.unresolved:
            contract.unresolved.append(note)

    # Spatial assumptions are recomputed by _ground_location; nothing to add here.
    return {"contract": contract, "stage": "planning"}


# ══════════════════════════════════════════════════════════════════
# Downstream: the Planning Agent
#
# Everything below consumes the finished contract and nothing else. Keeping the
# boundary at a node edge rather than inside a function is the point: Task 1
# still never picks a dataset, and the contract is demonstrably sufficient to
# drive the next stage.
# ══════════════════════════════════════════════════════════════════


async def planning(state: GoalAgentState) -> dict:
    """Choose which data layers answer the contract. LLM proposes, registry validates."""
    contract: AnalysisContract = state["contract"]
    plan = await build_plan(contract)
    return {"plan": plan, "stage": "execution"}


async def execution(state: GoalAgentState) -> dict:
    """Load the selected snapshots, clip them to the contract's scope, hand them to the map."""
    contract: AnalysisContract = state["contract"]
    plan = state["plan"]
    layers = execute(plan, contract)
    return {
        "layers": layers,
        "layer_summary": summarise(layers, plan),
        "stage": "done",
    }


# ══════════════════════════════════════════════════════════════════
# Routing
# ══════════════════════════════════════════════════════════════════


def route_after_compile(state: GoalAgentState) -> str:
    contract: AnalysisContract = state["contract"]
    if contract.pending_slots and state.get("clarification_rounds", 0) < MAX_CLARIFICATION_ROUNDS:
        return "ambiguity_resolution"
    return "analysis_contract"


def route_after_contract(state: GoalAgentState) -> str:
    """Only run the Planning Agent on a contract that is actually ready.

    An incomplete contract still ships - with its gaps recorded - but running
    analysis on top of unresolved ambiguity is precisely the error this whole
    system exists to prevent.
    """
    contract: AnalysisContract = state["contract"]
    return "planning" if contract.ready_for_planning else "skip"


def route_after_clarify(state: GoalAgentState) -> str:
    contract: AnalysisContract = state["contract"]
    if contract.pending_slots and state.get("clarification_rounds", 0) < MAX_CLARIFICATION_ROUNDS:
        return "ambiguity_resolution"
    return "analysis_contract"
