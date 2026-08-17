"""Pure-function tests for spatial grounding. No network."""

from wildfire_agent.geocoding import (
    GeocodeCandidate,
    bbox_from_center,
    haversine_km,
    is_ambiguous,
    normalize_query,
)


def _c(name: str, lon: float, lat: float, importance: float = 0.5) -> GeocodeCandidate:
    return GeocodeCandidate(name, lon, lat, "nominatim", importance)


class TestNormalizeQuery:
    """Regression: sending 'near Madison' verbatim to Nominatim matches a
    'Near Street' in Ohio and puts the whole map in the wrong state. The
    preposition has to come off first."""

    def test_strips_leading_preposition(self):
        assert normalize_query("near Madison") == "Madison"
        assert normalize_query("around Dane County") == "Dane County"
        assert normalize_query("close to Madison, WI") == "Madison, WI"
        assert normalize_query("  IN Madison ") == "Madison"

    def test_strips_repeatedly(self):
        assert normalize_query("near the Madison") == "Madison"

    def test_leaves_real_place_names_alone(self):
        assert normalize_query("Madison, WI") == "Madison, WI"
        # must not eat a word that legitimately appears in a place name
        assert normalize_query("Nearville") == "Nearville"

    def test_never_returns_empty(self):
        """All-modifier input is left alone so the geocoder fails honestly, rather
        than sending an empty query."""
        assert normalize_query("near") == "near"

    def test_strips_buffer_phrase(self):
        """After clarification the location reads '10 km buffer around Madison,
        WI'; sending that whole string to a geocoder finds nothing. The radius
        belongs to _extract_buffer_km, so strip it here."""
        assert normalize_query("10 km buffer around Madison, WI") == "Madison, WI"
        assert normalize_query("Madison, WI + 25km") == "Madison, WI"
        assert normalize_query("within 5 miles of Dane County") == "Dane County"

    def test_buffer_only_input_keeps_something_queryable(self):
        assert normalize_query("10 km") == "10 km"


class TestAmbiguity:
    def test_single_candidate_is_never_ambiguous(self):
        assert not is_ambiguous([_c("Madison, WI", -89.4, 43.07)])

    def test_far_apart_and_equally_prominent_is_ambiguous(self):
        assert is_ambiguous(
            [
                _c("Madison, Wisconsin", -89.4, 43.07, 0.60),
                _c("Madison, Alabama", -86.75, 34.70, 0.58),
            ]
        )

    def test_far_apart_but_one_clearly_dominant_is_not_ambiguous(self):
        """Madison WI is a state capital and far outranks its namesakes: take it and
        do not bother the user."""
        assert not is_ambiguous(
            [
                _c("Madison, Wisconsin", -89.4, 43.07, 0.80),
                _c("Madison, Ohio", -81.05, 41.77, 0.35),
            ]
        )

    def test_nearby_duplicates_are_not_ambiguous(self):
        """Two spellings of one place - either pick is fine, so it is not ambiguity."""
        assert not is_ambiguous(
            [
                _c("Madison, Dane County", -89.40, 43.073, 0.60),
                _c("Madison City Hall", -89.38, 43.075, 0.59),
            ]
        )


class TestGeometry:
    def test_bbox_brackets_the_center(self):
        w, s, e, n = bbox_from_center(-89.4012, 43.0731, 10)
        assert w < -89.4012 < e
        assert s < 43.0731 < n

    def test_bbox_half_height_matches_radius(self):
        _, s, _, n = bbox_from_center(-89.4012, 43.0731, 10)
        assert abs((n - s) / 2 * 111.32 - 10) < 0.1

    def test_haversine_matches_known_distance(self):
        # Madison WI → Milwaukee WI ≈ 122 km
        madison = _c("Madison", -89.4012, 43.0731)
        milwaukee = _c("Milwaukee", -87.9065, 43.0389)
        assert 115 < haversine_km(madison, milwaukee) < 130
