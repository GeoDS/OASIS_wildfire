"use client";

import dynamic from "next/dynamic";

import { ChatPanel } from "@/components/ChatPanel";
import { ContractCard } from "@/components/ContractCard";
import { FireLifecyclePanel } from "@/components/FireLifecyclePanel";
import { PipelineStepper } from "@/components/PipelineStepper";
import { Sidebar } from "@/components/Sidebar";
import { FireContextPanel } from "@/components/FireContextPanel";
import { SpatialAnalysisPanel } from "@/components/SpatialAnalysisPanel";
import { useSession } from "@/lib/useSession";

// MapLibre needs `window`, so server rendering has to be off.
const MapView = dynamic(() => import("@/components/MapView").then((m) => m.MapView), {
  ssr: false,
  loading: () => <div className="h-full w-full bg-paper-200" />,
});

export default function Page() {
  const s = useSession();
  const spatial = s.contract?.slots?.location;
  const resolved = spatial?.resolved ?? null;

  return (
    <main className="flex h-dvh w-screen overflow-hidden bg-paper-100">
      <Sidebar
        health={s.health}
        fireDataStatus={s.fireDataStatus}
        analyzing={s.busy}
        onReset={() => void s.reset()}
      />

      {/* Centre column: the pipeline reads across the top, the map runs through
          every stage, and the contract assembles below it. */}
      <section className="flex min-w-0 flex-1 flex-col">
        <PipelineStepper stages={s.stages} />
        <div className="relative min-h-0 flex-1">
          <MapView
            resolved={resolved}
            layers={s.layers}
            rasters={s.rasters}
          />
          {s.spatialAnalysis ? (
            <SpatialAnalysisPanel
              analysis={s.spatialAnalysis}
              view={s.analysisView}
              onViewChange={s.selectAnalysisView}
            />
          ) : s.fireContext ? (
            <FireContextPanel context={s.fireContext} />
          ) : s.fireLifecycle ? (
            <FireLifecyclePanel
              lifecycle={s.fireLifecycle}
              loading={s.lifecycleBusy}
              onDateChange={s.selectLifecycleDay}
            />
          ) : null}
        </div>
        <div
          className={`${
            s.fireLifecycle || s.spatialAnalysis || s.fireContext
              ? "h-[30%] min-h-[11rem]"
              : "h-[40%] min-h-[14rem]"
          } border-t border-paper-300 bg-paper-50`}
        >
          <ContractCard contract={s.contract} plan={s.plan} />
        </div>
      </section>

      <ChatPanel
        messages={s.messages}
        busy={s.busy}
        error={s.error}
        expertise={s.expertise}
        inferredExpertise={s.contract?.expertise ?? null}
        onExpertiseChange={s.setExpertise}
        onSend={s.send}
      />
    </main>
  );
}
