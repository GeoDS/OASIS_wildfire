"use client";

import maplibregl, { type MapMouseEvent } from "maplibre-gl";
import { useEffect, useRef } from "react";

import type { LayerResult, RasterLayerResult, ResolvedLocation } from "@/lib/types";

const MAP_STYLE =
  process.env.NEXT_PUBLIC_MAP_STYLE ??
  "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json";
const ALTADENA: [number, number] = [-118.1312, 34.1897];

const LAYER_STYLE: Record<string, { color: string }> = {
  official_fire_perimeters: { color: "#c8613a" },
  satellite_hotspots: { color: "#d9932b" },
  historical_fire_perimeters: { color: "#6b6862" },
  public_weather_point: { color: "#2f7ea5" },
  public_weather_linestring: { color: "#2f7ea5" },
  subject_city_boundary: { color: "#356f82" },
  subject_fire_perimeter: { color: "#b84a2f" },
  subject_fire_observed_footprint: { color: "#d47732" },
  fire_intersecting_place_boundaries: { color: "#d9932b" },
};
const DEFAULT_STYLE = { color: "#8c8880" };
const WIND_ARROW_IMAGE = "firescope-wind-arrow";
const WEATHER_POINT = "public_weather_point";
const AIR_QUALITY_POINT = "public_air_quality_point";
const CONDITION_POINT_OFFSET_PX = 14;

type GeoFeature = {
  type?: string;
  geometry?: { type?: string; coordinates?: unknown };
  properties?: Record<string, unknown>;
};

function styleFor(layer: LayerResult) {
  if (layer.visualization?.color) return { color: layer.visualization.color };
  if (LAYER_STYLE[layer.capability_id]) return LAYER_STYLE[layer.capability_id];
  if (layer.capability_id.includes("historical")) return LAYER_STYLE.historical_fire_perimeters;
  if (layer.family === "official_fire_perimeters") return LAYER_STYLE.official_fire_perimeters;
  if (layer.family === "satellite_hotspots") return LAYER_STYLE.satellite_hotspots;
  return DEFAULT_STYLE;
}

function colorExpression(layer: LayerResult): unknown {
  const visualization = layer.visualization;
  const fallback = styleFor(layer).color;
  if (!visualization?.field || visualization.stops.length === 0) return fallback;
  if (visualization.kind === "categorical") {
    return [
      "match",
      ["to-string", ["get", visualization.field]],
      ...visualization.stops.flatMap((stop) => [String(stop.value), stop.color]),
      fallback,
    ];
  }
  if (visualization.kind === "graduated") {
    const numeric = visualization.stops.filter(
      (stop): stop is typeof stop & { value: number } => typeof stop.value === "number",
    );
    if (!numeric.length) return fallback;
    return [
      "step",
      ["to-number", ["get", visualization.field], 0],
      numeric[0].color,
      ...numeric.slice(1).flatMap((stop) => [stop.value, stop.color]),
    ];
  }
  return fallback;
}

function pointRadiusExpression(layer: LayerResult): unknown {
  const field = layer.visualization?.size_field;
  if (!field) return ["interpolate", ["linear"], ["zoom"], 8, 4, 12, 7, 15, 10];
  return [
    "interpolate",
    ["linear"],
    ["to-number", ["get", field], 0],
    0,
    5,
    25,
    7,
    50,
    9,
    100,
    12,
    300,
    16,
  ];
}

function conditionPointOffset(
  layer: LayerResult,
  hasWeatherAndAirQuality: boolean,
): [number, number] {
  if (!hasWeatherAndAirQuality) return [0, 0];
  if (layer.capability_id === WEATHER_POINT) return [-CONDITION_POINT_OFFSET_PX, 0];
  if (layer.capability_id === AIR_QUALITY_POINT) return [CONDITION_POINT_OFFSET_PX, 0];
  return [0, 0];
}

function coordinates(value: unknown): [number, number][] {
  if (!Array.isArray(value)) return [];
  if (
    value.length >= 2 &&
    typeof value[0] === "number" &&
    typeof value[1] === "number"
  ) {
    return [[value[0], value[1]]];
  }
  return value.flatMap(coordinates);
}

function layerBounds(layer: LayerResult): [number, number, number, number] | null {
  const points = (layer.geojson.features as GeoFeature[]).flatMap((feature) =>
    coordinates(feature.geometry?.coordinates),
  );
  if (!points.length) return null;
  return [
    Math.min(...points.map(([lon]) => lon)),
    Math.min(...points.map(([, lat]) => lat)),
    Math.max(...points.map(([lon]) => lon)),
    Math.max(...points.map(([, lat]) => lat)),
  ];
}

