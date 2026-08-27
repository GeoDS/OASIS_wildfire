"""Planning Agent: registry match, proposal validation, and execution.

The theme throughout is that the model proposes and the registry decides. A
proposal may not invent a source, may not overrule the data family the user was
asked to choose, and may not quietly shrink the answer by forgetting a declared
hazard object.
"""

from __future__ import annotations

import pytest

from wildfire_agent.contract import (
    AnalysisContract,
    ResolvedLocation,
    ScalarSlot,
    SpatialSlot,
)
from wildfire_agent.planning import execute, executor, summarise
from wildfire_agent.planning.capabilities import CAPABILITIES, SHOWCASE_AREA
from wildfire_agent.planning.models import LayerResult
from wildfire_agent.planning.planner import (
    LayerChoice,
    PlanProposal,
    deterministic_plan,
    selected_families,
    validate_proposal,
    wants_history,
)

ALTADENA_BBOX = (-118.28, 34.12, -117.96, 34.32)


def _contract(
    *,
    target: str | None = "official_fire_perimeters + satellite_hotspots",
    hazards: list[str] | None = None,
    request: str = "Where are the active fires near Altadena?",
    time_horizon: str = "now",
    bbox: tuple[float, float, float, float] | None = ALTADENA_BBOX,
) -> AnalysisContract:
    resolved = (
        ResolvedLocation(center=(-118.1312, 34.1897), buffer_km=15, bbox=bbox) if bbox else None
    )
    return AnalysisContract(
        original_request=request,
        expertise="general",
        task_intent=["observation"],
        hazard_objects=hazards if hazards is not None else ["active_fire"],
        slots={
            "location": SpatialSlot(value="Altadena, CA", is_blocking=True, resolved=resolved),
            "target": ScalarSlot(value=target, source="user_stated" if target else "default"),
            "time_horizon": ScalarSlot(value=time_horizon, source="default"),
        },
    )


class TestFamilySelection:
    def test_reads_both_families_from_the_target_slot(self):
        assert selected_families(_contract()) == {
            "official_fire_perimeters",
            "satellite_hotspots",
        }

    def test_reads_a_single_family(self):
        assert selected_families(_contract(target="satellite_hotspots")) == {"satellite_hotspots"}

    def test_no_target_means_no_constraint(self):
        assert selected_families(_contract(target=None)) == set()


class TestHistoryDetection:
    def test_detects_history_from_the_request(self):
        assert wants_history(_contract(request="What fires have burned here since 2000?"))

    def test_present_tense_is_not_history(self):
        assert not wants_history(_contract())


class TestDeterministicPlan:
    def test_both_families_selected_when_the_contract_names_both(self):
        ids = [layer.capability_id for layer in deterministic_plan(_contract()).layers]
        assert ids == ["official_fire_perimeters", "satellite_hotspots"]

    def test_one_family_selected_when_the_contract_names_one(self):
        plan = deterministic_plan(_contract(target="satellite thermal detections"))
        assert [layer.capability_id for layer in plan.layers] == ["satellite_hotspots"]

    def test_history_request_selects_the_historical_layer_only(self):
        plan = deterministic_plan(
            _contract(
                request="Show past fires around Altadena since 2000", time_horizon="2000-2024"
            )
        )
        assert [layer.capability_id for layer in plan.layers] == ["historical_fire_perimeters"]


