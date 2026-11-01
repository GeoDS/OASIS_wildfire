"""Life-cycle phases of one archived fire, computed by rule from its daily record.

The lifecycle cache already holds every day's mapped growth, but nothing turned
that series into an account of the fire: asked for its life cycle, the system
could only describe the selected day. This module is that account, written as a
deterministic tool. It reads the cached timeline and nothing else, applies fixed
and stated thresholds, and the same timeline always yields the same phases. No
model decides where one phase ends and the next begins.

It is additive on purpose. `render_fire_lifecycle` attaches the result as
`lifecycle_analysis` for the panel, and no request routes on it. Only a question
whose own words ask about the life cycle hands it to the narrator
(`lifecycle_answer`); every other question keeps the facts, and so the answer,
it had before.
"""

from __future__ import annotations

import re
from statistics import median
from typing import Any

#: Newly mapped area that counts as fast growth, per day. About 4% of the Bobcat
#: Fire's mapped total. Phase boundaries move when this changes, so it is stated
#: in every result rather than buried here.
RAPID_GROWTH_KM2 = 20.0
#: The largest day is called a major run only when it dwarfs a typical growing day.
MAJOR_RUN_MULTIPLE = 3.0
#: Growth below this share of the mapped total, every day to the end of the
#: record, is a slowdown. A single quiet day between runs is not.
SLOWDOWN_SHARE = 0.02
#: Smoke can hide the ground, so a run is sometimes labelled days after the heat
#: that made it (Woolsey: heat peaked Nov 9, 405 km² labelled Nov 11). The labels
#: are taken to lag when the heat peak comes first, is at least this many times
#: the heat on the biggest labelled day ...
LAG_HEAT_MULTIPLE = 2.0
#: ... and either the days in between labelled less than this share of that
#: day's area ...
LAG_LABELLED_SHARE = 0.25
#: ... or that day itself had at most this share of the peak heat: ground does
#: not burn without heat (Mojave labelled 125 km² on a day with 7 detections).
LAG_PEAK_HEAT_SHARE = 0.1
#: With heat alone (no burn labels), a later day counts as a flare-up when it is
#: at least this share of the hottest day ...
FLARE_UP_SHARE = 0.5
#: ... and at least this many times the day before it.
FLARE_UP_RISE = 1.5

_RULES = {
    "rapid_growth_km2_per_day": RAPID_GROWTH_KM2,
    "major_run_median_multiple": MAJOR_RUN_MULTIPLE,
    "slowdown_share_per_day": SLOWDOWN_SHARE,
    "lag_heat_multiple": LAG_HEAT_MULTIPLE,
    "lag_labelled_share": LAG_LABELLED_SHARE,
    "lag_peak_heat_share": LAG_PEAK_HEAT_SHARE,
    "flare_up_share_of_peak": FLARE_UP_SHARE,
    "flare_up_rise": FLARE_UP_RISE,
}

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _day(date: str) -> str:
    """`2020-09-19` -> `Sep 19`."""
    _, month, day = date.split("-")
    return f"{_MONTHS[int(month) - 1]} {int(day)}"


def _span(start: str, end: str) -> str:
    """`Sep 4–5` within a month, `Aug 30–Sep 2` across one."""
    if start == end:
        return _day(start)
    if start[:7] == end[:7]:
        return f"{_day(start)}–{int(end[8:])}"
    return f"{_day(start)}–{_day(end)}"


def _days(count: int) -> str:
    return "1 day" if count == 1 else f"{count} days"


def _km2(value: float) -> str:
    """Whole numbers once a figure is large enough that a decimal is noise."""
    return f"{value:,.0f}" if value >= 10 else f"{value:.1f}"


