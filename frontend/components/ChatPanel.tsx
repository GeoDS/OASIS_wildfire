"use client";

import { useEffect, useRef, useState } from "react";

import { ExpertisePicker } from "./ExpertisePicker";
import { getCapabilities } from "@/lib/api";
import type {
  ChatMessage,
  ClarificationQuestion,
  ExpertiseLevel,
  SuggestionItem,
} from "@/lib/types";

// Starters come from `GET /api/capabilities` - the same declaration the agent
// answers "what can you do" from. They used to be hardcoded here, which is a
// second place for the list to be wrong: wording that no longer triggers its
// topic fails in front of the user, and nothing in the build would catch it.
const STARTER_COUNT = 3;

function SuggestionList({
  items,
  busy,
  onSend,
}: {
  items: SuggestionItem[];
  busy: boolean;
  onSend: (text: string) => void;
}) {
  return (
    <div className="mt-3 space-y-1.5">
      {items.map((item) => (
        <button
          key={item.ask}
          type="button"
          onClick={() => onSend(item.ask)}
          disabled={busy}
          className="block min-h-11 w-full rounded-lg border border-paper-300 bg-white px-3 py-2.5 text-left text-[12px] leading-[1.45] text-ink-700 transition hover:border-ember-300 hover:bg-ember-50 hover:text-ink-900 disabled:opacity-50"
        >
          <span className="block">{item.ask}</span>
          <span className="mt-1 block text-[10.5px] text-ink-400">{item.does}</span>
        </button>
      ))}
    </div>
  );
}


function OptionButton({
  label,
  implication,
  recommended,
  onPick,
}: {
  label: string;
  implication: string | null;
  recommended: boolean;
  onPick: () => void;
}) {
  return (
    <button
      onClick={onPick}
      className="group block min-h-11 w-full rounded-xl border border-paper-300 bg-white px-3 py-2.5 text-left transition hover:border-ember-300 hover:bg-ember-50"
    >
      <span className="flex items-baseline gap-2">
        <span className="text-[13px] font-medium text-ink-900">{label}</span>
        {recommended && (
          <span className="rounded-full bg-sage-100 px-1.5 py-px text-[10px] font-medium tracking-wide text-sage-500">
            suggested
          </span>
        )}
      </span>
      {implication && (
        <span className="mt-1 block text-[12px] leading-[1.45] text-ink-500">{implication}</span>
      )}
    </button>
  );
}

function QuestionBlock({
  question,
  onPick,
}: {
  question: ClarificationQuestion;
  onPick: (label: string) => void;
}) {
  return (
    <div className="mt-3 space-y-2">
      <p className="text-[13px] leading-[1.55] text-ink-900">
        {question.question}
        <span className="ml-1.5 font-mono text-[10px] text-ink-400">{question.slot}</span>
      </p>
      <div className="space-y-1.5">
        {question.options.map((opt) => (
          <OptionButton
            key={opt.label}
            label={opt.label}
            implication={opt.implication}
            recommended={opt.recommended}
            onPick={() => onPick(opt.label)}
          />
        ))}
      </div>
    </div>
  );
}

