"""Life-cycle phases: a deterministic tool over the daily burned-area record."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from wildfire_agent import raster_layers
from wildfire_agent.analyses import choose
from wildfire_agent.lifecycle import asks_about_lifecycle, lifecycle_answer, lifecycle_phases
from wildfire_agent.raster_layers import render_fire_lifecycle

#: The Bobcat Fire's cached timeline (TS-SatFire 24461771): date, active-fire
#: pixels, newly mapped km², cumulative km².
_BOBCAT = [
    ("2020-09-04", 4, 0.0, 0.0),
    ("2020-09-05", 4, 0.0, 0.0),
    ("2020-09-06", 47, 0.12, 0.12),
    ("2020-09-07", 363, 25.55, 25.67),
    ("2020-09-08", 353, 26.95, 52.62),
    ("2020-09-09", 1005, 39.14, 91.76),
    ("2020-09-10", 362, 18.12, 109.88),
    ("2020-09-11", 391, 13.24, 123.12),
    ("2020-09-12", 371, 13.71, 136.83),
    ("2020-09-13", 340, 11.96, 148.79),
    ("2020-09-14", 397, 27.64, 176.43),
    ("2020-09-15", 531, 16.14, 192.58),
    ("2020-09-16", 436, 19.51, 212.09),
    ("2020-09-17", 721, 23.23, 235.32),
    ("2020-09-18", 1116, 41.58, 276.9),
    ("2020-09-19", 1150, 153.9, 430.8),
    ("2020-09-20", 764, 21.26, 452.06),
    ("2020-09-21", 378, 29.73, 481.79),
    ("2020-09-22", 413, 35.31, 517.1),
    ("2020-09-23", 251, 5.46, 522.56),
    ("2020-09-24", 177, 3.72, 526.28),
    ("2020-09-25", 161, 1.51, 527.79),
    ("2020-09-26", 44, 0.7, 528.48),
    ("2020-09-27", 62, 0.46, 528.95),
]


def _timeline(rows, **extra):
    return [
        {
            "date": date,
            "active_pixels": active,
            "new_burned_km2": new,
            "cumulative_burned_km2": total,
            **{key: values[index] for key, values in extra.items()},
        }
        for index, (date, active, new, total) in enumerate(rows)
    ]


def _spans(result):
    return [(phase["id"], phase["start"], phase["end"]) for phase in result["phases"]]


def test_bobcat_splits_into_the_phases_the_record_shows():
    result = lifecycle_phases(_timeline(_BOBCAT))

    assert _spans(result) == [
        ("ignition", "2020-09-06", "2020-09-06"),
        ("initial_run", "2020-09-07", "2020-09-09"),
        ("sustained", "2020-09-10", "2020-09-18"),
        ("peak", "2020-09-19", "2020-09-19"),
        ("late", "2020-09-20", "2020-09-22"),
        ("slowdown", "2020-09-23", "2020-09-27"),
    ]
    peak = result["phases"][3]
    assert peak["label"] == "Major run"
    assert peak["share_of_total_percent"] == 29
    assert result["first_burn_date"] == "2020-09-06"
    end = result["record_end"]
    assert (end["date"], end["active_pixels"], end["still_active"]) == ("2020-09-27", 62, True)
    early = result["pre_ignition"]
    assert (early["dates"], early["active_pixels"], early["in_footprint"]) == (
        ["2020-09-04", "2020-09-05"],
        8,
        None,
    )
    assert "how the fire ended is not in this data" in result["summary"]


def test_every_day_from_first_burn_to_the_end_is_in_exactly_one_phase():
    result = lifecycle_phases(_timeline(_BOBCAT))
    covered = sum(phase["days"] for phase in result["phases"])
    assert covered == len(_BOBCAT) - 2  # the two days before the first burn
    # Daily figures are rounded in the cache, so their sum drifts by a hundredth.
    assert sum(phase["new_burned_km2"] for phase in result["phases"]) == pytest.approx(
        528.95, abs=0.05
    )


def test_the_same_timeline_always_gives_the_same_result():
    assert lifecycle_phases(_timeline(_BOBCAT)) == lifecycle_phases(_timeline(_BOBCAT))


def test_footprint_counts_are_used_when_the_cache_provides_them():
    inside = [0, 0, 39] + [100] * (len(_BOBCAT) - 3)
    result = lifecycle_phases(_timeline(_BOBCAT, active_pixels_in_footprint=inside))
    assert result["pre_ignition"]["in_footprint"] == 0
    assert "all outside what later burned" in result["summary"]
    assert "Of the 47 heat detections that day, 39 are inside" in result["phases"][0]["note"]


def test_a_fire_whose_first_day_is_its_largest_has_no_separate_peak():
    rows = [
        ("2021-08-01", 50, 80.0, 80.0),
        ("2021-08-02", 30, 10.0, 90.0),
        ("2021-08-03", 5, 0.5, 90.5),
    ]
    result = lifecycle_phases(_timeline(rows))
    assert _spans(result) == [
        ("ignition", "2021-08-01", "2021-08-01"),
        ("late", "2021-08-02", "2021-08-02"),
        ("slowdown", "2021-08-03", "2021-08-03"),
    ]
    assert "It is also the biggest day" in result["phases"][0]["note"]


def test_slow_growth_without_a_rapid_day_still_covers_every_day():
    rows = [(f"2019-07-{day:02d}", 10, 2.0, 2.0 * day) for day in range(1, 6)]
    result = lifecycle_phases(_timeline(rows))
    assert sum(phase["days"] for phase in result["phases"]) == 5
    assert not any(phase["id"] == "initial_run" for phase in result["phases"])
    # The largest day here does not dwarf a typical one, so it is not a "run".
    assert all(phase["label"] != "Major run" for phase in result["phases"])


def test_no_burned_area_and_no_heat_means_no_phases():
    assert lifecycle_phases([]) is None
    assert lifecycle_phases(_timeline([("2020-01-01", 0, 0.0, 0.0)])) is None


def test_the_tool_does_not_change_how_any_question_is_routed():
    """Phases ride along with the lifecycle; they are not an analysis to select.

    Routing is what decides every other answer, so this tool must stay out of it.
    """
    assert choose("What is the life cycle of the Bobcat Fire?") is None
    assert choose("Show the lifecycle of the Bobcat Fire on 2020-09-18") is None


def test_the_lifecycle_payload_carries_the_phases_for_every_selected_day():
    early = render_fire_lifecycle("24461771", day="2020-09-08")
    late = render_fire_lifecycle("24461771", day="2020-09-27")
    assert early["lifecycle_analysis"] == late["lifecycle_analysis"]
    peak = next(p for p in early["lifecycle_analysis"]["phases"] if p["id"] == "peak")
    assert peak["start"] == "2020-09-19"


def test_the_api_expects_the_cache_version_the_builder_writes():
    builder = Path(__file__).resolve().parents[1] / "scripts" / "build_fire_lifecycle_cache.py"
    written = int(re.search(r"^CACHE_VERSION = (\d+)$", builder.read_text(), re.MULTILINE)[1])
    assert written == raster_layers._LIFECYCLE_CACHE_VERSION


def test_a_cache_from_an_older_builder_is_not_served(tmp_path):
    metadata = tmp_path / "metadata.json"
    metadata.write_text(json.dumps({"cache_version": 1}))
    assert not raster_layers._lifecycle_cache_is_current(metadata)
    metadata.write_text(json.dumps({"cache_version": raster_layers._LIFECYCLE_CACHE_VERSION}))
    assert raster_layers._lifecycle_cache_is_current(metadata)
    metadata.write_text("not json")
    assert not raster_layers._lifecycle_cache_is_current(metadata)


@pytest.mark.parametrize(
    "question",
    [
        "What is the life cycle of the Bobcat Fire?",
        "Describe the lifecycle of the Bobcat Fire",
        "Show the Bobcat Fire life-cycle",
        "Bobcat Fire 的生命周期是什么？",
        # Typed live on 2026-10-29; the single-day answer it got said the life
        # cycle could not be determined.
        "what is the lifecyle for bobcat fire",
        "What is the life cyle of the Bobcat Fire?",
        "bobcat fire lifecylce",
        "Describe the lifcycle of the Bobcat Fire",
        "Bobcat Fire 的生命週期",
    ],
)
def test_life_cycle_questions_get_the_phase_answer(question):
    assert asks_about_lifecycle(question)


@pytest.mark.parametrize(
    "question",
    [
        # Naming a day keeps the day-by-day answer it always had.
        "Show the lifecycle of the Bobcat Fire on 2020-09-18.",
        "Show the Bobcat Fire lifecycle on Sep 18",
        # Questions that never named the life cycle are untouched.
        "Show the Woolsey fire on 2018-11-16.",
        "Which way did the Bobcat Fire spread?",
        "Which cities did the Bobcat Fire reach?",
        "What were the fire weather conditions?",
        # Near misses that are not the life cycle.
        "Did the fire reach the bicycle trails?",
        "How long was the fire's lifespan?",
        "Show the lifecyle of the Bobcat Fire on 2020-09-18.",
        "",
        None,
    ],
)
def test_every_other_question_keeps_its_answer(question):
    assert not asks_about_lifecycle(question)
    assert lifecycle_answer(question, lifecycle_phases(_timeline(_BOBCAT))) is None


def test_the_phase_answer_carries_the_summary_and_compact_facts():
    analysis = lifecycle_phases(_timeline(_BOBCAT))
    answer = lifecycle_answer("What is the life cycle of the Bobcat Fire?", analysis)
    assert answer["message"] == analysis["summary"]
    assert [phase["label"] for phase in answer["facts"]["phases"]] == [
        "Ignition",
        "Initial run",
        "Sustained spread",
        "Major run",
        "Late growth",
        "Slowdown",
    ]
    assert answer["facts"]["record_end"] == analysis["record_end"]["note"]
    assert answer["caveats"] == analysis["caveats"]


def test_no_phases_means_no_phase_answer():
    assert lifecycle_answer("What is the life cycle of the Bobcat Fire?", None) is None


#: The Woolsey Fire's cached timeline (TS-SatFire 22343688): date, active-fire
#: pixels, newly mapped km², cumulative km², and heat inside the final burn.
_WOOLSEY = [
    ("2018-11-07", 4, 0.0, 0.0),
    ("2018-11-08", 4, 0.12, 0.12),
    ("2018-11-09", 2221, 0.23, 0.35),
    ("2018-11-10", 810, 0.12, 0.47),
    ("2018-11-11", 181, 405.24, 405.71),
    ("2018-11-12", 76, 35.95, 441.66),
    ("2018-11-13", 66, 0.0, 441.66),
    ("2018-11-14", 61, 4.89, 446.54),
    ("2018-11-15", 20, 0.81, 447.36),
    ("2018-11-16", 6, 4.07, 451.43),
]
_WOOLSEY_HEAT_INSIDE = [0, 0, 2061, 781, 166, 51, 51, 36, 0, 0]


def test_burn_labels_that_lag_the_heat_do_not_move_the_run():
    """Woolsey burned hardest on Nov 9-10, but smoke hid the ground and the burned
    area was only labelled on Nov 11. Read from the labels alone, the record said
    "little growth on Nov 9-10, then a run on Nov 11" - the opposite of what
    happened. The heat inside the eventual burn dates the run instead."""
    result = lifecycle_phases(_timeline(_WOOLSEY, active_pixels_in_footprint=_WOOLSEY_HEAT_INSIDE))
    run = next(phase for phase in result["phases"] if phase["id"] == "peak")
    assert (run["start"], run["end"]) == ("2018-11-09", "2018-11-11")
    assert run["qualifier"] == "estimated"
    assert "Nov 9" in run["note"] and "Nov 11" in run["note"]
    assert result["burn_dates_lag_heat"] is True
    # No phase may describe Nov 9-10 as steady or slow spread.
    assert not any(
        phase["id"] == "sustained" and phase["start"] <= "2018-11-10" <= phase["end"]
        for phase in result["phases"]
    )
    assert any("lag" in caveat for caveat in result["caveats"])


def test_labels_that_keep_pace_with_the_heat_are_left_alone():
    """Bobcat's heat and its biggest mapped day coincide (Sep 19)."""
    inside = [0, 0, 39, 340, 346, 800, 340, 364, 353, 330, 374, 480,
              418, 708, 1080, 1100, 691, 345, 377, 216, 148, 128, 39, 54]  # fmt: skip
    result = lifecycle_phases(_timeline(_BOBCAT, active_pixels_in_footprint=inside))
    assert result["burn_dates_lag_heat"] is False
    run = next(phase for phase in result["phases"] if phase["id"] == "peak")
    assert (run["start"], run["end"]) == ("2020-09-19", "2020-09-19")


