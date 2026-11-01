"use client";

import { useState } from "react";

import type { LifecycleAnalysis } from "@/lib/types";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function day(value: string) {
  const [, month, date] = value.split("-");
  return `${MONTHS[Number(month) - 1]} ${Number(date)}`;
}

/** `Sep 7–9` within a month, `Aug 30–Sep 2` across one. */
function span(start: string, end: string) {
  if (start === end) return day(start);
  if (start.slice(0, 7) === end.slice(0, 7)) return `${day(start)}–${Number(end.slice(8))}`;
  return `${day(start)}–${day(end)}`;
}

/**
 * The rule-based phases the backend attaches to every lifecycle. Folded by
 * default, so the panel reads as it did until someone opens it; it reads only
 * the payload already on screen and asks the backend for nothing new.
 */
export function LifecyclePhasesSection({
  analysis,
  selectedDate,
  onDateChange,
}: {
  analysis: LifecycleAnalysis;
  selectedDate: string;
  onDateChange: (day: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const { phases, pre_ignition: early, record_end: end } = analysis;
  return (
    <section className="mt-3 border-t border-paper-200 pt-2" aria-label="Life cycle phases">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="flex min-h-9 w-full cursor-pointer items-center gap-2 rounded-md px-1 text-left text-[11px] font-semibold text-ink-700 transition hover:bg-paper-100 focus-visible:outline-2 focus-visible:outline-ember-500"
      >
        <svg width="10" height="10" viewBox="0 0 10 10" fill="none" aria-hidden className={`shrink-0 text-ink-400 transition-transform ${open ? "rotate-90" : ""}`}>
          <path d="m3.5 2 3 3-3 3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        <span className="min-w-0 truncate">Life cycle phases</span>
        <span className="ml-auto shrink-0 whitespace-nowrap font-mono text-[10px] font-normal text-ink-400">
          {span(analysis.first_burn_date, end.date)}
        </span>
      </button>
      {open && (
        <div className="mt-1 space-y-1.5">
          <ol className="space-y-0.5">
            {phases.map((phase) => {
              const current = phase.start <= selectedDate && selectedDate <= phase.end;
              return (
                <li key={phase.id}>
                  <button
                    type="button"
                    title={phase.rule}
                    aria-current={current ? "step" : undefined}
                    onClick={() => onDateChange(phase.end)}
                    className={`block w-full cursor-pointer rounded-md px-1.5 py-1.5 text-left transition hover:bg-paper-100 focus-visible:outline-2 focus-visible:outline-ember-500 ${current ? "bg-ember-50" : ""}`}
                  >
                    {/* One column: the panel is narrow beside the map and chat. */}
                    <span className="flex flex-wrap items-baseline gap-x-1.5">
                      <span className={`font-mono text-[10px] tabular-nums ${current ? "font-semibold text-ember-600" : "text-ink-400"}`}>
                        {span(phase.start, phase.end)}
                      </span>
                      <span className="text-[11px] font-semibold text-ink-900">{phase.label}</span>
                      <span className={`rounded-full border px-1.5 text-[9px] ${phase.qualifier === "observed" ? "border-ember-300 text-ember-600" : "border-paper-400 text-ink-400"}`}>
                        {phase.qualifier}
                      </span>
                    </span>
                    <span className="mt-0.5 block text-[10.5px] leading-[1.45] text-ink-500">{phase.note}</span>
                  </button>
                </li>
              );
            })}
          </ol>
          {/* The wording lives in the backend with the numbers it describes. */}
          {early?.note && <p className="px-1.5 text-[10px] leading-[1.45] text-ink-500">{early.note}</p>}
          {end.note && <p className="px-1.5 text-[10px] leading-[1.45] text-ink-500">{end.note}</p>}
          <details className="px-1.5">
            <summary className="cursor-pointer text-[10px] font-medium text-ink-400 hover:text-ink-700">How phases are defined</summary>
            <ul className="mt-1 space-y-0.5 text-[9.5px] leading-[1.4] text-ink-400">
              {analysis.caveats.map((caveat) => (
                <li key={caveat}>{caveat.replace(/^How phases are defined:\s*/, "")}</li>
              ))}
            </ul>
          </details>
        </div>
      )}
    </section>
  );
}
