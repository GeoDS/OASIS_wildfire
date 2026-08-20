"""API smoke tests. Stub LLM, no API key required."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from wildfire_agent import api
from wildfire_agent.contract import ResolvedLocation
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
        hazard_object="population_exposure",
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
        hazard_object="population_exposure",
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
    assert len(body["hazard_objects"]) == 13
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
    assert events[0][1] == {"kind": "analysis"}


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
    assert events[0][1] == {"kind": "discussion"}
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
