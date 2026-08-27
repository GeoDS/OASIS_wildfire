"use client";

import { useMemo } from "react";
import type { ReactNode } from "react";

import type { StageId, StageStatus } from "@/lib/types";

const PHASES: Array<{ label: string; stages: StageId[] }> = [
  {
    label: "Define Analysis",
    stages: [
      "requirement_understanding",
      "task_compiler",
      "ambiguity_resolution",
      "analysis_contract",
    ],
  },
  {
    label: "Select Data",
    stages: ["planning"],
  },
  {
    label: "Build Result",
    stages: ["execution"],
  },
];

function phaseStatus(stages: Record<StageId, StageStatus>, ids: StageId[]): StageStatus {
  const values = ids.map((id) => stages[id]);
  if (values.some((value) => value === "active")) return "active";
  if (values.every((value) => value === "done")) return "done";
  return "pending";
}

export function PipelineStepper({
  stages,
  onViewReasoning,
  actions,
}: {
  stages: Record<StageId, StageStatus>;
  onViewReasoning: () => void;
  actions?: ReactNode;
}) {
  const statuses = useMemo(
    () => PHASES.map((phase) => phaseStatus(stages, phase.stages)),
    [stages],
  );
  const activeIndex = statuses.findIndex((status) => status === "active");
  const completeCount = statuses.filter((status) => status === "done").length;
  const currentIndex = activeIndex >= 0 ? activeIndex : Math.min(completeCount, PHASES.length - 1);
  const complete = completeCount === PHASES.length;

  return (
    <div className="pipeline-shell relative z-30 shrink-0 border-b border-paper-300 bg-paper-50">
      <div className="flex min-w-0 items-stretch">
        <div className="flex min-h-12 min-w-0 flex-1 items-center gap-2.5 px-3">
          <span className="pipeline-compact-summary min-w-0 flex-1">
            <span className="block text-[9.5px] font-semibold tracking-[0.04em] text-ink-400">
              {complete ? "Analysis Complete" : `Step ${currentIndex + 1} of ${PHASES.length}`}
            </span>
            <span className="block truncate text-[12.5px] font-semibold text-ink-900">
              {PHASES[currentIndex].label}
            </span>
          </span>

          <ol className="pipeline-phases hidden min-w-0 flex-1 items-center">
            {PHASES.map((phase, index) => {
              const status = statuses[index];
              return (
                <li key={phase.label} className="flex min-w-0 flex-1 items-center">
                  <span className="flex min-w-0 items-center gap-2">
                    <span
                      className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-[9.5px] font-semibold ${
                        status === "done"
                          ? "border-ember-500 bg-ember-500 text-white"
                          : status === "active"
                            ? "border-ember-500 bg-ember-50 text-ember-600"
                            : "border-paper-400 bg-white text-ink-400"
                      }`}
                    >
                      {status === "done" ? (
                        <svg width="11" height="11" viewBox="0 0 12 12" fill="none" aria-hidden>
                          <path d="m2 6.2 2.4 2.4L10 3" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                      ) : index + 1}
                    </span>
                    <span className={`pipeline-wide-label text-[11.5px] leading-tight ${status === "pending" ? "text-ink-400" : "font-medium text-ink-900"}`}>
                      {phase.label}
                    </span>
                  </span>
                  {index < PHASES.length - 1 && <span className="mx-2 h-px min-w-3 flex-1 bg-paper-400" aria-hidden />}
                </li>
              );
            })}
          </ol>
        </div>
        <button type="button" onClick={onViewReasoning} className="hidden min-h-11 shrink-0 cursor-pointer items-center px-3 text-[10.5px] font-medium text-ink-500 transition hover:text-ember-600 focus-visible:outline-2 focus-visible:outline-ember-500 lg:flex">
          View Reasoning
        </button>
        {actions && <div className="flex shrink-0 items-center border-l border-paper-300 px-1">{actions}</div>}
      </div>
    </div>
  );
}
