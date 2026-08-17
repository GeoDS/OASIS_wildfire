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

from typing import Any

from ..contract import AnalysisContract
from .capabilities import CAPABILITIES, SHOWCASE_AREA, load_layer
from .models import ExecutionPlan, LayerResult

#: Cap on features returned per layer. Point layers can run to thousands and the
#: browser is the bottleneck, not the file.
MAX_FEATURES = 1500


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


def execute(plan: ExecutionPlan, contract: AnalysisContract) -> list[LayerResult]:
    bbox = _bbox_for(contract)
    results: list[LayerResult] = []

    for planned in plan.layers:
        cap = CAPABILITIES[planned.capability_id]
        try:
            raw = load_layer(cap.id)
        except FileNotFoundError as exc:
            results.append(
                LayerResult(
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
            )
            continue

        provenance = raw.get("provenance", {})
        matching = [
            f for f in raw.get("features", []) if f.get("geometry") and _intersects(f["geometry"], bbox)
        ]
        truncated = len(matching) > MAX_FEATURES

        results.append(
            LayerResult(
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
                geojson={
                    "type": "FeatureCollection",
                    "features": matching[:MAX_FEATURES],
                },
            )
        )

    return results


def summarise(results: list[LayerResult], plan: ExecutionPlan) -> str:
    """A short, honest prose summary for the chat panel."""
    lines: list[str] = []

    drawn = [r for r in results if r.feature_count]
    empty = [r for r in results if not r.feature_count]

    if drawn:
        parts = [f"{r.feature_count} {'feature' if r.feature_count == 1 else 'features'} of {r.title.lower()}" for r in drawn]
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
                f"{r.title} was capped at {MAX_FEATURES} features for display, so counts "
                f"shown are not totals."
            )
    for need in plan.unmet:
        lines.append(f"Not available: {need.reason}")
    lines.extend(plan.notes)

    return " ".join(lines) if lines else "The plan produced no layers."
