"""Conversation-context resolution tests."""

import pytest

from wildfire_agent import conversation
from wildfire_agent.conversation import (
    AnalysisRef,
    ConversationContext,
    ConversationResolution,
    ConversationResolutionError,
    SubjectRef,
    TimeRef,
    resolve_turn,
)


class _ResolverStub:
    def __init__(self, result=None, error: Exception | None = None):
        self.result = result
        self.error = error

    async def ainvoke(self, _messages):
        if self.error:
            raise self.error
        return self.result


def _fire_context() -> ConversationContext:
    return ConversationContext(
        active_subject=SubjectRef(type="fire_event", id="24461771", name="Bobcat Fire"),
        active_time=TimeRef(selected="2020-09-18", start="2020-09-04", end="2020-09-27"),
        active_analysis=AnalysisRef(
            id="bobcat:lifecycle",
            operation="fire_lifecycle",
            description="fire lifecycle for Bobcat Fire on 2020-09-18",
        ),
    )


async def test_model_resolves_followup_and_backend_only_validates_subject(monkeypatch):
    modeled = ConversationResolution(
        relation="follow_up",
        standalone_request=("Compare NDVI_last on 2020-09-04 and 2020-09-27 inside the mapped BA."),
        inherited_subject=True,
        inherited_time=True,
        inherited_analysis=True,
        reason="The user refers to the first and last day of the active fire analysis.",
    )
    monkeypatch.setattr(conversation, "is_mock", lambda: False)
    monkeypatch.setattr(conversation, "structured", lambda _schema: _ResolverStub(modeled))

    resolution = await resolve_turn(
        "How did NDVI change from the first to the last day?", _fire_context()
    )

    assert resolution.relation == "follow_up"
    assert resolution.standalone_request.startswith("For Bobcat Fire (TS-SatFire event 24461771)")
    assert "NDVI_last" in resolution.standalone_request


async def test_model_new_request_decision_is_not_overridden_by_keywords(monkeypatch):
    modeled = ConversationResolution(
        relation="new_request",
        standalone_request="stale rewritten text",
        reason="The user explicitly changed subjects.",
    )
    monkeypatch.setattr(conversation, "is_mock", lambda: False)
    monkeypatch.setattr(conversation, "structured", lambda _schema: _ResolverStub(modeled))
    raw = "Show weather in Santa Barbara, not this fire."

    resolution = await resolve_turn(raw, _fire_context())

    assert resolution.relation == "new_request"
    assert resolution.standalone_request == raw
    assert resolution.inherited_subject is False


async def test_model_failure_does_not_fall_back_to_keyword_or_regex(monkeypatch):
    monkeypatch.setattr(conversation, "is_mock", lambda: False)
    monkeypatch.setattr(
        conversation,
        "structured",
        lambda _schema: _ResolverStub(error=RuntimeError("provider unavailable")),
    )

    with pytest.raises(ConversationResolutionError, match="no keyword or regex fallback"):
        await resolve_turn("Is there fire nearby?", _fire_context())


async def test_mock_provider_refuses_to_guess_followup_references(monkeypatch):
    monkeypatch.setattr(conversation, "is_mock", lambda: True)

    with pytest.raises(ConversationResolutionError, match="require a configured LLM"):
        await resolve_turn("What changed next?", _fire_context())


class TestFetchedDataAccumulates:
    """`last_result` holds one turn and is overwritten by the next.

    So data fetched three turns ago was gone by the time someone asked about it,
    and the session answered "no ACS data" while holding the figures. What an
    approved fetch returned belongs to the session, not to the turn that
    happened to trigger it.
    """

    def test_a_fetch_is_remembered_under_its_source(self):
        context = ConversationContext()
        context.remember_fetch("census_acs", {"places": [{"name": "Monrovia", "population": 37571}]})

        assert context.fetched["census_acs"]["places"][0]["population"] == 37571

    def test_a_later_fetch_from_another_source_does_not_evict_the_first(self):
        context = ConversationContext()
        context.remember_fetch("census_acs", {"places": [{"name": "Monrovia"}]})
        context.remember_fetch("portal_debris_flow", {"hazard_areas": 12})

        assert set(context.fetched) == {"census_acs", "portal_debris_flow"}

    def test_re_fetching_a_source_replaces_its_entry(self):
        """Two answers from one source is a contradiction, not a history."""
        context = ConversationContext()
        context.remember_fetch("census_acs", {"places": [{"name": "Monrovia"}]})
        context.remember_fetch("census_acs", {"places": [{"name": "Duarte"}]})

        assert context.fetched["census_acs"]["places"] == [{"name": "Duarte"}]

    def test_geometry_is_never_carried_into_the_prompt(self):
        """A place layer's GeoJSON is ~54 KB. Structured attributes are ~1.4 KB.
        Only the second belongs in a prompt that goes out on every turn."""
        context = ConversationContext()
        context.remember_fetch(
            "census_acs",
            {"places": [{"name": "Monrovia", "population": 37571}], "geojson": {"big": "x" * 1000}},
        )

        assert "geojson" not in context.fetched["census_acs"]

    def test_the_resolver_and_the_narrator_both_see_it(self):
        context = ConversationContext()
        context.remember_fetch("census_acs", {"places": [{"medianHouseholdIncome": 108295}]})

        payload = context.prompt_payload()
        assert payload["fetched"]["census_acs"]["places"][0]["medianHouseholdIncome"] == 108295

    def test_a_session_that_fetched_nothing_says_nothing(self):
        assert "fetched" not in ConversationContext().prompt_payload()


