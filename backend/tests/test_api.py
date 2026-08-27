"""API smoke tests. Stub LLM, no API key required."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from wildfire_agent import api
from wildfire_agent.contract import AnalysisContract, ResolvedLocation
from wildfire_agent.graph.models import (
    ClarificationBatch,
    ClarificationInterpretation,
    CompiledTask,
    RequirementUnderstanding,
)
from wildfire_agent.planning.models import LayerResult
from wildfire_agent.planning.planner import PlanProposal

from .test_graph import (
    _batch,
    _compiled,
    _interpretation,
    _plan_proposal,
    _StubRunnable,
    _understanding,
)


@pytest.fixture
def client(monkeypatch):
    # Tests never touch the network: slow, flaky, and rude to Nominatim.
    async def fake_resolve(_query, *, buffer_km=None):
        return ResolvedLocation(
            display_name="Altadena, Los Angeles County, California, USA",
            center=(-118.1312, 34.1897),
            buffer_km=buffer_km if buffer_km is not None else 25.0,
            bbox=(-118.28, 34.12, -117.96, 34.32),
            geocoder="nominatim",
        )

    monkeypatch.setattr("wildfire_agent.graph.nodes.resolve_location", fake_resolve)

    responses = {
        RequirementUnderstanding: _understanding(),
        CompiledTask: _compiled(),
        ClarificationBatch: _batch(),
        ClarificationInterpretation: _interpretation(),
        PlanProposal: _plan_proposal(),
    }
    monkeypatch.setattr(
        "wildfire_agent.graph.nodes.structured",
        lambda schema, **_k: _StubRunnable(responses[schema]),
    )
    monkeypatch.setattr(
        "wildfire_agent.planning.planner.structured",
        lambda schema, **_k: _StubRunnable(_plan_proposal()),
    )
    monkeypatch.setattr(api, "check_llm_ready", lambda: (True, "stub"))
    return TestClient(api.app)


def _events(response) -> list[tuple[str, dict]]:
    out, event = [], None
    for line in response.text.splitlines():
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: ") and event:
            out.append((event, json.loads(line[6:])))
    return out


def test_fire_community_answer_leads_with_plain_language():
    intersections = LayerResult(
        capability_id="burned_area_intersecting_place_boundaries",
        title="places",
        hazard_object="exposure",
        geometry_type="Polygon",
        caveat="technical method detail",
        feature_count=3,
        source="test",
        as_of="2020-09-18",
        geojson={
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": None, "properties": {"name": name}}
                for name in ("Monrovia", "Duarte", "Arcadia")
            ],
        },
    )
    active = LayerResult(
        capability_id="active_fire_intersecting_place_boundaries",
        title="active places",
        hazard_object="exposure",
        geometry_type="Polygon",
        caveat="active signal detail",
        feature_count=1,
        source="test",
        as_of="2020-09-18",
        geojson={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": None,
                    "properties": {"name": "Glendale", "labelPixelCount": 4},
                }
            ],
        },
    )

    message = api._fire_community_message("Bobcat Fire", intersections, None, active)

    assert message.startswith(
        "By September 18, 2020, the mapped burned area of the Bobcat Fire had reached parts"
    )
    assert "Monrovia, Duarte, Arcadia" in message
    assert "TS-SatFire" not in message
    assert "Census" not in message
    assert "whole cities" in message
    assert "Glendale (4 pixels)" in message
    assert "may not belong to the Bobcat Fire" in message


def test_taxonomy_exposes_enums_so_frontend_never_hardcodes_them(client):
    body = client.get("/api/taxonomy").json()
    assert len(body["intents"]) == 5
    assert len(body["expertise"]) == 3
    assert len(body["roles"]) == 8
    # 14 since post-fire debris flow was added: a distinct cascading hazard
    # with its own variables, not a facet of the fire itself.
    assert len(body["hazard_objects"]) == 14
    # Coverage is derived from the capability registry, not restated in the
    # taxonomy - one source of truth for what this deployment can serve.
    assert body["hazard_objects"]["active_fire"]["covered_by"] == [
        "historical_fire_perimeters",
        "official_fire_perimeters",
        "satellite_hotspots",
    ]
    assert body["hazard_objects"]["fire_spread"]["covered_by"] == []


def test_contract_json_schema_is_servable(client):
    schema = client.get("/api/schema/contract").json()
    assert "ready_for_planning" in schema["properties"]


def test_message_stream_auto_selects_fire_evidence_without_product_question(client):
    session_id = client.post("/api/sessions").json()["session_id"]

    first = client.post(
        f"/api/sessions/{session_id}/messages",
        json={"text": "Where are the active fires near Altadena?"},
    )
    events = _events(first)
    kinds = [e for e, _ in events]
    assert "stage" in kinds
    assert "clarification" not in kinds
    assert "fire_data" in kinds
    done = next(d for e, d in events if e == "done")
    assert done["ready_for_planning"] is True
    assert done["filled_ratio"] == 1.0


def test_unknown_session_is_rejected(client):
    resp = client.post("/api/sessions/nope/messages", json={"text": "hi"})
    assert resp.status_code == 404


def test_session_archive_lists_restores_renames_and_deletes(client):
    session_id = client.post("/api/sessions").json()["session_id"]
    snapshot = {
        "status": "complete",
        "messages": [{"role": "user", "content": "Show the Bobcat Fire lifecycle"}],
        "layers": [],
    }
    saved = client.put(
        f"/api/sessions/{session_id}/snapshot",
        json={"snapshot": snapshot},
    )
    assert saved.status_code == 200

    listed = client.get("/api/sessions").json()["sessions"]
    archived = next(item for item in listed if item["id"] == session_id)
    assert archived["message_count"] == 1
    assert archived["preview"] == "Show the Bobcat Fire lifecycle"

    renamed = client.patch(
        f"/api/sessions/{session_id}",
        json={"title": "Bobcat lifecycle"},
    )
    assert renamed.status_code == 200
    restored = client.get(f"/api/sessions/{session_id}").json()
    assert restored["title"] == "Bobcat lifecycle"
    assert restored["snapshot"] == snapshot

    assert client.delete(f"/api/sessions/{session_id}").status_code == 200
    assert client.get(f"/api/sessions/{session_id}").status_code == 404


def test_a_failure_after_the_graph_still_reaches_the_client(client, monkeypatch):
    """Rendering runs outside the graph loop; its failures must not kill the stream.

    An exception raised there used to escape the SSE generator, so the browser
    received a truncated chunked response and could only report a bare network
    error with nothing to act on.
    """

    def boom(*_args, **_kwargs):
        raise RuntimeError("date must be one of the dates advertised by the event")

    monkeypatch.setattr(api, "render_fire_lifecycle", boom)
    monkeypatch.setattr(api, "plan_fire_raster", lambda _contract: _exploding_plan())

    session = client.post("/api/sessions").json()["session_id"]
    response = client.post(
        f"/api/sessions/{session}/messages",
        json={"text": "Show the lifecycle of the Bobcat Fire on 2020-09-18."},
    )
    events = _events(response)
    names = [name for name, _ in events]
    assert "error" in names, f"stream ended without telling the client why: {names}"
    message = next(payload for name, payload in events if name == "error")
    assert "dates advertised" in message["message"]
    assert message["type"] == "RuntimeError"


def _exploding_plan():
    """A plan object whose only job is to route the run into the failing renderer."""
    from wildfire_agent.raster_layers import FireRasterPlan, RasterDataset

    return FireRasterPlan(
        dataset=RasterDataset(
            dataset_id="bobcat-observed",
            variable="VIIRS_Day",
            event_id="24461771",
            event_name="Bobcat Fire area",
            spatial_scope="Angeles NF",
            time_start="2020-09-04",
            time_end="2020-09-27",
            dates=["2020-09-18"],
        ),
        day="2020-09-18",
        presentation="observed fire activity",
        explanation="test",
    )


# --- the orchestration branches of _run -------------------------------------
#
# These pin the behaviour of the stream itself rather than of any one analysis,
# because that orchestration is about to be decomposed and its branches are what
# a refactor can silently drop.


def test_every_turn_announces_its_kind_before_any_payload(client):
    """The `turn` event tells the browser whether to expect a new map.

    It has to arrive first: a consumer that clears on `turn` and then receives
    layers has one ordering, and one that receives layers first has a flash of
    the previous answer under the new question.
    """
    session_id = client.post("/api/sessions").json()["session_id"]
    events = _events(
        client.post(
            f"/api/sessions/{session_id}/messages",
            json={"text": "Where are the active fires near Altadena?"},
        )
    )
    assert events[0][0] == "turn"
    # The kind, not the whole payload: `turn` also carries `subject_changed`,
    # and pinning the dict made a contract addition look like a regression.
    assert events[0][1]["kind"] == "analysis"
    assert "subject_changed" in events[0][1]


def test_a_discussion_turn_answers_without_running_the_pipeline(client, monkeypatch):
    """No stage, no layer, no contract - the map on screen is the answer's subject."""
    from wildfire_agent.conversation import ConversationResolution

    async def fake_resolve(text, _context):
        return ConversationResolution(
            relation="follow_up",
            kind="discussion",
            standalone_request=text,
            reason="test",
        )

    async def fake_discuss(**kwargs):
        return "NDVI measures vegetation greenness."

    monkeypatch.setattr(api, "resolve_turn", fake_resolve)
    monkeypatch.setattr(api, "discuss", fake_discuss)

    session_id = client.post("/api/sessions").json()["session_id"]
    events = _events(
        client.post(
            f"/api/sessions/{session_id}/messages",
            json={"text": "what does that mean?"},
        )
    )
    kinds = [name for name, _ in events]
    assert kinds == ["turn", "summary"]
    assert events[0][1]["kind"] == "discussion"
    # Discussion draws nothing, so there is never anything to replace.
    assert events[0][1]["subject_changed"] is False
    assert events[1][1]["text"] == "NDVI measures vegetation greenness."
    # Nothing was drawn, so nothing may claim to have been.
    assert "layer" not in kinds
    assert "done" not in kinds


