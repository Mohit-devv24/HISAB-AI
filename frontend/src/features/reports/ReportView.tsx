import React from 'react';

export const ReportView: React.FC = () => (
  <div className="p-6 bg-slate-900 rounded-2xl border border-slate-800 flex flex-col gap-4">
    <h3 className="text-lg font-bold text-white">Export Statements</h3>
    <div className="flex gap-3">
      <button className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-white text-xs rounded-xl border border-slate-700">Download CSV</button>
      <button className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-white text-xs rounded-xl border border-slate-700">Generate Thermal Receipt</button>
    </div>
  </div>
);
