'use client';

import { useState, useCallback } from 'react';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { SystemDiagram, CLIENTS } from '@/components/architecture/system-diagram';
import { FlowSimulator } from '@/components/architecture/flow-simulator';
import { DataIsolationView } from '@/components/architecture/data-isolation-view';
import { IntegrationPattern } from '@/components/architecture/integration-pattern';

export default function ArchitecturePage() {
  const [selectedClient, setSelectedClient] = useState<string | null>(null);
  const [activeLayer, setActiveLayer] = useState<string | null>(null);

  const handleClientSelect = useCallback((id: string) => {
    setSelectedClient((prev) => (prev === id ? null : id));
    setActiveLayer(null);
  }, []);

  const handleLayerChange = useCallback((layer: string | null) => {
    setActiveLayer(layer);
  }, []);

  const selectedClientInfo = CLIENTS.find((c) => c.id === selectedClient);

  return (
    <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6">
      {/* Hero */}
      <div className="mb-8 text-center">
        <h2 className="text-[var(--font-size-2xl)] font-bold text-[var(--color-neutral-900)]">
          AI Platform 시스템 아키텍처
        </h2>
        <p className="mt-2 text-[var(--font-size-sm)] text-[var(--color-neutral-500)]">
          클라이언트를 클릭하면 요청이 시스템을 통해 어떻게 처리되는지 시뮬레이션합니다
        </p>
      </div>

      {/* Interactive System Diagram */}
      <section className="mb-8 rounded-[var(--radius-xl)] border border-[var(--color-neutral-200)] bg-[var(--color-neutral-50)]">
        <SystemDiagram
          selectedClient={selectedClient}
          activeLayer={activeLayer}
          onClientSelect={handleClientSelect}
        />
      </section>

      {/* Flow Simulator (appears when client selected) */}
      {selectedClientInfo && (
        <section className="mb-8 animate-fade-slide-up">
          <FlowSimulator
            client={selectedClientInfo}
            onLayerChange={handleLayerChange}
          />
        </section>
      )}

      {/* Tabbed detail sections */}
      <Tabs defaultValue="isolation" className="mb-12">
        <TabsList className="mb-4">
          <TabsTrigger value="isolation">데이터 격리 & 식별</TabsTrigger>
          <TabsTrigger value="integration">설계 원칙 & 연동</TabsTrigger>
        </TabsList>

        <TabsContent value="isolation">
          <DataIsolationView />
        </TabsContent>

        <TabsContent value="integration">
          <IntegrationPattern />
        </TabsContent>
      </Tabs>
    </div>
  );
}
