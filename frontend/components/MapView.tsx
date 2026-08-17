"use client";

import type { Feature, FeatureCollection, Polygon } from "geojson";
import maplibregl from "maplibre-gl";
import { useEffect, useRef } from "react";

import type { LayerResult, ResolvedLocation } from "@/lib/types";

const MAP_STYLE =
  process.env.NEXT_PUBLIC_MAP_STYLE ??
  "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json";

const AOI_SOURCE = "aoi";
const ALTADENA: [number, number] = [-118.1312, 34.1897];

/**
 * Colour by data family, not by layer.
 *
 * The distinction this whole system is built around is confirmed fire versus
 * unverified heat, so it gets the strongest visual separation available: a
 * filled polygon for what an agency verified, loose points for what a satellite
 * merely detected. It should be obvious which is which before anyone reads the
 * legend.
 */
const LAYER_STYLE: Record<string, { color: string }> = {
  official_fire_perimeters: { color: "#c8613a" },
  satellite_hotspots: { color: "#d9932b" },
  historical_fire_perimeters: { color: "#6b6862" },
};

const DEFAULT_STYLE = { color: "#8c8880" };

function styleFor(layer: LayerResult) {
  return LAYER_STYLE[layer.capability_id] ?? DEFAULT_STYLE;
}

/**
 * Draw a circular buffer as a polygon.
 *
 * Equirectangular approximation (1 degree of latitude is ~111.32 km, longitude
 * shrinks by cos(lat)) - the same maths as the backend's
 * `geocoding.bbox_from_center`. If the two ever diverge, the circle on the map
 * stops matching the bbox in the contract, which is exactly the kind of
 * inconsistency a reviewer spots immediately.
 */
function circlePolygon(
  center: [number, number],
  radiusKm: number,
  steps = 96,
): Feature<Polygon> {
  const [lon, lat] = center;
  const dLat = radiusKm / 111.32;
  const dLon = radiusKm / (111.32 * Math.max(Math.cos((lat * Math.PI) / 180), 1e-6));

  const ring: [number, number][] = [];
  for (let i = 0; i <= steps; i++) {
    const theta = (i / steps) * 2 * Math.PI;
    ring.push([lon + dLon * Math.cos(theta), lat + dLat * Math.sin(theta)]);
  }
  return { type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [ring] } };
}

function emptyCollection(): FeatureCollection {
  return { type: "FeatureCollection", features: [] };
}

