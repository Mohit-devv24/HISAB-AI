'use client';

import React from 'react';
import { DailySummary } from '../types';
import { formatINR } from '../utils/formatters';
import { TrendingUp, TrendingDown, Users, CreditCard } from 'lucide-react';

interface LedgerDashboardProps {
  summary: DailySummary;
}

export const LedgerDashboard: React.FC<LedgerDashboardProps> = ({ summary }) => {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
      {/* Total Udhaar Card */}
      <div className="p-5 bg-[#202020] border border-[#2F2F2F] rounded-2xl shadow-md">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs font-semibold text-gray-400">Total Udhaar (Credit Given)</span>
          <div className="w-8 h-8 rounded-lg bg-rose-500/10 flex items-center justify-center text-rose-400">
            <TrendingUp className="w-4 h-4" />
          </div>
        </div>
        <div className="text-2xl font-black text-rose-400">
          {formatINR(summary.total_udhaar)}
        </div>
        <div className="text-[11px] text-gray-500 mt-1">Pending to be collected</div>
      </div>

      {/* Total Jama Card */}
      <div className="p-5 bg-[#202020] border border-[#2F2F2F] rounded-2xl shadow-md">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs font-semibold text-gray-400">Total Jama (Collected)</span>
          <div className="w-8 h-8 rounded-lg bg-emerald-500/10 flex items-center justify-center text-emerald-400">
            <TrendingDown className="w-4 h-4" />
          </div>
        </div>
        <div className="text-2xl font-black text-emerald-400">
          {formatINR(summary.total_jama)}
        </div>
        <div className="text-[11px] text-gray-500 mt-1">Cash & digital collection</div>
      </div>

      {/* Net Balance */}
      <div className="p-5 bg-[#202020] border border-[#2F2F2F] rounded-2xl shadow-md">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs font-semibold text-gray-400">Net Outstanding Khata</span>
          <div className="w-8 h-8 rounded-lg bg-amber-500/10 flex items-center justify-center text-amber-400">
            <CreditCard className="w-4 h-4" />
          </div>
        </div>
        <div className="text-2xl font-black text-white">
          {formatINR(summary.net_balance)}
        </div>
        <div className="text-[11px] text-gray-500 mt-1">Udhaar minus Jama balance</div>
      </div>

      {/* Active Customers */}
      <div className="p-5 bg-[#202020] border border-[#2F2F2F] rounded-2xl shadow-md">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs font-semibold text-gray-400">Active Accounts</span>
          <div className="w-8 h-8 rounded-lg bg-blue-500/10 flex items-center justify-center text-blue-400">
            <Users className="w-4 h-4" />
          </div>
        </div>
        <div className="text-2xl font-black text-white">
          {summary.active_customers_count || 0}
        </div>
        <div className="text-[11px] text-gray-500 mt-1">Customers with ledger history</div>
      </div>
    </div>
  );
};
