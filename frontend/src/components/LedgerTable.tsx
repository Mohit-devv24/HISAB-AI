'use client';

import React from 'react';
import { Transaction } from '../types';
import { formatINR } from '../utils/formatters';
import { ArrowDownLeft, ArrowUpRight, Calendar, User, FileText } from 'lucide-react';

interface LedgerTableProps {
  transactions: Transaction[];
  onOpenNewEntryModal?: () => void;
}

export const LedgerTable: React.FC<LedgerTableProps> = ({
  transactions,
  onOpenNewEntryModal,
}) => {
  return (
    <div className="bg-[#202020] border border-[#2F2F2F] rounded-2xl shadow-xl overflow-hidden">
      <div className="p-4 md:p-6 border-b border-[#2F2F2F] flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
        <div>
          <h2 className="text-lg md:text-xl font-bold text-white flex items-center gap-2">
            <span>📖</span>
            <span>Digital Khata Transactions</span>
          </h2>
          <p className="text-xs text-gray-400 mt-0.5">
            Real-time ledger entries recorded via voice or manual entry
          </p>
        </div>

        {onOpenNewEntryModal && (
          <button
            onClick={onOpenNewEntryModal}
            className="px-4 py-2 rounded-xl bg-gradient-to-r from-amber-600 to-amber-500 hover:from-amber-500 hover:to-amber-400 text-white text-xs font-bold shadow-md transition active:scale-95"
          >
            + Add Manual Entry
          </button>
        )}
      </div>

      {transactions.length === 0 ? (
        <div className="p-12 text-center text-gray-400">
          <div className="text-4xl mb-3">🧾</div>
          <p className="text-base font-semibold text-gray-300">No transactions recorded yet</p>
          <p className="text-xs text-gray-500 mt-1">
            Say &quot;Suno Hisab, Ramesh ko 500 udhaar likho&quot; or tap the microphone below.
          </p>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm text-gray-300">
            <thead className="bg-[#18181A] text-xs uppercase font-bold text-gray-400 tracking-wider border-b border-[#2F2F2F]">
              <tr>
                <th className="py-3.5 px-4 md:px-6">Customer</th>
                <th className="py-3.5 px-4 md:px-6">Type</th>
                <th className="py-3.5 px-4 md:px-6">Amount</th>
                <th className="py-3.5 px-4 md:px-6 hidden sm:table-cell">Notes</th>
                <th className="py-3.5 px-4 md:px-6">Date</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#2F2F2F]">
              {transactions.map((tx) => {
                const isUdhaar = tx.type === 'UDHAAR';
                return (
                  <tr key={tx.id} className="hover:bg-white/[0.02] transition-colors">
                    <td className="py-4 px-4 md:px-6">
                      <div className="font-bold text-white flex items-center gap-2">
                        <div className="w-7 h-7 rounded-full bg-white/10 flex items-center justify-center text-xs text-amber-400 font-black">
                          {tx.customer_name?.[0]?.toUpperCase() || 'C'}
                        </div>
                        <div>
                          <div>{tx.customer_name}</div>
                          {tx.customer_email && (
                            <div className="text-[11px] text-gray-400 font-normal">
                              {tx.customer_email}
                            </div>
                          )}
                        </div>
                      </div>
                    </td>
                    <td className="py-4 px-4 md:px-6">
                      <span
                        className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-bold tracking-tight ${
                          isUdhaar
                            ? 'bg-rose-500/15 text-rose-400 border border-rose-500/30'
                            : 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30'
                        }`}
                      >
                        {isUdhaar ? (
                          <>
                            <ArrowUpRight className="w-3 h-3" />
                            <span>UDHAAR (Debit)</span>
                          </>
                        ) : (
                          <>
                            <ArrowDownLeft className="w-3 h-3" />
                            <span>JAMA (Credit)</span>
                          </>
                        )}
                      </span>
                    </td>
                    <td className="py-4 px-4 md:px-6">
                      <div
                        className={`text-base font-extrabold ${
                          isUdhaar ? 'text-rose-400' : 'text-emerald-400'
                        }`}
                      >
                        {formatINR(tx.amount)}
                      </div>
                    </td>
                    <td className="py-4 px-4 md:px-6 hidden sm:table-cell text-xs text-gray-400 max-w-[200px] truncate">
                      {tx.notes || '—'}
                    </td>
                    <td className="py-4 px-4 md:px-6 text-xs text-gray-400 whitespace-nowrap">
                      {new Date(tx.created_at || Date.now()).toLocaleDateString('en-IN', {
                        day: '2-digit',
                        month: 'short',
                        year: 'numeric',
                      })}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};
