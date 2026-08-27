"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  createSession,
  clearSessions as clearArchivedSessions,
  deleteSession as deleteArchivedSession,
  fetchFireLifecycle,
  getHealth,
  getSession,
  getTaxonomy,
  listSessions,
  renameSession as renameArchivedSession,
  resolveApiUrl,
  saveSessionSnapshot,
  sendMessage,
} from "./api";
import {
  STAGES,
  type AnalysisContract,
  type ChatMessage,
  type ClarificationPayload,
  type ExecutionPlan,
  type ExpertiseLevel,
  type FireContext,
  type FireDataStatus,
  type FireLifecycle,
  type Health,
  type LayerResult,
  type RasterLayerResult,
  type SessionSummary,
  type SpatialAnalysis,
  type StageId,
  type StageStatus,
  type Taxonomy,
  type WorkspaceSnapshot,
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
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [health, setHealth] = useState<Health | null>(null);
  const [taxonomy, setTaxonomy] = useState<Taxonomy | null>(null);

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [contract, setContract] = useState<AnalysisContract | null>(null);
  const [stages, setStages] = useState<Record<StageId, StageStatus>>(initialStages);
  const [clarification, setClarification] = useState<ClarificationPayload | null>(null);
  const [plan, setPlan] = useState<ExecutionPlan | null>(null);
  const [layers, setLayers] = useState<LayerResult[]>([]);
  const [rasters, setRasters] = useState<RasterLayerResult[]>([]);
  const [fireDataStatus, setFireDataStatus] = useState<FireDataStatus | null>(null);
  const [fireLifecycle, setFireLifecycle] = useState<FireLifecycle | null>(null);
  const [spatialAnalysis, setSpatialAnalysis] = useState<SpatialAnalysis | null>(null);
  const [fireContext, setFireContext] = useState<FireContext | null>(null);
  const [analysisView, setAnalysisView] = useState<"before" | "after" | "difference">(
    "difference",
  );
  const [lifecycleBusy, setLifecycleBusy] = useState(false);

  const [expertise, setExpertise] = useState<ExpertiseLevel | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const readyToPersistRef = useRef(false);

  // Slots the current analysis turn has not refreshed yet. A turn replaces a
  // slot the moment its first payload arrives, so the previous answer stays
  // visible until there is something to put in its place. Whatever is still
  // listed when the turn finishes was genuinely not produced this time, and
  // only then is it cleared.
  const staleRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    getHealth().then(setHealth).catch((e: Error) => setError(`Backend unreachable: ${e.message}`));
    getTaxonomy().then(setTaxonomy).catch(() => undefined);
  }, []);

  const clearWorkspace = useCallback(() => {
    abortRef.current?.abort();
    staleRef.current.clear();
    setMessages([]);
    setContract(null);
    setStages(initialStages());
    setClarification(null);
    setPlan(null);
    setLayers([]);
    setRasters([]);
    setFireDataStatus(null);
    setFireLifecycle(null);
    setSpatialAnalysis(null);
    setFireContext(null);
    setAnalysisView("difference");
    setLifecycleBusy(false);
    setError(null);
    setBusy(false);
  }, []);

  const refreshSessions = useCallback(async () => {
    const items = await listSessions();
    setSessions(items);
    return items;
  }, []);

  const reset = useCallback(async () => {
    readyToPersistRef.current = false;
    clearWorkspace();
    try {
      setSessionId(await createSession());
      readyToPersistRef.current = true;
      await refreshSessions();
    } catch (e) {
      setError((e as Error).message);
    }
  }, [clearWorkspace, refreshSessions]);

  const openSession = useCallback(async (id: string) => {
    if (id === sessionId || busy) return;
    readyToPersistRef.current = false;
    clearWorkspace();
    setHistoryLoading(true);
    try {
      const archived = await getSession(id);
      const snapshot = archived.snapshot;
      setSessionId(id);
      setMessages(snapshot.messages ?? []);
      setContract(snapshot.contract ?? null);
      setStages(snapshot.stages ?? initialStages());
      setPlan(snapshot.plan ?? null);
      setLayers(snapshot.layers ?? []);
      setRasters(snapshot.rasters ?? []);
      setFireDataStatus(snapshot.fireDataStatus ?? null);
      setFireLifecycle(snapshot.fireLifecycle ?? null);
      setSpatialAnalysis(snapshot.spatialAnalysis ?? null);
      setFireContext(snapshot.fireContext ?? null);
      setAnalysisView(snapshot.analysisView ?? "difference");
      readyToPersistRef.current = true;
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setHistoryLoading(false);
    }
  }, [busy, clearWorkspace, sessionId]);

  const renameSession = useCallback(async (id: string, title: string) => {
    await renameArchivedSession(id, title);
    await refreshSessions();
  }, [refreshSessions]);

  const removeSession = useCallback(async (id: string) => {
    await deleteArchivedSession(id);
    const remaining = await refreshSessions();
    if (id !== sessionId) return;
    if (remaining[0]) await openSession(remaining[0].id);
    else await reset();
  }, [openSession, refreshSessions, reset, sessionId]);

  // Everything, irreversibly. The fresh session afterwards is a real new one:
  // keeping the old id would point the UI at a row that no longer exists.
  const clearAllSessions = useCallback(async () => {
    const deleted = await clearArchivedSessions();
    await reset();
    await refreshSessions();
    return deleted;
  }, [refreshSessions, reset]);

  useEffect(() => {
    let cancelled = false;
    const bootstrap = async () => {
      setHistoryLoading(true);
      try {
        const archived = await refreshSessions();
        if (cancelled) return;
        if (archived[0]) await openSession(archived[0].id);
        else await reset();
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      } finally {
        if (!cancelled) setHistoryLoading(false);
      }
    };
    void bootstrap();
    return () => {
      cancelled = true;
    };
    // Session bootstrap is intentionally a one-time operation. The callbacks
    // above change identity while a turn is running; subscribing to them here
    // would reopen the latest archived result every time `busy` changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const send = useCallback(
    async (text: string) => {
      if (!sessionId || busy || !text.trim()) return;

      setBusy(true);
      setError(null);
      setClarification(null);
      // Clearing is deferred to the `turn` event. Not every turn replaces the
      // map: asking what a result means keeps that result on screen, and
      // wiping it here would delete the very thing the question is about.
      setMessages((prev) => [...prev, { role: "user", content: text }]);

      const controller = new AbortController();
      abortRef.current = controller;

      // One slot per piece of the answer, each with the way to empty it. A new
      // kind of result is an entry here rather than an edit in three places:
      // the list marked stale on `turn`, the branch that claims it, and the
      // sweep at `done` all read from this.
      const slots: Record<string, () => void> = {
        layers: () => setLayers([]),
        rasters: () => setRasters([]),
        fireDataStatus: () => setFireDataStatus(null),
        fireLifecycle: () => setFireLifecycle(null),
        spatialAnalysis: () => {
          setSpatialAnalysis(null);
          setAnalysisView("difference");
        },
        fireContext: () => setFireContext(null),
        plan: () => setPlan(null),
      };

      // Returns true the first time this turn touches `slot`, which is the
      // moment its previous contents stop being the answer on screen.
      const claim = (slot: string) => {
        const first = staleRef.current.has(slot);
        staleRef.current.delete(slot);
        return first;
      };

      try {
        for await (const { event, data } of sendMessage(
          sessionId,
          text,
          expertise,
          controller.signal,
        )) {
          if (event === "turn") {
            // An analysis turn re-runs the pipeline, but most of what it draws
            // is usually what is already on screen: asking which cities a fire
            // reached re-sends that fire's whole lifecycle unchanged. Marking
            // the slots stale rather than emptying them keeps the map steady -
            // each is replaced as its own payload lands.
            if ((data as { kind: string }).kind === "analysis") {
              staleRef.current = new Set(Object.keys(slots));
              setStages(initialStages());
            } else {
              staleRef.current = new Set();
            }
          } else if (event === "stage") {
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
            claim("plan");
            setPlan(data as ExecutionPlan);
          } else if (event === "layer") {
            // The first layer of a turn replaces last turn's set; the rest add to it.
            const first = claim("layers");
            setLayers((prev) => (first ? [data as LayerResult] : [...prev, data as LayerResult]));
          } else if (event === "fire_data") {
            claim("fireDataStatus");
            setFireDataStatus(data as FireDataStatus);
          } else if (event === "raster") {
            const rawRaster = data as RasterLayerResult;
            const raster = {
              ...rawRaster,
              image_url: resolveApiUrl(rawRaster.image_url),
            };
            const firstRaster = claim("rasters");
            setRasters((prev) =>
              firstRaster
                ? [raster]
                : [...prev.filter((item) => item.event_name !== raster.event_name), raster],
            );
          } else if (event === "fire_lifecycle") {
            const raw = data as FireLifecycle;
            const lifecycle = {
              ...raw,
              layers: raw.layers.map((layer) => ({
                ...layer,
                image_url: resolveApiUrl(layer.image_url),
              })),
            };
            claim("fireLifecycle");
            claim("rasters");
            setFireLifecycle(lifecycle);
            setRasters(lifecycle.layers);
          } else if (event === "fire_context") {
            claim("fireContext");
            setFireContext(data as FireContext);
          } else if (event === "spatial_analysis") {
            const raw = data as SpatialAnalysis;
            const analysis = {
              ...raw,
              layers: raw.layers.map((layer) => ({
                ...layer,
                image_url: resolveApiUrl(layer.image_url),
              })),
            };
            claim("spatialAnalysis");
            claim("rasters");
            setSpatialAnalysis(analysis);
            setAnalysisView(analysis.default_view);
            setRasters(
              analysis.layers.filter(
                (layer) => layer.analysis_view === analysis.default_view,
              ),
            );
          } else if (event === "summary") {
            setMessages((prev) => [
              ...prev,
              { role: "agent", content: (data as { text: string }).text },
            ]);
          } else if (event === "done") {
            // Slots still marked stale were not produced by this turn - a
            // weather-only answer draws no fire layers - so now they go.
            staleRef.current.forEach((slot) => slots[slot]?.());
            staleRef.current.clear();

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

  const selectLifecycleDay = useCallback(
    async (day: string) => {
      if (!fireLifecycle || lifecycleBusy || day === fireLifecycle.selected_date) return;
      setLifecycleBusy(true);
      setError(null);
      try {
        const lifecycle = await fetchFireLifecycle(fireLifecycle.event_id, day);
        setFireLifecycle(lifecycle);
        setRasters(lifecycle.layers);
        setFireDataStatus((previous) =>
          previous
            ? {
                ...previous,
                message:
                  `${lifecycle.event_name}: TS-SatFire historical record for ${day}. ` +
                  `The selected day contains ${lifecycle.selected_metrics.active_pixels.toLocaleString()} ` +
                  `AF label pixels; cumulative mapped BA is approximately ` +
                  `${lifecycle.selected_metrics.cumulative_burned_km2.toLocaleString()} km².`,
              }
            : previous,
        );
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setLifecycleBusy(false);
      }
    },
    [fireLifecycle, lifecycleBusy],
  );

  const selectAnalysisView = useCallback(
    (view: "before" | "after" | "difference") => {
      if (!spatialAnalysis) return;
      setAnalysisView(view);
      setRasters(
        spatialAnalysis.layers.filter((layer) => layer.analysis_view === view),
      );
    },
    [spatialAnalysis],
  );

  useEffect(() => {
    if (!sessionId || !readyToPersistRef.current || historyLoading) return;
    const snapshot: WorkspaceSnapshot = {
      status: error ? "failed" : busy ? "analyzing" : messages.length ? "complete" : "idle",
      messages,
      contract,
      stages,
      plan,
      layers,
      rasters,
      fireDataStatus,
      fireLifecycle,
      spatialAnalysis,
      fireContext,
      analysisView,
    };
    const timer = window.setTimeout(() => {
      void saveSessionSnapshot(sessionId, snapshot)
        .then(refreshSessions)
        .catch(() => undefined);
    }, 500);
    return () => window.clearTimeout(timer);
  }, [
    analysisView,
    busy,
    contract,
    error,
    fireContext,
    fireDataStatus,
    fireLifecycle,
    historyLoading,
    layers,
    messages,
    plan,
    rasters,
    refreshSessions,
    sessionId,
    spatialAnalysis,
    stages,
  ]);

  return {
    sessionId,
    sessions,
    historyLoading,
    openSession,
    renameSession,
    clearAllSessions,
    removeSession,
    health,
    taxonomy,
    messages,
    contract,
    stages,
    clarification,
    plan,
    layers,
    rasters,
    fireDataStatus,
    fireLifecycle,
    spatialAnalysis,
    fireContext,
    analysisView,
    selectAnalysisView,
    lifecycleBusy,
    selectLifecycleDay,
    expertise,
    setExpertise,
    busy,
    error,
    send,
    reset,
  };
}
