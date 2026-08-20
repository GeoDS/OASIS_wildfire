"""Pure parsing and safety checks; no public endpoint is called."""

from __future__ import annotations

import unittest

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
