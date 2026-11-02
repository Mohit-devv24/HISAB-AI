'use client';

import React, { useState, useEffect } from 'react';
import { RefreshCw, Zap } from 'lucide-react';

interface HeaderProps {
  pageTitle?: string;
  onSync?: () => void;
  isSyncing?: boolean;
}

export const Header: React.FC<HeaderProps> = ({
  pageTitle = 'Dashboard',
  onSync,
  isSyncing = false,
}) => {
  const [timeStr, setTimeStr] = useState<string>('');

  useEffect(() => {
    const updateTime = () => {
      const now = new Date();
      setTimeStr(
        now.toLocaleTimeString('en-IN', {
          timeZone: 'Asia/Kolkata',
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
        })
      );
    };
    updateTime();
    const interval = setInterval(updateTime, 1000);
    return () => clearInterval(interval);
  }, []);

  return (
    <header className="h-16 bg-white/95 dark:bg-[#19191c]/90 backdrop-blur-md border-b border-gray-200 dark:border-white/10 px-4 md:px-8 flex items-center justify-between sticky top-0 z-30 transition-colors">
      <div className="flex items-center gap-3">
        <h1 className="text-xl md:text-2xl font-black text-gray-900 dark:text-white tracking-tight">
          {pageTitle}
        </h1>
      </div>

      <div className="flex items-center gap-3">
        {/* IST Clock */}
        <div className="hidden sm:flex items-center gap-1.5 px-3 py-1 rounded-full bg-gray-100 dark:bg-white/5 text-xs text-gray-600 dark:text-gray-300 font-mono">
          <span>🕒</span>
          <span>{timeStr || 'Asia/Kolkata'}</span>
        </div>

        {/* Groq AI Active Status Pill */}
        <div className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-emerald-50 dark:bg-emerald-950/60 border border-emerald-200 dark:border-emerald-800/80 text-emerald-700 dark:text-emerald-400 text-xs font-bold tracking-tight shadow-sm">
          <Zap className="w-3.5 h-3.5 text-amber-500 fill-amber-500" />
          <span>Groq AI Active</span>
        </div>

        {/* Topbar Sync Button */}
        <button
          type="button"
          onClick={onSync}
          disabled={isSyncing}
          className="inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-full bg-[#F0F9FF] hover:bg-[#E0F2FE] text-[#0284C7] border border-[#BAE6FD] text-xs font-bold tracking-tight shadow-sm transition active:scale-95 disabled:opacity-60"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${isSyncing ? 'animate-spin' : ''}`} />
          <span className="hidden sm:inline">Sync</span>
        </button>
        <span className="hidden md:inline text-xs text-slate-500 dark:text-slate-400">
          Just now
        </span>
      </div>
    </header>
  );
};
