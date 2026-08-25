"use client";

import { useMemo, useState } from "react";
import type { ReactNode } from "react";

import type { StageId, StageStatus } from "@/lib/types";

const PHASES: Array<{ label: string; description: string; stages: StageId[] }> = [
  {
    label: "Understand question",
    description: "Interprets the request and determines which details matter.",
    stages: ["requirement_understanding", "task_compiler"],
  },
  {
    label: "Confirm scope",
    description: "Resolves ambiguity, location, time, and user-confirmed assumptions.",
    stages: ["ambiguity_resolution", "analysis_contract"],
  },
  {
    label: "Prepare data",
    description: "Selects available sources and checks whether they can answer the request.",
    stages: ["planning"],
  },
  {
    label: "Build result",
    description: "Fetches, filters, aligns, calculates, and renders the result.",
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
  actions,
}: {
  stages: Record<StageId, StageStatus>;
  actions?: ReactNode;
}) {
  const [expanded, setExpanded] = useState(false);
  const statuses = useMemo(
    () => PHASES.map((phase) => phaseStatus(stages, phase.stages)),
    [stages],
  );
  const activeIndex = statuses.findIndex((status) => status === "active");
  const completeCount = statuses.filter((status) => status === "done").length;
  const currentIndex = activeIndex >= 0 ? activeIndex : Math.min(completeCount, PHASES.length - 1);

  return (
    <div className="pipeline-shell relative z-30 shrink-0 border-b border-paper-300 bg-paper-50">
      <div className="flex min-w-0 items-stretch">
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          aria-expanded={expanded}
          className="flex min-h-12 min-w-0 flex-1 cursor-pointer items-center gap-2.5 px-3 text-left transition hover:bg-paper-100 focus-visible:outline-2 focus-visible:outline-ember-500"
        >
        <span className="pipeline-compact-summary min-w-0 flex-1">
          <span className="block text-[9.5px] font-semibold uppercase tracking-[0.1em] text-ink-400">
            Step {Math.min(currentIndex + 1, 4)} of 4
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

        <span className={`shrink-0 text-ink-400 transition-transform duration-200 ${expanded ? "rotate-180" : ""}`}>
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden>
            <path d="m4 6 4 4 4-4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </span>
        </button>
        {actions && <div className="flex shrink-0 items-center border-l border-paper-300 px-1">{actions}</div>}
      </div>

      {expanded && (
        <div className="animate-rise grid gap-1.5 border-t border-paper-300 bg-white px-3 py-2 sm:grid-cols-2 xl:grid-cols-4">
          {PHASES.map((phase, index) => (
            <section key={phase.label} className="rounded-lg bg-paper-100 p-2.5">
              <div className="flex items-center justify-between gap-2">
                <h2 className="text-[11.5px] font-semibold text-ink-900">{phase.label}</h2>
                <span className="text-[9.5px] capitalize text-ink-400">{statuses[index]}</span>
              </div>
              <p className="mt-1 text-[10.5px] leading-[1.45] text-ink-500">{phase.description}</p>
              <p className="mt-2 font-mono text-[9px] leading-[1.45] text-ink-400">
                {phase.stages.join(" · ").replaceAll("_", " ")}
              </p>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