function formatValue(value: unknown, unit: string | null) {
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") {
    const formatted = Number.isInteger(value)
      ? value.toLocaleString()
      : value.toLocaleString(undefined, { maximumFractionDigits: 2 });
    return unit ? `${formatted} ${unit}` : formatted;
  }
  return unit ? `${String(value)} ${unit}` : String(value);
}

function popupContent(layer: LayerResult, properties: Record<string, unknown>) {
  const root = document.createElement("div");
  root.className = "min-w-[220px] max-w-[290px] p-1 font-sans";
  const eyebrow = document.createElement("p");
  eyebrow.className = "text-[9px] font-semibold uppercase tracking-[0.08em] text-ink-400";
  eyebrow.textContent = layer.visualization?.label ?? layer.title;
  root.appendChild(eyebrow);
  const title = document.createElement("p");
  title.className = "mt-1 text-[13px] font-semibold leading-tight text-ink-900";
  title.textContent = String(properties.displayName ?? properties.name ?? layer.title);
  root.appendChild(title);
  const fields = layer.visualization?.popup_fields ?? [];
  const list = document.createElement("dl");
  list.className = "mt-2 space-y-1 border-t border-paper-300 pt-2";
  for (const field of fields) {
    const value = properties[field.key];
    if (value === null || value === undefined || value === "") continue;
    const row = document.createElement("div");
    row.className = "grid grid-cols-[minmax(0,1fr)_auto] gap-3 text-[10.5px] leading-[1.35]";
    const term = document.createElement("dt");
    term.className = "text-ink-400";
    term.textContent = field.label;
    const detail = document.createElement("dd");
    detail.className = "max-w-[155px] text-right font-medium text-ink-800";
    detail.textContent = formatValue(value, field.unit);
    row.append(term, detail);
    list.appendChild(row);
  }
  root.appendChild(list);
  const source = document.createElement("p");
  source.className = "mt-2 border-t border-paper-300 pt-1.5 text-[9.5px] leading-snug text-ink-400";
  source.textContent = `Source: ${layer.source}`;
  root.appendChild(source);
  return root;
}

function combinedPopupContent(
  entries: Array<{ layer: LayerResult; properties: Record<string, unknown> }>,
) {
  const container = document.createElement("div");
  container.className = "max-h-[360px] overflow-y-auto pr-1";
  for (const [index, entry] of entries.entries()) {
    const section = popupContent(entry.layer, entry.properties);
    if (index > 0) section.className += " mt-2 border-t border-paper-300 pt-2";
    container.appendChild(section);
  }
  return container;
}

function sourceGeojson(layer: LayerResult) {
  return {
    type: "FeatureCollection",
    features: (layer.geojson.features as GeoFeature[]).map((feature) => ({
      ...feature,
      properties: {
        ...(feature.properties ?? {}),
        __capability: layer.capability_id,
      },
    })),
  };
}

function windArrowImage() {
  const canvas = document.createElement("canvas");
  canvas.width = 40;
  canvas.height = 20;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("Canvas 2D context is unavailable");
  context.fillStyle = "#276f95";
  context.beginPath();
  context.moveTo(0, 7);
  context.lineTo(25, 7);
  context.lineTo(25, 0);
  context.lineTo(40, 10);
  context.lineTo(25, 20);
  context.lineTo(25, 13);
  context.lineTo(0, 13);
  context.closePath();
  context.fill();
  return context.getImageData(0, 0, canvas.width, canvas.height);
}