def test_a_discussion_failure_is_reported_rather_than_answered_anyway(client, monkeypatch):
    from wildfire_agent.conversation import ConversationResolution

    async def fake_resolve(text, _context):
        return ConversationResolution(
            relation="follow_up", kind="discussion", standalone_request=text, reason="test"
        )

    async def boom(**kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(api, "resolve_turn", fake_resolve)
    monkeypatch.setattr(api, "discuss", boom)

    session_id = client.post("/api/sessions").json()["session_id"]
    events = _events(client.post(f"/api/sessions/{session_id}/messages", json={"text": "why?"}))
    kinds = [name for name, _ in events]
    assert kinds == ["turn", "error"]
    assert "RuntimeError" == next(d for e, d in events if e == "error")["type"]


def test_a_resolution_failure_never_clears_the_map(client, monkeypatch):
    """If the turn cannot even be resolved, no `turn` event is owed and none is sent.

    A `turn: analysis` here would tell the browser to throw away a perfectly good
    result before finding out that nothing is coming to replace it.
    """
    from wildfire_agent.conversation import ConversationResolutionError

    async def fake_resolve(_text, _context):
        raise ConversationResolutionError("could not resolve")

    monkeypatch.setattr(api, "resolve_turn", fake_resolve)
    session_id = client.post("/api/sessions").json()["session_id"]
    events = _events(
        client.post(f"/api/sessions/{session_id}/messages", json={"text": "and there?"})
    )
    assert [name for name, _ in events] == ["error"]


def test_the_completed_turn_is_committed_to_session_memory(client):
    """A later follow-up is only possible because the finished turn was recorded."""
    session_id = client.post("/api/sessions").json()["session_id"]
    client.post(
        f"/api/sessions/{session_id}/messages",
        json={"text": "Where are the active fires near Altadena?"},
    )
    context = api._session_contexts[session_id]
    assert context.active_subject is not None
    assert context.last_result is not None
    assert [turn.role for turn in context.recent_turns] == ["user", "agent"]


def test_a_message_cannot_be_unbounded(client):
    """Every message is forwarded to a billed provider, so length is a cost.

    The endpoint takes no credentials and the pipeline makes several provider
    calls per turn, so an unbounded field turns one request into an unbounded
    bill. A real question is a sentence; the cap is far above that.
    """
    session_id = client.post("/api/sessions").json()["session_id"]
    response = client.post(
        f"/api/sessions/{session_id}/messages",
        json={"text": "x" * 50_000},
    )
    assert response.status_code == 422

    ok = client.post(
        f"/api/sessions/{session_id}/messages",
        json={"text": "Where are the active fires near Altadena?"},
    )
    assert ok.status_code == 200


# --- offering an external fetch before making it --------------------------


def test_a_missing_variable_is_offered_rather_than_fetched_silently(client, monkeypatch):
    """Going to an outside source is the user's decision, so it is asked first.

    The offer also names what will still be missing afterwards: approving a
    fetch is not approving "the gap is now closed".
    """
    from wildfire_agent.exposure import offer_for

    offer = offer_for("exposure", ("population count", "building footprints"))
    assert offer is not None
    assert offer["closes"] == ("population count",)
    assert "building footprints" in offer["remaining"]


def test_an_offer_that_would_close_nothing_is_never_made():
    """Asking to authorise a fetch that cannot help wastes the one question."""
    from wildfire_agent.exposure import offer_for

    assert offer_for("exposure", ("building footprints", "WUI boundary")) is None
    assert offer_for("exposure", ()) is None


def test_the_pending_offer_prompt_names_the_source_and_what_it_leaves_behind():
    offer = {
        "source": "U.S. Census Bureau, American Community Survey",
        "dataset": "ACS 5-year estimates, 2020-2024",
        "geography": "Census place",
        "closes": ("population count",),
        "remaining": ("WUI boundary",),
    }
    pending = api.PendingFill(offer=offer, capability_id="places")
    payload = pending.as_clarification()

    assert payload["type"] == "clarification"
    assert "American Community Survey" in payload["preamble"]
    assert "WUI boundary" in payload["preamble"]
    labels = [o["label"] for o in payload["questions"][0]["options"]]
    assert labels == ["Fetch it", "Answer without it"]
    # A free-text answer here would be ambiguous about consent.
    assert payload["questions"][0]["allow_free_text"] is False


def test_declining_the_offer_is_respected(client, monkeypatch):
    """"Answer without it" must not be read as anything but a refusal."""
    offer = {
        "source": "s", "dataset": "d", "geography": "g",
        "closes": ("population count",), "remaining": (),
    }
    pending = api.PendingFill(offer=offer, capability_id="places")
    assert "Answer without it".casefold() != pending.accept.casefold()
    assert "Fetch it".casefold() == pending.accept.casefold()


class TestFetchConsent:
    """Reading consent out of a reply, without reading it out of a refusal."""

    def _pending(self):
        return api.PendingFill(
            offer={
                "source": "s", "dataset": "d", "geography": "g",
                "closes": ("population count",), "remaining": (),
            },
            capability_id="places",
        )

    def test_the_option_label_on_its_own_is_agreement(self):
        assert self._pending().decide("Fetch it") is True

    def test_a_label_appended_to_an_existing_draft_is_still_agreement(self):
        """Picking an option appends to the composer rather than sending, and
        several answers join with "; ". An exact match would miss this."""
        assert self._pending().decide("official perimeters; Fetch it") is True

    def test_the_decline_label_is_never_read_as_consent(self):
        """"Answer without it" names the data too, so a naive containment check
        on the accept label could see agreement in a plain refusal."""
        pending = self._pending()
        assert pending.decide("Answer without it") is False
        assert pending.decide("please Answer without it") is False

    def test_plain_yes_and_no_are_understood(self):
        pending = self._pending()
        assert pending.decide("yes") is True
        assert pending.decide("Go ahead") is True
        assert pending.decide("no") is False
        assert pending.decide("skip") is False

    def test_an_unclear_answer_is_neither_agreement_nor_refusal(self):
        """The safe default for "did they agree" is to ask again.

        Defaulting to fetch would act without consent; defaulting to decline
        would silently ignore an answer the user believes they gave.
        """
        pending = self._pending()
        for reply in ("what does that cost?", "", "   ", "tell me more first"):
            assert pending.decide(reply) is None


def test_the_natural_phrasings_of_a_community_question_are_recognised():
    """"Which cities did it reach" is how people ask this, and it matched nothing.

    The verb list had intersect/closest/near/affect/impact but not reach, burn,
    hit or overlap, so the most natural wording fell through to the lifecycle
    answer and the reply said the analysis could not identify any cities.
    """
    from wildfire_agent.api import _asks_fire_community_question as asks

    for phrasing in (
        "Which cities did the Bobcat Fire reach by 2020-09-27?",
        "What towns burned?",
        "Which communities did it hit?",
        "What cities overlap the burn?",
        "Which cities were closest to this fire?",
    ):
        assert asks(phrasing), phrasing

    # Still not a community question: no place word at all.
    assert not asks("Show the lifecycle of the Bobcat Fire on 2020-09-18")
    assert not asks("How severe was the burn?")


def test_approved_data_reaches_the_answer_not_only_the_popup():
    """A fetch the user authorised has to change what they are told.

    The attributes land on the layer, so they are visible in a popup - but the
    reply is what most readers see, and leaving it unchanged makes the approval
    look ignored.
    """
    from wildfire_agent import api as api_module

    layer = LayerResult(
        capability_id="burned_area_intersecting_place_boundaries",
        title="places",
        hazard_object="exposure",
        geometry_type="Polygon",
        caveat="c",
        feature_count=2,
        source="s",
        as_of="2020-09-27",
        geojson={
            "type": "FeatureCollection",
            "features": [
                {"properties": {"name": "Monrovia", "population": 37571}},
                {"properties": {"name": "Duarte", "population": 22184}},
            ],
        },
    )
    text = api_module._place_population(layer)
    assert "Monrovia 37,571" in text
    assert "59,755 in total" in text
    # The share and the population sit side by side, which invites the product.
    assert "not for the area that burned" in text


def test_no_population_sentence_before_any_fetch():
    """Before approval there is nothing to report, and silence is correct."""
    from wildfire_agent import api as api_module

    layer = LayerResult(
        capability_id="burned_area_intersecting_place_boundaries",
        title="places",
        hazard_object="exposure",
        geometry_type="Polygon",
        caveat="c",
        feature_count=1,
        source="s",
        as_of="2020-09-27",
        geojson={"type": "FeatureCollection", "features": [{"properties": {"name": "Monrovia"}}]},
    )
    assert api_module._place_population(layer) == ""


class TestTheFetchQuestionIsAskedOnce:
    """A settled decision is not a question any more.

    The offer was re-derived on every render from `missing_variables`, a static
    taxonomy table that cannot see either the values already fetched or the
    answer the user already gave. So the same question came back after consent
    had been given and acted on. A decision belongs to the session.
    """

    def _offer(self, source_id: str = "census_acs"):
        return {
            "source_id": source_id,
            "hazard_object": "exposure",
            "source": "s",
            "dataset": "d",
            "geography": "g",
            "closes": ("population count",),
            "remaining": (),
        }

    def test_an_approval_is_recorded_against_the_source(self):
        decisions = api.FillDecisions()
        pending = api.PendingFill(offer=self._offer(), capability_id="places")
        decisions.record(pending.key, True)

        assert decisions.approved(pending.key) is True
        assert decisions.settled(pending.key) is True

    def test_an_approved_source_is_not_offered_again(self):
        decisions = api.FillDecisions()
        pending = api.PendingFill(offer=self._offer(), capability_id="places")
        decisions.record(pending.key, True)

        assert decisions.should_ask(pending.key) is False

    def test_a_refusal_is_not_re_asked_either(self):
        """Re-asking a decline is the same failure wearing the other answer: it
        overrides a "no" by attrition."""
        decisions = api.FillDecisions()
        pending = api.PendingFill(offer=self._offer(), capability_id="places")
        decisions.record(pending.key, False)

        assert decisions.should_ask(pending.key) is False
        assert decisions.approved(pending.key) is False

    def test_an_unrelated_source_is_still_asked_about(self):
        """Consent is per source, not a blanket permission to reach the network."""
        decisions = api.FillDecisions()
        decisions.record(api.PendingFill(offer=self._offer(), capability_id="p").key, True)
        other = api.PendingFill(offer=self._offer("portal_debris_flow"), capability_id="q")

        assert decisions.should_ask(other.key) is True

    def test_a_fresh_session_asks(self):
        decisions = api.FillDecisions()
        assert decisions.should_ask(api.PendingFill(offer=self._offer(), capability_id="p").key)


class TestTheFetchQuestionIsOnlyAskedWhenItChangesTheAnswer:
    """Population is printed on one branch of the community message only.

    On every other branch the figures are fetched, attached, and never
    mentioned, so the question buys the user nothing and costs them a network
    call they were asked to authorise.
    """

    def _places(self, capability_id: str, count: int = 3) -> LayerResult:
        return LayerResult(
            capability_id=capability_id,
            title="places",
            hazard_object="exposure",
            geometry_type="Polygon",
            caveat="c",
            feature_count=count,
            source="test",
            as_of="2020-09-27",
            geojson={"type": "FeatureCollection", "features": []},
        )

    def test_the_burned_area_intersection_prints_the_figures(self):
        layer = self._places("burned_area_intersecting_place_boundaries")
        assert api._answer_reads_population(layer) is True

    def test_the_perimeter_intersection_does_not(self):
        """The fallback swaps this layer in when no burned-area points were
        mapped, and its sentence never mentions population."""
        layer = self._places("place_boundaries_intersecting_fire_perimeter")
        assert api._answer_reads_population(layer) is False

    def test_a_layer_with_no_places_does_not(self):
        layer = self._places("burned_area_intersecting_place_boundaries", count=0)
        assert api._answer_reads_population(layer) is False

    def test_no_layer_at_all_does_not(self):
        assert api._answer_reads_population(None) is False


class TestFetchedAttributesReachTheFacts:
    """Whatever the fetch attached to the layer, the facts must carry.

    The ACS fill attaches seven variables. The answer sentence prints one -
    population - and the rest went onto the map and nowhere else. So a later
    question about income was answered "the analysis does not include ACS data"
    by a turn that was sitting on the income figure. The fact corpus is what
    `narrate` and `discuss` both read, so that is where the fetch has to land.
    """

    def _enriched(self) -> LayerResult:
        return LayerResult(
            capability_id="burned_area_intersecting_place_boundaries",
            title="places",
            hazard_object="exposure",
            geometry_type="Polygon",
            caveat="c",
            feature_count=2,
            source="test",
            as_of="2020-09-27",
            geojson={
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": None,
                        "properties": {
                            "name": "Monrovia",
                            "geoid": "0648648",
                            "labelSharePercent": 49.7,
                            "population": 37571,
                            "medianHouseholdIncome": 108295,
                            "housingUnits": 14855,
                        },
                    },
                    {
                        "type": "Feature",
                        "geometry": None,
                        "properties": {
                            "name": "Duarte",
                            "geoid": "0619990",
                            "labelSharePercent": 18.0,
                            "population": 22184,
                            "medianHouseholdIncome": 95536,
                            "housingUnits": 7832,
                        },
                    },
                ],
            },
        )

    def test_income_reaches_the_facts_even_though_no_sentence_prints_it(self):
        facts = api._place_facts(self._enriched())
        assert [p["name"] for p in facts] == ["Monrovia", "Duarte"]
        assert facts[0]["medianHouseholdIncome"] == 108295
        assert facts[1]["housingUnits"] == 7832

    def test_a_variable_nobody_thought_to_whitelist_still_arrives(self):
        """A new ACS column must not need an edit here to become answerable."""
        layer = self._enriched()
        layer.geojson["features"][0]["properties"]["someNewVariable"] = 7
        assert api._place_facts(layer)[0]["someNewVariable"] == 7

    def test_an_unenriched_layer_carries_what_it_has(self):
        layer = self._enriched()
        for feature in layer.geojson["features"]:
            feature["properties"].pop("medianHouseholdIncome")
        facts = api._place_facts(layer)
        assert "medianHouseholdIncome" not in facts[0]
        assert facts[0]["labelSharePercent"] == 49.7

    def test_no_layer_is_no_facts(self):
        assert api._place_facts(None) == []


