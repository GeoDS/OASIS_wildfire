from wildfire_agent.taxonomy import (
    HAZARD_OBJECTS,
    INTENT_SLOT_MATRIX,
    SLOT_DEFINITIONS,
    required_slots,
    slot_default,
    slot_matrix_prompt_block,
)


def test_all_matrix_slots_are_defined():
    for intent, slots in INTENT_SLOT_MATRIX.items():
        for slot in slots:
            assert slot in SLOT_DEFINITIONS, f"{intent} references an undefined slot: {slot}"


def test_location_is_blocking_for_every_intent():
    for intent, slots in INTENT_SLOT_MATRIX.items():
        assert slots.get("location") == "B", f"location must be blocking for intent {intent}"


def test_multi_intent_takes_union_and_strictest_level():
    merged = required_slots(["assessment", "decision_support"])
    # comparison_basis only comes from decision_support, so the union must carry it
    assert merged["comparison_basis"].requirement == "B"
    assert merged["comparison_basis"].from_intents == ["decision_support"]
    # target is B under both intents
    assert merged["target"].requirement == "B"


def test_strictest_level_wins_when_intents_disagree():
    # observation says D, assessment says B -> the stricter one wins
    merged = required_slots(["observation", "assessment"])
    assert merged["target"].requirement == "B"
    assert set(merged["target"].from_intents) == {"observation", "assessment"}


def test_hazard_objects_cover_three_layers():
    layers = {ho.layer for ho in HAZARD_OBJECTS.values()}
    assert layers == {"hazard", "exposure", "action"}


def test_prompt_block_marks_blocking_and_defaults():
    block = slot_matrix_prompt_block(["observation"])
    assert "`location` [BLOCKING]" in block
    assert "`time_horizon` [DEFAULTABLE] (neutral default: 'now')" in block


def test_requested_output_default_follows_intent():
    """Decision support wants a ranking, not just a picture: the default has to
    follow the intent."""
    assert slot_default("requested_output", ["observation"]) == "map"
    assert slot_default("requested_output", ["decision_support"]) == "ranking + map"
    assert slot_default("requested_output", ["evaluation_adaptation"]) == "report + map"
    # With several intents the override table key order decides; decision_support
    # precedes evaluation.
    assert (
        slot_default("requested_output", ["assessment", "decision_support"]) == "ranking + map"
    )


def test_slot_without_default_returns_none():
    """Neither blocking nor optional slots may be filled silently."""
    assert slot_default("comparison_basis", ["decision_support"]) is None
    assert slot_default("location", ["observation"]) is None
