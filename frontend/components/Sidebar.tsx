"use client";

import { useMemo, useState } from "react";

import type { Health, SessionSummary } from "@/lib/types";

function Icon({ name }: { name: "new" | "search" | "history" | "collapse" | "edit" | "trash" | "more" }) {
  const paths = {
    new: "M8 3v10M3 8h10",
    search: "m10.8 10.8 3 3M12 7.5A4.5 4.5 0 1 1 3 7.5a4.5 4.5 0 0 1 9 0Z",
    history: "M8 3a5 5 0 1 1-4.6 3M3 3v3h3M8 5.5V8l2 1.5",
    collapse: "m10 3-5 5 5 5",
    edit: "m3 11 1.5-3.8L10.8 1l2.2 2.2-6.2 6.3L3 11Z",
    trash: "M3.5 4.5h9M6 4.5V3h4v1.5M5 6.5l.5 6h5l.5-6",
    more: "M4 8h.01M8 8h.01M12 8h.01",
  } as const;
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden>
      <path d={paths[name]} stroke="currentColor" strokeWidth="1.35" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function groupLabel(value: string) {
  const date = new Date(value);
  const now = new Date();
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const age = start - new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
  if (age < 86_400_000) return "Today";
  if (age < 7 * 86_400_000) return "Previous 7 days";
  return "Earlier";
}

export function Sidebar({
  sessions,
  activeId,
  loading,
  health,
  collapsed,
  onToggle,
  onReset,
  onOpen,
  onRename,
  onDelete,
  onClearAll,
}: {
  sessions: SessionSummary[];
  activeId: string | null;
  loading: boolean;
  health: Health | null;
  collapsed: boolean;
  onToggle: () => void;
  onReset: () => void;
  onOpen: (id: string) => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
  onClearAll: () => void;
}) {
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const grouped = useMemo(() => {
    const groups = new Map<string, SessionSummary[]>();
    sessions
      .filter((session) => `${session.title} ${session.preview}`.toLowerCase().includes(query.toLowerCase()))
      .forEach((session) => {
        const label = groupLabel(session.updated_at);
        groups.set(label, [...(groups.get(label) ?? []), session]);
      });
    return groups;
  }, [query, sessions]);

  return (
    <aside
      className={`flex h-full shrink-0 flex-col border-r border-paper-300 bg-paper-50 transition-[width] duration-200 motion-reduce:transition-none ${collapsed ? "w-12" : "w-44"}`}
      aria-label="Conversation history"
    >
      <header className={`flex h-12 items-center border-b border-paper-300 ${collapsed ? "justify-center" : "justify-between pl-3 pr-0.5"}`}>
        {!collapsed && (
          <h1 className="min-w-0 truncate text-[13px] font-semibold tracking-tight text-ink-900">FireScope: WildFire Analyst</h1>
        )}
        <button
          type="button"
          onClick={onToggle}
          className={`flex h-11 w-11 cursor-pointer items-center justify-center rounded-lg text-ink-400 transition hover:bg-paper-200 hover:text-ink-900 focus-visible:outline-2 focus-visible:outline-ember-500 ${collapsed ? "rotate-180" : ""}`}
          aria-label={collapsed ? "Expand conversation history" : "Collapse conversation history"}
        >
          <Icon name="collapse" />
        </button>
      </header>

      <div className={`border-b border-paper-300 ${collapsed ? "p-0.5" : "px-1.5 py-1"}`}>
        <button
          type="button"
          onClick={onReset}
          className={`flex min-h-11 w-full cursor-pointer items-center rounded-lg text-[11.5px] font-medium text-ink-900 transition hover:bg-paper-200 focus-visible:outline-2 focus-visible:outline-ember-500 ${collapsed ? "justify-center" : "gap-2 px-2"}`}
          title="New Analysis"
        >
          <Icon name="new" />
          {!collapsed && "New Analysis"}
        </button>
        <button
          type="button"
          onClick={onClearAll}
          disabled={sessions.length === 0}
          className={`flex min-h-11 w-full cursor-pointer items-center rounded-lg text-[11.5px] font-medium text-ink-500 transition hover:bg-paper-200 hover:text-ink-900 focus-visible:outline-2 focus-visible:outline-ember-500 disabled:cursor-not-allowed disabled:text-ink-300 disabled:hover:bg-transparent ${collapsed ? "justify-center" : "gap-2 px-2"}`}
          title={sessions.length ? "Clear all conversations" : "No conversations to clear"}
        >
          <Icon name="trash" />
          {!collapsed && "Clear conversations"}
        </button>
        {!collapsed && (
          <label className="flex min-h-11 items-center gap-2 rounded-lg px-2 text-ink-400 transition hover:bg-paper-100 focus-within:bg-white focus-within:ring-2 focus-within:ring-ember-200">
            <Icon name="search" />
            <span className="sr-only">Search conversations</span>
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search analyses"
              className="min-w-0 flex-1 bg-transparent text-[12px] text-ink-900 outline-none placeholder:text-ink-400"
            />
          </label>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto py-1.5">
        {collapsed ? (
          <div className="flex flex-col items-center gap-1 px-0.5">
            {sessions.slice(0, 8).map((session) => (
              <button
                key={session.id}
                type="button"
                onClick={() => onOpen(session.id)}
                title={session.title}
                aria-label={`Open ${session.title}`}
                className={`flex h-11 w-11 cursor-pointer items-center justify-center rounded-lg transition focus-visible:outline-2 focus-visible:outline-ember-500 ${session.id === activeId ? "bg-ember-100 text-ember-600" : "text-ink-400 hover:bg-paper-200 hover:text-ink-900"}`}
              >
                <Icon name="history" />
              </button>
            ))}
          </div>
        ) : loading ? (
          <p className="px-4 py-3 text-[11px] text-ink-400">Loading history…</p>
        ) : sessions.length === 0 ? (
          <p className="px-4 py-3 text-[11px] leading-5 text-ink-400">Your analyses will appear here after the first question.</p>
        ) : (
          [...grouped.entries()].map(([label, items]) => (
            <section key={label} className="mb-1.5">
              <h2 className="px-2.5 pb-0.5 text-[8.5px] font-semibold uppercase tracking-[0.1em] text-ink-400">{label}</h2>
              <ul className="px-1">
                {items.map((session) => (
                  <li key={session.id} className="group relative">
                    {editing === session.id ? (
                      <input
                        autoFocus
                        value={draft}
                        onChange={(event) => setDraft(event.target.value)}
                        onBlur={() => {
                          if (draft.trim()) onRename(session.id, draft.trim());
                          setEditing(null);
                        }}
                        onKeyDown={(event) => {
                          if (event.key === "Enter") event.currentTarget.blur();
                          if (event.key === "Escape") setEditing(null);
                        }}
                        className="h-11 w-full rounded-lg border border-ember-300 bg-white px-2.5 text-[11.5px] outline-none ring-2 ring-ember-100 lg:h-9"
                      />
                    ) : (
                      <button
                        type="button"
                        onClick={() => onOpen(session.id)}
                        className={`min-h-11 w-full cursor-pointer rounded-lg py-1 pl-2.5 pr-11 text-left transition-colors focus-visible:outline-2 focus-visible:outline-ember-500 lg:min-h-9 lg:py-0.5 lg:pr-9 ${session.id === activeId ? "bg-ember-100/80" : "hover:bg-paper-200"}`}
                      >
                        <span className="flex min-w-0 items-center gap-2">
                          {(session.status === "failed" || session.status === "analyzing") && (
                            <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${session.status === "failed" ? "bg-danger-500" : "animate-pulse bg-ember-500"}`} aria-hidden />
                          )}
                          <span className="block min-w-0 truncate text-[11.5px] font-medium text-ink-900">{session.title}</span>
                          {(session.status === "failed" || session.status === "analyzing") && (
                            <span className="sr-only">{session.status === "failed" ? "Analysis failed" : "Analysis in progress"}</span>
                          )}
                        </span>
                      </button>
                    )}
                    {editing !== session.id && (
                      <details
                        className="group/menu absolute right-0 top-0 z-20 open:z-50"
                        onBlur={(event) => {
                          if (!event.currentTarget.contains(event.relatedTarget)) event.currentTarget.removeAttribute("open");
                        }}
                      >
                        <summary
                          className="flex h-11 w-11 cursor-pointer list-none items-center justify-center rounded-lg text-ink-400 opacity-40 transition hover:bg-paper-100 hover:text-ink-900 hover:opacity-100 focus-visible:outline-2 focus-visible:outline-ember-500 group-hover:opacity-100 lg:h-9 lg:w-9 [&::-webkit-details-marker]:hidden"
                          aria-label={`More actions for ${session.title}`}
                        >
                          <Icon name="more" />
                        </summary>
                        <div className="absolute right-0 top-10 z-30 w-28 overflow-hidden rounded-lg border border-paper-300 bg-white p-0.5 shadow-[0_8px_20px_rgba(31,30,29,0.12)] lg:top-8">
                          <button
                            type="button"
                            onClick={(event) => {
                              event.currentTarget.closest("details")?.removeAttribute("open");
                              setEditing(session.id);
                              setDraft(session.title);
                            }}
                            className="flex min-h-11 w-full cursor-pointer items-center gap-2 rounded-md px-2 text-[10.5px] text-ink-700 transition hover:bg-paper-100 focus-visible:outline-2 focus-visible:outline-ember-500 lg:min-h-8"
                          ><Icon name="edit" />Rename</button>
                          <button
                            type="button"
                            onClick={(event) => {
                              event.currentTarget.closest("details")?.removeAttribute("open");
                              if (window.confirm(`Delete “${session.title}”?`)) onDelete(session.id);
                            }}
                            className="flex min-h-11 w-full cursor-pointer items-center gap-2 rounded-md px-2 text-[10.5px] text-danger-600 transition hover:bg-ember-50 focus-visible:outline-2 focus-visible:outline-danger-500 lg:min-h-8"
                          ><Icon name="trash" />Delete</button>
                        </div>
                      </details>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          ))
        )}
      </div>

      <footer className="border-t border-paper-300 px-1 py-0.5">
        <div className={`flex min-h-11 items-center ${collapsed ? "justify-center" : "gap-2 px-2"}`} title={health?.llm ?? "Checking backend"}>
          <span className={`h-2 w-2 shrink-0 rounded-full ${health?.ok ? "bg-sage-500" : "bg-ember-500"}`} />
          {!collapsed && <span className="truncate text-[10px] text-ink-400">{health?.mock ? "Mock analysis mode" : health?.llm ?? "Connecting…"}</span>}
        </div>
      </footer>
    </aside>
  );
}
