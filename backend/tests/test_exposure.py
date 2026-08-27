"""Filling a gap must not turn an unreachable API into a fact about a place."""

from __future__ import annotations

from wildfire_agent import exposure
from wildfire_agent.census_acs import CensusUnavailable, PlaceAttributes
from wildfire_agent.exposure import enrich_places_with_acs
from wildfire_agent.planning.models import LayerResult


def _layer(*places: tuple[str, str]) -> LayerResult:
    return LayerResult(
        capability_id="burned_area_intersecting_place_boundaries",
        title="places",
        hazard_object="exposure",
        geometry_type="Polygon",
        caveat="A place is counted when it contains a label pixel centre.",
        feature_count=len(places),
        source="test",
        as_of="2020-09-27",
        geojson={
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": None, "properties": {"name": n, "geoid": g}}
                for n, g in places
            ],
        },
    )


def _attributes(geoid: str, population: int = 1000) -> PlaceAttributes:
    return PlaceAttributes(
        geoid=geoid,
        name="Test city",
        values={
            "B01003_001E": population,
            "B25001_001E": 400,
            "B01002_001E": 40.0,
            "B09021_022E": 90,
            "B19013_001E": 90000,
            "B25044_003E": 10,
            "B25044_010E": 20,
        },
    )


def test_attributes_are_attached_under_readable_names(monkeypatch):
    monkeypatch.setattr(exposure, "fetch_place_attributes", lambda g: _attributes(g))
    result = enrich_places_with_acs(_layer(("Monrovia", "0648648")))

    properties = result.layer.geojson["features"][0]["properties"]
    assert properties["population"] == 1000
    assert properties["medianHouseholdIncome"] == 90000
    # Two ACS columns answer one question and are reported as their sum.
    assert properties["householdsWithoutVehicle"] == 30
    assert result.closed


def test_one_unreachable_place_does_not_empty_the_others(monkeypatch):
    """"We could not reach the API for Duarte" and "nobody lives in Duarte"
    look identical on a map, so they must not arrive as the same answer."""

    def flaky(geoid: str):
        if geoid == "0619990":
            raise CensusUnavailable("Census API unreachable: timed out")
        return _attributes(geoid, population=37571)

    monkeypatch.setattr(exposure, "fetch_place_attributes", flaky)
    result = enrich_places_with_acs(_layer(("Monrovia", "0648648"), ("Duarte", "0619990")))

    monrovia, duarte = result.layer.geojson["features"]
    assert monrovia["properties"]["population"] == 37571
    # The failed place carries no population at all rather than a zero.
    assert "population" not in duarte["properties"]
    assert "Duarte" in result.failed
    assert "unreachable" in result.failed["Duarte"]
    assert "could not be fetched" in result.layer.caveat


def test_the_do_not_multiply_warning_travels_with_the_figures(monkeypatch):
    """These numbers sit beside a burned-area share, which invites the product."""
    monkeypatch.setattr(exposure, "fetch_place_attributes", lambda g: _attributes(g))
    result = enrich_places_with_acs(_layer(("Monrovia", "0648648")))
    assert "must not be multiplied by a burned-area share" in result.layer.caveat


def test_suppressed_estimates_are_named_rather_than_dropped_silently(monkeypatch):
    attributes = _attributes("0648648")
    attributes.values.pop("B19013_001E")
    attributes.suppressed = ("Median household income",)
    monkeypatch.setattr(exposure, "fetch_place_attributes", lambda g: attributes)

    result = enrich_places_with_acs(_layer(("Monrovia", "0648648")))
    assert result.suppressed["Monrovia"] == ("Median household income",)
    assert "suppressed some estimates" in result.layer.caveat


def test_a_feature_without_a_geoid_is_reported_not_guessed(monkeypatch):
    def explode(geoid: str):
        raise AssertionError("should not fetch without a GEOID")

    monkeypatch.setattr(exposure, "fetch_place_attributes", explode)
    result = enrich_places_with_acs(_layer(("Nowhere", "")))
    assert "Nowhere" in result.failed
    assert result.closed == ()


def test_nothing_is_claimed_closed_when_every_place_failed(monkeypatch):
    """A fill that fetched nothing has closed nothing, and must not say otherwise."""

    def always_fail(geoid: str):
        raise CensusUnavailable("no key")

    monkeypatch.setattr(exposure, "fetch_place_attributes", always_fail)
    result = enrich_places_with_acs(_layer(("Monrovia", "0648648")))
    assert result.closed == ()
    assert result.source == {}


def test_a_layer_that_already_carries_the_figures_closes_the_gap(monkeypatch):
    """The taxonomy declares what a *kind* of layer lacks, not what this one has.

    Deriving the gap from the static table alone is what made the same fetch be
    offered again after it had been approved and had succeeded: the table cannot
    see the population sitting on the features.
    """
    monkeypatch.setattr(exposure, "fetch_place_attributes", lambda g: _attributes(g))
    enriched = enrich_places_with_acs(_layer(("Monrovia", "0648648"))).layer

    assert "population count" in exposure.variables_present(enriched)
    assert "housing density" in exposure.variables_present(enriched)
    # A bare layer supplies none of them.
    assert exposure.variables_present(_layer(("Monrovia", "0648648"))) == ()


def test_an_offer_that_would_close_nothing_new_is_not_made(monkeypatch):
    """Asking again after a successful fetch spends the user's one question on
    data they already have."""
    monkeypatch.setattr(exposure, "fetch_place_attributes", lambda g: _attributes(g))
    enriched = enrich_places_with_acs(_layer(("Monrovia", "0648648"))).layer
    gap = ("population count", "housing density", "WUI boundary")

    assert exposure.offer_for("exposure", gap) is not None
    assert exposure.offer_for("exposure", gap, already=exposure.variables_present(enriched)) is None


def test_an_offer_still_stands_for_a_variable_the_layer_lacks():
    """Present-and-absent are decided per variable, not for the whole fill."""
    offer = exposure.offer_for(
        "exposure",
        ("population count", "income"),
        already=("population count",),
    )
    assert offer is not None
    assert offer["closes"] == ("income",)
