"use client";

import type { Health, ShowcaseArea } from "@/lib/types";

/**
 * Left column, deliberately empty.
 *
 * What used to live here - pipeline progress and the agent's reading of the
 * user - moved to the stepper above the map and into the contract panel, where
 * both belong. Rather than backfill this space with something plausible, it is
 * held open for product to design: an invented panel is harder to remove later
 * than an obviously blank one.
 *
 * The two things that do stay are session-level, not analysis: which model is
 * running, and which area the showcase dataset covers.
 */
export function Sidebar({
  health,
  area,
  onReset,
}: {
  health: Health | null;
  area: ShowcaseArea | null;
  onReset: () => void;
}) {
  return (
    <aside className="flex h-full w-[15rem] shrink-0 flex-col border-r border-paper-300 bg-paper-50">
      <header className="px-5 py-4">
        <h1 className="text-[13.5px] font-semibold leading-tight tracking-tight text-ink-900">
          Wildfire Analyst
        </h1>
        <p className="mt-0.5 text-[11px] text-ink-400">Multi-agent geospatial analysis</p>
      </header>

      <div className="flex flex-1 items-center justify-center px-5">
        <div className="w-full rounded-xl border border-dashed border-paper-400 px-4 py-8 text-center">
          <p className="text-[11.5px] font-medium text-ink-500">To be designed</p>
          <p className="mt-1 text-[10.5px] leading-[1.45] text-ink-400">
            Reserved for the data-layer panel once the product design lands.
          </p>
        </div>
      </div>

      {area && (
        <div className="px-5 pb-4">
          <p className="text-[10px] font-semibold uppercase tracking-[0.08em] text-ink-400">
            Study area
          </p>
          <p className="mt-1 text-[12px] font-medium text-ink-900">{area.name}</p>
          <p className="mt-0.5 text-[10.5px] leading-[1.45] text-ink-400">{area.context}</p>
        </div>
      )}

      <footer className="flex items-center justify-between gap-2 border-t border-paper-300 px-5 py-3">
        {health && (
          <span className="flex min-w-0 items-center gap-1.5" title={health.llm}>
            <span
              className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                health.ok ? "bg-sage-500" : "bg-ember-500"
              }`}
            />
            <span className="truncate text-[11px] text-ink-400">{health.llm}</span>
            {health.mock && (
              <span className="shrink-0 rounded bg-ember-100 px-1 py-px text-[9px] font-semibold uppercase tracking-wide text-ember-600">
                mock
              </span>
            )}
          </span>
        )}
        <button
          onClick={onReset}
          className="shrink-0 text-[11px] text-ink-400 transition hover:text-ink-900"
        >
          New
        </button>
      </footer>
    </aside>
  );
}
