"use client";

import type { FireContext } from "@/lib/types";

/** Bars carry the share; the number beside them carries the amount. Reading one
 *  proportion against another is what this panel is for, so the bar is the
 *  primary channel and the figure is the check. */
function ShareBar({
  label,
  percent,
  area,
  color,
}: {
  label: string;
  percent: number;
  area: number;
  color: string;
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="w-[8.5rem] shrink-0 truncate text-[11px] text-ink-700" title={label}>
        {label}
      </span>
      <span className="h-2 flex-1 overflow-hidden rounded-sm bg-paper-300">
        <span
          className="block h-full rounded-sm"
          style={{ width: `${Math.max(percent, 1.2)}%`, background: color }}
        />
      </span>
      <span className="w-[3.1rem] shrink-0 text-right text-[11px] tabular-nums text-ink-500">
        {percent.toFixed(1)}%
      </span>
      <span className="w-[4.6rem] shrink-0 text-right text-[11px] tabular-nums text-ink-500">
        {area.toFixed(1)} km²
      </span>
    </div>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] uppercase tracking-[0.09em] text-ink-500">{label}</span>
      <span className="text-[15px] tabular-nums text-ink-900">{value}</span>
      {hint ? <span className="text-[10px] text-ink-500">{hint}</span> : null}
    </div>
  );
}

export function FireContextPanel({ context }: { context: FireContext }) {
  const { analysis } = context;
  const terrain = context.terrain ?? {};
  const peak = context.peak_growth_day;
  const alignment = context.wind_alignment;

  return (
    <section className="animate-rise absolute bottom-4 left-4 z-20 w-[min(42rem,calc(100%-2rem))] rounded-2xl border border-paper-300 bg-white/96 p-4 shadow-sm backdrop-blur">
      <header className="mb-2 flex items-baseline justify-between gap-3">
        <div className="flex flex-col">
          <span className="text-[10px] uppercase tracking-[0.12em] text-ink-500">
            {analysis === "land_cover_composition"
              ? "Land cover and terrain"
              : analysis === "spread_behaviour"
                ? "Spread behaviour"
                : "Fire weather"}
          </span>
          <h3 className="text-[13px] font-medium text-ink-900">{context.event_name}</h3>
        </div>
        {context.footprint_area_km2 != null ? (
          <span className="text-[11px] tabular-nums text-ink-500">
            {context.footprint_area_km2.toFixed(0)} km² footprint
          </span>
        ) : null}
      </header>

      {analysis === "land_cover_composition" ? (
        <div className="flex flex-col gap-2">
          <div className="flex flex-col gap-1">
            {context.composition.slice(0, 7).map((item) => (
              <ShareBar
                key={item.code}
                label={item.label}
                percent={item.percent}
                area={item.area_km2}
                color={item.color}
              />
            ))}
          </div>
          {Object.keys(terrain).length > 0 ? (
            <div className="grid grid-cols-3 gap-3 border-t border-paper-300 pt-2">
              {terrain.elevation_m ? (
                <Stat
                  label="Elevation"
                  value={`${terrain.elevation_m.mean.toFixed(0)} m`}
                  hint={`${terrain.elevation_m.min?.toFixed(0)}–${terrain.elevation_m.max?.toFixed(0)} m`}
                />
              ) : null}
              {terrain.slope_deg ? (
                <Stat
                  label="Slope"
                  value={`${terrain.slope_deg.mean.toFixed(0)}°`}
                  hint={`up to ${terrain.slope_deg.max?.toFixed(0)}°`}
                />
              ) : null}
              {context.elevation_trend ? (
                <Stat
                  label="Ran"
                  value={context.elevation_trend.direction}
                  hint={`${Math.abs(context.elevation_trend.change_m).toFixed(0)} m first half to second`}
                />
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}

      {analysis === "spread_behaviour" ? (
        <div className="flex flex-col gap-2">
          {peak ? (
            <div className="grid grid-cols-3 gap-3">
              <Stat
                label="Largest day"
                value={`${peak.new_area_km2.toFixed(0)} km²`}
                hint={peak.date}
              />
              <Stat
                label="Centre moved"
                value={
                  peak.centroid_shift_km != null ? `${peak.centroid_shift_km.toFixed(1)} km` : "—"
                }
                hint={peak.spread_compass ? `toward ${peak.spread_compass}` : undefined}
              />
              <Stat
                label="Off downwind"
                value={
                  peak.spread_wind_offset_deg != null
                    ? `${peak.spread_wind_offset_deg.toFixed(0)}°`
                    : "—"
                }
                hint="on that day"
              />
            </div>
          ) : null}
          {alignment ? (
            <p className="border-t border-paper-300 pt-2 text-[11px] leading-[1.5] text-ink-500">
              Across {alignment.days_compared} days with measurable movement, spread sat{" "}
              <span className="tabular-nums text-ink-900">
                {alignment.area_weighted_offset_deg.toFixed(0)}°
              </span>{" "}
              from downwind on an area-weighted basis;{" "}
              <span className="tabular-nums text-ink-900">
                {alignment.area_share_within_45_deg.toFixed(0)}%
              </span>{" "}
              of the burned area grew within 45° of downwind.
            </p>
          ) : null}
        </div>
      ) : null}

      {analysis === "fire_weather" && peak?.conditions ? (
        <div className="flex flex-col gap-2">
          <div className="grid grid-cols-3 gap-3">
            {Object.entries(peak.conditions)
              .slice(0, 6)
              .map(([key, sample]) => (
                <Stat
                  key={key}
                  label={sample.label}
                  value={`${sample.mean.toLocaleString(undefined, {
                    maximumFractionDigits: 2,
                  })}${sample.unit === "index" ? "" : ` ${sample.unit}`}`}
                />
              ))}
          </div>
          <p className="border-t border-paper-300 pt-2 text-[11px] text-ink-500">
            Sampled over the {peak.conditions_zone ?? "footprint"} on {peak.date}, the day of
            largest growth.
          </p>
        </div>
      ) : null}

      <p className="mt-2 border-t border-paper-300 pt-2 text-[10px] leading-[1.5] text-ink-500">
        {context.caveats[0]}
      </p>
    </section>
  );
}
