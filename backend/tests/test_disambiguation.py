"""Non-interchangeable data families must force a question.

This is the single most important moment in the demo, and it is deliberately
*not* left to the prompt: gpt-4.1-mini read the instruction and defaulted the
slot anyway, silently choosing between "officially confirmed fire perimeters"
and "satellite thermal detections" - two things that are not the same fact.
The rule lives in the domain model and is enforced in code.
"""

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
    def test_ambiguous_target_is_forced_blocking_and_cleared(self):
        contract = _contract("active fires")
        enforce_family_disambiguation(contract)

        target = contract.slots["target"]
        assert target.is_blocking
        # The vague value must be cleared, not kept: leaving it in place is
        # exactly the silent wrong pick we are guarding against.
        assert target.value is None
        assert "not semantically interchangeable" in (target.blocking_reason or "")
        assert contract.pending_slots == ["target"]
        assert not contract.ready_for_planning

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
        assert contract.slots["target"].is_blocking

    def test_smoke_also_carries_a_family_choice(self):
        """Satellite plume extent and ground monitors answer different questions:
        where smoke is overhead vs what people actually breathe."""
        contract = _contract("smoke", hazards=["smoke_plume"])
        enforce_family_disambiguation(contract)
        assert contract.slots["target"].is_blocking

        contract = _contract("ground monitor readings", hazards=["smoke_plume"])
        enforce_family_disambiguation(contract)
        assert not contract.slots["target"].is_blocking
