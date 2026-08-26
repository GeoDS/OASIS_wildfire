"use client";

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";

import { AnalysisChartPanel } from "@/components/AnalysisChartPanel";
import { ChatPanel } from "@/components/ChatPanel";
import { PipelineStepper } from "@/components/PipelineStepper";
import { ResultDetailsPanel, type ResultDetailsTab } from "@/components/ResultDetailsPanel";
import { Sidebar } from "@/components/Sidebar";
import { useSession } from "@/lib/useSession";

const MapView = dynamic(() => import("@/components/MapView").then((module) => module.MapView), {
  ssr: false,
  loading: () => <div className="h-full w-full animate-pulse bg-paper-200" aria-label="Loading map" />,
});

function ToolbarIcon({ type }: { type: "history" | "layers" | "chat" }) {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden>
      {type === "history" ? (
        <path d="M8 3a5 5 0 1 1-4.6 3M3 3v3h3M8 5.5V8l2 1.5" stroke="currentColor" strokeWidth="1.35" strokeLinecap="round" strokeLinejoin="round" />
      ) : type === "layers" ? (
        <path d="m2.5 5 5.5-3 5.5 3L8 8 2.5 5Zm0 3L8 11l5.5-3M2.5 11 8 14l5.5-3" stroke="currentColor" strokeWidth="1.35" strokeLinecap="round" strokeLinejoin="round" />
      ) : (
        <path d="M3 3.5h10v7H7l-3 2v-2H3v-7Z" stroke="currentColor" strokeWidth="1.35" strokeLinecap="round" strokeLinejoin="round" />
      )}
    </svg>
  );
}

