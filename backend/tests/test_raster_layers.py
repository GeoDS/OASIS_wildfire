"""Pure safety checks for the local raster preview pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest

from wildfire_agent import raster_layers
from wildfire_agent.contract import AnalysisContract, ScalarSlot, SpatialSlot
from wildfire_agent.raster_layers import (
    FireRasterPlan,
    RasterDataset,
    RasterLayerError,
    _intersection,
    active_fire_label_points,
    burned_area_label_points,
    fire_activity_footprint_layer,
    fire_event_catalog_payload,
    lifecycle_asset_path,
    plan_fire_raster,
    preview_path,
    render_fire_lifecycle,
)
from wildfire_agent.spatial_analysis import compile_raster_analysis, execute_raster_analysis


def _datasets() -> tuple[RasterDataset, ...]:
    common = {
        "event_id": "24461771",
        "event_name": "Bobcat Fire area",
        "spatial_scope": "Angeles NF",
        "time_start": "2020-09-04",
        "time_end": "2020-09-27",
        "dates": ["2020-09-04", "2020-09-27"],
    }
    return (
        RasterDataset(dataset_id="bobcat-observed", variable="VIIRS_Day", **common),
        RasterDataset(dataset_id="bobcat-predicted", variable="FirePred", **common),
    )


def _contract(request: str, *, intents: list[str] | None = None) -> AnalysisContract:
    return AnalysisContract(
        original_request=request,
        task_intent=intents or ["observation"],
        slots={"time_horizon": ScalarSlot(value="2020-09-27")},
    )


def test_fire_match_is_event_first_not_bbox_or_nearby_place():
    assert (
        plan_fire_raster(
            _contract("Which areas burned in the Eaton Fire near Altadena?"),
            _datasets(),
        )
        is None
    )


def test_fire_match_selects_observation_and_requested_date():
    plan = plan_fire_raster(
        _contract("Show the Bobcat Fire on 2020-09-04"),
        _datasets(),
    )
    assert plan is not None
    assert plan.dataset.dataset_id == "bobcat-observed"
    assert plan.day == "2020-09-04"
    assert plan.presentation == "observed fire activity and burned area"


def test_spread_question_does_not_present_prediction_inputs_as_model_output():
    plan = plan_fire_raster(
        _contract("Where could the Bobcat Fire spread next?", intents=["prediction"]),
        _datasets(),
    )
    assert plan is not None
    assert plan.dataset.dataset_id == "bobcat-observed"
    assert plan.presentation == "historical fire progression labels"
    assert "no model output" in plan.explanation


def test_location_named_event_requires_archive_qualification():
    santa_barbara = RasterDataset(
        dataset_id="santa-barbara-observed",
        event_id="20777203",
        event_name="Santa Barbara Co.",
        spatial_scope="Santa Barbara County | 34.42, -119.70",
        variable="VIIRS_Day",
        time_start="2017-07-07",
        time_end="2017-07-14",
        dates=["2017-07-14"],
    )
    assert (
        plan_fire_raster(_contract("Is there a fire in Santa Barbara?"), (santa_barbara,)) is None
    )
    assert (
        plan_fire_raster(
            _contract("Show the historical Santa Barbara fire dataset"), (santa_barbara,)
        )
        is not None
    )


def test_weather_request_does_not_match_same_named_fire_dataset():
    santa_barbara = RasterDataset(
        dataset_id="santa-barbara-observed",
        event_id="weather-collision",
        event_name="Santa Barbara Co. area",
        spatial_scope="Santa Barbara County | 34.42, -119.70",
        variable="VIIRS_Day",
        time_start="2020-01-01",
        time_end="2020-01-02",
        dates=["2020-01-02"],
    )
    assert (
        plan_fire_raster(
            _contract("show weather in santa babara city in California, not fire"),
            (santa_barbara,),
        )
        is None
    )


def test_city_fire_followup_does_not_treat_inherited_city_as_fire_name():
    santa_barbara = RasterDataset(
        dataset_id="santa-barbara-observed",
        event_id="county-archive",
        event_name="Santa Barbara Co. area",
        spatial_scope="Santa Barbara County | 34.42, -119.70",
        variable="VIIRS_Day",
        time_start="2017-07-01",
        time_end="2017-07-14",
        dates=["2017-07-14"],
    )
    contract = _contract("is there fire nearby")
    contract.slots["location"] = SpatialSlot(
        value="Santa Barbara, California",
        raw="nearby",
        source="agent_inferred",
    )

    assert plan_fire_raster(contract, (santa_barbara,)) is None


def test_contextual_fire_followup_inherits_event_id_and_selected_day():
    contract = _contract(
        "Which cities intersected or were closest to this fire's mapped burned area?"
    )
    contract.resolved_request = (
        "For Bobcat Fire (TS-SatFire event 24461771) on 2020-09-04, which cities "
        "intersected or were closest to its mapped burned area?"
    )
    plan = plan_fire_raster(
        contract,
        _datasets(),
    )
    assert plan is not None
    assert plan.dataset.event_name == "Bobcat Fire area"
    assert plan.day == "2020-09-04"


def test_contextual_next_day_advances_within_same_event():
    contract = _contract("What changed the next day?")
    contract.resolved_request = (
        "For Bobcat Fire (TS-SatFire event 24461771), show what changed on 2020-09-27."
    )
    plan = plan_fire_raster(
        contract,
        _datasets(),
    )
    assert plan is not None
    assert plan.dataset.event_id == "24461771"
    assert plan.day == "2020-09-27"


def test_generic_city_fire_question_does_not_inherit_old_archive():
    assert plan_fire_raster(_contract("Is there fire nearby?"), _datasets()) is None


def test_intersection_returns_only_shared_bounds():
    assert _intersection((-120.0, 33.0, -117.0, 36.0), (-119.0, 34.0, -116.0, 35.0)) == (
        -119.0,
        34.0,
        -117.0,
        35.0,
    )


def test_intersection_rejects_non_overlapping_request():
    with pytest.raises(RasterLayerError, match="does not overlap"):
        _intersection((-120.0, 33.0, -119.0, 34.0), (-118.0, 35.0, -117.0, 36.0))


def test_preview_path_rejects_path_traversal():
    with pytest.raises(RasterLayerError, match="Invalid raster preview token"):
        preview_path("../outside")


def test_preview_path_requires_an_existing_hash(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("wildfire_agent.raster_layers._PREVIEW_ROOT", tmp_path)
    with pytest.raises(RasterLayerError, match="not found"):
        preview_path("a" * 32)


def test_lifecycle_asset_path_rejects_path_traversal():
    with pytest.raises(RasterLayerError, match="Invalid lifecycle asset path"):
        lifecycle_asset_path("24461771", "../metadata.json")


def test_local_catalog_groups_the_subset_as_historical_fire_events():
    payload = fire_event_catalog_payload()
    assert payload["event_count"] == 9
    bobcat = next(event for event in payload["events"] if event["event_id"] == "24461771")
    assert bobcat["event_name"] == "Bobcat Fire area"
    assert bobcat["task_support"] == {
        "active_fire_detection": True,
        "burned_area_mapping": True,
        "next_day_prediction_inputs": True,
        "model_prediction_output": False,
    }


def test_bobcat_lifecycle_uses_af_ba_labels_not_prediction_inputs():
    lifecycle = render_fire_lifecycle("24461771", day="2020-09-18")
    assert lifecycle["selected_date"] == "2020-09-18"
    assert len(lifecycle["dates"]) == 24
    assert [layer["variable"] for layer in lifecycle["layers"]] == [
        "burned_area",
        "active_fire",
    ]
    assert lifecycle["selected_metrics"]["active_pixels"] > 0
    assert lifecycle["selected_metrics"]["cumulative_burned_pixels"] > 0
    assert "no trained-model output" in lifecycle["prediction_status"]
    burned_points = burned_area_label_points("24461771", "2020-09-18")
    active_points = active_fire_label_points("24461771", "2020-09-18")
    assert len(burned_points) == lifecycle["selected_metrics"]["cumulative_burned_pixels"]
    assert len(active_points) == lifecycle["selected_metrics"]["active_pixels"]
    west, south, east, north = lifecycle["bounds"]
    assert all(west <= lon <= east and south <= lat <= north for lon, lat in burned_points)
    assert all(west <= lon <= east and south <= lat <= north for lon, lat in active_points)


def test_local_viirs_pixels_produce_object_footprint():
    plan = plan_fire_raster(_contract("Show the Bobcat Fire on 2020-09-27"))
    assert isinstance(plan, FireRasterPlan)
    layer = fire_activity_footprint_layer(plan)
    assert layer is not None
    assert layer.capability_id == "subject_fire_observed_footprint"
    feature = layer.geojson["features"][0]
    assert feature["geometry"]["type"] == "Polygon"
    ring = feature["geometry"]["coordinates"][0]
    assert max(point[0] for point in ring) - min(point[0] for point in ring) < 0.5
    assert feature["properties"]["observationPixels"] < 112
    assert "not an official perimeter" in layer.caveat


def test_ndvi_change_compiles_to_allow_listed_plan_and_real_local_result():
    contract = _contract(
        "Compare NDVI inside the Bobcat Fire's mapped burned area from the first to the last local record."
    )
    fire_plan = plan_fire_raster(contract)
    assert fire_plan is not None

    analysis_plan = compile_raster_analysis(
        contract.analysis_request(), fire_plan, operation="ndvi_change"
    )
    assert analysis_plan is not None
    # NDVI is read from same-day VIIRS reflectance, not the carried FirePred value,
    # so the reply no longer has to disclaim a value observed on another day.
    assert analysis_plan.formula == "NDVI(2020-09-27) - NDVI(2020-09-04)"
    assert analysis_plan.index == "ndvi_viirs"
    assert {item.dataset for item in analysis_plan.inputs} == {"VIIRS_Day"}
    assert [operation.type for operation in analysis_plan.operations] == [
        "select",
        "align_grids",
        "difference",
        "mask",
        "zonal_statistics",
    ]

    result = execute_raster_analysis(analysis_plan)
    assert result["default_view"] == "difference"
    assert [layer["analysis_view"] for layer in result["layers"]] == [
        "before",
        "after",
        "difference",
    ]
    assert result["statistics"]["valid_pixels"] > 0
    assert result["statistics"]["mean_delta"] < 0
    assert result["statistics"]["negative_percent"] > 50
    assert "not proof" in result["summary"]
    assert any("same-day VIIRS" in caveat for caveat in result["caveats"])
    assert not any("NDVI_last" in caveat for caveat in result["caveats"])


def test_plan_day_stays_on_the_event_timeline_when_land_cover_is_preferred():
    """A restatement mentioning vegetation must not yield a land-cover date.

    `ESRI_LULC` carries a single annual date, but the API always renders the
    lifecycle from the daily `VIIRS_Day` record. Taking the day from whichever
    variable the wording preferred put an annual date on a daily timeline.
    """
    contract = AnalysisContract(
        original_request="Why does a lower NDVI mean the area burned? Explain it simply.",
        resolved_request=(
            "Explain simply why a lower NDVI indicates burned area in the Bobcat Fire "
            "on 2020-09-27."
        ),
        restatement=(
            "Explain why NDVI was lower in the Bobcat Fire mapped burned area on "
            "2020-09-27, and what that suggests about damaged or reduced vegetation."
        ),
        task_intent=["assessment"],
    )
    plan = plan_fire_raster(contract)
    assert plan is not None
    assert plan.dataset.variable == "ESRI_LULC"

    # The chosen day must address the daily event timeline, not the land-cover epoch.
    assert plan.day == "2020-09-27"
    render_fire_lifecycle(plan.dataset.event_id or "", day=plan.day)


def test_burn_severity_compiles_to_dnbr_and_classifies_against_usgs_breaks():
    """dNBR is the second operation through the same allow-listed shape.

    NBR uses the near-infrared and shortwave-infrared bands that were already
    sitting unread in every VIIRS_Day file, so severity needs no new data - only
    a new row in the index table.
    """
    contract = _contract("How severe was the burn inside the Bobcat Fire?")
    fire_plan = plan_fire_raster(contract)
    assert fire_plan is not None

    plan = compile_raster_analysis(contract.analysis_request(), fire_plan, operation="nbr_change")
    assert plan is not None
    assert plan.operation == "nbr_change"
    assert plan.index == "nbr_viirs"
    # dNBR is prefire minus postfire, so a positive value means a larger drop.
    assert plan.formula == "NBR(2020-09-04) - NBR(2020-09-27)"

    result = execute_raster_analysis(plan)
    assert result["statistics"]["mean_delta"] > 0
    assert result["statistics"]["valid_pixels"] > 0

    breakdown = result["class_breakdown"]
    assert breakdown, "severity classification produced no classes"
    assert abs(sum(item["percent"] for item in breakdown) - 100) < 1.0
    labels = " ".join(item["label"] for item in breakdown)
    assert "severity" in labels.lower()
    assert any("USGS" in caveat for caveat in result["caveats"])


def test_ndvi_and_severity_are_cached_under_different_ids():
    """Two operations on one fire must not collide in the preview cache."""
    contract = _contract("Compare NDVI and burn severity for the Bobcat Fire")
    fire_plan = plan_fire_raster(contract)
    ndvi = compile_raster_analysis("Compare NDVI change", fire_plan, operation="ndvi_change")
    severity = compile_raster_analysis(
        "How severe was the burn?", fire_plan, operation="nbr_change"
    )
    assert ndvi is not None and severity is not None
    assert ndvi.analysis_id != severity.analysis_id


# --- composition, spread and fire weather -----------------------------------


def test_land_cover_composition_reads_the_aligned_firepred_band():
    from wildfire_agent.fire_context import compile_fire_context, execute_fire_context

    contract = _contract("What kind of land did the Bobcat Fire burn?")
    plan = compile_fire_context(
        contract.analysis_request(),
        plan_fire_raster(contract),
        analysis="land_cover_composition",
    )
    assert plan is not None and plan.analysis == "land_cover_composition"

    result = execute_fire_context(plan)
    assert result["composition"], "no land-cover classes were resolved"
    assert abs(sum(item["percent"] for item in result["composition"]) - 100) < 1.0
    # LC_Type1 travels in the daily FirePred stack, already on the analysis grid,
    # so no reprojection of the very large standalone land-cover tile is needed.
    assert "FirePred band 14" in (result["land_cover_source"] or "")
    assert result["terrain"]["elevation_m"]["mean"] > 0
    assert 0 <= result["terrain"]["aspect_deg"]["mean"] <= 360


def test_spread_analysis_reports_a_peak_day_and_states_wind_alignment_honestly():
    from wildfire_agent.fire_context import compile_fire_context, execute_fire_context

    contract = _contract("Which way did the Bobcat Fire spread?")
    plan = compile_fire_context(
        contract.analysis_request(), plan_fire_raster(contract), analysis="spread_behaviour"
    )
    assert plan is not None and plan.analysis == "spread_behaviour"

    result = execute_fire_context(plan)
    peak = result["peak_growth_day"]
    assert peak["new_area_km2"] > 0
    assert peak["spread_compass"]

    alignment = result["wind_alignment"]
    assert 0 <= alignment["area_weighted_offset_deg"] <= 180
    assert 0 <= alignment["area_share_within_45_deg"] <= 100
    # Alignment is an association, and the reply has to keep saying so.
    assert any("association" in caveat for caveat in result["caveats"])
    assert any("meteorological convention" in caveat for caveat in result["caveats"])


def test_fire_weather_excludes_only_the_mismatched_forecast_bands():
    from wildfire_agent.fire_context import compile_fire_context, execute_fire_context

    contract = _contract("What were the fire weather conditions during the Bobcat Fire?")
    plan = compile_fire_context(
        contract.analysis_request(), plan_fire_raster(contract), analysis="fire_weather"
    )
    result = execute_fire_context(plan)

    sampled = {key for entry in result["timeline"] for key in entry.get("conditions", {})}
    assert "energy_release_component" in sampled
    assert "wind_speed" in sampled
    # Precipitation reads zero for this fire - a dry September, not a dead band -
    # so it is sampled and simply reports zero. Excluding it on the strength of
    # one event would have hidden real rainfall on the other eight.
    assert "precipitation_mm" in sampled
    # The forecast bands are on a different scale from their observed
    # counterparts, so they may never be reported alongside them.
    assert not {key for key in sampled if "forecast" in key}


def test_severity_results_describe_themselves_as_severity_not_as_ndvi():
    """One event carries every index-change result, so the payload has to say
    which one it is. A panel that reads `title` or `caveats` must not be told
    "NDVI change" over a dNBR raster."""
    contract = _contract("How severe was the burn inside the Bobcat Fire?")
    plan = compile_raster_analysis(
        contract.analysis_request(), plan_fire_raster(contract), operation="nbr_change"
    )
    result = execute_raster_analysis(plan)

    assert "NDVI" not in result["title"]
    assert "severity" in result["title"].lower()
    assert not any("NDVI_last" in caveat for caveat in result["caveats"])
    # The share a severity panel shows is the damaged one, not "pixels lower":
    # a lower dNBR means less damage, so the NDVI framing inverts the meaning.
    assert result["statistics"]["damaged_percent"] > 50


class TestADateRangePicksTheEndNotTheStart:
    """`_requested_day` searched for one date and took the first it found.

    A contract that carries a span - "the 2020 incident, 2020-09-04 to
    2020-09-27" - therefore selected the *first* day of the fire. Burned area is
    cumulative, so day one has none of it, and "which cities were affected" came
    back "the required mapped fire area was unavailable" for a fire that reached
    three cities.
    """

    AVAILABLE: ClassVar[list[str]] = [f"2020-09-{d:02d}" for d in range(4, 28)]

    def _contract(self, text: str, time_horizon: str | None = None) -> AnalysisContract:
        slots = {}
        if time_horizon:
            slots["time_horizon"] = ScalarSlot(value=time_horizon)
        return AnalysisContract(original_request=text, resolved_request=text, slots=slots)

    def test_a_span_selects_its_end(self):
        contract = self._contract(
            "what cities are affected", time_horizon="the 2020 incident, 2020-09-04 to 2020-09-27"
        )
        assert raster_layers._requested_day(contract, self.AVAILABLE) == "2020-09-27"

    def test_a_single_date_is_still_that_date(self):
        contract = self._contract("what burned on 2020-09-12")
        assert raster_layers._requested_day(contract, self.AVAILABLE) == "2020-09-12"

    def test_a_date_the_archive_lacks_does_not_veto_one_it_has(self):
        """The membership check ran on the first match only, so a date outside
        the archive discarded a usable one later in the same sentence."""
        contract = self._contract("compare 2019-07-01 with 2020-09-12")
        assert raster_layers._requested_day(contract, self.AVAILABLE) == "2020-09-12"

    def test_no_date_defers_to_the_caller(self):
        contract = self._contract("what cities are affected")
        assert raster_layers._requested_day(contract, self.AVAILABLE) is None

    def test_the_end_of_a_span_is_what_the_plan_uses(self):
        contract = self._contract(
            "For Bobcat Fire: what cities are affected",
            time_horizon="active period, 2020-09-04 to 2020-09-27",
        )
        plan = plan_fire_raster(contract)
        assert plan is not None
        assert plan.day == "2020-09-27"

    def test_a_date_in_the_question_outranks_the_time_slot(self):
        """The user's own wording settles it, as it does for dataset selection."""
        contract = self._contract("show the Bobcat Fire on 2020-09-04", time_horizon="2020-09-27")
        assert raster_layers._requested_day(contract, self.AVAILABLE) == "2020-09-04"

    def test_the_slot_is_used_when_the_question_names_no_date(self):
        contract = self._contract("what cities are affected", time_horizon="2020-09-12")
        assert raster_layers._requested_day(contract, self.AVAILABLE) == "2020-09-12"