export function ChatPanel({
  messages,
  busy,
  error,
  expertise,
  inferredExpertise,
  onExpertiseChange,
  onSend,
  onClose,
}: {
  messages: ChatMessage[];
  busy: boolean;
  error: string | null;
  expertise: ExpertiseLevel | null;
  inferredExpertise: ExpertiseLevel | null;
  onExpertiseChange: (level: ExpertiseLevel | null) => void;
  onSend: (text: string) => void;
  onClose?: () => void;
}) {
  const [draft, setDraft] = useState("");
  const [starters, setStarters] = useState<SuggestionItem[]>([]);
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // An unreachable backend leaves the empty state without starters rather than
  // with stale ones: a question that cannot be sent is worse than no offer.
  useEffect(() => {
    let live = true;
    getCapabilities()
      .then((payload) => {
        if (!live) return;
        // Follow-ups read as questions but only mean something after an answer.
        setStarters(payload.topics.filter((t) => !t.follow_up).slice(0, STARTER_COUNT));
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

  // Grow the composer with its content, capped so it never eats the transcript.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [draft]);

  const submit = () => {
    const text = draft.trim();
    if (!text || busy) return;
    setDraft("");
    onSend(text);
  };

  /** Picking an option appends rather than sends, so several answers can go in
      one reply. Answering everything at once is the point - no drip-feeding. */
  const appendOption = (label: string) => {
    setDraft((prev) => (prev ? `${prev}; ${label}` : label));
    textareaRef.current?.focus();
  };

  return (
    <section className="flex h-full w-full min-w-0 flex-col bg-paper-50">
      <header className="flex min-h-12 items-center justify-between border-b border-paper-300 px-4">
        <h2 className="text-[13px] font-semibold tracking-tight text-ink-900">Conversation</h2>
        <div className="flex items-center">
          {onClose && (
            <button type="button" onClick={onClose} className="flex h-11 w-11 items-center justify-center rounded-xl text-ink-400 hover:bg-paper-200 hover:text-ink-900 focus-visible:outline-2 focus-visible:outline-ember-500" aria-label="Close conversation panel">
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden><path d="m2.5 2.5 9 9m0-9-9 9" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></svg>
            </button>
          )}
        </div>
      </header>

      <div className="flex-1 space-y-3 overflow-y-auto px-4 py-3">
        {messages.length === 0 && starters.length > 0 && (
          <div className="space-y-2">
            <p className="text-[12px] text-ink-500">Try one of these:</p>
            <SuggestionList items={starters} busy={busy} onSend={onSend} />
          </div>
        )}

        {messages.map((msg, i) =>
          msg.role === "user" ? (
            <div key={i} className="animate-rise flex justify-end">
              <p className="max-w-[85%] rounded-2xl rounded-br-md bg-ember-600 px-3.5 py-2 text-[13px] leading-[1.55] text-white">
                {msg.content}
              </p>
            </div>
          ) : (
            <div key={i} className="animate-rise max-w-[95%]">
              <p className="text-[13px] leading-[1.6] text-ink-700">{msg.content}</p>
              {msg.clarification?.questions.map((q) => (
                <QuestionBlock key={q.slot} question={q} onPick={appendOption} />
              ))}
              {msg.suggestions && msg.suggestions.length > 0 && (
                <SuggestionList items={msg.suggestions} busy={busy} onSend={onSend} />
              )}
            </div>
          ),
        )}

        {busy && (
          <div className="flex items-center gap-2 text-[12px] text-ink-400">
            <span className="flex gap-1">
              {[0, 1, 2].map((i) => (
                <span
                  key={i}
                  className="h-1 w-1 animate-bounce rounded-full bg-ember-300"
                  style={{ animationDelay: `${i * 120}ms` }}
                />
              ))}
            </span>
            thinking
          </div>
        )}

        {error && (
          <p className="rounded-xl border border-ember-300 bg-ember-50 px-3.5 py-2.5 text-[12px] leading-[1.5] text-ember-600">
            {error}
          </p>
        )}

        <div ref={bottomRef} />
      </div>

      <div className="border-t border-paper-300 bg-paper-50 p-2.5">
        <div className="rounded-xl border border-paper-300 bg-white p-1.5 transition focus-within:border-ember-300 focus-within:ring-2 focus-within:ring-ember-100">
          <textarea
            ref={textareaRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            rows={1}
            placeholder="Describe what you need…"
            aria-label="Describe the wildfire analysis you need"
            className="block max-h-40 w-full resize-none bg-transparent px-2 pb-1.5 pt-1 text-base leading-[1.55] text-ink-900 outline-none placeholder:text-ink-400 lg:text-[13px]"
          />
          <div className="flex items-center justify-between gap-2">
            <ExpertisePicker
              value={expertise}
              inferred={inferredExpertise}
              onChange={onExpertiseChange}
            />
            <button
              onClick={submit}
              disabled={busy || !draft.trim()}
              aria-label="Send"
              className="flex h-10 w-10 cursor-pointer items-center justify-center rounded-lg bg-ember-500 text-white transition hover:bg-ember-600 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ember-500 disabled:cursor-default disabled:bg-paper-300 disabled:text-ink-400 lg:h-8 lg:w-8 lg:rounded-md"
            >
              <svg width="13" height="13" viewBox="0 0 14 14" aria-hidden>
                <path
                  d="M7 11.5V2.5M7 2.5L3.2 6.3M7 2.5l3.8 3.8"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.6"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}
