from wildfire_agent.contract import (
    AnalysisContract,
    ResolvedLocation,
    ScalarSlot,
    SpatialSlot,
)


def _contract(**slots) -> AnalysisContract:
    return AnalysisContract(
        original_request="Where are the active fires near Altadena?",
        expertise="general",
        task_intent=["observation"],
        slots=slots,
    )


def test_blocking_but_empty_needs_clarification():
    slot = ScalarSlot(is_blocking=True)
    assert slot.needs_clarification


def test_empty_but_not_blocking_is_never_asked():
    """Golden rule: the trigger is not "the field is empty" but "the emptiness
    would change the analysis"."""
    slot = ScalarSlot(is_blocking=False)
    assert not slot.needs_clarification


def test_spatial_slot_filled_but_ungrounded_still_needs_clarification():
    slot = SpatialSlot(value="near my house", raw="near my house", is_blocking=True)
    assert slot.needs_clarification


def test_spatial_slot_grounded_is_satisfied():
    slot = SpatialSlot(
        value="Altadena, CA + 10km",
        is_blocking=True,
        resolved=ResolvedLocation(center=(-89.4012, 43.0731), buffer_km=10),
    )
    assert not slot.needs_clarification


def test_ready_for_planning_tracks_pending_slots():
    contract = _contract(
        location=SpatialSlot(is_blocking=True),
        target=ScalarSlot(value="all", is_blocking=False),
    )
    assert not contract.ready_for_planning
    assert contract.pending_slots == ["location"]

    contract.slots["location"] = SpatialSlot(
        value="Madison",
        is_blocking=True,
        resolved=ResolvedLocation(center=(-89.4012, 43.0731)),
    )
    assert contract.ready_for_planning


def test_discriminated_union_survives_json_roundtrip():
    """`slots` is a heterogeneous dict: a SpatialSlot must not degrade into a
    ScalarSlot on a serialisation round trip."""
    contract = _contract(
        location=SpatialSlot(value="Madison", raw="near Madison", is_blocking=True),
        target=ScalarSlot(value="all", is_blocking=False),
    )
    restored = AnalysisContract.model_validate_json(contract.model_dump_json())
    assert isinstance(restored.slots["location"], SpatialSlot)
    assert restored.slots["location"].raw == "near Madison"
    assert isinstance(restored.slots["target"], ScalarSlot)


def test_ready_for_planning_is_serialised_for_downstream():
    contract = _contract(target=ScalarSlot(value="all"))
    assert "ready_for_planning" in contract.model_dump()


def test_filled_ratio():
    contract = _contract(
        location=SpatialSlot(value="Madison"),
        target=ScalarSlot(),
        time_horizon=ScalarSlot(value="now"),
        requested_output=ScalarSlot(),
    )
    assert contract.filled_ratio() == 0.5