class TestEveryFetchedFigureCounts:
    """The fill attaches seven variables; only population was recognised.

    So a reply that answered an income question with income figures still had
    nothing the check would accept, and was thrown away for a fallback about
    which cities were reached.
    """

    def _enriched(self) -> LayerResult:
        return LayerResult(
            capability_id="burned_area_intersecting_place_boundaries",
            title="places", hazard_object="exposure", geometry_type="Polygon",
            caveat="c", feature_count=1, source="t", as_of="2020-09-27",
            geojson={"type": "FeatureCollection", "features": [{
                "type": "Feature", "geometry": None,
                "properties": {
                    "name": "Monrovia", "geoid": "0648648",
                    "labelSharePercent": 49.7,
                    "population": 37571, "medianHouseholdIncome": 108295,
                    "housingUnits": 14855, "medianAge": 41.0,
                },
            }]},
        )

    def test_income_is_an_authorised_figure(self):
        assert "108,295" in api._authorised_figures(self._enriched())

    def test_population_still_is(self):
        assert "37,571" in api._authorised_figures(self._enriched())

    def test_a_figure_the_fill_did_not_attach_is_not_claimed(self):
        """The area share is computed locally. Requiring it would let a reply
        that fetched nothing look like one that did."""
        assert "49.7" not in api._authorised_figures(self._enriched())
        assert "50" not in api._authorised_figures(self._enriched())

    def test_an_unfetched_layer_authorises_nothing(self):
        layer = self._enriched()
        layer.geojson["features"][0]["properties"] = {"name": "Monrovia", "labelSharePercent": 49.7}
        assert api._authorised_figures(layer) == ()