class TestTheCatalogueDoesNotAdvertiseWhatItLacks:
    """`burned_area_mapping` was set true whenever a VIIRS_Day series existed,
    without ever checking band 8 for content.

    Two events in the local subset - Thomas Fire and Santa Barbara Co. - carry
    thousands of active-fire pixels and zero burned-area labels on every single
    day. The catalogue claimed burned-area mapping for both, and a question
    about which cities they reached came back "the required mapped fire area was
    unavailable", which reads as a transient failure rather than as a gap that
    is permanent for that event.
    """

    def test_an_event_with_labels_supports_mapping(self, monkeypatch):
        monkeypatch.setattr(raster_layers, "burned_area_label_points", lambda e, d: ((1.0, 2.0),))
        raster_layers.event_supports_burned_area.cache_clear()
        assert raster_layers.event_supports_burned_area("bobcat", ("2020-09-27",)) is True

    def test_an_event_with_none_does_not(self, monkeypatch):
        monkeypatch.setattr(raster_layers, "burned_area_label_points", lambda e, d: ())
        raster_layers.event_supports_burned_area.cache_clear()
        assert raster_layers.event_supports_burned_area("thomas", ("2017-12-13",)) is False

    def test_only_the_last_day_is_read(self, monkeypatch):
        """Burned area is cumulative, so the final day is the maximum. Reading
        every day would cost a full raster scan per event per catalogue call."""
        seen = []

        def spy(event_id, day):
            seen.append(day)
            return ((1.0, 2.0),)

        monkeypatch.setattr(raster_layers, "burned_area_label_points", spy)
        raster_layers.event_supports_burned_area.cache_clear()
        raster_layers.event_supports_burned_area("e", ("2020-09-04", "2020-09-05", "2020-09-27"))
        assert seen == ["2020-09-27"]

    def test_an_unreadable_event_is_not_claimed(self, monkeypatch):
        """A raster that will not open is not evidence that labels exist."""

        def boom(event_id, day):
            raise raster_layers.RasterLayerError("no band 8")

        monkeypatch.setattr(raster_layers, "burned_area_label_points", boom)
        raster_layers.event_supports_burned_area.cache_clear()
        assert raster_layers.event_supports_burned_area("e", ("2020-09-27",)) is False

    def test_an_event_with_no_dates_is_not_claimed(self):
        raster_layers.event_supports_burned_area.cache_clear()
        assert raster_layers.event_supports_burned_area("e", ()) is False


