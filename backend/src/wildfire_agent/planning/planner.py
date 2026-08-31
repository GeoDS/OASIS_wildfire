"""Contract -> execution plan.

**The LLM proposes, the registry validates.** Same division of labour as
upstream: the model does the part that needs judgement - reading the contract
and deciding which data layers actually answer it - and code enforces the parts
that must never vary. A proposal naming a capability that does not exist is
dropped; a proposal quietly overriding the data family the user chose is
rejected; a hazard object the model forgot is still reported as unmet.

That split is what lets the system claim autonomous layer selection without
betting the demo on it. If the model is unreachable or returns nothing usable,
`deterministic_plan` produces the same shape from a lookup and the run
continues.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from ..contract import AnalysisContract
from ..llm import structured
from ..taxonomy import HAZARD_OBJECTS, family_choices_for
from .capabilities import (
    CAPABILITIES,
    SHOWCASE_AREA,
    Capability,
    capabilities_for,
    is_served,
    missing_variables,
    renderer_coverage_for,
    unserved_variables,
)
from .models import ExecutionPlan, PlannedLayer, UnmetNeed

#: Words in `time_horizon` (or the original request) that mean "the past".
_HISTORICAL_RE = re.compile(
    r"\b(histor\w*|past|previous|since|prior|last \d+ years?|"
    r"\b(19|20)\d{2}\b|over time|trend)",
    re.IGNORECASE,
)


# ══════════════════════════════════════════════════════════════════
# What we ask the model for
# ══════════════════════════════════════════════════════════════════


class LayerChoice(BaseModel):
    capability_id: str = Field(description="Must be an id from the supplied catalogue")
    reason: str = Field(description="One sentence: why this layer answers the contract")


class PlanProposal(BaseModel):
    layers: list[LayerChoice] = Field(
        default_factory=list, description="Only what the contract genuinely needs"
    )
    reading_note: str = Field(
        "",
        description=(
            "One or two sentences telling the user how to read the resulting map, "
            "pitched at their expertise level. Name any trap in the data."
        ),
    )


def _catalogue_block() -> str:
    lines = []
    for cap in CAPABILITIES.values():
        family = f" · family `{cap.family}`" if cap.family else ""
        lines.append(
            f"- `{cap.id}` — {cap.title}. Serves `{cap.hazard_object}`{family}; "
            f"{cap.temporality}; {cap.geometry_type}. Caveat: {cap.caveat}"
        )
    return "\n".join(lines)


def _planner_prompt(contract: AnalysisContract) -> str:
    return f"""You are the **Planning Agent** in a multi-agent wildfire analyst system.

Task 1 has already defined the problem. Your job is to decide which of the available data
layers actually answer it, and nothing more.

Hard boundaries:
- Choose ONLY from the catalogue below, by exact `capability_id`. Never invent a source.
- Do NOT override the data family the user chose. If the contract's `target` names a family,
  respect it; that choice was the point of the clarification they were asked.
- Choose the minimum set that answers the question. Extra layers are clutter, not thoroughness.
- If nothing in the catalogue serves part of the request, leave it out. The system reports the
  gap honestly elsewhere; do not substitute something that merely looks similar.

### Available capabilities
{_catalogue_block()}

### Showcase area
{SHOWCASE_AREA["name"]} — {SHOWCASE_AREA["context"]}

