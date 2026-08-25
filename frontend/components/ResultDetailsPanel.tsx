"use client";

import { useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";

import { countLabel, shortDate } from "@/lib/presentation";
import type {
  AnalysisContract,
  ExecutionPlan,
  FireContext,
  FireDataStatus,
  FireLifecycle,
  LayerResult,
  RasterLayerResult,
  SpatialAnalysis,
} from "@/lib/types";

type Tab = "summary" | "data" | "method" | "limits";

const TABS: Array<[Tab, string]> = [
  ["summary", "Result"],
  ["data", "Data"],
  ["method", "How it was made"],
  ["limits", "Limits"],
];

const PANEL_HEIGHTS = [144, 208, 360] as const;
const MIN_PANEL_HEIGHT = PANEL_HEIGHTS[0];

function maximumPanelHeight() {
  if (typeof window === "undefined") return 520;
  return Math.max(PANEL_HEIGHTS[1], Math.min(560, Math.round(window.innerHeight * 0.65)));
}

function clampPanelHeight(height: number) {
  return Math.min(maximumPanelHeight(), Math.max(MIN_PANEL_HEIGHT, height));
}

function Empty({ children }: { children: string }) {
  return <p className="rounded-xl border border-dashed border-paper-400 p-4 text-[11px] leading-5 text-ink-400">{children}</p>;
}

export function ResultDetailsPanel({
  collapsed,
  onCollapsedChange,
  height,
  onHeightChange,
  onResizingChange,
  contract,
  plan,
  layers,
  rasters,
  fireDataStatus,
  lifecycle,
  spatialAnalysis,
  fireContext,
}: {
  collapsed: boolean;
  onCollapsedChange: (collapsed: boolean) => void;
  height: number;
  onHeightChange: (height: number) => void;
  onResizingChange: (resizing: boolean) => void;
  contract: AnalysisContract | null;
  plan: ExecutionPlan | null;
  layers: LayerResult[];
  rasters: RasterLayerResult[];
  fireDataStatus: FireDataStatus | null;
  lifecycle: FireLifecycle | null;
  spatialAnalysis: SpatialAnalysis | null;
  fireContext: FireContext | null;
}) {
  const [tab, setTab] = useState<Tab>("summary");
  const drag = useRef<{ pointerId: number; startY: number; startHeight: number } | null>(null);
  const dragged = useRef(false);
  const limitations = useMemo(
    () => [
      ...(contract?.assumptions ?? []),
      ...(contract?.unresolved ?? []),
      ...(plan?.unmet.map((item) => item.reason) ?? []),
      ...layers.map((layer) => layer.caveat).filter(Boolean),
      ...(spatialAnalysis?.caveats ?? []),
      ...(fireContext?.caveats ?? []),
      ...(lifecycle?.caveat ? [lifecycle.caveat] : []),
    ].filter((item, index, all) => all.indexOf(item) === index),
    [contract, fireContext, layers, lifecycle, plan, spatialAnalysis],
  );
  const coverage = layers.map((layer) => layer.as_of).filter(Boolean) as string[];

  const expandTo = (nextHeight: number) => {
    onHeightChange(clampPanelHeight(nextHeight));
    onCollapsedChange(false);
  };

  const cycleHeight = () => {
    if (dragged.current) {
      dragged.current = false;
      return;
    }
    if (collapsed) {
      expandTo(PANEL_HEIGHTS[1]);
      return;
    }
    const next = PANEL_HEIGHTS.find((preset) => preset > height + 8) ?? PANEL_HEIGHTS[0];
    expandTo(next);
  };

  const startResize = (event: PointerEvent<HTMLButtonElement>) => {
    drag.current = {
      pointerId: event.pointerId,
      startY: event.clientY,
      startHeight: collapsed ? PANEL_HEIGHTS[1] : height,
    };
    dragged.current = false;
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const resize = (event: PointerEvent<HTMLButtonElement>) => {
    if (!drag.current || drag.current.pointerId !== event.pointerId) return;
    const distance = drag.current.startY - event.clientY;
    if (Math.abs(distance) < 4) return;
    if (!dragged.current) {
      dragged.current = true;
      onCollapsedChange(false);
      onResizingChange(true);
    }
    onHeightChange(clampPanelHeight(drag.current.startHeight + distance));
  };

  const finishResize = (event: PointerEvent<HTMLButtonElement>) => {
    if (!drag.current || drag.current.pointerId !== event.pointerId) return;
    drag.current = null;
    onResizingChange(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };

  const resizeWithKeyboard = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (!["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    if (event.key === "Home") expandTo(MIN_PANEL_HEIGHT);
    else if (event.key === "End") expandTo(maximumPanelHeight());
    else expandTo((collapsed ? PANEL_HEIGHTS[1] : height) + (event.key === "ArrowUp" ? 24 : -24));
  };

  return (
    <section className="flex h-full min-h-0 flex-col border-t border-paper-300 bg-paper-50" aria-label="Analysis details">
      <header className="flex min-h-11 shrink-0 items-center justify-between gap-2 border-b border-paper-300 pl-2 pr-1.5">
        <div className="flex min-w-0 overflow-x-auto" role="tablist" aria-label="Analysis detail sections">
          {TABS.map(([id, label]) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={tab === id}
              onClick={() => {
                setTab(id);
                if (collapsed) onCollapsedChange(false);
              }}
              className={`min-h-11 shrink-0 cursor-pointer border-b-2 px-3 text-[11px] font-medium transition focus-visible:outline-2 focus-visible:outline-ember-500 ${tab === id ? "border-ember-500 text-ink-900" : "border-transparent text-ink-400 hover:text-ink-900"}`}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="flex shrink-0 items-center">
          <button
            type="button"
            onClick={cycleHeight}
            onPointerDown={startResize}
            onPointerMove={resize}
            onPointerUp={finishResize}
            onPointerCancel={finishResize}
            onKeyDown={resizeWithKeyboard}
            className="flex h-10 w-10 touch-none cursor-ns-resize items-center justify-center rounded-lg text-ink-400 transition hover:bg-paper-200 hover:text-ink-900 focus-visible:outline-2 focus-visible:outline-ember-500"
            aria-label={`Resize results panel. Current height ${collapsed ? "collapsed" : `${height} pixels`}. Drag, click to cycle sizes, or use arrow keys.`}
            title="Drag to resize · click to change size"
          >
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden>
              <path d="M4 5.5h8M4 10.5h8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </button>
          <button
            type="button"
            onClick={() => onCollapsedChange(!collapsed)}
            aria-expanded={!collapsed}
            aria-controls="analysis-details-content"
            className="flex h-10 w-10 shrink-0 cursor-pointer items-center justify-center rounded-lg text-ink-400 transition hover:bg-paper-200 hover:text-ink-900 focus-visible:outline-2 focus-visible:outline-ember-500"
            aria-label={collapsed ? "Expand analysis details" : "Collapse analysis details"}
          >
            <svg className={`transition-transform duration-200 motion-reduce:transition-none ${collapsed ? "rotate-180" : ""}`} width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden>
              <path d="m4 10 4-4 4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
        </div>
      </header>

      {!collapsed && <div id="analysis-details-content" className="min-h-0 flex-1 overflow-y-auto px-3 py-2.5">
        {tab === "summary" && (
          <div className="grid gap-2.5 xl:grid-cols-[1.2fr_1fr]">
            <div>
              <p className="text-[9.5px] font-semibold uppercase tracking-[0.1em] text-ink-400">Question understood as</p>
              <p className="mt-1 text-[12px] leading-[1.55] text-ink-700">
                {contract?.restatement ?? contract?.original_request ?? "Ask a question to begin an analysis."}
              </p>
              {fireDataStatus?.message && <p className="mt-2 rounded-lg bg-white p-2.5 text-[11px] leading-[1.5] text-ink-500">{fireDataStatus.message}</p>}
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div className="rounded-lg border border-paper-300 bg-white p-2.5"><p className="text-[9px] uppercase tracking-wide text-ink-400">Data sources</p><p className="mt-0.5 text-[15px] font-semibold tabular-nums text-ink-900">{layers.length + rasters.length}</p></div>
              <div className="rounded-lg border border-paper-300 bg-white p-2.5"><p className="text-[9px] uppercase tracking-wide text-ink-400">Coverage</p><p className="mt-0.5 text-[10.5px] font-semibold text-ink-900">{coverage.length ? shortDate(coverage.sort()[0]) : lifecycle ? `${lifecycle.dates[0]}–${lifecycle.dates.at(-1)}` : "Not reported"}</p></div>
              {layers.slice(0, 4).map((layer) => (
                <div key={layer.capability_id} className="col-span-2 rounded-lg border border-paper-300 bg-white px-2.5 py-2"><p className="text-[11px] font-medium text-ink-900">{countLabel(layer)}</p><p className="mt-px text-[9px] text-ink-400">{layer.title}</p></div>
              ))}
              {spatialAnalysis && <div className="col-span-2 rounded-xl border border-paper-300 bg-white px-3 py-2.5"><p className="text-[11.5px] font-medium text-ink-900">{spatialAnalysis.statistics.valid_area_km2.toLocaleString()} km² with valid analysis values</p><p className="mt-0.5 text-[9.5px] text-ink-400">{spatialAnalysis.statistics.valid_pixels.toLocaleString()} valid raster pixels</p></div>}
            </div>
          </div>
        )}

        {tab === "data" && (
          <div className="space-y-2">
            {layers.length === 0 && rasters.length === 0 ? <Empty>No data has been selected yet. Source, coverage, quality, and object counts will appear here.</Empty> : null}
            {layers.map((layer) => (
              <article key={layer.capability_id} className="grid gap-3 rounded-xl border border-paper-300 bg-white p-3 md:grid-cols-[minmax(10rem,1fr)_2fr_auto]">
                <div><h3 className="text-[11.5px] font-semibold text-ink-900">{layer.title}</h3><p className="mt-0.5 text-[10px] text-ink-500">{countLabel(layer)}</p></div>
                <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-[9.5px]"><div><dt className="text-ink-400">Source</dt><dd className="mt-0.5 text-ink-700">{layer.source}</dd></div><div><dt className="text-ink-400">Coverage</dt><dd className="mt-0.5 text-ink-700">{shortDate(layer.as_of)}</dd></div><div><dt className="text-ink-400">Retrieved</dt><dd className="mt-0.5 text-ink-700">{layer.retrieved_at ?? "Bundled snapshot"}</dd></div><div><dt className="text-ink-400">Geometry</dt><dd className="mt-0.5 text-ink-700">{layer.geometry_type}</dd></div></dl>
                <span className={`self-start rounded-full px-2 py-1 text-[9px] font-medium ${layer.truncated ? "bg-ember-100 text-ember-600" : "bg-sage-100 text-sage-500"}`}>{layer.truncated ? "Partial" : "Loaded"}</span>
              </article>
            ))}
            {rasters.map((raster) => (
              <article key={raster.id} className="grid gap-3 rounded-xl border border-paper-300 bg-white p-3 md:grid-cols-[minmax(10rem,1fr)_2fr_auto]"><div><h3 className="text-[11.5px] font-semibold text-ink-900">{raster.title}</h3><p className="mt-0.5 text-[10px] text-ink-500">Raster surface · band {raster.display_band}</p></div><dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-[9.5px]"><div><dt className="text-ink-400">Dataset</dt><dd className="mt-0.5 text-ink-700">{raster.dataset_id}</dd></div><div><dt className="text-ink-400">Date</dt><dd className="mt-0.5 text-ink-700">{raster.date}</dd></div><div><dt className="text-ink-400">Mask</dt><dd className="mt-0.5 text-ink-700">{raster.mask}</dd></div><div><dt className="text-ink-400">Value range</dt><dd className="mt-0.5 text-ink-700">{raster.value_range.join("–")}</dd></div></dl><span className="self-start rounded-full bg-sage-100 px-2 py-1 text-[9px] font-medium text-sage-500">Rendered</span></article>
            ))}
          </div>
        )}

        {tab === "method" && (
          <div className="space-y-2">
            {[
              ["1", "Interpreted the request", contract?.restatement ?? contract?.original_request ?? "Waiting for a question"],
              ["2", "Resolved scope and assumptions", contract ? `${contract.filled_ratio * 100}% of required fields resolved; ${contract.assumptions.length} visible assumption(s).` : "No scope resolved yet."],
              ["3", "Selected evidence", plan?.layers.length ? plan.layers.map((item) => `${item.title}: ${item.reason}`).join(" · ") : `${layers.length + rasters.length} source(s) supplied the current view.`],
              ["4", "Filtered and aligned", spatialAnalysis ? spatialAnalysis.operations.map((operation) => operation.type.replaceAll("_", " ")).join(" → ") : layers.length ? layers.map((layer) => `${countLabel(layer)} passed to the map`).join(" · ") : "No filtering result yet."],
              ["5", "Built the visible result", spatialAnalysis?.summary ?? fireContext?.summary ?? fireDataStatus?.message ?? "The map, chart, and data panel update from the same result payload."],
            ].map(([number, title, detail]) => (
              <article key={number} className="grid grid-cols-[2rem_1fr] gap-3 rounded-xl border border-paper-300 bg-white p-3"><span className="flex h-8 w-8 items-center justify-center rounded-full bg-ember-100 text-[11px] font-semibold text-ember-600">{number}</span><div><h3 className="text-[11.5px] font-semibold text-ink-900">{title}</h3><p className="mt-1 text-[10.5px] leading-[1.5] text-ink-500">{detail}</p></div></article>
            ))}
            <p className="pt-1 text-[9.5px] leading-[1.5] text-ink-400">This is an auditable record of inputs and operations, not the model’s private chain of thought.</p>
          </div>
        )}

        {tab === "limits" && (
          <div className="grid gap-3 md:grid-cols-2">
            <section className="rounded-xl border border-paper-300 bg-white p-3"><h3 className="text-[10px] font-semibold uppercase tracking-[0.08em] text-ink-400">Assumptions and caveats</h3>{limitations.length ? <ul className="mt-2 space-y-2">{limitations.map((item) => <li key={item} className="flex gap-2 text-[10.5px] leading-[1.5] text-ink-500"><span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-ember-500" />{item}</li>)}</ul> : <p className="mt-2 text-[10.5px] text-ink-400">No limitations have been reported yet.</p>}</section>
            <section className="rounded-xl border border-paper-300 bg-white p-3"><h3 className="text-[10px] font-semibold uppercase tracking-[0.08em] text-ink-400">Unavailable capability</h3>{plan?.unmet.length ? <ul className="mt-2 space-y-2">{plan.unmet.map((item) => <li key={item.hazard_object} className="text-[10.5px] leading-[1.5] text-ink-500"><span className="font-medium text-ink-700">{item.hazard_object.replaceAll("_", " ")}:</span> {item.reason}</li>)}</ul> : <p className="mt-2 text-[10.5px] text-ink-400">No requested capability is currently marked unavailable.</p>}<details className="mt-3 border-t border-paper-200 pt-3"><summary className="cursor-pointer text-[10px] font-medium text-ink-500">Technical details</summary><pre className="mt-2 max-h-36 overflow-auto whitespace-pre-wrap rounded-lg bg-paper-100 p-2 font-mono text-[8.5px] leading-4 text-ink-500">{JSON.stringify({ schema: contract?.schema_version, intent: contract?.task_intent, pending: contract?.pending_slots, analysis: spatialAnalysis?.analysis_id ?? fireContext?.analysis_id }, null, 2)}</pre></details></section>
          </div>
        )}
      </div>}
    </section>
  );
}
