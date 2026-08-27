"""Backend-owned visual semantics for API and local map layers."""

from __future__ import annotations

from typing import Any, Literal

from .planning.models import LayerVisualization, LegendStop, PopupField

PublicSource = Literal["weather", "air_quality", "wfigs", "hmsfire", "fire_history"]


def _field(key: str, label: str, unit: str | None = None) -> PopupField:
    return PopupField(key=key, label=label, unit=unit)


def _stop(value: str | float, label: str, color: str) -> LegendStop:
    return LegendStop(value=value, label=label, color=color)


def _display_name(properties: dict[str, Any], fallback: str) -> str:
    for key in (
        "displayName",
        "name",
        "poly_IncidentName",
        "INCIDENT",
        "legalName",
    ):
        value = properties.get(key)
        if value not in (None, ""):
            return str(value)
    return fallback


def public_layer_visualization(
    source: PublicSource,
    geometry_type: str,
    features: list[dict[str, Any]],
) -> LayerVisualization:
    """Normalise feature labels and return the matching map contract."""
    for feature in features:
        properties = feature.setdefault("properties", {})
        properties["displayName"] = _display_name(properties, source.replace("_", " ").title())

    if source == "air_quality":
        return LayerVisualization(
            kind="graduated",
            label="U.S. AQI",
            field="usAqi",
            unit="AQI",
            stops=[
                _stop(0, "0–50 · Good", "#00e400"),
                _stop(51, "51–100 · Moderate", "#ffff00"),
                _stop(101, "101–150 · Unhealthy for sensitive groups", "#ff7e00"),
                _stop(151, "151–200 · Unhealthy", "#ff0000"),
                _stop(201, "201–300 · Very unhealthy", "#8f3f97"),
                _stop(301, "301+ · Hazardous", "#7e0023"),
            ],
            size_field="usAqi",
            popup_fields=[
                _field("usAqi", "U.S. AQI"),
                _field("pm25", "PM2.5", "μg/m³"),
                _field("pm10", "PM10", "μg/m³"),
                _field("ozone", "Ozone", "μg/m³"),
                _field("time", "Model time"),
                _field("modeled", "Modeled data"),
            ],
            explanation="EPA AQI health categories; this point is modeled, not a ground monitor.",
        )
    if source == "wfigs":
        return LayerVisualization(
            kind="graduated",
            label="Containment",
            field="attr_PercentContained",
            unit="%",
            stops=[
                _stop(0, "0–24%", "#9e2a2b"),
                _stop(25, "25–49%", "#d85c41"),
                _stop(50, "50–74%", "#e7a43a"),
                _stop(75, "75–99%", "#9cab55"),
                _stop(100, "100% contained", "#4f7c59"),
            ],
            popup_fields=[
                _field("poly_IncidentName", "Incident"),
                _field("attr_PercentContained", "Contained", "%"),
                _field("poly_GISAcres", "Mapped area", "acres"),
                _field("attr_IncidentSize", "Reported size", "acres"),
                _field("attr_POOCounty", "County"),
                _field("poly_DateCurrent", "Perimeter updated"),
            ],
            explanation="Polygon color shows reported containment, not fire intensity or safety.",
        )
    if source == "fire_history":
        return LayerVisualization(
            kind="graduated",
            label="Mapped fire area",
            field="GIS_ACRES",
            unit="acres",
            stops=[
                _stop(0, "Under 100 acres", "#fee8c8"),
                _stop(100, "100–999 acres", "#fdbb84"),
                _stop(1000, "1,000–9,999 acres", "#fc8d59"),
                _stop(10000, "10,000–49,999 acres", "#e34a33"),
                _stop(50000, "50,000+ acres", "#8c1d18"),
            ],
            popup_fields=[
                _field("INCIDENT", "Incident"),
                _field("FIRE_YEAR", "Fire year"),
                _field("GIS_ACRES", "Mapped area", "acres"),
                _field("AGENCY", "Agency"),
            ],
            explanation="Color represents mapped perimeter area, not burn severity.",
        )
    if source == "hmsfire":
        return LayerVisualization(
            kind="graduated",
            label="Fire radiative power",
            field="frpMw",
            unit="MW",
            stops=[
                _stop(0, "No FRP / below 5 MW", "#ffd166"),
                _stop(5, "5–19 MW", "#f8961e"),
                _stop(20, "20–49 MW", "#f45d48"),
                _stop(50, "50–99 MW", "#d62828"),
                _stop(100, "100+ MW", "#6a040f"),
            ],
            size_field="frpMw",
            popup_fields=[
                _field("frpMw", "Fire radiative power", "MW"),
                _field("satellite", "Satellite"),
                _field("timeUtc", "UTC time"),
                _field("method", "Detection method"),
                _field("ecosystem", "Ecosystem"),
            ],
            explanation="Satellite heat signal; it is not a confirmed wildfire perimeter.",
        )
    if source == "weather" and geometry_type == "LineString":
        return LayerVisualization(
            kind="vector",
            label="Downwind direction",
            color="#276f95",
            symbol="arrow",
            popup_fields=[
                _field("reportedWind", "Reported wind"),
                _field("downwindDirection", "Arrow points downwind"),
                _field("bearingDegrees", "Downwind bearing", "°"),
                _field("forecastTime", "Forecast time"),
                _field("notice", "Interpretation"),
            ],
            explanation="Arrow points downwind. Its 18 km length is illustrative, not a smoke forecast.",
        )
    return LayerVisualization(
        kind="fixed",
        label="Forecast location",
        color="#2f7ea5",
        popup_fields=[
            _field("shortForecast", "Forecast"),
            _field("temperature", "Temperature"),
            _field("relativeHumidity", "Relative humidity", "%"),
            _field("windSpeed", "Wind speed"),
            _field("windDirection", "Wind from"),
            _field("forecastTime", "Forecast time"),
        ],
        explanation="NWS hourly forecast at the resolved place point.",
    )
