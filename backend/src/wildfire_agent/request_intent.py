"""Deterministic routing hints taken only from the user's own words.

These checks are deliberately small.  They do not replace the goal agent; they
guard data selection so an LLM restatement cannot turn a weather request into a
fire-data request.
"""

from __future__ import annotations

import re

_FIRE_RE = re.compile(
    r"\b(?:wildfires?|fires?|burn(?:ed|ing)?|smoke|perimeters?|containment)\b",
    re.IGNORECASE,
)
_FIRE_NEGATION_RE = re.compile(
    r"\b(?:not|no|without)\s+(?:about\s+|any\s+)?(?:wild)?fires?\b"
    r"|\bweather\s+only\b",
    re.IGNORECASE,
)
_WEATHER_RE = re.compile(
    r"\b(?:weather|forecast|temperature|humidity|wind(?:y|s|speed|direction)?)\b",
    re.IGNORECASE,
)
_AIR_QUALITY_RE = re.compile(r"\b(?:aqi|air\s+quality|pm\s*2\.?5)\b", re.IGNORECASE)


def explicitly_excludes_fire(text: str) -> bool:
    return bool(_FIRE_NEGATION_RE.search(text))


def requests_fire(text: str) -> bool:
    return bool(_FIRE_RE.search(text)) and not explicitly_excludes_fire(text)


def requests_weather(text: str) -> bool:
    return bool(_WEATHER_RE.search(text))


def requests_air_quality(text: str) -> bool:
    return bool(_AIR_QUALITY_RE.search(text))


def is_weather_only(text: str) -> bool:
    return requests_weather(text) and not requests_fire(text) and not requests_air_quality(text)
