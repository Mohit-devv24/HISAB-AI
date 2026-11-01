import React from 'react';

export const Header: React.FC<{ shopName: string }> = ({ shopName }) => (
  <header className="h-16 border-b border-slate-700 flex items-center justify-between px-6 bg-slate-900/80 backdrop-blur">
    <h1 className="text-lg font-bold text-white">{shopName}</h1>
    <span className="text-xs text-emerald-400 bg-emerald-950/60 px-3 py-1 rounded-full border border-emerald-800">AI Active</span>
  </header>
);