### The user's expertise is `{contract.expertise}`
Pitch `reading_note` accordingly: plain language and explicit traps for `general`; operational
terms for `practitioner`; resolution, provenance and method for `expert`.
"""


def _contract_digest(contract: AnalysisContract) -> str:
    slots = "\n".join(
        f"- {name}: {slot.value or '(empty)'} [{slot.source}]"
        for name, slot in contract.slots.items()
    )
    return (
        f"Original request: {contract.original_request}\n"
        f"Restatement: {contract.restatement}\n"
        f"Task intent: {', '.join(contract.task_intent)}\n"
        f"User role: {contract.user_role}\n"
        f"Hazard objects declared: {', '.join(contract.hazard_objects) or '(none)'}\n"
        f"Slots:\n{slots}\n"
        f"Assumptions carried: {'; '.join(contract.assumptions) or '(none)'}"
    )


# ══════════════════════════════════════════════════════════════════
# Deterministic floor
# ══════════════════════════════════════════════════════════════════


def wants_history(contract: AnalysisContract) -> bool:
    slot = contract.slots.get("time_horizon")
    text = " ".join(filter(None, [slot.value if slot else None, contract.original_request]))
    return bool(_HISTORICAL_RE.search(text))


def selected_families(contract: AnalysisContract) -> set[str]:
    """Which data families the contract's `target` slot named.

    Task 1 refused to leave this ambiguous, which is precisely what makes the
    validation below possible rather than another guess.
    """
    target = contract.slots.get("target")
    value = (target.value or "").lower() if target else ""
    if not value:
        return set()

    chosen: set[str] = set()
    for choice in family_choices_for(contract.hazard_objects):
        if choice.id in value or any(k in value for k in choice.keywords):
            chosen.add(choice.id)
    return chosen


def _matches(cap: Capability, families: set[str], historical: bool) -> tuple[bool, str]:
    if cap.temporality == "historical" and not historical:
        return False, "the request is about current conditions, not past fires"
    if cap.temporality == "snapshot_current" and historical:
        return False, "the request is about past fires"

    if cap.family is None:
        return True, f"serves `{cap.hazard_object}`; no family choice applies"
    if not families:
        return True, (
            f"the contract named no data family, so both families for "
            f"`{cap.hazard_object}` are drawn rather than one being guessed"
        )
    if cap.family in families:
        return True, f"the contract asked for `{cap.family}`"
    return False, f"the contract asked for a different family than `{cap.family}`"


def deterministic_plan(contract: AnalysisContract) -> ExecutionPlan:
    """Lookup-only plan. The fallback, and the yardstick the proposal is checked against."""
    families = selected_families(contract)
    historical = wants_history(contract)

    layers: list[PlannedLayer] = []
    for hazard_object in contract.hazard_objects:
        for cap in capabilities_for(hazard_object):
            ok, why = _matches(cap, families, historical)
            if ok:
                layers.append(_to_planned(cap, why))
    return ExecutionPlan(layers=layers, unmet=[], notes=[])


def _to_planned(cap: Capability, reason: str) -> PlannedLayer:
    return PlannedLayer(
        capability_id=cap.id,
        title=cap.title,
        hazard_object=cap.hazard_object,
        family=cap.family,
        geometry_type=cap.geometry_type,
        caveat=cap.caveat,
        reason=reason,
    )


# ══════════════════════════════════════════════════════════════════
# Validation
# ══════════════════════════════════════════════════════════════════


def validate_proposal(contract: AnalysisContract, proposal: PlanProposal) -> ExecutionPlan:
    """Turn a model proposal into a plan, dropping anything it was not allowed to do."""
    families = selected_families(contract)
    declared = set(contract.hazard_objects)

    layers: list[PlannedLayer] = []
    notes: list[str] = []
    seen: set[str] = set()

    for choice in proposal.layers:
        cap = CAPABILITIES.get(choice.capability_id)
        if cap is None or cap.id in seen:
            continue  # invented or duplicated - drop without comment
        seen.add(cap.id)

        # The family the user picked is not the model's to revise.
        if cap.family and families and cap.family not in families:
            notes.append(
                f"Dropped {cap.title}: the user chose a different data family, and that "
                f"choice is not the planner's to override."
            )
            continue

        layers.append(_to_planned(cap, choice.reason))

    # Safety net, scoped deliberately. A data family the user was *interrupted to
    # choose* is a floor the planner may not lower: dropping one silently changes
    # the answer the user asked for. Everything else - which hazard objects are
    # worth drawing at all, whether history is relevant - stays the model's call,
    # which is where its judgement actually earns its keep.
    historical = wants_history(contract)
    for hazard_object in declared:
        for cap in capabilities_for(hazard_object):
            if not cap.family or cap.family not in families or cap.id in seen:
                continue
            fits, _ = _matches(cap, families, historical)
            if not fits:
                continue  # right family, wrong time window - not the floor
            seen.add(cap.id)
            layers.append(
                _to_planned(
                    cap,
                    f"the user explicitly chose `{cap.family}`, so it is drawn "
                    f"regardless of the proposal",
                )
            )
            notes.append(
                f"Added {cap.title}: the user chose this data family and the proposal left it out."
            )

    # A proposal that ignores the requested time window is answering a different
    # question. Drop those picks and say so rather than quietly presenting
    # today's perimeter as a twenty-year history.
    kept: list[PlannedLayer] = []
    for layer in layers:
        cap = CAPABILITIES[layer.capability_id]
        fits, why = _matches(cap, families, historical)
        if fits:
            kept.append(layer)
        else:
            notes.append(f"Dropped {cap.title}: {why}.")
    layers = kept

    # Two different gaps, reported differently. A hazard object with no
    # capability at all is missing wholesale; one whose selected layers cover
    # only part of its required variables is a partial gap, and naming which
    # variables are absent is what lets an outside source be asked for them.
    unmet = []
    chosen_ids = [layer.capability_id for layer in layers]
    for ho in declared:
        label = (HAZARD_OBJECTS[ho].label if ho in HAZARD_OBJECTS else ho).lower()
        if not capabilities_for(ho):
            unmet.append(
                UnmetNeed(
                    hazard_object=ho,
                    reason=(
                        f"No data source in this deployment covers {label}. "
                        f"The contract is complete; the capability is missing."
                    ),
                )
            )
            continue
        absent = missing_variables(ho, chosen_ids)
        if absent:
            named = ", ".join(absent)
            unmet.append(
                UnmetNeed(
                    hazard_object=ho,
                    reason=(
                        f"The selected layers for {label} do not carry {named}. "
                        "Everything else the object needs is present."
                    ),
                    missing_variables=absent,
                    # If nothing in the deployment supplies it, only an outside
                    # source can; if something does, the planner simply did not
                    # pick it, and fetching would paper over that.
                    fillable_externally=bool(missing_variables(ho)),
                )
            )

    if proposal.reading_note:
        notes.insert(0, proposal.reading_note)

    if any(layer.capability_id != "historical_fire_perimeters" for layer in layers):
        notes.append(
            f"Showcase dataset: {SHOWCASE_AREA['context']} Figures describe that event, "
            f"not conditions today."
        )
    if {layer.family for layer in layers} >= {"official_fire_perimeters", "satellite_hotspots"}:
        notes.append(
            "Both fire data families are drawn. Where satellite detections fall outside the "
            "confirmed perimeter, treat them as unverified heat rather than as fire."
        )

    return ExecutionPlan(layers=layers, unmet=unmet, notes=notes)


def deployment_unmet_needs(contract: AnalysisContract) -> list[UnmetNeed]:
    """Capability gaps for a contract that the renderer path will answer.

    A different question from the one `validate_proposal` answers, and the two
    must not be confused. That one reports what a *chosen set of registry
    layers* failed to carry, which is a fact about one plan. This reports what
    *this deployment* cannot produce by any path, which is a fact about the
    system - the answer walkthrough scenario 3 asks for, and the one the Limits
    tab is meant to show.

    It needs no model. That is what makes it affordable on a turn whose layer
    selection is skipped entirely - see `nodes.planning`.
    """
    unmet: list[UnmetNeed] = []
    for ho in contract.hazard_objects:
        label = (HAZARD_OBJECTS[ho].label if ho in HAZARD_OBJECTS else ho).lower()

        if not is_served(ho):
            unmet.append(
                UnmetNeed(
                    hazard_object=ho,
                    reason=(
                        f"No data source in this deployment covers {label}. "
                        f"The contract is complete; the capability is missing."
                    ),
                )
            )
            continue

        absent = unserved_variables(ho)
        if not absent:
            continue

        coverage = renderer_coverage_for(ho)
        served_by = coverage.served_by if coverage else "the layers this deployment holds"
        unmet.append(
            UnmetNeed(
                hazard_object=ho,
                reason=(
                    f"{label.capitalize()} is served by {served_by}, which does not "
                    f"carry {', '.join(absent)}. Everything else the object needs is "
                    f"available."
                ),
                missing_variables=absent,
                # Nothing this deployment can reach supplies these, outside
                # sources included - the approval-gated ones are already counted
                # as coverage. Offering to fetch them would be an offer that
                # cannot be honoured.
                fillable_externally=False,
            )
        )
    return unmet


async def build_plan(contract: AnalysisContract) -> ExecutionPlan:
    """Ask the model which layers to use, then validate the answer."""
    try:
        proposal: PlanProposal = await structured(PlanProposal).ainvoke(
            [
                ("system", _planner_prompt(contract)),
                ("human", _contract_digest(contract)),
            ]
        )
    except Exception as exc:  # noqa: BLE001 - the demo must survive a model outage
        plan = deterministic_plan(contract)
        plan.notes.append(
            f"Layer selection fell back to the deterministic registry match "
            f"({type(exc).__name__}); the model was not reachable."
        )
        plan.unmet = [
            UnmetNeed(
                hazard_object=ho,
                reason=f"No data source in this deployment covers `{ho}`.",
            )
            for ho in contract.hazard_objects
            if not capabilities_for(ho)
        ]
        return plan

    plan = validate_proposal(contract, proposal)
    if plan.is_empty and contract.hazard_objects:
        fallback = deterministic_plan(contract)
        if fallback.layers:
            fallback.notes.append(
                "The proposal selected no usable layer, so the registry match was used instead."
            )
            fallback.unmet = plan.unmet
            return fallback
    return plan
