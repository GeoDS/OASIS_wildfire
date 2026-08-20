"""Domain taxonomy: five task intents, three expertise levels, eight user roles,
thirteen hazard objects, and the intent -> slot requirement matrix.

**Single source of truth.** The enum blocks handed to the LLM inside prompts are
generated from this module. Never retype an enum inside a prompt string: a hand
copy drifts from the code the first time either side changes.

Companion document: `docs/01-taxonomy.md`, which carries the design rationale
and the grounds for each decision taken here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# ══════════════════════════════════════════════════════════════════
# The three orthogonal dimensions: intent, expertise, role.
# Independent of one another - a resident can be an expert, a researcher can
# ask an observation question.
# ══════════════════════════════════════════════════════════════════

TaskIntent = Literal[
    "observation",
    "assessment",
    "prediction",
    "decision_support",
    "evaluation_adaptation",
]

ExpertiseLevel = Literal["general", "practitioner", "expert"]

UserRole = Literal[
    "resident_general_public",
    "government_community_planner",
    "emergency_responder",
    "land_forest_manager",
    "researcher_gis_analyst",
    "infrastructure_operator",
    "insurance_financial",
    "unknown",
]

INTENT_DEFINITIONS: dict[str, str] = {
    "observation": (
        "Observation / Situation Awareness - retrieve, display, or summarize the current "
        'or observed state. Answers "What is happening now?"'
    ),
    "assessment": (
        "Assessment - measure or interpret hazard, exposure, vulnerability, impact, or risk. "
        'Answers "What does it mean, and how serious is it?"'
    ),
    "prediction": (
        "Prediction - estimate a future wildfire, smoke, or impact state using trends, "
        'forecasts, or models. Answers "What may happen next?"'
    ),
    "decision_support": (
        "Decision Support - compare, rank, or recommend actions, locations, routes, or "
        'resource allocations. Answers "What should be decided or prioritized?"'
    ),
    "evaluation_adaptation": (
        "Evaluation / Adaptation - assess whether an intervention worked and whether a plan "
        'should be adjusted using new evidence. Answers "Did it work, and should we change '
        'course?"'
    ),
}

EXPERTISE_DEFINITIONS: dict[str, str] = {
    "general": (
        "Little wildfire, GIS, or data background. Use plain language, explain uncertainty and "
        "limitations, offer concrete choices, emphasize understandable maps and summaries."
    ),
    "practitioner": (
        "Uses wildfire or hazard information in planning or operations but may not need code. "
        "Use domain terms and operational indicators; provide assumptions, data sources, "
        "geographic scale, and decision-oriented outputs."
    ),
    "expert": (
        "Researcher, GIS analyst, modeler, or technical developer. Provide datasets/APIs, "
        "spatial and temporal resolution, parameters, methods, provenance, uncertainty, and "
        "reproducible workflow or code when useful."
    ),
}

ROLE_DEFINITIONS: dict[str, str] = {
    "resident_general_public": (
        "Personal safety, nearby fire or smoke, understandable risk, and official information."
    ),
    "government_community_planner": (
        "Community risk, policy, infrastructure, mitigation priorities, and area comparisons."
    ),
    "emergency_responder": (
        "Current conditions, threatened assets, access, operational updates, resource priorities."
    ),
    "land_forest_manager": (
        "Fuels, prescribed fire, treatment planning, ecological effects, long-term risk reduction."
    ),
    "researcher_gis_analyst": (
        "Datasets, methods, model performance, uncertainty, reproducibility, and code."
    ),
    "infrastructure_operator": (
        "Threats to roads, power, communications, water, and service continuity."
    ),
    "insurance_financial": (
        "Asset exposure, expected loss, portfolio aggregation, and uncertainty."
    ),
    "unknown": (
        "Use a neutral default and infer only when the request clearly supports it; "
        "ask only when role materially changes the analysis."
    ),
}

# ══════════════════════════════════════════════════════════════════
# Hazard objects - thirteen, in three tiers. See docs/01-taxonomy.md section 1.
# Declared only down to the *data family* level; picking a concrete API endpoint
# is the downstream Planning Agent's job.
# ══════════════════════════════════════════════════════════════════

HazardLayer = Literal["hazard", "exposure", "action"]


@dataclass(frozen=True)
class DataFamilyChoice:
    """One of several data families that answer the same question differently.

    This exists because some hazard objects are served by families whose meanings
    are **not interchangeable**. Picking the wrong one does not degrade the answer,
    it invalidates it. Encoding the choice here rather than hoping the model
    remembers it is deliberate: domain facts belong in the domain model.
    """

    id: str
    label: str
    #: Why choosing this family rather than a sibling changes the conclusion.
    caveat: str
    #: Words that show the user has actually named *this* family.
    #: Declared explicitly rather than derived from `id`, because deriving them
    #: produces useless tokens: 'official_fire_perimeters' yields 'fire', and
    #: then the phrase "active fires" counts as a disambiguation when it is
    #: precisely the ambiguous wording we need to resolve.
    keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class HazardObject:
    id: str
    layer: HazardLayer
    label: str
    required_variables: tuple[str, ...]
    dataset_families: tuple[str, ...]
    #: Non-interchangeable data families. When non-empty, the `target` slot must
    #: name one of them before the contract can be handed off.
    family_choices: tuple[DataFamilyChoice, ...] = ()


HAZARD_OBJECTS: dict[str, HazardObject] = {
    ho.id: ho
    for ho in (
        # ── hazard tier: the hazard itself ──────────────────────────
        HazardObject(
            "active_fire",
            "hazard",
            "Active fire",
            (
                "fire perimeter",
                "hotspot location",
                "detection time",
                "detection confidence",
                "fire size",
            ),
            (
                "official fire perimeters (IRWIN/WFIGS family)",
                "satellite hotspots (VIIRS/MODIS/HMS family)",
            ),
            (
                DataFamilyChoice(
                    "official_fire_perimeters",
                    "Officially confirmed fire perimeters",
                    "Verified by fire agencies, so the most reliable, but slow to update "
                    "and frequently empty for any given area.",
                    ("official", "confirmed", "perimeter", "wfigs", "irwin"),
                ),
                DataFamilyChoice(
                    "satellite_hotspots",
                    "Satellite thermal detections",
                    "Near real time, but a detection is only a heat signature: it may be "
                    "an agricultural burn or an industrial source, not a wildfire.",
                    ("satellite", "hotspot", "hot spot", "thermal", "viirs", "modis", "hms"),
                ),
            ),
        ),
        HazardObject(
            "fire_spread",
            "hazard",
            "Fire spread",
            ("rate of spread", "spread direction", "fire front position", "time step"),
            ("fire behaviour models (FARSITE/FlamMap family)", "time-series hotspot differencing"),
        ),
        HazardObject(
            "smoke_plume",
            "hazard",
            "Smoke and air quality",
            ("plume polygon", "density class", "PM2.5", "observation time"),
            (
                "satellite smoke analysis (HMS family)",
                "air quality monitoring networks",
                "smoke dispersion models",
            ),
            (
                DataFamilyChoice(
                    "satellite_plume_extent",
                    "Satellite plume extent",
                    "Shows where smoke is overhead across a wide area, but says nothing "
                    "about concentration at ground level where people breathe.",
                    ("plume", "satellite", "extent", "overhead"),
                ),
                DataFamilyChoice(
                    "ground_air_quality",
                    "Ground monitor air quality",
                    "Measures what people actually breathe, but only at sparse station "
                    "locations, so it misses gaps between monitors.",
                    ("monitor", "station", "air quality", "pm2.5", "ground"),
                ),
            ),
        ),
        HazardObject(
            "fire_weather",
            "hazard",
            "Fire weather",
            (
                "wind speed",
                "wind direction",
                "temperature",
                "relative humidity",
                "fire danger index",
            ),
            ("weather forecast services (NWS family)", "fire danger rating products"),
        ),
        HazardObject(
            "fuel",
            "hazard",
            "Fuels",
            (
                "fuel model",
                "vegetation type",
                "fuel load",
                "fuel moisture",
                "canopy characteristics",
            ),
            ("land cover / fuel maps (LANDFIRE family)", "vegetation index remote sensing"),
        ),
        # ── exposure tier: who or what is affected ──────────────────
        HazardObject(
            "exposure",
            "exposure",
            "Population and building exposure",
            ("population count", "building footprints", "housing density", "WUI boundary"),
            ("census demographics", "building footprints", "WUI layers"),
        ),
        HazardObject(
            "vulnerability",
            "exposure",
            "Social vulnerability",
            (
                "age structure",
                "income",
                "no-vehicle households",
                "language isolation",
                "mobility limitation",
            ),
            ("social vulnerability indices (SVI/CDC family)", "census demographics"),
        ),
        HazardObject(
            "infrastructure",
            "exposure",
            "Lifeline infrastructure",
            ("road network", "power lines", "communication sites", "water facilities"),
            (
                "road networks (TIGER family)",
                "utility assets",
                "critical infrastructure (HIFLD family)",
            ),
        ),
        HazardObject(
            "critical_facility",
            "exposure",
            "Critical facilities",
            ("hospital/fire/police/school/shelter locations", "capacity"),
            ("POI and facility point data (HIFLD/OSM family)",),
        ),
        HazardObject(
            "ecological_asset",
            "exposure",
            "Ecological and water assets",
            ("watershed", "habitat", "protected area", "soil erosion risk"),
            ("protected area boundaries", "watershed layers", "habitat maps"),
        ),
        # ── action tier: what people do about it ────────────────────
        HazardObject(
            "evacuation",
            "action",
            "Evacuation",
            (
                "evacuation zone",
                "evacuation route",
                "network accessibility",
                "travel time",
                "shelter",
            ),
            ("official evacuation orders", "road network + routing", "accessibility analysis"),
        ),
        HazardObject(
            "suppression_resource",
            "action",
            "Suppression resources",
            (
                "crew/equipment location and count",
                "control lines",
                "water sources",
                "response time",
            ),
            ("incident resource summaries (ICS/SIT family)", "water source inventories"),
        ),
        HazardObject(
            "mitigation_treatment",
            "action",
            "Mitigation and fuel treatment",
            (
                "treatment unit boundary",
                "treatment type and year",
                "prescribed burn records",
                "priority",
            ),
            ("treatment records (NFPORS family)", "forest management plans"),
        ),
    )
}


def family_choices_for(hazard_object_ids: list[str]) -> list[DataFamilyChoice]:
    """Collect every non-interchangeable data family choice these objects imply."""
    choices: list[DataFamilyChoice] = []
    seen: set[str] = set()
    for ho_id in hazard_object_ids:
        ho = HAZARD_OBJECTS.get(ho_id)
        if ho is None:
            continue
        for choice in ho.family_choices:
            if choice.id not in seen:
                seen.add(choice.id)
                choices.append(choice)
    return choices


# ══════════════════════════════════════════════════════════════════
# Slots and the intent -> slot requirement matrix.
# See docs/01-taxonomy.md sections 2 and 3.
# ══════════════════════════════════════════════════════════════════

SLOT_DEFINITIONS: dict[str, str] = {
    "location": "Geographic scope of the analysis. The only slot that needs real geocoding.",
    "time_horizon": "Temporal reference or window, e.g. 'now', 'next 24h', '2024 fire season'.",
    "target": "The receptor or object being analysed, e.g. communities, my house, roads.",
    "requested_output": "Desired product form: map / ranking / report / alert.",
    "threshold": "Cut-off that defines 'high' or 'serious', e.g. PM2.5 > 35 or within 5 km.",
    "comparison_basis": "The criterion used to rank or compare options.",
    "intervention": "The intervention being evaluated, plus its baseline for comparison.",
    "scenario": "Assumed conditions for a prediction, e.g. current forecast wind vs wind +20%.",
}

#: B = blocking (must be known) - D = defaultable (fall back, but record it in
#: `assumptions`) - O = optional (use if present, otherwise ignore)
Requirement = Literal["B", "D", "O"]

INTENT_SLOT_MATRIX: dict[str, dict[str, Requirement]] = {
    "observation": {
        "location": "B",
        "time_horizon": "D",
        "target": "D",
        "requested_output": "D",
    },
    "assessment": {
        "location": "B",
        "time_horizon": "D",
        "target": "B",
        "requested_output": "D",
        "threshold": "O",
    },
    "prediction": {
        "location": "B",
        "time_horizon": "B",
        "target": "D",
        "requested_output": "D",
        "scenario": "O",
    },
    "decision_support": {
        "location": "B",
        "time_horizon": "D",
        "target": "B",
        "requested_output": "D",
        "threshold": "O",
        "comparison_basis": "B",
    },
    "evaluation_adaptation": {
        "location": "B",
        "time_horizon": "B",
        "target": "B",
        "requested_output": "D",
        "threshold": "O",
        "intervention": "B",
    },
}

#: Neutral fallback used when a defaultable slot is missing (intent-independent).
SLOT_DEFAULTS: dict[str, str] = {
    "time_horizon": "now",
    "target": "all relevant features in scope",
    "requested_output": "map",
}

#: Some defaults depend on intent - decision support wants a ranking, not just a
#: picture. Key order is precedence: the first matching intent wins.
INTENT_SLOT_DEFAULT_OVERRIDES: dict[str, dict[str, str]] = {
    "decision_support": {"requested_output": "ranking + map"},
    "evaluation_adaptation": {"requested_output": "report + map"},
}


def slot_default(slot: str, intents: list[str] | None = None) -> str | None:
    """Neutral default for a slot, with per-intent overrides applied.

    Returns None when the slot has no default: it is either blocking (must be
    asked) or optional (safe to ignore). Neither case may be filled silently.
    """
    for intent, overrides in INTENT_SLOT_DEFAULT_OVERRIDES.items():
        if intents and intent in intents and slot in overrides:
            return overrides[slot]
    return SLOT_DEFAULTS.get(slot)


@dataclass
class SlotRequirement:
    slot: str
    requirement: Requirement
    #: Which intents pulled this slot in (used to explain a union).
    from_intents: list[str] = field(default_factory=list)


def required_slots(intents: list[str]) -> dict[str, SlotRequirement]:
    """Union of slot requirements across intents, keeping the strictest level.

    This is the **baseline** for Ambiguity Resolution. The three override rules
    in docs/01-taxonomy.md section 3 may promote or demote a slot, but every
    deviation has to carry a `blocking_reason`.
    """
    strictness = {"B": 3, "D": 2, "O": 1}
    merged: dict[str, SlotRequirement] = {}
    for intent in intents:
        for slot, req in INTENT_SLOT_MATRIX.get(intent, {}).items():
            existing = merged.get(slot)
            if existing is None:
                merged[slot] = SlotRequirement(slot, req, [intent])
            else:
                existing.from_intents.append(intent)
                if strictness[req] > strictness[existing.requirement]:
                    existing.requirement = req
    return merged


# ══════════════════════════════════════════════════════════════════
# Prompt fragment builders - keep prompts from drifting away from the code
# ══════════════════════════════════════════════════════════════════


def _bullets(mapping: dict[str, str]) -> str:
    return "\n".join(f"- `{k}`: {v}" for k, v in mapping.items())


def intents_prompt_block() -> str:
    return _bullets(INTENT_DEFINITIONS)


def expertise_prompt_block() -> str:
    return _bullets(EXPERTISE_DEFINITIONS)


def roles_prompt_block() -> str:
    return _bullets(ROLE_DEFINITIONS)


def hazard_objects_prompt_block() -> str:
    lines = []
    for layer, title in (
        ("hazard", "HAZARD tier - the hazard itself"),
        ("exposure", "EXPOSURE tier - who or what is affected"),
        ("action", "ACTION tier - what people do about it"),
    ):
        lines.append(f"{title}:")
        for ho in HAZARD_OBJECTS.values():
            if ho.layer == layer:
                lines.append(f"  - `{ho.id}`: {', '.join(ho.required_variables)}")
    return "\n".join(lines)


def slot_matrix_prompt_block(intents: list[str]) -> str:
    """The slot baseline for this request's intents, for stages 2 and 3."""
    reqs = required_slots(intents)
    if not reqs:
        return "(no slot requirements known for these intents)"
    label = {"B": "BLOCKING", "D": "DEFAULTABLE", "O": "OPTIONAL"}
    lines = []
    for slot, r in reqs.items():
        default = slot_default(slot, intents)
        suffix = f" (neutral default: '{default}')" if r.requirement == "D" and default else ""
        lines.append(f"- `{slot}` [{label[r.requirement]}]{suffix}: {SLOT_DEFINITIONS[slot]}")
    return "\n".join(lines)
