"use client";

import { useEffect, useMemo, useState } from "react";

import type { FireLifecycle } from "@/lib/types";

function shortDate(value: string) {
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" }).format(
    new Date(`${value}T12:00:00Z`),
  );
}

export function FireLifecyclePanel({
  lifecycle,
  loading,
  onDateChange,
}: {
  lifecycle: FireLifecycle;
  loading: boolean;
  onDateChange: (day: string) => void | Promise<void>;
}) {
  const [playing, setPlaying] = useState(false);
  const selectedIndex = Math.max(0, lifecycle.dates.indexOf(lifecycle.selected_date));
  const maxGrowth = useMemo(
    () => Math.max(1, ...lifecycle.timeline.map((point) => point.new_burned_km2)),
    [lifecycle.timeline],
  );

  useEffect(() => {
    if (!playing || loading) return;
    if (selectedIndex >= lifecycle.dates.length - 1) {
      setPlaying(false);
      return;
    }
    const timer = window.setTimeout(
      () => void onDateChange(lifecycle.dates[selectedIndex + 1]),
      900,
    );
    return () => window.clearTimeout(timer);
  }, [lifecycle.dates, loading, onDateChange, playing, selectedIndex]);

  const metrics = lifecycle.selected_metrics;
  return (
    <section className="animate-rise absolute bottom-4 left-4 right-4 z-10 rounded-xl border border-paper-300 bg-white/96 p-3 shadow-sm backdrop-blur">
      <header className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="text-[10px] font-semibold uppercase tracking-[0.08em] text-ink-400">
            TS-SatFire historical event
          </p>
          <h2 className="mt-0.5 truncate text-[13px] font-semibold text-ink-900">
            {lifecycle.event_name} lifecycle
          </h2>
          <p className="text-[9.5px] text-ink-400">
            {lifecycle.dates[0]}–{lifecycle.dates[lifecycle.dates.length - 1]} ·{" "}
            {lifecycle.dates.length} daily frames
          </p>
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[9px] text-ink-500">
            <span className="flex items-center gap-1">
              <span className="h-2 w-3 rounded-[2px] bg-[#5b536c]" /> Cumulative BA label
            </span>
            <span className="flex items-center gap-1">
              <span className="h-2 w-3 rounded-[2px] bg-ember-500" /> Active fire label
            </span>
          </div>
        </div>
        <div className="grid shrink-0 grid-cols-3 gap-4 text-right">
          <div>
            <p className="text-[9px] uppercase tracking-wide text-ink-400">AF pixels</p>
            <p className="font-mono text-[12px] font-semibold text-ink-900">
              {metrics.active_pixels.toLocaleString()}
            </p>
          </div>
          <div>
            <p className="text-[9px] uppercase tracking-wide text-ink-400">New BA</p>
            <p className="font-mono text-[12px] font-semibold text-ink-900">
              {metrics.new_burned_km2.toLocaleString()} km²
            </p>
          </div>
          <div>
            <p className="text-[9px] uppercase tracking-wide text-ink-400">Cumulative BA</p>
            <p className="font-mono text-[12px] font-semibold text-ink-900">
              {metrics.cumulative_burned_km2.toLocaleString()} km²
            </p>
          </div>
        </div>
      </header>

      <div className="mt-2 border-t border-paper-200 pt-2">
        <div className="mb-1 flex items-end justify-between gap-3">
          <div>
            <p className="text-[10px] font-medium text-ink-700">Daily mapped BA growth</p>
            <p className="text-[9px] text-ink-400">Approximate grid area · km² per day</p>
          </div>
          <p className="font-mono text-[10.5px] font-semibold text-ink-700">
            {shortDate(lifecycle.selected_date)}
          </p>
        </div>
        <div className="flex h-10 items-end gap-[2px]" aria-label="Daily mapped burned-area growth">
          {lifecycle.timeline.map((point, index) => (
            <button
              key={point.date}
              type="button"
              title={`${point.date}: ${point.new_burned_km2} km² newly mapped BA`}
              aria-label={`Select ${point.date}`}
              onClick={() => void onDateChange(point.date)}
              className={`min-w-[3px] flex-1 rounded-t-[2px] transition-colors ${
                index === selectedIndex ? "bg-ember-500" : "bg-ember-300 hover:bg-ember-500"
              }`}
              style={{ height: `${Math.max(8, (point.new_burned_km2 / maxGrowth) * 100)}%` }}
            />
          ))}
        </div>
      </div>

      <div className="mt-2 flex items-center gap-2">
        <button
          type="button"
          onClick={() => setPlaying((value) => !value)}
          className="w-12 rounded-md border border-paper-300 bg-paper-50 px-2 py-1 text-[10px] font-medium text-ink-700 hover:bg-paper-200"
        >
          {playing ? "Pause" : "Play"}
        </button>
        <input
          aria-label="Historical fire date"
          type="range"
          min={0}
          max={Math.max(0, lifecycle.dates.length - 1)}
          value={selectedIndex}
          disabled={loading}
          onChange={(event) =>
            void onDateChange(lifecycle.dates[Number(event.currentTarget.value)])
          }
          className="min-w-0 flex-1 accent-[#c76e00]"
        />
        <span className="w-16 text-right text-[9.5px] text-ink-400">
          {loading ? "Loading…" : `${selectedIndex + 1} / ${lifecycle.dates.length}`}
        </span>
      </div>
      <p className="mt-1.5 truncate text-[8.5px] text-ink-400" title={lifecycle.caveat}>
        Historical AF/BA labels · mapped area is approximate · not current status or forecast
      </p>
    </section>
  );
}
