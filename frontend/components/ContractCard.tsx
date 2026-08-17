"use client";

import type { AnalysisContract, ExecutionPlan, Provenance, Slot } from "@/lib/types";

/**
 * The Analysis Contract, taking shape field by field.
 *
 * This panel is the deliverable, not a debug view: it is what the Planning
 * Agent receives. Provenance colour and the assumptions list carry the whole
 * argument of the project - the user must be able to see, at a glance, which
 * parts of the specification they supplied and which the agent guessed.
 */

const SOURCE_STYLE: Record<Provenance, { label: string; dot: string; text: string }> = {
  user_stated: { label: "you", dot: "bg-sage-500", text: "text-ink-700" },
  agent_inferred: { label: "inferred", dot: "bg-ember-300", text: "text-ink-500" },
  default: { label: "default", dot: "bg-paper-400", text: "text-ink-400" },
};

function SlotRow({ name, slot }: { name: string; slot: Slot }) {
  const filled = Boolean(slot.value?.trim());
  const pending = slot.is_blocking && !filled;
  const style = SOURCE_STYLE[slot.source];

  return (
    <div
      className={`animate-rise grid grid-cols-[7.5rem_1fr_auto] items-baseline gap-3 border-b border-paper-200 px-4 py-2.5 last:border-b-0 ${
        pending ? "bg-ember-50/60" : ""
      }`}
    >
      <span className="flex items-baseline gap-1">
        <span className="font-mono text-[11px] text-ink-500">{name}</span>
        {slot.is_blocking && (
          <span
            className="cursor-help text-[10px] text-ember-500"
            title={slot.blocking_reason ?? "Missing this would materially change the analysis"}
          >
            ●
          </span>
        )}
      </span>

      <span className="min-w-0 text-[12.5px] leading-[1.45]">
        {filled ? (
          <span className="text-ink-900">{slot.value}</span>
        ) : (
          <span className="italic text-ink-400">{pending ? "awaiting your answer" : "—"}</span>
        )}
        {slot.kind === "spatial" && slot.raw && slot.raw !== slot.value && (
          <span className="ml-1.5 text-[11px] text-ink-400">said “{slot.raw}”</span>
        )}
      </span>

      <span className="flex items-center gap-1.5" title={`${style.label} · confidence ${slot.confidence.toFixed(2)}`}>
        <span className={`h-1.5 w-1.5 rounded-full ${style.dot}`} />
        <span className={`text-[10px] ${style.text}`}>{style.label}</span>
      </span>
    </div>
  );
}

function NoteList({
  title,
  items,
  tone,
}: {
  title: string;
  items: string[];
  tone: "assumption" | "unresolved";
}) {
  const accent = tone === "assumption" ? "text-ember-600" : "text-ink-900";
  return (
    <section className="min-w-0">
      <h3 className="mb-1.5 text-[10px] font-semibold uppercase tracking-[0.08em] text-ink-400">
        {title} <span className={accent}>{items.length}</span>
      </h3>
      <ul className="space-y-1">
        {items.map((item, i) => (
          <li key={i} className="flex gap-1.5 text-[11.5px] leading-[1.45] text-ink-500">
            <span className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-paper-400" />
            {item}
          </li>
        ))}
      </ul>
    </section>
  );
}

export function ContractCard({
  contract,
  plan,
}: {
  contract: AnalysisContract | null;
  plan: ExecutionPlan | null;
}) {
  if (!contract) {
    return (
      <div className="flex h-full items-center justify-center px-10 text-center">
        <p className="max-w-sm text-[12.5px] leading-[1.6] text-ink-400">
          The Analysis Contract assembles here, field by field. Every answer you give fills a
          slot, retires an assumption, and tightens the circle on the map.
        </p>
      </div>
    );
  }

  const percent = Math.round(contract.filled_ratio * 100);

  return (
    <div className="h-full overflow-y-auto">
      <header className="sticky top-0 z-10 flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-paper-200 bg-paper-50/95 px-4 py-3 backdrop-blur">
        <h2 className="text-[12.5px] font-semibold tracking-tight text-ink-900">
          Analysis Contract
        </h2>
        <span className="font-mono text-[10px] text-ink-400">v{contract.schema_version}</span>

        <span
          className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${
            contract.ready_for_planning
              ? "bg-sage-100 text-sage-500"
              : "bg-ember-100 text-ember-600"
          }`}
        >
          {contract.ready_for_planning
            ? "ready for the Planning Agent"
            : `waiting on ${contract.pending_slots.join(", ")}`}
        </span>

        <span className="ml-auto flex items-center gap-2 text-[11px] text-ink-400">
          <span>{contract.clarification_rounds} round(s)</span>
          <span className="h-1 w-20 overflow-hidden rounded-full bg-paper-300">
            <span
              className={`block h-full rounded-full transition-all duration-500 ${
                contract.ready_for_planning ? "bg-sage-500" : "bg-ember-500"
              }`}
              style={{ width: `${percent}%` }}
            />
          </span>
          <span className="tabular-nums">{percent}%</span>
        </span>
      </header>

      {contract.restatement && (
        <p className="border-b border-paper-200 px-4 py-3 text-[12.5px] leading-[1.55] text-ink-700">
          <span className="text-ink-400">Understood as — </span>
          {contract.restatement}
        </p>
      )}

      <div>
        {Object.entries(contract.slots).map(([name, slot]) => (
          <SlotRow key={name} name={name} slot={slot} />
        ))}
      </div>

      {plan && (plan.unmet.length > 0 || plan.notes.length > 0) && (
        <div className="grid gap-6 border-t border-paper-200 px-4 py-3.5 sm:grid-cols-2">
          {plan.layers.length > 0 && (
            <NoteList
              title="Layers selected"
              items={plan.layers.map((l) => `${l.title} — ${l.reason}`)}
              tone="unresolved"
            />
          )}
          {plan.unmet.length > 0 && (
            <NoteList
              title="Cannot be delivered"
              items={plan.unmet.map((u) => u.reason)}
              tone="assumption"
            />
          )}
          {plan.notes.length > 0 && (
            <NoteList title="How to read this" items={plan.notes} tone="unresolved" />
          )}
        </div>
      )}

      {(contract.assumptions.length > 0 || contract.unresolved.length > 0) && (
        <div className="grid gap-6 border-t border-paper-200 px-4 py-3.5 sm:grid-cols-2">
          {contract.assumptions.length > 0 && (
            <NoteList
              title="Assumed on your behalf"
              items={contract.assumptions}
              tone="assumption"
            />
          )}
          {contract.unresolved.length > 0 && (
            <NoteList title="Still unresolved" items={contract.unresolved} tone="unresolved" />
          )}
        </div>
      )}
    </div>
  );
}
