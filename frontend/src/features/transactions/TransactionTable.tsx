import React from 'react';
import { Transaction } from '../../types';
import { formatINR, paiseToRupees } from '../../utils/formatters';

export const TransactionTable: React.FC<{ transactions: Transaction[] }> = ({ transactions }) => (
  <div className="overflow-x-auto rounded-2xl border border-slate-800 bg-slate-900">
    <table className="w-full text-left text-sm text-slate-300">
      <thead className="bg-slate-950 text-xs uppercase font-semibold text-slate-400 border-b border-slate-800">
        <tr>
          <th className="p-3">Customer</th>
          <th className="p-3">Type</th>
          <th className="p-3">Amount</th>
          <th className="p-3">Date</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-slate-800">
        {transactions.map(t => (
          <tr key={t.id} className="hover:bg-slate-800/50">
            <td className="p-3 font-medium text-white">{t.customer_name}</td>
            <td className="p-3">
              <span className={`px-2 py-0.5 rounded-full text-xs font-bold ${t.type === 'UDHAAR' ? 'bg-rose-950 text-rose-400' : 'bg-emerald-950 text-emerald-400'}`}>
                {t.type}
              </span>
            </td>
            <td className="p-3 font-semibold">{formatINR(t.amount || paiseToRupees(t.amount_paise))}</td>
            <td className="p-3 text-xs text-slate-500">{new Date(t.created_at).toLocaleDateString()}</td>
          </tr>
        ))}
      </tbody>
    </table>
  </div>
);
