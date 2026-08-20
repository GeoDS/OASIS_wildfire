# FireScope Southern California Public Data MCP

An isolated MCP server for public API retrieval in the FireScope demo. It keeps
the useful contract from the Madison handoff - allow-listed sources, unified
GeoJSON, explicit `loaded` / `empty` / `error` states, provenance, limits, and
no invented replacement data - while removing all Madison-specific scope and
city layers.

## Scope

Every spatial source is restricted to this WGS84 demo envelope:

```text
west -121.0, south 32.5, east -114.0, north 35.8
```

This rectangle covers the supplied Southern California fire events. It is a
demo envelope, not a formal administrative definition. Weather points outside
it are rejected, and callers cannot supply arbitrary upstream URLs.

## Sources

| source | public upstream | output | important caveat |
|---|---|---|---|
| `weather` | NOAA / NWS | forecast Point + derived downwind LineString | Forecast, not observation; line direction is illustrative |
| `wfigs` | NIFC WFIGS current perimeters | Polygon / MultiPolygon | Confirmed but may lag; empty is valid |
| `hmsfire` | NOAA NESDIS HMS daily text archive | Point | Thermal detection is not a confirmed wildfire |
| `fire_history` | NIFC fire perimeter history | Polygon / MultiPolygon | No burn severity; archive can lag |

Madison POI and roads are intentionally absent. NOAA smoke is also deferred
until its current shapefile/KML path has a tested GeoJSON conversion step.

## MCP surface

Resources:

- `firescope://public-api/catalog`
- `firescope://public-api/scope`

Tools:

- `list_public_data_sources()`
- `fetch_public_geojson(source, day?, latitude?, longitude?, start_year?)`

Every fetch returns:

```json
{
  "type": "FeatureCollection",
  "features": [],
  "metadata": {
    "source": "wfigs",
    "status": "loaded | empty | error",
    "featureCount": 0,
    "retrievedAt": "UTC ISO-8601",
    "scope": "...",
    "notice": null,
    "truncated": false,
    "caveat": "..."
  }
}
```

`empty` means the upstream call succeeded but nothing was found in scope. It is
not an error and must never be filled with mock or semantically different data.

## Run

The server uses the current stable v2 line of the official Python MCP SDK.

```bash
cd mcp
uv sync --group dev

# Local stdio server
uv run firescope-socal-mcp

# Inspector during development
uv run mcp dev src/firescope_mcp/server.py

# Tests never call public endpoints
uv run pytest
uv run ruff check src tests
```

Optional runtime settings:

```bash
FIRESCOPE_MCP_USER_AGENT="firescope-socal-mcp/0.1 (your-email@example.com)"
FIRESCOPE_MCP_TIMEOUT_SECONDS=20
FIRESCOPE_MCP_CACHE_TTL_SECONDS=300
```

Do not log MCP protocol messages with `print()` while using stdio; stdout is the
protocol wire. Use normal logging, which writes to stderr.

## LLM usage rules

1. Call `list_public_data_sources` before choosing a source when availability is unknown.
2. Fetch only the minimum sources needed for the question.
3. Inspect `metadata.status`, not just whether the tool call returned.
4. Preserve `retrievedAt`, `scope`, `notice`, `truncated`, and `caveat` in conclusions.
5. Never substitute HMS heat detections for confirmed WFIGS perimeters.
6. For `error`, the server has already retried at most once; report the source unavailable.
7. Never treat a capped result count as a complete regional total.

## Public documentation

- MCP Python SDK: https://github.com/modelcontextprotocol/python-sdk
- NWS API: https://www.weather.gov/documentation/services-web-api
- WFIGS layer: https://services3.arcgis.com/T4QMspbfLg3qTGWY/ArcGIS/rest/services/WFIGS_Interagency_Perimeters_Current/FeatureServer/0
- NOAA HMS: https://www.ospo.noaa.gov/products/land/hms.html
