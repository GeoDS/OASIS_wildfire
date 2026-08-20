"""Snapshot the Altadena / Eaton Fire showcase dataset into `backend/data/`.

Run once; the output is committed so the demo never depends on the network:

    uv run python scripts/fetch_showcase_data.py

Every source below is public and needs no API key. Each layer is written with a
`provenance` block recording where it came from and when it was retrieved -
a snapshot presented without its origin is indistinguishable from invented
data, and this project's whole argument is that you can see what is real.

Showcase area: **Altadena, California** - an unincorporated community of about
42,000 in Los Angeles County, roughly 22 km2, most of which burned in the Eaton
Fire of January 2025. Small enough to read on one screen, unlike "Los Angeles".

Why these three layers: they make the central distinction of the whole system
visible on a map for the first time. `official_fire_perimeters` is one
agency-verified polygon set; `satellite_hotspots` is over a thousand thermal
detections, some of them coarse GOES pixels and some not wildfire at all.
Choosing between them does not change the resolution of the answer, it changes
the answer.
"""

from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "altadena"

#: Altadena and the Eaton Fire footprint. [west, south, east, north]
AOI_BBOX = (-118.28, 34.12, -117.96, 34.32)
AOI_CENTER = (-118.1312, 34.1897)

#: The Eaton Fire ran 7-31 January 2025. HMS archives one file per day; two days
#: at the peak are plenty for a showcase and keep the snapshot small.
HMS_DAYS = ("20250108", "20250109")

USER_AGENT = "wildfire-analyst-agent/0.1 (competition demo; showcase data snapshot)"


def _get(url: str, params: dict | None = None, timeout: int = 90) -> bytes:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _write(name: str, features: list[dict], provenance: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "type": "FeatureCollection",
        "provenance": provenance,
        "features": features,
    }
    path = DATA_DIR / f"{name}.geojson"
    path.write_text(json.dumps(payload, separators=(",", ":")))
    size_kb = path.stat().st_size / 1024
    print(f"  wrote {path.name}: {len(features)} features, {size_kb:.0f} KB")


def _in_aoi(lon: float, lat: float) -> bool:
    w, s, e, n = AOI_BBOX
    return w <= lon <= e and s <= lat <= n


# ══════════════════════════════════════════════════════════════════
# 1. Official fire perimeter - LA County Public Works, Eaton Fire
# ══════════════════════════════════════════════════════════════════

EATON_PERIMETER_URL = (
    "https://services.arcgis.com/RmCCgQtiZLDCtblq/arcgis/rest/services/"
    "Palisades_and_Eaton_Dissolved_Fire_Perimeters_as_of_20250121/FeatureServer/0/query"
)


def fetch_official_perimeter() -> None:
    print("official_fire_perimeters (LA County Public Works)")
    raw = _get(
        EATON_PERIMETER_URL,
        {
            "where": "1=1",
            "outFields": "*",
            "returnGeometry": "true",
            "outSR": "4326",
            "f": "geojson",
        },
    )
    fc = json.loads(raw)

    # The service publishes many small heat-perimeter fragments plus the main
    # footprint. Keep anything of real size and drop pixel-scale slivers, which
    # would otherwise dominate the feature count without adding information.
    features = []
    for feat in fc.get("features", []):
        geom = feat.get("geometry")
        if not geom:
            continue
        feat["properties"] = {
            "incident_name": "Eaton Fire",
            "family": "official_fire_perimeters",
            "status": "agency perimeter as of 2025-01-21",
            "type": feat.get("properties", {}).get("type", "Heat Perimeter"),
        }
        features.append(feat)

    _write(
        "official_fire_perimeters",
        features,
        {
            "title": "Eaton Fire perimeter",
            "source": "Los Angeles County Department of Public Works (ArcGIS Online)",
            "url": EATON_PERIMETER_URL.replace("/query", ""),
            "as_of": "2025-01-21",
            "retrieved_at": _now(),
            "family": "official_fire_perimeters",
            "caveat": (
                "Agency-verified perimeter. Authoritative but published with a lag; "
                "it reflects the situation on 21 January 2025, not live conditions."
            ),
        },
    )


# ══════════════════════════════════════════════════════════════════
# 2. Satellite thermal detections - NOAA HMS daily archive
# ══════════════════════════════════════════════════════════════════

HMS_URL = (
    "https://satepsanone.nesdis.noaa.gov/pub/FIRE/web/HMS/Fire_Points/Text/{y}/{m}/hms_fire{d}.txt"
)


