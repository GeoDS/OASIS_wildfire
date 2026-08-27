"""A hazard nothing local carries, found at request time."""

from __future__ import annotations

from wildfire_agent import debris_flow
from wildfire_agent.debris_flow import HAZARD_OBJECT, SUPPLIES, _fire_filter, fetch, offer
from wildfire_agent.portals import Discovery, FetchedLayer


def _hit(url: str = "https://services.arcgis.com/x/FeatureServer") -> Discovery:
    return Discovery(title="Post-Fire Debris Flow", owner="county", url=url, portal_id="arcgis_online")


def test_the_hazard_object_is_one_the_taxonomy_declares():
    from wildfire_agent.taxonomy import HAZARD_OBJECTS

    assert HAZARD_OBJECT in HAZARD_OBJECTS
    declared = set(HAZARD_OBJECTS[HAZARD_OBJECT].required_variables)
    assert set(SUPPLIES) < declared
    # Rainfall thresholds are not published with the hazard polygons, and the
    # gap has to survive the fetch rather than be quietly absorbed by it.
    assert "rainfall threshold" in declared - set(SUPPLIES)


def test_a_fire_name_is_escaped_into_the_filter():
    """The name reaches a SQL-ish `where`, so a quote in it must not break out."""
    # Both spellings are matched now - the publisher uses both - so this checks
    # the escaping and the forms, not one exact string.
    assert "FIRE='Bobcat Fire'" in _fire_filter("Bobcat Fire")
    assert "FIRE='Bobcat Fire'" in _fire_filter("Bobcat")
    assert "''" in _fire_filter("O'Brien Fire")


def test_the_offer_names_what_it_leaves_missing(monkeypatch):
    monkeypatch.setattr(debris_flow, "discover", lambda **k: [_hit()])
    made = offer("Bobcat Fire", ("burn scar extent", "rainfall threshold"))
    assert made["closes"] == ("burn scar extent",)
    assert made["remaining"] == ("rainfall threshold",)
    assert made["endpoint"].startswith("https://")


def test_no_offer_when_discovery_finds_nothing(monkeypatch):
    monkeypatch.setattr(debris_flow, "discover", lambda **k: [])
    assert offer("Bobcat Fire", ("burn scar extent",)) is None


def test_no_offer_when_it_would_close_nothing(monkeypatch):
    monkeypatch.setattr(debris_flow, "discover", lambda **k: [_hit()])
    assert offer("Bobcat Fire", ("rainfall threshold",)) is None


def test_a_service_without_a_hazard_layer_says_so(monkeypatch):
    monkeypatch.setattr(
        debris_flow, "describe_service", lambda url: {"layers": [{"id": 0, "name": "Streams"}]}
    )
    result = fetch("Bobcat Fire", "https://services.arcgis.com/x/FeatureServer")
    assert result.layer is None
    assert "no potential-hazard layer" in result.note


def test_a_fire_the_service_does_not_cover_is_an_answer_not_a_failure(monkeypatch):
    """"This county published nothing for that fire" is a fact about the fire."""
    monkeypatch.setattr(
        debris_flow,
        "describe_service",
        lambda url: {"layers": [{"id": 1, "name": "Phase 1 Potential Hazard Areas"}]},
    )
    monkeypatch.setattr(
        debris_flow,
        "fetch_features",
        lambda url, **k: FetchedLayer(
            geojson={"type": "FeatureCollection", "features": []}, feature_count=0, truncated=False
        ),
    )
    result = fetch("Nowhere Fire", "https://services.arcgis.com/x/FeatureServer")
    assert result.layer is None
    assert "publishes no hazard area" in result.note
    assert result.closed == ()


class TestThePublishersOwnSpelling:
    """`_fire_filter` appended " Fire" whenever the name lacked it, which
    assumed every publisher writes "X Fire". Los Angeles County writes
    'Bobcat Fire' but plain 'Woolsey', so the filter matched 0 of the 2,248
    hazard polygons published for Woolsey and the system reported that the
    service "publishes no hazard area for Woolsey Fire" - a false negative
    stated as a fact about the fire.
    """

    def test_both_spellings_are_matched(self):
        where = debris_flow._fire_filter("Woolsey Fire")
        assert "'Woolsey Fire'" in where
        assert "'Woolsey'" in where

    def test_a_name_given_without_the_suffix_matches_both_too(self):
        where = debris_flow._fire_filter("Woolsey")
        assert "'Woolsey Fire'" in where
        assert "'Woolsey'" in where

    def test_a_quote_in_the_name_is_still_escaped(self):
        """An apostrophe must not end the literal - this string is sent as SQL."""
        where = debris_flow._fire_filter("O'Brien Fire")
        assert "O''Brien Fire" in where
        assert "O'Brien Fire'" not in where.replace("O''Brien", "X")

    def test_the_filter_is_a_disjunction_not_a_concatenation(self):
        where = debris_flow._fire_filter("Bobcat Fire")
        assert where.count("FIRE=") == 2
        assert " OR " in where
