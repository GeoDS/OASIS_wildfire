import type { LayerResult } from "./types";

const OBJECT_NAMES: Record<string, [string, string]> = {
  active_fire: ["active-fire detection", "active-fire detections"],
  burned_area: ["burned-area region", "burned-area regions"],
  fire_perimeter: ["fire-boundary polygon", "fire-boundary polygons"],
  satellite_hotspot: ["thermal-anomaly point", "thermal-anomaly points"],
  population_exposure: ["affected community", "affected communities"],
  weather: ["weather observation", "weather observations"],
  air_quality: ["air-quality observation", "air-quality observations"],
};

export function objectNoun(layer: LayerResult, count = layer.feature_count): string {
  if (layer.capability_id.includes("weather_linestring")) {
    return count === 1 ? "downwind-direction vector" : "downwind-direction vectors";
  }
  if (layer.capability_id.includes("weather_point")) {
    return count === 1 ? "weather observation" : "weather observations";
  }
  if (layer.capability_id.includes("air_quality")) {
    return count === 1 ? "air-quality observation" : "air-quality observations";
  }
  if (layer.family === "official_fire_perimeters") {
    return count === 1 ? "fire-boundary polygon" : "fire-boundary polygons";
  }
  if (layer.family === "satellite_hotspots") {
    return count === 1 ? "thermal-anomaly point" : "thermal-anomaly points";
  }
  const named = OBJECT_NAMES[layer.hazard_object];
  if (named) return count === 1 ? named[0] : named[1];
  if (layer.geometry_type === "Point") return count === 1 ? "mapped point" : "mapped points";
  if (layer.geometry_type === "Polygon") return count === 1 ? "mapped polygon" : "mapped polygons";
  return count === 1 ? "mapped line" : "mapped lines";
}

export function countLabel(layer: LayerResult): string {
  return `${layer.feature_count.toLocaleString()}${layer.truncated ? "+" : ""} ${objectNoun(layer)}`;
}

export function shortDate(value: string | null): string {
  if (!value) return "Not reported";
  const values = value.split(" / ").map((item) => item.trim());
  return values.length > 1 ? `${values[0]}–${values[values.length - 1]}` : value;
}
