"""Filling a gap on a layer that already exists.

The place polygons and their GEOIDs are local; what they lack is who lives
inside them. This module is the join: it takes a layer the pipeline already
produced, asks `census_acs` for the attributes the taxonomy says are missing,
and attaches them.

Keeping the join here rather than in `census_acs` means that module never learns
what a `LayerResult` is, and keeping it out of `context_layers` means building a
layer never reaches the network. One side knows the API, the other knows the
geometry, and this is the only place that knows both.

One place failing does not fail the layer. A city whose estimate is suppressed
is reported as such and the rest still carry their figures, because "we could
not reach the Census API for Duarte" and "nobody lives in Duarte" must never
arrive as the same answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .census_acs import (
    SUPPLIES,
    VARIABLES,
    CensusUnavailable,
    fetch_place_attributes,
    provenance,
)
from .planning.models import LayerResult

#: Readable property names, so a popup does not have to show `B19013_001E`.
_PROPERTY_NAMES: dict[str, str] = {
    "B01003_001E": "population",
    "B25001_001E": "housingUnits",
    "B01002_001E": "medianAge",
    "B09021_022E": "seniorsLivingAlone",
    "B19013_001E": "medianHouseholdIncome",
}

#: Two ACS columns answer one question and are reported as their sum.
_NO_VEHICLE_CODES = ("B25044_003E", "B25044_010E")

#: Readable property name -> the `required_variables` entry it answers. Derived
#: from the same two tables the attach step uses, so a variable cannot be written
#: onto a feature under a name this cannot read back.
FETCHED_PROPERTIES: tuple[str, ...]
_PROPERTY_SUPPLIES: dict[str, str] = {
    _PROPERTY_NAMES[v.code]: v.supplies for v in VARIABLES if v.code in _PROPERTY_NAMES
} | {"householdsWithoutVehicle": "no-vehicle households"}

#: Property names this fill and only this fill writes. A figure here is evidence
#: that an approved fetch happened; one computed locally, like the area share, is
#: not, and must never let a reply that fetched nothing look like one that did.
FETCHED_PROPERTIES = tuple(_PROPERTY_SUPPLIES)


@dataclass
class EnrichmentResult:
    """An enriched layer plus an account of what was fetched and what was not."""

    layer: LayerResult
    #: Required-variable names this fill actually closed.
    closed: tuple[str, ...] = ()
    #: Places whose attributes could not be fetched at all, with the reason.
    failed: dict[str, str] = field(default_factory=dict)
    #: Places fetched, but with one or more estimates suppressed by the Bureau.
    suppressed: dict[str, tuple[str, ...]] = field(default_factory=dict)
    source: dict[str, Any] = field(default_factory=dict)

    @property
    def enriched_count(self) -> int:
        return sum(
            1
            for feature in self.layer.geojson.get("features") or []
            if "population" in (feature.get("properties") or {})
        )


def enrich_places_with_acs(layer: LayerResult) -> EnrichmentResult:
    """Attach ACS attributes to every feature of a place layer, by GEOID."""
    features = layer.geojson.get("features") or []
    failed: dict[str, str] = {}
    suppressed: dict[str, tuple[str, ...]] = {}
    enriched: list[dict[str, Any]] = []
    any_success = False

    for feature in features:
        properties = dict(feature.get("properties") or {})
        geoid = str(properties.get("geoid") or "")
        name = str(properties.get("name") or geoid or "unknown place")
        if not geoid:
            failed[name] = "no GEOID on the feature, so nothing could be looked up"
            enriched.append({**feature, "properties": properties})
            continue

        try:
            attributes = fetch_place_attributes(geoid)
        except CensusUnavailable as exc:
            # Recorded against the place, not swallowed: a missing figure and an
            # unreachable API look identical on a map and must not read alike.
            failed[name] = str(exc)
            enriched.append({**feature, "properties": properties})
            continue

        any_success = True
        for code, readable in _PROPERTY_NAMES.items():
            if code in attributes.values:
                properties[readable] = attributes.values[code]
        no_vehicle = [attributes.values[c] for c in _NO_VEHICLE_CODES if c in attributes.values]
        if len(no_vehicle) == len(_NO_VEHICLE_CODES):
            properties["householdsWithoutVehicle"] = sum(no_vehicle)
        if attributes.suppressed:
            suppressed[name] = attributes.suppressed
        enriched.append({**feature, "properties": properties})

    caveats = [
        layer.caveat,
        # The figure most likely to be misused, so the warning travels with it.
        (
            "Population and income are for the whole place. A fire reaching part of a "
            "place does not affect that share of its residents, and these figures must "
            "not be multiplied by a burned-area share to imply that it does."
        ),
    ]
    if failed:
        caveats.append(
            "Attributes could not be fetched for: "
            + "; ".join(f"{name} ({reason})" for name, reason in failed.items())
        )
    if suppressed:
        caveats.append(
            "The Census Bureau suppressed some estimates: "
            + "; ".join(f"{name}: {', '.join(items)}" for name, items in suppressed.items())
        )

    return EnrichmentResult(
        layer=layer.model_copy(
            update={
                "geojson": {"type": "FeatureCollection", "features": enriched},
                "caveat": " ".join(caveats),
            }
        ),
        closed=SUPPLIES if any_success else (),
        failed=failed,
        suppressed=suppressed,
        source=provenance() if any_success else {},
    )


def variables_present(layer: LayerResult | None) -> tuple[str, ...]:
    """Which required-variable names this layer's own features already carry.

    The taxonomy declares what a *kind* of layer lacks. It cannot see that this
    particular layer was enriched, so a gap derived from it alone never closes
    and the same fetch is offered again after it has already succeeded. Reading
    the features is what makes the gap answer for the data actually in hand.

    A variable counts as present only when every feature carries it: one city
    with a population and two without is still a layer that cannot answer "who
    lives there", and offering to complete it is the right question to ask.
    """
    if not layer:
        return ()
    features = layer.geojson.get("features") or []
    if not features:
        return ()
    by_variable: dict[str, list[str]] = {}
    for prop, supplies in _PROPERTY_SUPPLIES.items():
        by_variable.setdefault(supplies, []).append(prop)
    return tuple(
        variable
        for variable, props in by_variable.items()
        if all(prop in (f.get("properties") or {}) for prop in props for f in features)
    )


def offer_for(
    hazard_object: str,
    missing: tuple[str, ...],
    *,
    already: tuple[str, ...] = (),
) -> dict[str, Any] | None:
    """What this source proposes to fetch, phrased for a person to approve.

    Returns None when the fill would close nothing, so the user is never asked
    to authorise a fetch that cannot help. The offer names what will remain
    missing afterwards too: approving this is not approving "the gap is closed".

    `already` is what the layer in hand carries, from `variables_present`. A
    variable there is not offered again: the answer would not change, and the
    question would read as though the previous fetch had not happened.
    """
    closes = tuple(v for v in missing if v in SUPPLIES and v not in already)
    if not closes:
        return None
    remaining = tuple(v for v in missing if v not in SUPPLIES)
    record = provenance()
    return {
        "source_id": "census_acs",
        "hazard_object": hazard_object,
        "source": record["source"],
        "dataset": record["dataset"],
        "geography": record["geography"],
        "closes": closes,
        "remaining": remaining,
        "variables": describe_variables(),
        "quality_notes": record["quality_notes"],
    }


def describe_variables() -> list[dict[str, str]]:
    """What this fill offers, for a confirmation prompt shown before fetching."""
    return [
        {"code": v.code, "label": v.label, "supplies": v.supplies, "unit": v.unit}
        for v in VARIABLES
    ]
