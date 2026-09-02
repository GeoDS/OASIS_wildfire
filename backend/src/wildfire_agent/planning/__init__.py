"""Planning Agent - the stage downstream of the Analysis Contract.

Kept in its own package on purpose. Task 1 declares *what problem to solve*;
everything here decides *how to solve it with what exists*, which is the
boundary the task brief draws. The contract is the only thing that crosses it.
"""

from .capabilities import CAPABILITIES, SHOWCASE_AREA, Capability
from .executor import execute, summarise
from .models import ExecutionPlan, LayerResult, PlannedLayer, UnmetNeed
from .planner import build_plan, deployment_unmet_needs, deterministic_plan

__all__ = [
    "CAPABILITIES",
    "SHOWCASE_AREA",
    "Capability",
    "ExecutionPlan",
    "LayerResult",
    "PlannedLayer",
    "UnmetNeed",
    "build_plan",
    "deployment_unmet_needs",
    "deterministic_plan",
    "execute",
    "summarise",
]