export default function Page() {
  const session = useSession();
  const spatial = session.contract?.slots?.location;
  const resolved = spatial?.resolved ?? null;
  const [historyCollapsed, setHistoryCollapsed] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [chatOpen, setChatOpen] = useState(false);
  const [detailsCollapsed, setDetailsCollapsed] = useState(false);
  const [detailsHeight, setDetailsHeight] = useState(208);
  const [detailsResizing, setDetailsResizing] = useState(false);
  const [detailsTab, setDetailsTab] = useState<ResultDetailsTab>("summary");
  const [activeLayerIds, setActiveLayerIds] = useState<string[]>([]);
  const [analysisPanelOpen, setAnalysisPanelOpen] = useState(true);

  const analysisPanelLabel = session.spatialAnalysis || session.fireLifecycle || session.fireContext
    ? "Analysis"
    : "Map Legend";

  useEffect(() => {
    if (window.matchMedia("(min-width: 1280px)").matches) setChatOpen(true);
  }, []);

  useEffect(() => {
    const available = new Set(session.layers.map((layer) => layer.capability_id));
    setActiveLayerIds((current) => {
      const next = current.filter((id) => available.has(id));
      return next.length === current.length ? current : next;
    });
  }, [session.layers]);

  const selectLayer = (id: string | null, additive = false) => {
    if (!id) {
      setActiveLayerIds([]);
      return;
    }
    setActiveLayerIds((current) => {
      if (additive) return current.includes(id) ? current.filter((item) => item !== id) : [...current, id];
      return current.length === 1 && current[0] === id ? [] : [id];
    });
  };

  const toggleHistory = () => {
    if (window.matchMedia("(max-width: 1023px)").matches) setHistoryOpen(false);
    else setHistoryCollapsed((value) => !value);
  };

  return (
    <main className="flex h-dvh w-full overflow-hidden bg-paper-100">
      {historyOpen && <button type="button" className="fixed inset-0 z-40 bg-ink-900/30 lg:hidden" onClick={() => setHistoryOpen(false)} aria-label="Close conversation history" />}
      <div className={`${historyOpen ? "fixed inset-y-0 left-0 z-50 block" : "hidden"} lg:relative lg:z-auto lg:block`}>
        <Sidebar
          sessions={session.sessions}
          activeId={session.sessionId}
          loading={session.historyLoading}
          health={session.health}
          collapsed={historyCollapsed}
          onToggle={toggleHistory}
          onReset={() => { void session.reset(); setHistoryOpen(false); }}
          onOpen={(id) => { void session.openSession(id); setHistoryOpen(false); }}
          onRename={(id, title) => void session.renameSession(id, title)}
          onDelete={(id) => void session.removeSession(id)}
        />
      </div>

      <section className="flex min-w-0 flex-1 flex-col" aria-label="Analysis workspace">
        <PipelineStepper
          stages={session.stages}
          onViewReasoning={() => {
            setDetailsTab("method");
            setDetailsCollapsed(false);
            setDetailsHeight((current) => Math.max(current, 208));
          }}
          actions={
            <>
              <button type="button" onClick={() => setHistoryOpen(true)} className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-ink-500 hover:bg-paper-200 focus-visible:outline-2 focus-visible:outline-ember-500 lg:hidden" aria-label="Open conversation history"><ToolbarIcon type="history" /></button>
              {activeLayerIds.length > 0 && <button type="button" onClick={() => selectLayer(null)} className="flex h-11 cursor-pointer items-center rounded-lg px-2.5 text-[10px] font-medium text-ember-600 hover:bg-ember-100 focus-visible:outline-2 focus-visible:outline-ember-500"><span className="hidden sm:inline">Clear {activeLayerIds.length > 1 ? `${activeLayerIds.length} layers` : "layer"}</span><span className="sm:hidden" aria-hidden>×</span></button>}
              <button type="button" onClick={() => setAnalysisPanelOpen((value) => !value)} className={`flex h-11 cursor-pointer items-center gap-2 px-2.5 text-[11px] transition hover:text-ink-900 active:opacity-60 focus-visible:outline-none focus-visible:underline focus-visible:decoration-2 focus-visible:underline-offset-4 ${analysisPanelOpen ? "font-semibold text-ember-600" : "font-medium text-ink-500"}`} aria-label={analysisPanelOpen ? `Hide ${analysisPanelLabel.toLowerCase()}` : `Open ${analysisPanelLabel.toLowerCase()}`} aria-pressed={analysisPanelOpen}><ToolbarIcon type="layers" /><span className="hidden xl:inline">{analysisPanelLabel}</span></button>
              <button type="button" onClick={() => setChatOpen((value) => !value)} className={`flex h-11 cursor-pointer items-center gap-2 px-2.5 text-[11px] transition hover:text-ink-900 active:opacity-60 focus-visible:outline-none focus-visible:underline focus-visible:decoration-2 focus-visible:underline-offset-4 ${chatOpen ? "font-semibold text-ember-600" : "font-medium text-ink-500"}`} aria-label={chatOpen ? "Hide conversation" : "Open conversation"} aria-pressed={chatOpen}><ToolbarIcon type="chat" /><span className="hidden xl:inline">Conversation</span></button>
            </>
          }
        />

        <div className="analysis-workspace min-h-0 flex-1">
          <div className={`analysis-visual-grid h-full min-h-0 ${analysisPanelOpen ? "" : "analysis-visual-grid--single"}`}>
            <section className="analysis-map-pane relative min-h-[20rem] overflow-hidden border-paper-300" aria-label="Map result">
              <MapView resolved={resolved} layers={session.layers} rasters={session.rasters} activeLayerIds={activeLayerIds} />
            </section>
            {analysisPanelOpen && (
              <section className="analysis-chart-pane min-h-[20rem] overflow-hidden border-paper-300" aria-label="Chart result">
                <AnalysisChartPanel
                  layers={session.layers}
                  lifecycle={session.fireLifecycle}
                  lifecycleLoading={session.lifecycleBusy}
                  spatialAnalysis={session.spatialAnalysis}
                  fireContext={session.fireContext}
                  analysisView={session.analysisView}
                  activeLayerIds={activeLayerIds}
                  onLayerSelect={selectLayer}
                  onDateChange={(day) => void session.selectLifecycleDay(day)}
                  onViewChange={session.selectAnalysisView}
                  onPanelClose={() => setAnalysisPanelOpen(false)}
                />
              </section>
            )}
          </div>
        </div>

        <div
          className={`shrink-0 ${detailsResizing ? "" : "transition-[height] duration-200 motion-reduce:transition-none"}`}
          style={{ height: detailsCollapsed ? 44 : detailsHeight }}
        >
          <ResultDetailsPanel
            collapsed={detailsCollapsed}
            onCollapsedChange={setDetailsCollapsed}
            height={detailsHeight}
            onHeightChange={setDetailsHeight}
            onResizingChange={setDetailsResizing}
            tab={detailsTab}
            onTabChange={setDetailsTab}
            contract={session.contract}
            plan={session.plan}
            layers={session.layers}
            rasters={session.rasters}
            fireDataStatus={session.fireDataStatus}
            lifecycle={session.fireLifecycle}
            spatialAnalysis={session.spatialAnalysis}
            fireContext={session.fireContext}
          />
        </div>
      </section>

      {chatOpen && (
        <>
          <button type="button" className="fixed inset-0 z-40 bg-ink-900/30 xl:hidden" onClick={() => setChatOpen(false)} aria-label="Close conversation" />
          <div className="fixed inset-y-0 right-0 z-50 w-[min(23rem,100vw)] border-l border-paper-300 xl:relative xl:z-auto xl:w-[20rem] xl:shrink-0">
            <ChatPanel
              messages={session.messages}
              busy={session.busy}
              error={session.error}
              expertise={session.expertise}
              inferredExpertise={session.contract?.expertise ?? null}
              onExpertiseChange={session.setExpertise}
              onSend={session.send}
              onClose={() => setChatOpen(false)}
            />
          </div>
        </>
      )}
    </main>
  );
}
