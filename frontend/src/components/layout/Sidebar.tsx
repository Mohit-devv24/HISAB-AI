import React from 'react';

export const Sidebar: React.FC = () => (
  <aside className="w-64 bg-slate-950 border-r border-slate-800 p-4 flex flex-col gap-2">
    <div className="text-xl font-black text-amber-400 mb-6">Hisab AI</div>
    <nav className="flex flex-col gap-1 text-sm font-medium text-slate-300">
      <a href="#dashboard" className="p-2.5 rounded-lg bg-slate-800 text-white">Dashboard</a>
      <a href="#ledger" className="p-2.5 rounded-lg hover:bg-slate-800">Ledger</a>
      <a href="#customers" className="p-2.5 rounded-lg hover:bg-slate-800">Customers</a>
      <a href="#reports" className="p-2.5 rounded-lg hover:bg-slate-800">Reports</a>
    </nav>
  </aside>
);
