from wildfire_agent import context_layers
from wildfire_agent.context_layers import (
    PlaceBoundary,
    city_context_status,
    city_subject_layer,
    city_weather_status,
    communities_intersecting_active_fire,
    communities_intersecting_burned_area,
    communities_nearest_burned_area,
    communities_nearest_fire,
    filter_layer_to_subject,
    nearby_communities_layer,
)
from wildfire_agent.contract import AnalysisContract, ResolvedLocation, SpatialSlot
from wildfire_agent.planning.models import LayerResult
from wildfire_agent.raster_layers import active_fire_label_points, burned_area_label_points


def _layer(count: int, properties: dict | None = None) -> LayerResult:
    features = (
        []
        if not count
        else [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-118, 34]},
                "properties": properties or {},
            }
        ]
    )
    return LayerResult(
        capability_id="test",
        title="test",
        hazard_object="active_fire",
        family=None,
        geometry_type="Point",
        caveat="test",
        feature_count=count,
        source="test",
        geojson={"type": "FeatureCollection", "features": features},
    )


def test_city_without_current_perimeter_reports_low_evidence_not_zero_risk():
    status = city_context_status(
        "Pasadena",
        _layer(0),
        _layer(1, {"usAqi": 42, "pm25": 8.5, "pm25Unit": "μg/m³"}),
        _layer(1, {"shortForecast": "Sunny", "windDirection": "W", "windSpeed": "8 mph"}),
    )
    assert status["evidence"] == "low"
    assert "does not appear to be directly affected" in status["message"]
    assert "Conditions can change" in status["message"]
    assert any("AQI" in detail for detail in status["details"])


def test_city_with_current_perimeter_leads_with_plain_language_conclusion():
    status = city_context_status("Pasadena", _layer(1), None, None)
    assert status["evidence"] == "elevated"
    assert status["message"].startswith(
        "Pasadena may currently be directly affected by an active fire."
    )


def test_weather_only_status_leads_with_weather_and_excludes_fire_assessment():
    status = city_weather_status(
        "Santa Barbara",
        _layer(
            1,
            {
                "shortForecast": "Mostly Sunny",
                "temperature": 72,
                "temperatureUnit": "F",
                "relativeHumidity": 48,
                "windDirection": "W",
                "windSpeed": "10 mph",
            },
        ),
    )
    assert status["focus"] == "weather"
    assert "Mostly Sunny" in status["message"]
    assert "72°F" in status["message"]
    assert "fire-impact" not in status["message"]


def test_fire_workflow_adds_nearby_communities_as_proximity_only():
    contract = AnalysisContract(
        original_request="Show the Bobcat Fire",
        slots={
            "location": SpatialSlot(
                value="Bobcat Fire",
                resolved=ResolvedLocation(
                    display_name="Bobcat Fire",
                    center=(-117.93, 34.33),
                    buffer_km=25,
                    bbox=(-118.2, 34.1, -117.66, 34.56),
                ),
            )
        },
    )
    layer = nearby_communities_layer(contract, "Bobcat Fire")
    assert layer is not None
    assert layer.feature_count > 0
    assert "does not prove" in layer.caveat


def test_city_subject_uses_census_place_polygon_instead_of_buffer():
    contract = AnalysisContract(
        original_request="Is Pasadena affected by fire?",
        slots={
            "location": SpatialSlot(
                value="Pasadena",
                resolved=ResolvedLocation(
                    display_name="Pasadena, California, USA",
                    center=(-118.1445, 34.1478),
                    buffer_km=25,
                    bbox=(-118.42, 33.92, -117.87, 34.37),
                ),
            )
        },
    )
    layer = city_subject_layer(contract)
    assert layer is not None
    assert layer.capability_id == "subject_city_boundary"
    assert layer.visualization is not None
    assert layer.visualization.label == "Resolved place boundary"
    assert layer.geojson["features"][0]["properties"]["name"] == "Pasadena"
    assert layer.geojson["features"][0]["geometry"]["type"] in {
        "Polygon",
        "MultiPolygon",
    }


def test_fire_perimeters_are_filtered_against_city_polygon_not_search_bbox():
    subject = LayerResult(
        capability_id="subject_city_boundary",
        title="subject",
        hazard_object="population_exposure",
        geometry_type="Polygon",
        caveat="test",
        feature_count=1,
        source="test",
        geojson={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[-2, -2], [2, -2], [2, 2], [-2, 2], [-2, -2]]],
                    },
                    "properties": {},
                }
            ],
        },
    )
    perimeter = subject.model_copy(
        update={
            "capability_id": "fire",
            "feature_count": 2,
            "geojson": {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[[1, 1], [3, 1], [3, 3], [1, 3], [1, 1]]],
                        },
                        "properties": {"name": "intersects"},
                    },
                    {
                        "type": "Feature",
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[[4, 4], [5, 4], [5, 5], [4, 5], [4, 4]]],
                        },
                        "properties": {"name": "outside"},
                    },
                ],
            },
        }
    )
    result = filter_layer_to_subject(perimeter, subject)
    assert result.feature_count == 1
    assert result.geojson["features"][0]["properties"]["name"] == "intersects"


