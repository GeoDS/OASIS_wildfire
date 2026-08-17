"use client";

import dynamic from "next/dynamic";

import { ChatPanel } from "@/components/ChatPanel";
import { ContractCard } from "@/components/ContractCard";
import { PipelineStepper } from "@/components/PipelineStepper";
import { Sidebar } from "@/components/Sidebar";
import { useSession } from "@/lib/useSession";

// MapLibre needs `window`, so server rendering has to be off.
const MapView = dynamic(() => import("@/components/MapView").then((m) => m.MapView), {
  ssr: false,
  loading: () => <div className="h-full w-full bg-paper-200" />,
});

export default function Page() {
  const s = useSession();
  const spatial = s.contract?.slots?.location;

  return (
    <main className="flex h-dvh w-screen overflow-hidden bg-paper-100">
      <Sidebar health={s.health} area={s.taxonomy?.showcase_area ?? null} onReset={s.reset} />

      {/* Centre column: the pipeline reads across the top, the map runs through
          every stage, and the contract assembles below it. */}
      <section className="flex min-w-0 flex-1 flex-col">
        <PipelineStepper stages={s.stages} />
        <div className="min-h-0 flex-1">
          <MapView resolved={spatial?.resolved ?? null} layers={s.layers} />
        </div>
        <div className="h-[40%] min-h-[14rem] border-t border-paper-300 bg-paper-50">
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