class TestAPlaceNamedEventCanStillBeNamed:
    """The guard asked whether the *dataset's* name contains "fire", so four of
    the nine archived events - Lake Hughes, Mojave / I-15, San Bernardino,
    Santa Barbara Co. - could not be reached by naming them. Even "show the San
    Bernardino fire" was dropped; only adding a year rescued it.

    What the guard is actually for is telling "the San Bernardino fire" - a
    historical event - from "is there a fire near San Bernardino" - a question
    about now. That distinction is in the user's words, not in the catalogue's.
    """

    def _datasets(self) -> tuple[RasterDataset, ...]:
        """A place-named event, exactly like the four in the local subset."""
        common = {
            "event_id": "24332783", "event_name": "San Bernardino",
            "spatial_scope": "San Bernardino NF", "time_start": "2020-07-31",
            "time_end": "2020-08-11", "dates": ["2020-07-31", "2020-08-11"],
        }
        return (
            RasterDataset(dataset_id="sb-observed", variable="VIIRS_Day", **common),
            *_datasets(),
        )

    def _contract(self, text: str) -> AnalysisContract:
        return AnalysisContract(original_request=text, resolved_request=text)

    def test_naming_the_event_reaches_it(self):
        plan = plan_fire_raster(self._contract("show the San Bernardino fire"), self._datasets())
        assert plan is not None and plan.dataset.event_name == "San Bernardino"

    def test_a_current_question_about_the_place_does_not(self):
        """"a fire near San Bernardino" is about now. Handing back a 2020
        archive would answer a different question in a confident voice."""
        plan = plan_fire_raster(
            self._contract("is there a fire near San Bernardino right now"), self._datasets()
        )
        assert plan is None

    def test_the_year_route_still_works(self):
        plan = plan_fire_raster(
            self._contract("which areas burned in the San Bernardino in 2020"), self._datasets()
        )
        assert plan is not None and plan.dataset.event_name == "San Bernardino"

    def test_a_fire_named_event_is_unaffected(self):
        plan = plan_fire_raster(self._contract("show the Bobcat fire"), self._datasets())
        assert plan is not None and "Bobcat" in plan.dataset.event_name

    def test_the_publishers_own_punctuation_does_not_block_it(self):
        """"the Santa Barbara Co. fire" names an incident just as plainly."""
        plan = plan_fire_raster(
            self._contract("tell me about the San Bernardino County fire"), self._datasets()
        )
        assert plan is not None and plan.dataset.event_name == "San Bernardino"

    def test_a_weather_question_never_reaches_the_archive(self):
        assert plan_fire_raster(self._contract("what's the weather in San Bernardino")) is None
