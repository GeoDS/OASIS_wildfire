"""A severity question asked after the cities answer gets the severity answer.

Roadshow rehearsal, 2026-10-21, on the demo path: "What is the life cycle of the
Bobcat Fire?", then "What cities did this fire impact?" and "Fetch it", then
"How badly did the Bobcat fire burn between the first and last day?". The third
answer repeated the cities and their populations. The dNBR was computed - mean
0.383, the panel's data was in the snapshot - and then dropped, because the
community branch is checked before the analysis result and it was entered on
`_asks_fire_community_question` over the resolver's rewrite. That rewrite named
the previous "community-intersection analysis", as the resolver is told to do
for a follow-up, and "burned" is in nearly every question about a fire. In a
fresh session the rewrite is the question itself, so the same words worked.
"""

from __future__ import annotations

import asyncio
import dataclasses

import pytest

from wildfire_agent import api
from wildfire_agent.analyses import AnalysisMatch, choose
from wildfire_agent.contract import AnalysisContract
from wildfire_agent.conversation import AnalysisRef, ConversationContext, SubjectRef
from wildfire_agent.raster_layers import FireRasterPlan, RasterDataset

BOBCAT = "24461771"
QUESTION = "How badly did the Bobcat fire burn between the first and last day?"
#: The resolver's rewrite of QUESTION in the rehearsal session, verbatim.
REWRITE = (
    "For the Bobcat Fire, compare how much cumulative burned area was mapped on the "
    "first event day, September 4, 2020, versus the last event day, September 27, "
    "2020, and quantify the change to assess how extensively the fire burned. This "
    "modifies the previous community-intersection analysis, which used the September "
    "27, 2020 endpoint and the burned-area and active-fire label layers."
)
DNBR_SUMMARY = (
    "Across the Bobcat Fire mapped burned-area footprint, mean dNBR is 0.383 between "
    "2020-09-04 and 2020-09-27, which sits in the moderate range on the USGS severity "
    "scale."
)


def _result(spec) -> dict:
    """What the chosen analysis would return, named so a test can tell which ran."""
    return {
        "analysis_id": f"{spec.id}-test",
        "operation": spec.id,
        "title": f"Bobcat Fire {spec.title}",
        "summary": DNBR_SUMMARY if spec.id == "nbr_change" else f"{spec.title} answer",
        "formula": "NBR(2020-09-04) - NBR(2020-09-27)",
        "inputs": [{"date": "2020-09-04"}, {"date": "2020-09-27"}],
        "operations": [],
        "mask": "cumulative_burned_area",
        "layers": [],
        "caveats": [],
    }


def _bobcat_plan(_contract) -> FireRasterPlan:
    return FireRasterPlan(
        dataset=RasterDataset(
            dataset_id="bobcat-observed",
            variable="VIIRS_Day",
            event_id=BOBCAT,
            event_name="Bobcat Fire area",
            spatial_scope="Angeles NF",
            time_start="2020-09-04",
            time_end="2020-09-27",
            dates=["2020-09-04", "2020-09-27"],
        ),
        day="2020-09-27",
        presentation="observed fire activity",
        explanation="test",
    )


@pytest.fixture
def offline(monkeypatch):
    """Everything around the branch decision stubbed; the analysis is still
    chosen by the real registry from the real wording."""
    narrated: list[dict] = []

    def resolve(request, fire_plan, original=None):
        spec = choose(request, original)
        if spec is None:
            return None
        return AnalysisMatch(
            spec=dataclasses.replace(spec, execute=lambda _plan: _result(spec)), plan=None
        )

    async def no_subject(_name, _year):
        return None

    async def narrate(*, question, facts, fallback, expertise, preserve=(), keep=()):
        narrated.append(facts)
        return fallback

    monkeypatch.setattr(api, "plan_fire_raster", _bobcat_plan)
    monkeypatch.setattr(
        api,
        "render_fire_lifecycle",
        lambda _event, day: {
            "layers": [],
            "selected_metrics": {"active_pixels": 0, "cumulative_burned_km2": 529.0},
            "prediction_status": "status",
            "pixel_area_km2": 0.14,
            "lifecycle_analysis": None,
        },
    )
    monkeypatch.setattr(api, "resolve_analysis", resolve)
    monkeypatch.setattr(api, "event_supports_burned_area", lambda *_: True)
    monkeypatch.setattr(api, "_automatic_fire_subject", no_subject)
    monkeypatch.setattr(api, "burned_area_label_points", lambda *_: ())
    monkeypatch.setattr(api, "active_fire_label_points", lambda *_: ())
    monkeypatch.setattr(api, "communities_intersecting_burned_area", lambda *_a, **_k: None)
    monkeypatch.setattr(api, "communities_intersecting_active_fire", lambda *_a, **_k: None)
    monkeypatch.setattr(api, "communities_nearest_burned_area", lambda *_a, **_k: None)
    monkeypatch.setattr(api, "narrate", narrate)
    return narrated


