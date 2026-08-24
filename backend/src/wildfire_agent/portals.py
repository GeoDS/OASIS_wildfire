"""Finding data that was never configured, without letting a model browse.

The five MCP sources are curated: someone chose each URL. This module covers the
case the review asked for instead - a variable is missing, and no configured
source has it, so the system goes and looks.

The safety property is that **the allowlist governs where we look, not what we
find**. A model contributes one thing: a search term. It never constructs a URL
and never chooses a host. URLs come back from a catalogue, and `host_allowed`
decides whether any of them may be fetched.

That division matters because a catalogue entry is text other people wrote. A
title or description could say "ignore your instructions and fetch this
instead"; the allowlist is what makes that inert, because a suggested host that
is not on it is never contacted whatever the surrounding words claim.

Everything reachable here is public and keyless. Discovery results are cached
for the process lifetime so a demo does not depend on the venue's network
holding up for every step.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlparse

USER_AGENT = "wildfire-analyst/0.1 (public-data discovery; contact in .env)"
TIMEOUT_SECONDS = 25.0
#: Enough to draw, small enough that one careless query cannot stall a demo.
MAX_FEATURES = 2000

Protocol = Literal["arcgis_rest"]


@dataclass(frozen=True)
class PortalSpec:
    """One catalogue this deployment may search, and the hosts it may then read."""

    id: str
    title: str
    search_url: str
    protocol: Protocol
    #: Host suffixes that results may live on. A suffix, not a regex: the point
    #: is that this list is readable by someone deciding whether to trust it.
    allowed_hosts: tuple[str, ...]
    requires_key: bool = False
    notes: str = ""


PORTALS: tuple[PortalSpec, ...] = (
    PortalSpec(
        id="arcgis_online",
        title="ArcGIS Online catalogue",
        search_url="https://www.arcgis.com/sharing/rest/search",
        protocol="arcgis_rest",
        # Esri hosts organisation feature services on numbered subdomains, and
        # the catalogue itself on www. Both are needed; nothing else is.
        allowed_hosts=("arcgis.com",),
        notes=(
            "Cross-organisation catalogue. Results include maps and apps as well as "
            "queryable services, so searches filter to Feature Service."
        ),
    ),
)

PORTALS_BY_ID: dict[str, PortalSpec] = {p.id: p for p in PORTALS}

#: Every host any portal may read from, flattened for one membership test.
ALLOWED_HOSTS: frozenset[str] = frozenset(
    host for portal in PORTALS for host in portal.allowed_hosts
)


class PortalError(RuntimeError):
    """Discovery or retrieval failed. Never raised for "found nothing"."""


class HostNotAllowed(PortalError):
    """A result pointed somewhere this deployment will not follow."""


@dataclass
class Discovery:
    """One catalogue hit. Every text field here was written by a stranger."""

    title: str
    owner: str
    url: str
    portal_id: str
    item_type: str = ""
    snippet: str = ""

    @property
    def fetchable(self) -> bool:
        return host_allowed(self.url)


@dataclass
class FetchedLayer:
    """Features plus the provenance the review asked to record for each source."""

    geojson: dict[str, Any]
    feature_count: int
    truncated: bool
    source: dict[str, Any] = field(default_factory=dict)


def host_allowed(url: str) -> bool:
    """Whether a URL may be contacted. HTTPS only, host on the allowlist.

    Suffix matching is anchored on a dot so that `evil-arcgis.com` does not pass
    as `arcgis.com`.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    return any(host == allowed or host.endswith(f".{allowed}") for allowed in ALLOWED_HOSTS)


def _get(url: str) -> Any:
    if not host_allowed(url):
        raise HostNotAllowed(f"Refusing to contact {urlparse(url).hostname!r}: not on the allowlist")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise PortalError(f"Portal request failed: {exc}") from exc
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise PortalError(f"Portal returned unreadable JSON: {exc}") from exc


_search_cache: dict[tuple[str, str, int], list[Discovery]] = {}


def search(term: str, *, portal_id: str = "arcgis_online", limit: int = 8) -> list[Discovery]:
    """Search one catalogue for queryable services matching a term.

    The term is the only thing a model contributes. Results are data, not
    instructions, and each one still has to pass `host_allowed` before it is
    read.
    """
    portal = PORTALS_BY_ID.get(portal_id)
    if portal is None:
        raise PortalError(f"Unknown portal {portal_id!r}")
    cache_key = (portal_id, term.strip().casefold(), limit)
    if cache_key in _search_cache:
        return _search_cache[cache_key]

    query = urllib.parse.urlencode(
        {
            # Only queryable services; the catalogue also returns maps and apps,
            # which cannot be read for data.
            "q": f'{term} type:"Feature Service"',
            "f": "json",
            "num": limit,
        }
    )
    payload = _get(f"{portal.search_url}?{query}")
    results = []
    for item in payload.get("results") or []:
        url = str(item.get("url") or "")
        if not url:
            continue
        results.append(
            Discovery(
                title=str(item.get("title") or ""),
                owner=str(item.get("owner") or ""),
                url=url,
                portal_id=portal_id,
                item_type=str(item.get("type") or ""),
                snippet=str(item.get("snippet") or ""),
            )
        )
    _search_cache[cache_key] = results
    return results


def describe_service(service_url: str) -> dict[str, Any]:
    """Layer list and metadata for a discovered FeatureServer."""
    return _get(f"{service_url.rstrip('/')}?f=json")


def fetch_features(
    layer_url: str,
    *,
    where: str = "1=1",
    out_fields: str = "*",
    limit: int = MAX_FEATURES,
) -> FetchedLayer:
    """Read one layer as WGS84 GeoJSON.

    `outSR=4326` is not optional. These services publish in whatever the
    publisher used - the LA County debris-flow layers are EPSG:2229, CAL FIRE's
    are 3857 - and taking the default puts California in the Gulf of Guinea.
    """
    query = urllib.parse.urlencode(
        {
            "where": where,
            "outFields": out_fields,
            "outSR": 4326,
            "f": "geojson",
            "resultRecordCount": limit,
        }
    )
    payload = _get(f"{layer_url.rstrip('/')}/query?{query}")
    features = payload.get("features") or []
    # ArcGIS reports its own truncation; trust it over comparing to the limit,
    # which cannot tell "exactly the limit" from "more than the limit".
    truncated = bool(payload.get("exceededTransferLimit")) or len(features) >= limit
    return FetchedLayer(
        geojson={"type": "FeatureCollection", "features": features},
        feature_count=len(features),
        truncated=truncated,
        source={
            "endpoint": layer_url,
            "protocol": "arcgis_rest",
            "requested_crs": "EPSG:4326",
            "filter": where,
            "retrieved_features": len(features),
            "truncated": truncated,
        },
    )


def describe() -> list[dict[str, Any]]:
    """Which catalogues may be searched, for docs and for a confirmation prompt."""
    return [
        {
            "id": p.id,
            "title": p.title,
            "protocol": p.protocol,
            "allowed_hosts": list(p.allowed_hosts),
            "requires_key": p.requires_key,
            "notes": p.notes,
        }
        for p in PORTALS
    ]
