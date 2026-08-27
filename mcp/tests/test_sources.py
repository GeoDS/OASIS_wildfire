"""Pure parsing and safety checks; no public endpoint is called."""

from __future__ import annotations

import io
import unittest

import pytest

from firescope_mcp import sources
from firescope_mcp.scope import SOCAL_BBOX
from firescope_mcp.sources import (
    _air_quality_feature,
    _feature_collection,
    _parse_hms,
    _validate_hms_day,
    _validate_url,
    _weather_features,
    fetch_public_geojson,
)


class SourceTests(unittest.TestCase):
    def test_socal_scope_accepts_altadena_and_rejects_madison(self):
        self.assertTrue(SOCAL_BBOX.contains(-118.1312, 34.1897))
        self.assertFalse(SOCAL_BBOX.contains(-89.4012, 43.0731))

    def test_hms_parser_filters_to_socal_and_deduplicates(self):
        text = """Lon,Lat,YearDay,Time,Satellite,Method,Ecosystem,FRP
-118.10,34.20,2025008,1200,GOES,Auto,Forest,12.5
-118.10,34.20,2025008,1200,GOES,Auto,Forest,12.5
-89.40,43.07,2025008,1200,GOES,Auto,Forest,4.0"""
        features, truncated = _parse_hms(text)

        self.assertEqual(len(features), 1)
        self.assertEqual(features[0]["geometry"]["coordinates"], [-118.1, 34.2])
        self.assertEqual(features[0]["properties"]["frpMw"], 12.5)
        self.assertFalse(truncated)

    def test_empty_feature_collection_is_a_valid_result(self):
        result = _feature_collection("wfigs", [], scope="test")

        self.assertEqual(result["metadata"]["status"], "empty")
        self.assertEqual(result["metadata"]["featureCount"], 0)
        self.assertIn("no records", result["metadata"]["notice"])

    def test_weather_builds_point_and_downwind_line(self):
        features = _weather_features(
            {
                "startTime": "2026-08-17T12:00:00-07:00",
                "temperature": 88,
                "temperatureUnit": "F",
                "relativeHumidity": {"value": 18},
                "windSpeed": "10 mph",
                "windDirection": "W",
                "shortForecast": "Sunny",
            },
            longitude=-118.1312,
            latitude=34.1897,
        )

        self.assertEqual(
            [feature["geometry"]["type"] for feature in features],
            ["Point", "LineString"],
        )
        self.assertTrue(features[1]["properties"]["derived"])
        self.assertEqual(features[1]["properties"]["reportedWind"], "From W at 10 mph")
        self.assertEqual(features[1]["properties"]["downwindDirection"], "E")
        self.assertIn("arrow points toward E", features[1]["properties"]["notice"])

    def test_air_quality_builds_one_honest_modeled_point(self):
        feature = _air_quality_feature(
            {
                "current": {"time": "2026-08-17T12:00", "us_aqi": 42, "pm2_5": 8.5},
                "current_units": {"pm2_5": "μg/m³"},
            },
            longitude=-118.13,
            latitude=34.19,
        )
        self.assertEqual(feature["properties"]["usAqi"], 42)
        self.assertTrue(feature["properties"]["modeled"])

    def test_rejects_non_allowlisted_upstream_url(self):
        with self.assertRaisesRegex(ValueError, "allow-list"):
            _validate_url("https://example.com/private")

    def test_rejects_malformed_or_future_hms_dates(self):
        with self.assertRaisesRegex(ValueError, "YYYYMMDD"):
            _validate_hms_day("2025-01-08")
        with self.assertRaisesRegex(ValueError, "future"):
            _validate_hms_day("29990101")

    def test_dispatch_rejects_unknown_source_before_network(self):
        with self.assertRaisesRegex(ValueError, "Unknown source"):
            fetch_public_geojson("madison")  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()


