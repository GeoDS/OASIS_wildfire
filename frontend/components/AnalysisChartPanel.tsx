"use client";

import type {
  FireContext,
  FireLifecycle,
  LayerResult,
  SpatialAnalysis,
} from "@/lib/types";

function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-paper-300 bg-paper-50 px-2.5 py-2">
      <p className="text-[9px] font-semibold uppercase tracking-[0.09em] text-ink-400">{label}</p>
      <p className="mt-0.5 text-[14px] font-semibold tabular-nums text-ink-900">{value}</p>
      {hint && <p className="mt-0.5 text-[9.5px] text-ink-400">{hint}</p>}
    </div>
  );
}

export function AnalysisChartPanel({
  layers,
  lifecycle,
  lifecycleLoading,
  spatialAnalysis,
  fireContext,
  analysisView,
  activeLayerIds,
  onLayerSelect,
  onDateChange,
  onViewChange,
}: {
  layers: LayerResult[];
  lifecycle: FireLifecycle | null;
  lifecycleLoading: boolean;
  spatialAnalysis: SpatialAnalysis | null;
  fireContext: FireContext | null;
  analysisView: "before" | "after" | "difference";
  activeLayerIds: string[];
  onLayerSelect: (id: string | null, additive?: boolean) => void;
  onDateChange: (day: string) => void;
  onViewChange: (view: "before" | "after" | "difference") => void;
}) {
  if (spatialAnalysis) {
    const stats = spatialAnalysis.statistics;
    const breakdown = spatialAnalysis.class_breakdown ?? [];
    return (
      <section className="flex h-full min-h-0 flex-col overflow-y-auto bg-white p-3" aria-label="Analysis chart">
        <header>
          <p className="text-[9.5px] font-semibold uppercase tracking-[0.1em] text-ember-600">Linked analysis</p>
          <h2 className="mt-1 text-[14px] font-semibold text-ink-900">{spatialAnalysis.title}</h2>
          <p className="mt-1 font-mono text-[9.5px] text-ink-400">{spatialAnalysis.formula}</p>
          <div className="mt-3 flex rounded-xl border border-paper-300 bg-paper-100 p-1">
            {(["before", "after", "difference"] as const).map((view) => (
              <button key={view} type="button" onClick={() => onViewChange(view)} className={`min-h-9 flex-1 cursor-pointer rounded-lg px-2 text-[10.5px] capitalize transition ${analysisView === view ? "bg-white font-medium text-ink-900 shadow-sm" : "text-ink-400 hover:text-ink-900"}`}>{view}</button>
            ))}
          </div>
        </header>
        <div className="mt-3 grid grid-cols-2 gap-2">
          <Metric label="Mean before" value={stats.mean_before.toFixed(3)} />
          <Metric label="Mean after" value={stats.mean_after.toFixed(3)} />
          <Metric label="Mean change" value={`${stats.mean_delta > 0 ? "+" : ""}${stats.mean_delta.toFixed(3)}`} />
          <Metric label="Valid area" value={`${stats.valid_area_km2.toLocaleString()} km²`} />
        </div>
        {breakdown.length > 0 && (
          <div className="mt-4 space-y-2">
            <h3 className="text-[10px] font-semibold uppercase tracking-[0.08em] text-ink-400">Area distribution</h3>
            {breakdown.map((item) => (
              <div key={item.label}>
                <div className="mb-1 flex justify-between gap-3 text-[10.5px]"><span className="text-ink-700">{item.label}</span><span className="tabular-nums text-ink-500">{item.percent.toFixed(1)}% · {item.area_km2.toFixed(1)} km²</span></div>
                <div className="h-2 overflow-hidden rounded-full bg-paper-200"><div className="h-full rounded-full" style={{ width: `${Math.max(item.percent, 1)}%`, backgroundColor: item.color }} /></div>
              </div>
            ))}
          </div>
        )}
      </section>
    );
  }

  if (lifecycle) {
    const max = Math.max(...lifecycle.timeline.map((item) => item.new_burned_km2), 1);
    return (
      <section className="flex h-full min-h-0 flex-col overflow-y-auto bg-white p-3" aria-label="Fire lifecycle chart">
        <p className="text-[9.5px] font-semibold uppercase tracking-[0.1em] text-ember-600">Historical progression</p>
        <h2 className="mt-1 text-[14px] font-semibold text-ink-900">{lifecycle.event_name} lifecycle</h2>
        <div className="mt-3 grid grid-cols-2 gap-2">
          <Metric label="Cumulative burned area" value={`${lifecycle.selected_metrics.cumulative_burned_km2.toLocaleString()} km²`} />
          <Metric label="New on selected day" value={`${lifecycle.selected_metrics.new_burned_km2.toLocaleString()} km²`} hint={lifecycle.selected_date} />
        </div>
        <div className="mt-4 flex min-h-32 flex-1 items-end gap-1 border-b border-paper-300" aria-label="Daily mapped burned-area growth">
          {lifecycle.timeline.map((point) => (
            <button
              key={point.date}
              type="button"
              onClick={() => onDateChange(point.date)}
              aria-label={`${point.date}: ${point.new_burned_km2} square kilometres newly mapped`}
              title={`${point.date}: ${point.new_burned_km2} km²`}
              className={`min-w-2 flex-1 cursor-pointer rounded-t-sm transition-colors focus-visible:outline-2 focus-visible:outline-ember-500 ${point.date === lifecycle.selected_date ? "bg-ember-500" : "bg-[#d8ad58] hover:bg-[#c7983d]"}`}
              style={{ height: `${Math.max(5, (point.new_burned_km2 / max) * 100)}%` }}
            />
          ))}
        </div>
        <p className="mt-2 text-[10px] text-ink-400">{lifecycleLoading ? "Loading selected day…" : `${lifecycle.dates.length} daily frames · ${lifecycle.dates[0]}–${lifecycle.dates.at(-1)}`}</p>
      </section>
    );
  }

  if (fireContext) {
    const shares = fireContext.composition.slice(0, 8);
    return (
      <section className="flex h-full min-h-0 flex-col overflow-y-auto bg-white p-3" aria-label="Fire context chart">
        <p className="text-[9.5px] font-semibold uppercase tracking-[0.1em] text-ember-600">Context analysis</p>
        <h2 className="mt-1 text-[14px] font-semibold text-ink-900">{fireContext.title}</h2>
        {fireContext.footprint_area_km2 != null && <p className="mt-1 text-[11px] text-ink-500">{fireContext.footprint_area_km2.toLocaleString()} km² analysed footprint</p>}
        {shares.length > 0 ? (
          <div className="mt-4 space-y-3">
            {shares.map((item) => (
              <div key={item.label}>
                <div className="mb-1 flex justify-between gap-3 text-[10.5px]"><span className="truncate text-ink-700">{item.label}</span><span className="tabular-nums text-ink-500">{item.percent.toFixed(1)}%</span></div>
                <div className="h-2 overflow-hidden rounded-full bg-paper-200"><div className="h-full rounded-full" style={{ width: `${Math.max(item.percent, 1)}%`, backgroundColor: item.color }} /></div>
              </div>
            ))}
          </div>
        ) : (
          <p className="mt-4 rounded-xl bg-paper-100 p-3 text-[11px] leading-5 text-ink-500">{fireContext.summary}</p>
        )}
      </section>
    );
  }

  const visibleLayers = layers.filter((layer) => layer.feature_count > 0);
  return (
    <section className="flex h-full min-h-0 flex-col overflow-y-auto bg-white p-3" aria-label="Map layer overview">
      <header className="flex items-start justify-between gap-3 border-b border-paper-200 pb-2.5">
        <div>
          <h2 className="text-[14px] font-semibold text-ink-900">Map layers</h2>
          <p className="mt-0.5 text-[10px] leading-4 text-ink-400">Click to focus · Shift-click to add layers.</p>
        </div>
        <span className="shrink-0 rounded-full bg-paper-100 px-2 py-1 text-[9.5px] font-medium tabular-nums text-ink-500">{activeLayerIds.length ? `${activeLayerIds.length} selected` : `${visibleLayers.length} visible`}</span>
      </header>
      {visibleLayers.length ? (
        <div className="mt-2.5 space-y-1.5">
          {visibleLayers.map((layer) => {
            const selected = activeLayerIds.includes(layer.capability_id);
            return <button key={layer.capability_id} type="button" onClick={(event) => onLayerSelect(layer.capability_id, event.shiftKey)} aria-pressed={selected} className={`grid min-h-14 w-full cursor-pointer grid-cols-[2rem_minmax(0,1fr)] items-start gap-2.5 rounded-lg border px-2.5 py-2.5 text-left transition focus-visible:outline-2 focus-visible:outline-ember-500 ${selected ? "border-ember-300 bg-ember-50" : "border-paper-300 hover:bg-paper-100"}`}>
              <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-paper-300 bg-white text-ember-600">
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden>
                  {layer.geometry_type === "Point" ? <><circle cx="8" cy="7" r="2.25" stroke="currentColor" strokeWidth="1.4" /><path d="M8 13c2.3-2.7 3.4-4.6 3.4-6A3.4 3.4 0 0 0 4.6 7c0 1.4 1.1 3.3 3.4 6Z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" /></> : <><path d="m3 5 5-2 5 2v6l-5 2-5-2V5Z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" /><path d="m3 5 5 2 5-2M8 7v6" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" /></>}
                </svg>
              </span>
              <span className="min-w-0 flex-1">
                <span className="flex flex-wrap items-start gap-x-1.5 gap-y-1">
                  <span className="break-words text-[11.5px] font-medium leading-4 text-ink-900">{layer.title}</span>
                  {selected && <span className="shrink-0 rounded-full bg-ember-100 px-1.5 py-0.5 text-[9px] font-medium leading-4 text-ember-600">Selected</span>}
                </span>
                <span className="mt-1 block break-words text-[9.5px] leading-4 text-ink-400">{layer.source}</span>
              </span>
            </button>;
          })}
        </div>
      ) : (
        <div className="mt-3 flex flex-1 flex-col items-center justify-center rounded-xl border border-dashed border-paper-400 p-4 text-center">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" className="text-ink-300" aria-hidden><path d="m4 8 8-4 8 4-8 4-8-4Zm0 4 8 4 8-4M4 16l8 4 8-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg>
          <p className="mt-2 text-[11px] font-medium text-ink-600">No map layers yet</p>
          <p className="mt-1 max-w-52 text-[10px] leading-4 text-ink-400">Layer names and sources will appear after data is fetched.</p>
        </div>
      )}
    </section>
  );
}
