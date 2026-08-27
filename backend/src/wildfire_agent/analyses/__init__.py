"""The registry of derived analyses this deployment can compute.

Adding an analysis is one entry here plus its implementation module. Nothing in
the API layer changes, because the API asks the registry rather than testing for
each analysis in turn - which is what the orchestrator used to do, one `elif` at
a time, until asking "which analysis is this?" meant reading three modules.

Recognition is scored, not ordered. A land-cover question resolved against a
previous turn about spread carries the word "spread", and a first-match rule
would hand it the wrong analysis in a confident voice.
"""

from __future__ import annotations

import re
from typing import Any

from ..fire_context import compile_fire_context, execute_fire_context
from ..spatial_analysis import compile_raster_analysis, execute_raster_analysis
from .base import AnalysisMatch, AnalysisSpec

_CHANGE = re.compile(
    r"\b(?:change|difference|differ|subtract|compare[ds]?|comparison|first day|last day|"
    r"before and after|before/after|delta)\b",
    re.IGNORECASE,
)
_NDVI = re.compile(r"\b(?:ndvi|greenness|vegetation index)\b", re.IGNORECASE)
_SEVERITY = re.compile(
    r"\b(?:dnbr|nbr|burn severity|burn-severity|severity|severe|how badly|damage level)\b",
    re.IGNORECASE,
)
_COMPOSITION = re.compile(
    r"\b(?:land ?cover|land ?use|vegetation type|fuel type|what kind of|what type of|"
    r"terrain|elevation|slope|forest|shrub|grass|chaparral)\b",
    re.IGNORECASE,
)
_SPREAD = re.compile(
    r"\b(?:spread|move[ds]?|moving|direction|which way|how fast|rate|advance[ds]?|run|"
    r"grew|growth|expand(?:ed|ing)?|progress(?:ed|ion)?)\b",
    re.IGNORECASE,
)
_WEATHER = re.compile(
    r"\b(?:weather|wind|humidity|temperature|drought|erc|energy release|"
    r"fire danger|conditions)\b",
    re.IGNORECASE,
)


def _compile_index(operation: str):
    def compile_for(request: str, fire_plan, *, original: str | None = None):
        return compile_raster_analysis(request, fire_plan, operation=operation)

    return compile_for


def _compile_context(analysis: str):
    def compile_for(request: str, fire_plan, *, original: str | None = None):
        return compile_fire_context(request, fire_plan, analysis=analysis)

    return compile_for


#: Declaration order breaks ties, so the more specific analysis comes first.
SPECS: tuple[AnalysisSpec, ...] = (
    AnalysisSpec(
        id="nbr_change",
        title="burn severity",
        event="spatial_analysis",
        compile=_compile_index("nbr_change"),
        execute=execute_raster_analysis,
        patterns=(_SEVERITY,),
        precedence=10,
        notes={"index": "nbr_viirs", "family": "index_change"},
    ),
    AnalysisSpec(
        id="ndvi_change",
        title="vegetation change",
        event="spatial_analysis",
        compile=_compile_index("ndvi_change"),
        execute=execute_raster_analysis,
        patterns=(_NDVI, _CHANGE),
        # NDVI alone names a subject; only a comparison asks for this analysis.
        requires=(_NDVI, _CHANGE),
        precedence=20,
        notes={"index": "ndvi_viirs", "family": "index_change"},
    ),
    AnalysisSpec(
        id="spread_behaviour",
        title="spread behaviour",
        event="fire_context",
        compile=_compile_context("spread_behaviour"),
        execute=execute_fire_context,
        patterns=(_SPREAD,),
        precedence=30,
        notes={"family": "fire_context"},
    ),
    AnalysisSpec(
        id="fire_weather",
        title="fire weather",
        event="fire_context",
        compile=_compile_context("fire_weather"),
        execute=execute_fire_context,
        patterns=(_WEATHER,),
        precedence=40,
        notes={"family": "fire_context"},
    ),
    AnalysisSpec(
        id="land_cover_composition",
        title="land cover and terrain",
        event="fire_context",
        compile=_compile_context("land_cover_composition"),
        execute=execute_fire_context,
        patterns=(_COMPOSITION,),
        precedence=50,
        notes={"family": "fire_context"},
    ),
)

ANALYSES: dict[str, AnalysisSpec] = {spec.id: spec for spec in SPECS}


def choose(request: str, original: str | None = None) -> AnalysisSpec | None:
    """The analysis a request asks for, or None.

    The user's own words are scored first and settle it whenever they say
    anything at all. Only a request that matches nothing the user actually typed
    falls back to the context-resolved rewrite, because that rewrite inherits
    vocabulary from the previous turn - the same reason `request_intent` refuses
    to let a restatement authorise a data selection.
    """
    for text in (original, request):
        if not text:
            continue
        scored = [(spec.score(text), -spec.precedence, spec) for spec in SPECS]
        best = max(scored, key=lambda item: (item[0], item[1]))
        if best[0] > 0:
            return best[2]
    return None


def resolve(request: str, fire_plan, original: str | None = None) -> AnalysisMatch | None:
    """Pick an analysis and compile it, or return None when none applies.

    A spec that recognises the request but cannot compile it - two dates needed,
    one available - returns None from its compiler, and that is a real answer:
    this analysis does not apply to this fire.
    """
    spec = choose(request, original)
    if spec is None:
        return None
    plan = spec.compile(request, fire_plan, original=original)
    if plan is None:
        return None
    return AnalysisMatch(spec=spec, plan=plan)


__all__ = ["ANALYSES", "SPECS", "AnalysisMatch", "AnalysisSpec", "choose", "resolve"]


def describe() -> list[dict[str, Any]]:
    """What this deployment can compute, for the taxonomy endpoint and for docs."""
    return [
        {"id": spec.id, "title": spec.title, "event": spec.event, **spec.notes} for spec in SPECS
    ]
