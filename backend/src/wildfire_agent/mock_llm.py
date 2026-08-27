"""Deterministic stub used when `LLM_PROVIDER=mock`.

Why it exists:
1. The frontend can be built against the **real SSE pipeline** without waiting
   for an API key, so there is only ever one code path.
2. A fallback for demo day if the network or the quota gives out.
3. Tests and CI stop burning tokens.

**It is not an LLM** - it is a bag of keyword rules. Honesty requirement:
`/api/health` and the CLI both report the provider as `mock`, and the UI shows a
badge. It must never pass itself off as real inference.
"""

from __future__ import annotations

import re
from typing import Any

from .contract import ClarificationOption, ClarificationQuestion
from .graph.models import (
    ClarificationBatch,
    ClarificationInterpretation,
    CompiledAssumption,
    CompiledTask,
    RequirementUnderstanding,
    SlotDecision,
    SlotUpdate,
)
from .planning.planner import LayerChoice, PlanProposal
from .taxonomy import required_slots, slot_default

# ── Keyword tables (enough for a demo) ────────────────────────────

_INTENT_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("prediction", ("spread", "next 24", "forecast", "may happen", "predict")),
    ("decision_support", ("should", "prioriti", "recommend", "rank", "allocate")),
    ("evaluation_adaptation", ("did the", "worked", "effective", "evaluate", "adjust")),
    (
        "assessment",
        ("exposed", "exposure", "risk", "vulnerab", "how serious", "impact", "affect"),
    ),
    (
        "observation",
        ("where are", "where is", "what is happening", "show", "current", "active"),
    ),
]

_HAZARD_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("active_fire", ("fire", "wildfire", "hotspot")),
    ("smoke_plume", ("smoke", "air quality", "pm2.5")),
    ("fire_weather", ("wind", "weather", "humidity")),
    ("fire_spread", ("spread")),
    ("fuel", ("fuel", "vegetation")),
    ("exposure", ("communit", "population", "exposed", "people", "house")),
    ("vulnerability", ("vulnerab", "elderly", "svi")),
    ("infrastructure", ("road", "power", "water main")),
    ("critical_facility", ("hospital", "shelter", "fire station", "school")),
    ("evacuation", ("evacuat", "escape", "route")),
    ("mitigation_treatment", ("treatment", "prescribed burn", "mitigation")),
]

_EXPERT_MARKERS = (
    "dataset",
    "api",
    "resolution",
    "parameter",
    "model",
    "crs",
    "geojson",
)
_PRACTITIONER_MARKERS = (
    "planning",
    "mitigation",
    "policy",
    "operational",
    "jurisdiction",
)

_ROLE_MARKERS: list[tuple[str, tuple[str, ...]]] = [
    ("resident_general_public", ("my house", "my home", "my family")),
    ("government_community_planner", ("communit", "policy", "prioriti", "jurisdiction")),
    ("researcher_gis_analyst", _EXPERT_MARKERS),
    ("emergency_responder", ("incident", "responder", "deploy", "crew")),
]

_LOCATION_RE = re.compile(r"(?:near|around|in|within)\s+([A-Z][A-Za-z\s]{2,30})")
_MY_PLACE_RE = re.compile(r"my (house|home|neighborhood|area)", re.IGNORECASE)
_TIME_RE = re.compile(r"(next \d+\s*(?:hours?|days?)|today|tonight)", re.IGNORECASE)


def _find_human(messages: Any) -> str:
    """Pull the human turn out of a LangChain message sequence."""
    texts = []
    for msg in messages or []:
        if isinstance(msg, tuple) and len(msg) == 2:
            role, content = msg
            if role == "human":
                texts.append(str(content))
        elif getattr(msg, "type", None) == "human":
            texts.append(str(msg.content))
    return "\n".join(texts)


def _find_system(messages: Any) -> str:
    for msg in messages or []:
        if isinstance(msg, tuple) and len(msg) == 2 and msg[0] == "system":
            return str(msg[1])
        if getattr(msg, "type", None) == "system":
            return str(msg.content)
    return ""


def _detect_intents(text: str) -> list[str]:
    low = text.lower()
    hits = [intent for intent, kws in _INTENT_KEYWORDS if any(k in low for k in kws)]
    # When prediction or decision support fires, observation is usually noise.
    if len(hits) > 1 and "observation" in hits:
        hits.remove("observation")
    return hits or ["observation"]


def _detect_hazards(text: str) -> list[str]:
    low = text.lower()
    return [h for h, kws in _HAZARD_KEYWORDS if any(k in low for k in kws)] or ["active_fire"]


def _detect_expertise(text: str) -> str:
    low = text.lower()
    if any(k in low for k in _EXPERT_MARKERS):
        return "expert"
    if any(k in low for k in _PRACTITIONER_MARKERS):
        return "practitioner"
    return "general"


