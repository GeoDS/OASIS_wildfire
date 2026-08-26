"""Fire-first and city-first context layers selected by the backend."""

from __future__ import annotations

import itertools
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import shapefile

from .config import settings
from .contract import AnalysisContract
from .planning.models import LayerResult, LayerVisualization, LegendStop, PopupField

_PLACES_RELATIVE = Path("boundaries/california_places_2025/tl_2025_06_place.shp")
_SOCAL_BBOX = (-121.0, 32.4, -114.0, 35.9)


@dataclass(frozen=True)
class PlaceBoundary:
    name: str
    legal_name: str
    geoid: str
    class_code: str
    center: tuple[float, float]
    bbox: tuple[float, float, float, float]
    geometry: dict[str, Any]
    #: Official land area from TIGER's own `ALAND`, in km². Taken from the
    #: attribute rather than computed from the ring, because the shapefile does
    #: not guarantee a winding order and a signed shoelace would silently treat
    #: a hole as extra area.
    land_area_km2: float



def _normalise_place_name(value: str) -> str:
    value = value.split(",", 1)[0]
    value = re.sub(r"\b(?:city|town|village|cdp)\b", " ", value, flags=re.IGNORECASE)
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


def _bbox_intersects(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> bool:
    return not (
        first[2] < second[0] or first[0] > second[2] or first[3] < second[1] or first[1] > second[3]
    )


@lru_cache(maxsize=1)
def california_places() -> tuple[PlaceBoundary, ...]:
    """Load official incorporated-place and CDP boundaries from local TIGER/Line."""
    path = settings.resolved_local_data_root / _PLACES_RELATIVE
    if not path.exists():
        return ()
    output: list[PlaceBoundary] = []
    with shapefile.Reader(str(path)) as reader:
        for shape_record in reader.iterShapeRecords():
            record = shape_record.record.as_dict()
            shape = shape_record.shape
            bbox = tuple(float(value) for value in shape.bbox)
            if len(bbox) != 4 or not _bbox_intersects(bbox, _SOCAL_BBOX):
                continue
            output.append(
                PlaceBoundary(
                    name=str(record.get("NAME") or ""),
                    legal_name=str(record.get("NAMELSAD") or record.get("NAME") or ""),
                    geoid=str(record.get("GEOID") or ""),
                    land_area_km2=float(record.get("ALAND") or 0.0) / 1_000_000,
                    class_code=str(record.get("CLASSFP") or ""),
                    center=(
                        float(record.get("INTPTLON") or 0),
                        float(record.get("INTPTLAT") or 0),
                    ),
                    bbox=bbox,  # type: ignore[arg-type]
                    geometry=dict(shape.__geo_interface__),
                )
            )
    return tuple(output)


def _closest_place(name: str, center: tuple[float, float]) -> PlaceBoundary | None:
    key = _normalise_place_name(name)
    matches = [place for place in california_places() if _normalise_place_name(place.name) == key]
    if not matches:
        return None
    return min(matches, key=lambda place: _distance_km(center, place.center))


def city_subject_layer(contract: AnalysisContract) -> LayerResult | None:
    """Return the real administrative/statistical polygon for a city workflow."""
    spatial = contract.spatial()
    resolved = spatial.resolved if spatial else None
    if not resolved or not resolved.center:
        return None
    place = _closest_place(resolved.display_name or spatial.value or "", resolved.center)
    if not place:
        return None
    feature = {
        "type": "Feature",
        "geometry": place.geometry,
        "properties": {
            "name": place.name,
            "legalName": place.legal_name,
            "geoid": place.geoid,
            "classCode": place.class_code,
            "geometryRole": "administrative_boundary",
        },
    }
    return LayerResult(
        capability_id="subject_city_boundary",
        title=f"{place.name} boundary",
        hazard_object="exposure",
        family=None,
        geometry_type="Polygon",
        caveat=(
            "The mapped subject is the Census place/CDP boundary; the backend query envelope "
            "is separate and is not interpreted as the city or an impact zone."
        ),
        feature_count=1,
        source="U.S. Census Bureau TIGER/Line 2025 Places",
        as_of="2025-01-01",
        visualization=LayerVisualization(
            kind="fixed",
            label="Resolved place boundary",
            color="#356f82",
            popup_fields=[
                PopupField(key="legalName", label="Census place"),
                PopupField(key="geoid", label="Census GEOID"),
                PopupField(key="classCode", label="Place class"),
            ],
            explanation="Official Census place/CDP geometry used as the city subject.",
        ),
        geojson={"type": "FeatureCollection", "features": [feature]},
    )


def _distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1 = map(math.radians, a)
    lon2, lat2 = map(math.radians, b)
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 6371.0088 * 2 * math.asin(math.sqrt(h))


def _coordinates(value: Any):
    if isinstance(value, (list, tuple)):
        if len(value) >= 2 and all(isinstance(item, (int, float)) for item in value[:2]):
            yield float(value[0]), float(value[1])
        else:
            for item in value:
                yield from _coordinates(item)


def _rings(geometry: dict[str, Any]) -> list[list[tuple[float, float]]]:
    coordinates = geometry.get("coordinates") or []
    if geometry.get("type") == "Polygon":
        polygons = [coordinates]
    elif geometry.get("type") == "MultiPolygon":
        polygons = coordinates
    else:
        return []
    return [
        [(float(point[0]), float(point[1])) for point in ring]
        for polygon in polygons
        for ring in polygon
        if len(ring) >= 3
    ]


def _point_in_ring(point: tuple[float, float], ring: list[tuple[float, float]]) -> bool:
    x, y = point
    inside = False
    previous = ring[-1]
    for current in ring:
        x1, y1 = previous
        x2, y2 = current
        if (y1 > y) != (y2 > y):
            crossing_x = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < crossing_x:
                inside = not inside
        previous = current
    return inside


def _orientation(
    first: tuple[float, float],
    second: tuple[float, float],
    third: tuple[float, float],
) -> float:
    return (second[0] - first[0]) * (third[1] - first[1]) - (second[1] - first[1]) * (
        third[0] - first[0]
    )


def _segments_intersect(
    a1: tuple[float, float],
    a2: tuple[float, float],
    b1: tuple[float, float],
    b2: tuple[float, float],
) -> bool:
    if not _bbox_intersects(
        (
            min(a1[0], a2[0]),
            min(a1[1], a2[1]),
            max(a1[0], a2[0]),
            max(a1[1], a2[1]),
        ),
        (
            min(b1[0], b2[0]),
            min(b1[1], b2[1]),
            max(b1[0], b2[0]),
            max(b1[1], b2[1]),
        ),
    ):
        return False
    return (_orientation(a1, a2, b1) * _orientation(a1, a2, b2) <= 0) and (
        _orientation(b1, b2, a1) * _orientation(b1, b2, a2) <= 0
    )


def geometry_intersects(first: dict[str, Any], second: dict[str, Any]) -> bool:
    """Small dependency-free polygon intersection used for demo-scale layers."""
    first_rings = _rings(first)
    second_rings = _rings(second)
    if not first_rings or not second_rings:
        return False
    first_points = [point for ring in first_rings for point in ring]
    second_points = [point for ring in second_rings for point in ring]
    first_bbox = (
        min(point[0] for point in first_points),
        min(point[1] for point in first_points),
        max(point[0] for point in first_points),
        max(point[1] for point in first_points),
    )
    second_bbox = (
        min(point[0] for point in second_points),
        min(point[1] for point in second_points),
        max(point[0] for point in second_points),
        max(point[1] for point in second_points),
    )
    if not _bbox_intersects(first_bbox, second_bbox):
        return False
    if any(_point_in_ring(point, second_rings[0]) for point in first_points):
        return True
    if any(_point_in_ring(point, first_rings[0]) for point in second_points):
        return True
    for first_ring in first_rings:
        for second_ring in second_rings:
            for first_start, first_end in itertools.pairwise(first_ring):
                for second_start, second_end in itertools.pairwise(second_ring):
                    if _segments_intersect(first_start, first_end, second_start, second_end):
                        return True
    return False


def _geometry_intersects(geometry: dict, bbox: tuple[float, float, float, float]) -> bool:
    points = list(_coordinates(geometry.get("coordinates")))
    if not points:
        return False
    west, south, east, north = bbox
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return not (max(xs) < west or min(xs) > east or max(ys) < south or min(ys) > north)


def filter_layer_to_bbox(
    layer: LayerResult,
    bbox: tuple[float, float, float, float],
) -> LayerResult:
    features = [
        feature
        for feature in layer.geojson.get("features", [])
        if _geometry_intersects(feature.get("geometry") or {}, bbox)
    ]
    return layer.model_copy(
        update={
            "feature_count": len(features),
            "geojson": {"type": "FeatureCollection", "features": features},
        }
    )


def filter_layer_to_subject(layer: LayerResult, subject: LayerResult) -> LayerResult:
    """Keep only polygons that actually intersect the subject polygon."""
    subject_features = subject.geojson.get("features") or []
    if not subject_features:
        return layer.model_copy(
            update={
                "feature_count": 0,
                "geojson": {"type": "FeatureCollection", "features": []},
            }
        )
    subject_geometry = subject_features[0].get("geometry") or {}
    features = [
        feature
        for feature in layer.geojson.get("features", [])
        if geometry_intersects(feature.get("geometry") or {}, subject_geometry)
    ]
    return layer.model_copy(
        update={
            "feature_count": len(features),
            "geojson": {"type": "FeatureCollection", "features": features},
        }
    )


def communities_intersecting_fire(fire_subject: LayerResult, fire_name: str) -> LayerResult | None:
    """Return place polygons that overlap an official fire perimeter."""
    fire_features = fire_subject.geojson.get("features") or []
    if not fire_features:
        return None
    fire_geometries = [feature.get("geometry") or {} for feature in fire_features]
    features = []
    for place in california_places():
        if not any(geometry_intersects(place.geometry, fire) for fire in fire_geometries):
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": place.geometry,
                "properties": {
                    "name": place.name,
                    "legalName": place.legal_name,
                    "geoid": place.geoid,
                    "relation": "intersects official fire perimeter",
                    "geometryRole": "intersecting_place_boundary",
                },
            }
        )
    return LayerResult(
        capability_id="fire_intersecting_place_boundaries",
        title=f"Places intersecting {fire_name}",
        hazard_object="exposure",
        family=None,
        geometry_type="Polygon",
        caveat=(
            "Boundary intersection is stronger than proximity, but it does not prove every "
            "part of the place burned or establish structure-level damage."
        ),
        feature_count=len(features),
        source="Derived from NIFC perimeter × U.S. Census TIGER/Line 2025 Places",
        visualization=LayerVisualization(
            kind="categorical",
            label="Place/perimeter relationship",
            field="relation",
            stops=[
                LegendStop(
                    value="intersects official fire perimeter",
                    label="Intersects official perimeter",
                    color="#e0a13e",
                )
            ],
            popup_fields=[
                PopupField(key="legalName", label="Place"),
                PopupField(key="relation", label="Spatial relationship"),
            ],
            explanation="Polygon overlap only; it does not imply uniform damage.",
        ),
        geojson={"type": "FeatureCollection", "features": features},
    )


