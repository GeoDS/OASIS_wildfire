"use client";

import { useEffect, useRef, useState } from "react";

import type { ExpertiseLevel } from "@/lib/types";

/**
 * Expertise selector, sitting next to the composer the way a model picker does
 * in a chat product.
 *
 * It belongs here rather than in the sidebar because it changes *how the agent
 * talks to you* - same class of setting as picking a model - and the effect is
 * only visible in the reply. A control whose consequence appears in the chat
 * should live within reach of the chat.
 */

const LEVELS: { id: ExpertiseLevel; label: string; blurb: string }[] = [
  {
    id: "general",
    label: "General",
    blurb: "Plain language, concrete choices, uncertainty spelled out",
  },
  {
    id: "practitioner",
    label: "Practitioner",
    blurb: "Domain terms, assumptions, data sources, decision-oriented output",
  },
  {
    id: "expert",
    label: "Expert",
    blurb: "Datasets, resolution, parameters, provenance, reproducible workflow",
  },
];

export function ExpertisePicker({
  value,
  inferred,
  onChange,
}: {
  /** Explicit user pick; null means "let the agent infer" */
  value: ExpertiseLevel | null;
  /** What the agent inferred, shown when there is no explicit pick */
  inferred: ExpertiseLevel | null;
  onChange: (level: ExpertiseLevel | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const effective = value ?? inferred;
  const current = LEVELS.find((l) => l.id === effective);

  return (
    <div ref={rootRef} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="flex items-center gap-1.5 rounded-lg px-2 py-1 text-[12px] text-ink-500 transition hover:bg-paper-200 hover:text-ink-900"
      >
        <span className="font-medium">{current?.label ?? "Auto"}</span>
        {!value && inferred && <span className="text-ink-400">· inferred</span>}
        <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden className="opacity-50">
          <path
            d="M2 4l3 3 3-3"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>

      {open && (
        <div
          role="listbox"
          className="absolute bottom-full left-0 z-20 mb-2 w-[19rem] overflow-hidden rounded-xl border border-paper-300 bg-white shadow-[0_8px_28px_-8px_rgb(31_30_29/0.18)]"
        >
          <p className="border-b border-paper-200 px-3 py-2 text-[11px] text-ink-400">
            How technical should the agent be?
          </p>
          {LEVELS.map((level) => {
            const selected = value === level.id;
            return (
              <button
                key={level.id}
                role="option"
                aria-selected={selected}
                onClick={() => {
                  onChange(level.id);
                  setOpen(false);
                }}
                className="flex w-full items-start gap-2.5 px-3 py-2.5 text-left transition hover:bg-paper-100"
              >
                <span
                  className={`mt-1 h-1.5 w-1.5 shrink-0 rounded-full ${
                    selected
                      ? "bg-ember-500"
                      : effective === level.id
                        ? "bg-paper-400"
                        : "bg-transparent"
                  }`}
                />
                <span className="min-w-0">
                  <span className="block text-[13px] font-medium text-ink-900">
                    {level.label}
                    {!value && effective === level.id && (
                      <span className="ml-1.5 font-normal text-ink-400">inferred</span>
                    )}
                  </span>
                  <span className="mt-0.5 block text-[11px] leading-4 text-ink-500">
                    {level.blurb}
                  </span>
                </span>
              </button>
            );
          })}

          <button
            onClick={() => {
              onChange(null);
              setOpen(false);
            }}
            className="w-full border-t border-paper-200 px-3 py-2 text-left text-[12px] text-ink-500 transition hover:bg-paper-100"
          >
            Let the agent infer
          </button>
        </div>
      )}
    </div>
  );
}
