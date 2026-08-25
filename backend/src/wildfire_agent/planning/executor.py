"""Execute a plan: load each snapshot, clip it to the contract's area, hand back
GeoJSON ready to draw.

Two disciplines carried over from the data-source guidance, both about not
lying to the user:

- An empty result is a legal answer. If nothing falls inside the area of
  interest the layer comes back with zero features and says so. It is never
  padded with anything invented.
- Truncation is reported. A capped feature list must never be mistaken for a
  complete count, so `truncated` travels with the layer and the UI shows it.
"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from math import isfinite
from typing import Any

from ..contract import AnalysisContract
from .capabilities import CAPABILITIES, SHOWCASE_AREA, load_layer
from .models import ExecutionPlan, LayerResult

#: Cap on features returned per layer. Point layers can run to thousands and the
#: browser is the bottleneck, not the file.
MAX_FEATURES = 1500

_RESULT_NOUNS: dict[str, tuple[str, str]] = {
    "burned_area": ("burned-area region", "burned-area regions"),
    "fire_perimeter": ("fire-boundary polygon", "fire-boundary polygons"),
    "satellite_hotspot": ("thermal-anomaly point", "thermal-anomaly points"),
    "population_exposure": ("affected community", "affected communities"),
}


def _result_noun(result: LayerResult, *, plural: bool) -> str:
    # ``active_fire`` is an analytical concept, not an object type. Its two
    # available sources represent very different things, so name the mapped
    # object instead of collapsing both into the vague word “detection”.
    if result.capability_id in {"official_fire_perimeters", "historical_fire_perimeters"}:
        return ("fire-boundary polygon", "fire-boundary polygons")[plural]
    if result.capability_id == "satellite_hotspots":
        return ("thermal-anomaly point", "thermal-anomaly points")[plural]
    named = _RESULT_NOUNS.get(result.hazard_object)
    if named:
        return named[1 if plural else 0]
    fallback = {
        "Point": ("mapped point", "mapped points"),
        "Polygon": ("mapped polygon", "mapped polygons"),
        "LineString": ("mapped line", "mapped lines"),
    }.get(result.geometry_type, ("mapped object", "mapped objects"))
    return fallback[1 if plural else 0]


def _bbox_for(contract: AnalysisContract) -> tuple[float, float, float, float]:
    """The clip box: the contract's resolved scope, else the whole showcase area."""
    spatial = contract.spatial()
    if spatial and spatial.resolved and spatial.resolved.bbox:
        return spatial.resolved.bbox
    return SHOWCASE_AREA["bbox"]  # type: ignore[return-value]


def _coords_iter(coords: Any):
    """Yield every [lon, lat] pair in an arbitrarily nested coordinate array."""
    if not coords:
        return
    if isinstance(coords[0], (int, float)):
        yield coords
        return
    for part in coords:
        yield from _coords_iter(part)


def _intersects(geometry: dict, bbox: tuple[float, float, float, float]) -> bool:
    """Cheap bbox-overlap test.

    Deliberately approximate: comparing the feature's own bounding box against
    the area of interest. A true polygon intersection would need a geometry
    library, and for deciding "is this fire anywhere near here" the envelope is
    the right level of effort.
    """
    w, s, e, n = bbox
    xs, ys = [], []
    for lon, lat, *_ in _coords_iter(geometry.get("coordinates")):
        xs.append(lon)
        ys.append(lat)
    if not xs:
        return False
    return not (max(xs) < w or min(xs) > e or max(ys) < s or min(ys) > n)