def _communities_intersecting_label_points(
    points: tuple[tuple[float, float], ...],
    fire_name: str,
    day: str,
    *,
    kind: str,
    pixel_area_km2: float = 0.0,
) -> LayerResult | None:
    """Intersect one lifecycle label with Census places and retain pixel counts."""
    if not points:
        return None
    if kind == "burned_area":
        capability_id = "burned_area_intersecting_place_boundaries"
        title = f"Places intersecting {fire_name} mapped BA"
        relation = "contains cumulative BA label pixel center"
        geometry_role = "burned_area_intersecting_place_boundary"
        source = "Derived from TS-SatFire BA labels × U.S. Census TIGER/Line 2025 Places"
        legend_label = "Intersects mapped burned area"
        legend_color = "#e0a13e"
        layer_label = "Cities overlapping mapped burned area"
        date_label = "BA date"
        pixel_label = "BA pixel centers"
        caveat = (
            "A place is counted when its Census polygon contains at least one cumulative "
            "TS-SatFire BA pixel center. This is a dataset-label intersection, not proof "
            "of uniform burning, structure damage, or population exposure."
        )
        explanation = "Burned-area pixel intersection only; not a damage assessment."
    elif kind == "active_fire":
        capability_id = "active_fire_intersecting_place_boundaries"
        title = f"Places containing same-day AF signals near {fire_name}"
        relation = "contains same-day AF label pixel center"
        geometry_role = "active_fire_intersecting_place_boundary"
        source = "Derived from TS-SatFire AF labels × U.S. Census TIGER/Line 2025 Places"
        legend_label = "Contains same-day active-fire signal"
        legend_color = "#c94f36"
        layer_label = "Cities containing same-day active-fire signals"
        date_label = "AF date"
        pixel_label = "AF pixel centers"
        caveat = (
            "A place is counted when its Census polygon contains at least one same-day "
            "TS-SatFire AF pixel center. An isolated AF signal does not prove the city "
            "burned or that the signal belonged to the named event."
        )
        explanation = "Same-day active-fire signals; event attribution is not established."
    else:
        raise ValueError(f"Unknown lifecycle label kind: {kind}")
    features = []
    for place in california_places():
        candidate_points = [
            point
            for point in points
            if place.bbox[0] <= point[0] <= place.bbox[2]
            and place.bbox[1] <= point[1] <= place.bbox[3]
        ]
        rings = _rings(place.geometry)
        matching_points = [
            point
            for point in candidate_points
            if any(_point_in_ring(point, ring) for ring in rings)
        ]
        if not matching_points:
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": place.geometry,
                "properties": {
                    "name": place.name,
                    "legalName": place.legal_name,
                    "geoid": place.geoid,
                    "relation": relation,
                    "labelPixelCount": len(matching_points),
                    "labelAreaKm2": round(len(matching_points) * pixel_area_km2, 2),
                    "placeAreaKm2": round(place.land_area_km2, 2),
                    # Share of the place's own land area, which is the figure that
                    # keeps "this city was touched" from reading as "this city burned".
                    "labelSharePercent": (
                        round(
                            len(matching_points) * pixel_area_km2 / place.land_area_km2 * 100,
                            1,
                        )
                        if pixel_area_km2 and place.land_area_km2
                        else None
                    ),
                    "date": day,
                    "geometryRole": geometry_role,
                },
            }
        )
    return LayerResult(
        capability_id=capability_id,
        title=title,
        hazard_object="exposure",
        family=None,
        geometry_type="Polygon",
        caveat=caveat,
        feature_count=len(features),
        source=source,
        as_of=day,
        visualization=LayerVisualization(
            kind="categorical",
            label=layer_label,
            field="relation",
            stops=[
                LegendStop(
                    value=relation,
                    label=legend_label,
                    color=legend_color,
                )
            ],
            popup_fields=[
                PopupField(key="legalName", label="Place"),
                PopupField(key="labelPixelCount", label=pixel_label),
                PopupField(key="labelSharePercent", label="Share of place area (%)"),
                PopupField(key="labelAreaKm2", label="Area within place (km²)"),
                PopupField(key="relation", label="Spatial relationship"),
                PopupField(key="date", label=date_label),
            ],
            explanation=explanation,
        ),
        geojson={"type": "FeatureCollection", "features": features},
    )


