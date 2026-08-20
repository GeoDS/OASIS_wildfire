"""The narration layer may re-word computed facts; it may never add to them."""

from __future__ import annotations

import pytest

from wildfire_agent.narration import (
    NarrationRejected,
    _figures,
    verify_grounded,
    verify_layer_claims,
)

FACTS = {
    "event": "Bobcat Fire",
    "day": "2020-09-18",
    "active_pixels": 1116,
    "cumulative_burned_km2": 276.9,
    "places": ["Monrovia", "Duarte"],
}


def test_rewording_the_same_figures_is_accepted():
    text = (
        "On 2020-09-18 the Bobcat Fire record holds 1,116 active-fire label pixels, "
        "and the cumulative mapped burned area reaches about 276.9 km²."
    )
    assert verify_grounded(text, FACTS) == text


def test_a_figure_absent_from_the_facts_is_rejected():
    text = "The Bobcat Fire burned 276.9 km² and destroyed 1,400 structures."
    with pytest.raises(NarrationRejected):
        verify_grounded(text, FACTS)


def test_a_place_absent_from_the_facts_is_rejected():
    text = "The Bobcat Fire on 2020-09-18 reached Monrovia, Duarte and Pasadena."
    with pytest.raises(NarrationRejected):
        verify_grounded(text, FACTS)


def test_thousands_separators_and_rounding_do_not_count_as_new_figures():
    text = "It contains 1116 active pixels across roughly 276.9 km²."
    assert verify_grounded(text, FACTS) == text


def test_figures_ignores_ordinary_prose_without_numbers():
    assert _figures("No perimeter overlaps the city boundary.") == set()


def test_percentages_present_in_facts_are_allowed():
    facts = {"pixels_lower": "88%"}
    assert verify_grounded("88% of valid pixels were lower.", facts)


# --- what a discussion turn may claim is on screen ---------------------------

DISPLAYED = {
    "active_layer_ids": ["subject_fire_observed_footprint", "fire_lifecycle_burned_area"],
    "last_result": {"message": "Bobcat Fire: TS-SatFire historical record for 2020-09-18."},
}


def test_claiming_a_displayed_layer_is_accepted():
    verify_layer_claims(["burned area", "TS-SatFire"], DISPLAYED)


def test_claiming_a_layer_that_is_not_displayed_is_rejected():
    with pytest.raises(NarrationRejected):
        verify_layer_claims(["NDVI"], DISPLAYED)


def test_claiming_nothing_is_always_accepted():
    verify_layer_claims([], DISPLAYED)


def test_a_snake_case_capability_id_matches_its_prose_name():
    verify_layer_claims(["observed footprint"], DISPLAYED)


def test_one_bad_claim_rejects_the_whole_draft():
    with pytest.raises(NarrationRejected) as excinfo:
        verify_layer_claims(["burned area", "FirePred"], DISPLAYED)
    assert "FirePred" in str(excinfo.value)