def _detect_role(text: str) -> str:
    low = text.lower()
    for role, markers in _ROLE_MARKERS:
        if any(k in low for k in markers):
            return role
    return "unknown"


def _detect_location(text: str) -> str | None:
    if _MY_PLACE_RE.search(text):
        return "near my house"
    match = _LOCATION_RE.search(text)
    return match.group(0).strip() if match else None


# ══════════════════════════════════════════════════════════════════
# One stub per schema
# ══════════════════════════════════════════════════════════════════


def _mock_understanding(messages: Any) -> RequirementUnderstanding:
    request = _find_human(messages).replace("User request:", "").strip()
    intents = _detect_intents(request)
    time_match = _TIME_RE.search(request)

    return RequirementUnderstanding(
        restatement=f"[mock] The user wants to know about: {request[:80]}",
        task_intent=intents,
        intent_reason="[mock] keyword match, not real inference",
        expertise=_detect_expertise(request),
        expertise_reason="[mock] based on the density of technical terms",
        user_role=_detect_role(request),
        role_reason="[mock] kept as unknown when there is no clear signal",
        role_materially_changes_analysis=False,
        hazard_objects=_detect_hazards(request),
        hazard_reason="[mock] keyword match",
        raw_location=_detect_location(request),
        raw_time_horizon=time_match.group(0) if time_match else None,
    )


_RAW_LINE_RE = re.compile(r"^-\s*(\w+):\s*'?\"?(.*?)'?\"?$", re.MULTILINE)


def _mock_compiled(messages: Any) -> CompiledTask:
    human = _find_human(messages)
    intents = _detect_intents(human)
    baseline = required_slots(intents)
    stated = {m.group(1): m.group(2) for m in _RAW_LINE_RE.finditer(human)}

    decisions: list[SlotDecision] = []
    assumptions: list[CompiledAssumption] = []

    for name, req in baseline.items():
        value = stated.get(name)
        if value:
            decisions.append(
                SlotDecision(
                    slot=name,
                    value=value,
                    source="user_stated",
                    confidence=0.9,
                    is_blocking=req.requirement == "B",
                    blocking_reason=f"[mock] matrix baseline {req.requirement}, user supplied it",
                )
            )
            continue

        blocking = req.requirement == "B"
        default = slot_default(name, intents) if not blocking else None
        if default:
            assumptions.append(
                CompiledAssumption(
                    slot=name,
                    text=f"[mock] '{name}' unspecified, using default '{default}'",
                )
            )

        decisions.append(
            SlotDecision(
                slot=name,
                value=default,
                source="default",
                confidence=0.5 if default else 0.0,
                is_blocking=blocking,
                blocking_reason=f"[mock] matrix baseline {req.requirement}",
            )
        )

    # Note: the non-interchangeable data family rule is NOT simulated here.
    # `nodes.enforce_family_disambiguation` applies it deterministically after
    # this stage, for the mock and for real providers alike.
    return CompiledTask(slots=decisions, assumptions=assumptions)


_PENDING_RE = re.compile(r"blocking and still empty:\s*(.+)")

_CANNED_QUESTIONS: dict[str, ClarificationQuestion] = {
    "target": ClarificationQuestion(
        slot="target",
        question=(
            "Which kind of fire data do you mean? These two mean different things and "
            "will give noticeably different answers."
        ),
        options=[
            ClarificationOption(
                label="Officially confirmed fire perimeters",
                implication=(
                    "Verified by fire agencies, so the most reliable, but slow to update "
                    "and often empty for any given area."
                ),
            ),
            ClarificationOption(
                label="Satellite thermal detections",
                implication=(
                    "Much timelier, but a detection may be an agricultural burn or an "
                    "industrial heat source rather than a wildfire."
                ),
            ),
            ClarificationOption(
                label="Both, so they cross-check each other",
                implication="Recommended when you want the timeliness without the false alarms.",
                recommended=True,
            ),
        ],
    ),
    "location": ClarificationQuestion(
        slot="location",
        question="Where exactly? I need a place name or address I can locate on the map.",
        options=[
            ClarificationOption(label="Altadena", recommended=True),
            ClarificationOption(
                label="Altadena and the surrounding foothills",
                implication="Wider area, coarser result",
            ),
        ],
    ),
    "comparison_basis": ClarificationQuestion(
        slot="comparison_basis",
        question="What should 'most affected' be ranked by?",
        options=[
            ClarificationOption(
                label="Absolute exposed population",
                implication="Large communities will always come out on top",
            ),
            ClarificationOption(
                label="Exposure per capita", implication="Removes the effect of community size"
            ),
            ClarificationOption(
                label="Exposure weighted by social vulnerability",
                implication=(
                    "Surfaces places that are both badly hit and slow to recover; usually "
                    "the best fit for allocating resources."
                ),
                recommended=True,
            ),
        ],
    ),
    "intervention": ClarificationQuestion(
        slot="intervention",
        question="Which intervention is being evaluated, and against what baseline period?",
    ),
    "time_horizon": ClarificationQuestion(
        slot="time_horizon",
        question="How far ahead should the prediction run?",
        options=[
            ClarificationOption(label="Next 24 hours", recommended=True),
            ClarificationOption(label="Next 72 hours"),
        ],
    ),
}