#: The Thomas Fire's cached timeline: heat detections only, no burned-area labels.
_THOMAS = [
    ("2017-12-04", 55, 0.0, 0.0),
    ("2017-12-05", 3320, 0.0, 0.0),
    ("2017-12-06", 1772, 0.0, 0.0),
    ("2017-12-07", 1748, 0.0, 0.0),
    ("2017-12-08", 1042, 0.0, 0.0),
    ("2017-12-09", 869, 0.0, 0.0),
    ("2017-12-10", 3145, 0.0, 0.0),
    ("2017-12-11", 1122, 0.0, 0.0),
    ("2017-12-12", 738, 0.0, 0.0),
    ("2017-12-13", 610, 0.0, 0.0),
]


def test_a_fire_with_heat_but_no_burn_labels_still_gets_a_life_cycle():
    """Thomas has ten days of heat detections and no burned-area labels. That is
    enough to say when it started, when it peaked, that it surged again and that
    it was still burning when the record ends - just not how much it burned."""
    result = lifecycle_phases(_timeline(_THOMAS))
    assert result["basis"] == "heat"
    assert _spans(result) == [
        ("first_heat", "2017-12-04", "2017-12-04"),
        ("peak_heat", "2017-12-05", "2017-12-05"),
        ("easing", "2017-12-06", "2017-12-09"),
        ("flare_up", "2017-12-10", "2017-12-10"),
        ("tailing_off", "2017-12-11", "2017-12-13"),
    ]
    assert result["record_end"]["still_active"] is True
    assert "no burned-area labels" in result["summary"]
    assert result["total_burned_km2"] is None


