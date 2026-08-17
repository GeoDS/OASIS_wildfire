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


def test_message_stream_interrupts_then_resumes(client):
    session_id = client.post("/api/sessions").json()["session_id"]

    first = client.post(
        f"/api/sessions/{session_id}/messages",
        json={"text": "Where are the active fires near Altadena?"},
    )
    events = _events(first)
    kinds = [e for e, _ in events]
    assert "stage" in kinds
    assert "clarification" in kinds
    assert "done" not in kinds, "clarification still open - done must not fire"

    clarification = next(d for e, d in events if e == "clarification")
    assert clarification["pending_slots"] == ["target"]

    second = client.post(
        f"/api/sessions/{session_id}/messages", json={"text": "both, 10 km"}
    )
    events = _events(second)
    done = next(d for e, d in events if e == "done")
    assert done["ready_for_planning"] is True
    assert done["filled_ratio"] == 1.0


def test_unknown_session_is_rejected(client):
    resp = client.post("/api/sessions/nope/messages", json={"text": "hi"})
    assert resp.status_code == 404
