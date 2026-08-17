"""Walkthrough scenario 1 end to end, including interrupt and resume.

Runs on a stub LLM, so no API key is needed and CI stays free. What is under
test is the **graph wiring** - slot merging, the clarification interrupt,
default back-filling, spatial grounding - not the quality of the model's
judgement.
"""

from __future__ import annotations

import uuid

import pytest
from langgraph.types import Command

from wildfire_agent.contract import (
    ClarificationOption,
    ClarificationQuestion,
    ResolvedLocation,
    SpatialSlot,
)
from wildfire_agent.graph import build_graph
from wildfire_agent.graph.models import (
    ClarificationBatch,
    ClarificationInterpretation,
    CompiledAssumption,
    CompiledTask,
    RequirementUnderstanding,
    SlotDecision,
    SlotUpdate,
)
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
        hazard_objects=["active_fire", "not_a_real_object"],  # the latter must be dropped
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

    # ── The run must pause for clarification ────────────────────────
    interrupts = state.get("__interrupt__")
    assert interrupts, "an empty blocking slot must interrupt"
    payload = interrupts[0].value
    assert payload["type"] == "clarification"

    # Only `target` is asked. "near Altadena" grounds fine, so the radius is a
    # default drawn on the map (rule 2) rather than an interruption: asking
    # about something the user can confirm visually is wasted friction.
    assert payload["pending_slots"] == ["target"]
    assert len(payload["questions"]) == 1
    # The general level must offer concrete options, never an open question.
    assert all(q["options"] for q in payload["questions"])

    # The defaulted radius has to be recorded, not used silently.
    assert any("25 km" in a for a in state["contract"].assumptions)

    # ── Resume ──────────────────────────────────────────────────────
    state = await graph.ainvoke(Command(resume="both, 10 km"), config=config)
    assert not state.get("__interrupt__"), "one round should have been enough"

    contract = state["contract"]
    assert contract.ready_for_planning
    assert contract.clarification_rounds == 1

    assert contract.slots["target"].source == "user_stated"
    assert contract.slots["target"].value == "official_fire_perimeters + satellite_hotspots"

    spatial = contract.slots["location"]
    assert isinstance(spatial, SpatialSlot)
    assert spatial.resolved is not None
    assert spatial.resolved.buffer_km == pytest.approx(10.0)
    assert spatial.resolved.confirmed_by_user is False

    # Assumptions are **recomputed**, not appended: once the user supplies 10 km,
    # "assuming 25 km" is no longer true and keeping it misreports the contract.
    assert not any(a.startswith("Radius not specified") for a in contract.assumptions)
    # But "not yet confirmed" still holds and must survive.
    assert any(a.startswith("Spatial scope resolved by the agent") for a in contract.assumptions)


async def test_family_disambiguation_promotes_target(stub_llm, stub_geocoder):
    """The compiler stub leaves `target` non-blocking on purpose. Enforcement in
    the domain model - not the prompt - is what turns it into a question."""
    graph = build_graph()
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    state = await graph.ainvoke(
        {"original_request": QUESTION, "clarification_rounds": 0}, config=config
    )
    assert state["__interrupt__"][0].value["pending_slots"] == ["target"]


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
        {"original_request": "Will the wildfire affect my house?", "clarification_rounds": 0},
        config=config,
    )
    assert "location" in state["__interrupt__"][0].value["pending_slots"]


async def test_hallucinated_enums_are_dropped(stub_llm, stub_geocoder):
    graph = build_graph()
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    await graph.ainvoke({"original_request": QUESTION, "clarification_rounds": 0}, config=config)
    state = await graph.ainvoke(Command(resume="both, 10 km"), config=config)
    contract = state["contract"]

    assert contract.hazard_objects == ["active_fire"]
    assert "made_up_slot" not in contract.slots


async def test_missing_baseline_slot_is_backfilled_with_default(stub_llm, stub_geocoder):
    """`requested_output` was skipped by the model; it must be filled and the
    default recorded in assumptions."""
    graph = build_graph()
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    await graph.ainvoke({"original_request": QUESTION, "clarification_rounds": 0}, config=config)
    state = await graph.ainvoke(Command(resume="both, 10 km"), config=config)
    contract = state["contract"]

    assert contract.slots["requested_output"].value == "map"
    assert contract.slots["requested_output"].source == "default"
    assert any("requested_output" in a for a in contract.assumptions)


async def test_user_declining_stops_the_questioning(stub_llm, stub_geocoder, monkeypatch):
    """When the user says "you decide", stop asking and use defaults - do not
    keep interrogating until the round limit."""
    monkeypatch.setattr(
        "wildfire_agent.graph.nodes.structured",
        lambda schema, **_k: _StubRunnable(
            {
                RequirementUnderstanding: _understanding(),
                CompiledTask: _compiled(),
                ClarificationBatch: _batch(),
                ClarificationInterpretation: ClarificationInterpretation(user_declined=True),
            }[schema]
        ),
    )
    graph = build_graph()
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    await graph.ainvoke({"original_request": QUESTION, "clarification_rounds": 0}, config=config)
    state = await graph.ainvoke(Command(resume="you decide"), config=config)

    assert not state.get("__interrupt__")
    contract = state["contract"]
    assert contract.clarification_rounds == 1
    assert contract.ready_for_planning
    # target fell back to a default, and that has to stay visible
    assert contract.slots["target"].source == "default"
    assert any("target" in a for a in contract.assumptions)


async def test_expertise_override_wins_over_inference(stub_llm, stub_geocoder):
    """Demo highlight: the manual picker in the UI outranks the inference."""
    graph = build_graph()
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    await graph.ainvoke(
        {"original_request": QUESTION, "clarification_rounds": 0, "expertise_override": "expert"},
        config=config,
    )
    state = await graph.ainvoke(Command(resume="both, 10 km"), config=config)
    assert state["contract"].expertise == "expert"