def _mock_batch(messages: Any) -> ClarificationBatch:
    match = _PENDING_RE.search(_find_system(messages))
    pending = [s.strip() for s in match.group(1).split(",")] if match else []

    questions = [
        _CANNED_QUESTIONS.get(
            slot,
            ClarificationQuestion(slot=slot, question=f"Please supply a value for '{slot}'."),
        )
        for slot in pending
    ]
    return ClarificationBatch(
        preamble=(
            f"[mock] I need to settle {len(questions)} thing(s) before handing this "
            f"to the analysis stage:"
        ),
        questions=questions,
    )


_KM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*km")


def _mock_interpretation(messages: Any) -> ClarificationInterpretation:
    human = _find_human(messages)
    reply = human.split("User reply:")[-1].strip().lower()
    asked = re.findall(r"-\s*\[(\w+)\]", human)

    if any(k in reply for k in ("you decide", "whatever", "i don't know")):
        return ClarificationInterpretation(user_declined=True)

    updates: list[SlotUpdate] = []

    if "target" in asked:
        both = any(k in reply for k in ("both"))
        official = any(k in reply for k in ("official", "perimeter"))
        satellite = any(k in reply for k in ("satellite", "hotspot", "thermal"))
        if both or (official and satellite):
            value = "official_fire_perimeters + satellite_hotspots"
        elif official:
            value = "official_fire_perimeters"
        elif satellite:
            value = "satellite_hotspots"
        else:
            value = ""
        if value:
            updates.append(SlotUpdate(slot="target", value=value))

    km = _KM_RE.search(reply)
    if km:
        updates.append(
            SlotUpdate(slot="location", value=f"{km.group(1)} km buffer around Altadena, CA")
        )
    elif "location" in asked and ("altadena" in reply or "pasadena" in reply):
        updates.append(SlotUpdate(slot="location", value=reply[:60]))

    for slot in asked:
        if slot in {"target", "location"} or any(u.slot == slot for u in updates):
            continue
        # Everything else is taken verbatim; the mock does no normalisation.
        updates.append(SlotUpdate(slot=slot, value=reply[:60] or "unspecified", confidence=0.7))

    return ClarificationInterpretation(updates=updates)


def _mock_plan(messages: Any) -> PlanProposal:
    """Mirror the deterministic registry match, so the mock exercises the same
    validation path a real proposal goes through."""
    human = _find_human(messages).lower()
    historical = any(k in human for k in ("histor", "past", "previous", "since", "trend"))

    if historical:
        return PlanProposal(
            layers=[
                LayerChoice(
                    capability_id="historical_fire_perimeters",
                    reason="[mock] the request concerns past fires",
                )
            ],
            reading_note="[mock] Past fire footprints only; no burn severity is included.",
        )

    wants_official = "official" in human or "perimeter" in human
    wants_satellite = "satellite" in human or "hotspot" in human or "thermal" in human
    if not (wants_official or wants_satellite):
        wants_official = wants_satellite = True

    layers = []
    if wants_official:
        layers.append(
            LayerChoice(
                capability_id="official_fire_perimeters",
                reason="[mock] the contract asked for agency-confirmed perimeters",
            )
        )
    if wants_satellite:
        layers.append(
            LayerChoice(
                capability_id="satellite_hotspots",
                reason="[mock] the contract asked for satellite detections",
            )
        )
    return PlanProposal(
        layers=layers,
        reading_note=(
            "[mock] Detections outside the confirmed perimeter are unverified heat, "
            "not necessarily wildfire."
        ),
    )


_DISPATCH = {
    PlanProposal: _mock_plan,
    RequirementUnderstanding: _mock_understanding,
    CompiledTask: _mock_compiled,
    ClarificationBatch: _mock_batch,
    ClarificationInterpretation: _mock_interpretation,
}


class MockStructuredRunnable:
    """What `structured(schema)` returns while the mock provider is active."""

    def __init__(self, schema: type):
        if schema not in _DISPATCH:
            raise NotImplementedError(f"mock provider does not cover schema {schema.__name__}")
        self._schema = schema

    def invoke(self, messages: Any, **_kwargs: Any):
        return _DISPATCH[self._schema](messages)

    async def ainvoke(self, messages: Any, **_kwargs: Any):
        return _DISPATCH[self._schema](messages)
