"use client";

import { useEffect, useState } from "react";

import type { FireLifecycle } from "@/lib/types";

/**
 * Steps through the record one day at a time. It waits for each day's layers to
 * arrive before moving on, so a slow fetch never skips a day; playing from the
 * last day starts again at the first.
 */
export function LifecyclePlayButton({
  lifecycle,
  loading,
  onDateChange,
}: {
  lifecycle: FireLifecycle;
  loading: boolean;
  onDateChange: (day: string) => void;
}) {
  const [playing, setPlaying] = useState(false);
  const index = Math.max(0, lifecycle.dates.indexOf(lifecycle.selected_date));
  const last = lifecycle.dates.length - 1;

  useEffect(() => {
    if (!playing || loading) return;
    if (index >= last) {
      setPlaying(false);
      return;
    }
    const timer = window.setTimeout(() => onDateChange(lifecycle.dates[index + 1]), 900);
    return () => window.clearTimeout(timer);
  }, [index, last, lifecycle.dates, loading, onDateChange, playing]);

  const toggle = () => {
    if (playing) {
      setPlaying(false);
      return;
    }
    if (index >= last) onDateChange(lifecycle.dates[0]);
    setPlaying(true);
  };

  return (
    <button
      type="button"
      onClick={toggle}
      aria-pressed={playing}
      aria-label={playing ? "Pause the day-by-day playback" : "Play the fire day by day"}
      className={`flex h-8 shrink-0 cursor-pointer items-center gap-1.5 rounded-full px-3 text-[11px] font-semibold transition focus-visible:outline-2 focus-visible:outline-ember-500 ${playing ? "bg-ember-600 text-white" : "bg-ember-500 text-white hover:bg-ember-600"}`}
    >
      <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden>
        {playing ? (
          <path d="M2 1.5h2v7H2zM6 1.5h2v7H6z" fill="currentColor" />
        ) : (
          <path d="M2.5 1.2v7.6L8.8 5 2.5 1.2Z" fill="currentColor" />
        )}
      </svg>
      {playing ? "Pause" : "Play"}
    </button>
  );
}
