"""The dates a severity or NDVI comparison uses are the user's to name.

Running the demo card twice on 2026-11-01, "How badly did the Bobcat fire burn
between the first and last day?", asked right after the life cycle, computed
mean dNBR 0.383 for 2020-09-04 to 2020-09-27 once and 0.296 for 2020-09-06 to
2020-09-27 the other time. Dates are read from the resolver's rewrite, and after
the life-cycle answer the rewrite can name September 6, the first burned ground,
as "the first day". The user named no day; the record's first and last day apply.
"""

from __future__ import annotations

import pytest

from wildfire_agent.analyses import resolve
from wildfire_agent.contract import AnalysisContract, ScalarSlot
from wildfire_agent.raster_layers import plan_fire_raster
from wildfire_agent.spatial_analysis import compile_raster_analysis

QUESTION = "How badly did the Bobcat fire burn between the first and last day?"
#: The shape of the rewrite that moved the first day to the first burned ground.
REWRITE = (
    "For the Bobcat Fire, compare burn severity between the first event day with mapped "
    "burned ground, 2020-09-06, and the last event day, 2020-09-27."
)


@pytest.fixture(scope="module")
def bobcat():
    plan = plan_fire_raster(
        AnalysisContract(
            original_request=QUESTION,
            task_intent=["observation"],
            slots={"time_horizon": ScalarSlot(value="2020-09-27")},
        )
    )
    assert plan is not None
    return plan


def test_dates_only_the_rewrite_named_do_not_move_the_comparison(bobcat):
    match = resolve(REWRITE, bobcat, original=QUESTION)
    assert match.plan.formula == "NBR(2020-09-04) - NBR(2020-09-27)"


def test_ndvi_from_the_first_to_the_last_record_is_the_whole_record(bobcat):
    question = "Compare NDVI inside the Bobcat fire's mapped burned area from the first to the last record."
    rewrite = f"{question} Use 2020-09-06 and 2020-09-27."
    match = resolve(rewrite, bobcat, original=question)
    assert match.plan.formula == "NDVI(2020-09-27) - NDVI(2020-09-04)"


@pytest.mark.parametrize(
    "question",
    [
        "How severe was the burn between 2020-09-10 and 2020-09-20?",
        "How severe was the burn between Sep 10 and Sep 20?",
        "Burn severity from September 10th to September 20th",
        "Compare the burn severity on 9/10 and 9/20",
    ],
)
def test_days_the_user_named_are_used(bobcat, question):
    rewrite = f"{question} (2020-09-10 and 2020-09-20)"
    match = resolve(rewrite, bobcat, original=question)
    assert match.plan.formula == "NBR(2020-09-10) - NBR(2020-09-20)"


def test_a_caller_without_the_users_words_keeps_the_old_reading(bobcat):
    plan = compile_raster_analysis(REWRITE, bobcat, operation="nbr_change")
    assert plan.formula == "NBR(2020-09-06) - NBR(2020-09-27)"


@pytest.mark.parametrize(
    "text, names_a_day",
    [
        ("2020-09-15", True),
        ("on Sep 15", True),
        ("September 15th", True),
        ("on 9/15", True),
        ("on the 15th", True),
        ("9月15日的严重程度", True),
        (QUESTION, False),
        ("How severe was it?", False),
        ("from the first to the last record", False),
    ],
)
def test_what_counts_as_naming_a_day(text, names_a_day):
    from wildfire_agent.spatial_analysis import _NAMED_DAY_RE

    assert bool(_NAMED_DAY_RE.search(text)) is names_a_day