def communities_intersecting_burned_area(
    burned_points: tuple[tuple[float, float], ...],
    fire_name: str,
    day: str,
    *,
    pixel_area_km2: float = 0.0,
) -> LayerResult | None:
    """Intersect cumulative TS-SatFire BA pixel centers with Census places."""
    return _communities_intersecting_label_points(
        burned_points,
        fire_name,
        day,
        kind="burned_area",
        pixel_area_km2=pixel_area_km2,
    )


def communities_intersecting_active_fire(
    active_points: tuple[tuple[float, float], ...],
    fire_name: str,
    day: str,
    *,
    pixel_area_km2: float = 0.0,
) -> LayerResult | None:
    """Intersect same-day TS-SatFire AF pixel centers with Census places."""
    return _communities_intersecting_label_points(
        active_points,
        fire_name,
        day,
        kind="active_fire",
        pixel_area_km2=pixel_area_km2,
    )


def communities_nearest_burned_area(
    burned_points: tuple[tuple[float, float], ...],
    fire_name: str,
    day: str,
    *,
    limit: int = 5,
) -> LayerResult | None:
    """Rank Census place reference points by distance to cumulative BA pixels."""
    if not burned_points:
        return None
    ranked = sorted(
        (
            (min(_distance_km(place.center, point) for point in burned_points), place)
            for place in california_places()
        ),
        key=lambda item: item[0],
    )[: max(1, limit)]
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": list(place.center)},
            "properties": {
                "name": place.name,
                "legalName": place.legal_name,
                "geoid": place.geoid,
                "distanceKm": round(distance, 1),
                "relation": "nearest Census place reference point to mapped BA pixel",
                "date": day,
                "geometryRole": "nearest_place_reference_point",
            },
        }
        for distance, place in ranked
    ]
    return LayerResult(
        capability_id="burned_area_nearest_place_reference_points",
        title=f"Places closest to {fire_name} mapped BA",
        hazard_object="exposure",
        family=None,
        geometry_type="Point",
        caveat=(
            "Distance is measured from each Census place reference point to the nearest "
            "cumulative TS-SatFire BA pixel center; proximity does not establish impact."
        ),
        feature_count=len(features),
        source="Derived from TS-SatFire BA labels × U.S. Census TIGER/Line 2025 Places",
        as_of=day,
        visualization=LayerVisualization(
            kind="graduated",
            label="Approximate distance to mapped BA",
            field="distanceKm",
            unit="km",
            stops=[
                LegendStop(value=5, label="≤ 5 km", color="#d95f35"),
                LegendStop(value=15, label="≤ 15 km", color="#e9a23b"),
                LegendStop(value=30, label="≤ 30 km", color="#e8cf6a"),
            ],
            popup_fields=[
                PopupField(key="legalName", label="Place"),
                PopupField(key="distanceKm", label="Approx. distance", unit="km"),
                PopupField(key="date", label="BA date"),
            ],
            explanation="Reference-point proximity to BA pixels; not an impact assessment.",
        ),
        geojson={"type": "FeatureCollection", "features": features},
    )