class TestAPlaceAttributeQuestionReachesTheFill:
    """"What's the population of Santa Barbara" had no path to ACS at all.

    The fill was wired only to the fire-community branch. A city with no fire
    renders through `_render_city_context`, whose subject layer carries a GEOID
    and the `exposure` hazard object - everything the fill needs - and which
    never called it. So the question was refused by a turn standing on the
    plumbing that answers it.
    """

    def test_a_population_question_asks_for_the_fill(self):
        assert api._asks_place_attribute_question("what's the population in santa barbara CA")

    def test_income_housing_and_residents_all_count(self):
        for text in (
            "median household income there",
            "how many housing units",
            "who lives in Altadena",
            "how many residents",
            "what's the age structure",
        ):
            assert api._asks_place_attribute_question(text), text

    def test_a_weather_question_does_not(self):
        """Population changes nothing about a weather answer, so it is not
        worth stopping the user to authorise a fetch."""
        for text in (
            "how is the weather at altadena",
            "is there a fire nearby",
            "show the lifecycle of the Bobcat Fire",
        ):
            assert not api._asks_place_attribute_question(text), text


class TestTheAskedVariableLeads:
    """A fetch attaches seven variables and the reply recited all seven in
    storage order, so a question about income was answered starting with
    population. Answer what was asked first; the rest is support, not filler.
    """

    def test_the_question_names_which_variable_it_wants(self):
        assert api._asked_attributes("what's the population in santa barbara") == ("population",)
        assert api._asked_attributes("median household income there") == ("medianHouseholdIncome",)
        assert api._asked_attributes("how many housing units") == ("housingUnits",)

    def test_two_variables_keep_the_order_they_were_asked_in(self):
        assert api._asked_attributes("income and population") == (
            "medianHouseholdIncome",
            "population",
        )

    def test_wording_that_means_population_without_saying_it(self):
        for text in ("who lives there", "how many residents", "how many people"):
            assert api._asked_attributes(text) == ("population",), text

    def test_a_question_about_none_of_them_asks_for_none(self):
        assert api._asked_attributes("how is the weather at altadena") == ()

    def test_the_predicate_and_the_list_cannot_drift(self):
        """One definition decides both "is this worth a fetch" and "what leads"."""
        for text in ("what's the population", "the weather", "median age"):
            assert api._asks_place_attribute_question(text) == bool(api._asked_attributes(text))

    def test_the_asked_variable_is_first_in_the_facts(self):
        layer = LayerResult(
            capability_id="subject_city_boundary", title="t", hazard_object="exposure",
            geometry_type="Polygon", caveat="c", feature_count=1, source="s", as_of="2025-01-01",
            geojson={"type": "FeatureCollection", "features": [{
                "type": "Feature", "geometry": None, "properties": {
                    "name": "Santa Barbara", "population": 88665,
                    "housingUnits": 37154, "medianHouseholdIncome": 96570}}]},
        )
        keys = list(api._place_facts(layer, lead=("medianHouseholdIncome",))[0])
        assert keys[0] == "medianHouseholdIncome"
        # Nothing is dropped - the rest still travels, just behind the answer.
        assert set(keys) == {"name", "population", "housingUnits", "medianHouseholdIncome"}

    def test_no_lead_keeps_the_layer_order(self):
        layer = LayerResult(
            capability_id="subject_city_boundary", title="t", hazard_object="exposure",
            geometry_type="Polygon", caveat="c", feature_count=1, source="s", as_of="2025-01-01",
            geojson={"type": "FeatureCollection", "features": [{
                "type": "Feature", "geometry": None,
                "properties": {"name": "SB", "population": 1}}]},
        )
        assert list(api._place_facts(layer)[0]) == ["name", "population"]


