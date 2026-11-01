"""A question about the places the last answer found stays on the community branch.

QA, 2026-10-20: "Which cities did the Woolsey Fire reach?" and then "How many
people live in those cities?". The Census offer is made on the community branch
only, and that branch was entered on `_asks_fire_community_question` over the
resolver's rewrite. The follow-up names no verb the rule knows, so the button
depended on how the rewrite happened to be worded - four rewrites of the same
question went three ways and one the other - and when it missed, the narrator
offered a lookup there was nothing to click for.
"""

from __future__ import annotations

import asyncio
import types

import pytest

from wildfire_agent import api
from wildfire_agent.contract import AnalysisContract
from wildfire_agent.conversation import AnalysisRef, ConversationContext, SubjectRef

WOOLSEY = "22343688"
QUESTION = "How many people live in those cities?"
#: One of the four rewrites the resolver gave for QUESTION. "intersection" is
#: not "intersect", and nothing else in it is a verb the keyword rule knows.
MISSED_REWRITE = (
    "For the places identified in the November 16, 2018 Woolsey Fire "
    "community-intersection analysis - Malibu, Calabasas, Los Angeles - provide "
    "the population (how many people live in each place)."
)


@pytest.fixture
def session(monkeypatch):
    """A session whose last turn answered which places a fire reached."""

    def make(
        *,
        operation: str = "community_intersection",
        event_id: str = WOOLSEY,
        variables: tuple[str, ...] = (),
    ) -> str:
        session_id = f"place-follow-up-{operation}-{event_id}"
        context = ConversationContext(
            active_subject=SubjectRef(type="fire_event", id=event_id, name="Woolsey Fire"),
            active_analysis=AnalysisRef(
                id=f"{event_id}:{operation}:2018-11-16",
                operation=operation,
                description=f"{operation} for Woolsey Fire on 2018-11-16",
            ),
            active_variables=list(variables),
        )
        monkeypatch.setitem(api._session_contexts, session_id, context)
        return session_id

    return make


def _contract(question: str = QUESTION, rewrite: str = MISSED_REWRITE) -> AnalysisContract:
    return AnalysisContract(original_request=question, resolved_request=rewrite)


def test_the_rewrite_that_missed_really_misses_the_keyword_rule():
    """The premise: without the session's memory this turn is not a community one."""
    assert not api._asks_fire_community_question(MISSED_REWRITE)
    assert not api._asks_fire_community_question(QUESTION)


def test_a_population_follow_up_continues_the_community_analysis(session):
    session_id = session()
    assert api._continues_community_analysis(session_id, _contract(), WOOLSEY, ())


def test_a_variable_only_the_resolver_recognised_counts(session):
    """"How wealthy are they" is an income question no keyword in the table
    names; the resolver's reading of it is what says so."""
    question = "How wealthy are they?"
    assert not api._asked_attributes(question)
    session_id = session()
    contract = _contract(question, question)
    assert not api._continues_community_analysis(session_id, contract, WOOLSEY, ())
    assert api._continues_community_analysis(
        session_id, contract, WOOLSEY, ("medianHouseholdIncome",)
    )


def test_a_question_about_something_else_does_not(session):
    session_id = session()
    contract = _contract("What was the wind like that day?", "Wind at the Woolsey Fire")
    assert not api._continues_community_analysis(session_id, contract, WOOLSEY, ())


def test_another_fire_does_not(session):
    session_id = session()
    assert not api._continues_community_analysis(session_id, _contract(), "24461771", ())


def test_after_a_life_cycle_answer_it_does_not(session):
    """"Those cities" has no referent when the last answer named none."""
    session_id = session(operation="fire_lifecycle")
    assert not api._continues_community_analysis(session_id, _contract(), WOOLSEY, ())


def test_a_session_with_no_history_does_not():
    assert not api._continues_community_analysis("place-follow-up-none", _contract(), WOOLSEY, ())


def _fire_plan(event_id: str = WOOLSEY):
    return types.SimpleNamespace(
        day="2018-11-16",
        dataset=types.SimpleNamespace(
            event_id=event_id,
            event_name="Woolsey Fire",
            time_start="2018-11-08",
            time_end="2018-11-16",
        ),
    )


def _render(monkeypatch, session_id: str, contract: AnalysisContract, text: str) -> list[bool]:
    """Run `_render_answer` with the renderer stubbed; report what it was told."""
    told: list[bool] = []

    async def fake_render(_contract, _plan, _body, outcome):
        told.append(outcome.continues_community)
        return
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(api, "plan_fire_raster", lambda _contract: _fire_plan())
    monkeypatch.setattr(api, "_render_fire_event", fake_render)

    async def drain():
        return [event async for event in api._render_answer(session_id, contract, api.MessageIn(text=text))]

    asyncio.run(drain())
    return told


def test_the_renderer_is_told_and_the_turn_is_remembered_as_one(session, monkeypatch):
    """Remembering it as a life-cycle turn would break the next one: the "Fetch
    it" that answers this turn's offer re-renders this contract, and it would no
    longer take the branch that does the fetch."""
    session_id = session()
    assert _render(monkeypatch, session_id, _contract(), QUESTION) == [True]
    assert api._session_contexts[session_id].active_analysis.operation == "community_intersection"


def test_approving_the_offer_it_made_still_fetches(session, monkeypatch):
    """"Fetch it" names no attribute. The question it answers does."""
    session_id = session()
    assert _render(monkeypatch, session_id, _contract(), "Fetch it") == [True]


def test_an_unrelated_follow_up_renders_as_before(session, monkeypatch):
    session_id = session()
    contract = _contract("What was the wind like that day?", "Wind at the Woolsey Fire")
    assert _render(monkeypatch, session_id, contract, contract.original_request) == [False]
    assert api._session_contexts[session_id].active_analysis.operation == "fire_lifecycle"