class TestProposalValidation:
    def test_invented_capability_is_dropped(self):
        proposal = PlanProposal(
            layers=[
                LayerChoice(capability_id="nasa_firms_live", reason="made up"),
                LayerChoice(capability_id="official_fire_perimeters", reason="real"),
            ]
        )
        plan = validate_proposal(_contract(target="official_fire_perimeters"), proposal)
        assert [layer.capability_id for layer in plan.layers] == ["official_fire_perimeters"]

    def test_proposal_may_not_override_the_users_family_choice(self):
        """The user was interrupted specifically to make this choice. A planner
        that quietly picks the other family throws that away."""
        contract = _contract(target="official_fire_perimeters")
        proposal = PlanProposal(
            layers=[LayerChoice(capability_id="satellite_hotspots", reason="timelier")]
        )
        plan = validate_proposal(contract, proposal)

        assert [layer.capability_id for layer in plan.layers] == ["official_fire_perimeters"]
        assert any("not the planner's to override" in n for n in plan.notes)

    def test_forgotten_hazard_object_is_backfilled(self):
        contract = _contract()
        proposal = PlanProposal(
            layers=[LayerChoice(capability_id="official_fire_perimeters", reason="partial")]
        )
        plan = validate_proposal(contract, proposal)
        assert "satellite_hotspots" in {layer.capability_id for layer in plan.layers}

    def test_uncovered_hazard_object_is_reported_not_substituted(self):
        """Walkthrough scenario 3: the contract is complete, the capability is
        missing, and the honest answer is to say so."""
        contract = _contract(hazards=["active_fire", "fire_spread", "fuel"])
        proposal = PlanProposal(
            layers=[LayerChoice(capability_id="official_fire_perimeters", reason="ok")]
        )
        plan = validate_proposal(contract, proposal)

        # Two kinds of gap, and they are not the same statement. `fire_spread`
        # and `fuel` have no capability at all; `active_fire` has one, but the
        # single chosen perimeter layer carries only the perimeter itself.
        by_object = {u.hazard_object: u for u in plan.unmet}
        assert set(by_object) == {"active_fire", "fire_spread", "fuel"}

        wholesale = by_object["fire_spread"]
        assert wholesale.missing_variables == ()
        assert "No data source in this deployment covers" in wholesale.reason

        partial = by_object["active_fire"]
        assert "detection confidence" in partial.missing_variables
        assert "fire perimeter" not in partial.missing_variables
        assert "do not carry" in partial.reason
        # Whichever kind, every gap says what is absent rather than substituting.
        assert all(u.reason for u in plan.unmet)
        assert wholesale.fillable_externally is True

    def test_reading_note_reaches_the_notes(self):
        proposal = PlanProposal(
            layers=[LayerChoice(capability_id="official_fire_perimeters", reason="ok")],
            reading_note="Read the perimeter as of 21 January.",
        )
        plan = validate_proposal(_contract(target="official_fire_perimeters"), proposal)
        assert plan.notes[0] == "Read the perimeter as of 21 January."

    def test_snapshot_caveat_is_always_attached(self):
        plan = validate_proposal(
            _contract(),
            PlanProposal(layers=[LayerChoice(capability_id="satellite_hotspots", reason="ok")]),
        )
        assert any("snapshot" in n.lower() for n in plan.notes)


class TestExecution:
    def test_layers_load_and_clip_to_the_contract_scope(self):
        contract = _contract()
        results = execute(deterministic_plan(contract), contract)

        assert {r.capability_id for r in results} == {
            "official_fire_perimeters",
            "satellite_hotspots",
        }
        for r in results:
            assert r.feature_count > 0
            assert r.geojson["type"] == "FeatureCollection"
            assert len(r.geojson["features"]) == r.feature_count
            # Provenance has to survive all the way to the browser.
            assert r.source and r.source != "unknown"
            assert r.caveat

    def test_a_scope_far_away_yields_an_empty_layer_not_an_error(self):
        """Empty is a legal result. It must never be padded with anything."""
        contract = _contract(bbox=(-70.1, 41.0, -69.9, 41.2))  # Nantucket
        results = execute(deterministic_plan(contract), contract)

        assert results
        assert all(r.feature_count == 0 for r in results)
        assert all(r.geojson["features"] == [] for r in results)
        assert "real result, not a loading error" in summarise(
            results, deterministic_plan(contract)
        )

    def test_missing_bbox_falls_back_to_the_showcase_area(self):
        contract = _contract(bbox=None)
        results = execute(deterministic_plan(contract), contract)
        assert any(r.feature_count > 0 for r in results)

    def test_summary_reports_unmet_needs(self):
        contract = _contract(hazards=["active_fire", "fire_spread"])
        plan = validate_proposal(
            contract,
            PlanProposal(
                layers=[LayerChoice(capability_id="official_fire_perimeters", reason="ok")]
            ),
        )
        text = summarise(execute(plan, contract), plan)
        assert "Not available" in text

    def test_summary_names_domain_objects_instead_of_features(self):
        contract = _contract(
            target="official_fire_perimeters",
            hazards=["active_fire"],
        )
        plan = deterministic_plan(contract)
        text = summarise(execute(plan, contract), plan)
        assert "fire-boundary polygon" in text
        assert " feature" not in text


class TestShowcaseData:
    @pytest.mark.parametrize("capability_id", sorted(CAPABILITIES))
    def test_snapshot_exists_and_carries_provenance(self, capability_id):
        from wildfire_agent.planning.capabilities import load_layer

        raw = load_layer(capability_id)
        prov = raw.get("provenance", {})
        assert raw["features"], f"{capability_id} snapshot is empty"
        assert prov.get("source"), f"{capability_id} has no source recorded"
        assert prov.get("retrieved_at"), f"{capability_id} has no retrieval timestamp"

    def test_showcase_area_is_altadena(self):
        assert SHOWCASE_AREA["id"] == "altadena"
        lon, lat = SHOWCASE_AREA["center"]
        assert -119 < lon < -117 and 33 < lat < 35


