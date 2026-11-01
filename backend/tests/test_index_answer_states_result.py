"""A severity or NDVI answer states the figure it computed.

Rerunning the demo on 2026-11-01: "How badly did the Bobcat fire burn between
the first and last day?" computed mean dNBR 0.383, and the reply was that the
facts "do not include the cumulative burned-area values for September 6 and
September 27, so they cannot quantify the increase". The question names two
days, the narrator read it as one about area, and its rule for facts that do
not answer the question let it say so instead of reporting the severity.
"""

from __future__ import annotations

import asyncio
import dataclasses

from wildfire_agent import api, narration
from wildfire_agent.analyses import AnalysisMatch, choose
from wildfire_agent.contract import AnalysisContract
from wildfire_agent.raster_layers import FireRasterPlan, RasterDataset

QUESTION = "How badly did the Bobcat fire burn between the first and last day?"
SUMMARY = (
    "Across the Bobcat Fire mapped burned-area footprint, mean dNBR is 0.383 between "
    "2020-09-04 and 2020-09-27, which sits in the moderate range on the USGS severity "
    "scale. 37% of the footprint burned at moderate-high or high severity."
)
#: The reply given on 2026-11-01, verbatim.
REFUSAL = (
    "The supplied results do not include the cumulative burned-area values for September 6 "
    "and September 27, so they cannot quantify the increase or the burning that occurred "
    "between those dates. They cover reflectance-based burn-severity conditions across the "
    "mapped burned-area footprint, not the fire_lifecycle cumulative-area or active-fire-label "
    "totals needed for that comparison."
)
NBR = {"statistics": {"mean_delta": 0.3826, "damaged_percent": 98.2}, "summary": SUMMARY}
NDVI = {"statistics": {"mean_delta": -0.1768}, "summary": "mean NDVI decreased by 0.177"}


def test_the_headline_is_the_mean_change_as_the_summary_prints_it():
    assert api._analysis_headline(NBR) == ("0.383",)
    assert api._analysis_headline(NDVI) == ("0.177",)


def test_a_result_without_statistics_asks_for_nothing():
    """Spread, weather and land cover carry no `mean_delta`; they are untouched."""
    assert api._analysis_headline({"summary": "Its mapped centre moved 2.3 km"}) == ()


def test_the_refusal_falls_back_to_the_computed_summary(monkeypatch):
    async def fake_draft(_system, _human):
        return REFUSAL

    monkeypatch.setattr(narration, "is_mock", lambda: False)
    monkeypatch.setattr(narration, "_draft", fake_draft)
    facts = {"message": SUMMARY}
    reply = asyncio.run(
        narration.narrate(question=QUESTION, facts=facts, fallback=SUMMARY, keep=("0.383",))
    )
    assert reply == SUMMARY


def _render(monkeypatch, question: str) -> dict:
    """Run `_render_fire_event` offline; report what `narrate` was asked to keep."""
    told: dict = {}

    def resolve(request, fire_plan, original=None):
        spec = choose(request, original)
        if spec is None:
            return None
        result = dict(NBR, analysis_id="t", operation=spec.id, title="t", inputs=[],
                      operations=[], mask="m", layers=[], caveats=[], formula="f")
        return AnalysisMatch(spec=dataclasses.replace(spec, execute=lambda _p: result), plan=None)

    async def no_subject(_name, _year):
        return None

    async def fake_narrate(**kwargs):
        told.update(kwargs)
        return kwargs["fallback"]

    plan = FireRasterPlan(
        dataset=RasterDataset(
            dataset_id="bobcat", variable="VIIRS_Day", event_id="24461771",
            event_name="Bobcat Fire area", spatial_scope="s", time_start="2020-09-04",
            time_end="2020-09-27", dates=["2020-09-04", "2020-09-27"],
        ),
        day="2020-09-27", presentation="p", explanation="e",
    )
    monkeypatch.setattr(api, "render_fire_lifecycle", lambda _e, day: {
        "layers": [], "selected_metrics": {"active_pixels": 0, "cumulative_burned_km2": 529.0},
        "prediction_status": "s", "pixel_area_km2": 0.14, "lifecycle_analysis": None,
    })
    monkeypatch.setattr(api, "resolve_analysis", resolve)
    monkeypatch.setattr(api, "event_supports_burned_area", lambda *_: True)
    monkeypatch.setattr(api, "_automatic_fire_subject", no_subject)
    monkeypatch.setattr(api, "narrate", fake_narrate)
    contract = AnalysisContract(original_request=question, resolved_request=question)

    async def drain():
        return [e async for e in api._render_fire_event(
            contract, plan, api.MessageIn(text=question), api._RenderOutcome())]

    asyncio.run(drain())
    return told


def test_the_severity_answer_must_state_the_mean_dnbr(monkeypatch):
    told = _render(monkeypatch, QUESTION)
    assert told["fallback"] == SUMMARY
    assert told["keep"] == ("0.383",)


def test_an_answer_that_is_not_the_analysis_keeps_nothing(monkeypatch):
    """A life-cycle or plain day answer is narrated as before."""
    told = _render(monkeypatch, "Show the Bobcat Fire on 2020-09-27.")
    assert told["keep"] == ()
