"""FastAPI + SSE - streams incremental contract updates to the frontend.

The frontend only needs two concepts:
  1. POST /api/sessions                 create a session
  2. POST /api/sessions/{id}/messages   send a message, receive an SSE stream

The same endpoint handles both the opening question and a clarification answer.
The backend decides which it is by checking whether the session is parked on an
interrupt, so the client never has to care.

    uv run uvicorn wildfire_agent.api:app --reload
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from firescope_mcp.server import mcp as public_data_mcp
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from mcp import Client
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from .analyses import AnalysisMatch
from .analyses import resolve as resolve_analysis
from .config import settings
from .context_layers import (
    city_context_status,
    city_subject_layer,
    city_weather_status,
    communities_intersecting_active_fire,
    communities_intersecting_burned_area,
    communities_intersecting_fire,
    communities_nearest_burned_area,
    communities_nearest_fire,
    filter_layer_to_bbox,
    filter_layer_to_subject,
)
from .contract import AnalysisContract, ResolvedLocation, SpatialSlot
from .conversation import (
    AnalysisRef,
    ConversationContext,
    ConversationResolution,
    ConversationResolutionError,
    SubjectRef,
    TimeRef,
    resolve_turn,
    sources_to_fetch,
    variables_to_lead,
)
from .debris_flow import HAZARD_OBJECT as DEBRIS_HAZARD_OBJECT
from .debris_flow import fetch as debris_flow_fetch
from .debris_flow import offer as debris_flow_offer
from .events import EventName
from .exposure import (
    FETCHED_PROPERTIES,
    enrich_places_with_acs,
    offer_for,
    variables_present,
)
from .graph import build_graph
from .graph.build import make_serde
from .graph.state import STAGE_AGENTS, STAGE_LABELS
from .llm import check_llm_ready, describe_llm, is_mock
from .narration import discuss, narrate
from .planning import CAPABILITIES, SHOWCASE_AREA, ExecutionPlan
from .planning.capabilities import missing_variables
from .planning.executor import clip_local_layer, validate_bbox
from .planning.models import LayerResult, LayerVisualization, LegendStop, PopupField
from .raster_layers import (
    FireRasterPlan,
    RasterLayerError,
    active_fire_label_points,
    burned_area_label_points,
    event_supports_burned_area,
    fire_event_catalog_payload,
    lifecycle_asset_path,
    plan_fire_raster,
    preview_path,
    raster_catalog_payload,
    render_fire_lifecycle,
    render_raster_preview,
)
from .request_intent import is_weather_only
from .session_store import SessionStore
from .taxonomy import (
    EXPERTISE_DEFINITIONS,
    HAZARD_OBJECTS,
    INTENT_DEFINITIONS,
    INTENT_SLOT_MATRIX,
    ROLE_DEFINITIONS,
    SLOT_DEFINITIONS,
    ExpertiseLevel,
)
from .visualization import public_layer_visualization

app = FastAPI(title="Wildfire User Goal Agent", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logger = logging.getLogger(__name__)

# In-process state. Swap in PostgresSaver plus real session storage for production.
_checkpointer = MemorySaver(serde=make_serde())
_graph = build_graph(_checkpointer)
_session_store = SessionStore(settings.session_db_path)
_sessions: set[str] = _session_store.ids()


#: Short replies that plainly answer a yes/no without using either label.
#: Anything outside these is treated as unclear rather than guessed at.
_AFFIRMATIVE = frozenset(
    {"yes", "y", "yeah", "yep", "ok", "okay", "sure", "go ahead", "do it", "please do", "fetch"}
)
_NEGATIVE = frozenset({"no", "n", "nope", "skip", "don't", "do not", "no thanks", "without it"})


@dataclass
class PendingFill:
    """An external fetch offered to the user and awaiting their decision.

    The graph parks its own interrupts in the checkpointer, but the fire
    rendering runs after the graph has finished, so it cannot use `interrupt()`.
    This is the same idea in the layer that owns the question: hold the offer,
    ask, and act on the answer at the start of the next turn.
    """

    offer: dict[str, Any]
    #: The layer the fill would enrich, so the answer acts on the right one.
    capability_id: str
    accept: str = "Fetch it"
    decline: str = "Answer without it"

    @property
    def key(self) -> str:
        """What the user's answer is an answer *about*.

        The source, not the layer or the turn. "Fetch the Census figures" asked
        again three turns later is the same question, and the user has already
        answered it.
        """
        return str(self.offer.get("source_id") or self.capability_id)

    def decide(self, text: str) -> bool | None:
        """True to fetch, False to decline, None when the answer is not clear.

        Picking an option in the UI *appends* it to the composer rather than
        sending, and several answers join with "; ", so an exact match on the
        label misses a reply that plainly said yes. Containment handles that.

        The decline label is tested first on purpose: "Answer without it" names
        the data too, and reading consent out of a refusal is the one mistake
        this gate exists to prevent. An unclear answer returns None, because the
        safe default for "did they agree" is to ask again, not to assume.
        """
        lowered = text.strip().casefold()
        if not lowered:
            return None
        if self.decline.casefold() in lowered:
            return False
        if self.accept.casefold() in lowered:
            return True
        if lowered in _AFFIRMATIVE:
            return True
        if lowered in _NEGATIVE:
            return False
        return None

    def as_clarification(self) -> dict[str, Any]:
        closes = ", ".join(self.offer["closes"])
        remaining = self.offer.get("remaining") or ()
        preamble = (
            f"To answer this I need {closes}, which no local layer carries. "
            f"I can fetch it from {self.offer['source']} "
            f"({self.offer['dataset']}, by {self.offer['geography']})."
        )
        if remaining:
            preamble += f" This would still leave {', '.join(remaining)} unavailable."
        return {
            "type": "clarification",
            "preamble": preamble,
            "questions": [
                {
                    "slot": "external_data",
                    "question": "Fetch this data?",
                    "options": [
                        {"label": self.accept, "value": self.accept, "implication": ""},
                        {"label": self.decline, "value": self.decline, "implication": ""},
                    ],
                    "allow_free_text": False,
                }
            ],
            "pending_slots": ["external_data"],
        }


@dataclass
class FillDecisions:
    """External fetches this session has already settled, by source.

    Every render re-derives its gap from `missing_variables`, a static taxonomy
    table. That table cannot see the values a previous fetch attached, nor the
    answer the user already gave, so on its own it re-asks the same question on
    every turn about the same fire. The decision is a property of the session,
    and this is where it lives.

    A refusal is remembered as firmly as an approval. Re-asking a "no" until it
    becomes a "yes" is the same failure wearing the other answer.
    """

    _answers: dict[str, bool] = field(default_factory=dict)

    def record(self, key: str, approved: bool) -> None:
        self._answers[key] = approved

    def settled(self, key: str) -> bool:
        return key in self._answers

    def approved(self, key: str) -> bool:
        return self._answers.get(key, False)

    def should_ask(self, key: str) -> bool:
        return key not in self._answers


#: Offers awaiting an answer, one per session.
_pending_fills: dict[str, PendingFill] = {}
#: Fetch decisions already made, one set per session.
_fill_decisions: dict[str, FillDecisions] = {}
_session_contexts: dict[str, ConversationContext] = {}


class MessageIn(BaseModel):
    #: A turn costs several provider calls, and the endpoint takes no
    #: credentials, so an unbounded field turns one request into an unbounded
    #: bill. A real question is a sentence; this is far above that.
    text: str = Field(min_length=1, max_length=4000)
    #: Expertise picker in the UI - replays one question at three depths.
    expertise_override: ExpertiseLevel | None = None


PublicSource = Literal[
    "weather", "air_quality", "wfigs", "hmsfire", "fire_history", "firms"
]
LocalCapability = Literal[
    "official_fire_perimeters",
    "satellite_hotspots",
    "historical_fire_perimeters",
]


class PublicLayerIn(BaseModel):
    source: PublicSource
    day: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    start_year: int = 2000


class LocalLayerIn(BaseModel):
    capability_id: LocalCapability
    bbox: tuple[float, float, float, float]


class LocalRasterLayerIn(BaseModel):
    dataset_id: str = Field(min_length=1)
    bbox: tuple[float, float, float, float]
    day: str | None = None


class FireLifecycleIn(BaseModel):
    event_id: str = Field(min_length=1)
    day: str | None = None


class SessionSnapshotIn(BaseModel):
    snapshot: dict[str, Any]


class SessionRenameIn(BaseModel):
    title: str = Field(min_length=1, max_length=100)


def _archive_summary() -> list[dict[str, Any]]:
    """The local fire archive, small enough to ride every prompt.

    Names, spans, and what each event can actually answer - not the raster
    metadata. `burned_area` is read from the data, so an event that carries only
    active-fire detections says so here rather than at the end of a failed
    analysis.
    """
    payload = fire_event_catalog_payload()
    return [
        {
            "name": (event["event_name"] or event["event_id"]).removesuffix(" area"),
            "first_day": (event["dates"] or [None])[0],
            "last_day": (event["dates"] or [None])[-1],
            "burned_area": event["task_support"]["burned_area_mapping"],
            "active_fire": event["task_support"]["active_fire_detection"],
        }
        for event in payload["events"]
    ]


def _remember_completed_analysis(
    session_id: str,
    user_text: str,
    contract: AnalysisContract,
    fire_plan: FireRasterPlan | None,
    summary: str | None,
    layer_ids: list[str],
    spatial_analysis: dict[str, Any] | None,
    result_facts: dict[str, Any] | None = None,
    fetched: dict[str, Any] | None = None,
) -> None:
    """Commit one completed turn to structured analytical memory."""
    context = _session_contexts.setdefault(session_id, ConversationContext())
    context.last_result = result_facts
    for source_id, payload in (fetched or {}).items():
        context.remember_fetch(source_id, payload)
    if fire_plan and fire_plan.dataset.event_id:
        event_name = (fire_plan.dataset.event_name or fire_plan.dataset.event_id).removesuffix(
            " area"
        )
        context.active_subject = SubjectRef(
            type="fire_event",
            id=fire_plan.dataset.event_id,
            name=event_name,
        )
        context.active_time = TimeRef(
            selected=fire_plan.day,
            start=fire_plan.dataset.time_start,
            end=fire_plan.dataset.time_end,
        )
        if spatial_analysis:
            analysis_dates = [item["date"] for item in spatial_analysis.get("inputs") or []]
            if analysis_dates:
                context.active_time = TimeRef(
                    selected=analysis_dates[-1],
                    start=analysis_dates[0],
                    end=analysis_dates[-1],
                )
            result_layer_ids = [layer["id"] for layer in spatial_analysis.get("layers") or []]
            context.active_analysis = AnalysisRef(
                id=spatial_analysis["analysis_id"],
                operation=spatial_analysis["operation"],
                description=(
                    f"{spatial_analysis['formula']}, masked to the mapped burned-area footprint"
                ),
                plan={
                    "formula": spatial_analysis["formula"],
                    "inputs": spatial_analysis["inputs"],
                    "operations": spatial_analysis["operations"],
                    "mask": spatial_analysis["mask"],
                },
                layer_ids=result_layer_ids,
            )
            context.active_layer_ids = result_layer_ids + layer_ids
        else:
            operation = (
                "community_intersection"
                if _asks_fire_community_question(contract.analysis_request())
                else "fire_lifecycle"
            )
            context.active_analysis = AnalysisRef(
                id=f"{fire_plan.dataset.event_id}:{operation}:{fire_plan.day}",
                operation=operation,
                description=(f"{operation.replace('_', ' ')} for {event_name} on {fire_plan.day}"),
                layer_ids=layer_ids,
            )
            context.active_layer_ids = layer_ids
    else:
        spatial = contract.spatial()
        resolved = spatial.resolved if spatial else None
        if spatial and resolved and resolved.center:
            name = resolved.display_name or spatial.value or "Resolved place"
            context.active_subject = SubjectRef(
                type="place",
                id=None,
                name=name.split(",")[0],
                location=resolved.model_dump(mode="json"),
            )
            context.active_time = TimeRef(
                selected=(
                    contract.slots.get("time_horizon").value
                    if contract.slots.get("time_horizon")
                    else None
                )
            )
            context.active_analysis = AnalysisRef(
                id=f"place:{name}:{contract.created_at.isoformat()}",
                operation="city_context",
                description=f"Current city fire, air-quality, and weather context for {name}",
                layer_ids=layer_ids,
            )
            context.active_layer_ids = layer_ids
    context.remember("user", user_text)
    if summary:
        context.remember("agent", summary)
    _session_store.save_context(session_id, context.model_dump(mode="json"))


def _expertise_label(override: ExpertiseLevel | None) -> str:
    """The narrator needs a register, not an enum."""
    if override is None:
        return "general"
    return getattr(override, "value", str(override))


def _sse(event: EventName, data: Any) -> dict:
    """Encode one SSE frame. `EventName` keeps the wire contract in events.py."""
    return {"event": event, "data": json.dumps(data, ensure_ascii=False, default=str)}


def _contract_payload(contract: AnalysisContract) -> dict:
    payload = contract.model_dump(mode="json")
    payload["pending_slots"] = contract.pending_slots
    payload["filled_ratio"] = contract.filled_ratio()
    return payload


@app.get("/api/health")
async def health() -> dict:
    ok, detail = check_llm_ready()
    # `mock` propagates all the way to a UI badge: a stub never poses as real inference.
    return {"ok": ok, "llm": describe_llm(), "mock": is_mock(), "detail": detail}


@app.get("/api/taxonomy")
async def taxonomy() -> dict:
    """Enums, the slot matrix, and stage labels, so the frontend never keeps its
    own copy of the domain vocabulary."""
    return {
        "intents": INTENT_DEFINITIONS,
        "expertise": EXPERTISE_DEFINITIONS,
        "roles": ROLE_DEFINITIONS,
        "slots": SLOT_DEFINITIONS,
        "intent_slot_matrix": INTENT_SLOT_MATRIX,
        "hazard_objects": {
            ho.id: {
                "layer": ho.layer,
                "label": ho.label,
                "required_variables": list(ho.required_variables),
                "dataset_families": list(ho.dataset_families),
                "covered_by": sorted(
                    c.id for c in CAPABILITIES.values() if c.hazard_object == ho.id
                ),
                "family_choices": [
                    {"id": c.id, "label": c.label, "caveat": c.caveat} for c in ho.family_choices
                ],
            }
            for ho in HAZARD_OBJECTS.values()
        },
        "stages": STAGE_LABELS,
        "stage_agents": STAGE_AGENTS,
        "showcase_area": {
            "id": SHOWCASE_AREA["id"],
            "name": SHOWCASE_AREA["name"],
            "center": list(SHOWCASE_AREA["center"]),
            "bbox": list(SHOWCASE_AREA["bbox"]),
            "context": SHOWCASE_AREA["context"],
        },
        "capabilities": {
            c.id: {
                "title": c.title,
                "hazard_object": c.hazard_object,
                "family": c.family,
                "temporality": c.temporality,
                "geometry_type": c.geometry_type,
                "caveat": c.caveat,
            }
            for c in CAPABILITIES.values()
        },
    }


@app.get("/api/schema/contract")
async def contract_schema() -> dict:
    """JSON Schema for generating the frontend TypeScript types.

    Serialization mode is required: the frontend consumes the **output** shape,
    and validation mode omits computed fields such as `ready_for_planning`.
    """
    return AnalysisContract.model_json_schema(mode="serialization")


def _public_layer_results(source: PublicSource, payload: dict) -> list[LayerResult]:
    """Split an MCP FeatureCollection into renderer-safe homogeneous layers."""
    metadata = payload.get("metadata") or {}
    feature_groups: dict[str, list[dict]] = {}
    for feature in payload.get("features") or []:
        geometry_type = (feature.get("geometry") or {}).get("type")
        if geometry_type in {"Polygon", "MultiPolygon"}:
            geometry_type = "Polygon"
        elif geometry_type in {"Point", "MultiPoint"}:
            geometry_type = "Point"
        elif geometry_type in {"LineString", "MultiLineString"}:
            geometry_type = "LineString"
        else:
            continue
        feature_groups.setdefault(geometry_type, []).append(feature)

    defaults: dict[PublicSource, tuple[str, str, str | None, str]] = {
        "weather": (
            "Point",
            "NWS hourly weather",
            None,
            "NOAA National Weather Service",
        ),
        "air_quality": (
            "Point",
            "Current modeled air quality",
            None,
            "Open-Meteo / CAMS global",
        ),
        "wfigs": (
            "Polygon",
            "Live WFIGS fire perimeters",
            "official_fire_perimeters",
            "NIFC WFIGS",
        ),
        "hmsfire": (
            "Point",
            "NOAA HMS thermal detections",
            "satellite_hotspots",
            "NOAA NESDIS HMS",
        ),
        "fire_history": (
            "Polygon",
            "NIFC historical fire perimeters",
            "official_fire_perimeters",
            "NIFC",
        ),
        "firms": (
            "Point",
            "NASA FIRMS near-real-time thermal detections",
            "satellite_hotspots",
            "NASA FIRMS",
        ),
    }
    primary_geometry, title, family, provider = defaults[source]
    if not feature_groups:
        feature_groups[primary_geometry] = []

    layers = []
    for geometry_type, features in feature_groups.items():
        suffix = geometry_type.lower()
        layer_title = title
        if source == "weather" and geometry_type == "LineString":
            layer_title = "NWS derived downwind direction"
        hazard_object = {
            "weather": "weather",
            "air_quality": "air_quality",
            "hmsfire": "satellite_hotspot",
            "wfigs": "fire_perimeter",
            "fire_history": "fire_perimeter",
            "firms": "satellite_hotspot",
        }[source]
        layers.append(
            LayerResult(
                capability_id=f"public_{source}_{suffix}",
                title=f"API · {layer_title}",
                hazard_object=hazard_object,
                family=family,
                geometry_type=geometry_type,
                caveat=metadata.get("caveat", "Public API result; inspect provenance."),
                feature_count=len(features),
                truncated=bool(metadata.get("truncated")),
                source=provider,
                retrieved_at=metadata.get("retrievedAt"),
                visualization=public_layer_visualization(source, geometry_type, features),
                geojson={"type": "FeatureCollection", "features": features},
            )
        )
    return layers


async def _fetch_public_mcp(body: PublicLayerIn, *, national: bool = False) -> dict:
    """Use the same in-process MCP protocol path an LLM client uses."""
    async with Client(public_data_mcp) as client:
        result = await client.call_tool(
            "fetch_public_geojson",
            {
                "source": body.source,
                "day": body.day,
                "latitude": body.latitude,
                "longitude": body.longitude,
                "start_year": body.start_year,
                "national": national,
            },
        )
    if result.is_error or not result.content:
        raise HTTPException(502, "Public-data MCP tool call failed")
    text = getattr(result.content[0], "text", None)
    if not text:
        raise HTTPException(502, "Public-data MCP returned no JSON payload")
    return json.loads(text)


async def _automatic_city_context(
    contract: AnalysisContract,
) -> tuple[dict, list[LayerResult]]:
    spatial = contract.spatial()
    resolved = spatial.resolved if spatial else None
    if not resolved or not resolved.center or not resolved.bbox:
        return (
            {
                "status": "no_scope",
                "workflow": "city",
                "message": "The city could not be grounded, so no current context was loaded.",
                "details": [],
            },
            [],
        )
    lon, lat = resolved.center
    subject = city_subject_layer(contract)
    weather_only = is_weather_only(contract.analysis_request())
    bodies = {"weather": PublicLayerIn(source="weather", latitude=lat, longitude=lon)}
    if not weather_only:
        bodies = {
            "wfigs": PublicLayerIn(source="wfigs"),
            **bodies,
            "air_quality": PublicLayerIn(source="air_quality", latitude=lat, longitude=lon),
            # Verified perimeters lag; these are hours old and carry intensity.
            # Both, never merged.
            "firms": PublicLayerIn(source="firms"),
        }
    fetched = await asyncio.gather(
        *(_fetch_public_mcp(body) for body in bodies.values()),
        return_exceptions=True,
    )
    by_source: dict[str, list[LayerResult]] = {}
    for source, result in zip(bodies, fetched, strict=True):
        if isinstance(result, Exception):
            by_source[source] = []
            continue
        layers = _public_layer_results(source, result)  # type: ignore[arg-type]
        if source == "wfigs":
            if subject:
                layers = [filter_layer_to_subject(layer, subject) for layer in layers]
            else:
                layers = [filter_layer_to_bbox(layer, resolved.bbox) for layer in layers]
        by_source[source] = layers

    perimeter = next(iter(by_source.get("wfigs", [])), None)
    firms = next(iter(by_source.get("firms", [])), None)
    air = next(iter(by_source.get("air_quality", [])), None)
    weather = next(
        (layer for layer in by_source.get("weather", []) if layer.geometry_type == "Point"),
        None,
    )
    city_name = (resolved.display_name or spatial.value or "Resolved city").split(",")[0]
    status = (
        city_weather_status(city_name, weather)
        if weather_only
        else city_context_status(city_name, perimeter, air, weather, firms)
    )
    if subject and not weather_only:
        subject_geojson = json.loads(json.dumps(subject.geojson))
        properties = subject_geojson["features"][0].setdefault("properties", {})
        properties.update(
            {
                "displayName": city_name,
                "fireEvidence": status["evidence"],
                "currentFirePerimeters": perimeter.feature_count if perimeter else 0,
                "usAqi": (
                    ((air.geojson.get("features") or [{}])[0].get("properties") or {}).get("usAqi")
                    if air
                    else None
                ),
            }
        )
        subject = subject.model_copy(
            update={
                "geojson": subject_geojson,
                "visualization": LayerVisualization(
                    kind="categorical",
                    label="Current direct fire evidence",
                    field="fireEvidence",
                    stops=[
                        LegendStop(value="low", label="Low direct evidence", color="#4f7c59"),
                        LegendStop(
                            value="elevated",
                            label="Elevated direct evidence",
                            color="#c34a36",
                        ),
                    ],
                    popup_fields=[
                        PopupField(key="fireEvidence", label="Direct fire evidence"),
                        PopupField(
                            key="currentFirePerimeters",
                            label="Intersecting current perimeters",
                        ),
                        PopupField(key="usAqi", label="Modeled U.S. AQI"),
                        PopupField(key="legalName", label="Census place"),
                    ],
                    explanation=(
                        "Color summarizes current direct perimeter evidence for the city; "
                        "it is not a probability or forecast."
                    ),
                ),
            }
        )
    layers = [
        layer
        for source_layers in by_source.values()
        for layer in source_layers
        if layer.feature_count
    ]
    if subject:
        layers.insert(0, subject)
        status["details"].insert(
            0,
            (
                "Weather was requested at the resolved city reference point; the Census place/CDP polygon is shown only as geographic context."
                if weather_only
                else "Fire-perimeter intersection was evaluated against the Census place/CDP polygon, not a fixed-radius circle."
            ),
        )
    else:
        status["details"].insert(
            0,
            "No local place polygon matched; the backend query envelope was used only as a fallback search scope.",
        )
    return status, layers


def _normalise_incident_name(value: str) -> str:
    ignored = {"fire", "area", "incident"}
    return " ".join(word for word in re.findall(r"[a-z0-9]+", value.lower()) if word not in ignored)


#: US-XX in WFIGS. Only the states this demo is likely to surface are spelled
#: out; anything else falls back to the code, which is still readable.
_STATE_NAMES = {
    "US-OR": "Oregon", "US-CA": "California", "US-ID": "Idaho", "US-WA": "Washington",
    "US-MT": "Montana", "US-NV": "Nevada", "US-AZ": "Arizona", "US-UT": "Utah",
    "US-CO": "Colorado", "US-WY": "Wyoming", "US-NM": "New Mexico", "US-TX": "Texas",
    "US-AK": "Alaska", "US-FL": "Florida",
}


def _is_a_new_question(value: str) -> bool:
    """Whether an unclear reply to an offer is itself a question owed an answer.

    A session parked on a fetch offer read the next message as the answer to it.
    "How many hazard areas are there?" is neither a yes nor a no, so the offer
    was put again and the question disappeared - and in a walkthrough that
    killed every turn after it.

    The bar is deliberately low but not absent: "hmm" is noise and re-asking is
    the right response to it, while anything with a question's shape gets
    carried through to the pipeline with the offer still standing.
    """
    text = value.strip()
    if len(text) < 8:
        return False
    if text.endswith("?"):
        return True
    return bool(
        re.match(
            r"\s*(?:what|which|who|where|when|why|how|is|are|was|were|do|does|did|can|show|"
            r"list|tell|explain|compare)\b",
            text,
            re.IGNORECASE,
        )
    )


def _asks_national_fire_question(value: str) -> bool:
    """A question about the country rather than about a place.

    It named no city, so it fell into the pipeline that resolves one, found
    nothing to geocode and returned nothing at all. WFIGS answers this directly
    and needs no subject.

    Narrow on purpose: a named place keeps the pipeline that resolves it, and
    "any fires near Santa Barbara" is not this question.
    """
    if re.search(r"\bnear\b|\baround\b", value, re.IGNORECASE):
        return False
    scope = r"(?:usa|u\.s\.a?\.?|united states|the us\b|the country|nationwide|anywhere)"
    burning = r"(?:fires?|wildfires?|burning|blazes?)"
    return bool(
        re.search(rf"\b{burning}\b[^?.]{{0,40}}\b{scope}", value, re.IGNORECASE)
        or re.search(rf"\b{scope}\b[^?.]{{0,40}}\b{burning}\b", value, re.IGNORECASE)
        or re.search(
            rf"\b(?:any|are there)\b[^?.]{{0,25}}\b(?:ongoing|active|current)\b[^?.]{{0,15}}"
            rf"\b{burning}\b",
            value,
            re.IGNORECASE,
        )
        or re.search(rf"\bwhat\b[^?.]{{0,15}}\b{burning}\b[^?.]{{0,25}}\bright now\b", value, re.IGNORECASE)
    )


def _national_fire_status(features: list[dict[str, Any]]) -> dict[str, Any]:
    """What agencies currently have mapped, nationally.

    An empty list is about the record, not the country: a perimeter is published
    after a fire has been mapped, so "none reported" and "nothing burning" are
    different statements and this never merges them.
    """
    ranked = sorted(
        (
            (
                (f.get("properties") or {}).get("attr_IncidentSize"),
                str((f.get("properties") or {}).get("poly_IncidentName") or "unnamed"),
                str((f.get("properties") or {}).get("attr_POOState") or ""),
            )
            for f in features
        ),
        key=lambda item: item[0] or 0,
        reverse=True,
    )
    details = [
        (
            "A perimeter appears here once an agency has mapped it, so this is the "
            "published record and not a complete picture of everything alight."
        ),
        "Sizes are the agency's own reported acreage at the last update.",
    ]
    if not ranked:
        return {
            "status": "national_assessed",
            "workflow": "national",
            "message": (
                "0 current wildfire perimeters are published for the contiguous United "
                "States right now. That is what agencies have mapped, not a statement "
                "that nothing is burning."
            ),
            "details": details,
        }
    named = []
    for acres, name, state in ranked[:3]:
        where = _STATE_NAMES.get(state, state.removeprefix("US-")) or "location not reported"
        # A fire with no reported acreage is not a fire of zero acres.
        size = f"{acres:,.0f} acres" if acres else "size not yet reported"
        named.append(f"{name} ({size}, {where})")
    return {
        "status": "national_assessed",
        "workflow": "national",
        "message": (
            f"{len(ranked)} current wildfire perimeters are published for the contiguous "
            f"United States. The largest are {'; '.join(named)}."
        ),
        "details": details,
    }


def _asks_archive_question(value: str) -> bool:
    """A question about what this deployment holds, not about a fire in it.

    The archive rides in the session context, but it was only ever read on a
    discussion turn, so whether "what fires do you have data for" got answered
    depended on the resolver's classification. On one run it listed all nine
    events; on the next it called the same question analysis, ran the pipeline,
    and asked which geographic area to consider.

    Deliberately narrow. It must not catch a question about a fire - routing
    "which cities did it reach" here would replace a result with a catalogue.
    """
    noun = r"(?:fires?|events?|data|datasets?|archive|database|catalogue|catalog|analyses)"
    holdings = (
        r"(?:do you have|you have|you can analys[ei]|you can do|can you analys[ei]|"
        r"are (?:there|available|in)|available|in your|is in|you cover|you support)"
    )
    opener = r"(?:what|which|list|show|tell me)"
    return bool(
        re.search(
            rf"\b{opener}\b[^?.]{{0,40}}\b{noun}\b[^?.]{{0,30}}\b{holdings}\b",
            value,
            re.IGNORECASE,
        )
        or re.search(rf"\b{holdings}\b[^?.]{{0,25}}\b{noun}\b", value, re.IGNORECASE)
        or re.search(rf"\bwhat(?:'s| is)?\s+in\s+your\s+{noun}\b", value, re.IGNORECASE)
        # No archive noun of its own, but no other reading either.
        or re.search(r"\bwhat can you (?:analys[ei]|do|show me|work with)\b", value, re.IGNORECASE)
    )


def _archive_kind(kind: str, text: str) -> str:
    """Route an archive question to the answer, never away from one.

    The override only ever moves a turn toward `discussion`, which is where the
    archive is read. A turn already classified that way is untouched, and a
    question that is not about the archive keeps whatever the resolver decided.
    """
    return "discussion" if kind == "analysis" and _asks_archive_question(text) else kind


def _asks_post_fire_risk_question(value: str) -> bool:
    """A question about what follows the fire rather than about the fire.

    Debris flow is the consequence a burned watershed carries into the next
    rainy season, so "what should I watch now" is asking for a hazard nothing
    in this deployment holds.
    """
    return bool(
        re.search(
            r"\b(?:debris flow|mudslide|mud flow|landslide|post[- ]?fire|"
            r"watershed|runoff|rainy season|next winter)\b",
            value,
            re.IGNORECASE,
        )
        or re.search(
            r"\b(?:what(?:'s| is)? next|watch (?:out )?for|worry about|"
            r"downstream|follow[- ]?on|come next|happens next)\b",
            value,
            re.IGNORECASE,
        )
    )


#: How a question names each fetched variable. Ordered most specific first, so
#: "median household income" is not claimed by the looser household pattern.
_ATTRIBUTE_QUERIES: tuple[tuple[str, str], ...] = (
    (r"\b(?:income|earnings|salary|wages|earn|afford)\b", "medianHouseholdIncome"),
    (r"\b(?:housing|houses|homes|dwellings?|units?)\b", "housingUnits"),
    (r"\b(?:median age|age structure|how old)\b", "medianAge"),
    (r"\b(?:elderly|seniors?|older adults?|living alone)\b", "seniorsLivingAlone"),
    (r"\b(?:vehicles?|cars?|carless|without a car)\b", "householdsWithoutVehicle"),
    (
        # "household income" is one phrase naming income, not a count of
        # households, and it reads earlier in the sentence than "income" does.
        (
            r"\b(?:population|residents?|inhabitants?|who lives|how many people|"
            r"demographics?|households?(?!\s+income)|people live)\b"
        ),
        "population",
    ),
)


def _subject_changed(resolution: ConversationResolution) -> bool:
    """Whether this turn is about something other than the last one.

    `relation` is deliberately not consulted. Reading `correction` as "changed"
    is the obvious mistake and the wrong one: a correction can correct the date
    and keep the fire - "no, I meant the 28th" - and wiping the map for that
    throws away the thing the user is still looking at. `inherited_subject` is
    the field that answers the question, and `resolve_turn` already forces it to
    False on a new request, so that case falls out for free.
    """
    return not resolution.inherited_subject


def _turn_payload(kind: str, *, subject_changed: bool = False) -> dict[str, Any]:
    """The `turn` event, which is the first event of every turn.

    Carries whether the map is about to be replaced rather than refreshed, so
    the frontend can clear a stale place immediately instead of holding it until
    the first new layer arrives seconds later. See docs/05-turn-subject-change.md.
    """
    return {"kind": kind, "subject_changed": subject_changed}


def _question_variables(session_id: str, contract: AnalysisContract) -> tuple[str, ...]:
    """Which place variables this turn is about.

    The resolver's answer, because it read the question with a model and knows
    that "how wealthy" means income. `_asked_attributes` is the fallback for the
    turns the resolver never sees - the first turn of a session, and mock mode -
    and is no longer authoritative anywhere. A keyword gate deciding what the
    narrator may know is what refused a wealth question while holding the figure.
    """
    context = _session_contexts.get(session_id)
    if context is None or context.active_subject is None:
        return _asked_attributes(contract.original_request) or _asked_attributes(
            contract.analysis_request()
        )
    # The resolver ran. Naming nothing is an answer, not a gap to guess around.
    return tuple(context.active_variables)


def _asked_attributes(value: str) -> tuple[str, ...]:
    """Which fetched variables this question actually asked for, in asked order.

    The reply used to recite all seven in storage order, so a question about
    income opened with a population count. Knowing which one was asked is what
    lets the answer lead with it - and it is the same test that decides whether
    the fill is worth stopping the user for at all, so the two cannot drift.
    """
    found: list[tuple[int, str]] = []
    for pattern, prop in _ATTRIBUTE_QUERIES:
        match = re.search(pattern, value, re.IGNORECASE)
        if match and prop not in (p for _, p in found):
            found.append((match.start(), prop))
    return tuple(prop for _, prop in sorted(found))


def _asks_place_attribute_question(value: str) -> bool:
    """Whether the ACS fill could change this answer at all.

    Weather and fire-proximity replies read no population, so offering there
    spends the one question on something that cannot change a word.
    """
    return bool(_asked_attributes(value))


def _asks_fire_community_question(value: str) -> bool:
    """Identify a fire-to-community spatial follow-up from user wording."""
    return bool(
        re.search(r"\b(?:cities|city|communities|community|towns?|places?)\b", value, re.IGNORECASE)
        and re.search(
            # "which cities did it reach" and "what towns burned" are the two
            # most natural phrasings and matched none of the original verbs.
            r"\b(?:intersect(?:ed|s|ing)?|closest|nearest|near|contain(?:ed|s|ing)?|"
            r"have|with|affect(?:ed|s|ing)?|impact(?:ed|s|ing)?|reach(?:ed|es|ing)?|"
            r"burn(?:ed|t|ing)?|hit|threaten(?:ed|s|ing)?|overlap(?:ped|s|ping)?)\b",
            value,
            re.IGNORECASE,
        )
    )


def _place_names(layer: LayerResult | None) -> list[str]:
    if layer is None:
        return []
    return [
        str(properties.get("name") or properties.get("legalName"))
        for feature in layer.geojson.get("features") or []
        if (properties := feature.get("properties") or {}).get("name")
        or properties.get("legalName")
    ]


def _human_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return value
    return f"{parsed.strftime('%B')} {parsed.day}, {parsed.year}"


def _place_population(layer: LayerResult | None) -> str:
    """Residents per place, once an approved fetch has attached them.

    Reported beside the area share and never multiplied by it: the share says
    how much of a place's land is inside the footprint, and these people are
    not distributed evenly across that land.
    """
    if not layer:
        return ""
    parts = []
    total = 0
    for feature in layer.geojson.get("features") or []:
        properties = feature.get("properties") or {}
        population = properties.get("population")
        name = properties.get("name")
        if population is None or not name:
            continue
        total += int(population)
        parts.append(f"{name} {int(population):,}")
    if not parts:
        return ""
    joined = ", ".join(parts)
    return (
        f" Census population is {joined}"
        + (f", {total:,} in total" if len(parts) > 1 else "")
        + ". These counts are for the whole place, not for the area that burned."
    )


def _place_shares(layer: LayerResult | None) -> str:
    """Each place with the share of its own land inside the footprint, and the area.

    The share alone is broken at both ends, because its denominator is the
    place's own size. Los Angeles had 3.37 km2 inside the Woolsey footprint and
    printed as "0%", while Hidden Hills' 0.70 km2 printed as "16%" - the larger
    burn read as nothing. And a pixel whose centre falls inside counts whole, so
    the sum can exceed the polygon: Pepperdine University printed as "100.2%",
    which is not a possible share of anything.

    The area survives both failures, so it travels with every share. A share
    below half a percent is written "<1%" rather than rounded to a zero that
    states nothing happened, and one above the whole place is capped at 100%
    because the excess is the grid overshooting, not extra land.
    """
    if not layer:
        return ""
    parts = []
    for feature in layer.geojson.get("features") or []:
        properties = feature.get("properties") or {}
        share = properties.get("labelSharePercent")
        name = properties.get("name")
        if share is None or not name:
            continue
        if share >= 100:
            share_text = "100%"
        elif share < 0.5:
            share_text = "<1%"
        else:
            share_text = f"{share:.0f}%"
        area = properties.get("labelAreaKm2")
        parts.append(
            f"{name} {share_text} ({area:.1f} km2)" if area is not None
            else f"{name} {share_text}"
        )
    return ", ".join(parts)


def _record_fill_on_contract(
    contract: AnalysisContract,
    *,
    source: str,
    dataset: str,
    closed: tuple[str, ...],
    remaining: tuple[str, ...],
    count: int,
    unit: tuple[str, str],
    complete: bool,
    failed: dict[str, str],
) -> None:
    """Put an approved external fetch on the contract, where it can be seen.

    The backend emits `data_fill` with all of this, and no frontend consumes it.
    So the one capability the review asked to have demonstrated - a person
    authorising an outside fetch, and the provenance of what came back - left no
    trace beyond the numbers quietly changing.

    `contract.assumptions` is already rendered under Limits, and `_run` emits
    `done` carrying this same contract after the render, so recording it here
    reaches the screen with no frontend change. It belongs there on the merits
    too: a fetch made on someone's approval is a decision taken on their behalf,
    which is exactly what that field is for.

    `unit` and `complete` are not decoration. The first version of this sentence
    called debris-flow polygons "places" and stated a capped 1,500 as though it
    were the total - the wrong noun and a truncated count reported as a count,
    both of which are mistakes corrected elsewhere in this file.
    """
    noun = unit[0] if count == 1 else unit[1]
    if closed:
        note = (
            f"At your approval, {dataset} was fetched from {source} for "
            f"{count:,} {noun}, supplying {', '.join(closed)}."
        )
        if not complete:
            note += (
                f" {count:,} is the fetch limit, not the total the source holds, so treat "
                "what is drawn as a sample rather than an inventory."
            )
    else:
        note = (
            f"At your approval, {source} was queried for "
            f"{', '.join(remaining) or 'these attributes'} and returned nothing that "
            "could be attached."
        )
    if remaining and closed:
        note += f" Still unavailable from any source: {', '.join(remaining)}."
    if failed:
        # A place we could not reach and a place with no residents must not read
        # alike, on the record as much as in the prose.
        note += (
            " Attributes could not be fetched for "
            + "; ".join(f"{name} ({reason})" for name, reason in failed.items())
            + "."
        )
    contract.assumptions = [*contract.assumptions, note]


def _place_facts(
    layer: LayerResult | None,
    lead: tuple[str, ...] = (),
    fetched_only: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """Every attribute the answer's place layer carries, for the fact corpus.

    The ACS fill attaches seven variables; the sentence prints one. The other
    six went onto the map and nowhere else, so a later question about income was
    answered "the analysis does not include ACS data" by a turn that was holding
    the income figure. `narrate` and `discuss` both read the fact corpus, so
    that is where a fetch has to land.

    Passed through whole rather than whitelisted, deliberately: a variable added
    to the fill should become answerable without a matching edit here, and the
    properties are already written under readable names.

    `lead` puts the variables the question asked for first.

    `fetched_only` restricts which fetched variables travel at all, and is how
    the narration facts are built. Handing the narrator all seven made it account
    for all seven, per city, in one sentence - and prompt wording could not hold
    that back. An answer's facts are what that answer needs; the full set goes to
    session memory, where a later question still reaches it. Locally computed
    properties - the name, the area share - are never restricted, because they
    are what the sentence is about.
    """
    if not layer:
        return []
    ordered: list[dict[str, Any]] = []
    for feature in layer.geojson.get("features") or []:
        properties = feature.get("properties")
        if not properties:
            continue
        if fetched_only is not None:
            properties = {
                key: value
                for key, value in properties.items()
                if key not in FETCHED_PROPERTIES or key in fetched_only
            }
        first = [key for key in lead if key in properties]
        ordered.append(
            {key: properties[key] for key in first + [k for k in properties if k not in first]}
        )
    return ordered


def _authorised_figures(
    layer: LayerResult | None, only: tuple[str, ...] | None = None
) -> tuple[str, ...]:
    """The fetched values an approved fill put on the layer, as a reply spells them.

    Every property the fill writes, not only the one the sentence prints. Asked
    about income, an analyst answers with income; when population was the only
    thing that counted, that reply satisfied nothing and was thrown away for a
    fallback about which cities were reached.

    Locally computed properties - the area share, the pixel count - are excluded
    on purpose. They are present whether or not anyone fetched anything, so
    accepting them would let a reply that fetched nothing pass as one that did.

    `only` narrows this to the variables the answer's facts actually carry.
    Demanding a figure that was trimmed out of those facts is unsatisfiable, and
    the reply it rejects is replaced by the template - which is how a sound
    answer became a recital again.
    """
    if not layer:
        return ()
    wanted = FETCHED_PROPERTIES if only is None else only
    figures: list[str] = []
    for feature in layer.geojson.get("features") or []:
        properties = feature.get("properties") or {}
        for name in wanted:
            value = properties.get(name)
            if value is None:
                continue
            figures.append(f"{value:,}" if isinstance(value, int) else f"{value:,.1f}")
    return tuple(dict.fromkeys(figures))


def _debris_facts(
    fire_name: str,
    *,
    drawn: int,
    truncated: bool,
    source: str,
    remaining: tuple[str, ...],
) -> dict[str, Any]:
    """What an approved debris-flow fetch put on the map, and how much of it.

    The county publishes 13,954 hazard polygons for one fire in this archive and
    the fetch is capped well below that. Reporting the drawn count as
    `hazard_areas` let it be stated as how many exist. What was drawn and how
    many there are are different figures, and only one of them was measured.
    """
    facts: dict[str, Any] = {
        "hazard_areas_drawn": drawn,
        "complete": not truncated,
        "burn_scar": fire_name,
        "source": source,
        "still_missing": list(remaining),
        "meaning": (
            "Modelled areas where debris flow is possible below this burn scar, "
            "published for planning. Not a forecast of any storm, and not a record "
            "that a debris flow has occurred."
        ),
    }
    if truncated:
        facts["coverage"] = (
            f"{drawn:,} polygons were drawn, which is the fetch limit and not the total "
            "the service holds for this burn scar. Treat the map as a sample of the "
            "hazard areas, not an inventory of them."
        )
    return facts


def _fire_record_message(
    fire_name: str,
    day: str,
    active_pixels: int,
    cumulative_burned_km2: float,
    *,
    burned_area_available: bool = True,
) -> str:
    """What the archive holds for one fire on one day.

    An event with no burned-area labels reported "cumulative mapped BA is
    approximately 0.0 km2", which reads as a quantity that was measured and came
    to nothing. Two events in this subset have no such labels on any day: for
    them the quantity does not exist, and saying so is the only honest form.

    A genuine zero on an event that does map burned area - day one of a fire,
    before anything accumulated - is still reported as zero, because there it is
    a real measurement.
    """
    head = (
        f"{fire_name}: TS-SatFire historical record for {day}. "
        f"The selected day contains {active_pixels:,} AF label pixels"
    )
    if not burned_area_available:
        return (
            f"{head}. This event carries no burned-area labels, on this day or any "
            "other, so no mapped burned area can be reported for it."
        )
    return f"{head}; cumulative mapped BA is approximately {cumulative_burned_km2:,.1f} km²."


def _answer_reads_population(layer: LayerResult | None) -> bool:
    """Whether the sentence about to be written would actually print the figures.

    `_fire_community_message` reports population on one branch only: the
    burned-area intersection. On every other branch the values are fetched,
    attached, and never mentioned, so asking the user to authorise the call
    spends their one question on something that cannot change a word they read.
    """
    return bool(
        layer
        and layer.feature_count
        and layer.capability_id == "burned_area_intersecting_place_boundaries"
    )


def _fire_community_message(
    fire_name: str,
    intersections: LayerResult | None,
    nearest: LayerResult | None,
    active_intersections: LayerResult | None = None,
    *,
    burned_area_available: bool = True,
) -> str:
    intersecting_names = _place_names(intersections)
    if intersecting_names:
        names = ", ".join(intersecting_names)
        if (
            intersections
            and intersections.capability_id == "burned_area_intersecting_place_boundaries"
        ):
            when = _human_date(intersections.as_of)
            # "Reached parts of" is true of one edge pixel and of half a city
            # alike. Naming the share is what stops the next question - who was
            # affected - from being answered with a whole city's population.
            shares = _place_shares(intersections)
            share_text = f" By area, {shares}." if shares else ""
            share_text += _place_population(intersections)
            base = (
                f"By {when}, the mapped burned area of the {fire_name} had reached parts "
                f"of {names}. This does not mean the whole cities burned—only that some "
                f"areas inside their city boundaries overlap the satellite map.{share_text}"
            )
        else:
            base = (
                f"The historical {fire_name} perimeter overlaps parts of {names}. "
                "This does not mean the whole cities burned or were uniformly damaged."
            )
    else:
        nearest_features = (nearest.geojson.get("features") or []) if nearest else []
        if nearest_features:
            ranked = ", ".join(
                f"{(feature.get('properties') or {}).get('name')} "
                f"({(feature.get('properties') or {}).get('distanceKm')} km)"
                for feature in nearest_features
            )
            if nearest and nearest.capability_id == "burned_area_nearest_place_reference_points":
                when = _human_date(nearest.as_of)
                base = (
                    f"By {when}, the mapped burned area had not reached a listed city "
                    f"boundary. The nearest cities were {ranked}. Being nearby does not "
                    "mean they were affected by the fire."
                )
            else:
                base = (
                    f"The historical {fire_name} perimeter does not overlap a listed city "
                    f"boundary. The nearest cities were {ranked}. Being nearby does not "
                    "prove fire impact."
                )
        elif not burned_area_available:
            # A permanent gap and a one-off failure must not read alike. This
            # event has no burned-area labels on any day, so "try again" is
            # advice that wastes the user's time.
            base = (
                f"The {fire_name} record carries no burned-area labels, on this day or "
                "any other, so its footprint cannot be compared with city boundaries. "
                "Only active-fire detections are available for this event."
            )
        else:
            base = (
                f"I could not compare the {fire_name} with city boundaries because the "
                "required mapped fire area was unavailable."
            )

    active_features = (
        active_intersections.geojson.get("features") or [] if active_intersections else []
    )
    if not active_features:
        return base
    active_places = ", ".join(
        f"{(feature.get('properties') or {}).get('name')} "
        f"({(feature.get('properties') or {}).get('labelPixelCount')} pixels)"
        for feature in active_features
    )
    return (
        f"{base} Separately, same-day active-fire signals appear inside {active_places}. "
        f"These signals do not show that those cities burned and may not belong to the "
        f"{fire_name}."
    )


async def _automatic_fire_subject(fire_name: str, year: int) -> LayerResult | None:
    """Prefer an official historical perimeter for the named local fire."""
    try:
        payload = await _fetch_public_mcp(PublicLayerIn(source="fire_history", start_year=year))
    except Exception:  # noqa: BLE001 - an unavailable upstream is valid demo state
        return None
    target = _normalise_incident_name(fire_name)
    features = [
        feature
        for feature in payload.get("features") or []
        if _normalise_incident_name(str((feature.get("properties") or {}).get("INCIDENT") or ""))
        == target
    ]
    if not features:
        return None
    metadata = payload.get("metadata") or {}
    return LayerResult(
        capability_id="subject_fire_perimeter",
        title=f"{fire_name} official perimeter",
        hazard_object="fire_perimeter",
        family="official_fire_perimeters",
        geometry_type="Polygon",
        caveat=(
            "Official final/historical fire perimeter. It represents mapped fire extent, "
            "not uniform burn severity or structure damage."
        ),
        feature_count=len(features),
        truncated=bool(metadata.get("truncated")),
        source="NIFC Interagency Fire Perimeter History",
        retrieved_at=metadata.get("retrievedAt"),
        visualization=public_layer_visualization("fire_history", "Polygon", features),
        geojson={"type": "FeatureCollection", "features": features},
    )


@app.post("/api/layers/public")
async def public_layer(body: PublicLayerIn) -> dict:
    """Fetch an allow-listed Southern California source through the MCP server."""
    payload = await _fetch_public_mcp(body)
    return {
        "metadata": payload.get("metadata") or {},
        "layers": [
            layer.model_dump(mode="json") for layer in _public_layer_results(body.source, payload)
        ],
    }


@app.post("/api/layers/local")
async def local_layer(body: LocalLayerIn) -> dict:
    """Clip one local, allow-listed snapshot to the current request bbox."""
    try:
        bbox = validate_bbox(body.bbox)
        layer = clip_local_layer(body.capability_id, bbox)
    except (KeyError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    layer = layer.model_copy(
        update={
            "capability_id": f"local_clip_{body.capability_id}",
            "title": f"Local clip · {layer.title}",
            "caveat": f"Clipped to the current request bbox. {layer.caveat}",
        }
    )
    return {"bbox": list(bbox), "layer": layer.model_dump(mode="json")}


@app.get("/api/local-data/raster-catalog")
async def local_raster_catalog() -> dict:
    """Expose safe ids and dates, never arbitrary local filesystem paths."""
    try:
        return await asyncio.to_thread(raster_catalog_payload)
    except (FileNotFoundError, RasterLayerError) as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/api/local-data/fire-events")
async def local_fire_events() -> dict:
    """Expose the local subset as historical wildfire events and task support."""
    try:
        return await asyncio.to_thread(fire_event_catalog_payload)
    except (FileNotFoundError, RasterLayerError) as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/layers/local-raster")
async def local_raster_layer(body: LocalRasterLayerIn) -> dict:
    """Crop and California-mask a local GeoTIFF for the current request."""
    try:
        return await asyncio.to_thread(
            render_raster_preview,
            body.dataset_id,
            body.bbox,
            day=body.day,
        )
    except (FileNotFoundError, RasterLayerError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/layers/fire-lifecycle")
async def fire_lifecycle_layer(body: FireLifecycleIn) -> dict:
    """Return event-scoped AF/BA overlays and a daily historical timeline."""
    try:
        return await asyncio.to_thread(render_fire_lifecycle, body.event_id, day=body.day)
    except (FileNotFoundError, RasterLayerError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/raster-previews/{token}.png", response_class=FileResponse)
async def raster_preview(token: str) -> FileResponse:
    """Serve only hash-addressed previews created by the trusted raster pipeline."""
    try:
        path = preview_path(token)
    except RasterLayerError as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(
        path,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=300"},
    )


@app.get("/api/fire-lifecycle-assets/{event_id}/{filename}", response_class=FileResponse)
async def fire_lifecycle_asset(event_id: str, filename: str) -> FileResponse:
    """Serve only validated, generated lifecycle PNGs from the cache root."""
    try:
        path = lifecycle_asset_path(event_id, filename)
    except RasterLayerError as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(
        path,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.post("/api/sessions")
async def create_session() -> dict:
    session_id = _session_store.create()
    _sessions.add(session_id)
    context = ConversationContext()
    # Loaded once per session so "what fires do you have?" is a question the
    # conversation can answer, rather than one only the sidebar could.
    try:
        context.remember_archive(_archive_summary())
    except Exception:
        # An unreadable archive is a missing answer, not a failed session.
        logger.exception("could not summarise the local fire archive")
    _session_contexts[session_id] = context
    # A new session has settled nothing, so consent from an earlier one cannot
    # carry into it.
    _fill_decisions[session_id] = FillDecisions()
    _pending_fills.pop(session_id, None)
    return {"session_id": session_id}


@app.get("/api/sessions")
async def list_sessions() -> dict:
    return {"sessions": _session_store.list()}


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str) -> dict:
    archived = _session_store.get(session_id)
    if archived is None:
        raise HTTPException(404, "unknown session")
    _sessions.add(session_id)
    if session_id not in _session_contexts:
        raw_context = _session_store.context(session_id)
        _session_contexts[session_id] = (
            ConversationContext.model_validate(raw_context)
            if raw_context
            else ConversationContext()
        )
    return archived


@app.put("/api/sessions/{session_id}/snapshot")
async def save_session_snapshot(session_id: str, body: SessionSnapshotIn) -> dict:
    if not _session_store.save_snapshot(session_id, body.snapshot):
        raise HTTPException(404, "unknown session")
    return {"ok": True}


@app.patch("/api/sessions/{session_id}")
async def rename_session(session_id: str, body: SessionRenameIn) -> dict:
    if not _session_store.rename(session_id, body.title):
        raise HTTPException(404, "unknown session")
    return {"ok": True}


def _forget_session(session_id: str) -> None:
    """Drop every trace of one session from process memory.

    Fetch decisions and a parked offer are keyed by session id, so leaving them
    behind means a deleted conversation's approvals outlive the conversation.
    """
    _sessions.discard(session_id)
    _session_contexts.pop(session_id, None)
    _fill_decisions.pop(session_id, None)
    _pending_fills.pop(session_id, None)


@app.delete("/api/sessions")
async def clear_sessions() -> dict:
    """Remove every stored conversation, and say how many that was.

    Idempotent, so a second press is a no-op rather than a 404. The count is
    what the caller shows before doing it: this is the one action in the API
    that cannot be undone.
    """
    deleted = _session_store.clear()
    for session_id in list(_sessions):
        _forget_session(session_id)
    _session_contexts.clear()
    _fill_decisions.clear()
    _pending_fills.clear()
    return {"deleted": deleted}


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str) -> dict:
    if not _session_store.delete(session_id):
        raise HTTPException(404, "unknown session")
    _forget_session(session_id)
    return {"ok": True}


@app.post("/api/sessions/{session_id}/messages")
async def post_message(session_id: str, body: MessageIn) -> EventSourceResponse:
    if session_id not in _sessions:
        raise HTTPException(404, "unknown session")

    if session_id not in _session_contexts:
        raw_context = _session_store.context(session_id)
        _session_contexts[session_id] = (
            ConversationContext.model_validate(raw_context)
            if raw_context
            else ConversationContext()
        )

    ok, detail = check_llm_ready()
    if not ok:
        raise HTTPException(503, detail)

    _session_store.touch_message(session_id, body.text)
    return EventSourceResponse(_guarded(_run(session_id, body), session_id))


async def _guarded(stream: AsyncIterator[dict], session_id: str) -> AsyncIterator[dict]:
    """Turn any escaping exception into a terminal `error` event.

    Only the graph loop inside `_run` used to be guarded. A failure in the
    rendering that follows it - a bad day on a raster timeline, an MCP call that
    raised - propagated out of the generator, and the browser saw a truncated
    chunked response: a bare "network error" with nothing to act on.
    """
    try:
        async for item in stream:
            yield item
        _session_store.mark_status(session_id, "complete")
    except Exception as exc:
        logger.exception("session stream failed")
        _session_store.mark_status(session_id, "failed")
        yield _sse("error", {"message": str(exc), "type": type(exc).__name__})


@dataclass
class _RenderOutcome:
    """What a renderer produced, for the memory commit that follows it.

    A renderer yields events and fills this in. Returning a value from an async
    generator is not possible, and threading half a dozen locals back out of one
    was what kept this rendering inside the orchestrator in the first place.
    """

    summary: str | None = None
    layer_ids: list[str] = field(default_factory=list)
    #: Fetch decisions this session has already made, so a settled question is
    #: acted on rather than asked again.
    decisions: FillDecisions = field(default_factory=FillDecisions)
    #: What an approved fetch returned this turn, by source id, for the session
    #: to keep. Structured attributes only - never geometry.
    fetched: dict[str, Any] = field(default_factory=dict)
    #: Place variables this turn asks about, as the resolver named them. Decides
    #: what the answer opens with and what travels into its facts.
    variables: tuple[str, ...] = ()
    #: An offer to make instead of answering, when data is missing, fetchable,
    #: and not yet decided.
    offer: PendingFill | None = None
    facts: dict[str, Any] | None = None
    #: Index-change results only. A context analysis has no formula or inputs,
    #: which is the shape the analytical memory records.
    analysis: dict[str, Any] | None = None


async def _render_fire_event(
    contract: AnalysisContract,
    fire_plan: FireRasterPlan,
    body: MessageIn,
    outcome: _RenderOutcome,
) -> AsyncIterator[dict]:
    """Everything a matched local fire event puts on screen."""
    emitted_layer_ids = outcome.layer_ids
    match: AnalysisMatch | None = None
    analysis_result: dict[str, Any] | None = None
    analysis_error: str | None = None
    response_summary: str | None = None

    fire_name = (fire_plan.dataset.event_name or "Local fire").removesuffix(" area")
    fire_year = int(fire_plan.day[:4])
    lifecycle = await asyncio.to_thread(
        render_fire_lifecycle,
        fire_plan.dataset.event_id or "",
        day=fire_plan.day,
    )
    # The lifecycle rasters travel on their own event rather than as
    # `layer`, so record them here too. A later discussion turn is
    # told what is on screen, and these are most of it.
    emitted_layer_ids.extend(layer["legend_label"] for layer in lifecycle["layers"])
    metrics = lifecycle["selected_metrics"]
    try:
        # One question of the registry replaces a chain of "is it
        # this analysis? is it that one?" - and a new analysis
        # becomes an entry there rather than another branch here.
        match = resolve_analysis(
            contract.analysis_request(),
            fire_plan,
            original=contract.original_request,
        )
        if match:
            analysis_result = await asyncio.to_thread(match.run)
    except (FileNotFoundError, RasterLayerError, ValueError) as exc:
        analysis_error = str(exc)
    has_burned_area = event_supports_burned_area(
        fire_plan.dataset.event_id or "", tuple(fire_plan.dataset.dates)
    )
    fire_data = {
        "status": "matched",
        "message": _fire_record_message(
            fire_name,
            fire_plan.day,
            metrics["active_pixels"],
            metrics["cumulative_burned_km2"],
            burned_area_available=has_burned_area,
        ),
    }
    official_fire_subject = await _automatic_fire_subject(fire_name, fire_year)
    fire_subject = official_fire_subject
    communities = (
        communities_intersecting_fire(official_fire_subject, fire_name)
        if official_fire_subject
        else None
    )
    # Nothing local carries a post-fire debris-flow hazard, so answering
    # this means finding a source at request time rather than reading one.
    # The user's own words first: a rewrite inherits the previous turn's
    # vocabulary and routinely drops the word that made this a debris-flow
    # question in the first place.
    asks_post_fire = _asks_post_fire_risk_question(
        contract.original_request
    ) or _asks_post_fire_risk_question(contract.analysis_request())
    if asks_post_fire:
        gap = missing_variables(DEBRIS_HAZARD_OBJECT)
        debris_offer = debris_flow_offer(fire_name, gap)
        debris_pending = (
            PendingFill(offer=debris_offer, capability_id="post_fire_debris_flow_hazard_areas")
            if debris_offer
            else None
        )
        if debris_pending and outcome.decisions.approved(debris_pending.key):
            found = await asyncio.to_thread(
                debris_flow_fetch, fire_name, debris_offer["endpoint"]
            )
            if found.layer:
                emitted_layer_ids.append("post_fire_debris_flow_hazard_areas")
                yield _sse("layer", {
                    "capability_id": "post_fire_debris_flow_hazard_areas",
                    "title": f"{fire_name} · potential debris-flow hazard areas",
                    "hazard_object": DEBRIS_HAZARD_OBJECT,
                    "family": None,
                    "geometry_type": "Polygon",
                    "caveat": (
                        "Modelled hazard areas published for planning. They mark where "
                        "debris flow is possible after this burn scar, not that one has "
                        "happened or is forecast."
                    ),
                    "feature_count": found.layer.feature_count,
                    "source": found.source.get("discovered_via", "portal"),
                    "as_of": fire_plan.day,
                    "truncated": found.layer.truncated,
                    "geojson": found.layer.geojson,
                    # A layer with no legend entry is 1,500 unexplained
                    # polygons; the review asked for every layer to say what
                    # it means.
                    "visualization": {
                        "kind": "categorical",
                        "label": "Potential debris-flow hazard area",
                        "field": "PHASE",
                        "stops": [
                            {"value": "1", "label": "Phase 1 assessment", "color": "#8d6e63"}
                        ],
                        "popup_fields": [
                            {"key": "FIRE", "label": "Burn scar"},
                            {"key": "PHASE", "label": "Assessment phase"},
                        ],
                        "explanation": (
                            "Modelled areas where debris flow is possible below this burn "
                            "scar. Published for planning; not a forecast of any storm."
                        ),
                    },
                })
                yield _sse("data_fill", {
                    "source": found.source,
                    "closed": list(found.closed),
                    "remaining": list(debris_offer["remaining"]),
                    "failed": {},
                    "suppressed": {},
                    "places_enriched": found.layer.feature_count,
                })
                # Drawn on the map and stated in the answer. Fetching a layer
                # the reply never mentions spends the user's approval on
                # something they cannot tell apart from having declined.
                _record_fill_on_contract(
                    contract,
                    source=str(found.source.get("discovered_via") or "a public ArcGIS catalogue"),
                    dataset=f"post-fire debris-flow hazard areas for the {fire_name} burn scar",
                    closed=tuple(found.closed),
                    remaining=tuple(debris_offer["remaining"]),
                    count=found.layer.feature_count,
                    unit=("hazard area", "hazard areas"),
                    complete=not found.layer.truncated,
                    failed={},
                )
                outcome.fetched["portal_debris_flow"] = _debris_facts(
                    fire_name,
                    drawn=found.layer.feature_count,
                    truncated=found.layer.truncated,
                    source=found.source.get("discovered_via", "portal"),
                    remaining=tuple(debris_offer["remaining"]),
                )
                fire_data["post_fire_debris_flow"] = _debris_facts(
                    fire_name,
                    drawn=found.layer.feature_count,
                    truncated=found.layer.truncated,
                    source=found.source.get("discovered_via", "portal"),
                    remaining=tuple(debris_offer["remaining"]),
                )
            elif found.note:
                yield _sse("data_fill", {
                    "source": {"endpoint": debris_offer["endpoint"]},
                    "closed": [],
                    "remaining": list(debris_offer["remaining"]),
                    "failed": {"post_fire_debris_flow": found.note},
                    "suppressed": {},
                    "places_enriched": 0,
                })
                # An approved fetch that came back empty is not the same answer
                # as one that was never offered, and must not read like it.
                fire_data["post_fire_debris_flow"] = {
                    "hazard_areas": 0,
                    "fetch_failed": found.note,
                    "burn_scar": fire_name,
                }
        elif debris_pending and outcome.decisions.should_ask(debris_pending.key):
            outcome.offer = debris_pending

    community_question = _asks_fire_community_question(contract.analysis_request())
    asked_attributes = outcome.variables
    # With no variable named, population is the default: it is what the sentence
    # prints and what the fill was offered for. Leaving it empty would strip
    # every fetched figure out of the answer's facts, and the approved fetch
    # would go invisible again.
    narrated_attributes = asked_attributes or ("population",)
    #: Figures the user was stopped and asked to authorise a fetch for. The
    #: narrator may not drop these, or approving the fetch buys them nothing.
    authorised_figures: tuple[str, ...] = ()
    answer_communities = communities
    nearest_communities = None
    active_communities = None
    if community_question:
        burned_result, active_result = await asyncio.gather(
            asyncio.to_thread(
                burned_area_label_points,
                fire_plan.dataset.event_id or "",
                fire_plan.day,
            ),
            asyncio.to_thread(
                active_fire_label_points,
                fire_plan.dataset.event_id or "",
                fire_plan.day,
            ),
            return_exceptions=True,
        )
        burned_points = () if isinstance(burned_result, Exception) else burned_result
        active_points = () if isinstance(active_result, Exception) else active_result
        # The cell size comes from the lifecycle that produced these points, so
        # "how much of this place" is measured against the same grid it was
        # counted on rather than a second, independently derived one.
        cell_km2 = float(lifecycle.get("pixel_area_km2") or 0.0)
        answer_communities = communities_intersecting_burned_area(
            burned_points,
            fire_name,
            fire_plan.day,
            pixel_area_km2=cell_km2,
        )
        active_communities = communities_intersecting_active_fire(
            active_points,
            fire_name,
            fire_plan.day,
            pixel_area_km2=cell_km2,
        )
        if not (answer_communities and answer_communities.feature_count):
            nearest_communities = communities_nearest_burned_area(
                burned_points,
                fire_name,
                fire_plan.day,
            )
        if not burned_points and official_fire_subject:
            answer_communities = communities
            nearest_communities = (
                communities_nearest_fire(official_fire_subject, fire_name)
                if not (communities and communities.feature_count)
                else None
            )

        # A place layer carries GEOIDs but no residents. Whether to go and get
        # them is the user's call, so this offers rather than fetches.
        #
        # Three things have to be true before it is worth a question, and each
        # of them was a way this asked for nothing. The answer about to be
        # written has to be one that prints the figures - the fallback above can
        # swap in the perimeter intersection, whose sentence never mentions
        # population. The fill has to close a variable the layer does not
        # already carry. And the session must not have settled this already:
        # `missing_variables` reads a static table, so left to itself it re-asks
        # a question the user answered three turns ago.
        if _answer_reads_population(answer_communities):
            gap = missing_variables(answer_communities.hazard_object)
            offer = offer_for(
                answer_communities.hazard_object,
                gap,
                already=variables_present(answer_communities),
            )
            pending = (
                PendingFill(offer=offer, capability_id=answer_communities.capability_id)
                if offer
                else None
            )
            if pending and outcome.decisions.approved(pending.key):
                enrichment = await asyncio.to_thread(enrich_places_with_acs, answer_communities)
                answer_communities = enrichment.layer
                authorised_figures = _authorised_figures(
                    answer_communities, only=narrated_attributes
                )
                if enrichment.source:
                    outcome.fetched["census_acs"] = {
                        "source": enrichment.source.get("source"),
                        "dataset": enrichment.source.get("dataset"),
                        "geography": enrichment.source.get("geography"),
                        "closed": list(enrichment.closed),
                        "still_missing": list(offer["remaining"]),
                        "places": _place_facts(answer_communities, lead=asked_attributes),
                        "do_not": (
                            "These are whole-place figures. They must never be multiplied "
                            "by a burned-area share to imply that many residents were "
                            "affected."
                        ),
                    }
                _record_fill_on_contract(
                    contract,
                    source=str(enrichment.source.get("source") or "the Census API"),
                    dataset=str(enrichment.source.get("dataset") or "ACS estimates"),
                    closed=tuple(enrichment.closed),
                    remaining=tuple(offer["remaining"]),
                    count=enrichment.enriched_count,
                    unit=("place", "places"),
                    complete=True,
                    failed=enrichment.failed,
                )
                if enrichment.source or enrichment.failed:
                    # Reported even when nothing came back. An approved fetch
                    # that quietly returned nothing is indistinguishable from a
                    # declined one, which is how consent gets spent for free.
                    yield _sse(
                        "data_fill",
                        {
                            "source": enrichment.source,
                            "closed": list(enrichment.closed),
                            "remaining": list(offer["remaining"]),
                            "failed": enrichment.failed,
                            "suppressed": {k: list(v) for k, v in enrichment.suppressed.items()},
                            "places_enriched": enrichment.enriched_count,
                        },
                    )
            elif pending and outcome.decisions.should_ask(pending.key):
                outcome.offer = pending
        fire_data["message"] = _fire_community_message(
            fire_name,
            answer_communities,
            nearest_communities,
            active_communities,
            burned_area_available=has_burned_area,
        )
    elif analysis_result:
        fire_data["message"] = analysis_result["summary"]
    elif analysis_error:
        fire_data["message"] = (
            f"I matched {fire_name}, but the requested raster calculation could "
            f"not be completed: {analysis_error}"
        )
    details = []
    if community_question and answer_communities:
        details.append(answer_communities.caveat)
    elif community_question and nearest_communities:
        details.append(nearest_communities.caveat)
    if community_question and active_communities and active_communities.feature_count:
        details.append(active_communities.caveat)
    if official_fire_subject:
        details.append(
            "The AF/BA overlays come from TS-SatFire; the official historical perimeter is shown only as separate context."
        )
        details.append(
            f"Census places whose polygons intersect the perimeter: {communities.feature_count if communities else 0}."
        )
    else:
        details.append(
            "No exact official perimeter was returned; the map is anchored directly to the event-scoped AF/BA label pixels, without a fabricated buffer."
        )
    details.append(
        "VIIRS_Day band 7 is treated as AF; band 8 ('first' locally) is treated as BA and accumulated through the selected date."
    )
    details.append(lifecycle["prediction_status"])
    if analysis_result:
        if analysis_result.get("formula"):
            details.append(f"Derived-raster formula: {analysis_result['formula']}.")
        details.extend(analysis_result["caveats"])
    if analysis_error:
        details.append(f"Derived raster analysis was not drawn: {analysis_error}")
    details.append(
        "Historical air quality is unavailable for this archived event; current AQI was not substituted."
    )
    if community_question:
        # Structured, beside the prose. The sentence prints what it has room
        # for; a later question about income or housing is answered from here
        # rather than refused by a turn that already holds the figure. Ordered so
        # the variable the question asked for is the one the reply opens with.
        fire_data["places"] = _place_facts(
            answer_communities, lead=narrated_attributes, fetched_only=narrated_attributes
        )
    fire_data.update({"workflow": "fire", "details": details})
    yield _sse(
        "fire_data",
        {key: value for key, value in fire_data.items() if key != "layer"},
    )
    yield _sse("fire_lifecycle", lifecycle)
    if match and analysis_result:
        yield _sse(match.spec.event, analysis_result)
    if fire_subject:
        emitted_layer_ids.append(fire_subject.capability_id)
        yield _sse("layer", fire_subject.model_dump(mode="json"))
    if communities and communities.feature_count:
        emitted_layer_ids.append(communities.capability_id)
        yield _sse("layer", communities.model_dump(mode="json"))
    if (
        answer_communities
        and answer_communities.feature_count
        and answer_communities is not communities
    ):
        emitted_layer_ids.append(answer_communities.capability_id)
        yield _sse("layer", answer_communities.model_dump(mode="json"))
    if nearest_communities and nearest_communities.feature_count:
        emitted_layer_ids.append(nearest_communities.capability_id)
        yield _sse("layer", nearest_communities.model_dump(mode="json"))
    if active_communities and active_communities.feature_count:
        emitted_layer_ids.append(active_communities.capability_id)
        yield _sse("layer", active_communities.model_dump(mode="json"))
    response_summary = await narrate(
        question=contract.analysis_request(),
        facts=fire_data,
        fallback=fire_data["message"],
        expertise=_expertise_label(body.expertise_override),
        preserve=authorised_figures,
    )
    yield _sse("summary", {"text": response_summary})

    outcome.summary = response_summary
    outcome.facts = fire_data
    if match and match.spec.notes.get("family") == "index_change":
        outcome.analysis = analysis_result


async def _render_city_context(
    contract: AnalysisContract,
    body: MessageIn,
    outcome: _RenderOutcome,
) -> AsyncIterator[dict]:
    """Current conditions for a resolved place, when no local fire event matched."""
    emitted_layer_ids = outcome.layer_ids

    status, layers = await _automatic_city_context(contract)

    # The resolved city polygon is a Census place: it carries a GEOID and the
    # `exposure` hazard object, which is everything the ACS fill needs. The fill
    # used to be wired only to the fire-community branch, so "what is the
    # population of Santa Barbara" was refused by a turn standing on the
    # plumbing that answers it.
    authorised_figures: tuple[str, ...] = ()
    subject_index = next(
        (i for i, layer in enumerate(layers) if layer.capability_id == "subject_city_boundary"),
        None,
    )
    asked = outcome.variables
    if subject_index is not None and asked:
        subject = layers[subject_index]
        offer = offer_for(
            subject.hazard_object,
            missing_variables(subject.hazard_object),
            already=variables_present(subject),
        )
        pending = (
            PendingFill(offer=offer, capability_id=subject.capability_id) if offer else None
        )
        if pending and outcome.decisions.approved(pending.key):
            enrichment = await asyncio.to_thread(enrich_places_with_acs, subject)
            layers[subject_index] = enrichment.layer
            status["message"] += _place_population(enrichment.layer)
            status["place"] = _place_facts(
                enrichment.layer, lead=asked, fetched_only=asked
            )
            status.setdefault("details", []).append(enrichment.layer.caveat)
            authorised_figures = _authorised_figures(enrichment.layer, only=asked)
            _record_fill_on_contract(
                contract,
                source=str(enrichment.source.get("source") or "the Census API"),
                dataset=str(enrichment.source.get("dataset") or "ACS estimates"),
                closed=tuple(enrichment.closed),
                remaining=tuple(offer["remaining"]),
                count=enrichment.enriched_count,
                unit=("place", "places"),
                complete=True,
                failed=enrichment.failed,
            )
            if enrichment.source or enrichment.failed:
                outcome.fetched["census_acs"] = {
                    "source": enrichment.source.get("source"),
                    "closed": list(enrichment.closed),
                    "still_missing": list(offer["remaining"]),
                    "places": _place_facts(enrichment.layer, lead=asked),
                    "failed": enrichment.failed,
                }
                yield _sse("data_fill", {
                    "source": enrichment.source,
                    "closed": list(enrichment.closed),
                    "remaining": list(offer["remaining"]),
                    "failed": enrichment.failed,
                    "suppressed": {k: list(v) for k, v in enrichment.suppressed.items()},
                    "places_enriched": enrichment.enriched_count,
                })
        elif pending and outcome.decisions.should_ask(pending.key):
            outcome.offer = pending

    yield _sse("fire_data", status)
    for layer in layers:
        emitted_layer_ids.append(layer.capability_id)
        yield _sse("layer", layer.model_dump(mode="json"))
    response_summary = await narrate(
        question=contract.analysis_request(),
        facts=status,
        fallback=status["message"],
        expertise=_expertise_label(body.expertise_override),
        preserve=authorised_figures,
    )
    yield _sse("summary", {"text": response_summary})

    outcome.summary = response_summary
    outcome.facts = status


#: The one scope this deployment answers for that is not a geocoded place.
#: Recorded as `fixed_scope` rather than a geocoder name, because nothing looked
#: it up - it is the boundary the national answer is about.
_CONUS_LOCATION = SpatialSlot(
    value="Contiguous United States",
    source="agent_inferred",
    confidence=0.99,
    resolved=ResolvedLocation(
        display_name="Contiguous United States",
        center=(-98.5, 39.5),
        bbox=(-125.0, 24.0, -66.5, 49.5),
        geocoder="fixed_scope",
    ),
)


async def _render_national(
    session_id: str, contract: AnalysisContract, body: MessageIn
) -> AsyncIterator[dict]:
    """Answer "is anything burning right now" for the country.

    No geocoding, no contract: there is no place to resolve. The one thing this
    must not do is let an empty published record read as an empty country.
    """
    try:
        payload = await _fetch_public_mcp(PublicLayerIn(source="wfigs"), national=True)
    except Exception as exc:
        # An unavailable upstream is valid state, but it is not "no fires".
        logger.exception("national fire status failed")
        yield _sse(
            "error",
            {
                "message": f"Current perimeters were unavailable: {exc}",
                "type": type(exc).__name__,
            },
        )
        return

    features = payload.get("features") or []
    status = _national_fire_status(features)
    # Onto the contract, because Limits renders `assumptions` and these two are
    # the caveats that matter: an empty record is not an empty country, and the
    # boundary drawn is not the boundary published.
    contract.assumptions = [
        *contract.assumptions,
        *status["details"],
        (
            "Perimeter boundaries are simplified to about 1 km for display at national "
            "scale; the demo scope draws them at full resolution."
        ),
    ]
    yield _sse("contract", _contract_payload(contract))
    yield _sse("fire_data", status)
    for layer in _public_layer_results("wfigs", payload):
        yield _sse("layer", layer.model_dump(mode="json"))

    summary = await narrate(
        question=body.text,
        facts=status,
        fallback=status["message"],
        expertise=_expertise_label(body.expertise_override),
    )
    yield _sse("summary", {"text": summary})

    context = _session_contexts.setdefault(session_id, ConversationContext())
    context.last_result = status
    context.remember("user", body.text)
    context.remember("agent", summary)


async def _render_answer(
    session_id: str,
    contract: AnalysisContract,
    body: MessageIn,
) -> AsyncIterator[dict]:
    """Render a settled contract, and commit the turn to analytical memory.

    Both the ordinary path and the answer to a fetch offer end here, so the two
    cannot drift into rendering the same contract differently.
    """
    fire_plan = plan_fire_raster(contract)
    outcome = _RenderOutcome(
        decisions=_fill_decisions.setdefault(session_id, FillDecisions()),
        variables=_question_variables(session_id, contract),
    )
    renderer = (
        _render_fire_event(contract, fire_plan, body, outcome)
        if fire_plan
        else _render_city_context(contract, body, outcome)
    )
    async for event in renderer:
        yield event

    if outcome.offer is not None:
        _pending_fills[session_id] = outcome.offer
        yield _sse("clarification", outcome.offer.as_clarification())

    _remember_completed_analysis(
        session_id,
        body.text,
        contract,
        fire_plan,
        outcome.summary,
        outcome.layer_ids,
        outcome.analysis,
        result_facts=outcome.facts,
        fetched=outcome.fetched,
    )


async def _run(session_id: str, body: MessageIn) -> AsyncIterator[dict]:
    config = {"configurable": {"thread_id": session_id}}

    # Waiting on a fetch decision? Then this message is that decision, and the
    # turn is a re-run of the same question with the answer applied - not a new
    # question that happens to say "yes".
    pending_to_restate: PendingFill | None = None
    pending = _pending_fills.pop(session_id, None)
    if pending is not None:
        approved = pending.decide(body.text)
        if approved is not None:
            # Settled for the session. The gap this came from is derived from a
            # static table that will report it as open again on the next turn;
            # this is what stops that from becoming the same question again.
            _fill_decisions.setdefault(session_id, FillDecisions()).record(
                pending.key, bool(approved)
            )
        if approved is None:
            # Neither agreement nor refusal. The offer stays open either way,
            # because consent must never be guessed. What differs is whether
            # there is a question to answer: a real one is carried through to
            # the pipeline below, and only noise ends the turn here.
            _pending_fills[session_id] = pending
            if not _is_a_new_question(body.text):
                yield _sse("turn", _turn_payload("discussion"))
                yield _sse("clarification", pending.as_clarification())
                return
            pending_to_restate = pending
        else:
            pending_to_restate = None
        parked = await _graph.aget_state(config)
        contract = parked.values.get("contract")
        if isinstance(contract, AnalysisContract) and contract.ready_for_planning:
            # The contract is already settled and sitting in the checkpointer, so
            # the decision only changes what the rendering fetches. Re-running the
            # graph would re-ask everything it already asked.
            # Answering a fetch offer is the second half of one question.
            yield _sse("turn", _turn_payload("analysis"))
            yield _sse("contract", _contract_payload(contract))
            async for event in _render_answer(session_id, contract, body):
                yield event
            yield _sse("done", _contract_payload(contract))
            return

    # Parked on an interrupt? Then this is a clarification answer, not a new question.
    snapshot = await _graph.aget_state(config)
    resuming = bool(snapshot.next) and bool(snapshot.tasks and snapshot.tasks[0].interrupts)

    payload: dict | Command
    if resuming:
        # Mid-clarification is still the same question; nothing is being replaced.
        yield _sse("turn", _turn_payload("analysis"))
        payload = Command(resume=body.text)
    else:
        context = _session_contexts.setdefault(session_id, ConversationContext())
        try:
            resolution = await resolve_turn(body.text, context)
        except ConversationResolutionError as exc:
            yield _sse("error", {"message": str(exc), "type": type(exc).__name__})
            return

        # A question about a hazard nothing local carries needs data, whatever
        # the resolver made of it. The same principle `request_intent` applies
        # to data selection: the user's own words settle it, and a rewrite that
        # inherited the previous turn's vocabulary does not get to say no.
        if resolution.kind == "discussion" and _asks_post_fire_risk_question(body.text):
            resolution.kind = "analysis"

        # And the same override in the other direction: a question about what
        # this deployment holds is answered from the archive in context, not by
        # running the pipeline and asking the user which area they meant.
        resolution.kind = _archive_kind(resolution.kind, body.text)

        # A question about the country names no place, so geocoding has nothing
        # to work with. The scope is supplied as a resolved fact instead - the
        # contiguous-US box is a constant, not a lookup - and everything else
        # runs normally. The contract and the stages are the real ones: skipping
        # them produced a right answer with an empty Reasoning panel, and a
        # fabricated contract would have been worse than an empty one.
        national = _asks_national_fire_question(body.text)

        # Asking in plain words for data to be fetched is an instruction. It was
        # being read as a remark about the map and answered "the analysis does
        # not include ACS data" - a refusal to do the one thing that was asked.
        # Consent is explicit in the words, so the offer is not put again; it is
        # recorded per source, so asking for one does not authorise the others.
        # Replaced every resolved turn, empty included: a question about income
        # must not keep leading the answer three turns after it was asked.
        context.active_variables = list(variables_to_lead(resolution))

        requested = sources_to_fetch(resolution, context)
        if requested:
            resolution.kind = "analysis"
            decisions = _fill_decisions.setdefault(session_id, FillDecisions())
            for source_id in requested:
                decisions.record(source_id, True)

        if resolution.kind == "discussion":
            # Asking what a result means is not a request for a new result. This
            # turn selects no layer and draws nothing, so the map keeps showing
            # the analysis the question is about.
            yield _sse("turn", _turn_payload("discussion"))
            try:
                answer = await discuss(
                    question=body.text,
                    context=context.prompt_payload(),
                    facts=context.last_result or {},
                    displayed=context.active_layer_ids,
                    expertise=_expertise_label(body.expertise_override),
                )
            except Exception as exc:
                logger.exception("discussion turn failed")
                yield _sse(
                    "error",
                    {
                        "message": (
                            "I could not answer that from the current analysis. "
                            f"({type(exc).__name__})"
                        ),
                        "type": type(exc).__name__,
                    },
                )
                return
            context.remember("user", body.text)
            context.remember("agent", answer)
            _session_store.save_context(session_id, context.model_dump(mode="json"))
            yield _sse("summary", {"text": answer})
            return

        yield _sse("turn", _turn_payload("analysis", subject_changed=_subject_changed(resolution)))
        prior_location = _CONUS_LOCATION.model_copy(deep=True) if national else None
        prior_contract = snapshot.values.get("contract")
        prior_fire_event_id = (
            context.active_subject.id
            if context.active_subject and context.active_subject.type == "fire_event"
            else None
        )
        prior_fire_day = context.active_time.selected if context.active_time else None
        if not national and isinstance(prior_contract, AnalysisContract):
            previous_spatial = prior_contract.spatial()
            if previous_spatial and previous_spatial.resolved:
                prior_location = previous_spatial.model_copy(deep=True)
        payload = {
            "original_request": body.text,
            "resolved_request": resolution.standalone_request,
            "context_resolution": resolution.model_dump(mode="json"),
            "transcript": [{"role": "user", "content": body.text}],
            "expertise_override": body.expertise_override,
            "clarification_rounds": 0,
            "stage": "requirement_understanding",
            "prior_location": prior_location,
            "prior_fire_event_id": prior_fire_event_id,
            "prior_fire_day": prior_fire_day,
        }

    latest_contract: AnalysisContract | None = None
    emitted_layer_ids: list[str] = []
    try:
        async for chunk in _graph.astream(payload, config=config, stream_mode="updates"):
            for node, update in chunk.items():
                if node == "__interrupt__":
                    # LangGraph surfaces interrupts through the same updates stream.
                    yield _sse("clarification", update[0].value)
                    continue

                yield _sse(
                    "stage",
                    {
                        "node": node,
                        "label": STAGE_LABELS.get(node, node),
                        "agent": STAGE_AGENTS.get(node),
                    },
                )

                update = update or {}

                contract = update.get("contract")
                if isinstance(contract, AnalysisContract):
                    latest_contract = contract
                    yield _sse("contract", _contract_payload(contract))

                automatic_context = bool(
                    latest_contract
                    and latest_contract.spatial()
                    and latest_contract.spatial().resolved
                )
                plan = update.get("plan")
                if isinstance(plan, ExecutionPlan) and not automatic_context:
                    yield _sse("plan", plan.model_dump(mode="json"))

                # Layers carry full GeoJSON, so each goes out on its own event
                # rather than in one payload the browser has to swallow whole.
                for layer in update.get("layers") or []:
                    if not automatic_context:
                        emitted_layer_ids.append(layer.capability_id)
                        yield _sse("layer", layer.model_dump(mode="json"))
                if update.get("layer_summary") and not automatic_context:
                    yield _sse("summary", {"text": update["layer_summary"]})
    except Exception as exc:  # noqa: BLE001 - report any stream failure, never swallow it
        yield _sse("error", {"message": str(exc), "type": type(exc).__name__})
        return

    final = await _graph.aget_state(config)
    contract = final.values.get("contract")
    if pending_to_restate is not None and session_id in _pending_fills:
        # The offer was never answered, so it is put again after the reply - the
        # user's question got its answer and the decision is still theirs.
        yield _sse("clarification", pending_to_restate.as_clarification())

    if isinstance(contract, AnalysisContract) and not final.next:
        if contract.ready_for_planning and national:
            async for event in _render_national(session_id, contract, body):
                yield event
        elif contract.ready_for_planning:
            async for event in _render_answer(session_id, contract, body):
                yield event
        yield _sse("done", _contract_payload(contract))
