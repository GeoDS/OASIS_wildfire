"""Walkthrough scenario 1 end to end, including interrupt and resume.

Runs on a stub LLM, so no API key is needed and CI stays free. What is under
test is the **graph wiring** - slot merging, the clarification interrupt,
default back-filling, spatial grounding - not the quality of the model's
judgement.
"""

from __future__ import annotations

import uuid

import pytest

from wildfire_agent.contract import (
    AnalysisContract,
    ClarificationOption,
    ClarificationQuestion,
    ResolvedLocation,
    ScalarSlot,
    SpatialSlot,
)
from wildfire_agent.graph import build_graph, nodes
from wildfire_agent.graph.models import (
    ClarificationBatch,
    ClarificationInterpretation,
    CompiledAssumption,
    CompiledTask,
    RequirementUnderstanding,
    SlotDecision,
    SlotUpdate,
)
from wildfire_agent.graph.nodes import _inherit_prior_fire_context, _inherit_prior_location
from wildfire_agent.planning.planner import LayerChoice, PlanProposal

QUESTION = "Where are the active fires near Altadena?"


class _StubRunnable:
    def __init__(self, value):
        self._value = value

    async def ainvoke(self, _messages, **_kwargs):
        return self._value


def _understanding() -> RequirementUnderstanding:
    return RequirementUnderstanding(
        restatement="The user wants to know which fires are currently active near Altadena",
        task_intent=["observation"],
        intent_reason="'Where are' retrieves the current state",
        expertise="general",
        expertise_reason="No technical vocabulary in the request",
        user_role="unknown",
        role_reason="No role signal at all; the golden rule says do not guess",
        role_materially_changes_analysis=False,
        hazard_objects=[
            "active_fire",
            "not_a_real_object",
        ],  # the latter must be dropped
        hazard_reason="'active fires' matches directly",
        raw_location="near Altadena",
    )


def _compiled() -> CompiledTask:
    return CompiledTask(
        slots=[
            SlotDecision(
                slot="location",
                value="near Altadena",
                source="user_stated",
                confidence=0.6,
                is_blocking=True,
                blocking_reason="location is always blocking; the radius in 'near' is undefined",
            ),
            SlotDecision(
                slot="target",
                value=None,
                source="default",
                confidence=0.0,
                is_blocking=False,
                blocking_reason="matrix baseline D - enforcement should promote this",
            ),
            SlotDecision(
                slot="time_horizon",
                value="now",
                source="default",
                confidence=0.8,
                is_blocking=False,
                blocking_reason="observation baseline D, neutral default applied",
            ),
            # requested_output deliberately absent - exercises the back-fill
            SlotDecision(
                slot="made_up_slot",  # not in the baseline - must be discarded
                value="x",
                is_blocking=True,
                blocking_reason="should never reach the contract",
            ),
        ],
        assumptions=[
            CompiledAssumption(
                slot="time_horizon",
                text="Time horizon defaulted to the present because none was given",
            )
        ],
    )


def _batch() -> ClarificationBatch:
    return ClarificationBatch(
        preamble="One thing to settle before I can show you the map:",
        questions=[
            ClarificationQuestion(
                slot="target",
                question="Which kind of fire data do you mean?",
                options=[
                    ClarificationOption(
                        label="Officially confirmed fire perimeters",
                        implication="Verified but slow to update",
                    ),
                    ClarificationOption(
                        label="Satellite thermal detections",
                        implication="Timelier, but may be an agricultural burn",
                    ),
                    ClarificationOption(label="Both", recommended=True),
                ],
            ),
        ],
    )


def _plan_proposal() -> PlanProposal:
    return PlanProposal(
        layers=[
            LayerChoice(
                capability_id="official_fire_perimeters",
                reason="the contract asked for agency-confirmed perimeters",
            ),
            LayerChoice(
                capability_id="satellite_hotspots",
                reason="the contract also asked for satellite detections",
            ),
        ],
        reading_note="Detections outside the confirmed perimeter are unverified heat.",
    )


def _interpretation() -> ClarificationInterpretation:
    return ClarificationInterpretation(
        updates=[
            SlotUpdate(slot="location", value="10 km buffer around Altadena, CA"),
            SlotUpdate(slot="target", value="official_fire_perimeters + satellite_hotspots"),
        ]
    )