def test_nearest_fire_places_are_ranked_by_reference_point_distance(monkeypatch):
    places = (
        PlaceBoundary(
            name="Nearville",
            legal_name="Nearville city",
            geoid="1",
            class_code="C1",
            center=(2.0, 0.5),
            bbox=(1.9, 0.4, 2.1, 0.6),
            geometry={},
        ),
        PlaceBoundary(
            name="Farville",
            legal_name="Farville city",
            geoid="2",
            class_code="C1",
            center=(5.0, 0.5),
            bbox=(4.9, 0.4, 5.1, 0.6),
            geometry={},
        ),
    )
    monkeypatch.setattr(context_layers, "california_places", lambda: places)
    fire = LayerResult(
        capability_id="fire",
        title="fire",
        hazard_object="active_fire",
        geometry_type="Polygon",
        caveat="test",
        feature_count=1,
        source="test",
        geojson={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
                    },
                    "properties": {},
                }
            ],
        },
    )

    layer = communities_nearest_fire(fire, "Test Fire", limit=2)
    assert layer is not None
    features = layer.geojson["features"]
    assert [feature["properties"]["name"] for feature in features] == [
        "Nearville",
        "Farville",
    ]
    assert features[0]["properties"]["distanceKm"] < features[1]["properties"]["distanceKm"]
    assert "does not establish fire impact" in layer.caveat


def test_burned_area_pixels_use_place_polygons_then_rank_nearest(monkeypatch):
    places = (
        PlaceBoundary(
            name="Overlap",
            legal_name="Overlap city",
            geoid="1",
            class_code="C1",
            center=(0.5, 0.5),
            bbox=(0.0, 0.0, 1.0, 1.0),
            geometry={
                "type": "Polygon",
                "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
            },
        ),
        PlaceBoundary(
            name="Nearby",
            legal_name="Nearby city",
            geoid="2",
            class_code="C1",
            center=(2.0, 0.5),
            bbox=(1.9, 0.4, 2.1, 0.6),
            geometry={
                "type": "Polygon",
                "coordinates": [[[1.9, 0.4], [2.1, 0.4], [2.1, 0.6], [1.9, 0.6], [1.9, 0.4]]],
            },
        ),
    )
    monkeypatch.setattr(context_layers, "california_places", lambda: places)
    points = ((0.25, 0.25),)

    intersections = communities_intersecting_burned_area(points, "Test Fire", "2020-01-02")
    assert intersections is not None
    assert intersections.feature_count == 1
    assert intersections.geojson["features"][0]["properties"]["name"] == "Overlap"
    assert intersections.geojson["features"][0]["properties"]["labelPixelCount"] == 1

    active = communities_intersecting_active_fire(
        ((2.0, 0.5), (2.0, 0.5), (2.0, 0.5), (2.0, 0.5)),
        "Test Fire",
        "2020-01-02",
    )
    assert active is not None
    assert active.feature_count == 1
    assert active.geojson["features"][0]["properties"]["name"] == "Nearby"
    assert active.geojson["features"][0]["properties"]["labelPixelCount"] == 4
    assert "may not" not in active.caveat
    assert "does not prove" in active.caveat

    nearest = communities_nearest_burned_area(points, "Test Fire", "2020-01-02", limit=2)
    assert nearest is not None
    assert [feature["properties"]["name"] for feature in nearest.geojson["features"]] == [
        "Overlap",
        "Nearby",
    ]


def test_bobcat_city_results_keep_burned_area_and_active_fire_separate():
    day = "2020-09-18"
    burned = communities_intersecting_burned_area(
        burned_area_label_points("24461771", day),
        "Bobcat Fire",
        day,
    )
    active = communities_intersecting_active_fire(
        active_fire_label_points("24461771", day),
        "Bobcat Fire",
        day,
    )
    assert burned is not None
    assert active is not None
    burned_counts = {
        feature["properties"]["name"]: feature["properties"]["labelPixelCount"]
        for feature in burned.geojson["features"]
    }
    active_counts = {
        feature["properties"]["name"]: feature["properties"]["labelPixelCount"]
        for feature in active.geojson["features"]
    }
    assert burned_counts == {"Arcadia": 2, "Duarte": 27, "Monrovia": 151}
    assert active_counts == {"Glendale": 4}
    assert "Los Angeles" not in burned_counts
    assert "Los Angeles" not in active_counts
