"""The Census API reports two different failures as things that look like success."""

from __future__ import annotations

import json

import pytest

from wildfire_agent import census_acs
from wildfire_agent.census_acs import (
    SUPPLIES,
    VARIABLES,
    CensusUnavailable,
    PlaceAttributes,
    _split_geoid,
    fetch_place_attributes,
    provenance,
)


class _Response:
    """Stands in for urlopen's context manager."""

    def __init__(self, body: str, status: int = 200, content_type: str = "application/json"):
        self._body = body.encode()
        self.status = status
        self.headers = {"Content-Type": content_type}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _serve(monkeypatch, response):
    monkeypatch.setattr(census_acs.settings, "census_api_key", "test-key")
    monkeypatch.setattr(census_acs.urllib.request, "urlopen", lambda *a, **k: response)


def test_a_geoid_is_split_into_state_and_place():
    """The joined form is what TIGER writes and what the API silently rejects."""
    assert _split_geoid("0648648") == ("06", "48648")
    assert _split_geoid("0601290") == ("06", "01290")


def test_a_geoid_too_short_to_split_is_refused():
    with pytest.raises(CensusUnavailable, match="too short"):
        _split_geoid("06")


def test_an_empty_204_is_reported_as_a_malformed_geography(monkeypatch):
    """204 is how the API answers a bad geography - not how it says "no data".

    Treating it as an empty result would report "this city has no population",
    which is a false statement about the place rather than about the request.
    """
    _serve(monkeypatch, _Response("", status=204))
    with pytest.raises(CensusUnavailable, match="malformed"):
        fetch_place_attributes("0648648")


def test_an_html_page_served_as_200_is_reported_as_a_key_problem(monkeypatch):
    """A missing key redirects to an HTML page with a 200 status."""
    _serve(monkeypatch, _Response("<html><title>Missing Key</title></html>", content_type="text/html"))
    with pytest.raises(CensusUnavailable, match="missing or rejected API key"):
        fetch_place_attributes("0648648")


def test_no_configured_key_is_refused_before_any_request(monkeypatch):
    monkeypatch.setattr(census_acs.settings, "census_api_key", "")

    def explode(*args, **kwargs):
        raise AssertionError("should not have reached the network")

    monkeypatch.setattr(census_acs.urllib.request, "urlopen", explode)
    with pytest.raises(CensusUnavailable, match="CENSUS_API_KEY"):
        fetch_place_attributes("0648648")


def test_values_are_parsed_and_suppression_is_not_read_as_zero(monkeypatch):
    """ACS writes -666666666 for an estimate it will not publish.

    Parsing that as a number would put a large negative income on the map.
    """
    header = ["NAME"] + [v.code for v in VARIABLES] + ["state", "place"]
    row = ["Monrovia city, California"]
    for variable in VARIABLES:
        row.append("-666666666" if variable.code == "B19013_001E" else "100")
    row += ["06", "48648"]
    _serve(monkeypatch, _Response(json.dumps([header, row])))

    attributes = fetch_place_attributes("0648648")
    assert isinstance(attributes, PlaceAttributes)
    assert attributes.values["B01003_001E"] == 100
    assert "B19013_001E" not in attributes.values
    assert "Median household income" in attributes.suppressed


def test_supplies_is_derived_from_the_variable_table():
    """The claim and the fetch cannot drift apart if one is built from the other."""
    assert SUPPLIES == tuple(dict.fromkeys(v.supplies for v in VARIABLES))
    # Deliberately not claimed: verified codes only.
    assert "language isolation" not in SUPPLIES
    assert "mobility limitation" not in SUPPLIES


def test_provenance_carries_what_the_review_asked_to_record():
    record = provenance()
    for field in ("source", "dataset", "geography", "spatial_resolution", "coverage"):
        assert record[field]
    joined = " ".join(record["quality_notes"])
    # The figure is for the whole place, and the caveat has to say so - this is
    # the number most likely to be multiplied by a burned-area share.
    assert "must not be" in joined
    assert "margin of error" in joined
