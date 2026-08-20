"""Pure safety checks for the local raster preview pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest

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
