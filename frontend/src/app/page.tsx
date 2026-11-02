'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { Sidebar } from '../components/Sidebar';
import { Header } from '../components/Header';
import { LedgerDashboard } from '../components/LedgerDashboard';
import { LedgerTable } from '../components/LedgerTable';
import { VoiceAssistant } from '../components/VoiceAssistant';
import { TransactionModal } from '../components/TransactionModal';
import { useSpeechRecognition } from '../hooks/useSpeechRecognition';
import { api } from '../lib/api';
import { Transaction, DailySummary } from '../types';

export default function HisabAiApp() {
  const [currentTab, setCurrentTab] = useState('dashboard');
  const [transactions, setTransactions] = useState<Transaction[]>([]);
  const [summary, setSummary] = useState<DailySummary>({
    total_udhaar: 0,
    total_jama: 0,
    net_balance: 0,
    active_customers_count: 0,
    transactions_count: 0,
  });
  const [isSyncing, setIsSyncing] = useState(false);
  const [isModalOpen, setIsModalOpen] = useState(false);

  // Fetch transactions and summaries
  const refreshData = useCallback(async () => {
    try {
      setIsSyncing(true);
      const [txData, sumData] = await Promise.all([
        api.getTransactions(50, 0),
        api.getDailySummary(),
      ]);

      setTransactions(txData);

      // Compute summary metrics if backend returned zeros or raw
      let uTotal = 0;
      let jTotal = 0;
      const custSet = new Set<string>();

      txData.forEach((t) => {
        if (t.type === 'UDHAAR') uTotal += Number(t.amount || 0);
        else jTotal += Number(t.amount || 0);
        if (t.customer_name) custSet.add(t.customer_name.toLowerCase());
      });

      setSummary({
        total_udhaar: sumData.total_udhaar || uTotal,
        total_jama: sumData.total_jama || jTotal,
        net_balance: (sumData.total_udhaar || uTotal) - (sumData.total_jama || jTotal),
        active_customers_count: sumData.active_customers_count || custSet.size,
        transactions_count: txData.length,
      });
    } catch (err) {
      console.warn('[Hisab AI] Data refresh failed:', err);
    } finally {
      setIsSyncing(false);
    }
  }, []);

  useEffect(() => {
    refreshData();
  }, [refreshData]);

  // Voice Assistant speech hook
  const {
    isListening,
    isProcessing,
    transcript,
    statusMessage,
    activePendingOperation,
    startListening,
    stopListening,
    confirmPendingOperation,
    cancelPendingOperation,
  } = useSpeechRecognition({
    onSuccess: (res) => {
      if (res.action_taken === 'committed_transaction') {
        refreshData();
      }
    },
    onError: (err) => {
      console.error('[Voice Error]:', err);
    },
  });

  const handleToggleMic = () => {
    if (isListening) stopListening();
    else startListening();
  };

  const handleManualTransactionSubmit = async (data: Partial<Transaction>) => {
    await api.createTransaction(data);
    await refreshData();
  };

  return (
    <div className="min-h-screen flex bg-[#131315] text-[#F8FAFC]">
      {/* Sidebar Navigation */}
      <div className="hidden lg:block w-64 flex-shrink-0">
        <Sidebar
          currentTab={currentTab}
          onTabChange={setCurrentTab}
          shopName="Mohit Kirana Store"
          userName="Mohit Goyal"
        />
      </div>

      {/* Main App Content View */}
      <div className="flex-1 flex flex-col min-w-0">
        <Header
          pageTitle={
            currentTab === 'dashboard'
              ? 'Vyapar Dashboard'
              : currentTab === 'ledger'
              ? 'Khata Ledger Book'
              : currentTab.toUpperCase()
          }
          onSync={refreshData}
          isSyncing={isSyncing}
        />

        <main className="flex-1 p-4 md:p-8 max-w-7xl w-full mx-auto pb-32">
          {/* Top Quick Voice Prompt Banner */}
          <div className="mb-6 p-4 rounded-2xl bg-gradient-to-r from-amber-950/40 via-amber-900/20 to-black/30 border border-amber-500/20 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 shadow-lg">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-xl bg-amber-500/10 flex items-center justify-center text-xl text-amber-400">
                🎙️
              </div>
              <div>
                <div className="text-sm font-bold text-white">
                  &quot;Suno Hisab&quot; Voice Assistant Active
                </div>
                <div className="text-xs text-amber-200/70">
                  Say &quot;Ramesh ko 500 udhaar likho&quot; or tap the bottom mic button to record
                  hands-free.
                </div>
              </div>
            </div>
            <button
              onClick={handleToggleMic}
              className={`px-4 py-2 rounded-xl text-xs font-bold transition flex items-center gap-2 ${
                isListening
                  ? 'bg-rose-600 text-white animate-pulse'
                  : 'bg-white/10 hover:bg-white/20 text-white'
              }`}
            >
              <span>{isListening ? 'Stop Recording' : 'Start Voice Mode'}</span>
            </button>
          </div>

          {/* Metric Highlights */}
          <LedgerDashboard summary={summary} />

          {/* Ledger Table */}
          <LedgerTable
            transactions={transactions}
            onOpenNewEntryModal={() => setIsModalOpen(true)}
          />
        </main>
      </div>

      {/* Interactive Voice Floating Hub */}
      <VoiceAssistant
        isListening={isListening}
        isProcessing={isProcessing}
        transcript={transcript}
        statusMessage={statusMessage}
        pendingOperation={activePendingOperation}
        onToggleMic={handleToggleMic}
        onConfirm={confirmPendingOperation}
        onCancel={cancelPendingOperation}
      />

      {/* Fallback Manual Entry Modal */}
      <TransactionModal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        onSubmit={handleManualTransactionSubmit}
      />
    </div>
  );
}