class TestVariableGaps:
    """What a layer carries, and what nothing carries.

    `supplies` is declared from the fields in each file rather than from the
    layer's name. That is the whole point: a perimeter file with no date column
    does not supply a detection time, and a registry that claimed otherwise
    would turn a real gap into a silent one.
    """

    def test_a_gap_nothing_in_the_deployment_can_close(self):
        from wildfire_agent.planning.capabilities import missing_variables

        absent = missing_variables("active_fire")
        # Every active-fire layer here is a perimeter or a thermal point. None
        # carries a confidence value - frp_mw is radiative power, which is
        # intensity, not confidence - so this gap needs an outside source.
        assert "detection confidence" in absent

    def test_the_deployment_covers_what_its_layers_between_them_carry(self):
        from wildfire_agent.planning.capabilities import missing_variables

        absent = missing_variables("active_fire")
        for supplied in ("fire perimeter", "hotspot location", "detection time", "fire size"):
            assert supplied not in absent

    def test_a_plan_that_selects_one_layer_has_a_wider_gap_than_the_deployment(self):
        """Choosing fewer layers is a planning gap, not a data gap.

        The distinction decides what to do about it: fetch from outside, or pick
        a layer already sitting in the registry.
        """
        from wildfire_agent.planning.capabilities import missing_variables

        deployment_gap = set(missing_variables("active_fire"))
        one_layer_gap = set(missing_variables("active_fire", ["official_fire_perimeters"]))

        assert deployment_gap < one_layer_gap
        assert "hotspot location" in one_layer_gap
        assert "hotspot location" not in deployment_gap

    def test_an_unknown_hazard_object_reports_no_gap_rather_than_raising(self):
        from wildfire_agent.planning.capabilities import missing_variables

        assert missing_variables("not_a_hazard_object") == ()


class TestLocalLayersAreNamedToo:
    """`_RESULT_NOUNS` was keyed on hazard objects that the taxonomy does not
    use - `population_exposure` where it says `exposure`, plus `burned_area`,
    `fire_perimeter` and `satellite_hotspot`, which were introduced for the
    public MCP layers only.

    Every layer the local fire path produces therefore fell through to "mapped
    polygons". That is invisible without the TS-SatFire archive, because with no
    local data no fire ever matches and none of these layers is ever built.
    """

    def _layer(self, capability_id: str, hazard: str, geometry: str = "Polygon") -> LayerResult:
        return LayerResult(
            capability_id=capability_id, title="t", hazard_object=hazard,
            geometry_type=geometry, caveat="c", feature_count=3, source="s",
            as_of="2020-09-27", geojson={"type": "FeatureCollection", "features": []},
        )

    def test_places_the_burned_area_reaches_are_named(self):
        noun = executor._result_noun(
            self._layer("burned_area_intersecting_place_boundaries", "exposure"), plural=True
        )
        assert noun == "places intersecting the burned area"

    def test_places_with_active_fire_signals_are_named_separately(self):
        """The two are different claims and the codebase never merges them."""
        noun = executor._result_noun(
            self._layer("active_fire_intersecting_place_boundaries", "exposure"), plural=True
        )
        assert "active-fire" in noun
        assert "burned" not in noun

    def test_nearest_places_are_not_called_intersecting(self):
        noun = executor._result_noun(
            self._layer("burned_area_nearest_place_reference_points", "exposure", "Point"),
            plural=True,
        )
        assert "nearby" in noun
        assert "intersect" not in noun

    def test_debris_hazard_areas_are_named_as_modelled(self):
        noun = executor._result_noun(
            self._layer("post_fire_debris_flow_hazard_areas", "post_fire_debris_flow"),
            plural=True,
        )
        assert noun == "modelled hazard areas"

    def test_no_local_layer_falls_back_to_the_generic_noun(self):
        for capability_id, hazard, geometry in (
            ("burned_area_intersecting_place_boundaries", "exposure", "Polygon"),
            ("active_fire_intersecting_place_boundaries", "exposure", "Polygon"),
            ("burned_area_nearest_place_reference_points", "exposure", "Point"),
            ("fire_intersecting_place_boundaries", "exposure", "Polygon"),
            ("fire_nearest_place_reference_points", "exposure", "Point"),
            ("subject_city_boundary", "exposure", "Polygon"),
            ("subject_fire_perimeter", "fire_perimeter", "Polygon"),
            ("post_fire_debris_flow_hazard_areas", "post_fire_debris_flow", "Polygon"),
        ):
            noun = executor._result_noun(self._layer(capability_id, hazard, geometry), plural=True)
            assert not noun.startswith("mapped "), f"{capability_id} -> {noun}"

    def test_no_noun_asserts_that_a_place_was_affected(self):
        """An intersection is not impact. "Affected community" states the one
        thing this pipeline refuses to state."""
        for singular, plural in executor._RESULT_NOUNS.values():
            assert "affected" not in singular and "affected" not in plural
        for singular, plural in executor._CAPABILITY_NOUNS.values():
            assert "affected" not in singular and "affected" not in plural
