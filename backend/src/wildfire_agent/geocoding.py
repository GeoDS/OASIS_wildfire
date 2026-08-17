"""Spatial grounding - the only external call the User Goal Agent makes.

Boundary reminder: this resolves place names to coordinates and nothing else.
It never fetches wildfire analysis data; that belongs to the Planning Agent,
downstream of the contract.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

import httpx

from .config import settings
from .contract import ResolvedLocation

#: Offline fallback gazetteer. Used only when Nominatim is unreachable, and the
#: `geocoder` field then says `fallback_gazetteer` - **provenance must stay
#: honest**. Same discipline as the ban on filling analysis results with mock data.
FALLBACK_GAZETTEER: dict[str, tuple[float, float, str]] = {
    "altadena": (-118.1312, 34.1897, "Altadena, Los Angeles County, California, USA"),
    "eaton": (-118.0692, 34.2035, "Eaton Canyon, Los Angeles County, California, USA"),
    "pasadena": (-118.1445, 34.1478, "Pasadena, Los Angeles County, California, USA"),
    "los angeles county": (-118.2437, 34.0522, "Los Angeles County, California, USA"),
}

#: Buffer used when "near X" comes without a radius. Recorded in `assumptions`
#: and drawn on the map for confirmation.
DEFAULT_BUFFER_KM = 25.0

#: Beyond this distance two candidates cannot be spellings of the same place.
_AMBIGUITY_DISTANCE_KM = 50.0
#: If the runner-up is at least this fraction as prominent, neither is a safe pick.
_AMBIGUITY_IMPORTANCE_RATIO = 0.85

#: Directional and prepositional modifiers. **Must be stripped before querying
#: the geocoder.** Sending "near Madison" verbatim to Nominatim matches a street
#: called "Near Street" in Madison County, Ohio, and the whole map lands in the
#: wrong state.
_MODIFIER_RE = re.compile(
    r"^\s*(?:near(?:by)?|around|close\s+to|next\s+to|within|in|at|of|from|the)\s+",
    re.IGNORECASE,
)

#: Radius wording. After clarification the location slot reads
#: "10 km buffer around Madison, WI" - sending that whole string to a geocoder
#: finds nothing. The radius is parsed separately by `_extract_buffer_km`;
#: here we only remove it from the place name.
_BUFFER_PHRASE_RE = re.compile(
    r"[+＋]?\s*\d+(?:\.\d+)?\s*(?:kilomet(?:er|re)s?|km|miles?|mi\b)"
    r"\s*(?:buffer|radius)?",
    re.IGNORECASE,
)


class GeocodingError(RuntimeError):
    pass


@dataclass
class GeocodeCandidate:
    display_name: str
    lon: float
    lat: float
    source: str
    #: Nominatim's prominence score (0-1). The fallback table has no such
    #: concept, so it reports 1.0.
    importance: float = 1.0


def normalize_query(raw: str) -> str:
    """Reduce a spatial phrase to a **bare place name** for the geocoder.

        'near Madison'                    -> 'Madison'
        '10 km buffer around Madison, WI' -> 'Madison, WI'

    If nothing can be stripped the input is returned unchanged, so the geocoder
    fails honestly instead of receiving an empty query.
    """
    text = _BUFFER_PHRASE_RE.sub(" ", raw).strip(" ,，、+＋")
    if not text:
        text = raw.strip()

    while True:
        stripped = _MODIFIER_RE.sub("", text).strip(" ,，、")
        if stripped == text or not stripped:
            return text
        text = stripped


def bbox_from_center(lon: float, lat: float, buffer_km: float) -> tuple[float, float, float, float]:
    """Bounding box of a circular buffer. [west, south, east, north]

    One degree of latitude is about 111.32 km; longitude shrinks by cos(lat).
    This equirectangular approximation is fine at demo scale (<= 100 km); a real
    projection is needed for anything larger. The frontend's
    `MapView.circlePolygon` uses the same maths - change one, change the other.
    """
    d_lat = buffer_km / 111.32
    d_lon = buffer_km / (111.32 * max(math.cos(math.radians(lat)), 1e-6))
    return (lon - d_lon, lat - d_lat, lon + d_lon, lat + d_lat)


def haversine_km(a: GeocodeCandidate, b: GeocodeCandidate) -> float:
    r = 6371.0088
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp = p2 - p1
    dl = math.radians(b.lon - a.lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def is_ambiguous(candidates: list[GeocodeCandidate]) -> bool:
    """Are the top two candidates both far apart *and* comparably prominent?

    Both conditions are required:
    - far apart: not two spellings of one place, so a wrong pick puts the whole
      map in another state;
    - comparably prominent: for `Madison` the top hit (the Wisconsin state
      capital) far outranks every namesake, and taking it is safe - that case
      must not bother the user.
    """
    if len(candidates) < 2:
        return False
    best, second = candidates[0], candidates[1]
    if haversine_km(best, second) < _AMBIGUITY_DISTANCE_KM:
        return False
    if best.importance <= 0:
        return True
    return second.importance / best.importance >= _AMBIGUITY_IMPORTANCE_RATIO


#: Query cache. Nominatim's usage policy is **1 request/second**, and a single
#: session resolves the same place several times (once at compile, once after
#: each clarification round). Without caching we get rate-limited and silently
#: drop to the fallback gazetteer - coordinates still roughly right, precision
#: and provenance both downgraded.
_CACHE: dict[str, list[GeocodeCandidate]] = {}
_CACHE_MAX = 256


async def geocode(query: str, *, limit: int = 5) -> list[GeocodeCandidate]:
    """Query Nominatim, falling back to the offline table. Never invents coordinates."""
    normalized = normalize_query(query)
    cached = _CACHE.get(normalized)
    if cached is not None:
        return cached

    params = {
        "q": normalized,
        "format": "jsonv2",
        "limit": str(limit),
        "addressdetails": "0",
    }
    headers = {"User-Agent": settings.geocoder_user_agent}

    try:
        async with httpx.AsyncClient(timeout=settings.geocoder_timeout_s) as client:
            resp = await client.get(settings.nominatim_url, params=params, headers=headers)
            resp.raise_for_status()
            payload = resp.json()
    except (httpx.HTTPError, ValueError):
        return _fallback(query)

    candidates = [
        GeocodeCandidate(
            display_name=item.get("display_name", query),
            lon=float(item["lon"]),
            lat=float(item["lat"]),
            source="nominatim",
            importance=float(item.get("importance") or 0.0),
        )
        for item in payload
        if "lon" in item and "lat" in item
    ]
    candidates.sort(key=lambda c: c.importance, reverse=True)
    result = candidates or _fallback(query)

    # Cache successes only. Caching a rate-limit or timeout would pin the whole
    # demo to the fallback gazetteer after a single blip.
    if candidates:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.clear()
        _CACHE[normalized] = result
    return result


def _fallback(query: str) -> list[GeocodeCandidate]:
    key = normalize_query(query).lower()
    for name, (lon, lat, display) in FALLBACK_GAZETTEER.items():
        if name in key:
            return [GeocodeCandidate(display, lon, lat, "fallback_gazetteer")]
    return []


async def resolve_location(
    raw: str,
    *,
    buffer_km: float | None = None,
) -> ResolvedLocation | None:
    """Resolve the user's spatial phrase into a `ResolvedLocation`.

    Returning None (for "near my house", say) is not an error - it is the signal
    that Ambiguity Resolution should step in.
    """
    candidates = await geocode(raw)
    if not candidates:
        return None

    best = candidates[0]
    buffer_km = DEFAULT_BUFFER_KM if buffer_km is None else buffer_km
    return ResolvedLocation(
        display_name=best.display_name,
        center=(best.lon, best.lat),
        buffer_km=buffer_km,
        bbox=bbox_from_center(best.lon, best.lat, buffer_km),
        geocoder=best.source,
        confirmed_by_user=False,
        alternatives=[c.display_name for c in candidates[1:4]],
        ambiguous=is_ambiguous(candidates),
    )