def lifecycle_phases(timeline: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Split a daily fire record into phases, or None if it holds nothing.

    With burned-area labels, every day from the first mapped burn to the end of
    the record belongs to exactly one phase, and days before the first burn are
    reported separately: any heat on them is not treated as the start of this
    fire. Without labels but with heat detections, the phases come from the heat
    alone (`_heat_only_phases`).
    """
    if not timeline:
        return None
    dates = [str(point["date"]) for point in timeline]
    new = [float(point.get("new_burned_km2") or 0.0) for point in timeline]
    cumulative = [float(point.get("cumulative_burned_km2") or 0.0) for point in timeline]
    active = [int(point.get("active_pixels") or 0) for point in timeline]
    first = next((index for index, value in enumerate(cumulative) if value > 0), None)
    if first is None:
        return _heat_only_phases(dates, active)
    last = len(timeline) - 1
    total = cumulative[last]
    # Earliest day with the most growth, so a tie names the first of them.
    peak = max(range(first, last + 1), key=lambda index: (new[index], -index))
    growing = [value for value in new[first:] if value > 0]
    typical = median(growing)

    in_footprint = [point.get("active_pixels_in_footprint") for point in timeline]
    # Heat inside the eventual burn where the cache records it, all heat otherwise.
    heat = [
        active[index] if inside is None else int(inside)
        for index, inside in enumerate(in_footprint)
    ]
    heat_peak = max(range(first, last + 1), key=lambda index: (heat[index], -index))
    lagged = (
        heat_peak < peak
        and heat[heat_peak] >= LAG_HEAT_MULTIPLE * max(heat[peak], 1)
        and (
            sum(new[heat_peak:peak]) < LAG_LABELLED_SHARE * new[peak]
            or heat[peak] <= LAG_PEAK_HEAT_SHARE * heat[heat_peak]
        )
    )
    # When the labels lag, the run is dated from the heat that made it; the days
    # before it can then no longer be called slow just because nothing was labelled.
    run_start = min(max(heat_peak, first + 1), peak) if lagged else peak

    run: tuple[int, int] | None = None
    start = next(
        (index for index in range(first, run_start) if new[index] >= RAPID_GROWTH_KM2), None
    )
    if start is not None:
        end = start
        while end + 1 < run_start and new[end + 1] >= RAPID_GROWTH_KM2:
            end += 1
        run = (start, end)
    slow = next(
        (
            index
            for index in range(peak + 1, last + 1)
            if all(
                new[later] < SLOWDOWN_SHARE * cumulative[later] for later in range(index, last + 1)
            )
        ),
        None,
    )

    spans: list[tuple[str, int, int]] = []

    def add(kind: str, start: int, end: int) -> None:
        if start <= end:
            spans.append((kind, start, end))

    if peak == first:
        add("ignition", first, first)
    else:
        ignition_end = run[0] - 1 if run and run[0] > first else first
        add("ignition", first, ignition_end)
        if run:
            add("initial_run", max(run[0], ignition_end + 1), run[1])
        add("sustained", max(ignition_end, run[1] if run else ignition_end) + 1, run_start - 1)
        add("peak", run_start, peak)
    add("late", (peak + 1), (slow - 1) if slow is not None else last)
    if slow is not None:
        add("slowdown", slow, last)

    is_major = new[peak] >= MAJOR_RUN_MULTIPLE * typical
    phases = []
    for kind, start, end in spans:
        area = sum(new[start : end + 1])
        low, high = min(new[start : end + 1]), max(new[start : end + 1])
        share = round(100 * area / total) if total else 0
        # Notes are read by people first: plain words, one point each, with the
        # figure that makes the point. The rule stays beside them for a tooltip.
        if kind == "ignition":
            label, qualifier = "Ignition", "estimated"
            rule = "First day with mapped burned area."
            note = "The fire first shows up as burned ground."
            if in_footprint[first] and active[first]:
                note += (
                    f" Of the {active[first]:,} heat detections that day, "
                    f"{in_footprint[first]:,} are inside what later burned."
                )
            if peak == first:
                note += f" It is also the biggest day: {_km2(area)} km², {share}% of the fire."
        elif kind == "initial_run":
            label, qualifier = "Initial run", "estimated"
            rule = f"First unbroken run of days with at least {RAPID_GROWTH_KM2:g} km² of new burn."
            note = (
                f"A fast start: {_km2(area)} km² in {_days(end - start + 1)}, "
                f"about {share}% of the final area."
            )
        elif kind == "sustained":
            label, qualifier = "Sustained spread", "estimated"
            rule = "Days between the initial run and the largest day."
            note = (
                f"Steady spread of {_km2(low)}–{_km2(high)} km² a day, "
                f"{_km2(area)} km² over {_days(end - start + 1)} ({share}%)."
            )
        elif kind == "peak" and lagged:
            label = "Major run" if is_major else "Largest run"
            qualifier = "estimated"
            rule = (
                f"Dated from the heat peak: the burned-area labels lag it by "
                f"{peak - heat_peak} days."
            )
            note = (
                f"The heaviest burning: heat peaked on {_day(dates[heat_peak])} with "
                f"{heat[heat_peak]:,} detections inside what later burned, but the ground was "
                f"mapped later, {_km2(area)} km² ({share}% of the fire) by {_day(dates[peak])}, "
                "likely because smoke hid it."
            )
        elif kind == "peak":
            label = "Major run" if is_major else "Largest day"
            qualifier = "observed"
            rule = (
                f"Largest single day; a major run when it exceeds {MAJOR_RUN_MULTIPLE:g}× "
                f"the median growing day ({_km2(typical)} km²)."
            )
            note = (
                f"The biggest day by far: {_km2(area)} km² burned, {share}% of the whole fire "
                f"and {new[peak] / typical:.0f} times a typical day."
                if is_major
                else f"The busiest day: {_km2(area)} km², {share}% of the fire."
            )
        elif kind == "late":
            label, qualifier = "Late growth", "estimated"
            rule = "Days after the largest day, before any slowdown."
            note = (
                f"Still growing: {_km2(area)} km² more."
                if start == end
                else f"Still growing at {_km2(low)}–{_km2(high)} km² a day, {_km2(area)} km² more."
            )
        else:
            label, qualifier = "Slowdown", "estimated"
            rule = (
                f"Every remaining day grows by less than {SLOWDOWN_SHARE:.0%} of the mapped total."
            )
            note = f"Winding down: under {SLOWDOWN_SHARE:.0%} of the burned area a day, " + (
                f"just {_km2(new[last])} km² on {_day(dates[last])}."
                if new[last] > 0
                else f"with nothing new mapped on {_day(dates[last])}."
            )
        phases.append(
            {
                "id": kind,
                "label": label,
                "start": dates[start],
                "end": dates[end],
                "days": end - start + 1,
                "new_burned_km2": round(area, 2),
                "share_of_total_percent": share,
                "daily_km2": [round(low, 2), round(high, 2)],
                "qualifier": qualifier,
                "rule": rule,
                "note": note,
            }
        )

    pre_ignition = None
    early = [index for index in range(first) if active[index] > 0]
    if early:
        known = [in_footprint[index] for index in early]
        inside = sum(known) if all(value is not None for value in known) else None
        count = sum(active[index] for index in early)
        where = ", all outside what later burned," if inside == 0 else ""
        pre_ignition = {
            "dates": [dates[index] for index in early],
            "active_pixels": count,
            "in_footprint": inside,
            "note": (
                f"Before {_day(dates[first])}, the satellite flagged {count:,} heat detections "
                f"on {_span(dates[early[0]], dates[early[-1]])}{where} so they are not counted "
                "as the start."
            ),
        }
    record_end = _record_end(dates[last], active[last])

    sentences = [
        (
            f"The mapped burn grew from first burned ground on {_day(dates[first])} to "
            f"{_km2(total)} km² by {_day(dates[last])}."
        )
    ]
    if pre_ignition:
        sentences.append(pre_ignition["note"])
    sentences.extend(
        f"{phase['label']}, {_span(phase['start'], phase['end'])}: {phase['note']}"
        for phase in phases
    )
    sentences.append(record_end["note"])
    caveats = [
        (
            f"How phases are defined: a fast day adds at least {RAPID_GROWTH_KM2:g} km²; the "
            f"biggest day counts as a major run when it is more than {MAJOR_RUN_MULTIPLE:g}× a "
            f"typical day ({_km2(typical)} km²); the slowdown starts once every remaining day "
            f"adds less than {SLOWDOWN_SHARE:.0%} of the burned area."
        ),
        (
            "The satellite passes once a day, so times within a day are unknown, and ground "
            "hidden by smoke or cloud is dated to the first clear pass."
        ),
        "Areas come from the dataset's satellite burn labels, not official acreage.",
    ]
    if lagged:
        caveats.insert(
            1,
            f"Burned-area labels lag the heat here: {_km2(new[peak])} km² was first mapped on "
            f"{_day(dates[peak])}, {peak - heat_peak} days after the heat peaked on "
            f"{_day(dates[heat_peak])}, so the run is dated from the heat.",
        )
    return {
        "basis": "burned_area",
        "burn_dates_lag_heat": lagged,
        "heat_peak_date": dates[heat_peak],
        "rules": _RULES,
        "first_burn_date": dates[first],
        "total_burned_km2": round(total, 2),
        "median_growing_day_km2": round(typical, 2),
        "phases": phases,
        "pre_ignition": pre_ignition,
        "record_end": record_end,
        "summary": " ".join(sentences),
        "caveats": caveats,
    }


def _record_end(date: str, heat: int) -> dict[str, Any]:
    return {
        "date": date,
        "active_pixels": heat,
        "still_active": heat > 0,
        "note": (
            f"The record stops on {_day(date)} with {heat:,} heat detections still active, "
            "so how the fire ended is not in this data."
            if heat > 0
            else f"No heat was detected on the last day of the record, {_day(date)}."
        ),
    }


def _heat_only_phases(dates: list[str], heat: list[int]) -> dict[str, Any] | None:
    """Phases from daily heat detections, for a record with no burned-area labels.

    Heat says when a fire started, peaked, surged again and was still burning; it
    cannot say how much ground burned, and the result says so instead of leaving
    a life-cycle question unanswered.
    """
    start = next((index for index, value in enumerate(heat) if value > 0), None)
    if start is None:
        return None
    last = len(heat) - 1
    peak = max(range(start, last + 1), key=lambda index: (heat[index], -index))
    surges = [
        index
        for index in range(peak + 2, last + 1)
        if heat[index] >= FLARE_UP_SHARE * heat[peak]
        and heat[index] >= FLARE_UP_RISE * max(heat[index - 1], 1)
    ]
    flare = max(surges, key=lambda index: (heat[index], -index)) if surges else None

    spans: list[tuple[str, int, int]] = []

    def add(kind: str, first: int, end: int) -> None:
        if first <= end:
            spans.append((kind, first, end))

    add("first_heat", start, peak - 1)
    add("peak_heat", peak, peak)
    if flare is not None:
        add("easing", peak + 1, flare - 1)
        add("flare_up", flare, flare)
        add("tailing_off", flare + 1, last)
    else:
        add("tailing_off", peak + 1, last)

    labels = {
        "first_heat": ("First heat", "estimated", "Days of heat before the hottest day."),
        "peak_heat": ("Peak heat", "observed", "The day with the most heat detections."),
        "easing": ("Easing", "estimated", "Days between the hottest day and a later surge."),
        "flare_up": (
            "Flare-up",
            "observed",
            (
                f"A later day at least {FLARE_UP_SHARE:.0%} as hot as the peak, rising "
                f"{FLARE_UP_RISE:g}× or more from the day before."
            ),
        ),
        "tailing_off": ("Tailing off", "estimated", "Days after the last surge."),
    }
    phases = []
    for kind, first, end in spans:
        days = heat[first : end + 1]
        if kind == "first_heat":
            note = (
                f"Heat first detected: {days[0]:,} detections."
                if len(days) == 1
                else f"Heat first detected, {min(days):,}–{max(days):,} detections a day."
            )
        elif kind == "peak_heat":
            note = f"The hottest day: {days[0]:,} heat detections."
        elif kind == "easing":
            note = f"Heat eased to {min(days):,}–{max(days):,} detections a day."
        elif kind == "flare_up":
            note = (
                f"A second surge: {days[0]:,} detections, "
                f"{round(100 * days[0] / heat[peak])}% of the peak on {_day(dates[peak])}."
            )
        else:
            if len(days) == 1:
                note = f"{days[0]:,} heat detections."
            elif days[-1] < days[0]:
                note = f"Heat falling, from {days[0]:,} to {days[-1]:,} detections a day."
            else:
                note = (
                    f"Heat stayed at {min(days):,}–{max(days):,} detections a day, "
                    f"{days[-1]:,} on the last day."
                )
        label, qualifier, rule = labels[kind]
        phases.append(
            {
                "id": kind,
                "label": label,
                "start": dates[first],
                "end": dates[end],
                "days": end - first + 1,
                "new_burned_km2": 0.0,
                "share_of_total_percent": 0,
                "daily_km2": [0.0, 0.0],
                "daily_heat": [min(days), max(days)],
                "qualifier": qualifier,
                "rule": rule,
                "note": note,
            }
        )

    record_end = _record_end(dates[last], heat[last])
    sentences = [
        (
            "This record has no burned-area labels, so the life cycle is told from daily heat "
            "detections alone and how much ground burned is not known. Heat runs from "
            f"{_day(dates[start])} to {_day(dates[last])}."
        )
    ]
    sentences.extend(
        f"{phase['label']}, {_span(phase['start'], phase['end'])}: {phase['note']}"
        for phase in phases
    )
    sentences.append(record_end["note"])
    return {
        "basis": "heat",
        "burn_dates_lag_heat": False,
        "heat_peak_date": dates[peak],
        "rules": _RULES,
        "first_burn_date": dates[start],
        "total_burned_km2": None,
        "median_growing_day_km2": None,
        "phases": phases,
        "pre_ignition": None,
        "record_end": record_end,
        "summary": " ".join(sentences),
        "caveats": [
            (
                "How phases are defined: the peak is the day with the most heat detections; a "
                f"flare-up is a later day at least {FLARE_UP_SHARE:.0%} as hot that rises "
                f"{FLARE_UP_RISE:g}× or more from the day before."
            ),
            (
                "Heat detections count hot pixels seen on one daytime satellite pass. They "
                "are not burned area, and cloud or smoke can hide them."
            ),
            "The archive has no burned-area labels for this event, so its size is not measured.",
        ],
    }


#: Typed live, so the usual slips count too. "what is the lifecyle for bobcat fire"
#: missed and got the single-day answer, which says the life cycle cannot be told.
_LIFECYCLE_WORDS = re.compile(
    r"\blif?e?[\s-]?cy(?:cle|le|lce|cel)s?\b|生命周期|生命週期", re.IGNORECASE
)
#: A named day ("on 2020-09-18", "Sep 18") asks to see that day, not the whole fire.
_NAMED_DAY = re.compile(
    r"\d{4}-\d{2}-\d{2}"
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}\b"
    r"|\d{1,2}月\d{1,2}日",
    re.IGNORECASE,
)


def asks_about_lifecycle(question: str | None) -> bool:
    """Whether the user's own words ask about the fire's life cycle as a whole.

    Deliberately narrow: the life cycle has to be named, and naming a single day
    ("show the lifecycle ... on 2020-09-18") keeps the day-by-day answer it always
    had. Anything this misses simply gets the existing answer.
    """
    if not question:
        return False
    return bool(_LIFECYCLE_WORDS.search(question)) and not _NAMED_DAY.search(question)


def lifecycle_answer(
    question: str | None, analysis: dict[str, Any] | None
) -> dict[str, Any] | None:
    """The facts a life-cycle question is answered from, or None for any other question.

    `message` replaces the selected-day sentence, `facts` travel to the narrator
    and the browser, and `caveats` join the answer's details.
    """
    if not analysis or not asks_about_lifecycle(question):
        return None
    return {
        "message": analysis["summary"],
        "facts": {
            "phases": [
                {key: phase[key] for key in ("label", "start", "end", "note")}
                for phase in analysis["phases"]
            ],
            "before_first_burn": (analysis["pre_ignition"] or {}).get("note"),
            "record_end": analysis["record_end"]["note"],
            "rules": analysis["rules"],
        },
        "caveats": analysis["caveats"],
    }