class TestOnlyTheAskedVariablesTravelToTheNarrator:
    """Putting all seven fetched variables in the narration facts made the
    narrator account for all seven, per city, in one sentence.

    Prompt wording could not hold that back, and should not have to: an answer's
    facts are what this answer needs. The full set stays in session memory,
    where a later question still reaches it.
    """

    def _layer(self) -> LayerResult:
        return LayerResult(
            capability_id="burned_area_intersecting_place_boundaries", title="t",
            hazard_object="exposure", geometry_type="Polygon", caveat="c",
            feature_count=1, source="s", as_of="2020-09-27",
            geojson={"type": "FeatureCollection", "features": [{
                "type": "Feature", "geometry": None, "properties": {
                    "name": "Monrovia", "geoid": "0648648", "labelSharePercent": 49.7,
                    "population": 37571, "medianHouseholdIncome": 108295,
                    "housingUnits": 14855, "medianAge": 41.0}}]},
        )

    def test_an_income_question_carries_income_and_not_the_rest(self):
        facts = _place_facts_for_answer(self._layer(), ("medianHouseholdIncome",))[0]
        assert facts["medianHouseholdIncome"] == 108295
        assert "housingUnits" not in facts
        assert "medianAge" not in facts

    def test_locally_computed_properties_always_travel(self):
        """The name and the area share are not from the fetch and are what the
        sentence is about."""
        facts = _place_facts_for_answer(self._layer(), ("medianHouseholdIncome",))[0]
        assert facts["name"] == "Monrovia"
        assert facts["labelSharePercent"] == 49.7

    def test_asking_nothing_carries_no_fetched_variable(self):
        facts = _place_facts_for_answer(self._layer(), ())[0]
        assert not any(key in facts for key in api.FETCHED_PROPERTIES)
        assert facts["labelSharePercent"] == 49.7

    def test_session_memory_still_gets_everything(self):
        """Trimming is for this answer only. The next question must still find
        the figure that was fetched and paid for."""
        remembered = api._place_facts(self._layer())[0]
        assert remembered["housingUnits"] == 14855
        assert remembered["medianAge"] == 41.0


def _place_facts_for_answer(layer, asked):
    return api._place_facts(layer, lead=asked, fetched_only=asked)


class TestTurnSaysWhetherTheSubjectChanged:
    """The frontend cannot tell "same subject, refreshing" from "different
    subject, replacing", so it holds the previous place's layers either way and
    the map stays centred on them. See docs/05-turn-subject-change.md.

    `relation` is deliberately not consulted: a correction can correct the date
    and keep the fire, and wiping the map for that is wrong.
    """

    def _resolution(self, *, relation="follow_up", inherited=True):
        from wildfire_agent.conversation import ConversationResolution

        return ConversationResolution(
            relation=relation, standalone_request="x", inherited_subject=inherited, reason="r"
        )

    def test_a_new_request_changed_the_subject(self):
        assert api._subject_changed(self._resolution(relation="new_request", inherited=False))

    def test_a_follow_up_that_inherited_did_not(self):
        assert not api._subject_changed(self._resolution())

    def test_a_correction_that_kept_the_subject_did_not(self):
        """"No, I meant the 28th" corrects the date. The fire is unchanged and
        the map must not be wiped."""
        assert not api._subject_changed(self._resolution(relation="correction", inherited=True))

    def test_a_correction_that_replaced_the_subject_did(self):
        assert api._subject_changed(self._resolution(relation="correction", inherited=False))

    def test_a_follow_up_that_inherited_nothing_did(self):
        assert api._subject_changed(self._resolution(inherited=False))

    def test_the_turn_event_carries_it(self):
        payload = api._turn_payload("analysis", subject_changed=True)
        assert payload == {"kind": "analysis", "subject_changed": True}

    def test_a_discussion_turn_never_reports_a_change(self):
        """Discussion draws nothing, so there is nothing to replace."""
        assert api._turn_payload("discussion")["subject_changed"] is False


