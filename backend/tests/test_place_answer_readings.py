"""A place answer reports the current readings it fetched.

Asked "Is there a fire near Riverside CA right now" on 2026-11-01, the reply was
a paragraph of caveats. Weather and air quality had been fetched and were in the
facts, but only under `details`: the sentence the narrator works from named the
perimeter check alone, and its rule against figures nobody asked for let it drop
the rest. Temperature and humidity never reached the facts at all.
"""

from __future__ import annotations

import asyncio

import pytest

from wildfire_agent import api, narration
from wildfire_agent.context_layers import city_context_status, city_weather_status
from wildfire_agent.contract import AnalysisContract
from wildfire_agent.planning.models import LayerResult


def _layer(capability_id: str, *properties: dict) -> LayerResult:
    return LayerResult(
        capability_id=capability_id,
        title=capability_id,
        hazard_object="active_fire",
        family=None,
        geometry_type="Point",
        caveat="c",
        feature_count=len(properties),
        source="test",
        geojson={
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-117.4, 33.95]},
                 "properties": p}
                for p in properties
            ],
        },
    )


#: Riverside as the NWS, Open-Meteo and FIRMS layers returned it on 2026-11-01.
WEATHER = _layer("public_weather_point", {
    "temperature": 66, "temperatureUnit": "F", "relativeHumidity": 74,
    "windSpeed": "10 mph", "windDirection": "SW", "shortForecast": "Slight Chance Rain Showers",
})
AIR = _layer("public_air_quality_point", {"usAqi": 37, "pm25": 2.2, "pm25Unit": "μg/m³"})
FIRMS = _layer(
    "public_firms_point",
    *({"frpMw": frp, "acquiredUtc": when} for frp, when in (
        (3.5, "2026-10-30 21:23"), (3.6, "2026-10-30 21:23"), (1.1, "2026-10-31 09:40"),
    )),
)


def _riverside(**layers) -> dict:
    return city_context_status(
        "Riverside",
        layers.get("perimeter"),
        layers.get("air", AIR),
        layers.get("weather", WEATHER),
        layers.get("firms", FIRMS),
    )


def test_the_answer_sentence_carries_the_current_readings():
    message = _riverside()["message"]
    for reading in ("66°F", "74% humidity", "wind SW 10 mph", "AQI 37"):
        assert reading in message, reading
    assert "slight chance rain showers" in message.lower()


def test_it_says_what_the_air_quality_index_means():
    assert "good (AQI 37)" in _riverside()["message"]


@pytest.mark.parametrize(
    "aqi, category",
    [(0, "good"), (50, "good"), (51, "moderate"), (101, "unhealthy for sensitive groups"),
     (151, "unhealthy"), (201, "very unhealthy"), (301, "hazardous")],
)
def test_air_quality_categories_follow_the_epa_breakpoints(aqi, category):
    air = _layer("public_air_quality_point", {"usAqi": aqi, "pm25": 1.0, "pm25Unit": "μg/m³"})
    assert f"{category} (AQI {aqi})" in _riverside(air=air)["message"]


def test_the_detections_are_counted_dated_and_not_called_fires():
    message = _riverside()["message"]
    assert "3 thermal detections" in message
    assert "Oct 30" in message and "Oct 31" in message
    assert "3.6 MW" in message
    assert "wildfire" not in message.lower()


def test_absence_is_still_not_reported_as_safety():
    assert "not a forecast" in _riverside()["message"]


def test_nothing_is_said_about_readings_that_were_not_fetched():
    message = _riverside(air=None, weather=None)["message"]
    assert "Right now" not in message
    assert "AQI" not in message


def test_the_readings_the_answer_must_keep_are_listed():
    readings = _riverside()["current_readings"]
    assert readings == ["66°F", "74% humidity", "wind SW 10 mph", "AQI 37"]


def test_a_weather_only_answer_is_untouched():
    assert "current_readings" not in city_weather_status("Riverside", WEATHER)


def _narrate(monkeypatch, draft: str, keep: tuple[str, ...]) -> str:
    async def fake_draft(_system, _human):
        return draft

    monkeypatch.setattr(narration, "is_mock", lambda: False)
    monkeypatch.setattr(narration, "_draft", fake_draft)
    status = _riverside()
    return asyncio.run(
        narration.narrate(question="Is there a fire near Riverside CA right now",
                          facts=status, fallback=status["message"], keep=keep)
    )


def test_a_reply_that_drops_the_readings_falls_back_to_the_sentence(monkeypatch):
    """The reported failure: a reply of caveats with the weather left out."""
    caveats_only = (
        "No agency-mapped fire perimeter currently overlaps Riverside; this is not a forecast. "
        "3 satellite thermal detections were recorded, but they are hot pixels."
    )
    reply = _narrate(monkeypatch, caveats_only, tuple(_riverside()["current_readings"]))
    assert reply == _riverside()["message"]


def test_a_reply_that_keeps_them_is_used(monkeypatch):
    good = (
        "No mapped fire perimeter overlaps Riverside right now. It is 66°F with 74% humidity "
        "and a SW wind at 10 mph, and air quality is good (AQI 37)."
    )
    assert _narrate(monkeypatch, good, tuple(_riverside()["current_readings"])) == good


def test_the_place_answer_asks_the_narrator_to_keep_them(monkeypatch):
    status = _riverside()
    told: dict = {}

    async def fake_city_context(_contract):
        return status, []

    async def fake_narrate(**kwargs):
        told.update(kwargs)
        return kwargs["fallback"]

    monkeypatch.setattr(api, "_automatic_city_context", fake_city_context)
    monkeypatch.setattr(api, "narrate", fake_narrate)
    contract = AnalysisContract(original_request="Is there a fire near Riverside CA right now")

    async def drain():
        return [event async for event in api._render_city_context(
            contract, api.MessageIn(text=contract.original_request), api._RenderOutcome())]

    asyncio.run(drain())
    assert told["keep"] == tuple(status["current_readings"])