def _point_to_segment_km(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    """Approximate local point-to-segment distance on an equirectangular plane."""
    lon, lat = point
    scale_x = 111.195 * math.cos(math.radians(lat))
    ax = (start[0] - lon) * scale_x
    ay = (start[1] - lat) * 111.195
    bx = (end[0] - lon) * scale_x
    by = (end[1] - lat) * 111.195
    dx, dy = bx - ax, by - ay
    length_squared = dx * dx + dy * dy
    if length_squared == 0:
        return math.hypot(ax, ay)
    projection = max(0.0, min(1.0, -(ax * dx + ay * dy) / length_squared))
    return math.hypot(ax + projection * dx, ay + projection * dy)


def _point_to_polygon_km(point: tuple[float, float], geometry: dict[str, Any]) -> float:
    rings = _rings(geometry)
    if not rings:
        return math.inf
    if any(_point_in_ring(point, ring) for ring in rings):
        return 0.0
    return min(
        _point_to_segment_km(point, start, end)
        for ring in rings
        for start, end in itertools.pairwise(ring)
    )


def communities_nearest_fire(
    fire_subject: LayerResult,
    fire_name: str,
    *,
    limit: int = 5,
) -> LayerResult | None:
    """Rank nearby Census places when no place polygon intersects a fire.

    Ranking uses each Census place reference point and the official perimeter
    edge. It is a proximity fallback, not evidence of smoke, evacuation, or
    damage.
    """
    fire_geometries = [
        feature.get("geometry") or {} for feature in fire_subject.geojson.get("features") or []
    ]
    if not fire_geometries:
        return None
    ranked = sorted(
        (
            (
                min(_point_to_polygon_km(place.center, fire) for fire in fire_geometries),
                place,
            )
            for place in california_places()
        ),
        key=lambda item: item[0],
    )[: max(1, limit)]
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": list(place.center)},
            "properties": {
                "name": place.name,
                "legalName": place.legal_name,
                "geoid": place.geoid,
                "distanceKm": round(distance, 1),
                "relation": "nearest Census place reference point",
                "geometryRole": "nearest_place_reference_point",
            },
        }
        for distance, place in ranked
        if math.isfinite(distance)
    ]
    return LayerResult(
        capability_id="fire_nearest_place_reference_points",
        title=f"Places closest to {fire_name}",
        hazard_object="exposure",
        family=None,
        geometry_type="Point",
        caveat=(
            "Proximity is measured from each Census place reference point to the official "
            "perimeter edge; it does not establish fire impact, smoke exposure, or damage."
        ),
        feature_count=len(features),
        source="Derived from NIFC perimeter × U.S. Census TIGER/Line 2025 Places",
        visualization=LayerVisualization(
            kind="graduated",
            label="Approximate distance to official perimeter",
            field="distanceKm",
            unit="km",
            stops=[
                LegendStop(value=5, label="≤ 5 km", color="#d95f35"),
                LegendStop(value=15, label="≤ 15 km", color="#e9a23b"),
                LegendStop(value=30, label="≤ 30 km", color="#e8cf6a"),
            ],
            popup_fields=[
                PopupField(key="legalName", label="Place"),
                PopupField(key="distanceKm", label="Approx. distance", unit="km"),
                PopupField(key="relation", label="Spatial relationship"),
            ],
            explanation="Reference-point proximity ranking; not an impact assessment.",
        ),
        geojson={"type": "FeatureCollection", "features": features},
    )