@pytest.fixture
def stub_llm(monkeypatch):
    """Return a canned object per requested schema."""
    responses = {
        RequirementUnderstanding: _understanding(),
        CompiledTask: _compiled(),
        ClarificationBatch: _batch(),
        ClarificationInterpretation: _interpretation(),
        PlanProposal: _plan_proposal(),
    }

    def fake_structured(schema, **_kwargs):
        return _StubRunnable(responses[schema])

    monkeypatch.setattr("wildfire_agent.graph.nodes.structured", fake_structured)
    # The planner imports `structured` from the llm module directly, so it needs
    # its own patch. Without this the suite silently calls a real provider:
    # slow, billable, and flaky.
    monkeypatch.setattr(
        "wildfire_agent.planning.planner.structured",
        lambda schema, **_k: _StubRunnable(_plan_proposal()),
    )


@pytest.fixture
def stub_geocoder(monkeypatch):
    """Never hit the network. Echo back whatever radius the caller passed, which
    is how we check that '10 km' was parsed out of the reply."""

    async def fake_resolve(query, *, buffer_km=None):
        return ResolvedLocation(
            display_name="Altadena, Los Angeles County, California, USA",
            center=(-118.1312, 34.1897),
            buffer_km=buffer_km if buffer_km is not None else 25.0,
            bbox=(-118.28, 34.12, -117.96, 34.32),
            geocoder="nominatim",
        )

    monkeypatch.setattr("wildfire_agent.graph.nodes.resolve_location", fake_resolve)


async def test_scenario_1_end_to_end(stub_llm, stub_geocoder):
    graph = build_graph()
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}

    state = await graph.ainvoke(
        {"original_request": QUESTION, "clarification_rounds": 0}, config=config
    )

    # Fire evidence is a backend policy choice, not a product question shown to
    # the user. A resolvable location therefore completes in one pass.
    assert not state.get("__interrupt__")

    contract = state["contract"]
    assert contract.ready_for_planning
    assert contract.clarification_rounds == 0

    assert contract.slots["target"].source == "agent_inferred"
    assert contract.slots["target"].value == "Officially confirmed fire perimeters"

    spatial = contract.slots["location"]
    assert isinstance(spatial, SpatialSlot)
    assert spatial.resolved is not None
    assert spatial.resolved.buffer_km == pytest.approx(25.0)
    assert spatial.resolved.confirmed_by_user is False

    assert any(a.startswith("Radius not specified") for a in contract.assumptions)
    assert any(a.startswith("Spatial scope resolved by the agent") for a in contract.assumptions)


async def test_family_disambiguation_is_resolved_by_backend(stub_llm, stub_geocoder):
    graph = build_graph()
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    state = await graph.ainvoke(
        {"original_request": QUESTION, "clarification_rounds": 0}, config=config
    )
    assert not state.get("__interrupt__")
    assert state["contract"].slots["target"].value == "Officially confirmed fire perimeters"


async def test_ungroundable_location_does_block(stub_llm, monkeypatch):
    """The mirror case: 'near my house' cannot be grounded, so location must be asked.

    Together with the scenario above this is the whole spatial policy: echo what
    resolves, interrupt only for what does not.
    """

    async def never_resolves(_query, *, buffer_km=None):
        return None

    monkeypatch.setattr("wildfire_agent.graph.nodes.resolve_location", never_resolves)

    graph = build_graph()
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    state = await graph.ainvoke(
        {
            "original_request": "Will the wildfire affect my house?",
            "clarification_rounds": 0,
        },
        config=config,
    )
    assert "location" in state["__interrupt__"][0].value["pending_slots"]


async def test_hallucinated_enums_are_dropped(stub_llm, stub_geocoder):
    graph = build_graph()
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    state = await graph.ainvoke(
        {"original_request": QUESTION, "clarification_rounds": 0}, config=config
    )
    contract = state["contract"]

    assert contract.hazard_objects == ["active_fire"]
    assert "made_up_slot" not in contract.slots