class TestAPermanentGapDoesNotReadAsAGlitch:
    """"The required mapped fire area was unavailable" reads as a transient
    failure. For Thomas Fire and Santa Barbara Co. it is permanent: those events
    carry no burned-area labels on any day. Telling the user to try again is a
    waste of their time, and telling them nothing is why they retry."""

    def test_an_event_without_burned_area_labels_says_so(self):
        message = api._fire_community_message(
            "Thomas Fire", None, None, None, burned_area_available=False
        )
        assert "no burned-area labels" in message
        assert "unavailable" not in message

    def test_an_event_that_has_them_keeps_the_old_wording(self):
        """A genuine one-off failure must still read as one."""
        message = api._fire_community_message("Bobcat Fire", None, None, None)
        assert "unavailable" in message

    def test_the_active_fire_caveat_still_travels(self):
        layer = LayerResult(
            capability_id="active_fire_intersecting_place_boundaries", title="t",
            hazard_object="active_fire", geometry_type="Polygon", caveat="c",
            feature_count=1, source="s", as_of="2017-12-13",
            geojson={"type": "FeatureCollection", "features": [{
                "type": "Feature", "geometry": None,
                "properties": {"name": "Montecito", "labelPixelCount": 11}}]},
        )
        message = api._fire_community_message(
            "Thomas Fire", None, None, layer, burned_area_available=False
        )
        assert "Montecito" in message
        assert "no burned-area labels" in message


class TestTheShareNeverStandsAlone:
    """The share is a ratio against the place's own land area, so it is broken
    at both ends: Los Angeles had 3.37 km² inside the Woolsey footprint and read
    as "0%", while Hidden Hills' 0.70 km² read as "16%". Pepperdine University
    read as "100.2%", because a pixel whose centre falls inside counts whole and
    the sum can exceed the polygon.

    The area is the figure that survives both. It travels with the share.
    """

    def _layer(self, *places) -> LayerResult:
        return LayerResult(
            capability_id="burned_area_intersecting_place_boundaries", title="t",
            hazard_object="exposure", geometry_type="Polygon", caveat="c",
            feature_count=len(places), source="s", as_of="2018-11-16",
            geojson={"type": "FeatureCollection", "features": [
                {"type": "Feature", "geometry": None, "properties": {
                    "name": n, "labelSharePercent": s, "labelAreaKm2": a}}
                for n, s, a in places]},
        )

    def test_the_area_travels_with_every_share(self):
        text = api._place_shares(self._layer(("Malibu", 79.7, 40.95)))
        assert "41.0 km2" in text or "41 km2" in text
        assert "80%" in text

    def test_a_share_that_rounds_to_zero_is_not_printed_as_zero(self):
        """"Los Angeles 0%" states that nothing happened. 3.37 km² happened."""
        text = api._place_shares(self._layer(("Los Angeles", 0.3, 3.37)))
        assert "0%" not in text
        assert "<1%" in text
        assert "3.4 km2" in text

    def test_a_share_over_one_hundred_is_reported_as_the_whole_place(self):
        """A place cannot have more than all of its own land inside a footprint;
        the excess is the pixel grid overshooting the boundary."""
        text = api._place_shares(self._layer(("Pepperdine University", 100.2, 1.4)))
        assert "100.2" not in text
        assert "100%" in text

    def test_places_without_an_area_still_report_their_share(self):
        text = api._place_shares(self._layer(("Duarte", 18.0, None)))
        assert "18%" in text


class TestZeroBurnedAreaIsNotAMeasurement:
    """Thomas Fire reported "cumulative mapped BA is approximately 0.0 km2".

    That reads as a measured quantity that happens to be zero. The event carries
    no burned-area labels at all, on any day - the honest statement is that the
    quantity does not exist for it, not that it was measured and came to nothing.
    """

    def test_an_event_without_labels_does_not_report_zero(self):
        message = api._fire_record_message("Thomas Fire", "2017-12-13", 610, 0.0,
                                           burned_area_available=False)
        assert "0.0 km" not in message
        assert "no burned-area labels" in message
        assert "610" in message

    def test_an_event_with_labels_reports_the_area(self):
        message = api._fire_record_message("Bobcat Fire", "2020-09-27", 62, 529.0,
                                           burned_area_available=True)
        assert "529.0 km" in message
        assert "62" in message

    def test_a_genuine_zero_on_an_event_that_has_labels_is_still_reported(self):
        """Day one of a fire that does map burned area really is zero so far."""
        message = api._fire_record_message("Bobcat Fire", "2020-09-04", 4, 0.0,
                                           burned_area_available=True)
        assert "0.0 km" in message


class TestATruncatedCountIsNotTheCount:
    """Los Angeles County publishes 13,954 debris-flow hazard polygons for the
    Bobcat Fire. The fetch is capped at 1,500, and the facts reported
    "hazard_areas: 1500" - a number the narrator can state as though it were how
    many there are. What was drawn and how many exist are different figures.
    """

    def test_a_truncated_fetch_says_it_is_partial(self):
        facts = api._debris_facts("Bobcat Fire", drawn=1500, truncated=True, source="portal",
                                  remaining=("rainfall threshold",))
        assert facts["hazard_areas_drawn"] == 1500
        assert facts["complete"] is False
        assert "not the total" in facts["coverage"]

    def test_a_complete_fetch_says_so(self):
        facts = api._debris_facts("Woolsey Fire", drawn=212, truncated=False, source="portal",
                                  remaining=())
        assert facts["hazard_areas_drawn"] == 212
        assert facts["complete"] is True
        assert "coverage" not in facts

    def test_what_is_still_missing_travels_either_way(self):
        facts = api._debris_facts("Bobcat Fire", drawn=1500, truncated=True, source="p",
                                  remaining=("rainfall threshold",))
        assert facts["still_missing"] == ["rainfall threshold"]


class TestAnArchiveQuestionDoesNotDependOnClassification:
    """"What fires do you have data for" was answered by listing all nine
    events - when the resolver happened to call it discussion. On a rerun it
    called the same question analysis, the pipeline ran, and the user was asked
    which geographic area to consider. Nothing consulted the archive.

    The archive is in the session context either way. Whether it gets read
    should not depend on a sampling decision, so the user's own words settle it
    - the same override `_asks_post_fire_risk_question` already makes in the
    other direction.
    """

    def test_the_wordings_that_ask_what_exists(self):
        for text in (
            "what fires do you have data for",
            "what's in your database",
            "what data do you have",
            "list all the fire events you can analyse",
            "which fires are in the archive",
            "what can you analyse",
        ):
            assert api._asks_archive_question(text), text

    def test_a_question_about_a_fire_is_not_a_question_about_the_archive(self):
        """"Which cities did the Bobcat Fire reach" needs the pipeline. Routing
        it to the archive answer would replace a result with a catalogue."""
        for text in (
            "which cities did it reach?",
            "show the lifecycle of the Bobcat Fire",
            "how is the weather at altadena",
            "what's the population here",
            "how wealthy are those places",
        ):
            assert not api._asks_archive_question(text), text

    def test_it_only_ever_routes_toward_the_answer(self):
        """The override adds a route; it never takes one away. A turn the
        resolver already called discussion is left alone."""
        assert api._archive_kind("analysis", "what fires do you have data for") == "discussion"
        assert api._archive_kind("discussion", "what fires do you have data for") == "discussion"
        assert api._archive_kind("analysis", "which cities did it reach?") == "analysis"
        assert api._archive_kind("discussion", "which cities did it reach?") == "discussion"


