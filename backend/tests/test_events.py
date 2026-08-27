"""The SSE wire contract must not drift away from its documentation.

Backend and frontend are built by different people against `events.py`; if the
API starts emitting a name that is not declared there, the document stops being
true and a consumer written from it silently misses data.
"""

from __future__ import annotations

import ast
import pathlib

from wildfire_agent.events import EVENT_NAMES

API_SOURCE = pathlib.Path(__file__).resolve().parents[1] / "src" / "wildfire_agent" / "api.py"


def _emitted_event_names() -> set[str]:
    """Every string literal passed as the first argument to `_sse(...)`."""
    tree = ast.parse(API_SOURCE.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_sse"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            found.add(node.args[0].value)
    return found


def test_every_emitted_event_is_declared():
    undeclared = _emitted_event_names() - set(EVENT_NAMES)
    assert not undeclared, f"api.py emits undeclared events: {sorted(undeclared)}"


def _registry_event_names() -> set[str]:
    """Events the analysis registry sends on the orchestrator's behalf.

    `api.py` emits these as `match.spec.event`, so the literal never appears
    there. The registry is where they are declared, and where this check has to
    look for them to stay meaningful.
    """
    from wildfire_agent.analyses import SPECS

    return {spec.event for spec in SPECS}


def test_every_declared_event_is_actually_emitted():
    """A declared-but-dead event is documentation for behaviour that does not exist."""
    unused = set(EVENT_NAMES) - _emitted_event_names() - _registry_event_names()
    assert not unused, f"events.py declares events nothing ever sends: {sorted(unused)}"


def test_every_registry_event_is_declared():
    """An analysis cannot invent an event name the wire contract does not know."""
    undeclared = _registry_event_names() - set(EVENT_NAMES)
    assert not undeclared, f"the analysis registry uses undeclared events: {sorted(undeclared)}"
