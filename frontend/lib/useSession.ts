"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { createSession, getHealth, getTaxonomy, sendMessage } from "./api";
import {
  STAGES,
  type AnalysisContract,
  type ChatMessage,
  type ClarificationPayload,
  type ExecutionPlan,
  type ExpertiseLevel,
  type Health,
  type LayerResult,
  type StageId,
  type StageStatus,
  type Taxonomy,
} from "./types";

function initialStages(): Record<StageId, StageStatus> {
  return {
    requirement_understanding: "pending",
    task_compiler: "pending",
    ambiguity_resolution: "pending",
    analysis_contract: "pending",
    planning: "pending",
    execution: "pending",
  };
}

export function useSession() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [taxonomy, setTaxonomy] = useState<Taxonomy | null>(null);

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [contract, setContract] = useState<AnalysisContract | null>(null);
  const [stages, setStages] = useState<Record<StageId, StageStatus>>(initialStages);
  const [clarification, setClarification] = useState<ClarificationPayload | null>(null);
  const [plan, setPlan] = useState<ExecutionPlan | null>(null);
  const [layers, setLayers] = useState<LayerResult[]>([]);

  const [expertise, setExpertise] = useState<ExpertiseLevel | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    getHealth().then(setHealth).catch((e: Error) => setError(`Backend unreachable: ${e.message}`));
    getTaxonomy().then(setTaxonomy).catch(() => undefined);
  }, []);

  const reset = useCallback(async () => {
    abortRef.current?.abort();
    setMessages([]);
    setContract(null);
    setStages(initialStages());
    setClarification(null);
    setPlan(null);
    setLayers([]);
    setError(null);
    setBusy(false);
    try {
      setSessionId(await createSession());
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    void reset();
    // Create the session once, on mount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const send = useCallback(
    async (text: string) => {
      if (!sessionId || busy || !text.trim()) return;

      setBusy(true);
      setError(null);
      setClarification(null);
      // A new question re-runs the whole pipeline, so last turn's layers are
      // stale from this moment on. Clearing now avoids the map briefly showing
      // an old answer under a new question.
      setLayers([]);
      setPlan(null);
      setMessages((prev) => [...prev, { role: "user", content: text }]);

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        for await (const { event, data } of sendMessage(
          sessionId,
          text,
          expertise,
          controller.signal,
        )) {
          if (event === "stage") {
            const node = (data as { node: StageId }).node;
            setStages((prev) => {
              const next = { ...prev };
              // Reaching a stage implies every earlier stage finished
              const idx = STAGES.indexOf(node);
              STAGES.forEach((s, i) => {
                if (i < idx && next[s] !== "done") next[s] = "done";
              });
              next[node] = node === "execution" ? "done" : "active";
              return next;
            });
          } else if (event === "contract") {
            setContract(data as AnalysisContract);
          } else if (event === "clarification") {
            const payload = data as ClarificationPayload;
            setClarification(payload);
            // The interrupt fires inside the node, before it returns, so the
            // backend never emits a stage event for ambiguity_resolution. Fill
            // it in here, otherwise the rail sits on stage 2 while the user is
            // plainly being asked a stage-3 question.
            setStages((prev) => ({
              ...prev,
              requirement_understanding: "done",
              task_compiler: "done",
              ambiguity_resolution: "active",
            }));
            setMessages((prev) => [
              ...prev,
              { role: "agent", content: payload.preamble, clarification: payload },
            ]);
          } else if (event === "plan") {
            setPlan(data as ExecutionPlan);
          } else if (event === "layer") {
            setLayers((prev) => [...prev, data as LayerResult]);
          } else if (event === "summary") {
            setMessages((prev) => [
              ...prev,
              { role: "agent", content: (data as { text: string }).text },
            ]);
          } else if (event === "done") {
            const final = data as AnalysisContract;
            setContract(final);
            setStages((prev) => {
              const next = { ...prev };
              STAGES.forEach((s) => (next[s] = "done"));
              return next;
            });
            if (!final.ready_for_planning) {
              setMessages((prev) => [
                ...prev,
                {
                  role: "agent",
                  content:
                    "Some ambiguity could not be resolved, so the analysis was not run. It is recorded under `unresolved` in the contract.",
                },
              ]);
            }
          } else if (event === "error") {
            setError((data as { message: string }).message);
          }
        }
      } catch (e) {
        if ((e as Error).name !== "AbortError") setError((e as Error).message);
      } finally {
        setBusy(false);
      }
    },
    [sessionId, busy, expertise],
  );

  return {
    health,
    taxonomy,
    messages,
    contract,
    stages,
    clarification,
    plan,
    layers,
    expertise,
    setExpertise,
    busy,
    error,
    send,
    reset,
  };
}
