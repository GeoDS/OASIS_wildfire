"""What a derived analysis is, as far as the rest of the system is concerned.

`planning/capabilities.py` already declares what layers this deployment can
fetch. This is the same idea one level up: a declaration of what it can
*compute*. Both exist so that adding a capability is an entry in a table rather
than a branch in an orchestrator.

A spec owns four things and nothing else - how to recognise the request, how to
compile it into a plan, how to run that plan, and which event carries the result
to the browser. Everything else about an analysis stays inside its own module.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


class FirePlanLike(Protocol):
    """The subject an analysis operates on: one matched local fire event."""

    day: str


@dataclass(frozen=True)
class AnalysisSpec:
    """One computable analysis, declared rather than wired in."""

    id: str
    #: Short human phrase naming the analysis, for panels and logs.
    title: str
    #: The SSE event its result travels on. Analyses that produce the same shape
    #: of result share an event, and therefore share a panel.
    event: str
    #: Compile a request into a plan, or return None when it does not apply.
    compile: Callable[..., Any]
    #: Run a compiled plan and return the wire payload.
    execute: Callable[[Any], dict[str, Any]]
    #: Terms that indicate this analysis. Scoring counts how many distinct ones
    #: appear, so the dominant subject of a sentence wins rather than whichever
    #: pattern happens to be tested first.
    patterns: tuple[re.Pattern[str], ...] = ()
    #: Terms that must also be present for any match to count. Used where a word
    #: alone is ambiguous: "NDVI" is a subject, "NDVI change" is a request.
    requires: tuple[re.Pattern[str], ...] = ()
    #: Consulted only to break a tie; lower wins.
    precedence: int = 100
    notes: dict[str, Any] = field(default_factory=dict)

    def score(self, text: str) -> int:
        """How strongly `text` asks for this analysis. Zero means it does not."""
        if any(not pattern.search(text) for pattern in self.requires):
            return 0
        return len(
            {
                match.group(0).casefold()
                for pattern in self.patterns
                for match in pattern.finditer(text)
            }
        )


@dataclass(frozen=True)
class AnalysisMatch:
    """A spec plus the plan it compiled, ready to run."""

    spec: AnalysisSpec
    plan: Any

    def run(self) -> dict[str, Any]:
        return self.spec.execute(self.plan)