class TestAnExplicitFetchRequestIsAnAction:
    """"fetch acs data" is not small talk about the map.

    It was classified as discussion and answered "the analysis does not include
    ACS data" - a refusal to do the one thing the user asked for in plain words.
    The intent layer has to be able to name a source, not only choose between
    "recompute" and "explain".
    """

    def test_a_named_source_the_session_lacks_is_requested(self):
        context = ConversationContext()
        resolution = ConversationResolution(
            relation="follow_up", kind="discussion", standalone_request="fetch ACS data",
            requested_sources=["census_acs"], reason="r",
        )
        assert conversation.sources_to_fetch(resolution, context) == ("census_acs",)

    def test_a_source_already_fetched_is_not_fetched_again(self):
        """The session is holding it. Re-fetching spends a call to learn nothing."""
        context = ConversationContext()
        context.remember_fetch("census_acs", {"places": [{"population": 1}]})
        resolution = ConversationResolution(
            relation="follow_up", kind="discussion", standalone_request="check ACS data",
            requested_sources=["census_acs"], reason="r",
        )
        assert conversation.sources_to_fetch(resolution, context) == ()

    def test_a_source_nobody_offers_is_ignored(self):
        """The model may name anything. Only ids this deployment has are acted on."""
        context = ConversationContext()
        resolution = ConversationResolution(
            relation="follow_up", standalone_request="fetch parcel records",
            requested_sources=["county_parcels"], reason="r",
        )
        assert conversation.sources_to_fetch(resolution, context) == ()

    def test_asking_for_nothing_requests_nothing(self):
        resolution = ConversationResolution(
            relation="follow_up", standalone_request="which cities?", reason="r",
        )
        assert conversation.sources_to_fetch(resolution, ConversationContext()) == ()


class TestTheResolverNamesTheVariables:
    """A keyword gate decided what the narrator was allowed to know, and broke
    on the first synonym: "how wealthy are those places" matched no pattern, so
    income was stripped from the facts and the reply offered to fetch a figure
    the session was already holding.

    `resolve_turn` already reads the question with a model. Naming the variables
    is its job; the regex stays as a fast path and stops being authoritative.
    """

    def _resolution(self, variables):
        return ConversationResolution(
            relation="follow_up", standalone_request="x",
            requested_variables=variables, reason="r",
        )

    def test_a_named_variable_is_accepted(self):
        assert conversation.variables_to_lead(self._resolution(["medianHouseholdIncome"])) == (
            "medianHouseholdIncome",
        )

    def test_a_variable_this_fill_does_not_supply_is_dropped(self):
        """The model may name anything; only what the fill attaches is acted on."""
        assert conversation.variables_to_lead(self._resolution(["unemploymentRate"])) == ()

    def test_order_is_kept_and_duplicates_collapse(self):
        resolution = self._resolution(
            ["medianHouseholdIncome", "population", "medianHouseholdIncome"]
        )
        assert conversation.variables_to_lead(resolution) == (
            "medianHouseholdIncome",
            "population",
        )

    def test_naming_nothing_leads_with_nothing(self):
        assert conversation.variables_to_lead(self._resolution([])) == ()

    def test_the_prompt_lists_every_variable_the_fill_attaches(self):
        """A variable the model is never told about cannot be asked for."""
        from wildfire_agent.exposure import FETCHED_PROPERTIES
        from wildfire_agent.external_sources import KNOWN_VARIABLES

        assert set(KNOWN_VARIABLES) == set(FETCHED_PROPERTIES)
        prompt = conversation._resolver_prompt()
        for name in KNOWN_VARIABLES:
            assert name in prompt, name

    def test_the_synonym_that_broke_the_regex_is_in_the_prompt(self):
        """The wording that caused the false refusal is named for the model, so
        it is not left to guess that wealth means income."""
        assert "wealth" in conversation._resolver_prompt().lower()


class TestTheSessionCanSayWhatItHolds:
    """Asked "what fires do you have data for", the system answered about the
    fire currently on screen and said it could not enumerate the others.

    The catalogue exists and is served at /api/local-data/fire-events - the
    sidebar reads it. The conversation simply could not reach it, so a question
    about the archive was answered as a question about the map.
    """

    def _catalogue(self):
        return [
            {"name": "Bobcat Fire", "first_day": "2020-09-04", "last_day": "2020-09-27",
             "burned_area": True},
            {"name": "Thomas Fire", "first_day": "2017-12-04", "last_day": "2017-12-13",
             "burned_area": False},
        ]

    def test_the_archive_travels_in_the_prompt(self):
        context = ConversationContext()
        context.remember_archive(self._catalogue())
        payload = context.prompt_payload()
        assert [e["name"] for e in payload["archive"]] == ["Bobcat Fire", "Thomas Fire"]

    def test_an_event_without_burned_area_is_marked_as_such(self):
        """So the answer can say what each event can and cannot support, rather
        than listing names and letting the user find out by asking."""
        context = ConversationContext()
        context.remember_archive(self._catalogue())
        thomas = next(e for e in context.prompt_payload()["archive"] if e["name"] == "Thomas Fire")
        assert thomas["burned_area"] is False

    def test_an_empty_archive_is_absent_rather_than_empty(self):
        """An empty list reads as "we looked and there is nothing", which is a
        different claim from "this was never loaded"."""
        assert "archive" not in ConversationContext().prompt_payload()
