"""Web data-layer integration tests; public upstream is always stubbed."""

from __future__ import annotations

from fastapi.testclient import TestClient

from wildfire_agent import api
from wildfire_agent.planning.executor import _coords_iter, clip_local_layer


def test_local_polygon_is_geometrically_clipped_to_request_bbox():
    bbox = (-118.14, 34.18, -118.12, 34.20)
    result = clip_local_layer("official_fire_perimeters", bbox)

    assert result.feature_count > 0
    for feature in result.geojson["features"]:
        for longitude, latitude, *_ in _coords_iter(feature["geometry"]["coordinates"]):
            assert bbox[0] <= longitude <= bbox[2]
            assert bbox[1] <= latitude <= bbox[3]


def test_public_layer_endpoint_uses_mcp_payload_without_network(monkeypatch):
    async def fake_fetch(_body):
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": []},
                    "properties": {"name": "Example"},
                }
            ],
            "metadata": {
                "status": "loaded",
                "featureCount": 1,
                "retrievedAt": "2026-08-17T12:00:00+00:00",
                "caveat": "Test caveat",
                "truncated": False,
            },
        }

    monkeypatch.setattr(api, "_fetch_public_mcp", fake_fetch)
    response = TestClient(api.app).post("/api/layers/public", json={"source": "wfigs"})

    assert response.status_code == 200
    body = response.json()
    assert body["metadata"]["status"] == "loaded"
    assert body["layers"][0]["capability_id"] == "public_wfigs_polygon"
    assert body["layers"][0]["feature_count"] == 1
    assert body["layers"][0]["visualization"]["kind"] == "graduated"
    assert body["layers"][0]["visualization"]["field"] == "attr_PercentContained"
    assert body["layers"][0]["visualization"]["stops"][0]["label"] == "0–24%"


def test_air_quality_layer_uses_epa_aqi_legend_and_popup_contract():
    payload = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-118.14, 34.15]},
                "properties": {"usAqi": 59, "pm25": 15.3},
            }
        ],
        "metadata": {"caveat": "Modeled", "truncated": False},
    }
    layer = api._public_layer_results("air_quality", payload)[0]

    assert layer.visualization is not None
    assert layer.visualization.field == "usAqi"
    assert [stop.value for stop in layer.visualization.stops] == [
        0,
        51,
        101,
        151,
        201,
        301,
    ]
    assert any(field.key == "pm25" for field in layer.visualization.popup_fields)


def test_local_layer_endpoint_rejects_reversed_bbox():
    response = TestClient(api.app).post(
        "/api/layers/local",
        json={
            "capability_id": "official_fire_perimeters",
            "bbox": [-118.0, 34.2, -118.2, 34.1],
        },
    )

    assert response.status_code == 422
    assert "bbox must be ordered" in response.text
