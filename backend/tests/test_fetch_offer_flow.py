"""What a turn does while a fetch offer is waiting for an answer.

`test_api.TestFetchConsent` covers how a reply is *classified*. These cover where
the turn then *goes*: a decision re-runs the parked question with it applied,
a new question is answered and drops the offer, and noise asks again.
"""

from __future__ import annotations

import asyncio
import types

from wildfire_agent import api
from wildfire_agent.contract import AnalysisContract
from wildfire_agent.conversation import ConversationResolutionError


def _pending() -> api.PendingFill:
    return api.PendingFill(
        offer={
            "source_id": "census_acs",
            "source": "s",
            "dataset": "d",
            "geography": "g",
            "closes": ("population count",),
            "remaining": (),
        },
        capability_id="places",
    )


class _ParkedGraph:
    """A checkpointer holding the previous, already-answered question."""

    def __init__(self, contract: AnalysisContract):
        self.contract = contract

    async def aget_state(self, _config):
        return types.SimpleNamespace(values={"contract": self.contract}, next=(), tasks=())


def _turn(monkeypatch, session_id: str, text: str) -> dict:
    """Run one turn; report what was re-rendered, resolved and left behind."""
    parked = AnalysisContract(original_request="Which cities did the Bobcat Fire reach?")
    assert parked.ready_for_planning
    monkeypatch.setattr(api, "_graph", _ParkedGraph(parked))
    rendered: list[str] = []
    resolved: list[str] = []

    async def fake_render(_session_id, _contract, body):
        rendered.append(body.text)
        return
        yield  # pragma: no cover - makes this an async generator

    async def fake_resolve(text, _context):
        resolved.append(text)
        # Stop the turn here: what matters is that the pipeline was reached.
        raise ConversationResolutionError("stop")

    monkeypatch.setattr(api, "_render_answer", fake_render)
    monkeypatch.setattr(api, "resolve_turn", fake_resolve)
    api._pending_fills[session_id] = _pending()

    async def drain():
        return [event async for event in api._run(session_id, api.MessageIn(text=text))]

    try:
        events = asyncio.run(drain())
        decisions = api._fill_decisions.get(session_id)
        return {
            "rendered": rendered,
            "resolved": resolved,
            "offer_still_open": session_id in api._pending_fills,
            "settled": decisions is not None and decisions.settled("census_acs"),
            "asked_again": any(event["event"] == "clarification" for event in events),
        }
    finally:
        api._pending_fills.pop(session_id, None)
        api._fill_decisions.pop(session_id, None)
        api._session_contexts.pop(session_id, None)


def test_a_new_question_asked_while_an_offer_waits_is_answered(monkeypatch):
    """The reported failure: asked something else while the Census offer stood,
    the user got the cities answer and the same offer again, and their question
    was never answered."""
    question = "Are there comparable fire events similar to this one?"
    turn = _turn(monkeypatch, "offer-flow-new-question", question)
    assert turn["rendered"] == [], "the parked question was answered again instead"
    assert turn["resolved"] == [question]


def test_moving_on_drops_the_offer_without_counting_it_as_a_refusal(monkeypatch):
    """Not fetched, not refused: the same gap can be offered again later, and a
    stray "yes" two questions on cannot fetch for a question long gone."""
    turn = _turn(monkeypatch, "offer-flow-drop", "Are there comparable fire events?")
    assert not turn["offer_still_open"]
    assert not turn["settled"]
    assert not turn["asked_again"]


def test_a_decision_still_reruns_the_parked_question(monkeypatch):
    turn = _turn(monkeypatch, "offer-flow-decision", "Fetch it")
    assert turn["rendered"] == ["Fetch it"]
    assert turn["resolved"] == []
    assert turn["settled"]


def test_noise_asks_again_and_keeps_the_offer(monkeypatch):
    turn = _turn(monkeypatch, "offer-flow-noise", "hmm")
    assert turn["rendered"] == [] and turn["resolved"] == []
    assert turn["asked_again"]
    assert turn["offer_still_open"]
