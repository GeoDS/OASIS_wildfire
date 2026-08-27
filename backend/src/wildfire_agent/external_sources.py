"""What this deployment can fetch from outside, and what each source supplies.

One definition, three readers: the turn resolver decides whether the user asked
for a fetch, and the two narration prompts decide whether "not available" is an
honest answer or a refusal hiding a source that is one question away.

It lives on its own because none of the three owns it. Keeping it in
`conversation` made the narrator import the resolver to learn what the backend
can do, which is not a fact about conversation.

Adding a source here makes it requestable by name and makes the narrator offer
it, so nothing may be listed that the backend cannot actually honour.
"""

from __future__ import annotations

#: Source id -> what it supplies, phrased for a model to read.
KNOWN_SOURCES: dict[str, str] = {
    "census_acs": (
        "U.S. Census ACS attributes for any Census place on the map - population, "
        "housing units, median age, median household income, households without a vehicle"
    ),
    "portal_debris_flow": (
        "Post-fire debris-flow hazard polygons for a burn scar, discovered in a public "
        "ArcGIS catalogue at request time"
    ),
}


#: Place variables the ACS fill attaches, and the wording a question uses to ask
#: for each. The synonyms are the point: a regex over this list is what broke on
#: "how wealthy are those places", so the model is told what the words mean
#: rather than left to match them. Kept in step with `exposure.FETCHED_PROPERTIES`
#: by a test - a variable missing here cannot be asked for at all.
KNOWN_VARIABLES: dict[str, str] = {
    "population": "how many people live there - residents, inhabitants, who lives there, size",
    "housingUnits": "how many housing units - homes, houses, dwellings, housing stock",
    "medianAge": "median age - how old the population is, age structure",
    "seniorsLivingAlone": "people aged 65+ living alone - elderly living alone, isolated seniors",
    "medianHouseholdIncome": (
        "median household income - also how wealthy, affluent, rich or poor a place is, "
        "earnings, what people make, cost of living, economic status"
    ),
    "householdsWithoutVehicle": (
        "households with no vehicle - carless households, who could not drive out, "
        "evacuation capacity, mobility"
    ),
}


def variable_catalogue() -> str:
    """The place variables as prompt text."""
    return "\n".join(f"- {name}: {what}" for name, what in KNOWN_VARIABLES.items())


def catalogue() -> str:
    """The source list as prompt text, so every prompt renders it identically."""
    return "\n".join(f"- {name}: {what}" for name, what in KNOWN_SOURCES.items())
