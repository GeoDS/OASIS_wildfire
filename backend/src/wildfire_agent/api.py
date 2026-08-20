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
import uuid
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
from .contract import AnalysisContract
from .conversation import (
    AnalysisRef,
    ConversationContext,
    ConversationResolutionError,
    SubjectRef,
    TimeRef,
    resolve_turn,
)
from .events import EventName
from .graph import build_graph
from .graph.build import make_serde
from .graph.state import STAGE_AGENTS, STAGE_LABELS
from .llm import check_llm_ready, describe_llm, is_mock
from .narration import discuss, narrate
from .planning import CAPABILITIES, SHOWCASE_AREA, ExecutionPlan
from .planning.executor import clip_local_layer, validate_bbox
from .planning.models import LayerResult, LayerVisualization, LegendStop, PopupField
from .raster_layers import (
    FireRasterPlan,
    RasterLayerError,
    active_fire_label_points,
    burned_area_label_points,
    fire_event_catalog_payload,
    lifecycle_asset_path,
    plan_fire_raster,
    preview_path,
    raster_catalog_payload,
    render_fire_lifecycle,
    render_raster_preview,
)
from .request_intent import is_weather_only
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
_sessions: set[str] = set()
_session_contexts: dict[str, ConversationContext] = {}


class MessageIn(BaseModel):
    #: A turn costs several provider calls, and the endpoint takes no
    #: credentials, so an unbounded field turns one request into an unbounded
    #: bill. A real question is a sentence; this is far above that.
    text: str = Field(min_length=1, max_length=4000)
    #: Expertise picker in the UI - replays one question at three depths.
    expertise_override: ExpertiseLevel | None = None


PublicSource = Literal["weather", "air_quality", "wfigs", "hmsfire", "fire_history"]
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


