"use client";

import type { SpatialAnalysis } from "@/lib/types";

type AnalysisView = "before" | "after" | "difference";

const VIEW_LABELS: Record<AnalysisView, string> = {
  before: "Before",
  after: "After",
  difference: "Difference",
};

function formatSigned(value: number) {
  return `${value > 0 ? "+" : ""}${value.toFixed(3)}`;
}

export function SpatialAnalysisPanel({
  analysis,
  view,
  onViewChange,
}: {
  analysis: SpatialAnalysis;
  view: AnalysisView;
  onViewChange: (view: AnalysisView) => void;
}) {
  const stats = analysis.statistics;
  // One event carries every index-change result, so the panel takes its wording
  // from the payload rather than assuming the analysis it was first written for.
  const isSeverity = analysis.operation === "nbr_change";
  return (
    <section className="animate-rise absolute bottom-4 left-4 z-20 w-[min(42rem,calc(100%-2rem))] rounded-2xl border border-paper-300 bg-white/96 p-4 shadow-sm backdrop-blur">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.08em] text-ember-500">
            Derived raster analysis
          </p>
          <h2 className="mt-0.5 text-[14px] font-semibold text-ink-900">{analysis.title}</h2>
          <p className="mt-0.5 font-mono text-[9.5px] text-ink-400">{analysis.formula}</p>
        </div>
        <div className="flex rounded-lg border border-paper-300 bg-paper-100 p-0.5">
          {(Object.keys(VIEW_LABELS) as AnalysisView[]).map((item) => (
            <button
              key={item}
              type="button"
              onClick={() => onViewChange(item)}
              className={`rounded-md px-2.5 py-1 text-[10.5px] transition ${
                view === item
                  ? "bg-white font-medium text-ink-900 shadow-sm"
                  : "text-ink-400 hover:text-ink-700"
              }`}
            >
              {VIEW_LABELS[item]}
            </button>
          ))}
        </div>
      </header>

      <div className="mt-3 grid grid-cols-4 gap-2">
        {[
          ["Mean before", stats.mean_before.toFixed(3)],
          ["Mean after", stats.mean_after.toFixed(3)],
          ["Mean change", formatSigned(stats.mean_delta)],
          // "Pixels lower" reads the wrong way round for severity: a lower dNBR
          // means less damage, so the share worth showing is the damaged one.
          isSeverity
            ? ["Damaged", `${(stats.damaged_percent ?? 0).toFixed(0)}%`]
            : ["Pixels lower", `${stats.negative_percent.toFixed(0)}%`],
        ].map(([label, value]) => (
          <div key={label} className="rounded-lg bg-paper-100 px-2.5 py-2">
            <p className="text-[8.5px] uppercase tracking-wide text-ink-400">{label}</p>
            <p className="mt-0.5 text-[13px] font-semibold tabular-nums text-ink-900">{value}</p>
          </div>
        ))}
      </div>

      <p className="mt-2 text-[10px] leading-[1.45] text-ink-500">
        {stats.valid_pixels.toLocaleString()} valid pixels · approximately {stats.valid_area_km2.toLocaleString()} km² · masked to cumulative mapped BA
      </p>
      <p className="mt-1 text-[9px] leading-[1.4] text-ink-400">
        {analysis.caveats[0]}
      </p>
    </section>
  );
}
