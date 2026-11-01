"""Naming an archived fire reaches it, whatever its name looks like.

QA, 2026-10-20. Four of the nine archived events are named for a place - Lake
Hughes, Mojave / I-15, San Bernardino, Santa Barbara Co. - and three separate
gaps kept them from being recognised:

- The location step's gazetteer still required "fire" in the event's name, the
  guard `plan_fire_raster` dropped long ago, so "the Lake Hughes fire" went to
  the web geocoder and came back as a fire station in Lake Hughes.
- "Mojave / I-15 fire" never paired its name with "fire": the pairing was a
  regex with literal spaces, and the slash and hyphen broke it, so the question
  was answered as current conditions in the town of Mojave.
- A named archived fire with no period stated was asked "which time period?",
  though the archive holds exactly one record per event.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from wildfire_agent.contract import AnalysisContract, ScalarSlot
from wildfire_agent.geocoding import (
    _local_fire_candidates,
    _local_fire_gazetteer,
    local_fire_period,
)
from wildfire_agent.graph.nodes import _settle_archived_fire_time
from wildfire_agent.raster_layers import RasterDataset, plan_fire_raster
from wildfire_agent.request_intent import names_fire_incident


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("Describe the lifecycle of the Mojave / I-15 fire", "mojave i 15"),
        ("What is the life cycle of the Mojave I-15 Fire?", "mojave i 15"),
        ("What kind of land did the Lake Hughes fire burn?", "lake hughes"),
        ("show the Santa Barbara Co. fire", "santa barbara"),
        ("the San Bernardino County fire", "san bernardino"),
    ],
)
def test_naming_an_event_as_a_fire_is_read_on_words_alone(text, key):
    assert names_fire_incident(text, key)


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("is there a fire near San Bernardino right now", "san bernardino"),
        ("Show me fires near Lake Hughes", "lake hughes"),
        ("What is the weather in Mojave?", "mojave i 15"),
        ("anything", ""),
    ],
)
def test_a_place_mentioned_beside_a_fire_is_not_a_named_fire(text, key):
    assert not names_fire_incident(text, key)


def _mojave_datasets() -> tuple[RasterDataset, ...]:
    return (
        RasterDataset(
            dataset_id="mojave-observed",
            variable="VIIRS_Day",
            event_id="24332783",
            event_name="Mojave / I-15",
            spatial_scope="Mojave / I-15",
            time_start="2020-08-15",
            time_end="2020-08-19",
            dates=["2020-08-15", "2020-08-19"],
        ),
    )


def test_the_slash_and_hyphen_in_mojave_i_15_no_longer_hide_it():
    text = "Describe the lifecycle of the Mojave / I-15 fire"
    contract = AnalysisContract(original_request=text, resolved_request=text)
    plan = plan_fire_raster(contract, _mojave_datasets())
    assert plan is not None and plan.dataset.event_name == "Mojave / I-15"


def _dataset(event_name: str, scope: str, variables: list[str], start: str, end: str):
    return SimpleNamespace(
        event_name=event_name,
        spatial_scope=scope,
        variables=variables,
        time_start=start,
        time_end=end,
    )


@pytest.fixture
def archive(monkeypatch):
    """A catalogue shaped like the real one: fire-named and place-named events,
    each with an annual land-cover epoch beside its daily record."""
    datasets = [
        _dataset("Lake Hughes area", "Lake Hughes | 34.68, -118.56", ["ESRI_LULC"], "2020-01-01", "2020-01-01"),
        _dataset("Lake Hughes area", "Lake Hughes | 34.68, -118.56", ["VIIRS_Day"], "2020-08-11", "2020-08-23"),
        _dataset("Mojave / I-15", "Mojave / I-15 | 35.29, -115.59", ["VIIRS_Day"], "2020-08-15", "2020-08-19"),
        _dataset("Blue Ridge Fire", "Yorba Linda | 33.88, -117.68", ["VIIRS_Day"], "2020-10-26", "2020-11-04"),
        _dataset("Bobcat Fire area", "Angeles NF | 34.33, -117.93", ["VIIRS_Day"], "2020-09-04", "2020-09-27"),
    ]
    monkeypatch.setattr(
        "wildfire_agent.geocoding.scan_local_data",
        lambda _root: SimpleNamespace(datasets=datasets),
    )
    _local_fire_gazetteer.cache_clear()
    yield
    _local_fire_gazetteer.cache_clear()


def _names(query: str) -> list[str]:
    return [candidate.display_name for candidate in _local_fire_candidates(query)]


def test_a_place_named_event_resolves_from_the_catalogue(archive):
    assert _names("What kind of land did the Lake Hughes fire burn?") == ["Lake Hughes"]
    # What the model put in the location slot, which the geocoder is handed.
    assert _names("Lake Hughes fire") == ["Lake Hughes"]
    assert _names("Mojave / I-15 fire") == ["Mojave / I-15"]


def test_a_current_question_about_the_place_still_goes_to_the_geocoder(archive):
    """"Fires near Lake Hughes" is about the place now, not the 2020 record."""
    assert _names("Show me fires near Lake Hughes") == []
    assert _names("Lake Hughes") == []


def test_a_fire_named_event_resolves_as_before(archive):
    assert _names("Show me the Bobcat Fire") == ["Bobcat Fire"]
    assert _names("Eaton Fire near Altadena") == []


def test_the_period_is_the_daily_record_not_the_land_cover_epoch(archive):
    assert local_fire_period("Which way did the Blue Ridge Fire spread?") == (
        "2020-10-26",
        "2020-11-04",
    )
    assert local_fire_period("What kind of land did the Lake Hughes fire burn?") == (
        "2020-08-11",
        "2020-08-23",
    )
    assert local_fire_period("What happened in the Eaton Fire?") is None


def _contract(text: str, **slot) -> AnalysisContract:
    defaults = {"value": None, "source": "agent_inferred", "is_blocking": True}
    return AnalysisContract(
        original_request=text,
        slots={"time_horizon": ScalarSlot(**{**defaults, **slot})},
    )


def test_a_named_archived_fire_is_not_asked_for_a_period(archive):
    contract = _contract("Which way did the Blue Ridge Fire spread?")
    assert contract.pending_slots == ["time_horizon"]

    _settle_archived_fire_time(contract)

    assert contract.pending_slots == []
    assert contract.slots["time_horizon"].value == "2020-10-26 to 2020-11-04"
    # Filled on the user's behalf, so it has to say so.
    assert any("2020-10-26 to 2020-11-04" in note for note in contract.assumptions)


def test_a_period_the_user_gave_is_left_alone(archive):
    contract = _contract(
        "Which way did the Blue Ridge Fire spread on 2020-10-27?",
        value="2020-10-27",
        source="user_stated",
    )
    _settle_archived_fire_time(contract)
    assert contract.slots["time_horizon"].value == "2020-10-27"
    assert contract.assumptions == []


def test_a_fire_outside_the_archive_is_still_asked(archive):
    contract = _contract("Which way did the Eaton Fire spread?")
    _settle_archived_fire_time(contract)
    assert contract.pending_slots == ["time_horizon"]