class TestAnApprovedFetchIsOnTheRecord:
    """The backend emits `data_fill` with source, what was closed, what remains
    missing, what failed and what the Bureau suppressed. No frontend consumes
    it - not the old one, not the new one - so the one capability the review
    asked to see demonstrated leaves no trace a reviewer can point at beyond
    the numbers changing.

    `contract.assumptions` is already rendered under Limits, and `_run` emits
    `done` with the same contract object after rendering. Recording the fill
    there puts the provenance on screen with no frontend change.
    """

    def _contract(self) -> AnalysisContract:
        return AnalysisContract(original_request="which cities did it reach?")

    def test_the_source_and_vintage_are_recorded(self):
        contract = self._contract()
        api._record_fill_on_contract(
            contract,
            source="U.S. Census Bureau, American Community Survey",
            dataset="ACS 5-year estimates, 2020-2024",
            closed=("population count", "housing density"),
            remaining=("building footprints", "WUI boundary"),
            count=3, unit=("place", "places"), complete=True,
            failed={},
        )
        note = " ".join(contract.assumptions)
        assert "American Community Survey" in note
        assert "ACS 5-year estimates, 2020-2024" in note
        assert "3" in note

    def test_it_says_the_user_approved_it(self):
        """Participation is the thing being demonstrated, so the record has to
        say a person authorised this rather than that data appeared."""
        contract = self._contract()
        api._record_fill_on_contract(
            contract, source="s", dataset="d", closed=("population count",),
            remaining=(), count=1, unit=("place", "places"), complete=True, failed={},
        )
        assert "approval" in " ".join(contract.assumptions).lower()

    def test_what_is_still_missing_is_recorded_beside_it(self):
        contract = self._contract()
        api._record_fill_on_contract(
            contract, source="s", dataset="d", closed=("population count",),
            remaining=("WUI boundary",), count=1, unit=("place", "places"), complete=True, failed={},
        )
        assert "WUI boundary" in " ".join(contract.assumptions)

    def test_a_place_that_failed_is_named_not_folded_into_the_count(self):
        """"We could not reach the API for Duarte" and "nobody lives in Duarte"
        must never read alike - that holds on the record too."""
        contract = self._contract()
        api._record_fill_on_contract(
            contract, source="s", dataset="d", closed=("population count",),
            remaining=(), count=2, unit=("place", "places"), complete=True, failed={"Duarte": "Census API unreachable"},
        )
        note = " ".join(contract.assumptions)
        assert "Duarte" in note
        assert "could not" in note.lower() or "not fetched" in note.lower()

    def test_a_fill_that_closed_nothing_is_not_claimed_as_one_that_did(self):
        contract = self._contract()
        api._record_fill_on_contract(
            contract, source="s", dataset="d", closed=(), remaining=("WUI boundary",),
            count=0, unit=("place", "places"), complete=True, failed={"Monrovia": "unreachable"},
        )
        note = " ".join(contract.assumptions)
        assert "returned nothing" in note.lower() or "no attributes" in note.lower()


class TestTheRecordNamesWhatItCounted:
    """The first version of this record said "fetched ... for 1500 place(s)"
    about debris-flow polygons - the wrong noun, and a truncated count stated as
    a count. Both are mistakes fixed elsewhere today and reintroduced here in a
    new sentence, which is what makes them worth a test rather than a re-read.
    """

    def _contract(self) -> AnalysisContract:
        return AnalysisContract(original_request="q")

    def test_hazard_areas_are_not_called_places(self):
        contract = self._contract()
        api._record_fill_on_contract(
            contract, source="ArcGIS Online catalogue", dataset="hazard areas",
            closed=("burn scar extent",), remaining=(), count=1500,
            unit=("hazard area", "hazard areas"), complete=False, failed={},
        )
        note = " ".join(contract.assumptions)
        assert "hazard areas" in note
        assert "place(s)" not in note

    def test_a_capped_fetch_says_the_count_is_the_cap(self):
        contract = self._contract()
        api._record_fill_on_contract(
            contract, source="s", dataset="d", closed=("burn scar extent",),
            remaining=(), count=1500, unit=("hazard area", "hazard areas"),
            complete=False, failed={},
        )
        assert "not the total" in " ".join(contract.assumptions)

    def test_a_complete_fetch_makes_no_such_claim(self):
        contract = self._contract()
        api._record_fill_on_contract(
            contract, source="s", dataset="d", closed=("population count",),
            remaining=(), count=3, unit=("place", "places"), complete=True, failed={},
        )
        note = " ".join(contract.assumptions)
        assert "3 places" in note
        assert "not the total" not in note

    def test_one_of_a_thing_is_singular(self):
        contract = self._contract()
        api._record_fill_on_contract(
            contract, source="s", dataset="d", closed=("population count",),
            remaining=(), count=1, unit=("place", "places"), complete=True, failed={},
        )
        assert "1 place," in " ".join(contract.assumptions)


class TestANationwideQuestionHasNoPlace:
    """"Are there any ongoing fires in the USA" named no city, so it fell into
    the place pipeline, found nothing to geocode, and returned nothing at all.

    It is a question about the country. WFIGS answers it directly - 237 current
    perimeters as this was written - and needs no subject resolved first.
    """

    def test_the_wordings_that_ask_about_the_country(self):
        for text in (
            "are there any ongoing fires in the USA",
            "are there any ongoing wildfires",
            "what fires are burning right now",
            "any active fires in the country",
            "is anything burning in the US right now",
        ):
            assert api._asks_national_fire_question(text), text

    def test_a_question_about_a_place_is_not_one(self):
        """A named place keeps the pipeline that resolves it."""
        for text in (
            "is there a fire near Altadena right now",
            "are there any fires near Santa Barbara",
            "show the lifecycle of the Bobcat Fire",
            "which cities did it reach?",
        ):
            assert not api._asks_national_fire_question(text), text

    def test_the_summary_leads_with_the_count_and_the_largest(self):
        status = api._national_fire_status([
            {"properties": {"poly_IncidentName": "Big Grass", "attr_IncidentSize": 578637,
                            "attr_POOState": "US-OR"}},
            {"properties": {"poly_IncidentName": "Tartar", "attr_IncidentSize": 158027,
                            "attr_POOState": "US-ID"}},
        ])
        assert "2" in status["message"]
        assert "Big Grass" in status["message"]
        assert "578,637" in status["message"]
        assert "Oregon" in status["message"] or "OR" in status["message"]

    def test_none_reported_is_not_none_burning(self):
        """An agency perimeter is published after mapping, so an empty list is
        about the record, not about the country."""
        status = api._national_fire_status([])
        assert "0" in status["message"] or "no " in status["message"].lower()
        assert any("not" in d.lower() for d in status["details"])

    def test_a_fire_without_a_reported_size_is_not_counted_as_zero_acres(self):
        status = api._national_fire_status([
            {"properties": {"poly_IncidentName": "Hudson", "attr_IncidentSize": None,
                            "attr_POOState": "US-CA"}},
        ])
        assert "Hudson" in status["message"]
        assert "0 acres" not in status["message"]