async def test_missing_baseline_slot_is_backfilled_with_default(stub_llm, stub_geocoder):
    """`requested_output` was skipped by the model; it must be filled and the
    default recorded in assumptions."""
    graph = build_graph()
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    state = await graph.ainvoke(
        {"original_request": QUESTION, "clarification_rounds": 0}, config=config
    )
    contract = state["contract"]

    assert contract.slots["requested_output"].value == "map"
    assert contract.slots["requested_output"].source == "default"
    assert any("requested_output" in a for a in contract.assumptions)


async def test_backend_default_avoids_a_source_question(stub_llm, stub_geocoder):
    graph = build_graph()
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    state = await graph.ainvoke(
        {"original_request": QUESTION, "clarification_rounds": 0}, config=config
    )

    assert not state.get("__interrupt__")
    contract = state["contract"]
    assert contract.clarification_rounds == 0
    assert contract.ready_for_planning
    assert contract.slots["target"].source == "agent_inferred"


async def test_expertise_override_wins_over_inference(stub_llm, stub_geocoder):
    """Demo highlight: the manual picker in the UI outranks the inference."""
    graph = build_graph()
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    state = await graph.ainvoke(
        {
            "original_request": QUESTION,
            "clarification_rounds": 0,
            "expertise_override": "expert",
        },
        config=config,
    )
    assert state["contract"].expertise == "expert"


def test_followup_nearby_inherits_previous_resolved_location():
    previous = SpatialSlot(
        value="Santa Barbara, California",
        raw="Santa Barbara, California",
        resolved=ResolvedLocation(
            display_name="Santa Barbara, California, USA",
            center=(-119.7027, 34.4221),
            buffer_km=25,
            bbox=(-119.98, 34.19, -119.43, 34.65),
            geocoder="nominatim",
        ),
    )
    contract = AnalysisContract(
        original_request="is there fire nearby",
        slots={
            "location": SpatialSlot(
                value="nearby",
                raw="nearby",
                is_blocking=True,
            )
        },
    )

    assert _inherit_prior_location(contract, previous, inherit_subject=True) is True
    spatial = contract.spatial()
    assert spatial is not None
    assert spatial.value == "Santa Barbara, California"
    assert spatial.resolved is not None
    assert spatial.resolved.center == (-119.7027, 34.4221)
    assert not spatial.needs_clarification


def test_followup_explicit_place_overrides_previous_location():
    previous = SpatialSlot(
        value="Santa Barbara, California",
        resolved=ResolvedLocation(center=(-119.7027, 34.4221)),
    )
    contract = AnalysisContract(
        original_request="is there fire near San Diego",
        slots={
            "location": SpatialSlot(
                value="near San Diego",
                raw="near San Diego",
                is_blocking=True,
            )
        },
    )

    assert _inherit_prior_location(contract, previous, inherit_subject=False) is False
    spatial = contract.spatial()
    assert spatial is not None
    assert spatial.value == "near San Diego"


def test_fire_burned_area_reference_inherits_previous_event_location():
    previous = SpatialSlot(
        value="Bobcat Fire",
        resolved=ResolvedLocation(
            display_name="Bobcat Fire",
            center=(-117.93, 34.33),
            bbox=(-118.22, 34.01, -117.74, 34.62),
        ),
    )
    contract = AnalysisContract(
        original_request="Which cities intersected this fire's mapped burned area?",
        slots={
            "location": SpatialSlot(
                value="this fire's mapped burned area",
                raw="this fire's mapped burned area",
                is_blocking=True,
            )
        },
    )

    assert _inherit_prior_location(contract, previous, inherit_subject=True) is True
    spatial = contract.spatial()
    assert spatial is not None
    assert spatial.value == "Bobcat Fire"
    assert spatial.resolved is not None
    assert spatial.resolved.center == (-117.93, 34.33)