export function MapView({
  resolved,
  layers,
  rasters,
  activeLayerIds = [],
  onLayerSelect,
}: {
  resolved: ResolvedLocation | null;
  layers: LayerResult[];
  rasters: RasterLayerResult[];
  activeLayerIds?: string[];
  onLayerSelect?: (capabilityId: string | null, additive?: boolean) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const popupRef = useRef<maplibregl.Popup | null>(null);
  const readyRef = useRef(false);
  const drawnRef = useRef<string[]>([]);
  const sourceRef = useRef<string[]>([]);
  const rasterDrawnRef = useRef<string[]>([]);
  const layerMetaRef = useRef<Map<string, LayerResult>>(new Map());
  const onLayerSelectRef = useRef(onLayerSelect);
  useEffect(() => {
    onLayerSelectRef.current = onLayerSelect;
  }, [onLayerSelect]);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: MAP_STYLE,
      center: ALTADENA,
      zoom: 10,
      attributionControl: { compact: true },
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");

    const interactiveFeatures = (event: MapMouseEvent) => {
      const ids = drawnRef.current.filter((id) => map.getLayer(id));
      return ids.length ? map.queryRenderedFeatures(event.point, { layers: ids }) : [];
    };
    const onMove = (event: MapMouseEvent) => {
      map.getCanvas().style.cursor = interactiveFeatures(event).length ? "pointer" : "";
    };
    const onClick = (event: MapMouseEvent) => {
      const geometryPriority: Record<string, number> = {
        Point: 0,
        MultiPoint: 0,
        LineString: 1,
        MultiLineString: 1,
        Polygon: 2,
        MultiPolygon: 2,
      };
      const features = interactiveFeatures(event).sort(
        (first, second) =>
          (geometryPriority[first.geometry.type] ?? 3) -
          (geometryPriority[second.geometry.type] ?? 3),
      );
      if (!features.length) return;
      const selectedPriority = geometryPriority[features[0].geometry.type] ?? 3;
      const seen = new Set<string>();
      let entries = features
        .filter(
          (feature) => (geometryPriority[feature.geometry.type] ?? 3) === selectedPriority,
        )
        .flatMap((feature) => {
          const properties = (feature.properties ?? {}) as Record<string, unknown>;
          const capability = String(properties.__capability ?? "");
          const layer = layerMetaRef.current.get(capability);
          if (!layer || seen.has(capability)) return [];
          seen.add(capability);
          return [{ layer, properties }];
        });
      const clickedCityCondition = entries.some((entry) =>
        [WEATHER_POINT, AIR_QUALITY_POINT].includes(entry.layer.capability_id),
      );
      if (clickedCityCondition) {
        // Weather and AQI intentionally share the resolved city sampling
        // coordinate. Include both records even when the renderer reports only
        // the topmost circle at a particular zoom level.
        const cityConditions = [WEATHER_POINT, AIR_QUALITY_POINT].flatMap((capability) => {
          const layer = layerMetaRef.current.get(capability);
          const feature = (layer?.geojson.features as GeoFeature[] | undefined)?.[0];
          if (!layer || !feature?.properties) return [];
          seen.add(capability);
          return [{ layer, properties: feature.properties }];
        });
        entries = [
          ...cityConditions,
          ...entries.filter(
            (entry) => ![WEATHER_POINT, AIR_QUALITY_POINT].includes(entry.layer.capability_id),
          ),
        ];
      }
      if (!entries.length) return;
      onLayerSelectRef.current?.(entries[0].layer.capability_id, event.originalEvent.shiftKey);
      popupRef.current?.remove();
      popupRef.current = new maplibregl.Popup({ closeButton: true, maxWidth: "320px" })
        .setLngLat(event.lngLat)
        .setDOMContent(combinedPopupContent(entries))
        .addTo(map);
    };
    map.on("mousemove", onMove);
    map.on("click", onClick);
    map.on("load", () => {
      if (!map.hasImage(WIND_ARROW_IMAGE)) {
        map.addImage(WIND_ARROW_IMAGE, windArrowImage(), { pixelRatio: 2 });
      }
      readyRef.current = true;
    });
    mapRef.current = map;
    return () => {
      popupRef.current?.remove();
      map.off("mousemove", onMove);
      map.off("click", onClick);
      map.remove();
      mapRef.current = null;
      readyRef.current = false;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const apply = () => {
      popupRef.current?.remove();
      for (const id of [...drawnRef.current].reverse()) {
        if (map.getLayer(id)) map.removeLayer(id);
      }
      for (const id of sourceRef.current) {
        if (map.getSource(id)) map.removeSource(id);
      }
      drawnRef.current = [];
      sourceRef.current = [];
      layerMetaRef.current = new Map();
      const availableCapabilities = new Set(
        layers.filter((layer) => layer.feature_count).map((layer) => layer.capability_id),
      );
      const hasWeatherAndAirQuality =
        availableCapabilities.has(WEATHER_POINT) &&
        availableCapabilities.has(AIR_QUALITY_POINT);

      for (const layer of layers) {
        if (!layer.feature_count) continue;
        const sourceId = `result-source-${layer.capability_id}`;
        const id = `result-${layer.capability_id}`;
        const color = colorExpression(layer);
        map.addSource(sourceId, { type: "geojson", data: sourceGeojson(layer) as never });
        sourceRef.current.push(sourceId);
        layerMetaRef.current.set(layer.capability_id, layer);

        if (layer.geometry_type === "Point") {
          const pointOffset = conditionPointOffset(layer, hasWeatherAndAirQuality);
          map.addLayer({
            id,
            type: "circle",
            source: sourceId,
            paint: {
              "circle-radius": pointRadiusExpression(layer) as never,
              "circle-color": color as never,
              "circle-opacity": 0.86,
              "circle-stroke-width": 1.25,
              "circle-stroke-color": "#ffffff",
              "circle-translate": pointOffset,
              "circle-translate-anchor": "viewport",
            },
          });
          drawnRef.current.push(id);
          const labelId = `${id}-label`;
          map.addLayer({
            id: labelId,
            type: "symbol",
            source: sourceId,
            layout: {
              "text-field": [
                "case",
                ["has", "usAqi"],
                ["concat", "AQI ", ["to-string", ["get", "usAqi"]]],
                ["coalesce", ["get", "displayName"], ""],
              ],
              "text-size": 11,
              "text-offset": [0, 1.35],
              "text-anchor": "top",
              "text-optional": true,
            },
            paint: {
              "text-color": "#383632",
              "text-halo-color": "#ffffff",
              "text-halo-width": 1.4,
              "text-translate": pointOffset,
              "text-translate-anchor": "viewport",
            },
          });
          drawnRef.current.push(labelId);
        } else if (layer.geometry_type === "LineString") {
          map.addLayer({
            id,
            type: "line",
            source: sourceId,
            paint: {
              "line-color": color as never,
              "line-width": 3,
              "line-opacity": 0.9,
            },
          });
          drawnRef.current.push(id);
          const arrowId = `${id}-arrows`;
          map.addLayer({
            id: arrowId,
            type: "symbol",
            source: sourceId,
            layout: {
              "symbol-placement": "line",
              "symbol-spacing": 95,
              "icon-image": WIND_ARROW_IMAGE,
              "icon-size": 0.9,
              "icon-rotation-alignment": "map",
              "icon-allow-overlap": true,
              "icon-ignore-placement": true,
            },
          });
          drawnRef.current.push(arrowId);
          const labelId = `${id}-label`;
          map.addLayer({
            id: labelId,
            type: "symbol",
            source: sourceId,
            layout: {
              "symbol-placement": "line-center",
              "text-field": ["coalesce", ["get", "displayLabel"], ["get", "displayName"]],
              "text-size": 11,
              "text-offset": [0, -1.1],
              "text-keep-upright": true,
            },
            paint: {
              "text-color": "#1f526c",
              "text-halo-color": "#ffffff",
              "text-halo-width": 1.5,
            },
          });
          drawnRef.current.push(labelId);
        } else {
          const isSubject = layer.capability_id.startsWith("subject_");
          map.addLayer({
            id,
            type: "fill",
            source: sourceId,
            paint: {
              "fill-color": color as never,
              "fill-opacity": isSubject ? 0.24 : 0.55,
            },
          });
          drawnRef.current.push(id);
          const outlineId = `${id}-outline`;
          map.addLayer({
            id: outlineId,
            type: "line",
            source: sourceId,
            paint: {
              "line-color": color as never,
              "line-width": isSubject ? 2.4 : 1.4,
              "line-opacity": 0.95,
            },
          });
          drawnRef.current.push(outlineId);
          const labelId = `${id}-label`;
          map.addLayer({
            id: labelId,
            type: "symbol",
            source: sourceId,
            layout: {
              "text-field": ["coalesce", ["get", "displayName"], ["get", "name"], ""],
              "text-size": 11,
              "text-max-width": 10,
              "text-optional": true,
            },
            paint: {
              "text-color": "#383632",
              "text-halo-color": "#ffffff",
              "text-halo-width": 1.5,
            },
          });
          drawnRef.current.push(labelId);
        }
      }
    };
    if (readyRef.current) apply();
    else map.once("load", apply);
  }, [layers]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const apply = () => {
      for (const id of drawnRef.current) {
        if (!map.getLayer(id)) continue;
        const belongsToActive = activeLayerIds.length
          ? activeLayerIds.some((activeLayerId) => id.startsWith(`result-${activeLayerId}`))
          : true;
        map.setLayoutProperty(id, "visibility", belongsToActive ? "visible" : "none");
      }
    };
    if (readyRef.current) apply();
    else map.once("load", apply);
  }, [activeLayerIds, layers]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const apply = () => {
      for (const id of rasterDrawnRef.current) {
        if (map.getLayer(id)) map.removeLayer(id);
        if (map.getSource(id)) map.removeSource(id);
      }
      rasterDrawnRef.current = [];
      for (const raster of rasters) {
        const [west, south, east, north] = raster.bounds;
        map.addSource(raster.id, {
          type: "image",
          url: raster.image_url,
          coordinates: [
            [west, north],
            [east, north],
            [east, south],
            [west, south],
          ],
        });
        const beforeId = drawnRef.current.find((id) => map.getLayer(id));
        map.addLayer(
          {
            id: raster.id,
            type: "raster",
            source: raster.id,
            paint: { "raster-opacity": raster.opacity },
          },
          beforeId,
        );
        rasterDrawnRef.current.push(raster.id);
      }
    };
    if (readyRef.current) apply();
    else map.once("load", apply);
  }, [rasters]);

  const fitBounds = (bounds: [number, number, number, number]) => {
    const map = mapRef.current;
    if (!map) return;
    const canvas = map.getCanvas();
    const padding = Math.max(
      8,
      Math.min(56, Math.floor(Math.min(canvas.clientWidth, canvas.clientHeight) / 8)),
    );
    map.fitBounds(
      [
        [bounds[0], bounds[1]],
        [bounds[2], bounds[3]],
      ],
      {
        padding,
        duration: 700,
        maxZoom: 12,
      },
    );
  };

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const apply = () => {
      const subjects = layers.filter((layer) => layer.capability_id.startsWith("subject_"));
      const subjectBounds = subjects.map(layerBounds).filter(Boolean) as Array<
        [number, number, number, number]
      >;
      const rasterBounds = rasters.map((raster) => raster.bounds);
      const fallbackBounds = layers
        .filter((layer) => layer.geometry_type !== "Point")
        .map(layerBounds)
        .filter(Boolean) as Array<[number, number, number, number]>;
      const candidates = subjectBounds.length
        ? subjectBounds
        : rasterBounds.length
          ? rasterBounds
          : fallbackBounds;
      if (candidates.length) {
        fitBounds([
          Math.min(...candidates.map(([west]) => west)),
          Math.min(...candidates.map(([, south]) => south)),
          Math.max(...candidates.map(([, , east]) => east)),
          Math.max(...candidates.map(([, , , north]) => north)),
        ]);
      } else if (resolved?.center) {
        map.easeTo({ center: resolved.center, zoom: 10, duration: 700 });
      }
    };
    if (readyRef.current) apply();
    else map.once("load", apply);
  }, [layers, rasters, resolved]);

  const drawn = layers.filter((layer) => layer.feature_count > 0);
  return (
    <div className="relative h-full w-full">
      <div ref={containerRef} className="h-full w-full" />

      {!resolved?.center && layers.length === 0 && rasters.length === 0 && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
          <p className="rounded-xl border border-paper-300 bg-white/90 px-4 py-2 text-[12.5px] text-ink-400 backdrop-blur">
            Ask about a fire or city and its real mapped geometry will appear here
          </p>
        </div>
      )}

      {resolved?.ambiguous && resolved.alternatives.length > 0 && (
        <div className="pointer-events-none absolute left-4 top-4 max-w-[19rem] rounded-xl border border-ember-300 bg-white/92 px-3 py-2.5 backdrop-blur">
          <p className="text-[11.5px] font-medium text-ink-900">Several places share this name</p>
          <p className="mt-0.5 text-[11px] leading-[1.45] text-ink-500">
            {resolved.alternatives.length} other candidate
            {resolved.alternatives.length > 1 ? "s" : ""}, including{" "}
            {resolved.alternatives.slice(0, 2).join(" and ")}. Say so in the chat if this is the
            wrong one.
          </p>
        </div>
      )}

      {(drawn.length > 0 || rasters.length > 0) && (
        <details
          className="animate-rise absolute right-4 top-4 z-20 max-h-[calc(100%-2rem)] w-[17rem] overflow-y-auto rounded-xl border border-paper-300 bg-white/95 p-3 shadow-sm backdrop-blur"
        >
          <summary className="flex cursor-pointer list-none items-center justify-between gap-3 rounded-sm focus-visible:outline-2 focus-visible:outline-ember-500">
            <p className="text-[10px] font-semibold uppercase tracking-[0.08em] text-ink-400">
              Map legend
            </p>
          </summary>
          <ul className="mt-2 space-y-3">
            {drawn.map((layer) => {
              const visualization = layer.visualization;
              const { color } = styleFor(layer);
              return (
                <li key={layer.capability_id} className="border-t border-paper-300 pt-2 first:border-0 first:pt-0">
                  <button
                    type="button"
                    className="block w-full cursor-pointer rounded-sm text-left focus-visible:outline-2 focus-visible:outline-ember-500"
                    aria-label={`Zoom to ${layer.title}`}
                    onClick={() => {
                      const bounds = layerBounds(layer);
                      if (bounds) fitBounds(bounds);
                    }}
                  >
                    <span className="text-[11.5px] font-medium leading-tight text-ink-900">
                      {layer.title}
                    </span>
                  </button>
                  {visualization?.kind === "vector" ? (
                    <div className="mt-1.5 flex items-center gap-2 text-[10.5px] text-ink-600">
                      <span className="relative block h-2 w-10 border-t-2" style={{ borderColor: color }}>
                        <span className="absolute -right-0.5 -top-[9px] text-[14px]" style={{ color }}>
                          ➤
                        </span>
                      </span>
                      <span>{visualization.label}</span>
                    </div>
                  ) : visualization?.stops.length ? (
                    <div className="mt-1.5 space-y-1">
                      <p className="text-[9.5px] font-medium text-ink-500">
                        {visualization.label}
                        {visualization.unit && !visualization.label.toLowerCase().includes(visualization.unit.toLowerCase())
                          ? ` · ${visualization.unit}`
                          : ""}
                      </p>
                      {visualization.stops.map((stop) => (
                        <div key={`${stop.value}-${stop.label}`} className="flex items-center gap-1.5">
                          <span
                            className="h-2.5 w-4 shrink-0 rounded-[2px] border border-black/10"
                            style={{ backgroundColor: stop.color }}
                          />
                          <span className="text-[9.5px] leading-tight text-ink-500">{stop.label}</span>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="mt-1.5 flex items-center gap-1.5">
                      <span
                        className={`h-2.5 w-4 shrink-0 border border-black/10 ${
                          layer.geometry_type === "Point" ? "rounded-full" : "rounded-[2px]"
                        }`}
                        style={{ backgroundColor: color }}
                      />
                      <span className="text-[9.5px] text-ink-500">
                        {visualization?.label ?? layer.title}
                      </span>
                    </div>
                  )}
                  {visualization?.kind !== "fixed" && (visualization?.explanation ?? layer.caveat) && (
                    <p className="mt-1 text-[9.5px] leading-[1.35] text-ink-500">
                      {visualization?.explanation ?? layer.caveat}
                    </p>
                  )}
                  <p className="mt-0.5 truncate text-[9px] text-ink-400">
                    {layer.source}
                    {layer.as_of ? ` · ${layer.as_of}` : ""}
                  </p>
                </li>
              );
            })}
            {rasters.map((raster) => (
              <li key={raster.id} className="border-t border-paper-300 pt-2">
                <p className="text-[11.5px] font-medium leading-tight text-ink-900">{raster.title}</p>
                {raster.legend_stops?.length ? (
                  <div className="mt-1.5 space-y-1">
                    <p className="text-[9.5px] font-medium text-ink-500">
                      {raster.legend_label ?? raster.variable_label ?? raster.variable}
                    </p>
                    {raster.legend_stops.map((stop) => (
                      <div key={`${stop.value}-${stop.label}`} className="flex items-center gap-1.5">
                        <span
                          className="h-2.5 w-4 shrink-0 rounded-[2px] border border-black/10"
                          style={{ backgroundColor: stop.color }}
                        />
                        <span className="text-[9.5px] leading-tight text-ink-500">
                          {stop.label}
                        </span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="mt-1.5 flex items-center gap-1.5">
                    <span
                      className="h-2.5 w-4 shrink-0 rounded-[2px] border border-black/10"
                      style={{ backgroundColor: raster.legend_color ?? "#c8613a" }}
                    />
                    <span className="text-[9.5px] text-ink-500">
                      {raster.legend_label ?? raster.variable_label ?? raster.variable}
                    </span>
                  </div>
                )}
                <p className="mt-1 text-[9.5px] leading-[1.35] text-ink-500">
                  {raster.explanation ?? "Matched automatically from the local fire archive."}
                </p>
                <p className="mt-0.5 truncate text-[9px] text-ink-400">
                  {raster.source_label ?? "Local fire archive"} · through {raster.date}
                </p>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
