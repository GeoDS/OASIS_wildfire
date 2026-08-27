"""MCP surface for Southern California public wildfire data."""

from __future__ import annotations

import asyncio
import json
from typing import Literal

from mcp.server import MCPServer

from .scope import scope_metadata
from .sources import fetch_public_geojson as _fetch_public_geojson
from .sources import list_sources

mcp = MCPServer("FireScope Southern California Public Data")


@mcp.resource("firescope://public-api/catalog")
def public_api_catalog() -> str:
    """Allowed sources, scope, limits, and caveats as JSON."""
    return json.dumps(list_sources(), ensure_ascii=False, indent=2)


@mcp.resource("firescope://public-api/scope")
def southern_california_scope() -> str:
    """The only geographic scope accepted by this demo server."""
    return json.dumps(scope_metadata(), ensure_ascii=False, indent=2)


@mcp.tool()
def list_public_data_sources() -> dict:
    """List Southern California sources before choosing which one to fetch."""
    return list_sources()


@mcp.tool()
async def fetch_public_geojson(
    source: Literal["weather", "air_quality", "wfigs", "hmsfire", "fire_history", "firms"],
    day: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    start_year: int = 2000,
    national: bool = False,
) -> dict:
    """Fetch one allow-listed source as GeoJSON with explicit status metadata.

    Parameters only apply to their relevant source: ``day`` (YYYYMMDD) for
    ``hmsfire``; latitude/longitude for point sources; and ``start_year`` for
    ``fire_history``. ``national`` widens ``wfigs`` from the Southern California
    demo box to the contiguous United States - a second fixed constant, never a
    caller-supplied geometry. Weather points outside the Southern California
    bbox are rejected. Always inspect ``metadata.status`` before using features.
    """
    return await asyncio.to_thread(
        _fetch_public_geojson,
        source,
        day=day,
        latitude=latitude,
        longitude=longitude,
        start_year=start_year,
        national=national,
    )


def main() -> None:
    # Stdio is the correct default for a local MCP host. Use `mcp run` with
    # streamable-http explicitly if this demo is later deployed as a service.
    mcp.run()


if __name__ == "__main__":
    main()
