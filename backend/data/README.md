# Showcase data

Snapshots of public data for the Altadena, California study area — the Eaton Fire of
January 2025. Regenerate with:

```bash
cd backend && uv run python scripts/fetch_showcase_data.py
```

They are committed so the demo does not depend on network access, upstream availability, or a
rate limit at the wrong moment.

## Sources and attribution

All three are public and require no API key. Each file carries a `provenance` block recording its
source, its as-of date, and when it was retrieved.

| File | Source | Terms |
|---|---|---|
| `altadena/official_fire_perimeters.geojson` | Los Angeles County Department of Public Works — Eaton Fire perimeter as of 2025-01-21 | US local government open data |
| `altadena/satellite_hotspots.geojson` | NOAA NESDIS Hazard Mapping System, daily fire product archive, 8–9 January 2025 | US federal government work, public domain |
| `altadena/historical_fire_perimeters.geojson` | National Interagency Fire Center — InteragencyFirePerimeterHistory, 2000 onwards | US federal government work, public domain |

Basemap tiles are served at runtime by CARTO from OpenStreetMap data, attributed in the map
itself; none of it is redistributed here.

## What these snapshots are not

- **Not live.** They describe January 2025. Any answer built on them says so.
- **Not complete.** The historical layer lags national reporting by about a year, so the Eaton
  Fire itself does not appear in it. Perimeters carry no burn severity.
- **Not interchangeable.** A satellite thermal detection is a heat signature, not a confirmed
  wildfire; it may be an agricultural burn or an industrial source. The confirmed perimeter and the
  detections answer different questions, which is precisely why the system asks the user which one
  they mean.