@pytest.mark.asyncio
async def test_the_city_branch_records_its_fill_without_crashing(monkeypatch):
    """`_record_fill_on_contract` was renamed and one of its three call sites
    was missed. 373 unit tests stayed green because none of them drives the
    city-branch ACS fill end to end - a real conversation found it on the first
    "Fetch it" about a place rather than a fire.

    This drives the renderer, so a signature drift here fails loudly.
    """
    from wildfire_agent.exposure import EnrichmentResult
    from wildfire_agent.planning.models import LayerResult

    subject = LayerResult(
        capability_id="subject_city_boundary", title="Altadena boundary",
        hazard_object="exposure", geometry_type="Polygon", caveat="c", feature_count=1,
        source="TIGER/Line", as_of="2025-01-01",
        geojson={"type": "FeatureCollection", "features": [{
            "type": "Feature", "geometry": None,
            "properties": {"name": "Altadena", "geoid": "0601290", "population": 42846}}]},
    )
    status = {"status": "city_assessed", "workflow": "city", "message": "m", "details": []}

    async def fake_context(_contract):
        return status, [subject]

    async def fake_narrate(**_kwargs):
        return "ok"

    monkeypatch.setattr(api, "_automatic_city_context", fake_context)
    monkeypatch.setattr(api, "narrate", fake_narrate)
    monkeypatch.setattr(
        api, "enrich_places_with_acs",
        lambda layer: EnrichmentResult(
            layer=layer, closed=("population count",),
            source={"source": "U.S. Census Bureau", "dataset": "ACS 5-year estimates"},
        ),
    )

    contract = AnalysisContract(original_request="what's the population here")
    outcome = api._RenderOutcome(variables=("population",))
    outcome.decisions.record("census_acs", True)
    events = [
        event
        async for event in api._render_city_context(contract, api.MessageIn(text="q"), outcome)
    ]

    assert events
    # The fill reached the contract, which is what the Limits tab renders.
    assert any("your approval" in a for a in contract.assumptions)


class TestAnUnclearReplyIsNotSwallowed:
    """A session parked on a fetch offer treated the next message as the answer
    to it. "How many hazard areas are there?" is not a yes or a no, so the offer
    was re-asked and the question vanished - and in a real walkthrough that
    killed every turn after it.

    The offer stays open, because consent must not be guessed. But the question
    the user actually asked has to be answered rather than eaten.
    """

    def _pending(self) -> api.PendingFill:
        return api.PendingFill(
            offer={
                "source_id": "census_acs", "source": "s", "dataset": "d", "geography": "g",
                "closes": ("population count",), "remaining": (),
            },
            capability_id="places",
        )

    def test_a_yes_is_still_a_decision(self):
        assert self._pending().decide("Fetch it") is True

    def test_a_no_is_still_a_decision(self):
        assert self._pending().decide("Answer without it") is False

    def test_a_question_is_not_a_decision(self):
        assert self._pending().decide("How many hazard areas are there?") is None

    def test_an_unclear_reply_that_is_a_real_question_is_carried_forward(self):
        """The distinction that matters: "hmm" is noise and should just re-ask,
        but "how many hazard areas are there?" is a question owed an answer."""
        assert api._is_a_new_question("How many hazard areas are there?") is True
        assert api._is_a_new_question("What kind of land did it burn?") is True
        assert api._is_a_new_question("which cities did it reach") is True

    def test_noise_is_not_treated_as_a_question(self):
        for text in ("hmm", "ok what", "?", "   ", "uh"):
            assert api._is_a_new_question(text) is False, text


class TestClearingEveryConversation:
    """A bulk delete is the one action here that cannot be undone, so it says
    how many it will remove before it does, and leaves the session it hands
    back genuinely empty rather than pointing at a row that no longer exists.
    """

    def test_it_reports_how_many_it_removed(self, client):
        before = len(client.get("/api/sessions").json()["sessions"])
        client.post("/api/sessions")
        client.post("/api/sessions")
        response = client.delete("/api/sessions")

        assert response.status_code == 200
        assert response.json()["deleted"] >= before + 2
        assert client.get("/api/sessions").json()["sessions"] == []

    def test_clearing_an_empty_store_is_not_an_error(self):
        """Idempotent: pressing it twice is not a failure the second time."""
        from fastapi.testclient import TestClient

        with TestClient(api.app) as fresh:
            fresh.delete("/api/sessions")
            second = fresh.delete("/api/sessions")
            assert second.status_code == 200
            assert second.json()["deleted"] == 0

    def test_a_cleared_session_takes_its_consent_with_it(self, client):
        """Fetch decisions are keyed by session id. Leaving them behind means a
        deleted conversation's approvals outlive the conversation."""
        sid = client.post("/api/sessions").json()["session_id"]
        api._fill_decisions[sid] = api.FillDecisions()
        api._fill_decisions[sid].record("census_acs", True)
        api._pending_fills[sid] = api.PendingFill(
            offer={"source_id": "census_acs", "source": "s", "dataset": "d",
                   "geography": "g", "closes": ("population count",), "remaining": ()},
            capability_id="places",
        )

        client.delete("/api/sessions")

        assert sid not in api._fill_decisions
        assert sid not in api._pending_fills
        assert sid not in api._session_contexts

    def test_deleting_one_session_also_takes_its_consent(self, client):
        sid = client.post("/api/sessions").json()["session_id"]
        api._fill_decisions[sid] = api.FillDecisions()

        client.delete(f"/api/sessions/{sid}")

        assert sid not in api._fill_decisions


def test_the_suite_never_touches_the_real_session_database():
    """A session endpoint used to write to `.runtime/wildfire-sessions.sqlite3`
    - the file a running server is serving - because `_session_store` binds to
    it at import. Running the tests emptied a live conversation history once.
    """
    from wildfire_agent.config import settings

    assert api._session_store.path != settings.session_db_path
    assert ".runtime" not in str(api._session_store.path)