def test_a_heat_only_record_answers_a_life_cycle_question():
    answer = lifecycle_answer(
        "What is the life cycle of the Thomas Fire?", lifecycle_phases(_timeline(_THOMAS))
    )
    assert answer is not None
    assert "3,320" in answer["message"]


def test_burned_area_records_say_which_basis_they_use():
    assert lifecycle_phases(_timeline(_BOBCAT))["basis"] == "burned_area"


def test_a_biggest_labelled_day_with_almost_no_heat_is_dated_from_the_heat():
    """Mojave / I-15 labelled 125 km² on Aug 18, a day with 7 heat detections
    against 210 on Aug 16. Ground does not burn without heat: the labels lag."""
    rows = [
        ("2020-08-15", 0, 0.57, 0.57),
        ("2020-08-16", 211, 0.11, 0.68),
        ("2020-08-17", 102, 71.84, 72.52),
        ("2020-08-18", 15, 124.86, 197.38),
        ("2020-08-19", 8, 0.0, 197.38),
    ]
    result = lifecycle_phases(_timeline(rows, active_pixels_in_footprint=[0, 210, 99, 13, 7]))
    assert result["burn_dates_lag_heat"] is True
    run = next(phase for phase in result["phases"] if phase["id"] == "peak")
    assert (run["start"], run["end"]) == ("2020-08-16", "2020-08-18")


def test_notes_read_naturally_at_the_edges():
    rows = [
        ("2021-10-11", 0, 1.0, 1.0),
        ("2021-10-12", 40, 36.0, 37.0),
        ("2021-10-13", 30, 38.0, 75.0),
        ("2021-10-14", 10, 6.8, 81.8),
        ("2021-10-15", 2, 0.0, 81.8),
    ]
    notes = " ".join(phase["note"] for phase in lifecycle_phases(_timeline(rows))["phases"])
    assert "1 days" not in notes
    assert "6.8–6.8" not in notes
    assert "Of the 0 heat" not in notes
    assert "just 0.0" not in notes


def test_a_tail_that_rises_again_is_not_called_falling():
    rows = [(f"2017-07-{day:02d}", heat, 0.0, 0.0) for day, heat in
            [(7, 27), (8, 961), (9, 1137), (10, 424), (11, 290), (12, 208), (13, 312), (14, 528)]]  # fmt: skip
    tail = lifecycle_phases(_timeline(rows))["phases"][-1]
    assert tail["id"] == "tailing_off"
    assert "falling" not in tail["note"]