export function MapView({
  resolved,
  layers,
}: {
  resolved: ResolvedLocation | null;
  layers: LayerResult[];
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const markerRef = useRef<maplibregl.Marker | null>(null);
  const readyRef = useRef(false);
  const drawnRef = useRef<string[]>([]);

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

    map.on("load", () => {
      map.addSource(AOI_SOURCE, { type: "geojson", data: emptyCollection() });
      map.addLayer({
        id: "aoi-fill",
        type: "fill",
        source: AOI_SOURCE,
        paint: { "fill-color": "#c8613a", "fill-opacity": 0.05 },
      });
      map.addLayer({
        id: "aoi-line",
        type: "line",
        source: AOI_SOURCE,
        // line-dasharray takes no data-driven expression, so apply() sets it imperatively
        paint: {
          "line-color": "#c8613a",
          "line-width": 1.5,
          "line-opacity": 0.7,
          "line-dasharray": [2, 2],
        },
      });
      readyRef.current = true;
    });

    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
      readyRef.current = false;
    };
  }, []);

  // ── Area of interest ────────────────────────────────────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    const apply = () => {
      const source = map.getSource(AOI_SOURCE) as maplibregl.GeoJSONSource | undefined;
      if (!source) return;

      if (!resolved?.center) {
        source.setData(emptyCollection());
        markerRef.current?.remove();
        markerRef.current = null;
        return;
      }

      source.setData({
        type: "FeatureCollection",
        features: [circlePolygon(resolved.center, resolved.buffer_km ?? 25)],
      });
      // Dashed = the agent assumed it; solid = the user confirmed it
      map.setPaintProperty("aoi-line", "line-dasharray", resolved.confirmed_by_user ? [1] : [2, 2]);

      if (!markerRef.current) markerRef.current = new maplibregl.Marker({ color: "#c8613a" });
      markerRef.current.setLngLat(resolved.center).addTo(map);

      if (resolved.bbox) {
        const [w, s, e, n] = resolved.bbox;
        // Padding has to scale with the canvas: the centre column is only a few
        // hundred pixels tall on a short window, and a fixed 64px makes MapLibre
        // warn "cannot fit within canvas" and give up on the zoom.
        const canvas = map.getCanvas();
        const padding = Math.max(
          8,
          Math.min(56, Math.floor(Math.min(canvas.clientWidth, canvas.clientHeight) / 8)),
        );
        map.fitBounds(
          [
            [w, s],
            [e, n],
          ],
          { padding, duration: 700, maxZoom: 12 },
        );
      }
    };

    if (readyRef.current) apply();
    else map.once("load", apply);
  }, [resolved]);

  // ── Analysis layers from the Planning Agent ─────────────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    const apply = () => {
      // Clear last turn's layers before drawing this one, so a new question
      // never leaves the previous answer sitting on the map underneath it.
      for (const id of drawnRef.current) {
        if (map.getLayer(id)) map.removeLayer(id);
        if (map.getSource(id)) map.removeSource(id);
      }
      drawnRef.current = [];

      for (const layer of layers) {
        if (!layer.feature_count) continue;
        const id = `result-${layer.capability_id}`;
        const { color } = styleFor(layer);

        map.addSource(id, { type: "geojson", data: layer.geojson as never });

        if (layer.geometry_type === "Point") {
          map.addLayer({
            id,
            type: "circle",
            source: id,
            paint: {
              "circle-radius": ["interpolate", ["linear"], ["zoom"], 8, 2, 12, 4.5, 15, 7],
              "circle-color": color,
              "circle-opacity": 0.75,
              "circle-stroke-width": 0.5,
              "circle-stroke-color": "#ffffff",
            },
          });
        } else if (layer.geometry_type === "LineString") {
          map.addLayer({
            id,
            type: "line",
            source: id,
            paint: { "line-color": color, "line-width": 2 },
          });
        } else {
          map.addLayer({
            id,
            type: "fill",
            source: id,
            paint: {
              "fill-color": color,
              "fill-opacity": layer.capability_id === "historical_fire_perimeters" ? 0.18 : 0.36,
              "fill-outline-color": color,
            },
          });
        }
        drawnRef.current.push(id);
      }
    };

    if (readyRef.current) apply();
    else map.once("load", apply);
  }, [layers]);

  const drawn = layers.filter((l) => l.feature_count > 0);

  return (
    <div className="relative h-full w-full">
      <div ref={containerRef} className="h-full w-full" />

      {!resolved?.center && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
          <p className="rounded-xl border border-paper-300 bg-white/90 px-4 py-2 text-[12.5px] text-ink-400 backdrop-blur">
            Ask a question and the scope the agent resolves will be drawn here
          </p>
        </div>
      )}

      {/* Same-name ambiguity never interrupts the user (rule 2), but it has to be
          visible: a circle drawn in the wrong state should be obvious at a glance. */}
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

      {/* Legend. Each entry carries its caveat and its source, because the gap
          between a confirmed perimeter and a heat pixel is the gap between a
          right answer and a wrong one. */}
      {drawn.length > 0 && (
        <div className="animate-rise absolute bottom-16 right-4 max-w-[19rem] rounded-xl border border-paper-300 bg-white/94 p-3 backdrop-blur">
          <p className="mb-2 text-[10px] font-semibold uppercase tracking-[0.08em] text-ink-400">
            Layers drawn
          </p>
          <ul className="space-y-2.5">
            {drawn.map((layer) => {
              const { color } = styleFor(layer);
              return (
                <li key={layer.capability_id} className="flex gap-2">
                  <span
                    className={`mt-[5px] h-2.5 w-2.5 shrink-0 ${
                      layer.geometry_type === "Point" ? "rounded-full" : "rounded-[3px]"
                    }`}
                    style={{
                      backgroundColor: color,
                      opacity: layer.geometry_type === "Point" ? 0.85 : 0.4,
                      border: `1px solid ${color}`,
                    }}
                  />
                  <span className="min-w-0">
                    <span className="block text-[11.5px] font-medium leading-tight text-ink-900">
                      {layer.title}
                      <span className="ml-1.5 font-normal tabular-nums text-ink-400">
                        {layer.feature_count}
                        {layer.truncated ? "+ capped" : ""}
                      </span>
                    </span>
                    <span className="mt-0.5 block text-[10.5px] leading-[1.4] text-ink-500">
                      {layer.caveat}
                    </span>
                    <span className="mt-0.5 block truncate text-[10px] text-ink-400">
                      {layer.source}
                      {layer.as_of ? ` · ${layer.as_of}` : ""}
                    </span>
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {resolved?.center && (
        <div className="pointer-events-none absolute bottom-4 left-1/2 flex max-w-[calc(100%-2rem)] -translate-x-1/2 items-center gap-2.5 rounded-full border border-paper-300 bg-white/92 py-1.5 pl-3.5 pr-3 text-[11.5px] backdrop-blur">
          <span className="truncate font-medium text-ink-900">
            {resolved.display_name ?? "Resolved scope"}
          </span>
          <span className="h-3 w-px shrink-0 bg-paper-300" />
          <span className="shrink-0 tabular-nums text-ink-500">
            {resolved.buffer_km ?? "?"} km radius
          </span>
          <span className="h-3 w-px shrink-0 bg-paper-300" />
          <span
            className={`shrink-0 ${resolved.confirmed_by_user ? "text-sage-500" : "text-ember-600"}`}
          >
            {resolved.confirmed_by_user ? "confirmed" : "dashed = assumed"}
          </span>
        </div>
      )}
    </div>
  );
}
