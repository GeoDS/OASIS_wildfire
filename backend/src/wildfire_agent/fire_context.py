"""Composition, spread and fire-weather analyses over one event's footprint.

These three answer questions the raster index pipeline cannot: not "how much
changed" but "what kind of land burned", "which way did it run", and "under what
conditions". They share one builder because they share one prerequisite - the
cumulative burned-area object, day by day - and traversing the archive once is
cheaper than three times.

As with `spatial_analysis`, the request never reaches the computation. A request
selects a named analysis; the analysis itself is fixed code.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from .config import settings
from .raster_layers import (
    _PREVIEW_ROOT,
    FireRasterPlan,
    RasterLayerError,
    _event_center,
    _gdal_python,
    _inside_root,
    raster_datasets,
)

ContextAnalysis = Literal["land_cover_composition", "spread_behaviour", "fire_weather"]


class FireContextPlan(BaseModel):
    analysis_id: str
    event_id: str
    event_name: str
    analysis: ContextAnalysis
    end_date: str


def compile_fire_context(
    request: str,
    fire_plan: FireRasterPlan,
    *,
    analysis: ContextAnalysis,
) -> FireContextPlan | None:
    """Compile one named context analysis. Recognition happens in `analyses`."""
    if not fire_plan.dataset.event_id:
        return None
    event_id = fire_plan.dataset.event_id
    dataset = next(
        (
            item
            for item in raster_datasets()
            if item.event_id == event_id and item.variable == "VIIRS_Day"
        ),
        None,
    )
    if dataset is None or not dataset.dates:
        raise RasterLayerError("This event has no local VIIRS_Day record to analyse")
    end_date = dataset.dates[-1]
    seed = {"event_id": event_id, "end_date": end_date, "version": 1}
    return FireContextPlan(
        analysis_id=hashlib.sha256(json.dumps(seed, sort_keys=True).encode()).hexdigest()[:20],
        event_id=event_id,
        event_name=(dataset.event_name or event_id).removesuffix(" area"),
        analysis=analysis,
        end_date=end_date,
    )


def _payload(plan: FireContextPlan) -> dict[str, Any]:
    """Run the trusted builder once per event and reuse its output."""
    root = settings.resolved_local_data_root
    event_root = _inside_root(root / "full_data" / plan.event_id, root)
    metadata_path = _PREVIEW_ROOT / f"{plan.analysis_id}.context.json"
    if metadata_path.exists():
        return json.loads(metadata_path.read_text(encoding="utf-8"))

    dataset = next(
        (
            item
            for item in raster_datasets()
            if item.event_id == plan.event_id and item.variable == "VIIRS_Day"
        ),
        None,
    )
    center = _event_center(dataset) if dataset else None
    if center is None:
        raise RasterLayerError("The selected event has no catalogue centre")

    script = Path(__file__).resolve().parents[2] / "scripts" / "build_fire_context_analysis.py"
    if not script.exists():
        raise RasterLayerError("The trusted fire-context builder is unavailable")
    _PREVIEW_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [
                _gdal_python(),
                str(script),
                "--event-id",
                plan.event_id,
                "--viirs-dir",
                str(event_root / "VIIRS_Day"),
                "--firepred-dir",
                str(event_root / "FirePred"),
                "--end-date",
                plan.end_date,
                "--center-longitude",
                str(center[0]),
                "--center-latitude",
                str(center[1]),
                "--output",
                str(metadata_path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "Fire-context analysis failed").strip()
        raise RasterLayerError(detail[-700:]) from exc
    except subprocess.TimeoutExpired as exc:
        raise RasterLayerError("Fire-context analysis exceeded its 120 second limit") from exc
    return json.loads(metadata_path.read_text(encoding="utf-8"))


def _composition_summary(name: str, data: dict[str, Any]) -> tuple[str, list[str]]:
    composition = data.get("composition") or []
    if not composition:
        raise RasterLayerError("No land-cover record overlaps this event's footprint")
    leading = composition[:3]
    phrase = ", ".join(f"{item['label'].lower()} {item['percent']:.0f}%" for item in leading)
    terrain = data.get("terrain") or {}
    slope = terrain.get("slope_deg", {}).get("mean")
    elevation = terrain.get("elevation_m", {})
    sentences = [
        (
            f"Across the {data['footprint_area_km2']:,.0f} km² mapped burned area of the "
            f"{name}, the land cover is mostly {phrase}."
        )
    ]
    if elevation:
        sentences.append(
            f"The footprint spans {elevation['min']:,.0f} to {elevation['max']:,.0f} m in "
            f"elevation, averaging {elevation['mean']:,.0f} m"
            + (f" on slopes averaging {slope:.0f}°." if slope is not None else ".")
        )
    trend = data.get("elevation_trend")
    if trend:
        sentences.append(
            f"Land burned in the second half of the event sits {abs(trend['change_m']):,.0f} m "
            f"{trend['direction']} of that burned in the first half."
        )
    caveats = [
        (
            f"Land cover is read from {data.get('land_cover_source')}; it describes the class "
            "mapped for the pixel, not a field survey of what was standing when the fire arrived."
        ),
        (
            "Shares are of the mapped burned-area footprint, which is a satellite label rather "
            "than an official perimeter."
        ),
    ]
    return " ".join(sentences), caveats


def _spread_summary(name: str, data: dict[str, Any]) -> tuple[str, list[str]]:
    peak = data.get("peak_growth_day")
    alignment = data.get("wind_alignment")
    if not peak:
        raise RasterLayerError("This event's daily record shows no measurable growth")
    sentences = [
        (
            f"The {name}'s largest single-day growth was {peak['new_area_km2']:,.1f} km² on "
            f"{peak['date']}."
        )
    ]
    if peak.get("centroid_shift_km") and peak.get("spread_compass"):
        sentences.append(
            f"Its mapped centre moved {peak['centroid_shift_km']:.1f} km toward the "
            f"{peak['spread_compass']} that day."
        )
    conditions = peak.get("conditions") or {}
    wind = conditions.get("wind_speed", {}).get("mean")
    if wind is not None and peak.get("spread_wind_offset_deg") is not None:
        sentences.append(
            f"Wind over the newly burned pixels averaged {wind:.1f} m/s, and the direction the "
            f"fire moved sat {peak['spread_wind_offset_deg']:.0f}° from straight downwind."
        )
    if alignment:
        sentences.append(
            f"Across the whole event the area-weighted offset between spread and downwind is "
            f"{alignment['area_weighted_offset_deg']:.0f}°, with "
            f"{alignment['area_share_within_45_deg']:.0f}% of the burned area growing within "
            "45° of downwind."
        )
    caveats = [
        (
            "Direction is the shift of the footprint's centre between consecutive days, not a "
            "tracked fire front; on a quiet day that centre moves by less than a pixel and its "
            "bearing carries no meaning."
        ),
        (
            "Wind direction is read as the meteorological convention, the direction the wind "
            "blows from, and compared against its reciprocal."
        ),
        (
            "An alignment between spread and wind is an association. Terrain, fuel and "
            "suppression all shape a fire's run."
        ),
    ]
    return " ".join(sentences), caveats


def _weather_summary(name: str, data: dict[str, Any]) -> tuple[str, list[str]]:
    timeline = [entry for entry in data.get("timeline") or [] if entry.get("conditions")]
    if not timeline:
        raise RasterLayerError("This event carries no local fire-weather record")
    peak = data.get("peak_growth_day") or {}
    ercs = [
        entry["conditions"]["energy_release_component"]["mean"]
        for entry in timeline
        if "energy_release_component" in entry["conditions"]
    ]
    sentences = [f"Fire weather over the {name}'s footprint is recorded for {len(timeline)} days."]
    if ercs:
        sentences.append(
            f"The energy release component ranged from {min(ercs):.0f} to {max(ercs):.0f}, "
            f"averaging {sum(ercs) / len(ercs):.0f}."
        )
    conditions = peak.get("conditions") or {}
    if conditions:
        parts = []
        if "wind_speed" in conditions:
            parts.append(f"wind {conditions['wind_speed']['mean']:.1f} m/s")
        if "max_temperature_c" in conditions:
            parts.append(f"maximum temperature {conditions['max_temperature_c']['mean']:.0f} °C")
        if "energy_release_component" in conditions:
            parts.append(f"ERC {conditions['energy_release_component']['mean']:.0f}")
        if parts:
            sentences.append(
                f"On {peak['date']}, the day of largest growth, conditions over the newly "
                f"burned pixels were {', '.join(parts)}."
            )
    caveats = [
        (
            "Conditions are sampled over the pixels that burned that day, so they describe "
            "where the fire was, not a single station reading."
        ),
        (
            "Forecast bands are not reported: they use a different scale from their observed "
            "counterparts, so the two cannot be read side by side."
        ),
        "These are the model inputs distributed with the archive, not a verified weather record.",
    ]
    return " ".join(sentences), caveats


_SUMMARIES = {
    "land_cover_composition": _composition_summary,
    "spread_behaviour": _spread_summary,
    "fire_weather": _weather_summary,
}

_TITLES = {
    "land_cover_composition": "land cover and terrain",
    "spread_behaviour": "spread behaviour",
    "fire_weather": "fire weather",
}


def execute_fire_context(plan: FireContextPlan) -> dict[str, Any]:
    """Run - or reuse - the analysis and shape it for the wire."""
    data = _payload(plan)
    summary, caveats = _SUMMARIES[plan.analysis](plan.event_name, data)
    return {
        "analysis_id": plan.analysis_id,
        "event_id": plan.event_id,
        "event_name": plan.event_name,
        "analysis": plan.analysis,
        "title": f"{plan.event_name} · {_TITLES[plan.analysis]}",
        "end_date": plan.end_date,
        "footprint_area_km2": data.get("footprint_area_km2"),
        "land_cover_source": data.get("land_cover_source"),
        "composition": data.get("composition") or [],
        "terrain": data.get("terrain") or {},
        "elevation_trend": data.get("elevation_trend"),
        "wind_alignment": data.get("wind_alignment"),
        "peak_growth_day": data.get("peak_growth_day"),
        "timeline": data.get("timeline") or [],
        "summary": summary,
        "caveats": caveats,
    }