def _remember_completed_analysis(
    session_id: str,
    user_text: str,
    contract: AnalysisContract,
    fire_plan: FireRasterPlan | None,
    summary: str | None,
    layer_ids: list[str],
    spatial_analysis: dict[str, Any] | None,
    result_facts: dict[str, Any] | None = None,
) -> None:
    """Commit one completed turn to structured analytical memory."""
    context = _session_contexts.setdefault(session_id, ConversationContext())
    context.last_result = result_facts
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
        layers.append(
            LayerResult(
                capability_id=f"public_{source}_{suffix}",
                title=f"API · {layer_title}",
                hazard_object="active_fire",
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


async def _fetch_public_mcp(body: PublicLayerIn) -> dict:
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
    air = next(iter(by_source.get("air_quality", [])), None)
    weather = next(
        (layer for layer in by_source.get("weather", []) if layer.geometry_type == "Point"),
        None,
    )
    city_name = (resolved.display_name or spatial.value or "Resolved city").split(",")[0]
    status = (
        city_weather_status(city_name, weather)
        if weather_only
        else city_context_status(city_name, perimeter, air, weather)
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


def _asks_fire_community_question(value: str) -> bool:
    """Identify a fire-to-community spatial follow-up from user wording."""
    return bool(
        re.search(r"\b(?:cities|city|communities|community|towns?|places?)\b", value, re.IGNORECASE)
        and re.search(
            r"\b(?:intersect(?:ed|s|ing)?|closest|nearest|near|contain(?:ed|s|ing)?|"
            r"have|with|affect(?:ed|s|ing)?|impact(?:ed|s|ing)?)\b",
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


def _fire_community_message(
    fire_name: str,
    intersections: LayerResult | None,
    nearest: LayerResult | None,
    active_intersections: LayerResult | None = None,
) -> str:
    intersecting_names = _place_names(intersections)
    if intersecting_names:
        names = ", ".join(intersecting_names)
        if (
            intersections
            and intersections.capability_id == "burned_area_intersecting_place_boundaries"
        ):
            when = _human_date(intersections.as_of)
            base = (
                f"By {when}, the mapped burned area of the {fire_name} had reached parts "
                f"of {names}. This does not mean the whole cities burned—only that some "
                "areas inside their city boundaries overlap the satellite map."
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
        hazard_object="active_fire",
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
    session_id = str(uuid.uuid4())
    _sessions.add(session_id)
    _session_contexts[session_id] = ConversationContext()
    return {"session_id": session_id}


@app.post("/api/sessions/{session_id}/messages")
async def post_message(session_id: str, body: MessageIn) -> EventSourceResponse:
    if session_id not in _sessions:
        raise HTTPException(404, "unknown session")

    ok, detail = check_llm_ready()
    if not ok:
        raise HTTPException(503, detail)

    return EventSourceResponse(_guarded(_run(session_id, body)))


async def _guarded(stream: AsyncIterator[dict]) -> AsyncIterator[dict]:
    """Turn any escaping exception into a terminal `error` event.

    Only the graph loop inside `_run` used to be guarded. A failure in the
    rendering that follows it - a bad day on a raster timeline, an MCP call that
    raised - propagated out of the generator, and the browser saw a truncated
    chunked response: a bare "network error" with nothing to act on.
    """
    try:
        async for item in stream:
            yield item
    except Exception as exc:
        logger.exception("session stream failed")
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
    fire_data = {
        "status": "matched",
        "message": (
            f"{fire_name}: TS-SatFire historical record for {fire_plan.day}. "
            f"The selected day contains {metrics['active_pixels']:,} AF label pixels; "
            f"cumulative mapped BA is approximately "
            f"{metrics['cumulative_burned_km2']:,.1f} km²."
        ),
    }
    official_fire_subject = await _automatic_fire_subject(fire_name, fire_year)
    fire_subject = official_fire_subject
    communities = (
        communities_intersecting_fire(official_fire_subject, fire_name)
        if official_fire_subject
        else None
    )
    community_question = _asks_fire_community_question(contract.analysis_request())
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
        answer_communities = communities_intersecting_burned_area(
            burned_points,
            fire_name,
            fire_plan.day,
        )
        active_communities = communities_intersecting_active_fire(
            active_points,
            fire_name,
            fire_plan.day,
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
        fire_data["message"] = _fire_community_message(
            fire_name,
            answer_communities,
            nearest_communities,
            active_communities,
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
    yield _sse("fire_data", status)
    for layer in layers:
        emitted_layer_ids.append(layer.capability_id)
        yield _sse("layer", layer.model_dump(mode="json"))
    response_summary = await narrate(
        question=contract.analysis_request(),
        facts=status,
        fallback=status["message"],
        expertise=_expertise_label(body.expertise_override),
    )
    yield _sse("summary", {"text": response_summary})

    outcome.summary = response_summary
    outcome.facts = status


async def _run(session_id: str, body: MessageIn) -> AsyncIterator[dict]:
    config = {"configurable": {"thread_id": session_id}}

    # Parked on an interrupt? Then this is a clarification answer, not a new question.
    snapshot = await _graph.aget_state(config)
    resuming = bool(snapshot.next) and bool(snapshot.tasks and snapshot.tasks[0].interrupts)

    payload: dict | Command
    if resuming:
        yield _sse("turn", {"kind": "analysis"})
        payload = Command(resume=body.text)
    else:
        context = _session_contexts.setdefault(session_id, ConversationContext())
        try:
            resolution = await resolve_turn(body.text, context)
        except ConversationResolutionError as exc:
            yield _sse("error", {"message": str(exc), "type": type(exc).__name__})
            return

        if resolution.kind == "discussion":
            # Asking what a result means is not a request for a new result. This
            # turn selects no layer and draws nothing, so the map keeps showing
            # the analysis the question is about.
            yield _sse("turn", {"kind": "discussion"})
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
            yield _sse("summary", {"text": answer})
            return

        yield _sse("turn", {"kind": "analysis"})
        prior_location = None
        prior_contract = snapshot.values.get("contract")
        prior_fire_event_id = (
            context.active_subject.id
            if context.active_subject and context.active_subject.type == "fire_event"
            else None
        )
        prior_fire_day = context.active_time.selected if context.active_time else None
        if isinstance(prior_contract, AnalysisContract):
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
    if isinstance(contract, AnalysisContract) and not final.next:
        if contract.ready_for_planning:
            fire_plan = plan_fire_raster(contract)
            outcome = _RenderOutcome(layer_ids=emitted_layer_ids)
            renderer = (
                _render_fire_event(contract, fire_plan, body, outcome)
                if fire_plan
                else _render_city_context(contract, body, outcome)
            )
            async for event in renderer:
                yield event
            _remember_completed_analysis(
                session_id,
                body.text,
                contract,
                fire_plan,
                outcome.summary,
                outcome.layer_ids,
                outcome.analysis,
                result_facts=outcome.facts,
            )
        yield _sse("done", _contract_payload(contract))
