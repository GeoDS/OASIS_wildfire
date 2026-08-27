"""The registry: one place that answers "which analysis is this request?"."""

from __future__ import annotations

import pytest

from wildfire_agent.analyses import ANALYSES, SPECS, choose, describe, resolve
from wildfire_agent.contract import AnalysisContract, ScalarSlot
from wildfire_agent.events import EVENT_NAMES
from wildfire_agent.raster_layers import plan_fire_raster


def _fire_plan(request: str):
    return plan_fire_raster(
        AnalysisContract(
            original_request=request,
            task_intent=["observation"],
            slots={"time_horizon": ScalarSlot(value="2020-09-27")},
        )
    )


@pytest.mark.parametrize(
    ("request_text", "expected"),
    [
        ("How severe was the burn inside the Bobcat Fire?", "nbr_change"),
        ("What was the dNBR?", "nbr_change"),
        ("Compare NDVI from the first to the last record", "ndvi_change"),
        ("Which way did the Bobcat Fire spread?", "spread_behaviour"),
        ("How fast did it move?", "spread_behaviour"),
        ("What were the fire weather conditions?", "fire_weather"),
        ("What kind of land did it burn?", "land_cover_composition"),
        ("What is the terrain like?", "land_cover_composition"),
    ],
)
def test_each_analysis_is_recognised_from_a_plain_question(request_text, expected):
    spec = choose(request_text)
    assert spec is not None and spec.id == expected


@pytest.mark.parametrize(
    "request_text",
    [
        "Show the lifecycle of the Bobcat Fire on 2020-09-18",
        "Which cities were closest to this fire?",
        "What is NDVI?",
    ],
)
def test_requests_that_ask_for_no_derived_analysis_match_nothing(request_text):
    """Naming a subject is not requesting an analysis of it.

    "What is NDVI?" is a question for the discussion route; routing it here
    would spend twenty seconds computing a raster to answer a definition.
    """
    assert choose(request_text) is None


def test_the_users_own_words_outrank_the_resolved_rewrite():
    """A rewrite inherits the previous turn's vocabulary and must not decide.

    Resolved after a spread question, a land-cover request comes back carrying
    the word "spread"; answering from the spread analysis would return the wrong
    result in a confident voice.
    """
    resolved = "For the Bobcat Fire: characterize the land cover burned in the area that spread"
    assert choose(resolved).id == "spread_behaviour"
    assert choose(resolved, "What kind of land did the Bobcat Fire burn?").id == (
        "land_cover_composition"
    )


def test_scoring_beats_declaration_order_when_a_subject_dominates():
    """Both patterns hit; the one with more distinct matches should win."""
    spec = choose("What land cover, forest or shrub, burned on this terrain?")
    assert spec.id == "land_cover_composition"


def test_resolve_returns_a_runnable_match():
    request = "How severe was the burn inside the Bobcat Fire?"
    match = resolve(request, _fire_plan(request))
    assert match is not None
    assert match.spec.id == "nbr_change"
    assert match.plan.operation == "nbr_change"


def test_a_recognised_analysis_that_cannot_compile_declines_rather_than_guesses():
    """Recognising a request is not the same as being able to answer it."""

    class _NoDates:
        class dataset:
            event_id = None
            event_name = "Nowhere Fire"

    assert resolve("How severe was the burn?", _NoDates()) is None


def test_every_spec_is_uniquely_identified_and_wired():
    assert len(ANALYSES) == len(SPECS), "two analyses share an id"
    for spec in SPECS:
        assert spec.event in EVENT_NAMES, f"{spec.id} sends an undeclared event"
        assert spec.patterns, f"{spec.id} can never be recognised"
        assert spec.notes.get("family"), f"{spec.id} declares no family"


def test_describe_lists_what_this_deployment_can_compute():
    listed = {entry["id"] for entry in describe()}
    assert listed == set(ANALYSES)
