"""American Community Survey attributes, fetched on demand for known places.

This is the first source that fills a gap rather than drawing a layer. The place
geometry is already local - TIGER/Line, with its GEOID - so nothing here fetches
shapes. It fetches the attributes those shapes are missing and attaches them,
which is what "completing a variable" means when the geography is already known.

Two things about this API cost an afternoon if you meet them by surprise:

- **A GEOID is not a place code.** TIGER writes `0648648`, which is state `06`
  followed by place `48648`. The API wants those separately. Passing the joined
  form returns HTTP 204 with an empty body - not an error, just nothing.
- **Failure looks like success.** A missing key redirects to an HTML page served
  as HTTP 200. Any client that checks the status code and then parses JSON gets
  a confusing crash; any client that catches that broadly reports "no population
  data for this city", which is a lie about the data rather than the request.

So this module separates *the request was wrong*, *the key is missing*, and
*this place genuinely has no value* into three different outcomes.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from .config import settings

#: 2020-2024 5-year estimates: available for every place regardless of size,
#: which the 1-year tables are not.
DATASET = "2024/acs/acs5"
BASE_URL = f"https://api.census.gov/data/{DATASET}"
TIMEOUT_SECONDS = 20.0


#: ACS variables, and which taxonomy variable each one answers. Only variables
#: verified against the live API are listed; a code that looks plausible and
#: returns nothing is worse than an admitted gap.
@dataclass(frozen=True)
class AcsVariable:
    code: str
    label: str
    #: The `required_variables` entry this contributes to.
    supplies: str
    unit: str = ""


VARIABLES: tuple[AcsVariable, ...] = (
    AcsVariable("B01003_001E", "Total population", "population count", "people"),
    AcsVariable("B25001_001E", "Housing units", "housing density", "units"),
    AcsVariable("B01002_001E", "Median age", "age structure", "years"),
    AcsVariable("B09021_022E", "People 65+ living alone", "age structure", "people"),
    AcsVariable("B19013_001E", "Median household income", "income", "USD"),
    AcsVariable(
        "B25044_003E", "Owner households with no vehicle", "no-vehicle households", "households"
    ),
    AcsVariable(
        "B25044_010E", "Renter households with no vehicle", "no-vehicle households", "households"
    ),
)

#: What this source can close, derived from the table above so the two cannot
#: drift apart. `language isolation` and `mobility limitation` are deliberately
#: absent: ACS carries them, but only as multi-part aggregates that were not
#: verified here, and claiming them would hide a gap that is still real.
SUPPLIES: tuple[str, ...] = tuple(dict.fromkeys(v.supplies for v in VARIABLES))

#: ACS suppresses some estimates; these sentinels mean "not published", not zero.
_SUPPRESSED = {"-666666666", "-999999999", "-888888888", None, ""}


class CensusUnavailable(RuntimeError):
    """The request could not be made or answered. Distinct from an empty result."""


@dataclass
class PlaceAttributes:
    """One place's fetched attributes, with enough provenance to be checked."""

    geoid: str
    name: str
    values: dict[str, Any] = field(default_factory=dict)
    suppressed: tuple[str, ...] = ()

    def as_properties(self) -> dict[str, Any]:
        return dict(self.values)


def _split_geoid(geoid: str) -> tuple[str, str]:
    """`0648648` -> (`06`, `48648`). The API rejects the joined form silently."""
    digits = "".join(ch for ch in geoid if ch.isdigit())
    if len(digits) < 4:
        raise CensusUnavailable(f"GEOID {geoid!r} is too short to split into state and place")
    return digits[:2], digits[2:]


def _request(url: str) -> list[list[str]]:
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as response:
            status = response.status
            body = response.read().decode("utf-8", errors="replace")
            content_type = response.headers.get("Content-Type", "")
    except urllib.error.URLError as exc:
        raise CensusUnavailable(f"Census API unreachable: {exc}") from exc

    if status == 204 or not body.strip():
        # The API answers a malformed geography with an empty 204 rather than an
        # error, so this is the most likely cause and the most useful message.
        raise CensusUnavailable(
            "Census API returned no content. This usually means the geography was "
            "malformed - a GEOID passed whole instead of split into state and place."
        )
    if "json" not in content_type.lower() or body.lstrip()[:1] not in "[{":
        raise CensusUnavailable(
            "Census API returned a page instead of data, which is how it reports a "
            "missing or rejected API key. Set CENSUS_API_KEY."
        )
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise CensusUnavailable(f"Census API returned unreadable JSON: {exc}") from exc


def fetch_place_attributes(geoid: str) -> PlaceAttributes:
    """Fetch the verified ACS variables for one Census place."""
    key = settings.census_api_key
    if not key:
        raise CensusUnavailable(
            "No CENSUS_API_KEY is configured. Every Census API query now requires one; "
            "the previous keyless allowance no longer applies."
        )
    state, place = _split_geoid(geoid)
    query = urllib.parse.urlencode(
        {
            "get": "NAME," + ",".join(v.code for v in VARIABLES),
            "for": f"place:{place}",
            "in": f"state:{state}",
            "key": key,
        },
        safe=":",  # the API rejects a percent-encoded colon in for/in
    )
    rows = _request(f"{BASE_URL}?{query}")
    if len(rows) < 2:
        raise CensusUnavailable(f"Census API returned no row for place {geoid}")

    record = dict(zip(rows[0], rows[1], strict=False))
    values: dict[str, Any] = {}
    suppressed: list[str] = []
    for variable in VARIABLES:
        raw = record.get(variable.code)
        if raw in _SUPPRESSED:
            suppressed.append(variable.label)
            continue
        try:
            values[variable.code] = float(raw) if "." in str(raw) else int(raw)
        except (TypeError, ValueError):
            suppressed.append(variable.label)
    return PlaceAttributes(
        geoid=geoid,
        name=str(record.get("NAME") or ""),
        values=values,
        suppressed=tuple(suppressed),
    )


def provenance() -> dict[str, Any]:
    """Source, vintage, geography and quality notes, as the review asked for."""
    return {
        "source": "U.S. Census Bureau, American Community Survey",
        "dataset": "ACS 5-year estimates, 2020-2024",
        "endpoint": BASE_URL,
        "geography": "Census place (incorporated places and CDPs)",
        "spatial_resolution": "whole place; not resolved below the municipal boundary",
        "coverage": "United States",
        "variables": [
            {"code": v.code, "label": v.label, "supplies": v.supplies, "unit": v.unit}
            for v in VARIABLES
        ],
        "quality_notes": [
            "5-year estimates are averages over 2020-2024, not a single-year count.",
            (
                "Estimates carry a margin of error that this response does not fetch; "
                "small places have proportionally wider intervals."
            ),
            (
                "Values are for the whole place. A fire touching part of a place does not "
                "affect that share of its population, and these figures must not be "
                "multiplied by a burned-area share to imply that it does."
            ),
        ],
    }
