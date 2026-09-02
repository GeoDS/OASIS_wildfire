"""One explicit geographic boundary for the public API demo."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BoundingBox:
    west: float
    south: float
    east: float
    north: float

    def contains(self, longitude: float, latitude: float) -> bool:
        return self.west <= longitude <= self.east and self.south <= latitude <= self.north

    def as_list(self) -> list[float]:
        return [self.west, self.south, self.east, self.north]

    def arcgis_envelope(self) -> str:
        return f"{self.west},{self.south},{self.east},{self.north}"


# Demo coverage: coast from San Diego through Santa Barbara, east to the
# California-Arizona border, and north far enough to cover the supplied events.
SOCAL_BBOX = BoundingBox(west=-121.0, south=32.5, east=-114.0, north=35.8)

# The one other fixed scope: the contiguous United States, for "is anything
# burning right now" - a question about the country rather than about a place.
# A second constant, not a caller-supplied geometry: the safety property here is
# that no request is ever built from an argument, and widening the demo must not
# be the thing that quietly gives that up.
CONUS_BBOX = BoundingBox(west=-125.0, south=24.0, east=-66.5, north=49.5)

# Default weather point: Altadena / Eaton Fire showcase area. [lon, lat]
ALTADENA_CENTER = (-118.1312, 34.1897)


def scope_metadata() -> dict:
    return {
        "id": "southern_california_demo",
        "label": "Southern California demo scope",
        "bbox": SOCAL_BBOX.as_list(),
        "bbox_order": "[west, south, east, north] in WGS84",
        "default_weather_point": list(ALTADENA_CENTER),
        "notice": (
            "This is a rectangular demo envelope, not an administrative or scientific "
            "definition of Southern California. Requests outside it are rejected."
        ),
    }