class TestFirmsSeparatesItsThreeFailures:
    """FIRMS answers a bad key with the plain text "Invalid MAP_KEY." under
    HTTP 400 on one endpoint and 401 on another. A client that checks the
    status, or that hands the body to a CSV reader, turns "we are not
    authorised" into "there are no fires here" - which is the lie this project
    exists to avoid.

    Three outcomes, three different answers: no key configured, a key the
    service rejected, and a genuine absence of detections.
    """

    def test_no_key_configured_is_not_an_absence_of_fire(self, monkeypatch):
        monkeypatch.setattr(sources, "FIRMS_MAP_KEY", "")
        payload = sources.fetch_firms()
        assert payload["features"] == []
        assert payload["metadata"]["status"] == "not_configured"
        assert "FIRMS_MAP_KEY" in payload["metadata"]["notice"]

    def test_a_rejected_key_is_not_an_absence_of_fire(self, monkeypatch):
        """FIRMS answers a bad key with HTTP 400 and the body "Invalid MAP_KEY.",
        so `_request_bytes` raises before anything can inspect the text.

        The first version of this test stubbed `_request_bytes` to *return* that
        body, which no real call ever does - it passed against a fiction while
        a rejected key surfaced to the user as a generic upstream failure."""
        import urllib.error

        def reject(*_a, **_k):
            raise urllib.error.HTTPError(
                "https://firms.modaps.eosdis.nasa.gov/api/area/csv/x", 400, "Bad Request",
                {}, io.BytesIO(b"Invalid MAP_KEY."),
            )

        monkeypatch.setattr(sources, "FIRMS_MAP_KEY", "bogus")
        monkeypatch.setattr(sources, "_request_bytes", reject)
        payload = sources.fetch_firms()
        assert payload["features"] == []
        assert payload["metadata"]["status"] == "rejected"
        assert "rejected" in payload["metadata"]["notice"].lower()

    def test_a_genuine_upstream_failure_is_not_called_a_rejected_key(self, monkeypatch):
        """A timeout is about the network, not about our credentials."""
        def boom(*_a, **_k):
            raise TimeoutError("read timed out")

        monkeypatch.setattr(sources, "FIRMS_MAP_KEY", "k")
        monkeypatch.setattr(sources, "_request_bytes", boom)
        assert sources.fetch_firms()["metadata"]["status"] != "rejected"

    def test_an_empty_result_says_nothing_was_detected(self, monkeypatch):
        header = b"latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,"
        header += b"instrument,confidence,version,bright_ti5,frp,daynight\n"
        monkeypatch.setattr(sources, "FIRMS_MAP_KEY", "k")
        monkeypatch.setattr(sources, "_request_bytes", lambda *a, **k: header)
        payload = sources.fetch_firms()
        assert payload["features"] == []
        assert payload["metadata"]["status"] == "ok"

    def test_a_detection_carries_intensity_and_confidence(self, monkeypatch):
        csv_text = (
            "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,"
            "instrument,confidence,version,bright_ti5,frp,daynight\n"
            "34.2035,-118.0692,367.4,0.39,0.36,2026-08-26,2118,N,VIIRS,n,2.0NRT,295.1,12.7,N\n"
        )
        monkeypatch.setattr(sources, "FIRMS_MAP_KEY", "k")
        monkeypatch.setattr(sources, "_request_bytes", lambda *a, **k: csv_text.encode())
        feature = sources.fetch_firms()["features"][0]
        props = feature["properties"]
        assert props["frpMw"] == 12.7
        assert props["confidence"] == "nominal"
        assert props["daynight"] == "night"
        assert props["brightnessKelvin"] == 367.4
        # The footprint is not 1 km except at nadir, and the project never lets
        # a detection stand in for a burned area.
        assert props["footprintKm2"] == pytest.approx(0.39 * 0.36, rel=1e-3)

    def test_a_detection_outside_the_scope_is_dropped(self, monkeypatch):
        csv_text = (
            "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,"
            "instrument,confidence,version,bright_ti5,frp,daynight\n"
            "48.0,-122.0,367.4,0.39,0.36,2026-08-26,2118,N,VIIRS,n,2.0NRT,295.1,12.7,N\n"
        )
        monkeypatch.setattr(sources, "FIRMS_MAP_KEY", "k")
        monkeypatch.setattr(sources, "_request_bytes", lambda *a, **k: csv_text.encode())
        assert sources.fetch_firms()["features"] == []


class TestTheNationalScopeIsDrawable:
    """237 national perimeters carry 1.1 million coordinates and 40 MB of
    GeoJSON. That is not a slow map, it is a broken one: the frontend's bounds
    helper spread every point into `Math.min(...)` and overflowed the call
    stack on every render.

    Generalising at ~1 km keeps all 237 features and drops the payload by two
    orders of magnitude. At national scale the difference is invisible - but it
    is still a difference, so the layer has to say so.
    """

    def test_the_national_request_generalises_its_geometry(self, monkeypatch):
        captured = {}

        def fake(url, *, params=None):
            captured.update(params or {})
            return {"type": "FeatureCollection", "features": []}

        monkeypatch.setattr(sources, "_request_json", fake)
        sources.fetch_wfigs(national=True)
        assert "maxAllowableOffset" in captured
        assert float(captured["maxAllowableOffset"]) > 0

    def test_the_demo_scope_keeps_full_resolution(self, monkeypatch):
        """Two perimeters at city scale is where the detail is the point."""
        captured = {}

        def fake(url, *, params=None):
            captured.update(params or {})
            return {"type": "FeatureCollection", "features": []}

        monkeypatch.setattr(sources, "_request_json", fake)
        sources.fetch_wfigs(national=False)
        assert "maxAllowableOffset" not in captured

    def test_a_generalised_boundary_says_it_is_approximate(self, monkeypatch):
        monkeypatch.setattr(
            sources, "_request_json",
            lambda *a, **k: {"type": "FeatureCollection", "features": []},
        )
        national = sources.fetch_wfigs(national=True)["metadata"]["caveat"].lower()
        assert "simplified" in national or "generalis" in national or "approximate" in national
        local = sources.fetch_wfigs(national=False)["metadata"]["caveat"].lower()
        assert "simplified" not in local
