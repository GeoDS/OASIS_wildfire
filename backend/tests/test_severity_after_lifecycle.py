"""A severity question asked right after the life cycle is computed, not discussed.

Timing the roadshow demo in the video's order, 2026-10-29: "What is the life
cycle of the Bobcat Fire?" and then "How badly did the Bobcat fire burn between
the first and last day?". In one run of two the resolver called the second turn
discussion, so no dNBR was computed and the answer was burned-area growth read
off the life-cycle chart. The other run computed mean dNBR 0.383.
"""

from __future__ import annotations

import asyncio
import types

import pytest

from wildfire_agent import api
from wildfire_agent.conversation import (
    AnalysisRef,
    ConversationContext,
    ConversationResolution,
    SubjectRef,
)

BOBCAT = "24461771"
SEVERITY = "How badly did the Bobcat fire burn between the first and last day?"
NDVI = "Compare NDVI inside the Bobcat fire's mapped burned area from the first to the last record."


def _context(operation: str | None = "fire_lifecycle", subject: str = "fire_event") -> ConversationContext:
    return ConversationContext(
        active_subject=SubjectRef(
            type=subject, id=BOBCAT if subject == "fire_event" else None, name="Bobcat Fire"
        ),
        active_analysis=(
            AnalysisRef(id=f"{BOBCAT}:{operation}", operation=operation, description=operation)
            if operation
            else None
        ),
    )


@pytest.mark.parametrize("question", [SEVERITY, NDVI, "How severe was it?", "Show the dNBR"])
def test_naming_severity_or_ndvi_change_after_the_life_cycle_computes_it(question):
    assert api._index_change_kind("discussion", question, _context()) == "analysis"


def test_after_the_cities_answer_too():
    assert api._index_change_kind("discussion", SEVERITY, _context("community_intersection")) == (
        "analysis"
    )


@pytest.mark.parametrize(
    "operation, question",
    [
        # The figures asked about are on screen: discussion is right.
        ("nbr_change", "How badly did it burn?"),
        ("nbr_change", "Why is the severity only moderate?"),
        ("ndvi_change", "Why did NDVI change so much?"),
    ],
)
def test_the_same_analysis_on_screen_stays_a_discussion(operation, question):
    assert api._index_change_kind("discussion", question, _context(operation)) == "discussion"


@pytest.mark.parametrize(
    "question",
    [
        "What does that mean?",
        "Why did it grow so fast on Sep 19?",
        "Which way did it spread?",
        "What does NDVI stand for?",
    ],
)
def test_every_other_discussion_is_left_to_the_resolver(question):
    assert api._index_change_kind("discussion", question, _context()) == "discussion"


def test_it_never_moves_a_turn_away_from_analysis():
    assert api._index_change_kind("analysis", "What does that mean?", _context()) == "analysis"


def test_only_for_a_fire():
    """A place has no raster to difference."""
    assert api._index_change_kind("discussion", SEVERITY, _context(None, "place")) == "discussion"
    assert api._index_change_kind("discussion", SEVERITY, ConversationContext()) == "discussion"


def test_the_turn_reaches_the_pipeline_instead_of_discuss(monkeypatch):
    """The reported failure, end to end through `_run`."""
    session_id = "severity-after-lifecycle"
    discussed: list[str] = []
    planned: list[dict] = []

    async def fake_resolve(text, _context):
        return ConversationResolution(
            relation="follow_up", kind="discussion", standalone_request=text, reason="test"
        )

    async def fake_discuss(**kwargs):
        discussed.append(kwargs["question"])
        return "The burned area grew to 529 km² by Sep 27."

    class _Graph:
        async def aget_state(self, _config):
            return types.SimpleNamespace(values={}, next=(), tasks=())

        async def astream(self, payload, config, stream_mode):
            planned.append(payload)
            return
            yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(api, "resolve_turn", fake_resolve)
    monkeypatch.setattr(api, "discuss", fake_discuss)
    monkeypatch.setattr(api, "_graph", _Graph())
    monkeypatch.setitem(api._session_contexts, session_id, _context())

    async def drain():
        return [event async for event in api._run(session_id, api.MessageIn(text=SEVERITY))]

    try:
        events = asyncio.run(drain())
    finally:
        api._session_contexts.pop(session_id, None)
    assert discussed == [], "answered from the chart on screen instead of computing dNBR"
    assert [payload["original_request"] for payload in planned] == [SEVERITY]
    assert events[0]["event"] == "turn"