def city_context_status(
    city_name: str,
    perimeter_layer: LayerResult | None,
    air_layer: LayerResult | None,
    weather_layer: LayerResult | None,
) -> dict[str, Any]:
    perimeter_count = perimeter_layer.feature_count if perimeter_layer else 0
    air_feature = (air_layer.geojson.get("features") or [None])[0] if air_layer else None
    air = (air_feature or {}).get("properties") or {}
    weather_feature = (
        (weather_layer.geojson.get("features") or [None])[0] if weather_layer else None
    )
    weather = (weather_feature or {}).get("properties") or {}
    if perimeter_count:
        assessment = (
            f"An agency-mapped fire perimeter currently overlaps {city_name}. "
            "The overlap is with the city boundary, which is not the same as "
            "any particular address being affected."
        )
        evidence = "elevated"
    else:
        # Three hedges in three sentences was the old wording, and the narrator
        # mirrored its shape. One statement, one caveat.
        assessment = (
            f"No agency-mapped fire perimeter currently overlaps {city_name}. "
            "That is the position as last published, not a forecast."
        )
        evidence = "low"
    details = [f"Current fire perimeters in scope: {perimeter_count}."]
    if air.get("usAqi") is not None:
        details.append(
            f"Modeled current U.S. AQI: {air['usAqi']}; PM2.5: {air.get('pm25')} {air.get('pm25Unit', '')}. "
            "Air quality alone is not attributed to wildfire."
        )
    if weather:
        details.append(
            "Current fire-weather context: "
            f"{weather.get('shortForecast') or 'forecast available'}, wind "
            f"{weather.get('windDirection') or '?'} {weather.get('windSpeed') or '?'}."
        )
    return {
        "status": "city_assessed",
        "workflow": "city",
        "evidence": evidence,
        "message": assessment,
        "details": details,
    }


