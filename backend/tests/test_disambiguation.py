"""Backend-owned evidence selection and the remaining blocking choices."""

from wildfire_agent.contract import AnalysisContract, ScalarSlot
from wildfire_agent.graph.nodes import enforce_family_disambiguation, names_a_family
from wildfire_agent.taxonomy import family_choices_for


def _contract(target_value: str | None, *, hazards: list[str] | None = None) -> AnalysisContract:
    return AnalysisContract(
        original_request="Where are the active fires near Altadena?",
        expertise="general",
        task_intent=["observation"],
        hazard_objects=hazards if hazards is not None else ["active_fire"],
        slots={
            "target": ScalarSlot(
                value=target_value,
                source="user_stated" if target_value else "default",
                confidence=0.9 if target_value else 0.0,
                is_blocking=False,
            )
        },
    )


class TestNamesAFamily:
    def test_generic_wording_does_not_count(self):
        """Regression: 'fire' was derived as a keyword of official_fire_perimeters,
        so "active fires" looked like an answer. It is the question."""
        choices = family_choices_for(["active_fire"])
        assert not names_a_family("active fires", choices)
        assert not names_a_family("fires", choices)
        assert not names_a_family("all relevant features in scope", choices)

    def test_naming_a_family_counts(self):
        choices = family_choices_for(["active_fire"])
        assert names_a_family("satellite hotspots", choices)
        assert names_a_family("official_fire_perimeters", choices)
        assert names_a_family("VIIRS thermal detections", choices)
        assert names_a_family("official_fire_perimeters + satellite_hotspots", choices)

    def test_empty_value_never_counts(self):
        assert not names_a_family(None, family_choices_for(["active_fire"]))
        assert not names_a_family("", family_choices_for(["active_fire"]))


class TestEnforcement:
    def test_generic_fire_question_gets_conservative_backend_default(self):
        contract = _contract("active fires")
        enforce_family_disambiguation(contract)

        target = contract.slots["target"]
        assert not target.is_blocking
        assert target.value == "Officially confirmed fire perimeters"
        assert target.source == "agent_inferred"
        assert contract.ready_for_planning
        assert "Fire evidence selected automatically" in contract.assumptions[0]

    def test_satellite_wording_is_selected_without_a_question(self):
        contract = _contract(None)
        contract.original_request = "Show hotspot activity for the Bobcat Fire"
        enforce_family_disambiguation(contract)
        assert contract.slots["target"].value == "Satellite thermal detections"
        assert not contract.slots["target"].is_blocking

    def test_local_event_uses_ts_satfire_labels_in_the_contract(self):
        contract = _contract(None)
        contract.original_request = "Show the lifecycle of the Bobcat Fire on 2020-09-18"
        enforce_family_disambiguation(contract)
        assert (
            contract.slots["target"].value
            == "TS-SatFire active fire + burned area historical labels"
        )
        assert contract.slots["target"].source == "agent_inferred"
        assert not contract.slots["target"].is_blocking

    def test_named_family_passes_through(self):
        contract = _contract("official_fire_perimeters + satellite_hotspots")
        enforce_family_disambiguation(contract)

        target = contract.slots["target"]
        assert not target.is_blocking
        assert target.value == "official_fire_perimeters + satellite_hotspots"
        assert contract.ready_for_planning

    def test_hazard_object_without_family_choices_is_untouched(self):
        contract = _contract("roads", hazards=["infrastructure"])
        assert enforce_family_disambiguation(contract) == []
        assert contract.slots["target"].value == "roads"
        assert not contract.slots["target"].is_blocking

    def test_missing_target_slot_is_created(self):
        contract = AnalysisContract(
            original_request="Where are the active fires near Altadena?",
            expertise="general",
            task_intent=["observation"],
            hazard_objects=["active_fire"],
            slots={},
        )
        enforce_family_disambiguation(contract)
        assert contract.slots["target"].value == "Officially confirmed fire perimeters"
        assert not contract.slots["target"].is_blocking

    def test_smoke_family_is_also_selected_by_backend(self):
        contract = _contract("smoke", hazards=["smoke_plume"])
        enforce_family_disambiguation(contract)
        assert not contract.slots["target"].is_blocking
        assert contract.slots["target"].value == "Satellite plume extent"

        contract = _contract("ground monitor readings", hazards=["smoke_plume"])
        enforce_family_disambiguation(contract)
        assert not contract.slots["target"].is_blocking
