"use client";

import { useEffect, useRef, useState } from "react";

import { ExpertisePicker } from "./ExpertisePicker";
import type { ChatMessage, ClarificationQuestion, ExpertiseLevel } from "@/lib/types";

// Each starter is backed by data this demo actually holds. The follow-up line
// tells the user how to test context continuity after the first answer.
const SAMPLE_QUESTIONS = [
  {
    prompt: "Show the lifecycle of the Bobcat Fire on 2020-09-18.",
    followUp: "Then ask: Which cities were closest to this fire?",
  },
  {
    prompt: "Compare NDVI inside the Bobcat Fire's mapped burned area from the first to the last local record.",
    followUp: "Then ask: Show only the difference and explain the red areas.",
  },
  {
    prompt: "Show current weather and fire-related conditions in Santa Barbara, California.",
    followUp: "Then ask: Is there a fire nearby?",
  },
];

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
      className="group block w-full rounded-xl border border-paper-300 bg-white px-3 py-2.5 text-left transition hover:border-ember-300 hover:bg-ember-50"
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
}: {
  messages: ChatMessage[];
  busy: boolean;
  error: string | null;
  expertise: ExpertiseLevel | null;
  inferredExpertise: ExpertiseLevel | null;
  onExpertiseChange: (level: ExpertiseLevel | null) => void;
  onSend: (text: string) => void;
}) {
  const [draft, setDraft] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

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
    <section className="flex h-full w-[26rem] shrink-0 flex-col border-l border-paper-300 bg-paper-50">
      <header className="flex items-baseline justify-between px-5 py-4">
        <h2 className="text-[13px] font-semibold tracking-tight text-ink-900">Conversation</h2>
        <p className="text-[11px] text-ink-400">asks only what changes the answer</p>
      </header>

      <div className="flex-1 space-y-4 overflow-y-auto px-5 pb-4">
        {messages.length === 0 && (
          <div className="space-y-2">
            <p className="text-[12px] text-ink-500">Try one of the walkthrough scenarios:</p>
            {SAMPLE_QUESTIONS.map((question) => (
              <button
                key={question.prompt}
                onClick={() => onSend(question.prompt)}
                disabled={busy}
                className="block w-full rounded-xl border border-paper-300 bg-white px-3.5 py-3 text-left text-[13px] leading-[1.45] text-ink-700 transition hover:border-ember-300 hover:bg-ember-50 hover:text-ink-900 disabled:opacity-50"
              >
                <span className="block">{question.prompt}</span>
                <span className="mt-1 block text-[10.5px] text-ink-400">
                  {question.followUp}
                </span>
              </button>
            ))}
          </div>
        )}

        {messages.map((msg, i) =>
          msg.role === "user" ? (
            <div key={i} className="animate-rise flex justify-end">
              <p className="max-w-[85%] rounded-2xl rounded-br-md bg-ember-500 px-3.5 py-2 text-[13px] leading-[1.55] text-white">
                {msg.content}
              </p>
            </div>
          ) : (
            <div key={i} className="animate-rise max-w-[95%]">
              <p className="text-[13px] leading-[1.6] text-ink-700">{msg.content}</p>
              {msg.clarification?.questions.map((q) => (
                <QuestionBlock key={q.slot} question={q} onPick={appendOption} />
              ))}
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

      <div className="border-t border-paper-300 bg-paper-50 p-3">
        <div className="rounded-2xl border border-paper-300 bg-white p-2 transition focus-within:border-ember-300 focus-within:ring-2 focus-within:ring-ember-100">
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
            className="block max-h-40 w-full resize-none bg-transparent px-2 pb-1.5 pt-1 text-[13px] leading-[1.55] text-ink-900 outline-none placeholder:text-ink-400"
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
              className="flex h-7 w-7 items-center justify-center rounded-lg bg-ember-500 text-white transition hover:bg-ember-600 disabled:bg-paper-300 disabled:text-ink-400"
            >
              <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden>
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