def test_fire_followup_inherits_historical_date_and_evidence_family():
    contract = AnalysisContract(
        original_request="Which cities intersected this fire's mapped burned area?",
        slots={
            "time_horizon": ScalarSlot(value="now", source="default"),
            "target": ScalarSlot(value="Officially confirmed fire perimeters"),
        },
        assumptions=[
            "No time window was specified, so the analysis defaults to now.",
            "Fire evidence selected automatically: Officially confirmed fire perimeters.",
        ],
    )

    assert (
        _inherit_prior_fire_context(
            contract,
            "24461771",
            "2020-09-18",
            inherit_subject=True,
            inherit_time=True,
        )
        is True
    )
    time_horizon = contract.slots["time_horizon"]
    target = contract.slots["target"]
    assert time_horizon.value == "2020-09-18"
    assert time_horizon.source == "agent_inferred"
    assert target.value == "TS-SatFire active fire + burned area historical labels"
    assert target.source == "agent_inferred"
    assert all("defaults to now" not in item for item in contract.assumptions)
    assert any("event 24461771, time 2020-09-18" in item for item in contract.assumptions)


def test_fire_followup_preserves_model_compiled_time_range():
    contract = AnalysisContract(
        original_request=(
            "For Bobcat Fire, compare NDVI from 2020-09-04 with 2020-09-27 "
            "inside the mapped burned area."
        ),
        slots={
            "time_horizon": ScalarSlot(
                value="2020-09-04 to 2020-09-27",
                source="user_stated",
                confidence=0.99,
            ),
            "target": ScalarSlot(value="Vegetation condition (NDVI)"),
        },
    )

    assert (
        _inherit_prior_fire_context(
            contract,
            "24461771",
            "2020-09-18",
            inherit_subject=True,
            inherit_time=True,
        )
        is True
    )
    time_horizon = contract.slots["time_horizon"]
    assert time_horizon.value == "2020-09-04 to 2020-09-27"
    assert time_horizon.source == "user_stated"
    assert any(
        "event 24461771, time 2020-09-04 to 2020-09-27" in item for item in contract.assumptions
    )


def test_no_prompt_names_a_real_place():
    """A concrete place inside a live prompt is a thumb on the scale.

    The clarification-interpretation prompt carried `"10 km buffer around
    Altadena, CA"` as its worked example, and that prompt runs on every
    clarification turn whatever the user asked about - so a question about
    Santa Barbara was shown Altadena as the shape of a correct answer.
    Placeholders carry the same format without naming anywhere.
    """
    from wildfire_agent.graph import prompts

    sources = [
        getattr(prompts, name)
        for name in dir(prompts)
        if not name.startswith("__") and isinstance(getattr(prompts, name), str)
    ]
    sources += [
        prompts.requirement_understanding_prompt(),
        prompts.task_compiler_prompt(["assess_risk"], "general"),
        prompts.clarification_prompt("general", ["location"]),
        prompts.interpretation_prompt(["location"], ["location", "target"], ["viirs_af"]),
    ]
    banned = ("Altadena", "Monrovia", "Duarte", "Arcadia", "Pasadena", "Santa Barbara", "Bobcat")
    for text in sources:
        for name in banned:
            assert name not in text, f"{name!r} is baked into a prompt"


class TestASingleOptionIsNotAQuestion:
    """"What's the population here" was interrupted with a choice about time
    period - "an official Census count and a newer population estimate are
    different measures" - when the only source this deployment has publishes
    exactly one vintage. The user was asked to pick between an option that
    exists and one that does not.

    The same rule the fetch gate follows: ask only what changes the answer.
    Where one value is available, settle it and record the assumption.
    """

    def _slot(self, value: str | None = None, blocking: bool = True) -> ScalarSlot:
        return ScalarSlot(
            value=value, confidence=0.0,
            source="default", is_blocking=blocking, blocking_reason="matrix baseline B",
        )

    def _contract(self, **slots) -> AnalysisContract:
        return AnalysisContract(original_request="what's the population here", slots=slots)

    def test_a_place_attribute_question_settles_the_vintage(self):
        contract = self._contract(time_horizon=self._slot())
        nodes._settle_place_attribute_time(contract, ("population",))

        slot = contract.slots["time_horizon"]
        assert slot.is_blocking is False
        assert "2020-2024" in (slot.value or "")
        assert slot.source == "agent_inferred"

    def test_the_assumption_is_recorded_where_the_user_can_see_it(self):
        """Filling a slot on someone's behalf is only acceptable while it stays
        visible - that is what `assumptions` is for."""
        contract = self._contract(time_horizon=self._slot())
        nodes._settle_place_attribute_time(contract, ("medianHouseholdIncome",))

        assert any("2020-2024" in a for a in contract.assumptions)
        assert any("only" in a.lower() or "one" in a.lower() for a in contract.assumptions)

    def test_a_turn_that_names_no_variable_is_untouched(self):
        contract = self._contract(time_horizon=self._slot())
        nodes._settle_place_attribute_time(contract, ())

        assert contract.slots["time_horizon"].is_blocking is True
        assert contract.assumptions == []

    def test_a_date_the_user_actually_gave_is_never_overwritten(self):
        """The user's own words outrank an inferred default, here as everywhere."""
        slot = self._slot(value="2020-09-27", blocking=False)
        slot.source = "user_stated"
        contract = self._contract(time_horizon=slot)
        nodes._settle_place_attribute_time(contract, ("population",))

        assert contract.slots["time_horizon"].value == "2020-09-27"
        assert contract.assumptions == []


