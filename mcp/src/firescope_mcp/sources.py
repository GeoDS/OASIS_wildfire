"""Allow-listed public API clients returning one honest GeoJSON contract.

No caller can supply an upstream URL. Every request is constructed from a
source id, validated parameters, and the fixed Southern California scope.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlparse

from .scope import ALTADENA_CENTER, CONUS_BBOX, SOCAL_BBOX, scope_metadata

SourceId = Literal["weather", "air_quality", "wfigs", "hmsfire", "fire_history", "firms"]

USER_AGENT = os.getenv(
    "FIRESCOPE_MCP_USER_AGENT",
    "firescope-socal-mcp/0.1 (public-data demo; replace-with-contact)",
)
TIMEOUT_SECONDS = float(os.getenv("FIRESCOPE_MCP_TIMEOUT_SECONDS", "20"))
CACHE_TTL_SECONDS = float(os.getenv("FIRESCOPE_MCP_CACHE_TTL_SECONDS", "300"))

WFIGS_URL = (
    "https://services3.arcgis.com/T4QMspbfLg3qTGWY/ArcGIS/rest/services/"
    "WFIGS_Interagency_Perimeters_Current/FeatureServer/0/query"
)
FIRE_HISTORY_URL = (
    "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/"
    "InterAgencyFirePerimeterHistory_All_Years_View/FeatureServer/0/query"
)
HMS_FIRE_URL = (
    "https://satepsanone.nesdis.noaa.gov/pub/FIRE/web/HMS/Fire_Points/Text/"
    "{year}/{month}/hms_fire{day}.txt"
)
#: FIRMS answers a bad key with the plain text "Invalid MAP_KEY." - HTTP 400 on
#: this endpoint and 401 on the availability one. Neither is JSON and neither is
#: CSV, so the reply must be inspected before it is parsed.
FIRMS_AREA_URL = (
    "https://firms.modaps.eosdis.nasa.gov/api/area/csv/"
    "{key}/{source}/{west},{south},{east},{north}/{days}"
)
#: Backend-held, like CENSUS_API_KEY. Free from NASA, and never asked of a user.
FIRMS_MAP_KEY = os.getenv("FIRMS_MAP_KEY", "")
#: Suomi-NPP VIIRS: 375 m, the finest resolution FIRMS publishes for this region.
FIRMS_SOURCE = os.getenv("FIRMS_SOURCE", "VIIRS_SNPP_NRT")

NWS_POINTS_URL = "https://api.weather.gov/points/{latitude},{longitude}"
AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

SOURCE_CATALOG: dict[SourceId, dict[str, Any]] = {
    "weather": {
        "label": "NWS hourly weather and derived downwind vector",
        "provider": "NOAA National Weather Service",
        "geometry": ["Point", "LineString"],
        "temporality": "first available hourly forecast period",
        "scope": "one caller-selected point inside the Southern California bbox",
        "limit": 2,
        "api_key_required": False,
        "caveat": "Forecast, not an observation. Wind speed remains the NWS text value.",
    },
    "air_quality": {
        "label": "Current modeled air quality",
        "provider": "Open-Meteo / CAMS global atmospheric forecast",
        "geometry": ["Point"],
        "temporality": "current model condition",
        "scope": "one caller-selected point inside the Southern California bbox",
        "limit": 1,
        "api_key_required": False,
        "caveat": (
            "Modeled air quality at coarse global resolution, not a ground monitor; "
            "poor air cannot be attributed to wildfire from this layer alone."
        ),
    },
    "wfigs": {
        "label": "Current agency wildfire perimeters",
        "provider": "NIFC WFIGS",
        "geometry": ["Polygon", "MultiPolygon"],
        "temporality": "current upstream view",
        "scope": "fixed Southern California bbox",
        "limit": 800,
        "api_key_required": False,
        "caveat": "Agency-verified perimeters can lag current conditions; empty is valid.",
    },
    "firms": {
        "label": "NASA FIRMS near-real-time thermal detections",
        "provider": "NASA FIRMS (VIIRS S-NPP, 375 m)",
        "geometry": ["Point"],
        "temporality": "past 1-5 days, roughly 3 hours behind the satellite pass",
        "scope": "fixed Southern California bbox",
        "limit": 2000,
        "api_key_required": True,
        "caveat": (
            "A thermal anomaly is not a wildfire: flares, kilns and hot roofs are "
            "detected too. Each point is a pixel footprint, not a burned area, and "
            "FRP measures radiated power at the moment of the pass, not fire size."
        ),
    },
    "hmsfire": {
        "label": "NOAA HMS daily satellite thermal detections",
        "provider": "NOAA NESDIS Hazard Mapping System",
        "geometry": ["Point"],
        "temporality": "caller-selected UTC day",
        "scope": "fixed Southern California bbox",
        "limit": 2000,
        "api_key_required": False,
        "caveat": ("A point is a satellite heat detection, not a confirmed wildfire or perimeter."),
    },
    "fire_history": {
        "label": "Historical agency fire perimeters",
        "provider": "NIFC Interagency Fire Perimeter History",
        "geometry": ["Polygon", "MultiPolygon"],
        "temporality": "historical, filtered by start year",
        "scope": "fixed Southern California bbox",
        "limit": 1000,
        "api_key_required": False,
        "caveat": "Perimeters have no burn severity and the national archive can lag.",
    },
}

_CACHE: dict[str, tuple[float, bytes]] = {}
_CACHE_LOCK = threading.Lock()
_ALLOWED_HOSTS = {
    "api.weather.gov",
    "air-quality-api.open-meteo.com",
    "services3.arcgis.com",
    "satepsanone.nesdis.noaa.gov",
    "firms.modaps.eosdis.nasa.gov",
}


def list_sources() -> dict:
    return {
        "scope": scope_metadata(),
        "sources": SOURCE_CATALOG,
        "rules": [
            "Choose only the minimum sources required for the request.",
            "Check metadata.status even when the MCP tool call succeeds.",
            "Treat empty as a valid result and never fill it with substitute data.",
            "Never substitute HMS heat detections for WFIGS confirmed perimeters.",
        ],
    }


def _validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_HOSTS:
        raise ValueError(f"Upstream URL is outside the allow-list: {parsed.hostname}")


def _request_bytes(url: str, *, params: dict[str, Any] | None = None) -> bytes:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    _validate_url(url)

    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(url)
        if cached and now - cached[0] <= CACHE_TTL_SECONDS:
            return cached[1]

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/geo+json, application/json, text/plain;q=0.9",
        },
    )
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                body = response.read()
            with _CACHE_LOCK:
                _CACHE[url] = (time.monotonic(), body)
            return body
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 1:
                break
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt == 1:
                break
        time.sleep(0.4)
    assert last_error is not None
    raise last_error


def _request_json(url: str, *, params: dict[str, Any] | None = None) -> dict:
    payload = json.loads(_request_bytes(url, params=params))
    if not isinstance(payload, dict):
        raise TypeError("Upstream returned JSON that is not an object")
    return payload


def _metadata(
    source: SourceId,
    features: list[dict],
    *,
    scope: str,
    notice: str | None = None,
    truncated: bool = False,
) -> dict:
    status = "loaded" if features else "empty"
    if not features and notice is None:
        notice = "The request succeeded, but no records were found in scope."
    return {
        "source": source,
        "status": status,
        "featureCount": len(features),
        "retrievedAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "label": SOURCE_CATALOG[source]["label"],
        "scope": scope,
        "notice": notice,
        "truncated": truncated,
        "caveat": SOURCE_CATALOG[source]["caveat"],
    }


def _feature_collection(
    source: SourceId,
    features: list[dict],
    *,
    scope: str,
    notice: str | None = None,
    truncated: bool = False,
) -> dict:
    valid = [
        feature
        for feature in features
        if isinstance(feature, dict)
        and feature.get("type") == "Feature"
        and isinstance(feature.get("geometry"), dict)
        and feature["geometry"].get("type")
        and feature["geometry"].get("coordinates") is not None
    ]
    return {
        "type": "FeatureCollection",
        "features": valid,
        "metadata": _metadata(
            source,
            valid,
            scope=scope,
            notice=notice,
            truncated=truncated,
        ),
    }


def _error_collection(source: SourceId, exc: Exception, *, scope: str) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [],
        "metadata": {
            "source": source,
            "status": "error",
            "featureCount": 0,
            "retrievedAt": datetime.now(UTC).isoformat(timespec="seconds"),
            "label": SOURCE_CATALOG[source]["label"],
            "scope": scope,
            "notice": f"Upstream request failed after at most one retry: {type(exc).__name__}",
            "truncated": False,
            "caveat": SOURCE_CATALOG[source]["caveat"],
        },
    }


def _arcgis_features(payload: dict) -> list[dict]:
    if payload.get("error"):
        raise ValueError(f"ArcGIS error: {payload['error']}")
    features = payload.get("features", [])
    if not isinstance(features, list):
        raise TypeError("ArcGIS response has no feature array")
    return features


def fetch_wfigs(national: bool = False) -> dict:
    """Current agency wildfire perimeters, in one of two fixed scopes.

    `national` selects the contiguous-US box rather than the demo box. It is a
    flag over two constants, deliberately, so no caller ever supplies geometry.
    """
    box = CONUS_BBOX if national else SOCAL_BBOX
    # ~1 km. Full-resolution national perimeters are 1.1 million coordinates and
    # 40 MB, which is not a slow map but a broken one - the browser overflows its
    # call stack computing bounds. At this scale the simplification is invisible;
    # at demo scale, where two perimeters sit beside a city boundary, it is not,
    # so only the national request is generalised.
    generalise = {"maxAllowableOffset": "0.01"} if national else {}
    scope = (
        f"Contiguous United States bbox {box.as_list()}"
        if national
        else f"Southern California bbox {box.as_list()}"
    )
    try:
        payload = _request_json(
            WFIGS_URL,
            params={
                "geometry": box.arcgis_envelope(),
                "geometryType": "esriGeometryEnvelope",
                "inSR": "4326",
                "spatialRel": "esriSpatialRelIntersects",
                "where": "attr_IncidentTypeCategory='WF'",
                "outFields": (
                    "poly_IncidentName,poly_GISAcres,poly_DateCurrent,"
                    "attr_PercentContained,attr_IncidentSize,attr_POOState,attr_POOCounty"
                ),
                "returnGeometry": "true",
                "outSR": "4326",
                "resultRecordCount": "800",
                "f": "geojson",
                **generalise,
            },
        )
        features = _arcgis_features(payload)
        truncated = len(features) >= 800
        notice = "Result reached the 800-feature display cap." if truncated else None
        collection = _feature_collection(
            "wfigs", features[:800], scope=scope, notice=notice, truncated=truncated
        )
        if national:
            # A drawn boundary that is not the published boundary must say so.
            collection["metadata"]["caveat"] = (
                f"{collection['metadata']['caveat']} Boundaries are simplified to about "
                "1 km for display at national scale; use the demo scope for a perimeter "
                "drawn at full resolution."
            )
        return collection
    except Exception as exc:  # noqa: BLE001 - source failure must remain data
        return _error_collection("wfigs", exc, scope=scope)


def fetch_fire_history(start_year: int = 2000) -> dict:
    current_year = datetime.now(UTC).year
    if not 1900 <= start_year <= current_year:
        raise ValueError(f"start_year must be between 1900 and {current_year}")
    scope = f"Southern California bbox {SOCAL_BBOX.as_list()}, fire year >= {start_year}"
    try:
        payload = _request_json(
            FIRE_HISTORY_URL,
            params={
                "geometry": SOCAL_BBOX.arcgis_envelope(),
                "geometryType": "esriGeometryEnvelope",
                "inSR": "4326",
                "spatialRel": "esriSpatialRelIntersects",
                "where": f"FIRE_YEAR_INT >= {start_year}",
                "outFields": "INCIDENT,FIRE_YEAR,GIS_ACRES,AGENCY",
                "returnGeometry": "true",
                "outSR": "4326",
                "orderByFields": "FIRE_YEAR_INT DESC",
                "resultRecordCount": "1000",
                "f": "geojson",
            },
        )
        features = _arcgis_features(payload)
        truncated = len(features) >= 1000
        notice = "Result reached the 1000-feature display cap." if truncated else None
        return _feature_collection(
            "fire_history",
            features[:1000],
            scope=scope,
            notice=notice,
            truncated=truncated,
        )
    except Exception as exc:  # noqa: BLE001 - source failure must remain data
        return _error_collection("fire_history", exc, scope=scope)


def _parse_hms(text: str, *, limit: int = 2000) -> tuple[list[dict], bool]:
    features: list[dict] = []
    seen: set[tuple[Any, ...]] = set()
    reader = csv.reader(io.StringIO(text), skipinitialspace=True)
    next(reader, None)
    for row in reader:
        if len(row) < 5:
            continue
        try:
            longitude, latitude = float(row[0]), float(row[1])
        except ValueError:
            continue
        if not SOCAL_BBOX.contains(longitude, latitude):
            continue
        satellite = row[4].strip() if len(row) > 4 else "unknown"
        key = (round(longitude, 5), round(latitude, 5), row[2], row[3], satellite)
        if key in seen:
            continue
        seen.add(key)
        frp: float | None = None
        if len(row) > 7:
            try:
                frp = float(row[7])
            except ValueError:
                pass
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [longitude, latitude]},
                "properties": {
                    "yearDay": row[2].strip() if len(row) > 2 else None,
                    "timeUtc": row[3].strip() if len(row) > 3 else None,
                    "satellite": satellite,
                    "method": row[5].strip() if len(row) > 5 else None,
                    "ecosystem": row[6].strip() if len(row) > 6 else None,
                    "frpMw": frp,
                    "family": "satellite_hotspots",
                },
            }
        )
        if len(features) > limit:
            return features[:limit], True
    return features, False


def _validate_hms_day(day: str | None) -> str:
    if day is None:
        return datetime.now(UTC).strftime("%Y%m%d")
    try:
        parsed = datetime.strptime(day, "%Y%m%d").replace(tzinfo=UTC).date()
    except ValueError as exc:
        raise ValueError("date must use YYYYMMDD, for example 20250108") from exc
    if parsed > datetime.now(UTC).date():
        raise ValueError("date cannot be in the future")
    if parsed.year < 2003:
        raise ValueError("HMS archive requests before 2003 are not supported by this demo")
    return day


def fetch_hmsfire(day: str | None = None) -> dict:
    selected_day = _validate_hms_day(day)
    scope = f"Southern California bbox {SOCAL_BBOX.as_list()}, UTC day {selected_day}"
    url = HMS_FIRE_URL.format(year=selected_day[:4], month=selected_day[4:6], day=selected_day)
    try:
        text = _request_bytes(url).decode("utf-8", errors="replace")
        features, truncated = _parse_hms(text)
        notice = "Result reached the 2000-feature display cap." if truncated else None
        return _feature_collection(
            "hmsfire",
            features,
            scope=scope,
            notice=notice,
            truncated=truncated,
        )
    except Exception as exc:  # noqa: BLE001 - source failure must remain data
        return _error_collection("hmsfire", exc, scope=scope)


_WIND_BEARINGS = {
    "N": 0.0,
    "NNE": 22.5,
    "NE": 45.0,
    "ENE": 67.5,
    "E": 90.0,
    "ESE": 112.5,
    "SE": 135.0,
    "SSE": 157.5,
    "S": 180.0,
    "SSW": 202.5,
    "SW": 225.0,
    "WSW": 247.5,
    "W": 270.0,
    "WNW": 292.5,
    "NW": 315.0,
    "NNW": 337.5,
}


#: VIIRS publishes confidence as a letter, which means nothing on a popup.
_FIRMS_CONFIDENCE = {"l": "low", "n": "nominal", "h": "high"}


def _parse_firms(text: str, *, limit: int = 2000) -> tuple[list[dict], bool]:
    """Detections inside the demo scope, with intensity and confidence kept.

    `frp` is the one thing this source has that nothing else in the deployment
    does: radiated power in megawatts, a physical proxy for how hard a pixel is
    burning. Everything else here already existed as a yes/no detection.
    """
    features: list[dict] = []
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        try:
            longitude = float(row["longitude"])
            latitude = float(row["latitude"])
        except (KeyError, TypeError, ValueError):
            continue
        if not SOCAL_BBOX.contains(longitude, latitude):
            continue

        def _number(field: str, source: dict = row) -> float | None:
            # `row` bound as a default rather than captured: a closure over the
            # loop variable is the classic way this silently reads the wrong row.
            try:
                return float(source[field])
            except (KeyError, TypeError, ValueError):
                return None

        scan, track = _number("scan"), _number("track")
        raw_confidence = (row.get("confidence") or "").strip().lower()
        acq_time = (row.get("acq_time") or "").strip().rjust(4, "0")
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [longitude, latitude]},
                "properties": {
                    "acquiredUtc": (
                        f"{(row.get('acq_date') or '').strip()} {acq_time[:2]}:{acq_time[2:]}"
                    ).strip(),
                    "satellite": (row.get("satellite") or "").strip() or None,
                    "instrument": (row.get("instrument") or "").strip() or None,
                    "frpMw": _number("frp"),
                    "confidence": _FIRMS_CONFIDENCE.get(raw_confidence, raw_confidence or None),
                    "brightnessKelvin": _number("bright_ti4") or _number("brightness"),
                    # Not 1 km except at nadir. A detection is a pixel footprint,
                    # never a mapped burned area, and the size says how coarse.
                    "footprintKm2": (
                        round(scan * track, 4) if scan is not None and track is not None else None
                    ),
                    "daynight": {"d": "day", "n": "night"}.get(
                        (row.get("daynight") or "").strip().lower()
                    ),
                    "family": "satellite_hotspots",
                },
            }
        )
        if len(features) > limit:
            return features[:limit], True
    return features, False


#: Three, not one. FIRMS counts the window back from the most recent available
#: date and NRT runs ~3 hours behind the pass, so a one-day query returned zero
#: detections on a day when the previous day held dozens - a window-boundary
#: effect that reads as "nothing is burning".
FIRMS_DEFAULT_DAYS = 3


def fetch_firms(days: int = FIRMS_DEFAULT_DAYS) -> dict:
    """Near-real-time thermal detections, or an honest account of why not.

    Three outcomes are kept apart on purpose. No key configured, a key the
    service rejected, and no detections in scope look identical to a careless
    client - and two of them are about us while the third is about the fire.
    """
    days = max(1, min(int(days), 5))
    scope = (
        f"Southern California bbox {SOCAL_BBOX.as_list()}, the {days} day(s) ending at "
        "the most recent satellite pass"
    )
    if not FIRMS_MAP_KEY:
        payload = _feature_collection("firms", [], scope=scope)
        payload["metadata"]["status"] = "not_configured"
        payload["metadata"]["notice"] = (
            "No FIRMS_MAP_KEY is configured, so no near-real-time detections were "
            "requested. This is a gap in this deployment, not an absence of fire."
        )
        return payload

    west, south, east, north = SOCAL_BBOX.as_list()
    url = FIRMS_AREA_URL.format(
        key=FIRMS_MAP_KEY, source=FIRMS_SOURCE,
        west=west, south=south, east=east, north=north, days=days,
    )
    def _rejected() -> dict:
        payload = _feature_collection("firms", [], scope=scope)
        payload["metadata"]["status"] = "rejected"
        payload["metadata"]["notice"] = (
            "FIRMS rejected the request; the configured MAP_KEY was not accepted. "
            "No conclusion about current fire activity can be drawn from this."
        )
        return payload

    try:
        text = _request_bytes(url).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        # The rejection arrives as HTTP 400 with the body "Invalid MAP_KEY." -
        # not a status a retry helps, and not a network problem. Reading the
        # body is the only way to tell "our credentials are wrong" from "the
        # service is down", and those are different things to tell a user.
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - an unreadable body is not a verdict
            body = ""
        if "invalid map_key" in body.lower():
            return _rejected()
        return _error_collection("firms", exc, scope=scope)
    except Exception as exc:  # noqa: BLE001 - source failure must remain data
        return _error_collection("firms", exc, scope=scope)

    if "invalid map_key" in text[:200].lower() or not text.lstrip().lower().startswith("latitude"):
        # Answered 200, but not with data. Parsing this as CSV would report zero
        # detections, which states something about the fire rather than about us.
        return _rejected()

    features, truncated = _parse_firms(text)
    payload = _feature_collection(
        "firms", features, scope=scope,
        notice="Result reached the 2000-feature display cap." if truncated else None,
        truncated=truncated,
    )
    payload["metadata"]["status"] = "ok"
    return payload


def _cardinal_direction(bearing: float) -> str:
    return min(
        _WIND_BEARINGS,
        key=lambda direction: abs((bearing - _WIND_BEARINGS[direction] + 180) % 360 - 180),
    )


def _destination(
    longitude: float,
    latitude: float,
    *,
    bearing_degrees: float,
    distance_km: float,
) -> list[float]:
    radians = math.radians(bearing_degrees)
    delta_lat = distance_km * math.cos(radians) / 111.32
    delta_lon = (
        distance_km * math.sin(radians) / (111.32 * max(math.cos(math.radians(latitude)), 1e-6))
    )
    return [longitude + delta_lon, latitude + delta_lat]


def _weather_features(
    period: dict,
    *,
    longitude: float,
    latitude: float,
) -> list[dict]:
    properties = {
        "name": "NWS hourly forecast",
        "forecastTime": period.get("startTime"),
        "temperature": period.get("temperature"),
        "temperatureUnit": period.get("temperatureUnit"),
        "relativeHumidity": (period.get("relativeHumidity") or {}).get("value"),
        "windSpeed": period.get("windSpeed"),
        "windDirection": period.get("windDirection"),
        "shortForecast": period.get("shortForecast"),
        "source": "NOAA National Weather Service",
    }
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [longitude, latitude]},
            "properties": properties,
        }
    ]
    direction = str(period.get("windDirection") or "").upper()
    if direction in _WIND_BEARINGS:
        downwind = (_WIND_BEARINGS[direction] + 180.0) % 360.0
        downwind_direction = _cardinal_direction(downwind)
        wind_speed = period.get("windSpeed")
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [longitude, latitude],
                        _destination(
                            longitude,
                            latitude,
                            bearing_degrees=downwind,
                            distance_km=18.0,
                        ),
                    ],
                },
                "properties": {
                    "name": f"Downwind {downwind_direction} · {wind_speed or 'speed unavailable'}",
                    "displayLabel": f"↓ {downwind_direction} · {wind_speed or '?'}",
                    "windDirectionReported": direction,
                    "windSpeed": wind_speed,
                    "reportedWind": f"From {direction} at {wind_speed or 'unknown speed'}",
                    "downwindDirection": downwind_direction,
                    "bearingDegrees": downwind,
                    "forecastTime": period.get("startTime"),
                    "source": "NOAA National Weather Service",
                    "derived": True,
                    "notice": (
                        f"NWS reports wind from {direction}; the arrow points toward "
                        f"{downwind_direction}. Its 18 km length does not predict smoke travel."
                    ),
                },
            }
        )
    return features


def fetch_weather(
    latitude: float | None = None,
    longitude: float | None = None,
) -> dict:
    default_lon, default_lat = ALTADENA_CENTER
    latitude = default_lat if latitude is None else latitude
    longitude = default_lon if longitude is None else longitude
    if not SOCAL_BBOX.contains(longitude, latitude):
        raise ValueError(
            f"Weather point must be inside Southern California bbox {SOCAL_BBOX.as_list()}"
        )
    scope = f"Point [{longitude}, {latitude}] inside Southern California demo bbox"
    try:
        point = _request_json(
            NWS_POINTS_URL.format(latitude=round(latitude, 4), longitude=round(longitude, 4))
        )
        forecast_url = (point.get("properties") or {}).get("forecastHourly")
        if not isinstance(forecast_url, str):
            raise TypeError("NWS point response did not provide forecastHourly")
        _validate_url(forecast_url)
        forecast = _request_json(forecast_url)
        periods = (forecast.get("properties") or {}).get("periods") or []
        features = (
            _weather_features(periods[0], longitude=longitude, latitude=latitude) if periods else []
        )
        return _feature_collection("weather", features, scope=scope)
    except Exception as exc:  # noqa: BLE001 - source failure must remain data
        return _error_collection("weather", exc, scope=scope)


def _air_quality_feature(payload: dict, *, longitude: float, latitude: float) -> dict:
    current = payload.get("current") or {}
    units = payload.get("current_units") or {}
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [longitude, latitude]},
        "properties": {
            "name": "Current modeled air quality",
            "time": current.get("time"),
            "usAqi": current.get("us_aqi"),
            "pm25": current.get("pm2_5"),
            "pm25Unit": units.get("pm2_5", "μg/m³"),
            "pm10": current.get("pm10"),
            "ozone": current.get("ozone"),
            "source": "Open-Meteo / CAMS global",
            "modeled": True,
        },
    }


def fetch_air_quality(
    latitude: float | None = None,
    longitude: float | None = None,
) -> dict:
    default_lon, default_lat = ALTADENA_CENTER
    latitude = default_lat if latitude is None else latitude
    longitude = default_lon if longitude is None else longitude
    if not SOCAL_BBOX.contains(longitude, latitude):
        raise ValueError(
            f"Air-quality point must be inside Southern California bbox {SOCAL_BBOX.as_list()}"
        )
    scope = f"Point [{longitude}, {latitude}] inside Southern California demo bbox"
    try:
        payload = _request_json(
            AIR_QUALITY_URL,
            params={
                "latitude": round(latitude, 4),
                "longitude": round(longitude, 4),
                "current": "us_aqi,pm2_5,pm10,ozone",
                "timezone": "auto",
            },
        )
        return _feature_collection(
            "air_quality",
            [_air_quality_feature(payload, longitude=longitude, latitude=latitude)],
            scope=scope,
        )
    except Exception as exc:  # noqa: BLE001 - source failure must remain data
        return _error_collection("air_quality", exc, scope=scope)


def fetch_public_geojson(
    source: SourceId,
    *,
    day: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    start_year: int = 2000,
    national: bool = False,
) -> dict:
    """Dispatch one allow-listed source. No arbitrary upstream URL is accepted."""
    if source == "weather":
        return fetch_weather(latitude=latitude, longitude=longitude)
    if source == "air_quality":
        return fetch_air_quality(latitude=latitude, longitude=longitude)
    if source == "wfigs":
        return fetch_wfigs(national=national)
    if source == "hmsfire":
        return fetch_hmsfire(day)
    if source == "fire_history":
        return fetch_fire_history(start_year)
    if source == "firms":
        return fetch_firms()
    raise ValueError(f"Unknown source: {source}")
