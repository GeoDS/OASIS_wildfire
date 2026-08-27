"""Post-fire debris flow, found at run time rather than configured in advance.

Where `exposure` fills a gap on a layer that already exists, this one has no
local layer at all: nothing in the deployment carries a debris-flow hazard area.
So the fill is a discovery - search a catalogue, read what came back, fetch it -
and it is the case the review had in mind when it asked for data the system was
not shipped with.

It is also the point of the story. A fire that burned 529 km² of watershed above
three foothill cities did little structural damage; the consequence arrives with
the first winter rain, on ground that no longer holds it. Answering "what should
I watch next" needs a source nobody chose ahead of time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .portals import Discovery, FetchedLayer, PortalError, describe_service, fetch_features, search

HAZARD_OBJECT = "post_fire_debris_flow"

#: What a county post-fire programme publishes, against the taxonomy's names.
SUPPLIES: tuple[str, ...] = ("burn scar extent", "potential hazard area", "watershed subarea")

#: The search term. Fixed rather than model-written: the term decides which
#: catalogue entries are even considered, so it is part of the analysis, not
#: part of the conversation.
SEARCH_TERM = "post-fire debris flow hazard"

#: Layer names within a discovered service, matched case-insensitively.
_HAZARD_LAYER_HINT = "potential hazard areas"
_SCAR_LAYER_HINT = "fire ("


@dataclass
class DebrisFlowResult:
    layer: FetchedLayer | None = None
    service_title: str = ""
    service_owner: str = ""
    layer_name: str = ""
    fire_matched: str = ""
    closed: tuple[str, ...] = ()
    source: dict[str, Any] = field(default_factory=dict)
    note: str = ""


def _fire_filter(fire_name: str) -> str:
    """Match the publisher's own spelling of the fire, escaping the quote.

    Both spellings, because the publisher uses both. Los Angeles County writes
    'Bobcat Fire' and plain 'Woolsey' in the same column. Normalising to one
    form matched 0 of the 2,248 polygons published for Woolsey, and the system
    reported that the service publishes no hazard area for it - a false negative
    stated as a fact about the fire, which is the one thing this module's note
    was written to avoid.

    An equality disjunction rather than LIKE: '%Lake%' would also claim Lake
    Hughes' polygons for the Lake Fire, and a wrong burn scar is worse than a
    missing one.
    """
    bare = fire_name.strip()
    if bare.lower().endswith(" fire"):
        bare = bare[: -len(" fire")].strip()
    variants = dict.fromkeys([f"{bare} Fire", bare])
    return " OR ".join(f"FIRE='{name.replace(chr(39), chr(39) * 2)}'" for name in variants)


def discover(term: str = SEARCH_TERM, *, limit: int = 8) -> list[Discovery]:
    """Catalogue hits that this deployment is allowed to read."""
    return [hit for hit in search(term, limit=limit) if hit.fetchable]


def offer(fire_name: str, missing: tuple[str, ...]) -> dict[str, Any] | None:
    """Describe the fetch for approval, naming what it will and will not close."""
    closes = tuple(v for v in missing if v in SUPPLIES)
    if not closes:
        return None
    try:
        hits = discover()
    except PortalError:
        return None
    if not hits:
        return None
    best = hits[0]
    return {
        "source_id": "portal_debris_flow",
        "hazard_object": HAZARD_OBJECT,
        "source": f"{best.title} ({best.owner})",
        "dataset": "Discovered through the ArcGIS Online catalogue at request time",
        "geography": "Mapped hazard polygons within the burn scar's watersheds",
        "closes": closes,
        "remaining": tuple(v for v in missing if v not in SUPPLIES),
        "endpoint": best.url,
        "fire": fire_name,
        "quality_notes": [
            (
                "Published by a county programme for planning, not as a forecast of any "
                "particular storm."
            ),
            (
                "Hazard areas are modelled from the burn scar and terrain; they say where "
                "debris flow is possible, not that one has happened or will."
            ),
            (
                "Found by searching a public catalogue at request time, so the service and "
                "its contents may change between runs."
            ),
        ],
    }


def fetch(fire_name: str, endpoint: str, *, limit: int = 1500) -> DebrisFlowResult:
    """Read the hazard-area layer for one fire from a discovered service."""
    metadata = describe_service(endpoint)
    layers = list(metadata.get("layers") or [])
    hazard_layers = [
        layer for layer in layers if _HAZARD_LAYER_HINT in str(layer.get("name", "")).lower()
    ]
    if not hazard_layers:
        return DebrisFlowResult(note="The discovered service carries no potential-hazard layer.")

    # Phase 1 is the earliest assessment and the one every fire has; later
    # phases exist only where the county revisited the burn scar.
    target = hazard_layers[0]
    fetched = fetch_features(
        f"{endpoint.rstrip('/')}/{target['id']}",
        where=_fire_filter(fire_name),
        limit=limit,
    )
    if not fetched.feature_count:
        return DebrisFlowResult(
            layer_name=str(target.get("name") or ""),
            note=(
                f"The service was reached, but it publishes no hazard area for "
                f"{fire_name}. That is an answer about this fire, not a failure."
            ),
        )
    return DebrisFlowResult(
        layer=fetched,
        service_title=str(metadata.get("serviceDescription") or metadata.get("name") or ""),
        layer_name=str(target.get("name") or ""),
        fire_matched=fire_name,
        closed=SUPPLIES,
        source={
            **fetched.source,
            "discovered_via": "ArcGIS Online catalogue",
            "layer": target.get("name"),
            "hazard_object": HAZARD_OBJECT,
        },
    )
