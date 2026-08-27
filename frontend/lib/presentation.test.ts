import { describe, expect, it } from "vitest";

import { countLabel } from "./presentation";
import type { LayerResult } from "./types";

function layer(overrides: Partial<LayerResult>): LayerResult {
  return {
    capability_id: "test",
    title: "Test layer",
    hazard_object: "fire_perimeter",
    family: null,
    geometry_type: "Polygon",
    caveat: "test",
    feature_count: 2,
    truncated: false,
    source: "test",
    as_of: null,
    retrieved_at: null,
    visualization: null,
    geojson: { type: "FeatureCollection", features: [] },
    ...overrides,
  };
}

describe("semantic object labels", () => {
  it("names fire perimeters as polygons rather than features", () => {
    expect(countLabel(layer({ feature_count: 2 }))).toBe("2 fire-boundary polygons");
  });

  it("distinguishes weather points from downwind vectors", () => {
    const weather = layer({
      capability_id: "public_weather_point",
      hazard_object: "weather",
      geometry_type: "Point",
      feature_count: 1,
    });
    const wind = layer({
      capability_id: "public_weather_linestring",
      hazard_object: "weather",
      geometry_type: "LineString",
      feature_count: 1,
    });

    expect(countLabel(weather)).toBe("1 weather observation");
    expect(countLabel(wind)).toBe("1 downwind-direction vector");
  });

  it("keeps saved API sessions semantic after the backend object migration", () => {
    expect(
      countLabel(
        layer({
          capability_id: "public_air_quality_point",
          hazard_object: "active_fire",
          geometry_type: "Point",
          feature_count: 1,
        }),
      ),
    ).toBe("1 air-quality observation");
  });

  it("names satellite observations as thermal-anomaly points", () => {
    expect(
      countLabel(
        layer({
          hazard_object: "satellite_hotspot",
          geometry_type: "Point",
          feature_count: 1300,
          truncated: true,
        }),
      ),
    ).toBe("1,300+ thermal-anomaly points");
  });
});
