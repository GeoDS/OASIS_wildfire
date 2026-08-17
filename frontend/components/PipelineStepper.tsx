"use client";

import { HANDOFF_INDEX, STAGES, type StageId, type StageStatus } from "@/lib/types";

/**
 * The pipeline, read left to right above the map.
 *
 * Horizontal because the process is linear, and because a vertical rail spent a
 * whole column on six short labels. The break in the middle is the point of the
 * component: it shows where the User Goal Agent stops and the Planning Agent
 * takes over, with the Analysis Contract as the only thing crossing between
 * them. Anyone watching should see the handoff without being told about it.
 */

// Short enough not to truncate at six across. The stepper is a progress
// indicator, not documentation - the full names live in the docs.
const LABELS: Record<StageId, string> = {
  requirement_understanding: "Understand",
  task_compiler: "Compile",
  ambiguity_resolution: "Clarify",
  analysis_contract: "Contract",
  planning: "Select layers",
  execution: "Fetch & render",
};

const TITLES: Record<StageId, string> = {
  requirement_understanding: "Requirement Understanding - intent x role x expertise",
  task_compiler: "Task Compiler - fill slots, judge which gaps matter",
  ambiguity_resolution: "Ambiguity Resolution - ask only what changes the answer",
  analysis_contract: "Analysis Contract - the interface handed downstream",
  planning: "Layer Selection - the model proposes, the registry validates",
  execution: "Fetch & Render - load snapshots, clip to scope, draw",
};

// Returns a span, not an <li>: the caller already provides the list item, and
// nesting <li> inside <li> is invalid HTML. The browser silently repairs it,
// which then fails hydration against the server-rendered markup.
function Step({ id, status, index }: { id: StageId; status: StageStatus; index: number }) {
  return (
    <span className="flex min-w-0 items-center gap-1.5" title={TITLES[id]}>
      <span
        className={`flex h-4 w-4 shrink-0 items-center justify-center rounded-full border-2 transition ${
          status === "done"
            ? "border-ember-500 bg-ember-500"
            : status === "active"
              ? "border-ember-500 bg-white"
              : "border-paper-400 bg-paper-100"
        }`}
      >
        {status === "done" ? (
          <svg width="8" height="8" viewBox="0 0 8 8" aria-hidden>
            <path
              d="M1.5 4.2l1.8 1.8L6.5 2.5"
              fill="none"
              stroke="white"
              strokeWidth="1.7"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        ) : status === "active" ? (
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-ember-500" />
        ) : (
          <span className="text-[8px] font-semibold text-ink-400">{index + 1}</span>
        )}
      </span>
      <span
        className={`truncate text-[11.5px] leading-none ${
          status === "pending" ? "text-ink-400" : "text-ink-900"
        } ${status === "active" ? "font-semibold" : ""}`}
      >
        {LABELS[id]}
      </span>
    </span>
  );
}

export function PipelineStepper({ stages }: { stages: Record<StageId, StageStatus> }) {
  return (
    <div className="flex items-center gap-3 border-b border-paper-300 bg-paper-50 px-5 py-2.5">
      <ol className="flex min-w-0 flex-1 items-center gap-2.5 overflow-x-auto">
        {STAGES.map((id, i) => (
          <li key={id} className="flex min-w-0 items-center gap-2.5">
            {i === HANDOFF_INDEX && (
              <span className="flex shrink-0 items-center gap-2 pl-0.5" aria-hidden>
                <span className="h-4 w-px bg-paper-400" />
                <span className="whitespace-nowrap rounded-full bg-paper-200 px-2 py-0.5 text-[9.5px] font-medium uppercase tracking-[0.06em] text-ink-500">
                  Planning Agent
                </span>
              </span>
            )}
            <Step id={id} status={stages[id]} index={i} />
            {i < STAGES.length - 1 && i !== HANDOFF_INDEX - 1 && (
              <span className="h-px w-3 shrink-0 bg-paper-400" aria-hidden />
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}
