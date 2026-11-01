import React from 'react';

export const DashboardView: React.FC = () => (
  <div className="p-6 flex flex-col gap-6">
    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
      <div className="p-5 bg-slate-900 rounded-2xl border border-slate-800">
        <div className="text-xs text-slate-400 font-medium">Total Udhaar Outstanding</div>
        <div className="text-2xl font-black text-rose-400 mt-1">₹14,500.00</div>
      </div>
      <div className="p-5 bg-slate-900 rounded-2xl border border-slate-800">
        <div className="text-xs text-slate-400 font-medium">Today's Jama Collected</div>
        <div className="text-2xl font-black text-emerald-400 mt-1">₹8,200.00</div>
      </div>
      <div className="p-5 bg-slate-900 rounded-2xl border border-slate-800">
        <div className="text-xs text-slate-400 font-medium">Active Kirana Customers</div>
        <div className="text-2xl font-black text-amber-400 mt-1">38</div>
      </div>
    </div>
  </div>
);