def validate_bbox(values: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """Validate the browser-to-backend GeoJSON bbox contract."""
    bbox = tuple(float(value) for value in values)
    if not all(isfinite(value) for value in bbox):
        raise ValueError("bbox coordinates must be finite numbers")
    west, south, east, north = bbox
    if west >= east or south >= north:
        raise ValueError("bbox must be ordered [west, south, east, north]")
    if not (-180 <= west <= 180 and -180 <= east <= 180):
        raise ValueError("bbox longitude must be between -180 and 180")
    if not (-90 <= south <= 90 and -90 <= north <= 90):
        raise ValueError("bbox latitude must be between -90 and 90")
    return bbox


def _clip_ring(
    coordinates: list[list[float]],
    bbox: tuple[float, float, float, float],
) -> list[list[float]]:
    """Sutherland-Hodgman clip of one polygon ring to an axis-aligned bbox."""
    if len(coordinates) < 3:
        return []
    points = [[float(point[0]), float(point[1])] for point in coordinates]
    if points[0] == points[-1]:
        points.pop()

    west, south, east, north = bbox
    edges: list[
        tuple[Callable[[list[float]], bool], Callable[[list[float], list[float]], list[float]]]
    ] = [
        (
            lambda point: point[0] >= west,
            lambda start, end: [
                west,
                start[1] + (end[1] - start[1]) * (west - start[0]) / (end[0] - start[0]),
            ],
        ),
        (
            lambda point: point[0] <= east,
            lambda start, end: [
                east,
                start[1] + (end[1] - start[1]) * (east - start[0]) / (end[0] - start[0]),
            ],
        ),
        (
            lambda point: point[1] >= south,
            lambda start, end: [
                start[0] + (end[0] - start[0]) * (south - start[1]) / (end[1] - start[1]),
                south,
            ],
        ),
        (
            lambda point: point[1] <= north,
            lambda start, end: [
                start[0] + (end[0] - start[0]) * (north - start[1]) / (end[1] - start[1]),
                north,
            ],
        ),
    ]

    output = points
    for inside, intersection in edges:
        incoming = output
        output = []
        if not incoming:
            break
        start = incoming[-1]
        for end in incoming:
            start_inside, end_inside = inside(start), inside(end)
            if end_inside:
                if not start_inside:
                    output.append(intersection(start, end))
                output.append(end)
            elif start_inside:
                output.append(intersection(start, end))
            start = end

    if len(output) < 3:
        return []
    output.append(output[0])
    return output


def _clip_geometry(
    geometry: dict,
    bbox: tuple[float, float, float, float],
) -> dict | None:
    """Clip the geometry types used by local demo datasets to ``bbox``."""
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    west, south, east, north = bbox

    if geometry_type == "Point" and coordinates:
        lon, lat = coordinates[:2]
        return deepcopy(geometry) if west <= lon <= east and south <= lat <= north else None

    if geometry_type == "MultiPoint" and coordinates:
        clipped = [
            point
            for point in coordinates
            if west <= point[0] <= east and south <= point[1] <= north
        ]
        return {**geometry, "coordinates": clipped} if clipped else None

    if geometry_type == "Polygon" and coordinates:
        rings = [_clip_ring(ring, bbox) for ring in coordinates]
        rings = [ring for ring in rings if ring]
        return {**geometry, "coordinates": rings} if rings else None

    if geometry_type == "MultiPolygon" and coordinates:
        polygons = []
        for polygon in coordinates:
            rings = [_clip_ring(ring, bbox) for ring in polygon]
            rings = [ring for ring in rings if ring]
            if rings:
                polygons.append(rings)
        return {**geometry, "coordinates": polygons} if polygons else None

    # No current local capability uses lines. Keep the previous envelope filter
    # as an explicit fallback so a future source is not silently discarded.
    return deepcopy(geometry) if _intersects(geometry, bbox) else None


def clip_local_layer(
    capability_id: str,
    bbox: tuple[float, float, float, float],
) -> LayerResult:
    """Load one allow-listed local capability and clip its geometry to the request bbox."""
    bbox = validate_bbox(bbox)
    cap = CAPABILITIES[capability_id]
    try:
        raw = load_layer(cap.id)
    except FileNotFoundError as exc:
        return LayerResult(
            capability_id=cap.id,
            title=cap.title,
            hazard_object=cap.hazard_object,
            family=cap.family,
            geometry_type=cap.geometry_type,
            caveat=f"Layer unavailable: {exc}",
            feature_count=0,
            source="unavailable",
            geojson={"type": "FeatureCollection", "features": []},
        )

    matching = []
    for feature in raw.get("features", []):
        geometry = feature.get("geometry")
        if not geometry:
            continue
        clipped = _clip_geometry(geometry, bbox)
        if clipped:
            matching.append({**feature, "geometry": clipped})

    provenance = raw.get("provenance", {})
    truncated = len(matching) > MAX_FEATURES
    return LayerResult(
        capability_id=cap.id,
        title=cap.title,
        hazard_object=cap.hazard_object,
        family=cap.family,
        geometry_type=cap.geometry_type,
        caveat=cap.caveat,
        feature_count=len(matching),
        truncated=truncated,
        source=provenance.get("source", "unknown"),
        as_of=provenance.get("as_of"),
        retrieved_at=provenance.get("retrieved_at"),
        geojson={"type": "FeatureCollection", "features": matching[:MAX_FEATURES]},
    )


def execute(plan: ExecutionPlan, contract: AnalysisContract) -> list[LayerResult]:
    bbox = _bbox_for(contract)
    results: list[LayerResult] = []

    for planned in plan.layers:
        results.append(clip_local_layer(planned.capability_id, bbox))

    return results


def summarise(results: list[LayerResult], plan: ExecutionPlan) -> str:
    """A short, honest prose summary for the chat panel."""
    lines: list[str] = []

    drawn = [r for r in results if r.feature_count]
    empty = [r for r in results if not r.feature_count]

    if drawn:
        parts = [
            f"{r.feature_count} {_result_noun(r, plural=r.feature_count != 1)}"
            for r in drawn
        ]
        lines.append("Drawn on the map: " + "; ".join(parts) + ".")
    if empty:
        # Empty is a result, not a failure - say it plainly rather than hiding it.
        lines.append(
            "No data in this area for: "
            + ", ".join(r.title.lower() for r in empty)
            + ". That is a real result, not a loading error."
        )
    for r in results:
        if r.truncated:
            lines.append(
                f"{r.title} was capped at {MAX_FEATURES} {_result_noun(r, plural=True)} "
                "for display, so counts "
                f"shown are not totals."
            )
    for need in plan.unmet:
        lines.append(f"Not available: {need.reason}")
    lines.extend(plan.notes)

    return " ".join(lines) if lines else "The plan produced no layers."
