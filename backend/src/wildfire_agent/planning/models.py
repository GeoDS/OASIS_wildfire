"""What the Planning Agent produces: a plan, then the layers it resolved."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class PlannedLayer(BaseModel):
    """One capability the plan intends to fetch."""

    capability_id: str
    title: str
    hazard_object: str
    family: str | None = None
    geometry_type: Literal["Point", "Polygon", "LineString"]
    caveat: str
    #: Why this layer was selected, in terms of the contract that asked for it.
    reason: str


class UnmetNeed(BaseModel):
    """A hazard object the contract declared that nothing can currently serve.

    Reporting these is the point of separating the contract from capabilities:
    Task 1 states the need honestly, and this is where the system admits what it
    cannot do instead of quietly substituting something that looks similar.
    """

    hazard_object: str
    reason: str


class ExecutionPlan(BaseModel):
    layers: list[PlannedLayer] = Field(default_factory=list)
    unmet: list[UnmetNeed] = Field(default_factory=list)
    #: Caveats about the plan as a whole, e.g. that the dataset is a snapshot.
    notes: list[str] = Field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.layers


class LayerResult(BaseModel):
    """A fetched, filtered layer, ready to draw."""

    capability_id: str
    title: str
    hazard_object: str
    family: str | None = None
    geometry_type: Literal["Point", "Polygon", "LineString"]
    caveat: str
    feature_count: int
    #: True when the area of interest contained more features than the cap.
    truncated: bool = False
    source: str
    as_of: str | None = None
    retrieved_at: str | None = None
    geojson: dict[str, Any]
