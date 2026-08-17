"""FastAPI + SSE - streams incremental contract updates to the frontend.

The frontend only needs two concepts:
  1. POST /api/sessions                 create a session
  2. POST /api/sessions/{id}/messages   send a message, receive an SSE stream

The same endpoint handles both the opening question and a clarification answer.
The backend decides which it is by checking whether the session is parked on an
interrupt, so the client never has to care.

    uv run uvicorn wildfire_agent.api:app --reload
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from .config import settings
from .contract import AnalysisContract
from .events import EventName
from .graph import build_graph
from .graph.build import make_serde
from .graph.state import STAGE_AGENTS, STAGE_LABELS
from .llm import check_llm_ready, describe_llm, is_mock
from .planning import CAPABILITIES, SHOWCASE_AREA, ExecutionPlan
from .taxonomy import (
    EXPERTISE_DEFINITIONS,
    HAZARD_OBJECTS,
    INTENT_DEFINITIONS,
    INTENT_SLOT_MATRIX,
    ROLE_DEFINITIONS,
    SLOT_DEFINITIONS,
    ExpertiseLevel,
)

app = FastAPI(title="Wildfire User Goal Agent", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-process state. Swap in PostgresSaver plus real session storage for production.
_checkpointer = MemorySaver(serde=make_serde())
_graph = build_graph(_checkpointer)
_sessions: set[str] = set()


class MessageIn(BaseModel):
    text: str = Field(min_length=1)
    #: Expertise picker in the UI - replays one question at three depths.
    expertise_override: ExpertiseLevel | None = None


def _sse(event: EventName, data: Any) -> dict:
    """Encode one SSE frame. `EventName` keeps the wire contract in events.py."""
    return {"event": event, "data": json.dumps(data, ensure_ascii=False, default=str)}


def _contract_payload(contract: AnalysisContract) -> dict:
    payload = contract.model_dump(mode="json")
    payload["pending_slots"] = contract.pending_slots
    payload["filled_ratio"] = contract.filled_ratio()
    return payload


@app.get("/api/health")
async def health() -> dict:
    ok, detail = check_llm_ready()
    # `mock` propagates all the way to a UI badge: a stub never poses as real inference.
    return {"ok": ok, "llm": describe_llm(), "mock": is_mock(), "detail": detail}


@app.get("/api/taxonomy")
async def taxonomy() -> dict:
    """Enums, the slot matrix, and stage labels, so the frontend never keeps its
    own copy of the domain vocabulary."""
    return {
        "intents": INTENT_DEFINITIONS,
        "expertise": EXPERTISE_DEFINITIONS,
        "roles": ROLE_DEFINITIONS,
        "slots": SLOT_DEFINITIONS,
        "intent_slot_matrix": INTENT_SLOT_MATRIX,
        "hazard_objects": {
            ho.id: {
                "layer": ho.layer,
                "label": ho.label,
                "required_variables": list(ho.required_variables),
                "dataset_families": list(ho.dataset_families),
                "covered_by": sorted(
                    c.id for c in CAPABILITIES.values() if c.hazard_object == ho.id
                ),
                "family_choices": [
                    {"id": c.id, "label": c.label, "caveat": c.caveat} for c in ho.family_choices
                ],
            }
            for ho in HAZARD_OBJECTS.values()
        },
        "stages": STAGE_LABELS,
        "stage_agents": STAGE_AGENTS,
        "showcase_area": {
            "id": SHOWCASE_AREA["id"],
            "name": SHOWCASE_AREA["name"],
            "center": list(SHOWCASE_AREA["center"]),
            "bbox": list(SHOWCASE_AREA["bbox"]),
            "context": SHOWCASE_AREA["context"],
        },
        "capabilities": {
            c.id: {
                "title": c.title,
                "hazard_object": c.hazard_object,
                "family": c.family,
                "temporality": c.temporality,
                "geometry_type": c.geometry_type,
                "caveat": c.caveat,
            }
            for c in CAPABILITIES.values()
        },
    }


@app.get("/api/schema/contract")
async def contract_schema() -> dict:
    """JSON Schema for generating the frontend TypeScript types.

    Serialization mode is required: the frontend consumes the **output** shape,
    and validation mode omits computed fields such as `ready_for_planning`.
    """
    return AnalysisContract.model_json_schema(mode="serialization")


@app.post("/api/sessions")
async def create_session() -> dict:
    session_id = str(uuid.uuid4())
    _sessions.add(session_id)
    return {"session_id": session_id}


@app.post("/api/sessions/{session_id}/messages")
async def post_message(session_id: str, body: MessageIn) -> EventSourceResponse:
    if session_id not in _sessions:
        raise HTTPException(404, "unknown session")

    ok, detail = check_llm_ready()
    if not ok:
        raise HTTPException(503, detail)

    return EventSourceResponse(_run(session_id, body))


async def _run(session_id: str, body: MessageIn) -> AsyncIterator[dict]:
    config = {"configurable": {"thread_id": session_id}}

    # Parked on an interrupt? Then this is a clarification answer, not a new question.
    snapshot = await _graph.aget_state(config)
    resuming = bool(snapshot.next) and bool(snapshot.tasks and snapshot.tasks[0].interrupts)

    payload: dict | Command
    if resuming:
        payload = Command(resume=body.text)
    else:
        payload = {
            "original_request": body.text,
            "expertise_override": body.expertise_override,
            "clarification_rounds": 0,
            "stage": "requirement_understanding",
        }

    try:
        async for chunk in _graph.astream(payload, config=config, stream_mode="updates"):
            for node, update in chunk.items():
                if node == "__interrupt__":
                    # LangGraph surfaces interrupts through the same updates stream.
                    yield _sse("clarification", update[0].value)
                    continue

                yield _sse(
                    "stage",
                    {
                        "node": node,
                        "label": STAGE_LABELS.get(node, node),
                        "agent": STAGE_AGENTS.get(node),
                    },
                )

                update = update or {}

                contract = update.get("contract")
                if isinstance(contract, AnalysisContract):
                    yield _sse("contract", _contract_payload(contract))

                plan = update.get("plan")
                if isinstance(plan, ExecutionPlan):
                    yield _sse("plan", plan.model_dump(mode="json"))

                # Layers carry full GeoJSON, so each goes out on its own event
                # rather than in one payload the browser has to swallow whole.
                for layer in update.get("layers") or []:
                    yield _sse("layer", layer.model_dump(mode="json"))
                if update.get("layer_summary"):
                    yield _sse("summary", {"text": update["layer_summary"]})
    except Exception as exc:  # noqa: BLE001 - report any stream failure, never swallow it
        yield _sse("error", {"message": str(exc), "type": type(exc).__name__})
        return

    final = await _graph.aget_state(config)
    contract = final.values.get("contract")
    if isinstance(contract, AnalysisContract) and not final.next:
        yield _sse("done", _contract_payload(contract))


def main() -> None:
    import uvicorn

    uvicorn.run("wildfire_agent.api:app", host=settings.api_host, port=settings.api_port)


if __name__ == "__main__":
    main()