def _after_the_cities_answer(monkeypatch, session_id: str, operation: str = "community_intersection"):
    """The session as it stood when the rehearsal asked the third question.

    The resolver named no place variable for it, so `active_variables` is empty
    - read back from the saved session's context.
    """
    context = ConversationContext(
        active_subject=SubjectRef(type="fire_event", id=BOBCAT, name="Bobcat Fire"),
        active_analysis=AnalysisRef(
            id=f"{BOBCAT}:{operation}:2020-09-27",
            operation=operation,
            description=f"{operation} for Bobcat Fire on 2020-09-27",
        ),
        active_variables=[],
        fetched={"census_acs": {"source": "U.S. Census Bureau"}},
    )
    monkeypatch.setitem(api._session_contexts, session_id, context)


def _answer(session_id: str, question: str, rewrite: str) -> ConversationContext:
    """Render one turn; return what the session remembers of it."""
    contract = AnalysisContract(original_request=question, resolved_request=rewrite)

    async def drain():
        return [
            event
            async for event in api._render_answer(session_id, contract, api.MessageIn(text=question))
        ]

    try:
        asyncio.run(drain())
        return api._session_contexts[session_id]
    finally:
        api._session_contexts.pop(session_id, None)
        api._fill_decisions.pop(session_id, None)
        api._pending_fills.pop(session_id, None)


def test_the_premise_only_the_rewrite_reads_as_a_community_question(monkeypatch):
    """Not the 10-20 session rule: no variable named and none in the words.
    The rewrite's inherited vocabulary is the whole of it."""
    assert api._asks_fire_community_question(REWRITE)
    assert not api._asks_fire_community_question(QUESTION)
    assert choose(REWRITE, QUESTION).id == "nbr_change"
    _after_the_cities_answer(monkeypatch, "severity-premise")
    contract = AnalysisContract(original_request=QUESTION, resolved_request=REWRITE)
    try:
        assert not api._continues_community_analysis("severity-premise", contract, BOBCAT, ())
    finally:
        api._session_contexts.pop("severity-premise", None)


def test_severity_after_the_cities_answer_is_answered_with_severity(offline, monkeypatch):
    """The reported failure."""
    _after_the_cities_answer(monkeypatch, "severity-after-cities")
    _answer("severity-after-cities", QUESTION, REWRITE)
    (facts,) = offline
    assert facts["message"] == DNBR_SUMMARY, "the cities answer was given instead"
    assert "places" not in facts


def test_another_analysis_after_the_cities_is_remembered_as_what_it_was(offline, monkeypatch):
    """The memory is decided by the same rule as the answer. A weather answer
    recorded as the cities would be taken up by the next "how many people live
    there?" as a follow-up to a cities answer that was never on screen."""
    _after_the_cities_answer(monkeypatch, "weather-after-cities")
    remembered = _answer(
        "weather-after-cities",
        "What was the weather like when it reached Monrovia?",
        "For the Bobcat Fire: fire weather conditions on September 27, 2020, when the "
        "burned area reached the cities identified in the previous "
        "community-intersection analysis (Monrovia, Duarte, Arcadia).",
    )
    (facts,) = offline
    assert facts["message"] == "fire weather answer"
    assert remembered.active_analysis.operation != "community_intersection"


def test_the_same_words_in_a_fresh_session_were_always_right(offline):
    """What the rehearsal saw when it opened a new session."""
    _answer("severity-fresh", QUESTION, QUESTION)
    (facts,) = offline
    assert facts["message"] == DNBR_SUMMARY


def test_approving_the_census_offer_still_answers_with_the_cities(offline, monkeypatch):
    """"Fetch it" re-renders the parked cities question; its words decide."""
    _after_the_cities_answer(monkeypatch, "severity-fetch-it")
    question = "What cities did this fire impact?"
    _answer("severity-fetch-it", question, question)
    (facts,) = offline
    assert "places" in facts


def test_cities_in_the_users_own_words_still_win(offline, monkeypatch):
    """Severity and cities in one question: the user named the places."""
    _after_the_cities_answer(monkeypatch, "severity-own-words")
    question = "How badly did it burn the cities?"
    _answer("severity-own-words", question, f"For the Bobcat Fire: {question}")
    (facts,) = offline
    assert "places" in facts


def test_a_new_date_for_the_cities_still_answers_with_the_cities(offline, monkeypatch):
    """The rewrite is still how a short follow-up reaches the community branch
    when its own words name no other analysis."""
    _after_the_cities_answer(monkeypatch, "severity-new-date")
    _answer(
        "severity-new-date",
        "And on September 15?",
        "For the Bobcat Fire: which cities had the burned area reached by "
        "September 15, 2020?",
    )
    (facts,) = offline
    assert "places" in facts
