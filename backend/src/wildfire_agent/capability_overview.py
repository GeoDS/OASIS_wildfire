"""What this deployment can be asked to do, as structured facts.

Answers "what can you do?" - the first thing a new user types, and until now the
one question the system handled worst. One phrasing improvised an answer from
the discussion prompt and trailed off into what was not on screen; another ran
the whole pipeline and asked which geographic area to consider. A question about
the system is not a question about a place.

The topics are declared here rather than derived, because a capability is only
useful to a user as *a question they can type*, and no registry holds those. Two
rules keep the declaration honest: every `ask` must be wording that actually
triggers the topic, and every topic must correspond to something in
`docs/06-how-to-use.md`, which is verified end to end rather than aspirational.

Everything else *is* derived - the archive from the local catalogue, the
approval-gated sources from `RENDERER_COVERAGE` - so the parts most likely to
drift are the parts nobody has to remember to update.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .planning.capabilities import RENDERER_COVERAGE


@dataclass(frozen=True)
class CapabilityTopic:
    """One kind of question, with wording that reliably triggers it."""

    title: str
    #: Wording that actually works. Copied from `docs/06`, not invented here: an
    #: example the system then fails to honour is worse than no example.
    ask: str
    does: str
    data: str
    #: True when the wording only means something after an earlier answer.
    #: "Which cities did it reach?" has no antecedent on a fresh session. Fine
    #: to mention in prose, never offered as a one-click option - the click
    #: sends it immediately, and "it" would refer to nothing.
    follow_up: bool = False


#: Ordered as a newcomer would meet them: what is happening now, then a specific
#: fire, then who it affected, then what follows it.
TOPICS: tuple[CapabilityTopic, ...] = (
    CapabilityTopic(
        title="Current fire activity nationally",
        ask="Are there any ongoing wildfires in the USA?",
        does="Lists the fire perimeters agencies have currently published, largest first.",
        data="NIFC WFIGS current interagency perimeters",
    ),
    CapabilityTopic(
        title="Conditions for a place right now",
        ask="Is there a fire near Altadena right now?",
        does=(
            "Resolves the place to its Census boundary, then reports weather, air "
            "quality, whether any mapped perimeter overlaps it, and satellite thermal "
            "detections in scope."
        ),
        data="NWS, Open-Meteo, WFIGS, NASA FIRMS, TIGER/Line Places",
    ),
    CapabilityTopic(
        title="A historical fire on one day",
        ask="Show the Woolsey fire on 2018-11-16.",
        does=(
            "Renders that day's active-fire labels over the burned area accumulated "
            "through that date, with pixel counts and area."
        ),
        data="TS-SatFire VIIRS_Day archive",
    ),
    CapabilityTopic(
        title="Which communities a fire reached",
        ask="Which cities did it reach?",
        does=(
            "Intersects the burned-area footprint with Census places, reporting both "
            "the area inside each place and its share of that place's own land."
        ),
        data="TS-SatFire labels x TIGER/Line Places",
        follow_up=True,
    ),
    CapabilityTopic(
        title="Who lives in those communities",
        ask="What's the median household income there?",
        does=(
            "Attaches population, housing, median age, seniors living alone, income "
            "and no-vehicle households to each place."
        ),
        data="U.S. Census ACS 5-year estimates, fetched on approval",
        follow_up=True,
    ),
    CapabilityTopic(
        title="How severely it burned",
        ask="How badly did the Bobcat fire burn between the first and last day?",
        does=(
            "Computes a burn-severity or vegetation index on two dates and subtracts "
            "them, masked to the mapped burned area."
        ),
        data="TS-SatFire VIIRS reflectance bands",
    ),
    CapabilityTopic(
        title="How it behaved and what it burned",
        ask="Which direction did the Bobcat fire spread, and how fast?",
        does=(
            "Three separate analyses over one event: spread direction and rate, the "
            "fire-weather record as observed, and land cover with terrain."
        ),
        data="TS-SatFire daily labels, local fire-weather record, ESRI_LULC",
    ),
    CapabilityTopic(
        title="What follows the fire",
        ask="Is there a debris flow risk?",
        does=(
            "Searches a public catalogue at request time for modelled post-fire "
            "debris-flow hazard areas over the burn scar."
        ),
        data="LA County Public Works, via ArcGIS catalogue search, on approval",
    ),
    CapabilityTopic(
        title="What this deployment holds",
        ask="What fires do you have data for?",
        does="Lists the archived events and what each one can actually answer.",
        data="The local TS-SatFire event catalogue",
    ),
)


def _archive(events: list[dict[str, Any]] | None) -> dict[str, Any]:
    """The archive as a compact roster.

    Takes what `api._archive_summary` already builds - name, span, and whether
    the event carries burned-area labels or only active-fire detections. Derived
    from the data rather than declared, so adding an event to the archive adds
    it here too.
    """
    if not events:
        return {}
    return {
        "event_count": len(events),
        "events": [
            {
                "name": event.get("name"),
                "span": (
                    f"{event['first_day']} to {event['last_day']}"
                    if event.get("first_day") and event.get("last_day")
                    else None
                ),
                # The distinction that decides which questions an event can
                # answer at all, so it travels with the roster rather than being
                # discovered by asking.
                "has_burned_area_labels": bool(event.get("burned_area")),
            }
            for event in events
        ],
    }


def _approval_required() -> list[dict[str, str]]:
    """Sources the user is asked about before they are contacted.

    Derived from the coverage declaration so that adding an approval-gated
    source cannot silently drop it from this answer.
    """
    return [
        {"hazard_object": coverage.hazard_object, "source": coverage.served_by}
        for coverage in RENDERER_COVERAGE.values()
        if coverage.needs_approval
    ]


def overview_facts(archive_events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Structured grounding for a capability answer. Builds no prose and calls
    no model: this is the material the narration is held to."""
    return {
        "topics": [
            {
                "title": t.title,
                "ask": t.ask,
                "does": t.does,
                "data": t.data,
                "follow_up": t.follow_up,
            }
            for t in TOPICS
        ],
        "archive": _archive(archive_events),
        "approval_required": _approval_required(),
        "consent_rule": (
            "An outside fetch is offered before it happens, asked once per session per "
            "source, and recorded with what it supplied."
        ),
    }


