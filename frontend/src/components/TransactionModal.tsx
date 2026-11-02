'use client';

import React, { useState } from 'react';
import { X } from 'lucide-react';
import { Transaction, TransactionType } from '../types';

interface TransactionModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSubmit: (data: Partial<Transaction>) => Promise<void>;
}

export const TransactionModal: React.FC<TransactionModalProps> = ({
  isOpen,
  onClose,
  onSubmit,
}) => {
  const [customerName, setCustomerName] = useState('');
  const [customerEmail, setCustomerEmail] = useState('');
  const [amount, setAmount] = useState('');
  const [type, setType] = useState<TransactionType>('UDHAAR');
  const [notes, setNotes] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState('');

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!customerName.trim() || !amount.trim()) {
      setError('Please provide customer name and amount');
      return;
    }

    try {
      setIsSubmitting(true);
      setError('');
      await onSubmit({
        customer_name: customerName.trim(),
        customer_email: customerEmail.trim() || undefined,
        amount: parseFloat(amount),
        type,
        notes: notes.trim() || undefined,
      });
      // reset form
      setCustomerName('');
      setCustomerEmail('');
      setAmount('');
      setNotes('');
      onClose();
    } catch (err: any) {
      setError(err.message || 'Failed to record entry');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="w-full max-w-md bg-[#202020] border border-[#2F2F2F] rounded-2xl shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="p-5 border-b border-[#2F2F2F] flex items-center justify-between">
          <h3 className="text-lg font-bold text-white">Record New Ledger Entry</h3>
          <button
            onClick={onClose}
            className="w-8 h-8 rounded-lg bg-white/5 hover:bg-white/10 flex items-center justify-center text-gray-400 hover:text-white"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Form Body */}
        <form onSubmit={handleSubmit} className="p-5 flex flex-col gap-4">
          {error && (
            <div className="p-3 rounded-xl bg-rose-500/15 border border-rose-500/30 text-rose-400 text-xs font-semibold">
              {error}
            </div>
          )}

          {/* Type Toggle */}
          <div className="grid grid-cols-2 gap-2 p-1 bg-black/40 rounded-xl border border-white/5">
            <button
              type="button"
              onClick={() => setType('UDHAAR')}
              className={`py-2 text-xs font-bold rounded-lg transition-all ${
                type === 'UDHAAR'
                  ? 'bg-rose-600 text-white shadow'
                  : 'text-gray-400 hover:text-white'
              }`}
            >
              UDHAAR (Debit/Credit Given)
            </button>
            <button
              type="button"
              onClick={() => setType('JAMA')}
              className={`py-2 text-xs font-bold rounded-lg transition-all ${
                type === 'JAMA'
                  ? 'bg-emerald-600 text-white shadow'
                  : 'text-gray-400 hover:text-white'
              }`}
            >
              JAMA (Payment Received)
            </button>
          </div>

          <div>
            <label className="text-xs font-semibold text-gray-300 block mb-1">
              Customer Name *
            </label>
            <input
              type="text"
              required
              value={customerName}
              onChange={(e) => setCustomerName(e.target.value)}
              placeholder="e.g. Ramesh Kumar"
              className="w-full p-2.5 rounded-xl bg-[#18181A] border border-[#2F2F2F] text-white text-sm focus:outline-none focus:border-amber-500"
            />
          </div>

          <div>
            <label className="text-xs font-semibold text-gray-300 block mb-1">
              Amount (₹) *
            </label>
            <input
              type="number"
              step="0.01"
              required
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              placeholder="e.g. 500"
              className="w-full p-2.5 rounded-xl bg-[#18181A] border border-[#2F2F2F] text-white text-sm focus:outline-none focus:border-amber-500"
            />
          </div>

          <div>
            <label className="text-xs font-semibold text-gray-300 block mb-1">
              Customer Email (for receipt dispatch)
            </label>
            <input
              type="email"
              value={customerEmail}
              onChange={(e) => setCustomerEmail(e.target.value)}
              placeholder="customer@example.com"
              className="w-full p-2.5 rounded-xl bg-[#18181A] border border-[#2F2F2F] text-white text-sm focus:outline-none focus:border-amber-500"
            />
          </div>

          <div>
            <label className="text-xs font-semibold text-gray-300 block mb-1">
              Notes / Items
            </label>
            <input
              type="text"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="e.g. 5kg Atta, 1L Mustard Oil"
              className="w-full p-2.5 rounded-xl bg-[#18181A] border border-[#2F2F2F] text-white text-sm focus:outline-none focus:border-amber-500"
            />
          </div>

          <div className="flex gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="flex-1 py-2.5 rounded-xl bg-white/5 hover:bg-white/10 text-gray-300 text-xs font-bold"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isSubmitting}
              className="flex-1 py-2.5 rounded-xl bg-amber-600 hover:bg-amber-500 text-white text-xs font-bold shadow-lg shadow-amber-600/30 disabled:opacity-50"
            >
              {isSubmitting ? 'Saving...' : 'Save to Ledger'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