class TestPlanningSkipsTheDiscardedModelCall:
    """The registry's three layers are pinned to Altadena.

    Once the contract's location resolves, `api._run` suppresses them rather
    than drawing January's Eaton snapshot over a question about another fire.
    Selecting them anyway cost a model call per turn whose result nobody saw.
    """

    async def test_a_resolved_location_skips_layer_selection_entirely(self, monkeypatch):
        from wildfire_agent.contract import (
            AnalysisContract,
            ResolvedLocation,
            ScalarSlot,
            SpatialSlot,
        )
        from wildfire_agent.graph import nodes

        async def fail(*_args, **_kwargs):
            raise AssertionError("build_plan was called on a turn the renderers answer")

        monkeypatch.setattr(nodes, "build_plan", fail)

        contract = AnalysisContract(
            original_request="Show the Woolsey fire on 2018-11-16.",
            hazard_objects=["active_fire"],
            slots={
                "location": SpatialSlot(
                    value="Malibu, CA",
                    resolved=ResolvedLocation(
                        center=(-118.80, 34.12), buffer_km=25, bbox=(-119.1, 34.0, -118.5, 34.3)
                    ),
                ),
                "target": ScalarSlot(value="official_fire_perimeters"),
            },
        )

        result = await nodes.planning({"contract": contract})

        assert result["plan"].layers == []
        # Active fire is fully served across the two paths, so silence here is
        # the correct answer rather than a missing one.
        assert result["plan"].unmet == []

    async def test_an_unresolved_location_still_runs_the_planner(self, monkeypatch):
        """The registry path is not deleted, only bypassed where it is moot."""
        from wildfire_agent.contract import AnalysisContract, ScalarSlot, SpatialSlot
        from wildfire_agent.graph import nodes
        from wildfire_agent.planning import ExecutionPlan

        called = []

        async def fake_build_plan(contract):
            called.append(contract)
            return ExecutionPlan()

        monkeypatch.setattr(nodes, "build_plan", fake_build_plan)

        contract = AnalysisContract(
            original_request="Where are the active fires?",
            hazard_objects=["active_fire"],
            slots={
                "location": SpatialSlot(value="somewhere", resolved=None),
                "target": ScalarSlot(value="official_fire_perimeters"),
            },
        )

        await nodes.planning({"contract": contract})
        assert called

    async def test_a_capability_gap_survives_the_skip(self, monkeypatch):
        """The one thing this stage still owns on the renderer path.

        Suppressing the whole plan event left `UnmetNeed` with no route to the
        screen, which is the honesty `docs/02` scenario 3 exists to check.
        """
        from wildfire_agent.contract import (
            AnalysisContract,
            ResolvedLocation,
            ScalarSlot,
            SpatialSlot,
        )
        from wildfire_agent.graph import nodes

        contract = AnalysisContract(
            original_request="Where should people evacuate to?",
            hazard_objects=["active_fire", "evacuation"],
            slots={
                "location": SpatialSlot(
                    value="Altadena, CA",
                    resolved=ResolvedLocation(
                        center=(-118.13, 34.19), buffer_km=15, bbox=(-118.3, 34.1, -117.9, 34.3)
                    ),
                ),
                "target": ScalarSlot(value="official_fire_perimeters"),
            },
        )

        result = await nodes.planning({"contract": contract})

        assert [u.hazard_object for u in result["plan"].unmet] == ["evacuation"]