def _normalise(ask: str) -> str:
    """Compare example wording without tripping over punctuation or case."""
    return ask.strip().rstrip("?.!").strip().casefold()


#: The trio a newcomer can act on immediately: what is happening now, one place,
#: one archived fire that supports every downstream question. Used when the
#: narration named nothing usable, so the menu is never empty.
FALLBACK_ASKS: tuple[str, ...] = (
    "Are there any ongoing wildfires in the USA?",
    "Is there a fire near Altadena right now?",
    "Show the Woolsey fire on 2018-11-16.",
)


def suggestions_for(asks: list[str] | tuple[str, ...]) -> list[dict[str, str]]:
    """Turn the examples a reply used into the options offered beneath it.

    The model names which examples it led with; this checks them against the
    declared topics and drops anything that is not one, the same way the planner
    validates a layer proposal. An invented example is worse here than in prose:
    prose the user has to retype, but an offered option is one click from being
    sent, and wording that does not trigger its topic fails in front of them.
    """
    # Follow-ups are excluded rather than filtered later: a clicked option is
    # sent immediately, so "Which cities did it reach?" would arrive with
    # nothing for "it" to refer to.
    by_ask = {_normalise(topic.ask): topic for topic in TOPICS if not topic.follow_up}
    chosen: list[CapabilityTopic] = []
    for ask in asks:
        topic = by_ask.get(_normalise(ask))
        if topic is not None and topic not in chosen:
            chosen.append(topic)

    if not chosen:
        chosen = [by_ask[_normalise(ask)] for ask in FALLBACK_ASKS]

    return [{"title": t.title, "ask": t.ask, "does": t.does} for t in chosen]


def fallback_answer(facts: dict[str, Any]) -> str:
    """The same answer, composed without a model.

    `build_plan` falls back to a registry lookup when the model is unreachable,
    for the reason stated there: the demo must survive a model outage. This is
    the same idea for the question a reviewer asks first, and it earns its place
    twice over - a reasoning model can spend its whole token budget thinking and
    return nothing, which is a failure no retry policy distinguishes from an
    outage, and the mock provider has no model to call at all.

    Deliberately plainer than the narrated reply rather than a stub: a user who
    hits this gets a complete answer, not an apology.
    """
    topics = facts.get("topics") or []
    archive = facts.get("archive") or {}
    lines: list[str] = []

    if topics:
        lines.append(
            "You can ask about current fire activity, conditions for a place, and "
            "archived fires - what they burned, which communities they reached, and "
            "what followed. Wording that works, if you want to start somewhere:"
        )
        for topic in topics[:3]:
            lines.append(f'  "{topic["ask"]}" - {topic["does"]}')

    count = archive.get("event_count")
    if count:
        named = [
            event["name"]
            for event in (archive.get("events") or [])
            if event.get("has_burned_area_labels") and event.get("name")
        ][:3]
        sentence = f"The archive is a fixed set of {count} past fires"
        if named:
            sentence += ", including " + ", ".join(named)
        lines.append(sentence + ". Each one answers only what its own data supports.")

    if facts.get("approval_required"):
        lines.append(
            "Some sources are fetched only after you approve them, and you are asked "
            "once per source per session."
        )

    return "\n".join(lines)