def fetch_satellite_hotspots() -> None:
    print("satellite_hotspots (NOAA HMS archive)")
    features: list[dict] = []
    seen: set[tuple] = set()

    for day in HMS_DAYS:
        url = HMS_URL.format(y=day[:4], m=day[4:6], d=day)
        text = _get(url).decode("utf-8", errors="replace")
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if not lines:
            print(f"  {day}: empty")
            continue

        for line in lines[1:]:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 5:
                continue
            try:
                lon, lat = float(parts[0]), float(parts[1])
            except ValueError:
                continue
            if not _in_aoi(lon, lat):
                continue

            satellite = parts[4] if len(parts) > 4 else "unknown"
            # HMS repeats the same pixel across scans; one point per
            # location-satellite-day is enough and keeps the file small.
            key = (round(lon, 4), round(lat, 4), satellite, day)
            if key in seen:
                continue
            seen.add(key)

            frp = None
            if len(parts) > 7:
                try:
                    frp = float(parts[7])
                except ValueError:
                    frp = None

            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [lon, lat]},
                    "properties": {
                        "family": "satellite_hotspots",
                        "acq_date": f"{day[:4]}-{day[4:6]}-{day[6:]}",
                        "satellite": satellite,
                        "method": parts[5] if len(parts) > 5 else None,
                        "frp_mw": frp,
                    },
                }
            )
        print(f"  {day}: {len(features)} cumulative unique detections in AOI")

    _write(
        "satellite_hotspots",
        features,
        {
            "title": "NOAA HMS satellite fire detections, Eaton Fire peak",
            "source": "NOAA NESDIS Hazard Mapping System (daily archive)",
            "url": HMS_URL.format(y="2025", m="01", d="20250108"),
            "as_of": "2025-01-08 / 2025-01-09",
            "retrieved_at": _now(),
            "family": "satellite_hotspots",
            "caveat": (
                "A detection is a thermal anomaly, not a confirmed wildfire. GOES pixels "
                "are coarse, and agricultural burning or industrial heat produces the same "
                "signature. Near real time, but not agency-verified."
            ),
        },
    )


# ══════════════════════════════════════════════════════════════════
# 3. Historical fire perimeters - NIFC interagency history
# ══════════════════════════════════════════════════════════════════

NIFC_HISTORY_URL = (
    "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/"
    "InterAgencyFirePerimeterHistory_All_Years_View/FeatureServer/0/query"
)


def fetch_historical_perimeters() -> None:
    print("historical_fire_perimeters (NIFC)")
    w, s, e, n = AOI_BBOX
    raw = _get(
        NIFC_HISTORY_URL,
        {
            "geometry": f"{w},{s},{e},{n}",
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "where": "FIRE_YEAR_INT >= 2000 AND GIS_ACRES > 100",
            "outFields": "INCIDENT,FIRE_YEAR,GIS_ACRES,AGENCY",
            "returnGeometry": "true",
            "outSR": "4326",
            "orderByFields": "FIRE_YEAR_INT DESC",
            "f": "geojson",
        },
    )
    fc = json.loads(raw)

    features = []
    seen_names: set[tuple] = set()
    for feat in fc.get("features", []):
        props = feat.get("properties", {}) or {}
        key = (str(props.get("INCIDENT", "")).upper(), props.get("FIRE_YEAR"))
        if key in seen_names:  # the history layer carries duplicate records
            continue
        seen_names.add(key)
        feat["properties"] = {
            "family": "historical_fire_perimeters",
            "incident_name": props.get("INCIDENT"),
            "fire_year": props.get("FIRE_YEAR"),
            "acres": round(props.get("GIS_ACRES") or 0),
            "agency": props.get("AGENCY"),
        }
        features.append(feat)

    _write(
        "historical_fire_perimeters",
        features,
        {
            "title": "Historical fire perimeters near Altadena, 2000 onwards",
            "source": "National Interagency Fire Center, InteragencyFirePerimeterHistory",
            "url": NIFC_HISTORY_URL.replace("/query", ""),
            "as_of": "all years to present",
            "retrieved_at": _now(),
            "family": "historical_fire_perimeters",
            "caveat": (
                "Perimeters only, with no burn severity. The layer lags by roughly a "
                "year, so the most recent fires - the Eaton Fire included - are absent."
            ),
        },
    )


def main() -> int:
    print(f"Snapshotting showcase data into {DATA_DIR}\n")
    for step in (fetch_official_perimeter, fetch_satellite_hotspots, fetch_historical_perimeters):
        try:
            step()
        except Exception as exc:  # noqa: BLE001 - one bad source must not stop the rest
            print(f"  FAILED: {type(exc).__name__}: {exc}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