def city_weather_status(city_name: str, weather_layer: LayerResult | None) -> dict[str, Any]:
    """Summarize a weather-only city request without introducing fire claims."""
    feature = (weather_layer.geojson.get("features") or [None])[0] if weather_layer else None
    weather = (feature or {}).get("properties") or {}
    if not weather:
        return {
            "status": "weather_assessed",
            "workflow": "city",
            "focus": "weather",
            "message": f"{city_name}: current weather could not be retrieved.",
            "details": ["No fire dataset was selected for this weather-only request."],
        }

    forecast = weather.get("shortForecast") or "Current forecast available"
    temperature = weather.get("temperature")
    unit = weather.get("temperatureUnit") or ""
    temperature_text = f", {temperature}°{unit}" if temperature is not None else ""
    wind_direction = weather.get("windDirection") or "unknown direction"
    wind_speed = weather.get("windSpeed") or "unknown speed"
    details = [f"Wind: from {wind_direction} at {wind_speed}."]
    if weather.get("relativeHumidity") is not None:
        details.append(f"Relative humidity: {weather['relativeHumidity']}%.")
    if weather.get("forecastTime"):
        details.append(f"Forecast time: {weather['forecastTime']}.")
    details.append("No fire dataset was selected for this weather-only request.")
    return {
        "status": "weather_assessed",
        "workflow": "city",
        "focus": "weather",
        "message": f"{city_name}: {forecast}{temperature_text}; wind from {wind_direction} at {wind_speed}.",
        "details": details,
    }
