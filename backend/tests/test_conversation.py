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
