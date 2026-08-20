"use client";

import { DataLayerPanel } from "@/components/DataLayerPanel";
import type { FireDataStatus, Health } from "@/lib/types";

/** Session controls plus the backend-owned fire-data decision state. */
export function Sidebar({
  health,
  fireDataStatus,
  analyzing,
  onReset,
}: {
  health: Health | null;
  fireDataStatus: FireDataStatus | null;
  analyzing: boolean;
  onReset: () => void;
}) {
  return (
    <aside className="flex h-full w-[17rem] shrink-0 flex-col border-r border-paper-300 bg-paper-50">
      <header className="px-5 py-4">
        <h1 className="text-[13.5px] font-semibold leading-tight tracking-tight text-ink-900">
          Wildfire Analyst
        </h1>
        <p className="mt-0.5 text-[11px] text-ink-400">Multi-agent geospatial analysis</p>
      </header>

      <DataLayerPanel
        status={fireDataStatus}
        analyzing={analyzing}
      />

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
