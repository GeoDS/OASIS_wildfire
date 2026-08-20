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
            caveat=(
                "Perimeters only, with no burn severity. The national layer lags by "
                "about a year, so the Eaton Fire itself is not in it."
            ),
        ),
    )
}


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


def available_hazard_objects() -> set[str]:
    return {c.hazard_object for c in CAPABILITIES.values()}
