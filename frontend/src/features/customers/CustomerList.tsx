import React from 'react';

export const CustomerList: React.FC<{ customers: string[] }> = ({ customers }) => (
  <div className="p-4 bg-slate-900 rounded-2xl border border-slate-800">
    <h3 className="text-md font-bold text-white mb-3">Customer Accounts</h3>
    <ul className="divide-y divide-slate-800 text-sm text-slate-300">
      {customers.map((c, i) => (
        <li key={i} className="py-2 flex justify-between">
          <span>{c}</span>
          <span className="text-blue-400 hover:underline cursor-pointer">View Ledger</span>
        </li>
      ))}
    </ul>
  </div>
);
