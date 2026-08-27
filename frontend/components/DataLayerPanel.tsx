"use client";

import type { FireDataStatus } from "@/lib/types";

/**
 * Read-only fire-data state. Selection belongs to the backend: the user names
 * the fire and the question, while the application explains only the decision.
 */
export function DataLayerPanel({
  status,
  analyzing,
}: {
  status: FireDataStatus | null;
  analyzing: boolean;
}) {
  const weatherMode = status?.focus === "weather";
  const successful =
    status?.status === "matched" ||
    status?.status === "city_assessed" ||
    status?.status === "weather_assessed";
  return (
    <div className="flex min-h-0 flex-1 flex-col px-4 py-3">
      <section className="rounded-xl border border-paper-300 bg-white p-3">
        <div className="flex items-center gap-2">
          <span
            className={`h-2 w-2 rounded-full ${
              analyzing
                ? "animate-pulse bg-ember-400"
                : successful
                  ? "bg-sage-500"
                  : "bg-paper-400"
            }`}
          />
          <p className="text-[11.5px] font-semibold text-ink-900">
            {weatherMode
              ? "City weather"
              : status?.workflow === "city"
                ? "City fire context"
                : "Fire context"}
          </p>
        </div>
        <p className="mt-2 text-[10.5px] leading-[1.5] text-ink-500">
          {analyzing
            ? "Understanding the place and choosing matching data…"
            : status?.message ??
              "Ask about a fire or weather in a Southern California city. Layers are selected automatically."}
        </p>
        {status?.details && status.details.length > 0 && (
          <ul className="mt-2 space-y-1 border-t border-paper-200 pt-2">
            {status.details.map((detail) => (
              <li key={detail} className="text-[9.5px] leading-[1.45] text-ink-400">
                {detail}
              </li>
            ))}
          </ul>
        )}
        <p className="mt-2 border-t border-paper-200 pt-2 text-[9.5px] leading-[1.45] text-ink-400">
          {weatherMode
            ? "Weather is reported for the resolved reference point; the boundary is geographic context, not a citywide sensor surface."
            : "A nearby dataset is never substituted just because its source tile overlaps the map."}
        </p>
      </section>
    </div>
  );
}
