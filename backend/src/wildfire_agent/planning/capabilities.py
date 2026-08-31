"""The capability registry - what this system can actually deliver today.

This is the missing piece of the framework's `slots -> capabilities -> workflow`
chain, and it deliberately lives **downstream of Task 1**. Capabilities describe
what the *system* has; the Analysis Contract describes what the *user* asked.
The first changes whenever a data source is added or removed, so keeping it out
of the contract is what stops our interface with the Planning Agent wobbling
every time the data layer moves.

Each entry declares which hazard object it serves and, where it matters, which
non-interchangeable data family it belongs to. That family tag is what finally
cashes in the question Task 1 insisted on asking.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

DATA_ROOT = Path(__file__).resolve().parents[3] / "data"

#: Whether a layer describes the present or the past. The showcase snapshot is
#: pinned to the Eaton Fire of January 2025, so "current" here means "current as
#: of the snapshot" - a distinction the planner states out loud rather than
#: quietly papering over.
Temporality = Literal["snapshot_current", "historical"]


@dataclass(frozen=True)
class Capability:
    id: str
    title: str
    #: Which hazard object from `taxonomy.HAZARD_OBJECTS` this serves.
    hazard_object: str
    #: Which `DataFamilyChoice.id` this belongs to, when the hazard object has
    #: families that are not interchangeable. None when the object has only one.
    family: str | None
    temporality: Temporality
    geometry_type: Literal["Point", "Polygon", "LineString"]
    #: Path relative to `DATA_ROOT`.
    path: str
    #: Which of the hazard object's `required_variables` this layer actually
    #: carries. Declared from the fields in the file, not from the layer's name:
    #: a perimeter layer without a date does not supply a detection time, and
    #: saying otherwise turns a real gap into a silent one.
    supplies: tuple[str, ...]
    #: One line the renderer must surface with the layer. Not optional: a layer
    #: shown without its caveat is how a satellite heat pixel becomes "a fire".
    caveat: str

    @property
    def file(self) -> Path:
        return DATA_ROOT / self.path


#: Showcase area: Altadena, California - an unincorporated LA County community
#: of ~42,000 across ~22 km2, most of which burned in the Eaton Fire of
#: January 2025. Small enough to read on one screen, unlike "Los Angeles".
SHOWCASE_AREA = {
    "id": "altadena",
    "name": "Altadena, California",
    "center": (-118.1312, 34.1897),
    "bbox": (-118.28, 34.12, -117.96, 34.32),
    "context": (
        "Eaton Fire, January 2025. The dataset is a snapshot of that event, not a live feed."
    ),
}

CAPABILITIES: dict[str, Capability] = {
    c.id: c
    for c in (
        Capability(
            id="official_fire_perimeters",
            title="Officially confirmed fire perimeters",
            hazard_object="active_fire",
            family="official_fire_perimeters",
            temporality="snapshot_current",
            geometry_type="Polygon",
            path="altadena/official_fire_perimeters.geojson",
            # Fields are incident_name, status, type. The as-of date lives in the
            # caveat, not in the data, so no detection time is carried.
            supplies=("fire perimeter",),
            caveat=(
                "Agency-verified perimeter for the Eaton Fire as of 21 January 2025. "
                "Authoritative, but published with a lag - it is not live conditions."
            ),
        ),
        Capability(
            id="satellite_hotspots",
            title="Satellite thermal detections",
            hazard_object="active_fire",
            family="satellite_hotspots",
            temporality="snapshot_current",
            geometry_type="Point",
            path="altadena/satellite_hotspots.geojson",
            # acq_date gives the detection time. frp_mw is radiative power -
            # intensity, not confidence - so no confidence is claimed.
            supplies=("hotspot location", "detection time"),
            caveat=(
                "Each point is a thermal anomaly, not a confirmed wildfire. GOES pixels "
                "are coarse, and agricultural or industrial heat produces the same "
                "signature. Timely, but unverified."
            ),
        ),
        Capability(
            id="historical_fire_perimeters",
            title="Historical fire perimeters (2000 onwards)",
            hazard_object="active_fire",
            # Same family as the current perimeters - agency-verified polygons -
            # separated from them by `temporality`, not by family. Tagging this
            # None meant "official perimeters, since 2000" resolved to the
            # current snapshot instead of the archive.
            family="official_fire_perimeters",
            temporality="historical",
            geometry_type="Polygon",
            path="altadena/historical_fire_perimeters.geojson",
            # acres gives the size. fire_year is a year of record, not a moment
            # of detection, so it is not offered as a detection time.
            supplies=("fire perimeter", "fire size"),
            caveat=(
                "Perimeters only, with no burn severity. The national layer lags by "
                "about a year, so the Eaton Fire itself is not in it."
            ),
        ),
    )
}


@dataclass(frozen=True)
class RendererCoverage:
    """What the *renderer* path can serve for one hazard object.

    Deliberately not a `Capability`. A capability names one file that
    `load_layer` reads and `execute` clips; these have no single file and no
    single layer - a per-event archive of thirty bands, a live service behind an
    approval gate, a catalogue searched at request time. Forcing them into the
    `Capability` schema would mean inventing paths that cannot be loaded.

    This exists for one reason: without it `capabilities_for` answers "nothing"
    for six hazard objects the system demonstrably serves, and any honest gap
    report built on that answer would deny a capability in the same reply that
    just exercised it. See `docs/03-planning-agent.md` section 5.

    `supplies` follows the same rule as `Capability.supplies` and is meant to be
    read strictly: declare a variable only when the answer actually carries it.
    Over-declaring here turns a real gap into a silent one, which is worse than
    the gap.
    """

    hazard_object: str
    #: Which of the hazard object's `required_variables` the renderer path
    #: actually produces.
    supplies: tuple[str, ...]
    #: Human-readable, for the note attached to a partial gap.
    served_by: str
    #: True when the data only arrives after the user approves a fetch. Coverage
    #: is still coverage - "we can get this if you let us" is not "we cannot do
    #: this" - but the two must not be reported identically.
    needs_approval: bool = False


#: What the renderer path serves, by hazard object. Verified against
#: `docs/06-how-to-use.md` (end-to-end checked 2026-08-27) rather than inferred
#: from a module being present.
RENDERER_COVERAGE: dict[str, RendererCoverage] = {
    c.hazard_object: c
    for c in (
        RendererCoverage(
            hazard_object="active_fire",
            # WFIGS supplies the perimeter and agency acreage; FIRMS supplies
            # hotspot location, acquisition time and a confidence class; the
            # TS-SatFire labels supply cumulative burned area as a size.
            supplies=(
                "fire perimeter",
                "hotspot location",
                "detection time",
                "detection confidence",
                "fire size",
            ),
            served_by="WFIGS perimeters, NASA FIRMS detections, and TS-SatFire AF/BA labels",
        ),
        RendererCoverage(
            hazard_object="fire_spread",
            # Direction and rate come from differencing daily AF labels. A
            # front *position* is not published: the labels are pixels, and
            # calling their edge a fire front would be a claim about the fire
            # rather than about the data.
            supplies=("rate of spread", "spread direction", "time step"),
            served_by="daily TS-SatFire active-fire label differencing",
        ),
        RendererCoverage(
            hazard_object="fire_weather",
            # No fire danger index is computed anywhere in this deployment, and
            # NWS conditions are not one.
            supplies=("wind speed", "wind direction", "temperature", "relative humidity"),
            served_by="the event's local fire-weather record, and NWS for current conditions",
        ),
        RendererCoverage(
            hazard_object="fuel",
            # Land cover gives vegetation type. It does not give a fuel model,
            # a load, a moisture, or canopy structure, and NDVI is a greenness
            # index rather than any of them.
            supplies=("vegetation type",),
            served_by="ESRI_LULC land cover with terrain, and VIIRS NDVI",
        ),
        RendererCoverage(
            hazard_object="exposure",
            # Whole-place ACS only. Building footprints and the WUI boundary
            # are unavailable from any source this deployment can reach, which
            # is exactly what the fetch record already tells the user.
            supplies=("population count", "housing density"),
            served_by="U.S. Census ACS 5-year estimates, by Census place",
            needs_approval=True,
        ),
        RendererCoverage(
            hazard_object="vulnerability",
            # Language isolation and mobility limitation are not among the
            # attributes attached.
            supplies=("age structure", "income", "no-vehicle households"),
            served_by="U.S. Census ACS 5-year estimates, by Census place",
            needs_approval=True,
        ),
        RendererCoverage(
            hazard_object="post_fire_debris_flow",
            # Los Angeles County only, and no rainfall threshold - so the
            # system will not say what storm triggers concern.
            supplies=("burn scar extent", "potential hazard area", "watershed subarea"),
            served_by="LA County Public Works post-fire debris-flow hazard areas",
            needs_approval=True,
        ),
        RendererCoverage(
            hazard_object="smoke_plume",
            # Ground-level concentration only. No plume extent and no density
            # class, so this never answers "where is the smoke".
            supplies=("PM2.5", "observation time"),
            served_by="Open-Meteo air quality, current conditions only",
        ),
    )
}


def renderer_coverage_for(hazard_object: str) -> RendererCoverage | None:
    return RENDERER_COVERAGE.get(hazard_object)


def is_served(hazard_object: str) -> bool:
    """Whether anything in this deployment can serve this hazard object at all.

    The question behind a wholesale `UnmetNeed`. Distinct from "is it in the
    registry": the registry drives layer dispatch on one path, and answering
    this from it alone understates the deployment by six hazard objects.
    """
    return bool(capabilities_for(hazard_object)) or hazard_object in RENDERER_COVERAGE


def unserved_variables(hazard_object: str) -> tuple[str, ...]:
    """Required variables no path in this deployment supplies, in declared order.

    Note this is a different question from `missing_variables`, and the two must
    not be merged. `missing_variables` asks what is absent from an answer *now*,
    which is what decides whether a fetch is worth offering. This asks what the
    deployment cannot produce *at all*, which is what an honest capability gap
    reports. Census attributes are missing by the first measure until the user
    approves, and supplied by the second measure the whole time.
    """
    # Imported here for the same reason `missing_variables` does: taxonomy
    # imports nothing from planning, and this keeps it that way.
    from ..taxonomy import HAZARD_OBJECTS

    hazard = HAZARD_OBJECTS.get(hazard_object)
    if hazard is None:
        return ()
    supplied: set[str] = set()
    for cap in capabilities_for(hazard_object):
        supplied.update(cap.supplies)
    coverage = RENDERER_COVERAGE.get(hazard_object)
    if coverage:
        supplied.update(coverage.supplies)
    return tuple(v for v in hazard.required_variables if v not in supplied)


@lru_cache(maxsize=8)
def load_layer(capability_id: str) -> dict:
    """Read a snapshot from disk. Cached - these files never change at runtime."""
    cap = CAPABILITIES[capability_id]
    if not cap.file.exists():
        raise FileNotFoundError(
            f"Showcase data missing: {cap.file}. "
            f"Run `uv run python scripts/fetch_showcase_data.py` to snapshot it."
        )
    return json.loads(cap.file.read_text())


def capabilities_for(hazard_object: str) -> list[Capability]:
    return [c for c in CAPABILITIES.values() if c.hazard_object == hazard_object]


def missing_variables(
    hazard_object: str,
    capability_ids: Iterable[str] | None = None,
) -> tuple[str, ...]:
    """Required variables for a hazard object that nothing supplies.

    `capability_ids` scopes the question to one plan's chosen layers; omitting it
    asks what this deployment could supply at best. The distinction matters: a
    variable the deployment has but this plan did not select is a planning gap,
    while one nothing has at all is a data gap that only an outside source can
    close.
    """
    from ..taxonomy import HAZARD_OBJECTS

    hazard = HAZARD_OBJECTS.get(hazard_object)
    if hazard is None:
        return ()
    if capability_ids is None:
        pool = capabilities_for(hazard_object)
    else:
        wanted = set(capability_ids)
        pool = [c for c in capabilities_for(hazard_object) if c.id in wanted]
    supplied = {variable for capability in pool for variable in capability.supplies}
    return tuple(v for v in hazard.required_variables if v not in supplied)


def available_hazard_objects() -> set[str]:
    return {c.hazard_object for c in CAPABILITIES.values()}
